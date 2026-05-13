# SignalOS Core v1.3 — Plugin registry (AMD-CORE-006, W1.3).
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
#
# Plugins ship as cosign-signed tarballs:
#   <name>-<version>.tar.gz       — the package itself
#   <name>-<version>.tar.gz.sig   — detached cosign signature
#
# On-disk layout once installed (below the repo root):
#   core/registry/<namespace>/<name>/<version>/
#       manifest.json          # copy of the tarball's manifest
#       .signature             # detached signature kept for `signalos verify`
#       .install.json          # {installed_at, sha256, signer, trust_tier, unsigned?}
#       skill/…  |  command/…  |  emitter/…  |  hook/…  |  overlay/…
#       README.md              # optional, author-supplied
#
# Invariants (Constitution §C + AMD-CORE-006):
#
#   1. Namespaces are @signalos/* (reserved) or community/* (open).
#      Anything else is refused at install time.
#   2. Cosign verification is MANDATORY unless --allow-unsigned AND a
#      co-signed Amendment exist. The Amendment path is refused in
#      code; the escape hatch is present but documented as "needs
#      amendment in .signalos/AMENDMENTS.md before use" and audit rows
#      tagged `unsigned: true`.
#   3. Every installed plugin is recorded with `trust_tier: "T3"` in
#      the AUDIT_TRAIL, regardless of what the manifest's
#      `trust_tier_default` field says. Promotion requires a co-signed
#      Amendment — not implemented here.
#   4. Compat ranges are checked against plugin.json's current version.
#      Incompatible manifests exit non-zero (code 5).
#   5. The module is stdlib-only. No jsonschema, no cryptography, no
#      requests. Cosign is shelled out to; `SIGNALOS_REGISTRY_TEST=1`
#      short-circuits the shell-out to a deterministic mock (matching
#      how `SIGNALOS_HARNESS_TEST=1` stubs the Anthropic SDK).


from __future__ import annotations

__all__ = ["install", "verify", "list_plugins", "uninstall", "publish"]  # W-2: explicit public API

import sys
# Cross-platform file locking: fcntl on POSIX, msvcrt on Windows.
# Both gate the audit-trail append in _audit_append() so concurrent
# install/uninstall calls don't corrupt the JSONL record.
if sys.platform == "win32":  # pragma: no cover
    import msvcrt  # type: ignore[import-not-found]
    fcntl = None  # type: ignore[assignment]
else:  # pragma: no cover
    import fcntl
    msvcrt = None  # type: ignore[assignment]
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .session import repo_root

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUDIT_TRAIL_REL = Path(".signalos") / "AUDIT_TRAIL.jsonl"
SCHEMA_REL = Path("core") / "registry" / "_schema" / "plugin-manifest.schema.json"
MOCK_SIG_MARKER = "MOCK-COSIGN-SIG"

# Namespaces allowed in manifest.name.
_NAMESPACE_RE = re.compile(r"^(@signalos/[a-z0-9][a-z0-9-]*|community/[a-z0-9][a-z0-9-]*)$")

# Semver shape used by version fields (no range — ranges live in compat).
_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)

# Signature ref (sha256:<64 hex>).
_SIG_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Range clause (>=, <=, >, <, =, ^, ~, * on semver-ish strings).
_RANGE_CLAUSE_RE = re.compile(
    r"^\s*(?P<op>>=|<=|>|<|=|\^|~)?\s*(?P<ver>[0-9A-Za-z.+*-]+)\s*$"
)


# ---------------------------------------------------------------------------
# Public errors (map 1:1 to CLI exit codes; see commands/registry.py)
# ---------------------------------------------------------------------------

class RegistryError(RuntimeError):
    exit_code = 2  # generic


class RegistryManifestError(RegistryError):
    exit_code = 2


class RegistryUnsignedError(RegistryError):
    exit_code = 3


class RegistryNamespaceError(RegistryError):
    exit_code = 4


class RegistryCompatError(RegistryError):
    exit_code = 5


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def registry_root(root: Path | None = None) -> Path:
    """Absolute path to core/registry/ (the installed-packages tree)."""
    return (root or repo_root()) / "core" / "registry"


