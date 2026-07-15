"""Trusted, reproducible dependency provisioning for funded execution.

Generated code never resolves or installs its own dependency graph.  SignalOS
uses a reviewed package manifest and lockfile, installs them with lifecycle
scripts disabled inside a credential-free hardened staging container, records a
content-bound receipt, and then materializes that immutable bundle into each
workspace.  Build/test containers receive ``node_modules`` read-only.
"""

from __future__ import annotations

__all__ = [
    "DependencyBrokerError",
    "DependencyPolicy",
    "load_dependency_policy",
    "materialize_dependency_bundle",
    "materialize_funded_dependencies_from_environment",
    "funded_dependency_mount_from_environment",
    "prepare_dependency_bundle",
    "validate_package_lock",
    "verify_dependency_bundle",
    "verify_materialized_dependencies",
    "verify_funded_dependencies_from_environment",
]

import base64
import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import stat
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .sandbox import SandboxUnavailableError, validate_pinned_image

POLICY_SCHEMA = "signalos.funded-dependency-policy.v1"
RECEIPT_SCHEMA = "signalos.dependency-receipt.v1"
SUPPORTED_PROFILE = "react-vite"
SUPPORTED_PLATFORM = "linux/amd64"
APPROVED_ORIGIN = "https://registry.npmjs.org"
FIXED_INSTALL_COMMAND = (
    "npm",
    "ci",
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
)
FIXED_ARCHIVE_COMMAND = (
    "mkdir -p node_modules/.vite && "
    "(cd node_modules && find . -mindepth 1 -printf '%P\\0' | LC_ALL=C sort -z | "
    "tar --null --no-recursion --files-from=- --format=pax "
    "--pax-option=delete=atime,delete=ctime --mtime='@0' "
    "--owner=0 --group=0 --numeric-owner -cf ../node_modules.tar)"
)
RECEIPT_NAME = "dependency-receipt.json"
_BUNDLE_RECEIPT_NAME = ".signalos-dependency-receipt.json"
ARCHIVE_NAME = "node_modules.tar"
ATTESTATION_KEY_ENV = "SIGNALOS_DEPENDENCY_ATTESTATION_KEY"
TRUSTED_EGRESS_POLICY = "npm-registry-proxy-v1"
_MATERIALIZED_ARCHIVE_REL = Path(".signalos") / "dependencies" / ARCHIVE_NAME
_SEMVER_SPEC_RE = re.compile(r"^[~^]?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_INTEGRITY_RE = re.compile(r"^sha512-([A-Za-z0-9+/]+={0,2})$")
_NPM_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_WINDOWS_REPARSE_POINT = 0x0400


class DependencyBrokerError(SandboxUnavailableError):
    """The trusted dependency boundary could not be established or verified."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DependencyBrokerError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DependencyBrokerError(f"dependency JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise DependencyBrokerError(f"dependency JSON root must be an object: {path}")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise DependencyBrokerError(f"cannot hash dependency artifact: {path}") from exc


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _attestation_key(value: bytes | str | None = None) -> bytes:
    """Resolve a run-scoped HMAC key that never enters generated containers."""
    raw: bytes | str = value if value is not None else os.environ.get(
        ATTESTATION_KEY_ENV, ""
    )
    if isinstance(raw, bytes):
        key = raw
    else:
        text = str(raw).strip()
        if not re.fullmatch(r"[0-9a-fA-F]{64,}", text) or len(text) % 2:
            raise DependencyBrokerError(
                f"{ATTESTATION_KEY_ENV} must be at least 32 random bytes encoded as hex"
            )
        key = bytes.fromhex(text)
    if len(key) < 32:
        raise DependencyBrokerError("dependency attestation key is shorter than 32 bytes")
    return key


@dataclass(frozen=True)
class DependencyPolicy:
    path: Path
    profile: str
    platform: str
    image: str
    allowed_origins: tuple[str, ...]
    install_command: tuple[str, ...]
    max_files: int
    max_bytes: int
    policy_sha256: str
    package_json: Path
    package_lock: Path


@dataclass(frozen=True)
class _TreeEvidence:
    sha256: str
    file_count: int
    total_bytes: int


def load_dependency_policy(
    policy_path: str | os.PathLike[str],
    *,
    profile: str = SUPPORTED_PROFILE,
) -> DependencyPolicy:
    path = Path(policy_path).resolve()
    raw = _read_json(path)
    if raw.get("schema") != POLICY_SCHEMA:
        raise DependencyBrokerError("unsupported funded dependency policy schema")
    if raw.get("profile") != profile or profile != SUPPORTED_PROFILE:
        raise DependencyBrokerError(
            f"funded dependency policy supports only {SUPPORTED_PROFILE!r}"
        )
    if raw.get("platform") != SUPPORTED_PLATFORM:
        raise DependencyBrokerError(
            f"funded dependency platform must be {SUPPORTED_PLATFORM}"
        )
    try:
        image = validate_pinned_image(str(raw.get("buildImage") or ""))
    except ValueError as exc:
        raise DependencyBrokerError(str(exc)) from exc
    origins = tuple(str(value) for value in (raw.get("allowedRegistryOrigins") or []))
    if origins != (APPROVED_ORIGIN,):
        raise DependencyBrokerError("funded npm registry origin is not the approved exact origin")
    command = tuple(str(value) for value in (raw.get("installCommand") or []))
    if command != FIXED_INSTALL_COMMAND:
        raise DependencyBrokerError("funded dependency install command is not the fixed safe command")
    try:
        max_files = int(raw.get("maxFiles"))
        max_bytes = int(raw.get("maxBytes"))
    except (TypeError, ValueError) as exc:
        raise DependencyBrokerError("dependency bundle limits must be integers") from exc
    if not 1_000 <= max_files <= 250_000:
        raise DependencyBrokerError("dependency maxFiles is outside the safe range")
    if not 64 * 1024 * 1024 <= max_bytes <= 2 * 1024 * 1024 * 1024:
        raise DependencyBrokerError("dependency maxBytes is outside the safe range")
    fixture = path.parent / profile
    package_json = fixture / "package.json"
    package_lock = fixture / "package-lock.json"
    if not package_json.is_file() or not package_lock.is_file():
        raise DependencyBrokerError("reviewed dependency manifest/lockfile is missing")
    return DependencyPolicy(
        path=path,
        profile=profile,
        platform=SUPPORTED_PLATFORM,
        image=image,
        allowed_origins=origins,
        install_command=command,
        max_files=max_files,
        max_bytes=max_bytes,
        policy_sha256=_sha256_file(path),
        package_json=package_json,
        package_lock=package_lock,
    )


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("private") is not True:
        raise DependencyBrokerError("funded package manifest must be private")
    for section in ("dependencies", "devDependencies"):
        values = manifest.get(section, {})
        if not isinstance(values, dict):
            raise DependencyBrokerError(f"package.json {section} must be an object")
        for package, spec in values.items():
            if not isinstance(package, str) or not package.strip():
                raise DependencyBrokerError("package.json contains an invalid package name")
            if not isinstance(spec, str) or _SEMVER_SPEC_RE.fullmatch(spec) is None:
                raise DependencyBrokerError(
                    f"package {package!r} uses a forbidden non-semver dependency spec"
                )


def _validate_registry_url(value: str) -> str:
    if any(ord(char) < 32 or char == "\\" for char in value):
        raise DependencyBrokerError("lockfile resolved URL contains forbidden characters")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise DependencyBrokerError("lockfile resolved URL has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "registry.npmjs.org"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise DependencyBrokerError(f"lockfile resolved URL is not approved: {value}")
    lower_path = parsed.path.lower()
    if "%2f" in lower_path or "%5c" in lower_path:
        raise DependencyBrokerError("lockfile resolved URL contains encoded path separators")
    decoded_parts = unquote(parsed.path).split("/")
    if any(part in (".", "..") for part in decoded_parts):
        raise DependencyBrokerError("lockfile resolved URL contains path traversal")
    if not parsed.path.endswith(".tgz"):
        raise DependencyBrokerError("lockfile resolved URL is not an npm tarball")
    return value


def validate_package_lock(policy: DependencyPolicy) -> dict[str, Any]:
    manifest = _read_json(policy.package_json)
    lock = _read_json(policy.package_lock)
    _validate_manifest(manifest)
    if lock.get("lockfileVersion") != 3:
        raise DependencyBrokerError("funded npm lockfileVersion must be exactly 3")
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise DependencyBrokerError("package-lock.json packages must be an object")
    root = packages.get("")
    if not isinstance(root, dict):
        raise DependencyBrokerError("package-lock.json has no root package entry")
    for section in ("dependencies", "devDependencies"):
        expected = manifest.get(section, {})
        actual = root.get(section, {})
        if actual != expected:
            raise DependencyBrokerError(
                f"package-lock root {section} does not match package.json"
            )
    resolved_urls: list[str] = []
    for package_path, entry in packages.items():
        if package_path == "":
            continue
        if not isinstance(entry, dict):
            raise DependencyBrokerError(f"invalid lock entry: {package_path}")
        if entry.get("link") is True:
            raise DependencyBrokerError(f"linked dependency is forbidden: {package_path}")
        version = entry.get("version")
        if not isinstance(version, str) or _EXACT_VERSION_RE.fullmatch(version) is None:
            raise DependencyBrokerError(f"dependency has no exact version: {package_path}")
        resolved = entry.get("resolved")
        if not isinstance(resolved, str):
            raise DependencyBrokerError(f"dependency has no resolved URL: {package_path}")
        resolved_urls.append(_validate_registry_url(resolved))
        integrity = entry.get("integrity")
        if not isinstance(integrity, str) or _INTEGRITY_RE.fullmatch(integrity) is None:
            raise DependencyBrokerError(f"dependency has no strong sha512 integrity: {package_path}")
        try:
            base64.b64decode(_INTEGRITY_RE.fullmatch(integrity).group(1), validate=True)
        except (ValueError, TypeError) as exc:
            raise DependencyBrokerError(f"dependency integrity is malformed: {package_path}") from exc
    return {
        "package_json_sha256": _sha256_file(policy.package_json),
        "package_lock_sha256": _sha256_file(policy.package_lock),
        "lockfile_version": 3,
        "resolved_urls_sha256": _sha256_bytes(
            "\n".join(sorted(resolved_urls)).encode("utf-8")
        ),
        "package_count": len(packages) - 1,
    }


def _real_directory(path: Path, *, label: str) -> Path:
    """Require *path* itself to be a non-reparse directory before resolving it."""
    try:
        info = path.lstat()
    except OSError as exc:
        raise DependencyBrokerError(f"{label} is missing or unreadable") from exc
    attrs = int(getattr(info, "st_file_attributes", 0) or 0)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or (
        attrs & _WINDOWS_REPARSE_POINT
    ):
        raise DependencyBrokerError(f"{label} must be a real non-reparse directory")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise DependencyBrokerError(f"{label} cannot be resolved") from exc


def _real_file(path: Path, *, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DependencyBrokerError(f"{label} is missing or unreadable") from exc
    attrs = int(getattr(info, "st_file_attributes", 0) or 0)
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or (
        attrs & _WINDOWS_REPARSE_POINT
    ):
        raise DependencyBrokerError(f"{label} must be a real non-reparse file")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise DependencyBrokerError(f"{label} cannot be resolved") from exc


def _ensure_contained_directory(
    root: Path, relative: Path, *, create: bool = True
) -> Path:
    """Create/validate a direct directory chain without following link parents."""
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if create and not cursor.exists() and not cursor.is_symlink():
            try:
                cursor.mkdir()
            except OSError as exc:
                raise DependencyBrokerError(
                    f"cannot create protected dependency directory: {relative}"
                ) from exc
        resolved = _real_directory(cursor, label=f"protected directory {cursor.name}")
        if resolved != root and root not in resolved.parents:
            raise DependencyBrokerError("protected dependency directory escapes workspace")
        cursor = resolved
    return cursor


def _safe_symlink_target(root: Path, path: Path, rel: str) -> str:
    try:
        target = os.readlink(path)
    except OSError as exc:
        raise DependencyBrokerError(f"cannot read dependency symlink: {rel}") from exc
    if os.path.isabs(target):
        raise DependencyBrokerError(f"absolute dependency symlink is forbidden: {rel}")
    resolved = (path.parent / target).resolve()
    if resolved != root and root not in resolved.parents:
        raise DependencyBrokerError(f"dependency symlink escapes the bundle: {rel}")
    return target


def _tree_entries(root_path: Path) -> tuple[Path, list[tuple[str, Path, os.stat_result]]]:
    root = _real_directory(root_path, label="dependency tree")
    pending = [root]
    records: list[tuple[str, Path, os.stat_result]] = []
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise DependencyBrokerError(f"cannot read dependency tree: {directory}") from exc
        for entry in entries:
            path = Path(entry.path)
            rel = path.relative_to(root).as_posix()
            try:
                info = path.lstat()
            except OSError as exc:
                raise DependencyBrokerError(f"cannot stat dependency entry: {rel}") from exc
            records.append((rel, path, info))
            if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                attrs = int(getattr(info, "st_file_attributes", 0) or 0)
                if attrs & _WINDOWS_REPARSE_POINT:
                    raise DependencyBrokerError(
                        f"dependency junction/reparse point is forbidden: {rel}"
                    )
                pending.append(path)
    return root, sorted(records, key=lambda item: item[0])


def _dependency_tree(root: Path, *, max_files: int, max_bytes: int) -> _TreeEvidence:
    root, entries = _tree_entries(root)
    if len(entries) > max_files:
        raise DependencyBrokerError("dependency tree exceeds the file-count limit")
    digest = hashlib.sha256()
    total = 0
    for rel, path, info in entries:
        mode = stat.S_IMODE(info.st_mode) & 0o7777
        if mode & 0o7000:
            raise DependencyBrokerError(
                f"dependency entry has forbidden special permission bits: {rel}"
            )
        if stat.S_ISLNK(info.st_mode):
            target = _safe_symlink_target(root, path, rel)
            digest.update(f"L\0{rel}\0{target}\n".encode("utf-8"))
        elif stat.S_ISDIR(info.st_mode):
            digest.update(f"D\0{rel}\0{mode:o}\n".encode("utf-8"))
        elif stat.S_ISREG(info.st_mode):
            total += info.st_size
            if total > max_bytes:
                raise DependencyBrokerError("dependency tree exceeds the byte limit")
            digest.update(f"F\0{rel}\0{mode:o}\0{info.st_size}\0".encode("utf-8"))
            try:
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                raise DependencyBrokerError(f"cannot hash dependency file: {rel}") from exc
            digest.update(b"\n")
        else:
            raise DependencyBrokerError(f"special dependency file is forbidden: {rel}")
    return _TreeEvidence(digest.hexdigest(), len(entries), total)


def _write_dependency_archive(root_path: Path, archive_path: Path) -> None:
    root, entries = _tree_entries(root_path)
    try:
        with tarfile.open(archive_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for rel, path, info in entries:
                member = archive.gettarinfo(str(path), arcname=rel)
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                member.mtime = 0
                member.pax_headers = {}
                if stat.S_ISLNK(info.st_mode):
                    member.linkname = _safe_symlink_target(root, path, rel)
                    archive.addfile(member)
                elif stat.S_ISREG(info.st_mode):
                    with path.open("rb") as handle:
                        archive.addfile(member, handle)
                elif stat.S_ISDIR(info.st_mode):
                    archive.addfile(member)
                else:
                    raise DependencyBrokerError(
                        f"special dependency file is forbidden: {rel}"
                    )
    except DependencyBrokerError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise DependencyBrokerError("cannot create deterministic dependency archive") from exc


def _archive_tree(
    archive_path: Path, *, max_files: int, max_bytes: int
) -> _TreeEvidence:
    digest = hashlib.sha256()
    count = 0
    total = 0
    seen: set[str] = set()
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = sorted(archive.getmembers(), key=lambda item: item.name.rstrip("/"))
            for member in members:
                rel = member.name.rstrip("/")
                if (
                    not rel
                    or rel.startswith("/")
                    or "\\" in rel
                    or any(part in ("", ".", "..") for part in rel.split("/"))
                    or rel in seen
                ):
                    raise DependencyBrokerError("dependency archive contains an unsafe path")
                seen.add(rel)
                count += 1
                if count > max_files:
                    raise DependencyBrokerError("dependency archive exceeds the file-count limit")
                mode = member.mode & 0o7777
                if mode & 0o7000:
                    raise DependencyBrokerError(
                        "dependency archive has forbidden special permission bits"
                    )
                if member.issym():
                    target = member.linkname
                    if not target or target.startswith("/") or "\\" in target:
                        raise DependencyBrokerError("dependency archive has an unsafe symlink")
                    base_parts = rel.split("/")[:-1]
                    normalized: list[str] = []
                    for part in [*base_parts, *target.split("/")]:
                        if part in ("", "."):
                            continue
                        if part == "..":
                            if not normalized:
                                raise DependencyBrokerError(
                                    "dependency archive symlink escapes its root"
                                )
                            normalized.pop()
                        else:
                            normalized.append(part)
                    digest.update(f"L\0{rel}\0{target}\n".encode("utf-8"))
                elif member.isdir():
                    digest.update(f"D\0{rel}\0{mode:o}\n".encode("utf-8"))
                elif member.isfile():
                    total += member.size
                    if total > max_bytes:
                        raise DependencyBrokerError("dependency archive exceeds the byte limit")
                    digest.update(f"F\0{rel}\0{mode:o}\0{member.size}\0".encode("utf-8"))
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise DependencyBrokerError("dependency archive file is unreadable")
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                    digest.update(b"\n")
                else:
                    raise DependencyBrokerError("dependency archive contains a special entry")
    except DependencyBrokerError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise DependencyBrokerError("dependency archive is unreadable") from exc
    return _TreeEvidence(digest.hexdigest(), count, total)


def _require_top_level_packages(node_modules: Path, manifest_path: Path) -> None:
    manifest = _read_json(manifest_path)
    required = {
        str(name)
        for section in ("dependencies", "devDependencies")
        for name in (manifest.get(section, {}) or {})
    }
    missing = []
    for package in sorted(required):
        parts = package.split("/")
        target = node_modules.joinpath(*parts)
        if not target.is_dir() or target.is_symlink():
            missing.append(package)
    if missing:
        raise DependencyBrokerError(
            "dependency install omitted reviewed top-level packages: "
            + ", ".join(missing)
        )


def _receipt_hash(receipt: dict[str, Any]) -> str:
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    return _sha256_bytes(_canonical_json(unsigned))


def _receipt_mac_payload(receipt: dict[str, Any]) -> bytes:
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    unsigned.pop("provenance_hmac_sha256", None)
    return _canonical_json(unsigned)


def _receipt_mac(receipt: dict[str, Any], key: bytes) -> str:
    return hmac.new(key, _receipt_mac_payload(receipt), hashlib.sha256).hexdigest()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise DependencyBrokerError(f"cannot persist dependency receipt: {path}") from exc


def _validate_receipt(
    receipt: dict[str, Any],
    policy: DependencyPolicy,
    lock_evidence: dict[str, Any],
    attestation_key: bytes,
) -> None:
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("status") != "ready":
        raise DependencyBrokerError("dependency receipt is not ready")
    if receipt.get("receipt_sha256") != _receipt_hash(receipt):
        raise DependencyBrokerError("dependency receipt self-hash is invalid")
    supplied_mac = str(receipt.get("provenance_hmac_sha256") or "")
    expected_mac = _receipt_mac(receipt, attestation_key)
    if not hmac.compare_digest(supplied_mac, expected_mac):
        raise DependencyBrokerError("dependency receipt provenance HMAC is invalid")
    expected = {
        "profile": policy.profile,
        "policy_sha256": policy.policy_sha256,
        "broker_sha256": _sha256_file(Path(__file__).resolve()),
        "image": policy.image,
        "platform": policy.platform,
        "attestation_key_id": _sha256_bytes(attestation_key),
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise DependencyBrokerError(f"dependency receipt {key} does not match policy")
    inputs = receipt.get("inputs")
    if not isinstance(inputs, dict):
        raise DependencyBrokerError("dependency receipt inputs are missing")
    for key in (
        "package_json_sha256",
        "package_lock_sha256",
        "lockfile_version",
        "resolved_urls_sha256",
        "package_count",
    ):
        if inputs.get(key) != lock_evidence.get(key):
            raise DependencyBrokerError(f"dependency receipt input drifted: {key}")
    package_manager = receipt.get("package_manager")
    if (
        not isinstance(package_manager, dict)
        or package_manager.get("name") != "npm"
        or not isinstance(package_manager.get("version"), str)
        or _NPM_VERSION_RE.fullmatch(package_manager["version"]) is None
    ):
        raise DependencyBrokerError("dependency receipt has an invalid npm version")
    if receipt.get("fetch") != {
        "scripts_ignored": True,
        "audit": False,
        "fund": False,
        "lockfile_allowed_registry_origins": list(policy.allowed_origins),
        "egress_policy": TRUSTED_EGRESS_POLICY,
    }:
        raise DependencyBrokerError("dependency receipt fetch policy is invalid")


def prepare_dependency_bundle(
    policy_path: str | os.PathLike[str],
    bundle_dir: str | os.PathLike[str],
    *,
    engine: str,
    runner: Any | None = None,
    timeout: float = 900,
    attestation_key: bytes | str | None = None,
) -> dict[str, Any]:
    policy = load_dependency_policy(policy_path)
    lock_evidence = validate_package_lock(policy)
    key = _attestation_key(attestation_key)
    if engine not in {"docker", "podman"}:
        raise DependencyBrokerError("trusted dependency engine must be docker or podman")
    destination = Path(bundle_dir).resolve()
    if destination.exists():
        return verify_dependency_bundle(
            policy.path, destination, attestation_key=key
        )
    if runner is None:
        raise DependencyBrokerError(
            "online dependency provisioning is disabled until the allowlisted "
            "registry-proxy runner is configured"
        )
    if (
        getattr(runner, "dependency_egress_policy", None) != TRUSTED_EGRESS_POLICY
        or getattr(runner, "platform", None) != policy.platform
        or getattr(runner, "image", None) != policy.image
        or getattr(runner, "engine", None) != engine
    ):
        raise DependencyBrokerError(
            "dependency installer lacks the trusted registry egress/platform attestation"
        )
    staging = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
    except OSError as exc:
        raise DependencyBrokerError(
            "cannot create trusted dependency staging directory"
        ) from exc
    published = False
    try:
        shutil.copy2(policy.package_json, staging / "package.json")
        shutil.copy2(policy.package_lock, staging / "package-lock.json")
        command = (
            "node -p '\"SIGNALOS_RUNTIME=\"+process.platform+\"/\"+process.arch'"
            " && npm --version && "
            + shlex.join(policy.install_command)
            + " && "
            + FIXED_ARCHIVE_COMMAND
        )
        exit_code, output = runner.run(
            command,
            staging,
            timeout,
            {
                "CI": "1",
                "NPM_CONFIG_REGISTRY": APPROVED_ORIGIN + "/",
                "NPM_CONFIG_IGNORE_SCRIPTS": "true",
                "NPM_CONFIG_AUDIT": "false",
                "NPM_CONFIG_FUND": "false",
                "NPM_CONFIG_UPDATE_NOTIFIER": "false",
            },
        )
        if output.timed_out or exit_code != 0:
            detail = (output.stderr or output.stdout or "dependency install failed")[-2000:]
            raise DependencyBrokerError(f"trusted dependency install failed: {detail}")
        lines = [line.strip() for line in output.stdout.splitlines() if line.strip()]
        if not lines or lines[0] != "SIGNALOS_RUNTIME=linux/x64":
            raise DependencyBrokerError(
                "trusted dependency installer runtime is not linux/amd64"
            )
        npm_version = lines[1] if len(lines) > 1 else ""
        if _NPM_VERSION_RE.fullmatch(npm_version) is None:
            raise DependencyBrokerError("trusted dependency installer did not report npm version")
        _require_top_level_packages(staging / "node_modules", staging / "package.json")
        archive_path = staging / ARCHIVE_NAME
        tree = _archive_tree(
            archive_path,
            max_files=policy.max_files,
            max_bytes=policy.max_bytes,
        )
        shutil.rmtree(staging / "node_modules")
        receipt: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA,
            "status": "ready",
            "profile": policy.profile,
            "policy_sha256": policy.policy_sha256,
            "broker_sha256": _sha256_file(Path(__file__).resolve()),
            "image": policy.image,
            "platform": policy.platform,
            "attestation_key_id": _sha256_bytes(key),
            "package_manager": {"name": "npm", "version": npm_version},
            "inputs": lock_evidence,
            "fetch": {
                "scripts_ignored": True,
                "audit": False,
                "fund": False,
                "lockfile_allowed_registry_origins": list(policy.allowed_origins),
                "egress_policy": TRUSTED_EGRESS_POLICY,
            },
            "bundle": {
                "tree_sha256": tree.sha256,
                "file_count": tree.file_count,
                "total_bytes": tree.total_bytes,
                "archive_sha256": _sha256_file(archive_path),
            },
        }
        receipt["provenance_hmac_sha256"] = _receipt_mac(receipt, key)
        receipt["receipt_sha256"] = _receipt_hash(receipt)
        _write_json_atomic(staging / _BUNDLE_RECEIPT_NAME, receipt)
        os.replace(staging, destination)
        published = True
        return verify_dependency_bundle(
            policy.path, destination, attestation_key=key
        )
    except DependencyBrokerError:
        shutil.rmtree(staging, ignore_errors=True)
        if published:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if published:
            shutil.rmtree(destination, ignore_errors=True)
        raise DependencyBrokerError("trusted dependency provisioning failed") from exc


def verify_dependency_bundle(
    policy_path: str | os.PathLike[str],
    bundle_dir: str | os.PathLike[str],
    *,
    attestation_key: bytes | str | None = None,
) -> dict[str, Any]:
    policy = load_dependency_policy(policy_path)
    lock_evidence = validate_package_lock(policy)
    key = _attestation_key(attestation_key)
    bundle = _real_directory(Path(bundle_dir), label="dependency bundle")
    receipt = _read_json(
        _real_file(
            bundle / _BUNDLE_RECEIPT_NAME, label="dependency bundle receipt"
        )
    )
    _validate_receipt(receipt, policy, lock_evidence, key)
    package_file = _real_file(bundle / "package.json", label="bundle package.json")
    lock_file = _real_file(bundle / "package-lock.json", label="bundle package-lock.json")
    if _sha256_file(package_file) != lock_evidence["package_json_sha256"]:
        raise DependencyBrokerError("dependency bundle package.json drifted")
    if _sha256_file(lock_file) != lock_evidence["package_lock_sha256"]:
        raise DependencyBrokerError("dependency bundle package-lock.json drifted")
    archive_path = _real_file(bundle / ARCHIVE_NAME, label="dependency bundle archive")
    archive_sha256 = _sha256_file(archive_path)
    tree = _archive_tree(
        archive_path,
        max_files=policy.max_files,
        max_bytes=policy.max_bytes,
    )
    expected_tree = receipt.get("bundle")
    if not isinstance(expected_tree, dict) or expected_tree != {
        "tree_sha256": tree.sha256,
        "file_count": tree.file_count,
        "total_bytes": tree.total_bytes,
        "archive_sha256": archive_sha256,
    }:
        raise DependencyBrokerError("dependency bundle archive does not match its receipt")
    return receipt


def materialize_dependency_bundle(
    workspace: str | os.PathLike[str],
    policy_path: str | os.PathLike[str],
    bundle_dir: str | os.PathLike[str],
    *,
    attestation_key: bytes | str | None = None,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    policy = load_dependency_policy(policy_path)
    key = _attestation_key(attestation_key)
    receipt = verify_dependency_bundle(
        policy.path, bundle_dir, attestation_key=key
    )
    lock_evidence = validate_package_lock(policy)
    package_path = root / "package.json"
    if _real_file(package_path, label="workspace package.json").parent != root:
        raise DependencyBrokerError("workspace package.json escapes the workspace")
    if _sha256_file(package_path) != lock_evidence["package_json_sha256"]:
        raise DependencyBrokerError("workspace package.json does not match the reviewed scaffold")
    protected_root = _ensure_contained_directory(root, Path(".signalos"))
    materialized_receipt = protected_root / RECEIPT_NAME
    target_modules = root / "node_modules"
    archive_parent = _ensure_contained_directory(
        root, Path(".signalos") / "dependencies"
    )
    target_archive = archive_parent / ARCHIVE_NAME
    target_lock = root / "package-lock.json"
    if target_modules.exists() or target_modules.is_symlink():
        if materialized_receipt.is_file() and target_archive.is_file():
            return verify_materialized_dependencies(
                root, policy.path, attestation_key=key
            )
        raise DependencyBrokerError(
            "workspace node_modules must be absent before dependency materialization"
        )
    lock_preexisting = target_lock.exists() or target_lock.is_symlink()
    if lock_preexisting:
        if _real_file(target_lock, label="workspace package-lock.json").parent != root:
            raise DependencyBrokerError("workspace package-lock.json escapes the workspace")
        if _sha256_file(target_lock) != lock_evidence["package_lock_sha256"]:
            raise DependencyBrokerError("workspace package-lock.json is not the reviewed lockfile")
    bundle = Path(bundle_dir).resolve()
    temporary_archive = archive_parent / f".{ARCHIVE_NAME}.{uuid.uuid4().hex}.tmp"
    temporary_lock = root / f".package-lock.signalos-{uuid.uuid4().hex}.json"
    archive_installed = False
    modules_installed = False
    lock_installed = False
    receipt_installed = False

    def rollback() -> None:
        if receipt_installed:
            try:
                materialized_receipt.unlink(missing_ok=True)
            except OSError:
                pass
        for path in (temporary_archive, temporary_lock):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if modules_installed:
            shutil.rmtree(target_modules, ignore_errors=True)
        if archive_installed:
            try:
                target_archive.unlink(missing_ok=True)
            except OSError:
                pass
        if lock_installed:
            try:
                target_lock.unlink(missing_ok=True)
            except OSError:
                pass

    try:
        archive_parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle / ARCHIVE_NAME, temporary_archive)
        if not lock_preexisting:
            shutil.copy2(bundle / "package-lock.json", temporary_lock)
        os.replace(temporary_archive, target_archive)
        archive_installed = True
        target_modules.mkdir()
        modules_installed = True
        if not lock_preexisting:
            os.replace(temporary_lock, target_lock)
            lock_installed = True
        _write_json_atomic(materialized_receipt, receipt)
        receipt_installed = True
        return verify_materialized_dependencies(
            root, policy.path, attestation_key=key
        )
    except DependencyBrokerError:
        rollback()
        raise
    except Exception as exc:
        rollback()
        raise DependencyBrokerError("dependency materialization failed") from exc


def verify_materialized_dependencies(
    workspace: str | os.PathLike[str],
    policy_path: str | os.PathLike[str],
    *,
    attestation_key: bytes | str | None = None,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    policy = load_dependency_policy(policy_path)
    lock_evidence = validate_package_lock(policy)
    key = _attestation_key(attestation_key)
    protected_root = _ensure_contained_directory(
        root, Path(".signalos"), create=False
    )
    receipt_path = _real_file(
        protected_root / RECEIPT_NAME, label="materialized dependency receipt"
    )
    receipt = _read_json(receipt_path)
    _validate_receipt(receipt, policy, lock_evidence, key)
    if _real_file(root / "package.json", label="materialized package.json").parent != root:
        raise DependencyBrokerError("materialized package.json escapes the workspace")
    if _sha256_file(root / "package.json") != lock_evidence["package_json_sha256"]:
        raise DependencyBrokerError("materialized package.json drifted")
    if _real_file(
        root / "package-lock.json", label="materialized package-lock.json"
    ).parent != root:
        raise DependencyBrokerError("materialized package-lock.json escapes the workspace")
    if _sha256_file(root / "package-lock.json") != lock_evidence["package_lock_sha256"]:
        raise DependencyBrokerError("materialized package-lock.json drifted")
    modules = root / "node_modules"
    modules_root = _real_directory(modules, label="materialized node_modules mountpoint")
    try:
        with os.scandir(modules_root) as entries:
            nonempty = next(entries, None) is not None
    except OSError as exc:
        raise DependencyBrokerError("materialized node_modules is unreadable") from exc
    if modules_root.parent != root or nonempty:
        raise DependencyBrokerError(
            "materialized node_modules must be an empty direct mountpoint"
        )
    archive_parent = _ensure_contained_directory(
        root, Path(".signalos") / "dependencies", create=False
    )
    archive_path = _real_file(
        archive_parent / ARCHIVE_NAME, label="materialized dependency archive"
    )
    tree = _archive_tree(
        archive_path,
        max_files=policy.max_files,
        max_bytes=policy.max_bytes,
    )
    archive_sha256 = _sha256_file(archive_path)
    expected = receipt.get("bundle")
    if not isinstance(expected, dict) or expected != {
        "tree_sha256": tree.sha256,
        "file_count": tree.file_count,
        "total_bytes": tree.total_bytes,
        "archive_sha256": archive_sha256,
    }:
        raise DependencyBrokerError("materialized dependency archive drifted")
    return receipt


def _required_environment_path(name: str) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise DependencyBrokerError(f"funded dependency environment is missing {name}")
    path = Path(raw).resolve()
    if not path.exists():
        raise DependencyBrokerError(f"funded dependency path does not exist: {name}")
    return path


def materialize_funded_dependencies_from_environment(
    workspace: str | os.PathLike[str],
) -> dict[str, Any] | None:
    if os.environ.get("SIGNALOS_SANDBOX_PROFILE", "").strip().lower() != "funded":
        return None
    policy = _required_environment_path("SIGNALOS_DEPENDENCY_POLICY")
    bundle = _required_environment_path("SIGNALOS_DEPENDENCY_BUNDLE")
    return materialize_dependency_bundle(workspace, policy, bundle)


def verify_funded_dependencies_from_environment(
    workspace: str | os.PathLike[str],
) -> dict[str, Any] | None:
    if os.environ.get("SIGNALOS_SANDBOX_PROFILE", "").strip().lower() != "funded":
        return None
    policy = _required_environment_path("SIGNALOS_DEPENDENCY_POLICY")
    return verify_materialized_dependencies(workspace, policy)


def funded_dependency_mount_from_environment(
    workspace: str | os.PathLike[str],
) -> dict[str, Any] | None:
    """Return immutable archive evidence for one funded container command."""
    receipt = verify_funded_dependencies_from_environment(workspace)
    if receipt is None:
        return None
    bundle = receipt.get("bundle")
    if not isinstance(bundle, dict):
        raise DependencyBrokerError("dependency receipt bundle evidence is missing")
    return {
        "archive_path": str(Path(workspace).resolve() / _MATERIALIZED_ARCHIVE_REL),
        "archive_sha256": str(bundle.get("archive_sha256") or ""),
        "tree_sha256": str(bundle.get("tree_sha256") or ""),
        "file_count": int(bundle.get("file_count") or 0),
        "total_bytes": int(bundle.get("total_bytes") or 0),
    }