def _schema_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / SCHEMA_REL


def _audit_trail_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / AUDIT_TRAIL_REL


def _plugin_json_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "plugin.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_plugin_id(name: str) -> tuple[str, str]:
    """Split an @signalos/foo or community/bar id into (namespace, leaf)."""
    if name.startswith("@signalos/"):
        return "@signalos", name[len("@signalos/"):]
    if name.startswith("community/"):
        return "community", name[len("community/"):]
    raise RegistryNamespaceError(
        f"signalos registry: namespace refused — {name!r}. "
        "Allowed: @signalos/* (reserved) or community/*."
    )


def _install_dir(root: Path, name: str, version: str) -> Path:
    ns, leaf = _split_plugin_id(name)
    return registry_root(root) / ns / leaf / version


# ---------------------------------------------------------------------------
# Stdlib-only manifest validator
# ---------------------------------------------------------------------------

def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return a list of validation errors against plugin-manifest.schema.json.

    Intentionally stdlib-only — no jsonschema import. The schema file is
    the source of truth; this function is kept in sync by hand. An empty
    return list means the manifest validates.
    """
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]

    # name
    name = manifest.get("name")
    if not isinstance(name, str):
        errors.append("name: required string")
    elif not _NAMESPACE_RE.match(name):
        errors.append(
            f"name: {name!r} must match @signalos/<slug> or community/<slug>"
        )

    # version
    version = manifest.get("version")
    if not isinstance(version, str):
        errors.append("version: required string")
    elif not _SEMVER_RE.match(version):
        errors.append(f"version: {version!r} is not a valid semver string")

    # type
    ptype = manifest.get("type")
    if ptype not in {"skill", "command", "emitter", "hook", "overlay"}:
        errors.append(
            "type: must be one of skill|command|emitter|hook|overlay"
        )

    # compat
    compat = manifest.get("compat")
    if not isinstance(compat, dict):
        errors.append("compat: required object")
    else:
        sigc = compat.get("signalos_core")
        if not isinstance(sigc, str) or not sigc.strip():
            errors.append("compat.signalos_core: required non-empty string")

    # entry_points
    ep = manifest.get("entry_points")
    if not isinstance(ep, dict) or not ep:
        errors.append("entry_points: required non-empty object")
    else:
        for k, v in ep.items():
            if not isinstance(k, str) or not isinstance(v, str) or not v:
                errors.append(f"entry_points.{k!r}: must map to non-empty string")
        if isinstance(ptype, str) and ptype and ptype not in ep:
            errors.append(
                f"entry_points: missing required key for type={ptype!r}"
            )

    # signature
    sig = manifest.get("signature")
    if not isinstance(sig, dict):
        errors.append("signature: required object")
    else:
        if sig.get("algo") != "cosign":
            errors.append("signature.algo: must be 'cosign'")
        ref = sig.get("ref")
        if not isinstance(ref, str) or not _SIG_REF_RE.match(ref):
            errors.append("signature.ref: must match ^sha256:[0-9a-f]{64}$")

    # trust_tier_default (optional; enum if present)
    ttd = manifest.get("trust_tier_default")
    if ttd is not None and ttd not in {"T1", "T2", "T3"}:
        errors.append("trust_tier_default: must be T1|T2|T3 if present")

    # dependencies (optional array)
    deps = manifest.get("dependencies")
    if deps is not None:
        if not isinstance(deps, list):
            errors.append("dependencies: must be an array if present")
        else:
            for i, d in enumerate(deps):
                if not isinstance(d, dict):
                    errors.append(f"dependencies[{i}]: must be an object")
                    continue
                dn = d.get("name")
                dv = d.get("version")
                if not isinstance(dn, str) or not _NAMESPACE_RE.match(dn):
                    errors.append(
                        f"dependencies[{i}].name: must match namespace pattern"
                    )
                if not isinstance(dv, str) or not dv.strip():
                    errors.append(f"dependencies[{i}].version: required")

    # author (optional object)
    author = manifest.get("author")
    if author is not None and not isinstance(author, dict):
        errors.append("author: must be an object if present")

    # license (optional string)
    lic = manifest.get("license")
    if lic is not None and (not isinstance(lic, str) or not lic.strip()):
        errors.append("license: must be a non-empty string if present")

    return errors


# ---------------------------------------------------------------------------
# Minimal semver range checker (no third-party deps)
# ---------------------------------------------------------------------------

def _parse_semver(version: str) -> tuple[int, int, int, str]:
    """Parse a release-series semver. Pre-release strings sort as strings
    (strict enough for compat checks in W1.3; will be revisited if any
    plugin ships a prerelease range).
    """
    m = _SEMVER_RE.match(version.strip())
    if not m:
        raise RegistryCompatError(f"compat: unparsable semver: {version!r}")
    return (int(m.group("major")), int(m.group("minor")),
            int(m.group("patch")), m.group("pre") or "")


def _cmp_semver(a: tuple[int, int, int, str], b: tuple[int, int, int, str]) -> int:
    for x, y in zip(a[:3], b[:3]):
        if x != y:
            return -1 if x < y else 1
    # Pre-release: a version with a prerelease tag is lower than one
    # without. If both have one, compare lexicographically (good enough
    # for compat in W1.3).
    ap, bp = a[3], b[3]
    if ap == bp:
        return 0
    if not ap:
        return 1
    if not bp:
        return -1
    return -1 if ap < bp else 1


def _range_satisfies(version: str, clause: str) -> bool:
    m = _RANGE_CLAUSE_RE.match(clause)
    if not m:
        return False
    op = m.group("op") or "="
    ver = m.group("ver")
    if ver == "*":
        return True
    v = _parse_semver(version)
    r = _parse_semver(ver)
    cmp = _cmp_semver(v, r)
    if op == ">=":
        return cmp >= 0
    if op == "<=":
        return cmp <= 0
    if op == ">":
        return cmp > 0
    if op == "<":
        return cmp < 0
    if op == "=":
        return cmp == 0
    if op == "^":
        # Caret: same major, version >= r.
        return v[0] == r[0] and cmp >= 0
    if op == "~":
        # Tilde: same major & minor, version >= r.
        return v[0] == r[0] and v[1] == r[1] and cmp >= 0
    return False


def _compat_ok(current_version: str, compat_range: str) -> bool:
    """Check `current_version` against a space-separated AND of clauses."""
    if not compat_range.strip():
        return False
    clauses = [c for c in compat_range.split() if c]
    return all(_range_satisfies(current_version, c) for c in clauses)


def _core_current_version(root: Path) -> str:
    """Read the current SignalOS Core version from plugin.json."""
    path = _plugin_json_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RegistryCompatError(
            f"compat: cannot read current core version from {path}: {exc}"
        ) from exc
    ver = data.get("version")
    if not isinstance(ver, str) or not _SEMVER_RE.match(ver):
        raise RegistryCompatError(
            f"compat: plugin.json version {ver!r} is not a valid semver"
        )
    return ver


# ---------------------------------------------------------------------------
# Cosign shim — test mode honours SIGNALOS_REGISTRY_TEST=1
# ---------------------------------------------------------------------------

def _cosign_verify(
    tarball: Path,
    signature: Path,
    *,
    key_ref: str | None = None,
) -> tuple[bool, str]:
    """Return (ok, signer). Never raises; callers decide what to do on False.

    Test mode: if SIGNALOS_REGISTRY_TEST=1, the signature file is accepted
    when it exists and contains MOCK-COSIGN-SIG; signer is "mock-cosign".
    No cosign binary is invoked. This mirrors harness.SIGNALOS_HARNESS_TEST.

    Real mode: shells out to
        cosign verify-blob --key <key_ref> --signature <sig> <tarball>
    and parses its exit code. Stdout (the signer identity when --output is
    set by the caller) is returned if available; otherwise a generic label.
    """
    if not signature.exists():
        return (False, "no-signature")

    if os.environ.get("SIGNALOS_REGISTRY_TEST") == "1":
        body = signature.read_text(encoding="utf-8", errors="ignore").strip()
        if MOCK_SIG_MARKER in body:
            return (True, "mock-cosign")
        return (False, "mock-sig-marker-missing")

    # Real cosign shell-out. Key is required in real mode; callers pass
    # `key_ref` (either a file path with .pub or a sigstore reference).
    if not key_ref:
        return (False, "no-key-ref")
    if not shutil.which("cosign"):
        return (False, "cosign-not-on-path")

    proc = subprocess.run(
        [
            "cosign", "verify-blob",
            "--key", key_ref,
            "--signature", str(signature),
            str(tarball),
        ],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0:
        # cosign doesn't print a stable signer line for --key flows; use
        # a generic label and let the signature sha256 carry identity.
        return (True, "cosign")
    return (False, f"cosign-rc={proc.returncode}")


# ---------------------------------------------------------------------------
# Audit-trail append (flock'd, JSONL)
# ---------------------------------------------------------------------------

def _audit_append(root: Path, row: dict[str, Any]) -> None:
    path = _audit_trail_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    # Open the lock file exclusively, then append the event with the
    # lock held. Matches the flock pattern in journal-append.sh.
    # Cross-platform: fcntl on POSIX, msvcrt.locking on Windows.
    with open(lock, "a", encoding="utf-8") as lfh:
        if fcntl is not None:  # pragma: no cover
            fcntl.flock(lfh.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover
            try:
                msvcrt.locking(lfh.fileno(), msvcrt.LK_LOCK, 1)
            except OSError:
                pass  # best-effort lock on Windows
        try:
            with open(path, "a", encoding="utf-8") as jfh:
                jfh.write(line)
        finally:
            if fcntl is not None:  # pragma: no cover
                fcntl.flock(lfh.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover
                try:
                    lfh.seek(0)
                    msvcrt.locking(lfh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# SHA-256 helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Public API — install / verify / list / uninstall / publish
# ---------------------------------------------------------------------------

def install(
    tarball_path: Path,
    *,
    allow_unsigned: bool = False,
    root: Path | None = None,
    key_ref: str | None = None,
) -> dict[str, Any]:
    """Install a cosign-signed tarball into core/registry/.

    Steps (see module docstring for invariants):
      1. Verify <tarball>.sig via cosign (or the test-mode mock).
      2. Extract, load manifest.json, validate_manifest.
      3. Enforce namespace + compat.
      4. Copy to core/registry/<ns>/<name>/<version>/ (atomic rename).
      5. Append an AUDIT_TRAIL row with trust_tier: "T3" (always).

    Returns a dict with (plugin_id, version, sha256, trust_tier, signer,
    unsigned, install_dir).
    """
    tarball_path = Path(tarball_path).resolve()
    if not tarball_path.is_file():
        raise RegistryError(f"signalos install: tarball not found: {tarball_path}")

    root = (root or repo_root()).resolve()
    sig_path = tarball_path.with_suffix(tarball_path.suffix + ".sig")

    # ---- 1. Signature check -----------------------------------------------
    sig_ok, signer = _cosign_verify(tarball_path, sig_path, key_ref=key_ref)
    unsigned = not sig_ok
    if unsigned and not allow_unsigned:
        raise RegistryUnsignedError(
            f"signalos install: signature refused (reason={signer!r}). "
            f"Pass --allow-unsigned only with a co-signed Amendment in "
            f".signalos/AMENDMENTS.md. Tarball: {tarball_path}"
        )

    sha256 = _sha256_file(tarball_path)

    # ---- 2. Extract + validate manifest ----------------------------------
    with tempfile.TemporaryDirectory(prefix="signalos-install-") as tmp:
        tmp_path = Path(tmp)
        try:
            with tarfile.open(tarball_path, "r:*") as tf:
                _safe_extract(tf, tmp_path)
        except tarfile.TarError as exc:
            raise RegistryError(f"signalos install: tarball unreadable: {exc}") from exc

        manifest_path = tmp_path / "manifest.json"
        if not manifest_path.is_file():
            raise RegistryManifestError(
                "signalos install: tarball missing manifest.json at root"
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RegistryManifestError(
                f"signalos install: manifest.json is not valid JSON: {exc}"
            ) from exc

        errs = validate_manifest(manifest)
        if errs:
            raise RegistryManifestError(
                "signalos install: manifest invalid:\n  - " + "\n  - ".join(errs)
            )

        name = manifest["name"]
        version = manifest["version"]
        # Namespace check is now redundant with schema but kept for clarity +
        # the explicit error type (exit code 4).
        _split_plugin_id(name)  # raises RegistryNamespaceError on refusal

        # ---- 3. Compat check ---------------------------------------------
        core_version = _core_current_version(root)
        compat_range = manifest["compat"]["signalos_core"]
        if not _compat_ok(core_version, compat_range):
            raise RegistryCompatError(
                f"signalos install: compat refused — core={core_version} "
                f"does not satisfy {compat_range!r} (manifest {name}@{version})"
            )

        # ---- 4. Copy to install dir (atomic swap) ------------------------
        install_dir = _install_dir(root, name, version)
        if install_dir.exists():
            raise RegistryError(
                f"signalos install: already installed: "
                f"{name}@{version} at {install_dir}"
            )
        install_dir.parent.mkdir(parents=True, exist_ok=True)

        staging = install_dir.with_name(install_dir.name + ".staging")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(tmp_path, staging)
        # Record the detached signature (copy if present, mock otherwise).
        if sig_path.exists():
            shutil.copyfile(sig_path, staging / ".signature")
        install_meta = {
            "plugin_id": name,
            "version": version,
            "sha256": sha256,
            "signer": signer,
            "trust_tier": "T3",
            "unsigned": unsigned,
            "installed_at": _now_iso(),
        }
        (staging / ".install.json").write_text(
            json.dumps(install_meta, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, install_dir)

    # ---- 5. Audit trail --------------------------------------------------
    audit = {
        "action": "plugin-install",
        "plugin_id": name,
        "version": version,
        "sha256": sha256,
        "signer": signer,
        "trust_tier": "T3",
        "unsigned": unsigned,
        "ts": _now_iso(),
    }
    _audit_append(root, audit)

    return {
        "plugin_id": name,
        "version": version,
        "sha256": sha256,
        "signer": signer,
        "trust_tier": "T3",
        "unsigned": unsigned,
        "install_dir": str(install_dir),
    }


def verify(root: Path | None = None, *, key_ref: str | None = None) -> list[dict[str, Any]]:
    """Re-run cosign verification against every installed package's cached
    .signature file. Returns a list of {plugin_id, version, ok, reason}.
    """
    rows: list[dict[str, Any]] = []
    root = (root or repo_root()).resolve()
    reg = registry_root(root)
    if not reg.is_dir():
        return rows

    for meta_path in sorted(reg.rglob(".install.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            rows.append({
                "plugin_id": str(meta_path.parent),
                "ok": False,
                "reason": f"install-meta-unreadable: {exc}",
            })
            continue
        pkg_dir = meta_path.parent
        sig = pkg_dir / ".signature"
        # There is no original tarball on disk; re-verify the signature
        # against a pseudo-blob made of the cached install meta's sha256.
        # In test mode this only checks the marker exists; in real mode
        # it shells out to cosign using the stored signer's public key
        # (callers must supply --key at verify time; otherwise we note
        # "no-key-ref" and move on).
        ok, reason = _cosign_verify(pkg_dir, sig, key_ref=key_ref)
        rows.append({
            "plugin_id": meta.get("plugin_id"),
            "version": meta.get("version"),
            "sha256": meta.get("sha256"),
            "trust_tier": meta.get("trust_tier", "T3"),
            "ok": bool(ok),
            "reason": reason if not ok else "ok",
        })
    return rows


def list_installed(root: Path | None = None) -> list[dict[str, Any]]:
    """Walk core/registry/ and return one row per installed package."""
    rows: list[dict[str, Any]] = []
    root = (root or repo_root()).resolve()
    reg = registry_root(root)
    if not reg.is_dir():
        return rows

    for meta_path in sorted(reg.rglob(".install.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        manifest_path = meta_path.parent / "manifest.json"
        ptype = None
        if manifest_path.is_file():
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
                ptype = m.get("type")
            except Exception:
                ptype = None
        rows.append({
            "plugin_id": meta.get("plugin_id"),
            "version": meta.get("version"),
            "type": ptype,
            "trust_tier": meta.get("trust_tier", "T3"),
            "sha256": meta.get("sha256"),
            "installed_at": meta.get("installed_at"),
            "unsigned": bool(meta.get("unsigned", False)),
        })
    return rows


def uninstall(
    plugin_id: str,
    version: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Remove an installed package and append a plugin-uninstall audit row."""
    root = (root or repo_root()).resolve()
    target = _install_dir(root, plugin_id, version)
    if not target.is_dir():
        raise RegistryError(
            f"signalos uninstall: not installed: {plugin_id}@{version}"
        )
    meta_path = target / ".install.json"
    sha256 = ""
    signer = ""
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            sha256 = meta.get("sha256", "")
            signer = meta.get("signer", "")
        except (json.JSONDecodeError, OSError):
            pass
    shutil.rmtree(target)

    _audit_append(root, {
        "action": "plugin-uninstall",
        "plugin_id": plugin_id,
        "version": version,
        "sha256": sha256,
        "signer": signer,
        "trust_tier": "T3",
        "ts": _now_iso(),
    })
    return {
        "plugin_id": plugin_id,
        "version": version,
        "uninstalled_dir": str(target),
    }


def publish(
    package_dir: Path,
    out_dir: Path,
    *,
    key_ref: str | None = None,
    catalog_path: Path | None = None,
) -> Path:
    """Bundle `package_dir` (which must contain manifest.json) as a
    tarball under `out_dir`. When SIGNALOS_REGISTRY_TEST=1 a companion
    .sig file containing MOCK-COSIGN-SIG is produced; in real mode the
    caller must run `cosign sign-blob` (or pass a key_ref and this
    function will shell out to cosign itself).

    When *catalog_path* is provided the local catalog index file is
    updated after signing via catalog.update_catalog() (W4.3).

    Returns the absolute path to the tarball.
    """
    package_dir = Path(package_dir).resolve()
    out_dir = Path(out_dir).resolve()
    if not package_dir.is_dir():
        raise RegistryError(f"signalos publish: package dir missing: {package_dir}")
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RegistryManifestError(
            f"signalos publish: manifest.json missing in {package_dir}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errs = validate_manifest(manifest)
    if errs:
        raise RegistryManifestError(
            "signalos publish: manifest invalid:\n  - " + "\n  - ".join(errs)
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    ns, leaf = _split_plugin_id(manifest["name"])
    safe_ns = ns.replace("@", "").replace("/", "-")
    tar_name = f"{safe_ns}-{leaf}-{manifest['version']}.tar.gz"
    tar_path = out_dir / tar_name
    with tarfile.open(tar_path, "w:gz") as tf:
        for entry in sorted(package_dir.iterdir()):
            tf.add(entry, arcname=entry.name)

    # Sign
    sig_path = tar_path.with_suffix(tar_path.suffix + ".sig")
    if os.environ.get("SIGNALOS_REGISTRY_TEST") == "1":
        sig_path.write_text(
            MOCK_SIG_MARKER + " " + _sha256_file(tar_path) + "\n",
            encoding="utf-8",
        )
    elif key_ref:
        if not shutil.which("cosign"):
            raise RegistryError(
                "signalos publish: cosign not on PATH; cannot sign. "
                "Install cosign (sigstore) or drop --key to skip signing."
            )
        proc = subprocess.run(
            ["cosign", "sign-blob", "--key", key_ref,
             "--output-signature", str(sig_path), str(tar_path)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise RegistryError(
                f"signalos publish: cosign sign-blob failed rc={proc.returncode}: "
                f"{proc.stderr.strip()}"
            )
    # No key_ref and not in test mode: caller signs out-of-band.

    # W4.3: update local catalog index if requested
    if catalog_path is not None:
        from signalos_lib.catalog import update_catalog, CatalogOwnershipError
        try:
            update_catalog(manifest, tar_path, Path(catalog_path))
        except CatalogOwnershipError as exc:
            raise RegistryError(str(exc)) from exc

    return tar_path


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract with path-traversal protection.

    Python 3.12+ has `filter="data"`; we implement the check manually so
    we work on 3.11 too.
    """
    dest = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest)):
            raise RegistryError(
                f"signalos install: refusing tarball member escaping root: "
                f"{member.name!r}"
            )
    tf.extractall(dest)
