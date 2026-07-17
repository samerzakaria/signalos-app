#!/usr/bin/env python3
"""Repeatable, fail-closed SignalOS backend journey benchmark.

This is intentionally an external driver.  It talks to the same long-lived
NDJSON sidecar used by the desktop host, but it does not import or construct the
GateOrchestrator.  That keeps the system boundary under test honest while still
allowing deterministic, independent review of persisted gate evidence.

Live runs spend provider credit and provide the selected provider key only to
the trusted long-lived backend process.  Consequently live execution requires
both an explicit ``--live`` switch and an explicit acknowledgement.  No
credential is accepted on the command line or written to result bundles.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import importlib.metadata as importlib_metadata
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import signal
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, deque
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS_CONFIG = Path(__file__).with_name("models.json")
DEFAULT_SCENARIO = Path(__file__).with_name("scenarios") / "expense_tracker.json"
DEFAULT_CI_POLICY = Path(__file__).with_name("ci_policy.json")
DEFAULT_DEPENDENCY_POLICY = Path(__file__).with_name("dependencies") / "policy.json"
DEFAULT_ORACLE_DEPENDENCY_POLICY = (
    Path(__file__).with_name("dependencies") / "oracle-policy.json"
)
SCENARIO_ROOT = Path(__file__).with_name("scenarios")
ORACLE_ROOT = Path(__file__).with_name("oracles")
SIDECAR = ROOT / "python" / "signalos_ipc_server.py"
WINDOWS_JOB_BOOTSTRAP = Path(__file__).with_name("windows_job_bootstrap.py")
GATES = ("G0", "G1", "G2", "G3", "G4", "G5")
ORCHESTRATOR_PROFILES = ("benchmark", "production")
DEFAULT_ORCHESTRATOR_PROFILE = "benchmark"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
GITHUB_COLLECTION_MAX_TOTAL = 1_000
CI_REPOSITORY_NODE_ID = "R_kgDOSSqeCA"
CI_REPOSITORY_FULL_NAME = "samerzakaria/signalos-app"
CI_REQUIRED_BRANCH = "main"
CI_REQUIRED_WORKFLOWS = {
    277295597: ("test-automation", ".github/workflows/test-automation.yml"),
    270226986: ("Smoke", ".github/workflows/smoke.yml"),
}
REDACTED = "[REDACTED]"
ORACLE_RUNTIME_PROFILE = "oracle-playwright"
ORACLE_CHECK_TIMEOUT_MS = 15_000
ARTIFACT_MAX_FILES = 100_000
ARTIFACT_MAX_BYTES = 1024 * 1024 * 1024
ORACLE_CONTRACTS: dict[str, dict[str, Any]] = {
    "expense_tracker": {
        "oracle": "expense-tracker-black-box",
        "version": "1.1.0",
        "checks": (
            "BOOT_FORM",
            "ADD_FIELDS",
            "DELETE_DURABLE",
            "RECONCILE_DURABLE",
            "FILTER",
            "PERSIST_ADD",
        ),
    }
}

# Keep this list aligned with python/signalos_lib/harness.py.  A row receives
# only its selected credential; clean-room product commands receive none.
PROVIDER_KEY_ENVS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
    "TOGETHER_API_KEY",
    "CEREBRAS_API_KEY",
    "DASHSCOPE_API_KEY",
    "COHERE_API_KEY",
    "PERPLEXITY_API_KEY",
}

# Deliberately do not clone os.environ into a process that executes generated
# commands.  The allowlist contains only OS/runtime discovery, locale, TLS, and
# optional proxy settings.  HOME/config/cache are rebound to a row-local
# directory by ``_isolated_subprocess_env`` below.
SUBPROCESS_ENV_ALLOWLIST = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PROCESSOR_ARCHITECTURE",
    "NUMBER_OF_PROCESSORS",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "TZ",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "PLAYWRIGHT_BROWSERS_PATH",
}

PROXY_ENV_NAMES = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
}

SECRET_SHAPES = (
    re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
)


class HarnessError(RuntimeError):
    """A benchmark contract failed."""


class InfrastructureError(HarnessError):
    """The harness or its required local/provider infrastructure failed."""


class ProductFailure(HarnessError):
    """The generated product or its governance evidence failed acceptance."""


class CostGuardError(HarnessError):
    """Provider cost could not be bounded or exceeded its configured cap."""


@dataclasses.dataclass(frozen=True)
class ModelSpec:
    alias: str
    provider: str
    model: str
    key_env: str
    cohort: str


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"configuration file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc


def _read_json_bytes(payload: bytes, *, label: str) -> Any:
    """Decode one already-sealed JSON payload without reopening its path."""

    try:
        return json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON in {label}: {exc}") from exc


def _parse_model_catalog(raw: Any) -> list[ModelSpec]:
    if not isinstance(raw, dict):
        raise ValueError("model configuration must be a JSON object")
    provider = str(raw.get("provider") or "").strip().lower()
    key_env = str(raw.get("api_key_env") or "").strip()
    rows = raw.get("models")
    if not provider or not key_env or not isinstance(rows, list) or not rows:
        raise ValueError("model configuration requires provider, api_key_env, and models")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key_env):
        raise ValueError(f"invalid API key environment variable name: {key_env!r}")

    result: list[ModelSpec] = []
    seen_aliases: set[str] = set()
    seen_models: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"models[{index}] must be an object")
        alias = str(row.get("alias") or "").strip()
        model = str(row.get("id") or "").strip()
        cohort = str(row.get("cohort") or "primary").strip().lower()
        if not alias or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", alias):
            raise ValueError(f"models[{index}] has an invalid alias")
        if not model or "/" not in model or model.startswith("openrouter/"):
            raise ValueError(
                f"models[{index}] has an invalid provider model id; do not add an openrouter/ prefix"
            )
        if cohort not in {"primary", "challenger", "exploratory"}:
            raise ValueError(
                f"models[{index}] cohort must be primary, challenger, or exploratory"
            )
        if alias in seen_aliases or model in seen_models:
            raise ValueError(f"duplicate model alias or id at models[{index}]")
        seen_aliases.add(alias)
        seen_models.add(model)
        result.append(ModelSpec(alias, provider, model, key_env, cohort))
    return result


def load_model_catalog(path: Path) -> list[ModelSpec]:
    """Load and validate the versioned provider/model catalog."""

    return _parse_model_catalog(_read_json(path))


def _load_model_catalog_bytes(payload: bytes, *, label: str) -> list[ModelSpec]:
    return _parse_model_catalog(_read_json_bytes(payload, label=label))


def _flatten_model_args(requested: Sequence[str] | None) -> list[str] | None:
    if requested is None:
        return None
    flattened: list[str] = []
    for item in requested:
        flattened.extend(part.strip() for part in str(item).split(",") if part.strip())
    return flattened


def select_models(catalog: Sequence[ModelSpec], requested: Sequence[str] | None) -> list[ModelSpec]:
    """Resolve aliases/ids in caller order; fail on empty, unknown, or duplicates."""

    choices = _flatten_model_args(requested)
    if choices is None or choices == ["all"]:
        if not catalog:
            raise ValueError("the model catalog is empty")
        return list(catalog)
    if not choices:
        raise ValueError("model selection is empty")
    if "all" in choices:
        raise ValueError("'all' cannot be combined with other model selections")
    by_name = {m.alias: m for m in catalog}
    by_name.update({m.model: m for m in catalog})
    selected: list[ModelSpec] = []
    seen: set[str] = set()
    for choice in choices:
        matches = (
            [model for model in catalog if model.cohort == choice]
            if choice in {"primary", "challenger", "exploratory"}
            else [by_name[choice]] if choice in by_name else []
        )
        if not matches:
            raise ValueError(f"model or cohort {choice!r} is not-configured; use --list-models")
        for spec in matches:
            if spec.alias in seen:
                raise ValueError(f"model {choice!r} was selected more than once")
            seen.add(spec.alias)
            selected.append(spec)
    return selected


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.strip()


def load_env_file(path: Path) -> dict[str, str]:
    """Strict, non-mutating dotenv parser used only for an explicitly chosen file."""

    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read environment file: {path}") from exc
    parsed: dict[str, str] = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"malformed environment assignment on line {number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"invalid environment variable on line {number}")
        value = _strip_inline_comment(value.strip())
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif value[:1] in ("'", '"') or value[-1:] in ("'", '"'):
            raise ValueError(f"unmatched quote in environment assignment on line {number}")
        parsed[name] = value
    return parsed


def redact(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Return a recursively redacted copy suitable for logs and JSON evidence."""

    secret_values = tuple(s for s in secrets if isinstance(s, str) and s)

    def clean(item: Any) -> Any:
        if isinstance(item, str):
            text = item
            for secret in secret_values:
                text = text.replace(secret, REDACTED)
            for pattern in SECRET_SHAPES:
                text = pattern.sub(REDACTED, text)
            return text
        if isinstance(item, dict):
            return {str(k): clean(v) for k, v in item.items()}
        if isinstance(item, list):
            return [clean(v) for v in item]
        if isinstance(item, tuple):
            return tuple(clean(v) for v in item)
        if isinstance(item, set):
            return sorted((clean(v) for v in item), key=repr)
        if dataclasses.is_dataclass(item):
            return clean(dataclasses.asdict(item))
        if isinstance(item, Path):
            return str(item)
        return item

    return clean(value)


def results_exit_code(results: Sequence[dict[str, Any]]) -> int:
    return 0 if results and all(row.get("status") == "pass" for row in results) else 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_json_write(path: Path, payload: Any, secrets: Iterable[str] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(redact(payload, secrets), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with contextlib.suppress(OSError):
        os.chmod(temporary, 0o600)
    temporary.replace(path)


def _resolve_api_key(spec: ModelSpec, env_file: Path | None) -> tuple[str, str]:
    # An explicit file is an explicit account choice and therefore wins over a
    # stale ambient key.  SIGNALOS_MATRIX_ENV_FILE is the same intentional
    # choice for automation.  We never silently load the repository's .env.
    if env_file is not None:
        values = load_env_file(Path(env_file))
        key = values.get(spec.key_env, "").strip()
        if not key:
            raise ValueError(f"explicit environment file does not define {spec.key_env}")
        return key, "explicit-env-file"
    matrix_env = os.environ.get("SIGNALOS_MATRIX_ENV_FILE", "").strip()
    if matrix_env:
        values = load_env_file(Path(matrix_env))
        key = values.get(spec.key_env, "").strip()
        if not key:
            raise ValueError(f"SIGNALOS_MATRIX_ENV_FILE does not define {spec.key_env}")
        return key, "matrix-env-file"
    ambient = os.environ.get(spec.key_env, "").strip()
    if ambient:
        return ambient, "environment"
    raise ValueError(
        f"missing API key in {spec.key_env}; set it in the process or use --env-file"
    )


def _scrub_provider_keys(env: dict[str, str]) -> dict[str, str]:
    cleaned = dict(env)
    for name in PROVIDER_KEY_ENVS:
        cleaned.pop(name, None)
    return cleaned


def _isolated_subprocess_env(
    runtime_home: Path,
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal environment for code-generation and product processes."""

    runtime_home = Path(runtime_home).resolve()
    roaming = runtime_home / "appdata" / "roaming"
    local = runtime_home / "appdata" / "local"
    temporary = runtime_home / "tmp"
    cache = runtime_home / "cache"
    for directory in (runtime_home, roaming, local, temporary, cache):
        directory.mkdir(parents=True, exist_ok=True)
    env = {
        name: value
        for name, value in os.environ.items()
        if name in SUBPROCESS_ENV_ALLOWLIST and value
    }
    env.update(
        {
            "HOME": str(runtime_home),
            "USERPROFILE": str(runtime_home),
            "APPDATA": str(roaming),
            "LOCALAPPDATA": str(local),
            "XDG_CONFIG_HOME": str(runtime_home / "config"),
            "XDG_CACHE_HOME": str(cache),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "TMPDIR": str(temporary),
            "NPM_CONFIG_CACHE": str(cache / "npm"),
            "PIP_CACHE_DIR": str(cache / "pip"),
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return _scrub_provider_keys(env)


def _credential_free_host_env() -> dict[str, str]:
    """Return only local runtime discovery variables, never credentials."""

    return {
        name: value
        for name, value in os.environ.items()
        if name in SUBPROCESS_ENV_ALLOWLIST
        and name not in PROXY_ENV_NAMES
        and value
    }


def _tool_subprocess_env(runtime_home: Path) -> dict[str, str]:
    """Return a credential-free environment for trusted Git/tool subprocesses."""

    env = _isolated_subprocess_env(runtime_home)
    for name in PROXY_ENV_NAMES:
        env.pop(name, None)
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
        }
    )
    return env


def _clear_parent_environment(env: dict[str, str]) -> None:
    """Best-effort overwrite, then release, a parent-owned child environment."""

    for name, value in tuple(env.items()):
        env[name] = "\0" * len(value)
    env.clear()


def _zero_bytearray(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0


def _remove_directory_with_readback(
    path: Path,
    *,
    attempts: int = 4,
) -> tuple[bool, str]:
    """Remove one already-authorized directory and verify it is absent."""

    target = Path(path)
    last_error = ""

    def make_writable_and_retry(
        function: Callable[[str], Any],
        name: str,
        _error: tuple[type[BaseException], BaseException, Any],
    ) -> None:
        os.chmod(name, stat_module.S_IWRITE)
        function(name)

    for attempt in range(attempts):
        try:
            shutil.rmtree(target, onerror=make_writable_and_retry)
        except FileNotFoundError:
            return True, ""
        except OSError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if not target.exists() and not target.is_symlink():
            return True, ""
        if attempt + 1 < attempts:
            time.sleep(0.05 * (attempt + 1))
    return False, last_error or "directory still exists after cleanup"


def _assert_external_owned_root(path: Path) -> Path:
    """Validate a unique run root before it can ever be recursively removed."""

    raw = Path(path)
    try:
        info = raw.lstat()
    except OSError as exc:
        raise InfrastructureError("registered run root is not a real directory") from exc
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    if (
        not stat_module.S_ISDIR(info.st_mode)
        or stat_module.S_ISLNK(info.st_mode)
        or attributes & 0x0400
    ):
        raise InfrastructureError("registered run root must not be a link/reparse point")
    resolved = raw.resolve(strict=True)
    if (
        resolved.parent == resolved
        or resolved == ROOT
        or resolved in ROOT.parents
        or ROOT in resolved.parents
    ):
        raise InfrastructureError(
            "registered run root must be a unique directory outside the repository"
        )
    return resolved


def _purge_external_owned_root(path: Path) -> None:
    target = Path(path)
    if not target.exists() and not target.is_symlink():
        return
    resolved = _assert_external_owned_root(target)
    removed, error = _remove_directory_with_readback(resolved)
    if not removed:
        raise InfrastructureError(
            "unsafe retained run output could not be removed: " + error
        )


def _dependency_broker_module() -> Any:
    _lazy_signalos_path()
    from signalos_lib.product import dependency_broker

    return dependency_broker


@dataclasses.dataclass
class FundedRunContext:
    """Own one funded dependency bundle and its run-scoped HMAC key."""

    policy_path: Path
    policy: Any
    scratch_root: Path
    bundle_dir: Path
    _receipt: dict[str, Any] = dataclasses.field(repr=False)
    _attestation_key: bytearray = dataclasses.field(repr=False)
    _scan_roots: list[Path] = dataclasses.field(default_factory=list, repr=False)
    _registered_secrets: list[tuple[str, bytearray]] = dataclasses.field(
        default_factory=list,
        repr=False,
    )
    _closed: bool = dataclasses.field(default=False, init=False, repr=False)
    _close_evidence: dict[str, Any] = dataclasses.field(default_factory=dict, init=False, repr=False)

    @classmethod
    def prepare(
        cls,
        policy_path: Path,
        *,
        timeout: float,
        expected_profile: str | None = None,
    ) -> "FundedRunContext":
        broker = _dependency_broker_module()
        resolved_policy = Path(policy_path).expanduser().resolve()
        scratch_root = Path(tempfile.mkdtemp(prefix="signalos-funded-dependencies-"))
        bundle_dir = scratch_root / "bundle"
        key = bytearray()
        try:
            policy = broker.load_dependency_policy(
                resolved_policy,
                profile=expected_profile,
            )
            if expected_profile is not None and policy.profile != expected_profile:
                raise InfrastructureError(
                    "scenario profile does not match the reviewed dependency policy"
                )
            generated = secrets.token_bytes(32)
            if type(generated) is not bytes or len(generated) != 32:
                raise InfrastructureError("dependency attestation key generation failed closed")
            key.extend(generated)
            generated = b""
            receipt = broker.prepare_dependency_bundle(
                resolved_policy,
                bundle_dir,
                engine="docker",
                timeout=timeout,
                attestation_key=bytes(key),
            )
            verified = broker.verify_dependency_bundle(
                resolved_policy,
                bundle_dir,
                attestation_key=bytes(key),
            )
            provisioner = verified.get("provisioner") if isinstance(verified, dict) else None
            if verified != receipt or not isinstance(provisioner, dict):
                raise InfrastructureError("trusted dependency bundle verification disagreed with preparation")
            if provisioner.get("cleanup_verified") is not True:
                raise InfrastructureError("dependency proxy cleanup was not independently verified")
            return cls(
                policy_path=resolved_policy,
                policy=policy,
                scratch_root=scratch_root,
                bundle_dir=bundle_dir,
                _receipt=dict(verified),
                _attestation_key=key,
            )
        except BaseException as exc:
            sanitized_error = str(
                redact(
                    f"{type(exc).__name__}: {exc}",
                    (bytes(key).hex(),) if key else (),
                )
            )
            scan: dict[str, Any] = {"ok": False, "hits": [], "errors": []}
            removed = False
            cleanup_error = "cleanup did not run"
            try:
                scan = _scan_exact_secret_values(
                    (scratch_root,), _attestation_needles(key)
                )
                removed, cleanup_error = _remove_directory_with_readback(
                    scratch_root
                )
            finally:
                _zero_bytearray(key)
            if not scan["ok"]:
                raise InfrastructureError(
                    "dependency bundle preparation left unverifiable secret evidence"
                ) from None
            if not removed:
                raise InfrastructureError(
                    "failed dependency bundle preparation could not clean its scratch root: "
                    + cleanup_error
                ) from None
            if not isinstance(exc, Exception):
                raise
            if isinstance(exc, HarnessError):
                raise
            raise InfrastructureError(
                "trusted dependency bundle preparation failed: " + sanitized_error
            ) from None

    def __enter__(self) -> "FundedRunContext":
        if self._closed:
            raise InfrastructureError("funded run context is already closed")
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    def _key_bytes(self) -> bytes:
        if self._closed or len(self._attestation_key) != 32:
            raise InfrastructureError("funded dependency attestation key is unavailable")
        return bytes(self._attestation_key)

    def _key_hex(self) -> str:
        return self._key_bytes().hex()

    def redaction_secrets(self, provider_key: str = "") -> tuple[str, ...]:
        values = [self._key_hex()]
        if provider_key:
            values.insert(0, provider_key)
        return tuple(values)

    def attestation_scan_needles(self) -> tuple[tuple[str, bytes], ...]:
        """Return exact in-memory secret forms for retained-file scanning only."""

        return _attestation_needles(self._attestation_key)

    def register_scan_root(self, root: Path) -> None:
        resolved = _assert_external_owned_root(root)
        if resolved not in self._scan_roots:
            self._scan_roots.append(resolved)

    def register_exact_secret(self, kind: str, value: str) -> None:
        if self._closed:
            raise InfrastructureError("cannot register a secret on a closed funded context")
        encoded = bytearray(value.encode("utf-8"))
        if encoded:
            self._registered_secrets.append((str(kind), encoded))

    def docker_binding(self) -> dict[str, str]:
        provisioner = self._receipt.get("provisioner")
        if not isinstance(provisioner, dict):
            raise InfrastructureError("dependency receipt has no provisioner binding")
        binding = {
            "host_trust_profile": str(
                provisioner.get("host_trust_profile") or ""
            ),
            "docker_endpoint": str(provisioner.get("docker_endpoint") or ""),
            "daemon_os_type": str(provisioner.get("daemon_os_type") or ""),
        }
        if (
            not binding["host_trust_profile"]
            or not binding["docker_endpoint"]
            or binding["daemon_os_type"] != "linux"
        ):
            raise InfrastructureError(
                "dependency receipt has an invalid Docker endpoint binding"
            )
        return binding

    def sidecar_environment(
        self,
        runtime_home: Path,
        *,
        spec: ModelSpec | None = None,
        provider_key: str = "",
        expected_git_remote: Path | None = None,
    ) -> dict[str, str]:
        if (spec is None) != (not provider_key):
            raise ValueError("provider model and key must be supplied together")
        if spec is not None and expected_git_remote is None:
            raise InfrastructureError(
                "funded model execution requires an expected local Git remote"
            )
        docker_binding = self.docker_binding()
        runtime_home = Path(runtime_home).resolve()
        disabled_hooks = runtime_home / "git-hooks-disabled"
        disabled_hooks.mkdir(parents=True, exist_ok=True)
        if any(disabled_hooks.iterdir()):
            raise InfrastructureError(
                "funded Git disabled-hooks directory is not empty"
            )
        env: dict[str, str] = {}
        try:
            env = _isolated_subprocess_env(
                runtime_home,
                extra={
                    "SIGNALOS_SANDBOX": "docker",
                    "SIGNALOS_SANDBOX_PROFILE": "funded",
                    "SIGNALOS_SANDBOX_IMAGE": str(self.policy.image),
                    "SIGNALOS_SANDBOX_NETWORK": "none",
                    "SIGNALOS_SANDBOX_PULL": "never",
                    "SIGNALOS_SANDBOX_READONLY": "1",
                    "SIGNALOS_SANDBOX_STRICT": "1",
                    "SIGNALOS_DEPENDENCY_POLICY": str(self.policy_path),
                    "SIGNALOS_DEPENDENCY_BUNDLE": str(self.bundle_dir),
                    "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY": self._key_hex(),
                    "SIGNALOS_FUNDED_GIT_HOOKS_DIR": str(disabled_hooks),
                    "DOCKER_HOST": docker_binding["docker_endpoint"],
                },
            )
            if spec is not None:
                expected_remote = Path(expected_git_remote).resolve()
                env.update(
                    {
                        "SIGNALOS_LLM_PROVIDER": spec.provider,
                        "SIGNALOS_LLM_MODEL": spec.model,
                        "SIGNALOS_FUNDED_EXPECTED_GIT_REMOTE": str(
                            expected_remote
                        ),
                        spec.key_env: provider_key,
                    }
                )
            return env
        except BaseException:
            _clear_parent_environment(env)
            raise

    def _receipt_evidence(self, receipt: dict[str, Any]) -> dict[str, Any]:
        def selected(name: str, allowed: tuple[str, ...]) -> dict[str, Any]:
            value = receipt.get(name)
            if not isinstance(value, dict):
                return {}
            return {key: value.get(key) for key in allowed if key in value}

        return {
            "schema": receipt.get("schema"),
            "status": receipt.get("status"),
            "profile": receipt.get("profile"),
            "platform": receipt.get("platform"),
            "image": receipt.get("image"),
            "policy_sha256": receipt.get("policy_sha256"),
            "broker_sha256": receipt.get("broker_sha256"),
            "attestation_key_id": receipt.get("attestation_key_id"),
            "receipt_sha256": receipt.get("receipt_sha256"),
            "inputs": selected(
                "inputs",
                (
                    "package_json_sha256",
                    "package_lock_sha256",
                    "lockfile_version",
                    "resolved_urls_sha256",
                    "package_count",
                ),
            ),
            "provisioner": selected(
                "provisioner",
                (
                    "schema",
                    "engine",
                    "host_trust_profile",
                    "docker_endpoint",
                    "daemon_os_type",
                    "platform",
                    "installer_image",
                    "proxy_image",
                    "runtime_image_id",
                    "proxy_script_sha256",
                    "runner_sha256",
                    "egress_policy",
                    "allowed_connect_authorities",
                    "installer_network",
                    "proxy_egress_network",
                    "tls_mode",
                    "pull_policy",
                    "cleanup_verified",
                ),
            ),
            "bundle": selected(
                "bundle",
                (
                    "tree_sha256",
                    "file_count",
                    "total_bytes",
                    "archive_sha256",
                ),
            ),
        }

    def public_evidence(self) -> dict[str, Any]:
        return self._receipt_evidence(self._receipt)

    def evidence_hashes(self) -> dict[str, str]:
        evidence = self.public_evidence()
        inputs = evidence["inputs"]
        bundle = evidence["bundle"]
        provisioner = evidence["provisioner"]
        return {
            "dependency_policy_sha256": str(evidence.get("policy_sha256") or ""),
            "dependency_package_json_sha256": str(inputs.get("package_json_sha256") or ""),
            "dependency_package_lock_sha256": str(inputs.get("package_lock_sha256") or ""),
            "dependency_proxy_script_sha256": str(provisioner.get("proxy_script_sha256") or ""),
            "dependency_bundle_archive_sha256": str(bundle.get("archive_sha256") or ""),
            "dependency_attestation_key_id": str(evidence.get("attestation_key_id") or ""),
        }

    def materialize_after_init(self, workspace: Path) -> dict[str, Any]:
        root = Path(workspace).resolve()
        if not (root / ".signalos" / "INIT_COMPLETE.json").is_file():
            raise InfrastructureError("dependency materialization requires completed signal-init")
        broker = _dependency_broker_module()
        try:
            receipt = broker.materialize_dependency_bundle(
                root,
                self.policy_path,
                self.bundle_dir,
                attestation_key=self._key_bytes(),
            )
        except Exception as exc:
            raise InfrastructureError(
                "trusted dependency materialization failed: "
                + str(redact(f"{type(exc).__name__}: {exc}", self.redaction_secrets()))
            ) from None
        return self._receipt_evidence(receipt)

    def verify_materialized_after_init(self, workspace: Path) -> dict[str, Any]:
        root = Path(workspace).resolve()
        if not (root / ".signalos" / "INIT_COMPLETE.json").is_file():
            raise InfrastructureError("dependency verification requires completed signal-init")
        broker = _dependency_broker_module()
        try:
            receipt = broker.verify_materialized_dependencies(
                root,
                self.policy_path,
                attestation_key=self._key_bytes(),
            )
        except Exception as exc:
            raise InfrastructureError(
                "materialized dependency verification failed: "
                + str(redact(f"{type(exc).__name__}: {exc}", self.redaction_secrets()))
            ) from None
        return self._receipt_evidence(receipt)

    def _materialize_for_isolated_execution(
        self,
        workspace: Path,
    ) -> tuple[dict[str, Any], Any]:
        """Bind one fresh workspace to this context's reviewed dependency bytes."""

        root = Path(workspace).resolve()
        broker = _dependency_broker_module()
        try:
            receipt = broker.materialize_dependency_bundle(
                root,
                self.policy_path,
                self.bundle_dir,
                attestation_key=self._key_bytes(),
            )
            verified = broker.verify_materialized_dependencies(
                root,
                self.policy_path,
                attestation_key=self._key_bytes(),
            )
        except Exception as exc:
            raise InfrastructureError(
                "isolated dependency materialization failed: "
                + str(
                    redact(
                        f"{type(exc).__name__}: {exc}",
                        self.redaction_secrets(),
                    )
                )
            ) from None
        if receipt != verified:
            raise InfrastructureError(
                "isolated dependency materialization verification disagreed"
            )
        public = self._receipt_evidence(verified)
        bundle = public.get("bundle")
        if not isinstance(bundle, dict):
            raise InfrastructureError("isolated dependency receipt has no bundle")
        _lazy_signalos_path()
        from signalos_lib.product.sandbox import DependencyMount

        mount = DependencyMount(
            archive_path=root
            / ".signalos"
            / "dependencies"
            / "node_modules.tar",
            archive_sha256=str(bundle.get("archive_sha256") or ""),
            tree_sha256=str(bundle.get("tree_sha256") or ""),
            file_count=int(bundle.get("file_count") or 0),
            total_bytes=int(bundle.get("total_bytes") or 0),
        )
        return public, mount

    @contextlib.contextmanager
    def _bound_docker_runtime(
        self,
        timeout: float,
    ) -> Iterable[tuple[Callable[..., subprocess.CompletedProcess[str]], dict[str, str]]]:
        """Yield a credential-free Docker CLI pinned to the attested endpoint."""

        binding = self.docker_binding()
        docker_endpoint = binding["docker_endpoint"]
        with tempfile.TemporaryDirectory(
            prefix="signalos-funded-docker-control-"
        ) as control_home:
            docker_env = _tool_subprocess_env(Path(control_home))
            docker_env["DOCKER_HOST"] = docker_endpoint

            def bound_docker_runtime(
                argv: Sequence[str], **kwargs: Any
            ) -> subprocess.CompletedProcess[str]:
                command = list(argv)
                executable = Path(command[0]).name.lower() if command else ""
                if executable not in {"docker", "docker.exe"}:
                    raise InfrastructureError(
                        "funded runner attempted a non-Docker control-plane command"
                    )
                if "--host" in command[1:]:
                    raise InfrastructureError(
                        "funded runner attempted to override the attested Docker endpoint"
                    )
                command = [command[0], "--host", docker_endpoint, *command[1:]]
                kwargs["env"] = dict(docker_env)
                kwargs["shell"] = False
                return subprocess.run(command, **kwargs)

            try:
                daemon_probe = bound_docker_runtime(
                    ["docker", "info", "--format", "{{.OSType}}"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=min(timeout, 30.0),
                    check=False,
                )
                if (
                    daemon_probe.returncode != 0
                    or daemon_probe.stdout.strip() != binding["daemon_os_type"]
                ):
                    raise InfrastructureError(
                        "funded Docker endpoint does not expose the attested Linux daemon"
                    )
                yield bound_docker_runtime, binding
            finally:
                _clear_parent_environment(docker_env)

    def run_offline_command(
        self,
        workspace: Path,
        command: str,
        *,
        timeout: float,
        writable_paths: tuple[str, ...] = (),
        env: dict[str, str] | None = None,
        secrets_to_redact: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Run one command in a fresh, offline, dependency-attested container."""

        receipt, mount = self._materialize_for_isolated_execution(workspace)
        try:
            _lazy_signalos_path()
            from signalos_lib.product.sandbox import ContainerRunner

            with self._bound_docker_runtime(timeout) as (runtime, binding):
                runner = ContainerRunner(
                    workspace,
                    engine="docker",
                    image=str(self.policy.image),
                    network="none",
                    read_only=True,
                    pull="never",
                    hardened=True,
                    workspace_read_only=True,
                    writable_paths=writable_paths,
                    platform=str(self.policy.platform),
                    dependency_mount=mount,
                    require_funded_dependencies=True,
                    runner=runtime,
                )
                exit_code, output = runner.run(
                    command,
                    workspace,
                    timeout,
                    env or {},
                )
        except InfrastructureError:
            raise
        except Exception as exc:
            raise InfrastructureError(
                "offline funded command infrastructure failed: "
                + str(
                    redact(
                        f"{type(exc).__name__}: {exc}",
                        (*self.redaction_secrets(), *tuple(secrets_to_redact)),
                    )
                )
            ) from None
        redaction_values = (*self.redaction_secrets(), *tuple(secrets_to_redact))
        return {
            "ok": exit_code == 0 and not output.timed_out,
            "returncode": exit_code,
            "timed_out": bool(output.timed_out),
            "stdout_tail": redact((output.stdout or "")[-8000:], redaction_values),
            "stderr_tail": redact((output.stderr or "")[-8000:], redaction_values),
            "dependencies": receipt,
            "container": {
                "engine": "docker",
                "image": str(self.policy.image),
                "platform": str(self.policy.platform),
                "network": "none",
                "pull": "never",
                "root_read_only": True,
                "workspace_read_only": True,
                "dependencies_read_only": True,
                "writable_paths": list(writable_paths),
                "capabilities": "none",
                "no_new_privileges": True,
                **binding,
            },
        }

    def browser_runtime_probe(self, *, timeout: float) -> dict[str, Any]:
        if self.policy.profile != ORACLE_RUNTIME_PROFILE:
            raise InfrastructureError("browser probe requires the oracle runtime profile")
        workspace = self.scratch_root / "browser-runtime-probe"
        workspace.mkdir(exist_ok=False)
        shutil.copy2(self.policy.package_json, workspace / "package.json")
        shutil.copy2(self.policy.package_lock, workspace / "package-lock.json")
        probe = workspace / "probe.mjs"
        probe.write_text(
            "import { chromium } from 'playwright';\n"
            "const browser = await chromium.launch({ headless: true });\n"
            "const page = await browser.newPage();\n"
            "await page.setContent('<title>SignalOS oracle probe</title>');\n"
            "if (await page.title() !== 'SignalOS oracle probe') throw new Error('title mismatch');\n"
            "await browser.close();\n"
            "process.stdout.write('SIGNALOS_ORACLE_RUNTIME_OK');\n",
            encoding="utf-8",
        )
        result = self.run_offline_command(
            workspace,
            "node probe.mjs",
            timeout=timeout,
            env={"CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"},
        )
        if not result["ok"] or "SIGNALOS_ORACLE_RUNTIME_OK" not in str(
            result.get("stdout_tail") or ""
        ):
            raise InfrastructureError(
                "offline Playwright/Chromium runtime probe did not complete"
            )
        return result

    def offline_probe(self, workspace: Path, *, timeout: float) -> dict[str, Any]:
        receipt = self.verify_materialized_after_init(workspace)
        bundle = receipt["bundle"]
        docker_binding = self.docker_binding()
        docker_endpoint = docker_binding["docker_endpoint"]
        _lazy_signalos_path()
        from signalos_lib.product.sandbox import ContainerRunner, DependencyMount

        mount = DependencyMount(
            archive_path=Path(workspace).resolve()
            / ".signalos"
            / "dependencies"
            / "node_modules.tar",
            archive_sha256=str(bundle.get("archive_sha256") or ""),
            tree_sha256=str(bundle.get("tree_sha256") or ""),
            file_count=int(bundle.get("file_count") or 0),
            total_bytes=int(bundle.get("total_bytes") or 0),
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix="signalos-funded-docker-control-"
            ) as control_home:
                docker_env = _tool_subprocess_env(Path(control_home))
                docker_env["DOCKER_HOST"] = docker_endpoint
                try:
                    def bound_docker_runtime(
                        argv: Sequence[str], **kwargs: Any
                    ) -> subprocess.CompletedProcess[str]:
                        command = list(argv)
                        executable = Path(command[0]).name.lower() if command else ""
                        if executable not in {"docker", "docker.exe"}:
                            raise InfrastructureError(
                                "funded runner attempted a non-Docker control-plane command"
                            )
                        command = [
                            command[0], "--host", docker_endpoint, *command[1:]
                        ]
                        kwargs["env"] = dict(docker_env)
                        return subprocess.run(command, **kwargs)

                    daemon_probe = bound_docker_runtime(
                        ["docker", "info", "--format", "{{.OSType}}"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=min(timeout, 30.0),
                        check=False,
                    )
                    if (
                        daemon_probe.returncode != 0
                        or daemon_probe.stdout.strip()
                        != docker_binding["daemon_os_type"]
                    ):
                        raise InfrastructureError(
                            "funded Docker endpoint does not expose the attested Linux daemon"
                        )
                    runner = ContainerRunner(
                        workspace,
                        engine="docker",
                        image=str(self.policy.image),
                        network="none",
                        read_only=True,
                        pull="never",
                        hardened=True,
                        workspace_read_only=True,
                        writable_paths=("dist",),
                        platform=str(self.policy.platform),
                        dependency_mount=mount,
                        require_funded_dependencies=True,
                        runner=bound_docker_runtime,
                    )
                    exit_code, output = runner.run(
                        "node -e \"for (const name of ['react','vite','vitest']) "
                        "require.resolve(name); process.stdout.write('SIGNALOS_DEPENDENCIES_OK')\"",
                        workspace,
                        timeout,
                        {"CI": "1", "FORCE_COLOR": "0"},
                    )
                finally:
                    _clear_parent_environment(docker_env)
        except Exception as exc:
            raise InfrastructureError(
                "offline funded dependency probe failed: "
                + str(redact(f"{type(exc).__name__}: {exc}", self.redaction_secrets()))
            ) from None
        if output.timed_out or exit_code != 0 or "SIGNALOS_DEPENDENCIES_OK" not in output.stdout:
            raise InfrastructureError("offline funded dependency probe did not complete successfully")
        return {
            "ok": True,
            "engine": "docker",
            "network": "none",
            "pull": "never",
            "image": str(self.policy.image),
            **docker_binding,
            "dependencies_read_only": True,
        }

    def close(self) -> dict[str, Any]:
        if self._closed:
            evidence = dict(self._close_evidence)
            if evidence.get("scratch_removed") is not True:
                removed, _error = _remove_directory_with_readback(self.scratch_root)
                evidence["scratch_removed"] = removed
                self._close_evidence = dict(evidence)
                if not removed:
                    raise InfrastructureError(
                        "funded dependency scratch cleanup failed"
                    )
            if (evidence.get("secret_scan") or {}).get("ok") is not True:
                if evidence.get("retained_roots_removed") is not True:
                    removal_ok = True
                    for root in self._scan_roots:
                        removed, _error = _remove_directory_with_readback(root)
                        removal_ok = removal_ok and removed
                    evidence["retained_roots_removed"] = removal_ok
                    self._close_evidence = dict(evidence)
                    if not removal_ok:
                        raise InfrastructureError(
                            "funded-run secret evidence was unverifiable and retained "
                            "output could not be removed"
                        )
                raise InfrastructureError(
                    "funded-run secret evidence was unverifiable"
                )
            return evidence
        registered_roots = list(self._scan_roots)
        roots = [self.scratch_root, *registered_roots]
        needles = list(_attestation_needles(self._attestation_key))
        needles.extend(
            (kind, bytes(value)) for kind, value in self._registered_secrets
        )
        scan = _scan_exact_secret_values(roots, needles)
        scratch_removed = False
        cleanup_error = ""
        retained_cleanup_errors: list[str] = []
        retained_roots_removed = False
        try:
            scratch_removed, cleanup_error = _remove_directory_with_readback(
                self.scratch_root
            )
            if not scan["ok"]:
                for root in registered_roots:
                    removed, error = _remove_directory_with_readback(root)
                    if not removed:
                        retained_cleanup_errors.append(error)
                retained_roots_removed = all(
                    not root.exists() and not root.is_symlink()
                    for root in registered_roots
                )
        finally:
            _zero_bytearray(self._attestation_key)
            for _kind, value in self._registered_secrets:
                _zero_bytearray(value)
            self._registered_secrets.clear()
            self._closed = True
            self._close_evidence = {
                "secret_scan": scan,
                "scratch_removed": scratch_removed
                and not self.scratch_root.exists(),
                "retained_roots_removed": retained_roots_removed
                if not scan["ok"]
                else None,
                "key_zeroed": all(value == 0 for value in self._attestation_key),
            }
            if scratch_removed and (scan["ok"] or retained_roots_removed):
                self._scan_roots.clear()
        if not scan["ok"]:
            if retained_cleanup_errors or not retained_roots_removed:
                raise InfrastructureError(
                    "funded-run secret evidence was unverifiable and retained output "
                    "could not be removed"
                )
            raise InfrastructureError(
                "funded-run secret evidence was unverifiable; retained output was removed"
            )
        if cleanup_error or not scratch_removed or self.scratch_root.exists():
            raise InfrastructureError("funded dependency scratch cleanup failed")
        return dict(self._close_evidence)


class OpenRouterClient:
    def __init__(self, key: str, timeout: float = 30.0) -> None:
        self._key = key
        self._timeout = timeout

    def _get(self, endpoint: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{OPENROUTER_BASE}{endpoint}",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Accept": "application/json",
                "User-Agent": "SignalOS-backend-matrix/1",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            # Never include provider response bodies: an upstream/proxy error can
            # echo authorization material or account details.
            raise InfrastructureError(f"OpenRouter request {endpoint} failed with HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise InfrastructureError(f"OpenRouter request {endpoint} failed: {type(exc).__name__}") from exc
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InfrastructureError(f"OpenRouter returned invalid JSON for {endpoint}") from exc
        if not isinstance(payload, dict):
            raise InfrastructureError(f"OpenRouter returned an invalid payload for {endpoint}")
        return payload

    def models(self) -> dict[str, dict[str, Any]]:
        payload = self._get("/models")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise InfrastructureError("OpenRouter model catalog has no data list")
        return {
            str(row.get("id")): row
            for row in rows
            if isinstance(row, dict) and row.get("id")
        }

    def key_info(self) -> dict[str, Any]:
        payload = self._get("/key")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise InfrastructureError("OpenRouter key response has no data object")
        return data

    def usage(self) -> float:
        data = self.key_info()
        for field in ("usage", "total_usage", "usage_monthly"):
            value = data.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            if isinstance(value, str):
                with contextlib.suppress(ValueError):
                    return float(value)
        raise InfrastructureError("OpenRouter key response does not expose numeric usage")


class CostGuard:
    """Cumulative per-row usage monitor.

    Provider reporting can lag, so this is a fail-closed tripwire rather than a
    substitute for the required provider-side low spending limit.
    """

    def __init__(self, client: OpenRouterClient, cap: float, interval: float = 20.0) -> None:
        self.client = client
        self.cap = float(cap)
        self.interval = interval
        self.started_usage = client.usage()
        self.last_usage = self.started_usage
        self.last_checked = 0.0
        self.failures = 0
        self.backward_observations = 0

    @property
    def spent(self) -> float:
        return max(0.0, self.last_usage - self.started_usage)

    def check(self, *, force: bool = False) -> float:
        now = time.monotonic()
        if not force and now - self.last_checked < self.interval:
            return self.spent
        self.last_checked = now
        try:
            observed_usage = self.client.usage()
            self.failures = 0
        except InfrastructureError as exc:
            self.failures += 1
            if self.failures >= 2:
                raise CostGuardError("provider usage monitoring failed twice; aborting fail-closed") from exc
            return self.spent
        if observed_usage + 1e-9 < self.last_usage:
            # OpenRouter's key-usage endpoint is eventually consistent: a read
            # served by a lagging replica can transiently DIP below an earlier
            # reading (observed live: a healthy run was killed mid-G0 by one
            # dip). A dip can never help a run evade the cap, because spent is
            # measured against the MAXIMUM counter ever observed (monotonic
            # watermark) -- ignoring the lower reading only ever OVER-estimates
            # spend, which trips the cap EARLIER. Fail-closed is preserved;
            # record the anomaly as evidence instead of killing the run.
            self.backward_observations += 1
        else:
            self.last_usage = observed_usage
        if self.spent > self.cap:
            raise CostGuardError(
                f"provider-reported row usage ${self.spent:.4f} exceeded the ${self.cap:.4f} cap"
            )
        return self.spent


def _command_exists(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise InfrastructureError(f"required command is not installed: {name}")
    return found


def _run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 600,
    secrets: Iterable[str] = (),
) -> dict[str, Any]:
    started = time.monotonic()
    creationflags = 0
    popen_options: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_options["start_new_session"] = True
    process = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        env=env if env is not None else _credential_free_host_env(),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=creationflags,
        **popen_options,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        with contextlib.suppress(Exception):
            stdout, stderr = process.communicate(timeout=5)
        stdout = locals().get("stdout", exc.stdout or "")
        stderr = locals().get("stderr", exc.stderr or "")
        return {
            "ok": False,
            "timed_out": True,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": redact((stdout or "")[-8000:], secrets),
            "stderr_tail": redact((stderr or "")[-8000:], secrets),
        }
    return {
        "ok": process.returncode == 0,
        "returncode": process.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": redact(stdout[-8000:], secrets),
        "stderr_tail": redact(stderr[-8000:], secrets),
    }


def _prepare_local_release_remote(
    workspace: Path,
    remote: Path,
    *,
    env: dict[str, str],
) -> dict[str, Any]:
    """Create an offline origin so G5 exercises a real commit and push.

    Benchmark rows must never depend on the user's GitHub credentials, but a
    release-pending result would leave the G5 state machine untested.  A bare
    row-local repository provides the same Git transport semantics without an
    external side effect.
    """
    commands = [
        (["git", "init"], workspace),
        (["git", "config", "user.name", "SignalOS Backend Matrix"], workspace),
        (["git", "config", "user.email", "matrix@signalos.invalid"], workspace),
        (["git", "add", "-A"], workspace),
        (["git", "commit", "-m", "SignalOS matrix baseline"], workspace),
    ]
    remote.mkdir(parents=True, exist_ok=False)
    commands.extend([
        (["git", "init", "--bare"], remote),
        (["git", "remote", "add", "origin", str(remote.resolve())], workspace),
        (["git", "push", "-u", "origin", "HEAD"], workspace),
    ])
    evidence: list[dict[str, Any]] = []
    for argv, cwd in commands:
        result = _run_command(argv, cwd=cwd, env=env, timeout=60)
        evidence.append({"argv": argv, **result})
        if not result.get("ok"):
            raise InfrastructureError(
                f"could not prepare row-local release origin ({' '.join(argv)}): "
                f"{result.get('stderr_tail') or result.get('stdout_tail')}"
            )
    return {
        "kind": "row-local-bare-git-origin",
        "path": str(remote.resolve()),
        "commands": evidence,
    }


def _checkout_pushed_release(
    remote: Path,
    destination: Path,
    finalization: dict[str, Any],
    *,
    env: dict[str, str],
) -> dict[str, Any]:
    """Materialize and verify the exact remote bytes named by the G5 receipt."""

    outcome = finalization.get("outcome")
    if not isinstance(outcome, dict):
        raise ProductFailure("release finalization has no outcome receipt")
    commit = outcome.get("commit")
    push = outcome.get("push")
    if not isinstance(commit, dict) or not isinstance(push, dict):
        raise ProductFailure("release finalization omits commit or push receipt")
    expected_sha = str(commit.get("sha") or "").strip().lower()
    pushed_sha = str(push.get("sha") or "").strip().lower()
    ref = str(push.get("ref") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected_sha):
        raise ProductFailure("release commit receipt has an invalid object id")
    if pushed_sha != expected_sha:
        raise ProductFailure("release push receipt does not match the release commit")
    if not re.fullmatch(r"refs/heads/[A-Za-z0-9._/-]+", ref) or ".." in ref:
        raise ProductFailure("release push receipt has an invalid branch ref")
    if push.get("status") != "ok" or push.get("verified") is not True:
        raise ProductFailure("release push receipt is not independently verified")
    branch = ref.removeprefix("refs/heads/")
    remote = Path(remote).resolve()
    if not remote.is_dir():
        raise InfrastructureError("row-local release origin is missing")
    if destination.exists():
        raise InfrastructureError("release checkout destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)

    commands: dict[str, Any] = {}
    commands["ls_remote"] = _run_command(
        ["git", "ls-remote", "--exit-code", str(remote), ref],
        cwd=destination.parent,
        env=env,
        timeout=60,
    )
    rows = [
        line.split()
        for line in commands["ls_remote"].get("stdout_tail", "").splitlines()
        if line.strip()
    ]
    if not commands["ls_remote"].get("ok") or rows != [[expected_sha, ref]]:
        raise ProductFailure("row-local origin no longer exposes the exact G5 commit")

    commands["clone"] = _run_command(
        [
            "git", "clone", "--no-local", "--no-hardlinks", "--single-branch",
            "--branch", branch, str(remote), str(destination),
        ],
        cwd=destination.parent,
        env=env,
        timeout=120,
    )
    if not commands["clone"].get("ok"):
        raise InfrastructureError("could not clone the G5 release origin")
    commands["head"] = _run_command(
        ["git", "rev-parse", "HEAD"], cwd=destination, env=env, timeout=30,
    )
    actual_sha = str(commands["head"].get("stdout_tail") or "").strip().lower()
    if not commands["head"].get("ok") or actual_sha != expected_sha:
        raise ProductFailure("fresh release checkout HEAD does not match the G5 receipt")
    commands["status"] = _run_command(
        ["git", "status", "--porcelain"], cwd=destination, env=env, timeout=30,
    )
    if not commands["status"].get("ok") or str(
        commands["status"].get("stdout_tail") or ""
    ).strip():
        raise ProductFailure("fresh release checkout is not clean")
    return {
        "verified": True,
        "ref": ref,
        "commit": expected_sha,
        "commands": commands,
    }


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Stop only the owned process group/tree; never use a global image kill."""

    if process.poll() is not None:
        return
    if os.name == "nt":
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
                shell=False,
                env=_credential_free_host_env(),
            )
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        with contextlib.suppress(Exception):
            process.wait(timeout=5)
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(Exception):
        process.kill()
    with contextlib.suppress(Exception):
        process.wait(timeout=5)
    if process.poll() is None:
        raise InfrastructureError("owned subprocess tree did not terminate")


class _WindowsKillOnCloseJob:
    """Minimal verified Windows Job Object wrapper for one owned process tree."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self) -> None:
        if os.name != "nt":
            raise InfrastructureError("Windows Job Objects are unavailable on this platform")
        import ctypes
        from ctypes import wintypes

        size_t = ctypes.c_size_t

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", size_t),
                ("MaximumWorkingSetSize", size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", size_t),
                ("JobMemoryLimit", size_t),
                ("PeakProcessMemoryUsed", size_t),
                ("PeakJobMemoryUsed", size_t),
            ]

        class BasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        kernel32.IsProcessInJob.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise InfrastructureError(
                f"could not create Windows Job Object (error {ctypes.get_last_error()})"
            )
        self._ctypes = ctypes
        self._wintypes = wintypes
        self._kernel32 = kernel32
        self._handle = handle
        self._accounting_type = BasicAccountingInformation
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle,
            self._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            self._handle = None
            raise InfrastructureError(
                f"could not set kill-on-close Job Object policy (error {error})"
            )

    def assign(self, process: subprocess.Popen[Any]) -> None:
        raw_process_handle = getattr(process, "_handle", None)
        if raw_process_handle is None:
            raise InfrastructureError("Windows sidecar process exposes no assignable handle")
        process_handle = self._wintypes.HANDLE(int(raw_process_handle))
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise InfrastructureError(
                "could not assign sidecar bootstrap to its Windows Job Object "
                f"(error {self._ctypes.get_last_error()})"
            )
        is_member = self._wintypes.BOOL(False)
        if not self._kernel32.IsProcessInJob(
            process_handle,
            self._handle,
            self._ctypes.byref(is_member),
        ) or not bool(is_member.value):
            raise InfrastructureError(
                "Windows could not verify sidecar bootstrap Job Object membership"
            )

    def active_processes(self) -> int:
        if self._handle is None:
            return 0
        accounting = self._accounting_type()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            1,
            self._ctypes.byref(accounting),
            self._ctypes.sizeof(accounting),
            None,
        ):
            raise InfrastructureError(
                "could not query the owned Windows Job Object "
                f"(error {self._ctypes.get_last_error()})"
            )
        return int(accounting.ActiveProcesses)

    def terminate(self) -> None:
        if self._handle is None:
            return
        if self.active_processes() and not self._kernel32.TerminateJobObject(
            self._handle, 1
        ):
            raise InfrastructureError(
                "could not terminate the owned Windows Job Object "
                f"(error {self._ctypes.get_last_error()})"
            )
        deadline = time.monotonic() + 10
        while self.active_processes():
            if time.monotonic() >= deadline:
                raise InfrastructureError(
                    "owned Windows Job Object retained active descendant processes"
                )
            time.sleep(0.02)

    def close(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        if not self._kernel32.CloseHandle(handle):
            raise InfrastructureError(
                "could not close the owned Windows Job Object "
                f"(error {self._ctypes.get_last_error()})"
            )
        self._handle = None


def _new_windows_kill_on_close_job() -> _WindowsKillOnCloseJob:
    return _WindowsKillOnCloseJob()


def _windows_job_enabled() -> bool:
    return os.name == "nt"


def _new_windows_sidecar_gate(workspace: Path) -> tuple[Path, str]:
    parent = Path(workspace).resolve().parent
    gate_dir = Path(
        tempfile.mkdtemp(prefix=".signalos-sidecar-job-", dir=str(parent))
    ).resolve()
    return gate_dir / "release", secrets.token_hex(32)


def _release_windows_sidecar_gate(gate: Path, token: str, process_id: int) -> None:
    pending = gate.with_name("release.pending")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(pending, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(f"{int(process_id)}:{token}".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        with contextlib.suppress(OSError):
            pending.unlink()
        raise
    os.replace(pending, gate)


def _remove_windows_sidecar_gate(gate: Path | None) -> None:
    if gate is None:
        return
    with contextlib.suppress(OSError):
        gate.unlink()
    with contextlib.suppress(OSError):
        gate.with_name("release.pending").unlink()
    with contextlib.suppress(OSError):
        gate.parent.rmdir()
    if gate.exists() or gate.parent.exists():
        raise InfrastructureError("Windows sidecar release gate could not be removed")


def _local_preflight() -> dict[str, Any]:
    if not SIDECAR.is_file():
        raise InfrastructureError(f"source sidecar is missing: {SIDECAR}")
    if not WINDOWS_JOB_BOOTSTRAP.is_file():
        raise InfrastructureError(
            f"Windows Job Object bootstrap is missing: {WINDOWS_JOB_BOOTSTRAP}"
        )
    return {
        "python": sys.version.split()[0],
        "sidecar": str(SIDECAR.relative_to(ROOT)).replace("\\", "/"),
        "windows_job_bootstrap": str(WINDOWS_JOB_BOOTSTRAP.relative_to(ROOT)).replace(
            "\\", "/"
        ),
        "generated_product_execution": "attested-offline-containers-only",
        "host_node_required": False,
        "host_browser_required": False,
    }


def _backend_preflight(
    scenario: dict[str, Any],
    *,
    init_timeout: float,
    dependency_timeout: float,
    funded_context: FundedRunContext,
) -> dict[str, Any]:
    """Exercise the real source-sidecar handshake and init path without an LLM.

    This disposable journey is deliberately stronger than an import or file
    existence check: it catches an NDJSON startup regression, a stale command
    catalog, and a broken profile-aware ``signal-init`` before provider credit
    is placed at risk.
    """

    with tempfile.TemporaryDirectory(prefix="signalos-matrix-preflight-") as temporary:
        base = Path(temporary)
        workspace = base / "workspace"
        workspace.mkdir()
        env: dict[str, str] = {}
        sidecar: SidecarClient | None = None
        try:
            env = funded_context.sidecar_environment(base / "runtime-home")
            sidecar = SidecarClient(
                workspace,
                env,
                funded_context.redaction_secrets(),
            )
            _events, terminal = sidecar.call("capabilities", [], timeout=60)
            capabilities = _require_ok("capabilities", terminal)
            required = {
                "gate0:approve",
                "agent:deliver",
                "agent:verdict",
                "agent:cancel",
                "agent:resume",
            }
            advertised = set(capabilities.get("commands") or [])
            if capabilities.get("protocol") != 1 or not required.issubset(advertised):
                raise InfrastructureError("source sidecar capability contract is incompatible")
            _events, terminal = sidecar.call(
                "signal-init",
                [
                    "--mode",
                    "keep",
                    "--name",
                    "Backend Matrix Preflight",
                    "--profile",
                    str(scenario["profile"]),
                ],
                timeout=init_timeout,
            )
            _require_ok("signal-init", terminal)
            marker = workspace / ".signalos" / "INIT_COMPLETE.json"
            if not marker.is_file():
                raise InfrastructureError(
                    "disposable signal-init returned success without INIT_COMPLETE.json"
                )
            _lazy_signalos_path()
            from signalos_lib.product.stacks import get_adapter

            scaffold = get_adapter(str(scenario["profile"])).scaffold(
                workspace,
                {"product_name": "Backend Matrix Preflight"},
            )
            if (
                scaffold.get("can_deliver_ui") is not True
                or scaffold.get("can_deliver_runnable") is not True
            ):
                raise InfrastructureError(
                    "funded scenario scaffold is not a runnable UI product"
                )
            materialized = funded_context.materialize_after_init(workspace)
            verified = funded_context.verify_materialized_after_init(workspace)
            offline_probe = funded_context.offline_probe(
                workspace,
                timeout=dependency_timeout,
            )
            return {
                "ready": True,
                "protocol": capabilities.get("protocol"),
                "version": capabilities.get("version"),
                "required_commands": sorted(required),
                "signal_init_profile": scenario["profile"],
                "init_complete": True,
                "scaffold": {
                    "created": sorted(str(path) for path in scaffold.get("created") or []),
                    "can_deliver_ui": scaffold.get("can_deliver_ui") is True,
                    "can_deliver_runnable": scaffold.get("can_deliver_runnable") is True,
                },
                "funded_dependencies": {
                    "materialized": materialized,
                    "verified": verified,
                    "offline_probe": offline_probe,
                },
            }
        finally:
            try:
                if sidecar is not None:
                    sidecar.close()
            finally:
                _clear_parent_environment(env)
                preflight_scan = _scan_exact_secret_values(
                    (base,),
                    funded_context.attestation_scan_needles(),
                )
                if not preflight_scan["ok"]:
                    raise InfrastructureError(
                        "keyless backend preflight retained dependency attestation secret data"
                    )


def _provider_preflight(
    client: OpenRouterClient,
    selected: Sequence[ModelSpec],
    *,
    required_remaining: float = 0.0,
    require_provider_limit: bool = False,
) -> dict[str, Any]:
    info = client.key_info()
    usage = client.usage()
    catalog = client.models()
    model_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    without_tools: list[str] = []
    without_context: list[str] = []
    for spec in selected:
        row = catalog.get(spec.model)
        if row is None:
            missing.append(spec.model)
            model_rows.append(
                {
                    "alias": spec.alias,
                    "cohort": spec.cohort,
                    "id": spec.model,
                    "available": False,
                }
            )
            continue
        supported = row.get("supported_parameters") or []
        has_tools = isinstance(supported, list) and (
            "tools" in supported or "tool_choice" in supported
        )
        if not has_tools:
            without_tools.append(spec.model)
        raw_context = row.get("context_length")
        context_length = (
            raw_context
            if isinstance(raw_context, int)
            and not isinstance(raw_context, bool)
            and 4_096 <= raw_context <= 10_000_000
            else None
        )
        if context_length is None:
            without_context.append(spec.model)
        model_rows.append(
            {
                "alias": spec.alias,
                "cohort": spec.cohort,
                "id": spec.model,
                "available": True,
                "tool_calling": has_tools,
                "canonical_id": str(row.get("id") or spec.model),
                "created": row.get("created"),
                "context_length": context_length,
                "supported_parameters": sorted(str(value) for value in supported),
                "architecture": row.get("architecture"),
                "pricing": row.get("pricing"),
                "top_provider": row.get("top_provider"),
            }
        )
    if missing:
        raise InfrastructureError("configured OpenRouter model(s) unavailable: " + ", ".join(missing))
    if without_tools:
        raise InfrastructureError(
            "configured model(s) do not advertise tool calling: " + ", ".join(without_tools)
        )
    if without_context:
        raise InfrastructureError(
            "configured model(s) have no trustworthy context length: "
            + ", ".join(without_context)
        )

    def numeric(name: str) -> float | None:
        value = info.get(name)
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    remaining = numeric("limit_remaining")
    limit = numeric("limit")
    provider_limit_configured = limit is not None and remaining is not None
    if require_provider_limit and not provider_limit_configured:
        raise InfrastructureError(
            "live matrix requires a provider-side spending limit on the selected key; "
            "use a dedicated capped benchmark key"
        )
    if required_remaining > 0 and remaining is not None and remaining < required_remaining:
        raise InfrastructureError(
            f"OpenRouter key remaining limit ${remaining:.4f} is below requested row cap "
            f"${required_remaining:.4f}"
        )
    return {
        "key_valid": True,
        "usage": usage,
        "limit": limit,
        "limit_remaining": remaining,
        "provider_limit_configured": provider_limit_configured,
        "safe_for_live": provider_limit_configured,
        "is_free_tier": bool(info.get("is_free_tier", False)),
        "models": model_rows,
    }


def _provider_stack_preflight(
    selected: Sequence[ModelSpec],
    model_rows: Sequence[dict[str, Any]],
    *,
    litellm_module: Any | None = None,
) -> dict[str, Any]:
    """Prove the installed adapter can construct every selected route offline."""

    if litellm_module is None:
        try:
            import litellm as litellm_module  # type: ignore[import-not-found]
        except Exception as exc:
            raise InfrastructureError(
                "LiteLLM provider stack is unavailable; install the locked sidecar dependencies"
            ) from exc
    _lazy_signalos_path()
    from signalos_lib.product.provider_adapter import (
        ProviderAdapter,
        normalize_provider_model,
    )

    by_id = {str(row.get("id")): row for row in model_rows}
    routes: list[dict[str, Any]] = []
    for spec in selected:
        row = by_id.get(spec.model)
        if row is None:
            raise InfrastructureError(
                f"provider metadata missing for selected model {spec.model}"
            )
        context_length = row.get("context_length")
        if not isinstance(context_length, int):
            raise InfrastructureError(
                f"provider context length missing for selected model {spec.model}"
            )
        routed_model = normalize_provider_model(
            spec.model, provider_name=spec.provider
        )
        expected_route = f"openrouter/{spec.model}"
        if routed_model != expected_route:
            raise InfrastructureError(
                f"LiteLLM route mismatch for {spec.model}: {routed_model!r}"
            )
        adapter = ProviderAdapter(
            model=spec.model,
            provider_name=spec.provider,
            litellm_module=litellm_module,
            context_length=context_length,
        )
        if adapter.routed_model != routed_model or adapter.context_length != context_length:
            raise InfrastructureError(
                f"provider adapter metadata mismatch for {spec.model}"
            )
        routes.append(
            {
                "alias": spec.alias,
                "model": spec.model,
                "routed_model": routed_model,
                "context_length": context_length,
                "tool_calling": bool(row.get("tool_calling")),
            }
        )
    version = str(getattr(litellm_module, "__version__", "") or "")
    if not version:
        with contextlib.suppress(importlib_metadata.PackageNotFoundError):
            version = importlib_metadata.version("litellm")
    version = version or "unknown"
    return {"ready": True, "litellm_version": version, "routes": routes}


class SidecarClient:
    """Long-lived NDJSON client with concurrent stdout/stderr draining."""

    def __init__(self, workspace: Path, env: dict[str, str], secrets: Iterable[str]) -> None:
        self.workspace = Path(workspace)
        self.secrets: tuple[str, ...] = ()
        self._windows_job: _WindowsKillOnCloseJob | None = None
        self._windows_gate: Path | None = None
        self._windows_job_required = _windows_job_enabled()
        self._windows_job_cleanup_verified = not self._windows_job_required
        try:
            self.secrets = tuple(secrets)
            self._stdout: queue.Queue[str | None] = queue.Queue()
            # Deep enough to retain a full Python crash traceback (or a burst of
            # provider/build warnings) if the sidecar dies during a heavy G4
            # fleet -- the tail is the only forensic record of a silent exit.
            self._stderr: deque[str] = deque(maxlen=2000)
            self._counter = 0
            creationflags = 0
            kwargs: dict[str, Any] = {}
            command = [sys.executable, "-u", str(SIDECAR)]
            if self._windows_job_required:
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                self._windows_job = _new_windows_kill_on_close_job()
                self._windows_gate, gate_token = _new_windows_sidecar_gate(self.workspace)
                command = [
                    sys.executable,
                    "-S",
                    "-B",
                    "-u",
                    str(WINDOWS_JOB_BOOTSTRAP),
                    "--gate",
                    str(self._windows_gate),
                    "--token",
                    gate_token,
                    "--sidecar",
                    str(SIDECAR),
                ]
            else:
                kwargs["start_new_session"] = True
            try:
                self.proc = subprocess.Popen(
                    command,
                    cwd=str(self.workspace),
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    close_fds=True,
                    creationflags=creationflags,
                    **kwargs,
                )
            finally:
                # Popen has copied the child environment by the time it
                # returns.  Destroy the parent-side credential/HMAC mapping on
                # both success and constructor failure.
                _clear_parent_environment(env)
            if self._windows_job is not None:
                if self._windows_gate is None:
                    raise InfrastructureError(
                        "Windows sidecar Job Object has no release gate"
                    )
                self._windows_job.assign(self.proc)
                _release_windows_sidecar_gate(
                    self._windows_gate, gate_token, self.proc.pid
                )
            threading.Thread(target=self._pump_stdout, daemon=True).start()
            threading.Thread(target=self._pump_stderr, daemon=True).start()
            ready = self._wait_for("init", timeout=90, guard=None)
            if not ready or not ready[1] or ready[1].get("ok") is not True:
                raise InfrastructureError("source sidecar did not produce a valid init handshake")
            data = ready[1].get("data") or {}
            if not isinstance(data, dict) or data.get("ready") is not True:
                raise InfrastructureError("source sidecar init handshake did not report ready=true")
        except BaseException as initialization_error:
            _clear_parent_environment(env)
            cleanup_failed = False
            if hasattr(self, "proc"):
                try:
                    self.terminate_tree()
                except Exception:
                    cleanup_failed = True
                with contextlib.suppress(Exception):
                    if self.proc.stdin:
                        self.proc.stdin.close()
            elif self._windows_job is not None:
                try:
                    self._windows_job.close()
                    self._windows_job = None
                    _remove_windows_sidecar_gate(self._windows_gate)
                    self._windows_gate = None
                    self._windows_job_cleanup_verified = True
                except Exception:
                    cleanup_failed = True
            self._clear_raw_buffers()
            self.secrets = ()
            if cleanup_failed:
                raise InfrastructureError(
                    "source sidecar initialization failed and process-tree cleanup "
                    "could not be verified"
                ) from initialization_error
            raise

    def _clear_raw_buffers(self) -> None:
        proc = getattr(self, "proc", None)
        if proc is not None:
            for name in ("stdout", "stderr"):
                stream = getattr(proc, name, None)
                with contextlib.suppress(Exception):
                    if stream is not None:
                        stream.close()
        stderr = getattr(self, "_stderr", None)
        if stderr is not None:
            stderr.clear()
        stdout = getattr(self, "_stdout", None)
        if stdout is not None:
            while True:
                try:
                    stdout.get_nowait()
                except queue.Empty:
                    break

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                self._stdout.put(line)
        finally:
            self._stdout.put(None)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip())

    def _next_id(self, command: str) -> str:
        self._counter += 1
        return f"matrix-{self._counter:03d}-{command.replace(':', '-')}"

    def _send(self, payload: dict[str, Any]) -> None:
        if self.proc.poll() is not None:
            raise InfrastructureError(f"sidecar exited unexpectedly with code {self.proc.returncode}")
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()

    def _wait_for(
        self,
        req_id: str,
        *,
        timeout: float,
        guard: Callable[[], Any] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        deadline = time.monotonic() + timeout
        events: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            if guard is not None:
                guard()
            try:
                line = self._stdout.get(timeout=min(1.0, max(0.05, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if line is None:
                return events, None
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InfrastructureError("sidecar corrupted the NDJSON protocol with non-JSON stdout") from exc
            if not isinstance(message, dict):
                raise InfrastructureError("sidecar emitted a non-object NDJSON message")
            if message.get("id") == req_id and "ok" in message and not message.get("kind"):
                return events, message
            if "ok" in message and not message.get("kind") and message.get("id") != req_id:
                raise InfrastructureError(
                    f"sidecar emitted an unmatched terminal response for {message.get('id')!r}"
                )
            events.append(message)
        return events, None

    def call(
        self,
        command: str,
        args: Any = None,
        *,
        timeout: float,
        project_id: str = "default",
        guard: Callable[[], Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        req_id = self._next_id(command)
        request: dict[str, Any] = {
            "id": req_id,
            "command": command,
            "cwd": str(self.workspace),
            "project_id": project_id,
        }
        if args is not None:
            request["args"] = args
        self._send(request)
        try:
            events, terminal = self._wait_for(req_id, timeout=timeout, guard=guard)
        except Exception:
            self.cancel_and_stop(args.get("run_id") if isinstance(args, dict) else None)
            raise
        if terminal is None:
            # Distinguish a genuine timeout (process still alive, hung) from a
            # silent process EXIT (stdout hit EOF because the sidecar died --
            # e.g. OOM-killed under a heavy build fleet, which leaves no Python
            # traceback). Capturing the exit code + stderr here is what makes
            # that failure diagnosable instead of an opaque "no response".
            exit_code = self.proc.poll()
            stderr_snapshot = self.stderr_tail()
            self.cancel_and_stop(args.get("run_id") if isinstance(args, dict) else None)
            tail = " | ".join(stderr_snapshot[-12:]) if stderr_snapshot else "<empty>"
            if exit_code is not None:
                raise InfrastructureError(
                    f"sidecar process EXITED (code {exit_code}) during {command} "
                    f"before returning a terminal response; stderr tail: {tail}"
                )
            raise InfrastructureError(
                f"sidecar command {command} timed out (process still alive) with no "
                f"terminal response; stderr tail: {tail}"
            )
        return events, terminal

    def cancel_and_stop(self, run_id: str | None) -> None:
        if run_id and self.proc.poll() is None:
            with contextlib.suppress(Exception):
                self._send(
                    {
                        "id": self._next_id("agent:cancel"),
                        "command": "agent:cancel",
                        "cwd": str(self.workspace),
                        "project_id": "default",
                        "args": {"run_id": run_id},
                    }
                )
            # Cancellation is cooperative.  Give the in-process interceptor a
            # short opportunity, then stop this sidecar tree only.
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=5)
        self.terminate_tree()

    def terminate_tree(self) -> None:
        job_error: Exception | None = None
        windows_job = getattr(self, "_windows_job", None)
        job_required = bool(getattr(self, "_windows_job_required", False))
        cleanup_verified = bool(
            getattr(self, "_windows_job_cleanup_verified", not job_required)
        )
        if job_required and windows_job is None and not cleanup_verified:
            job_error = InfrastructureError(
                "owned Windows Job Object handle is unexpectedly unavailable"
            )
        if windows_job is not None:
            job_closed = False
            try:
                windows_job.terminate()
            except Exception as exc:
                job_error = exc
            try:
                windows_job.close()
                job_closed = True
            except Exception as exc:
                job_error = job_error or exc
            if job_closed:
                self._windows_job = None
            try:
                _remove_windows_sidecar_gate(getattr(self, "_windows_gate", None))
                self._windows_gate = None
            except Exception as exc:
                job_error = job_error or exc
            if job_error is None:
                self._windows_job_cleanup_verified = True

        if self.proc.poll() is None and _windows_job_enabled():
            # This is only a constructor-failure fallback for a process that
            # could not be assigned. Verified rows terminate through the Job.
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                    shell=False,
                    env=_credential_free_host_env(),
                )
        elif self.proc.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=5)
            if self.proc.poll() is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        if self.proc.poll() is None:
            with contextlib.suppress(Exception):
                self.proc.kill()
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=5)
        if self.proc.poll() is None:
            raise InfrastructureError("source sidecar process tree did not terminate")
        if job_error is not None:
            raise InfrastructureError(
                "owned Windows Job Object cleanup could not be verified"
            ) from job_error

    def close(self) -> None:
        # Terminate the owned process group/Job before closing stdin. On
        # Windows the Job remains authoritative even if the root PID exited.
        try:
            self.terminate_tree()
        finally:
            with contextlib.suppress(Exception):
                if self.proc.stdin:
                    self.proc.stdin.close()
            self._clear_raw_buffers()
            self.secrets = ()

    def stderr_tail(self) -> list[str]:
        return [str(redact(line, self.secrets)) for line in self._stderr]


def _event_evidence(events: Sequence[dict[str, Any]], secrets: Iterable[str]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    key_events: list[dict[str, Any]] = []
    interesting = {
        "gate",
        "gate_signed",
        "gate_blocked",
        "delivery_complete",
        "release_verified",
        "error",
        "incident",
        "system",
    }
    for event in events:
        kind = str(event.get("type") or event.get("kind") or "unknown")
        counts[kind] += 1
        if kind in interesting and len(key_events) < 100:
            compact = {
                key: event.get(key)
                for key in ("kind", "type", "gate", "status", "ready", "error", "reason", "text")
                if event.get(key) is not None
            }
            key_events.append(redact(compact, secrets))
    return {"counts": dict(sorted(counts.items())), "key_events": key_events}


def _terminal_evidence(
    command: str,
    terminal: dict[str, Any],
    events: Sequence[dict[str, Any]],
    duration: float,
    secrets: Iterable[str],
) -> dict[str, Any]:
    compact = {
        "command": command,
        "duration_seconds": round(duration, 3),
        "ok": terminal.get("ok"),
        "data": terminal.get("data"),
        "error": terminal.get("error"),
        "events": _event_evidence(events, secrets),
    }
    return redact(compact, secrets)


def _is_execution_infrastructure_failure(value: object) -> bool:
    code = str(value or "").strip().lower()
    return code == "provider-init" or code.startswith(
        ("provider-", "sandbox-", "infrastructure-", "dependency-")
    )


def _require_ok(command: str, terminal: dict[str, Any]) -> dict[str, Any]:
    if terminal.get("ok") is not True:
        message = f"{command} failed: {terminal.get('error') or 'unknown sidecar error'}"
        error_code = str(terminal.get("error_code") or "")
        if _is_execution_infrastructure_failure(error_code):
            raise InfrastructureError(message)
        raise ProductFailure(message)
    data = terminal.get("data")
    return data if isinstance(data, dict) else {}


def _load_delivery(workspace: Path, run_id: str) -> dict[str, Any]:
    path = workspace / ".signalos" / "agent-runs" / run_id / "delivery.json"
    if not path.is_file():
        raise ProductFailure(f"delivery state was not persisted for run {run_id}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductFailure("persisted delivery state is unreadable") from exc
    if not isinstance(payload, dict):
        raise ProductFailure("persisted delivery state is not an object")
    return payload


def _lazy_signalos_path() -> None:
    source = str(ROOT / "python")
    if source not in sys.path:
        sys.path.insert(0, source)


def _strict_gate(workspace: Path, gate: str) -> dict[str, Any]:
    _lazy_signalos_path()
    from signalos_lib.sign import check_gate_signed_strict

    result = check_gate_signed_strict(workspace, gate, project_id="default")
    return {"gate": result.gate, "signed": result.signed, "reasons": list(result.reasons)}


def _gate_requirement_trace(
    workspace: Path,
    gate: str,
    requirement_ids: Sequence[str],
    *,
    scan_gates: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Requirement-ID traceability for *gate*.

    Traceability is **cumulative**: requirements registered in an earlier traced
    gate's artifacts persist on disk and stay traceable at every later gate, so
    the corpus spans *scan_gates* (every traced gate up to and including *gate*)
    rather than the current gate's artifacts alone. This matches how real
    requirement traceability works -- register once, carry forward -- instead of
    demanding every narrative artifact at every gate re-list every identifier
    (which no agent card directed, and which made the check pass or fail on
    sampling luck: the same model registered all ids one run and dropped two the
    next).
    """
    _lazy_signalos_path()
    from signalos_lib.artifacts import resolve_gate_artifacts

    gates = list(scan_gates) if scan_gates else [gate]
    combined: list[str] = []
    present: list[str] = []
    for scan_gate in gates:
        for artifact in resolve_gate_artifacts(workspace, scan_gate, project_id="default"):
            if not artifact.path.is_file():
                continue
            present.append(artifact.rel_path)
            with contextlib.suppress(OSError):
                combined.append(artifact.path.read_text(encoding="utf-8", errors="replace"))
    corpus = "\n".join(combined)
    missing = [req for req in requirement_ids if req not in corpus]
    return {"gate": gate, "artifacts": present, "missing_requirement_ids": missing, "ok": not missing}


def _validate_review_checkpoint(
    state: dict[str, Any],
    *,
    run_id: str,
    gate: str,
    signed_before: Sequence[str],
) -> None:
    if state.get("run_id") != run_id:
        raise ProductFailure(f"persisted run id changed at {gate}")
    if state.get("current_gate") != gate:
        raise ProductFailure(
            f"expected persisted current gate {gate}, got {state.get('current_gate')!r}"
        )
    if state.get("status") != "awaiting-verdict":
        last_outcome = state.get("last_outcome") or {}
        reason = last_outcome.get("reason")
        failure_type = str(last_outcome.get("failure_type") or "")
        if _is_execution_infrastructure_failure(failure_type):
            raise InfrastructureError(
                f"{gate} execution infrastructure failed "
                f"(type={failure_type!r}; reason={reason!r})"
            )
        raise ProductFailure(
            f"{gate} is not reviewable (status={state.get('status')!r}; reason={reason!r})"
        )
    if state.get("signed") != list(signed_before):
        raise ProductFailure(
            f"persisted signed-gate order drifted at {gate}: {state.get('signed')!r}"
        )
    outcome = state.get("last_outcome") or {}
    if outcome.get("gate") != gate or outcome.get("ok") is not True:
        raise ProductFailure(f"{gate} has no successful current-run agent outcome")
    if state.get("waived"):
        raise ProductFailure(f"waivers are not accepted by this benchmark: {state.get('waived')!r}")
    if state.get("conditions"):
        raise ProductFailure(
            f"unresolved approval conditions are not accepted: {state.get('conditions')!r}"
        )


def _snapshot_product_tree(workspace: Path) -> dict[str, str]:
    excluded_dirs = {".git", ".signalos", "core", "node_modules", "dist", ".vite"}
    result: dict[str, str] = {}
    for path in sorted(_owned_tree_files(workspace)):
        try:
            rel = path.relative_to(workspace)
        except ValueError:
            continue
        if any(part in excluded_dirs for part in rel.parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        with contextlib.suppress(OSError):
            result[rel.as_posix()] = _sha256_file(path)
    return result


def _entry_is_reparse(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    try:
        attributes = int(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0))
    except OSError:
        return False
    flag = int(getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & flag)


def _owned_tree_files(root: Path) -> Iterable[Path]:
    """Walk row-owned regular files without ever following a link/reparse point."""

    pending = [Path(root)]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            if _entry_is_reparse(entry):
                continue
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    yield path
            except OSError:
                continue


def _tree_reparse_points(root: Path) -> list[str]:
    """Find links/junctions safely, pruning them before traversal."""

    found: list[str] = []
    pending = [Path(root)]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            path = Path(entry.path)
            if _entry_is_reparse(entry):
                with contextlib.suppress(ValueError):
                    found.append(path.relative_to(root).as_posix())
                continue
            with contextlib.suppress(OSError):
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    return sorted(found)


def _changed_product_sources(before: dict[str, str], after: dict[str, str]) -> list[str]:
    prefixes = ("src/", "app/", "components/", "pages/", "lib/")
    suffixes = (".ts", ".tsx", ".js", ".jsx", ".vue", ".html", ".css", ".scss")
    return sorted(
        path
        for path, digest in after.items()
        if path.startswith(prefixes) and path.endswith(suffixes) and before.get(path) != digest
    )


def _attestation_needles(key: bytes | bytearray) -> tuple[tuple[str, bytes], ...]:
    raw = bytes(key)
    if not raw:
        return ()
    return (
        ("exact-dependency-attestation-key", raw),
        ("exact-dependency-attestation-key-hex", raw.hex().encode("ascii")),
    )


def _scan_exact_secret_values(
    roots: Iterable[Path],
    needles: Iterable[tuple[str, bytes]],
) -> dict[str, Any]:
    """Fail-closed scan of every owned path and regular-file byte."""

    exact = tuple((str(kind), bytes(value)) for kind, value in needles if value)
    hits: list[dict[str, str]] = []
    errors: list[str] = []
    scanned: set[Path] = set()

    def display(path: Path, root: Path) -> str:
        try:
            label = path.relative_to(root).as_posix()
        except ValueError:
            label = str(path)
        if not label or label == ".":
            label = path.name or str(path)
        for _kind, value in exact:
            with contextlib.suppress(UnicodeDecodeError):
                decoded = value.decode("utf-8")
                if decoded:
                    label = label.replace(decoded, REDACTED)
        return label

    def scan_path_bytes(path: Path, root: Path, payload: bytes) -> None:
        for kind, value in exact:
            if value in payload:
                hits.append({"path": display(path, root), "kind": kind})

    for raw_root in roots:
        root = Path(raw_root)
        try:
            root_info = root.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"retained root cannot be inspected: {type(exc).__name__}")
            continue
        scan_path_bytes(root, root, os.fsencode(str(root)))
        pending = [root] if stat_module.S_ISDIR(root_info.st_mode) else []
        if stat_module.S_ISREG(root_info.st_mode):
            pending_files = [root]
        elif pending:
            pending_files = []
        else:
            scan_path_bytes(root, root, os.fsencode(str(root)))
            continue
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                errors.append(
                    f"retained directory cannot be scanned: {type(exc).__name__}"
                )
                continue
            for entry in entries:
                path = Path(entry.path)
                scan_path_bytes(path, root, os.fsencode(str(path)))
                if _entry_is_reparse(entry):
                    try:
                        scan_path_bytes(
                            path,
                            root,
                            os.fsencode(os.readlink(path)),
                        )
                    except OSError as exc:
                        errors.append(
                            "retained reparse target cannot be inspected: "
                            + type(exc).__name__
                        )
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        pending_files.append(path)
                    else:
                        errors.append("retained tree contains an unsupported special path")
                except OSError as exc:
                    errors.append(
                        f"retained path cannot be inspected: {type(exc).__name__}"
                    )
        for path in pending_files:
            try:
                resolved = path.resolve(strict=True)
                if resolved in scanned:
                    continue
                scanned.add(resolved)
                for kind, value in exact:
                    if _file_contains_bytes(path, value):
                        hits.append({"path": display(path, root), "kind": kind})
            except OSError as exc:
                errors.append(f"retained file cannot be scanned: {type(exc).__name__}")
    return {
        "ok": not hits and not errors,
        "hits": hits,
        "errors": errors,
        "files_scanned": len(scanned),
    }


def _secret_scan(
    workspace: Path,
    exact_secret: str,
    *,
    exact_values: Iterable[tuple[str, bytes]] = (),
) -> dict[str, Any]:
    exact_needles = []
    if exact_secret:
        exact_needles.append(("exact-selected-key", exact_secret.encode("utf-8")))
    exact_needles.extend((str(kind), bytes(value)) for kind, value in exact_values if value)
    exact_result = _scan_exact_secret_values((workspace,), exact_needles)
    hits: list[dict[str, str]] = list(exact_result["hits"])
    errors: list[str] = list(exact_result["errors"])
    exact_hit_paths = {str(hit["path"]) for hit in hits}
    skipped = {".git", "node_modules"}
    for path in _owned_tree_files(workspace):
        try:
            rel = path.relative_to(workspace)
            if not path.is_file():
                continue
            if rel.as_posix() in exact_hit_paths:
                continue
            size = path.stat().st_size
            kind = ""
            # Generic shape matching is diagnostic and intentionally skips
            # dependency/cache content where package documentation often
            # contains fake key examples.  The fail-closed exact scan above
            # still scanned every byte of every owned regular file.
            generic_excluded = {"cache", ".cache", "npm", "pip"}
            if (
                any(part in skipped for part in rel.parts)
                or size > 20 * 1024 * 1024
                or any(part.lower() in generic_excluded for part in rel.parts)
            ):
                continue
            data = path.read_bytes()
            text = data.decode("utf-8", errors="ignore")
            if any(pattern.search(text) for pattern in SECRET_SHAPES):
                kind = "provider-key-shaped-text"
            if kind:
                hits.append({"path": rel.as_posix(), "kind": kind})
        except OSError as exc:
            errors.append(f"generic secret scan failed: {type(exc).__name__}")
    return {
        "ok": not hits and not errors,
        "hits": hits,
        "errors": errors,
        "files_scanned": exact_result["files_scanned"],
    }


def _file_contains_bytes(path: Path, needle: bytes) -> bool:
    """Stream-search an exact secret in a file of any size with chunk overlap."""

    if not needle:
        return False
    overlap = max(0, len(needle) - 1)
    tail = b""
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            combined = tail + chunk
            if needle in combined:
                return True
            tail = combined[-overlap:] if overlap else b""
    return False


def _copy_clean_room(workspace: Path, destination: Path) -> None:
    if destination.exists():
        resolved = destination.resolve()
        if destination.parent.resolve() not in resolved.parents:
            raise InfrastructureError("refusing to remove clean-room path outside its run directory")
        shutil.rmtree(destination)

    unsafe_links = _tree_reparse_points(workspace)
    if unsafe_links:
        raise ProductFailure(
            "generated workspace contains symlink/reparse-point paths and is not clean-room safe: "
            + ", ".join(unsafe_links[:10])
        )

    excluded = {".git", ".signalos", "core", "node_modules", "dist", ".vite"}
    destination.mkdir(parents=True)
    pending: list[tuple[Path, Path]] = [(workspace, destination)]
    while pending:
        source_dir, target_dir = pending.pop()
        try:
            entries = list(os.scandir(source_dir))
        except OSError as exc:
            raise ProductFailure(f"cannot read generated workspace for clean-room copy: {source_dir}") from exc
        for entry in entries:
            if entry.name in excluded:
                continue
            source = Path(entry.path)
            target = target_dir / entry.name
            if _entry_is_reparse(entry):
                rel = source.relative_to(workspace).as_posix()
                raise ProductFailure(
                    f"generated workspace changed to a symlink/reparse point during clean-room copy: {rel}"
                )
            try:
                if entry.is_dir(follow_symlinks=False):
                    target.mkdir()
                    pending.append((source, target))
                elif entry.is_file(follow_symlinks=False):
                    shutil.copy2(source, target, follow_symlinks=False)
            except OSError as exc:
                raise ProductFailure(
                    f"failed to copy generated file into clean room: {source.relative_to(workspace).as_posix()}"
                ) from exc
    copied_links = _tree_reparse_points(destination)
    if copied_links:
        raise ProductFailure(
            "clean-room copy contains unexpected symlink/reparse points: "
            + ", ".join(copied_links[:10])
        )


def _strict_artifact_snapshot(
    root: Path,
    *,
    label: str,
    error_type: type[HarnessError] = InfrastructureError,
) -> dict[str, Any]:
    """Hash a bounded regular-file tree without following links or special files."""

    tree_root = Path(root)
    try:
        root_info = tree_root.lstat()
        root_attributes = int(getattr(root_info, "st_file_attributes", 0) or 0)
        root_resolved = tree_root.resolve(strict=True)
    except OSError as exc:
        raise error_type(f"{label} is missing or unreadable") from exc
    if (
        not stat_module.S_ISDIR(root_info.st_mode)
        or stat_module.S_ISLNK(root_info.st_mode)
        or root_attributes & 0x0400
    ):
        raise error_type(f"{label} must be a real directory")

    files: dict[str, str] = {}
    total_bytes = 0
    pending: list[Path] = [root_resolved]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise error_type(f"{label} contains an unreadable directory") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                relative = path.relative_to(root_resolved).as_posix()
                # Use os.stat (NOT DirEntry.stat): on Windows a DirEntry from
                # os.scandir reports st_ino/st_dev == 0, while the `after`
                # re-stat below uses os.stat which populates them -- so the
                # stable-field comparison would falsely flag EVERY file as
                # "changed while it was being sealed". os.stat on both sides
                # keeps the seal cross-platform and byte-faithful.
                before = os.stat(entry.path, follow_symlinks=False)
                attributes = int(
                    getattr(before, "st_file_attributes", 0) or 0
                )
            except (OSError, ValueError) as exc:
                raise error_type(f"{label} contains an unreadable path") from exc
            if entry.is_symlink() or attributes & 0x0400:
                raise error_type(f"{label} contains a link/reparse point: {relative}")
            if stat_module.S_ISDIR(before.st_mode):
                pending.append(path)
                continue
            if not stat_module.S_ISREG(before.st_mode):
                raise error_type(f"{label} contains a special file: {relative}")
            if relative in files:
                raise error_type(f"{label} contains a duplicate path: {relative}")
            try:
                digest = _sha256_file(path)
                after = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise error_type(f"{label} contains an unreadable file: {relative}") from exc
            stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
            if any(
                getattr(before, name, None) != getattr(after, name, None)
                for name in stable_fields
            ):
                raise error_type(f"{label} changed while it was being sealed: {relative}")
            total_bytes += int(after.st_size)
            if len(files) + 1 > ARTIFACT_MAX_FILES or total_bytes > ARTIFACT_MAX_BYTES:
                raise error_type(f"{label} exceeds the reviewed artifact limits")
            files[relative] = digest

    files = dict(sorted(files.items()))
    return {
        "schema": "signalos.artifact-tree.v1",
        "tree_sha256": _canonical_json_sha256(files),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
    }


def _copy_strict_artifact_tree(
    source: Path,
    destination: Path,
    *,
    label: str,
) -> dict[str, Any]:
    """Copy a sealed artifact tree and prove the copy is byte-identical."""

    source_path = Path(source)
    destination_root = Path(destination)
    if destination_root.exists() or destination_root.is_symlink():
        raise InfrastructureError(f"{label} destination already exists")
    before = _strict_artifact_snapshot(
        source_path,
        label=label,
        error_type=ProductFailure,
    )
    source_root = source_path.resolve(strict=True)
    destination_root.mkdir(parents=True, exist_ok=False)
    pending: list[tuple[Path, Path]] = [(source_root, destination_root)]
    while pending:
        source_dir, target_dir = pending.pop()
        try:
            entries = sorted(os.scandir(source_dir), key=lambda item: item.name)
        except OSError as exc:
            raise ProductFailure(f"{label} changed during the isolated copy") from exc
        for entry in entries:
            source_path = Path(entry.path)
            target_path = target_dir / entry.name
            if _entry_is_reparse(entry):
                raise ProductFailure(f"{label} gained a link during the isolated copy")
            try:
                if entry.is_dir(follow_symlinks=False):
                    target_path.mkdir()
                    pending.append((source_path, target_path))
                elif entry.is_file(follow_symlinks=False):
                    shutil.copyfile(source_path, target_path, follow_symlinks=False)
                else:
                    raise ProductFailure(f"{label} contains a special file")
            except OSError as exc:
                raise ProductFailure(f"{label} could not be copied safely") from exc
    after = _strict_artifact_snapshot(
        destination_root,
        label=f"isolated {label}",
        error_type=InfrastructureError,
    )
    current = _strict_artifact_snapshot(
        source_root,
        label=label,
        error_type=ProductFailure,
    )
    if before != current or before != after:
        raise ProductFailure(f"{label} changed or did not copy byte-for-byte")
    return after


def _validate_oracle_evidence(
    payload: Any,
    *,
    scenario_id: str,
    dist_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Validate the complete trusted-oracle success contract."""

    contract = ORACLE_CONTRACTS.get(str(scenario_id))
    if contract is None:
        raise InfrastructureError("no reviewed oracle contract exists for this scenario")
    if not isinstance(payload, dict):
        raise InfrastructureError("browser oracle evidence is not a JSON object")
    expected_isolation = {
        "sourceInspected": False,
        "storageInspected": False,
        "network": "loopback-origin-only",
        "webSockets": "blocked",
        "server": "oracle-owned-ephemeral-loopback",
        "browserContext": "fresh-per-check",
    }
    inputs = payload.get("input")
    runtime = payload.get("runtime")
    checks = payload.get("checks")
    index_sha256 = (dist_snapshot.get("files") or {}).get("index.html")
    if (
        payload.get("schemaVersion") != 1
        or payload.get("oracle") != contract["oracle"]
        or payload.get("oracleVersion") != contract["version"]
        or payload.get("status") != "pass"
        or payload.get("exitCode") != 0
        or payload.get("isolation") != expected_isolation
        or payload.get("infrastructureErrors") != []
        or not isinstance(inputs, dict)
        or inputs.get("dist") != "/workspace/product"
        or inputs.get("indexSha256") != index_sha256
        or inputs.get("timeoutMs") != ORACLE_CHECK_TIMEOUT_MS
        or not isinstance(runtime, dict)
        or runtime.get("platform") != "linux-x64"
        or not isinstance(runtime.get("node"), str)
        or not runtime.get("node")
        or not isinstance(runtime.get("browser"), str)
        or not runtime.get("browser")
        or not isinstance(checks, list)
    ):
        raise InfrastructureError("browser oracle evidence failed its sealed contract")
    expected_checks = list(contract["checks"])
    actual_checks = [
        check.get("name") if isinstance(check, dict) else None for check in checks
    ]
    if actual_checks != expected_checks or any(
        not isinstance(check, dict) or check.get("status") != "pass"
        for check in checks
    ):
        raise InfrastructureError("browser oracle evidence has incomplete passing checks")
    return payload


def _clean_room_acceptance(
    workspace: Path,
    clean_room: Path,
    oracle_asset: dict[str, Any],
    *,
    scenario_id: str,
    timeout: float,
    secrets: Iterable[str],
    funded_context: FundedRunContext,
    oracle_context: FundedRunContext,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = evidence if evidence is not None else {}
    commands = evidence.setdefault("commands", {})
    _copy_clean_room(workspace, clean_room)
    package_path = clean_room / "package.json"
    lock_path = clean_room / "package-lock.json"
    if not package_path.is_file() or not lock_path.is_file():
        raise ProductFailure("clean-room npm verification requires package.json and package-lock.json")
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductFailure("generated package.json is unreadable") from exc
    scripts = package.get("scripts") if isinstance(package, dict) else None
    if not isinstance(scripts, dict) or not scripts.get("build") or not scripts.get("test"):
        raise ProductFailure("generated package.json must define both build and test scripts")

    product_inputs = funded_context.public_evidence().get("inputs")
    if not isinstance(product_inputs, dict) or (
        _sha256_file(package_path) != product_inputs.get("package_json_sha256")
        or _sha256_file(lock_path) != product_inputs.get("package_lock_sha256")
    ):
        raise ProductFailure(
            "generated package/lock bytes drifted from the reviewed dependency profile"
        )
    clean_env = {"CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"}
    evidence["dependency_mode"] = "reviewed-attested-bundle; no install during acceptance"
    commands["npm_test"] = funded_context.run_offline_command(
        clean_room,
        "npm test",
        timeout=timeout,
        env=clean_env,
        secrets_to_redact=secrets,
    )
    if not commands["npm_test"]["ok"]:
        raise ProductFailure("clean-room generated-product tests failed")

    dist = clean_room / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    commands["npm_build"] = funded_context.run_offline_command(
        clean_room,
        "npm run build",
        timeout=timeout,
        writable_paths=("dist",),
        env=clean_env,
        secrets_to_redact=secrets,
    )
    index = dist / "index.html"
    if not commands["npm_build"]["ok"] or not index.is_file():
        raise ProductFailure("clean-room production build failed or produced no dist/index.html")
    dist_snapshot = _strict_artifact_snapshot(
        dist,
        label="clean-room production artifact",
        error_type=ProductFailure,
    )
    if "index.html" not in dist_snapshot["files"]:
        raise ProductFailure("clean-room production artifact has no regular dist/index.html")
    evidence["dist_tree"] = dist_snapshot

    oracle_source = oracle_asset.get("source")
    oracle_name = str(oracle_asset.get("name") or "")
    oracle_sha256 = str(oracle_asset.get("sha256") or "")
    if (
        not isinstance(oracle_source, bytes)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:js|mjs|cjs)", oracle_name)
        or hashlib.sha256(oracle_source).hexdigest() != oracle_sha256
    ):
        raise InfrastructureError("trusted browser oracle asset is invalid")
    if oracle_context.policy.profile != ORACLE_RUNTIME_PROFILE:
        raise InfrastructureError("browser oracle context has the wrong dependency profile")
    oracle_root = clean_room.parent / "trusted-oracle-runtime"
    oracle_root.mkdir(exist_ok=False)
    shutil.copy2(oracle_context.policy.package_json, oracle_root / "package.json")
    shutil.copy2(oracle_context.policy.package_lock, oracle_root / "package-lock.json")
    oracle = oracle_root / oracle_name
    oracle.write_bytes(oracle_source)
    with contextlib.suppress(OSError):
        oracle.chmod(0o400)
    oracle_product = oracle_root / "product"
    oracle_input = _copy_strict_artifact_tree(
        dist,
        oracle_product,
        label="clean-room production artifact",
    )
    evidence["oracle_input_tree"] = oracle_input
    command = " ".join(
        shlex.quote(value)
        for value in (
            "node",
            oracle_name,
            "--dist",
            "product",
            "--evidence",
            "dist/oracle-evidence.json",
            "--artifacts",
            "dist/oracle-artifacts",
            "--timeout-ms",
            str(ORACLE_CHECK_TIMEOUT_MS),
        )
    )
    oracle_result = oracle_context.run_offline_command(
        oracle_root,
        command,
        timeout=timeout,
        writable_paths=("dist",),
        env=clean_env,
        secrets_to_redact=secrets,
    )
    commands["browser_oracle"] = oracle_result
    oracle_output = oracle_root / "dist"
    output_snapshot = _strict_artifact_snapshot(
        oracle_output,
        label="browser oracle output",
        error_type=InfrastructureError,
    )
    evidence["oracle_output_tree"] = output_snapshot
    evidence_path = oracle_output / "oracle-evidence.json"
    oracle_evidence: dict[str, Any] = {}
    if "oracle-evidence.json" in output_snapshot["files"]:
        try:
            evidence_bytes = evidence_path.read_bytes()
            if len(evidence_bytes) > 4 * 1024 * 1024:
                raise InfrastructureError("browser oracle evidence is unexpectedly large")
            if hashlib.sha256(evidence_bytes).hexdigest() != output_snapshot["files"][
                "oracle-evidence.json"
            ]:
                raise InfrastructureError("browser oracle evidence changed after sealing")
            loaded = _read_json_bytes(evidence_bytes, label="browser oracle evidence")
            if isinstance(loaded, dict):
                oracle_evidence = loaded
        except ValueError as exc:
            raise InfrastructureError(str(exc)) from exc
    evidence["oracle_evidence"] = oracle_evidence
    if oracle_result.get("timed_out") or oracle_result.get("returncode") == 2:
        raise InfrastructureError("browser oracle infrastructure failed")
    if not oracle_result["ok"]:
        raise ProductFailure("browser oracle rejected the generated product")
    _validate_oracle_evidence(
        oracle_evidence,
        scenario_id=scenario_id,
        dist_snapshot=dist_snapshot,
    )
    oracle_product_after = _strict_artifact_snapshot(
        oracle_product,
        label="source-blind oracle product input",
        error_type=InfrastructureError,
    )
    if oracle_product_after != oracle_input:
        raise InfrastructureError("browser oracle mutated its sealed production input")
    evidence["clean_tree"] = _snapshot_product_tree(clean_room)
    return evidence


def _load_ci_policy(path: Path = DEFAULT_CI_POLICY) -> dict[str, Any]:
    raw = _read_json(path)
    if not isinstance(raw, dict) or raw.get("schema") != "signalos.backend-matrix.ci-policy.v1":
        raise InfrastructureError("backend-matrix CI policy has an invalid schema")
    repository = raw.get("repository")
    if not isinstance(repository, dict) or repository != {
        "node_id": CI_REPOSITORY_NODE_ID,
        "full_name": CI_REPOSITORY_FULL_NAME,
        "branch": CI_REQUIRED_BRANCH,
    }:
        raise InfrastructureError("backend-matrix CI policy names the wrong repository or branch")
    configured = raw.get("workflows")
    if not isinstance(configured, list) or not configured:
        raise InfrastructureError("backend-matrix CI policy has no required workflows")

    workflows: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for index, item in enumerate(configured):
        if not isinstance(item, dict):
            raise InfrastructureError(f"backend-matrix CI policy workflow {index} is invalid")
        workflow_id = item.get("id")
        if isinstance(workflow_id, bool) or not isinstance(workflow_id, int):
            raise InfrastructureError(f"backend-matrix CI policy workflow {index} has no numeric id")
        expected = CI_REQUIRED_WORKFLOWS.get(workflow_id)
        if expected is None or workflow_id in seen_ids:
            raise InfrastructureError("backend-matrix CI policy has an unknown or duplicate workflow")
        name = str(item.get("name") or "")
        workflow_path = str(item.get("path") or "")
        if (name, workflow_path) != expected:
            raise InfrastructureError(
                f"backend-matrix CI policy metadata drifted for workflow {workflow_id}"
            )
        jobs = item.get("required_jobs")
        if (
            not isinstance(jobs, list)
            or not jobs
            or any(not isinstance(job, str) or not job.strip() for job in jobs)
            or len(set(jobs)) != len(jobs)
        ):
            raise InfrastructureError(
                f"backend-matrix CI policy jobs are invalid for workflow {workflow_id}"
            )
        seen_ids.add(workflow_id)
        workflows.append(
            {
                "id": workflow_id,
                "name": name,
                "path": workflow_path,
                "required_jobs": list(jobs),
            }
        )
    if seen_ids != set(CI_REQUIRED_WORKFLOWS):
        raise InfrastructureError("backend-matrix CI policy omits a required workflow")
    return {
        "schema": raw["schema"],
        "repository": dict(repository),
        "workflows": workflows,
    }


class _RejectGitHubRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _github_rest_json(
    endpoint: str,
    query: dict[str, str] | None = None,
    *,
    token: str | None = None,
    timeout: float = 30.0,
    urlopen: Callable[..., Any] | None = None,
) -> Any:
    """Read one GitHub REST document without ever surfacing response bodies.

    GitHub error bodies can contain organization/account context, and the
    authorization value must never enter an exception, result bundle, or child
    process.  Only fixed, repository-scoped endpoints are accepted here.
    """

    if (
        not isinstance(endpoint, str)
        or not endpoint.startswith("/")
        or "://" in endpoint
        or "\\" in endpoint
    ):
        token = None
        raise InfrastructureError(
            "refusing an invalid GitHub REST endpoint"
        ) from None
    request_token = token
    if request_token is None:
        request_token = (
            os.environ.get("SIGNALOS_GITHUB_TOKEN", "")
            or os.environ.get("GH_TOKEN", "")
            or os.environ.get("GITHUB_TOKEN", "")
        )
    if not isinstance(request_token, str) or any(
        character.isspace()
        or ord(character) < 32
        or 127 <= ord(character) <= 159
        for character in request_token
    ):
        request_token = None
        token = None
        raise InfrastructureError(
            "GitHub CI verification rejected an invalid bearer token"
        ) from None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "SignalOS-backend-matrix-ci-verifier/1",
    }
    request: urllib.request.Request | None = None
    request_url = ""
    request_error = False
    try:
        suffix = ""
        if query:
            suffix = "?" + urllib.parse.urlencode(query)
        request_url = f"{GITHUB_API_BASE}{endpoint}{suffix}"
        request = urllib.request.Request(request_url, headers=headers, method="GET")
        if request_token:
            # urllib copies ordinary headers to redirected requests.  Keeping
            # credentials unredirected is a second barrier behind the handler
            # that rejects every redirect.
            request.add_unredirected_header(
                "Authorization", f"Bearer {request_token}"
            )
    except Exception:
        request_error = True
    if request_error or request is None:
        if request is not None:
            request.remove_header("Authorization")
        request = None
        request_token = None
        token = None
        urlopen = None
        raise InfrastructureError(
            "GitHub CI verification could not construct its fixed request"
        ) from None

    failure: str | None = None
    body: bytes | bytearray | None = None
    opener: Callable[..., Any] | None = urlopen
    response: Any = None
    try:
        if opener is None:
            opener = urllib.request.build_opener(_RejectGitHubRedirects()).open
        with opener(request, timeout=timeout) as response:
            final_url = response.geturl()
            final_origin = urllib.parse.urlsplit(final_url)
            if (
                final_url != request_url
                or final_origin.scheme != "https"
                or final_origin.netloc != "api.github.com"
            ):
                failure = (
                    "GitHub CI verification rejected a redirect or non-canonical origin"
                )
            else:
                candidate_body = response.read(16 * 1024 * 1024 + 1)
                if not isinstance(candidate_body, (bytes, bytearray)):
                    failure = "GitHub CI verification transport or protocol failure"
                else:
                    body = candidate_body
    except Exception:
        failure = "GitHub CI verification transport or protocol failure"

    # Do not retain a bearer-bearing request or token in the exception frame.
    request.remove_header("Authorization")
    request_token = None
    token = None
    urlopen = None
    request = None
    response = None
    opener = None
    if failure is not None or body is None:
        raise InfrastructureError(
            failure or "GitHub CI verification transport or protocol failure"
        ) from None
    if len(body) > 16 * 1024 * 1024:
        body = None
        raise InfrastructureError(
            "GitHub CI verification response exceeded its size limit"
        ) from None
    invalid_json = False
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        invalid_json = True
        parsed = None
    body = None
    if invalid_json:
        raise InfrastructureError(
            "GitHub CI verification returned invalid JSON"
        ) from None
    return parsed


def _github_collection(
    fetch_json: Callable[[str, dict[str, str] | None], Any],
    endpoint: str,
    key: str,
    query: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    expected_total: int | None = None
    base_query = dict(query or {})
    max_pages = (GITHUB_COLLECTION_MAX_TOTAL + 99) // 100
    for page in range(1, max_pages + 1):
        page_query = {**base_query, "per_page": "100", "page": str(page)}
        payload = fetch_json(endpoint, page_query)
        if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
            raise InfrastructureError(f"GitHub CI verification returned no {key} collection")
        page_rows = payload[key]
        total = payload.get("total_count")
        if (
            isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or total > GITHUB_COLLECTION_MAX_TOTAL
        ):
            raise InfrastructureError(
                f"GitHub CI verification returned an invalid {key} total"
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise InfrastructureError(
                f"GitHub CI verification {key} total changed during pagination"
            )
        if len(page_rows) > 100 or any(not isinstance(item, dict) for item in page_rows):
            raise InfrastructureError(f"GitHub CI verification returned invalid {key} rows")
        for item in page_rows:
            item_id = item.get("id")
            if (
                isinstance(item_id, bool)
                or not isinstance(item_id, int)
                or item_id <= 0
                or item_id in seen_ids
            ):
                raise InfrastructureError(
                    f"GitHub CI verification returned duplicate/invalid {key} ids"
                )
            seen_ids.add(item_id)
            rows.append(item)
        if len(rows) > expected_total:
            raise InfrastructureError(
                f"GitHub CI verification returned more {key} rows than declared"
            )
        if len(rows) == expected_total:
            return rows
        if len(page_rows) < 100:
            raise InfrastructureError(
                f"GitHub CI verification returned a truncated {key} collection"
            )
    raise InfrastructureError(
        f"GitHub CI verification {key} collection did not reach its declared total"
    )


def _verify_ci_attestation(
    engine: dict[str, Any],
    *,
    policy_path: Path = DEFAULT_CI_POLICY,
    fetch_json: Callable[[str, dict[str, str] | None], Any] | None = None,
) -> dict[str, Any]:
    """Verify authoritative GitHub evidence for the exact local main HEAD."""

    policy_path = Path(policy_path).resolve()
    policy = _load_ci_policy(policy_path)
    commit = str(engine.get("commit") or "").strip().lower()
    tree = str(engine.get("tree") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(
        r"[0-9a-f]{40}", tree
    ):
        raise InfrastructureError("CI verification requires exact Git commit and tree identities")
    if engine.get("branch") != CI_REQUIRED_BRANCH:
        raise InfrastructureError("CI verification is restricted to the local main branch")

    if fetch_json is None:
        fetch_json = lambda endpoint, query=None: _github_rest_json(endpoint, query)
    repository_endpoint = f"/repos/{CI_REPOSITORY_FULL_NAME}"
    repository = fetch_json(repository_endpoint, None)
    if (
        not isinstance(repository, dict)
        or repository.get("node_id") != CI_REPOSITORY_NODE_ID
        or repository.get("full_name") != CI_REPOSITORY_FULL_NAME
        or repository.get("default_branch") != CI_REQUIRED_BRANCH
    ):
        raise InfrastructureError("GitHub repository identity does not match the funded CI policy")

    ref_endpoint = (
        f"/repos/{CI_REPOSITORY_FULL_NAME}/git/ref/heads/{CI_REQUIRED_BRANCH}"
    )
    remote_ref = fetch_json(ref_endpoint, None)
    ref_object = remote_ref.get("object") if isinstance(remote_ref, dict) else None
    if (
        not isinstance(remote_ref, dict)
        or remote_ref.get("ref") != f"refs/heads/{CI_REQUIRED_BRANCH}"
        or not isinstance(ref_object, dict)
        or ref_object.get("type") != "commit"
        or str(ref_object.get("sha") or "").lower() != commit
    ):
        raise InfrastructureError("GitHub refs/heads/main does not equal the local engine HEAD")

    workflow_evidence: list[dict[str, Any]] = []
    for required in policy["workflows"]:
        workflow_id = int(required["id"])
        workflow_endpoint = (
            f"/repos/{CI_REPOSITORY_FULL_NAME}/actions/workflows/{workflow_id}"
        )
        workflow = fetch_json(workflow_endpoint, None)
        if (
            not isinstance(workflow, dict)
            or workflow.get("id") != workflow_id
            or workflow.get("name") != required["name"]
            or workflow.get("path") != required["path"]
            or workflow.get("state") != "active"
        ):
            raise InfrastructureError(
                f"GitHub workflow identity drifted for {required['name']}"
            )

        runs_endpoint = workflow_endpoint + "/runs"
        runs = _github_collection(
            fetch_json,
            runs_endpoint,
            "workflow_runs",
            {
                "branch": CI_REQUIRED_BRANCH,
                "event": "push",
                "head_sha": commit,
                "exclude_pull_requests": "true",
            },
        )
        candidates = [
            run
            for run in runs
            if run.get("workflow_id") == workflow_id
            and run.get("event") == "push"
            and run.get("head_branch") == CI_REQUIRED_BRANCH
            and str(run.get("head_sha") or "").lower() == commit
        ]
        if not candidates:
            raise InfrastructureError(
                f"GitHub has no exact main/push run for required workflow {required['name']}"
            )

        def run_order(run: dict[str, Any]) -> tuple[str, int, int]:
            run_id = run.get("id")
            attempt = run.get("run_attempt")
            return (
                str(run.get("created_at") or ""),
                run_id if isinstance(run_id, int) and not isinstance(run_id, bool) else -1,
                attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else -1,
            )

        run = max(candidates, key=run_order)
        run_id = run.get("id")
        run_attempt = run.get("run_attempt")
        if (
            isinstance(run_id, bool)
            or not isinstance(run_id, int)
            or run_id <= 0
            or isinstance(run_attempt, bool)
            or not isinstance(run_attempt, int)
            or run_attempt <= 0
            or run.get("name") != required["name"]
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
        ):
            raise InfrastructureError(
                f"required GitHub workflow {required['name']} is not completed and successful"
            )

        jobs = _github_collection(
            fetch_json,
            (
                f"/repos/{CI_REPOSITORY_FULL_NAME}/actions/runs/{run_id}"
                f"/attempts/{run_attempt}/jobs"
            ),
            "jobs",
        )
        jobs_by_name: dict[str, dict[str, Any]] = {}
        for job in jobs:
            name = job.get("name")
            if (
                job.get("run_id") != run_id
                or str(job.get("head_sha") or "").lower() != commit
                or job.get("workflow_name") != required["name"]
                or job.get("head_branch") != CI_REQUIRED_BRANCH
            ):
                raise InfrastructureError(
                    f"required GitHub workflow {required['name']} returned an unbound job"
                )
            if not isinstance(name, str) or not name or name in jobs_by_name:
                raise InfrastructureError(
                    f"required GitHub workflow {required['name']} returned duplicate/invalid jobs"
                )
            jobs_by_name[name] = job
        expected_jobs = list(required["required_jobs"])
        if set(jobs_by_name) != set(expected_jobs):
            missing = sorted(set(expected_jobs) - set(jobs_by_name))
            extra = sorted(set(jobs_by_name) - set(expected_jobs))
            detail = []
            if missing:
                detail.append("missing=" + ", ".join(missing))
            if extra:
                detail.append("unexpected=" + ", ".join(extra))
            raise InfrastructureError(
                f"required GitHub workflow {required['name']} job set drifted: "
                + "; ".join(detail)
            )
        job_evidence: list[dict[str, Any]] = []
        for name in expected_jobs:
            job = jobs_by_name[name]
            job_id = job.get("id")
            if (
                isinstance(job_id, bool)
                or not isinstance(job_id, int)
                or job_id <= 0
                or job.get("status") != "completed"
                or job.get("conclusion") != "success"
            ):
                raise InfrastructureError(
                    f"required GitHub job did not pass: {required['name']} / {name}"
                )
            job_evidence.append(
                {
                    "id": job_id,
                    "name": name,
                    "run_id": run_id,
                    "run_attempt": run_attempt,
                    "head_sha": commit,
                    "workflow_name": required["name"],
                    "head_branch": CI_REQUIRED_BRANCH,
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": job.get("started_at"),
                    "completed_at": job.get("completed_at"),
                    "html_url": job.get("html_url"),
                }
            )
        confirmed_run = fetch_json(
            f"/repos/{CI_REPOSITORY_FULL_NAME}/actions/runs/{run_id}", None
        )
        if (
            not isinstance(confirmed_run, dict)
            or isinstance(confirmed_run.get("id"), bool)
            or confirmed_run.get("id") != run_id
            or isinstance(confirmed_run.get("run_attempt"), bool)
            or confirmed_run.get("run_attempt") != run_attempt
            or isinstance(confirmed_run.get("workflow_id"), bool)
            or confirmed_run.get("workflow_id") != workflow_id
            or confirmed_run.get("name") != required["name"]
            or confirmed_run.get("event") != "push"
            or confirmed_run.get("head_branch") != CI_REQUIRED_BRANCH
            or str(confirmed_run.get("head_sha") or "").lower() != commit
            or confirmed_run.get("status") != "completed"
            or confirmed_run.get("conclusion") != "success"
        ):
            raise InfrastructureError(
                f"required GitHub workflow {required['name']} changed during attestation"
            )
        workflow_evidence.append(
            {
                "id": workflow_id,
                "name": required["name"],
                "path": required["path"],
                "state": "active",
                "run": {
                    "id": run_id,
                    "attempt": run_attempt,
                    "event": "push",
                    "head_branch": CI_REQUIRED_BRANCH,
                    "head_sha": commit,
                    "status": "completed",
                    "conclusion": "success",
                    "created_at": confirmed_run.get("created_at"),
                    "updated_at": confirmed_run.get("updated_at"),
                    "html_url": confirmed_run.get("html_url"),
                },
                "jobs": job_evidence,
            }
        )

    evidence = {
        "schema": "signalos.backend-matrix.ci-evidence.v1",
        "verified_at": _utc_now(),
        "subject": {
            "commit": commit,
            "tree": tree,
            "branch": CI_REQUIRED_BRANCH,
            "upstream": engine.get("upstream"),
            "upstream_commit": engine.get("upstream_commit"),
        },
        "repository": {
            "node_id": CI_REPOSITORY_NODE_ID,
            "full_name": CI_REPOSITORY_FULL_NAME,
            "default_branch": CI_REQUIRED_BRANCH,
            "remote_ref": f"refs/heads/{CI_REQUIRED_BRANCH}",
            "remote_sha": commit,
        },
        "policy": {
            "path": str(policy_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": _sha256_file(policy_path),
        },
        "github_api_version": GITHUB_API_VERSION,
        "workflows": workflow_evidence,
    }
    return {
        "schema": "signalos.backend-matrix.ci-attestation.v1",
        "canonicalization": "utf8-json-sort-keys-compact-v1",
        "evidence": evidence,
        "evidence_sha256": _canonical_json_sha256(evidence),
    }


def _engine_metadata(
    *, git_reader: Callable[[tuple[str, ...]], str] | None = None
) -> dict[str, Any]:
    runtime_home: tempfile.TemporaryDirectory[str] | None = None
    git_env: dict[str, str] | None = None
    if git_reader is None:
        runtime_home = tempfile.TemporaryDirectory(
            prefix="signalos-engine-git-"
        )
        git_env = _tool_subprocess_env(Path(runtime_home.name))

    def git(*args: str) -> str:
        if git_reader is not None:
            value = git_reader(tuple(args))
            return str(value).strip() or "unknown"
        completed = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20, check=False,
            env=git_env,
        )
        return completed.stdout.strip() if completed.returncode == 0 else "unknown"

    try:
        status = git("status", "--porcelain")
        commit = git("rev-parse", "HEAD")
        upstream = git(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
        )
        upstream_commit = git("rev-parse", "@{upstream}")
        pushed = (
            commit != "unknown"
            and upstream != "unknown"
            and upstream_commit != "unknown"
            and commit == upstream_commit
        )
        return {
            "commit": commit,
            "tree": git("rev-parse", "HEAD^{tree}"),
            "branch": git("branch", "--show-current"),
            "upstream": upstream,
            "upstream_commit": upstream_commit,
            "pushed": pushed,
            "dirty": bool(status),
            "dirty_paths": [
                line[3:] for line in status.splitlines() if len(line) > 3
            ],
            "python": sys.version.split()[0],
            "platform": sys.platform,
        }
    finally:
        if git_env is not None:
            _clear_parent_environment(git_env)
        if runtime_home is not None:
            runtime_home.cleanup()


def _require_reproducible_engine(engine: dict[str, Any], *, live: bool) -> None:
    """Paid evidence must map to one reconstructable committed code tree."""
    if not live:
        return
    commit = str(engine.get("commit") or "").strip().lower()
    tree = str(engine.get("tree") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(
        r"[0-9a-f]{40}", tree
    ):
        raise InfrastructureError(
            "live matrix requires a Git commit and tree identity for the engine"
        )
    if engine.get("dirty"):
        paths = ", ".join(str(path) for path in engine.get("dirty_paths", [])[:8])
        suffix = f": {paths}" if paths else ""
        raise InfrastructureError(
            "live matrix refuses an uncommitted engine; commit or stash every "
            f"backend/harness change before spending provider credit{suffix}"
        )
    if engine.get("branch") != CI_REQUIRED_BRANCH:
        raise InfrastructureError("live matrix is restricted to the main branch")
    upstream = str(engine.get("upstream") or "")
    upstream_commit = str(engine.get("upstream_commit") or "").strip().lower()
    if (
        not engine.get("pushed")
        or upstream != "origin/main"
        or upstream_commit != commit
    ):
        raise InfrastructureError(
            "live matrix requires main HEAD to equal its configured main upstream"
        )


def _require_external_output_root(path: Path) -> Path:
    """Keep generated products and result evidence outside the engine tree."""

    output = Path(path).expanduser().resolve()
    repo = ROOT.resolve()
    if output == repo or repo in output.parents:
        raise InfrastructureError(
            "live matrix output root must be outside the SignalOS Git worktree"
        )
    return output


def _require_engine_unchanged(expected: dict[str, Any]) -> dict[str, Any]:
    """Re-check the paid engine boundary before and after every model row."""

    current = _engine_metadata()
    _require_reproducible_engine(current, live=True)
    if current.get("commit") != expected.get("commit") or current.get("tree") != expected.get("tree"):
        raise InfrastructureError(
            "engine commit/tree changed during the funded matrix; aborting"
        )
    return current


def _regular_owned_file(path: Path, root: Path, *, label: str) -> Path:
    raw = Path(path)
    try:
        info = raw.lstat()
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise InfrastructureError(f"{label} is not a readable regular file") from exc
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    owned_root = Path(root).resolve(strict=True)
    if (
        not stat_module.S_ISREG(info.st_mode)
        or stat_module.S_ISLNK(info.st_mode)
        or attributes & 0x0400
        or (resolved != owned_root and owned_root not in resolved.parents)
    ):
        raise InfrastructureError(f"{label} escapes its repository-owned directory")
    return resolved


def _committed_file_bytes(
    path: Path,
    *,
    commit: str,
    max_bytes: int = 4 * 1024 * 1024,
) -> bytes:
    resolved = Path(path).resolve(strict=True)
    try:
        rel = resolved.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise InfrastructureError("trusted matrix asset is outside the repository") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", str(commit).lower()):
        raise InfrastructureError("trusted matrix asset has no committed engine identity")
    with tempfile.TemporaryDirectory(prefix="signalos-asset-git-") as temporary:
        env = _tool_subprocess_env(Path(temporary))
        try:
            completed = subprocess.run(
                ["git", "show", f"{commit}:{rel}"],
                cwd=str(ROOT),
                capture_output=True,
                timeout=30,
                check=False,
                env=env,
            )
        finally:
            _clear_parent_environment(env)
    if completed.returncode != 0:
        raise InfrastructureError("trusted matrix asset is not tracked at the attested commit")
    payload = bytes(completed.stdout)
    if not payload or len(payload) > max_bytes:
        raise InfrastructureError("trusted matrix asset has an invalid size")
    return payload


def _trusted_oracle_asset(
    scenario_path: Path,
    scenario: dict[str, Any],
    *,
    engine: dict[str, Any],
    live: bool,
    scenario_source: bytes | None = None,
) -> dict[str, Any]:
    scenario_file = _regular_owned_file(
        scenario_path,
        SCENARIO_ROOT,
        label="matrix scenario",
    )
    oracle_rel = Path(str(scenario.get("oracle") or ""))
    if oracle_rel.is_absolute() or ".." in oracle_rel.parts:
        raise InfrastructureError("matrix oracle path must be repository-relative")
    oracle_file = _regular_owned_file(
        scenario_file.parent.parent / oracle_rel,
        ORACLE_ROOT,
        label="matrix oracle",
    )
    if oracle_file.suffix.lower() not in {".js", ".mjs", ".cjs"}:
        raise InfrastructureError("matrix oracle must be a tracked JavaScript module")
    current_scenario = scenario_file.read_bytes()
    sealed_scenario = (
        bytes(scenario_source)
        if scenario_source is not None
        else current_scenario
    )
    current_oracle = oracle_file.read_bytes()
    if live:
        commit = str(engine.get("commit") or "").lower()
        committed_scenario = _committed_file_bytes(scenario_file, commit=commit)
        committed_oracle = _committed_file_bytes(oracle_file, commit=commit)
        if (
            sealed_scenario != committed_scenario
            or current_scenario != committed_scenario
            or current_oracle != committed_oracle
        ):
            raise InfrastructureError(
                "matrix scenario/oracle bytes differ from the CI-attested commit"
            )
        oracle_source = committed_oracle
    else:
        oracle_source = current_oracle
    return {
        "name": oracle_file.name,
        "source": oracle_source,
        "sha256": hashlib.sha256(oracle_source).hexdigest(),
        "repository_path": oracle_file.relative_to(ROOT).as_posix(),
    }


def _parse_scenario(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("scenario must be a JSON object")
    required = ("id", "name", "profile", "prompt", "requirements", "expected_gates", "oracle")
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ValueError("scenario is missing: " + ", ".join(missing))
    expected = raw.get("expected_gates")
    if expected != list(GATES):
        raise ValueError(f"scenario expected_gates must be exactly {list(GATES)!r}")
    requirements = raw.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("scenario requirements must be a non-empty list")
    ids: list[str] = []
    for item in requirements:
        if not isinstance(item, dict) or not str(item.get("id") or "").startswith("REQ-"):
            raise ValueError("each scenario requirement needs a REQ-* id")
        req_id = str(item["id"])
        if req_id in ids or req_id not in str(raw["prompt"]):
            raise ValueError(f"scenario requirement {req_id} is duplicate or absent from prompt")
        ids.append(req_id)
    trace_gates = raw.get("trace_requirements_through_gates") or []
    if not isinstance(trace_gates, list) or any(g not in GATES for g in trace_gates):
        raise ValueError("trace_requirements_through_gates contains an invalid gate")
    raw["requirement_ids"] = ids
    return raw


def _load_scenario(path: Path) -> dict[str, Any]:
    return _parse_scenario(_read_json(path))


def _load_scenario_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    return _parse_scenario(_read_json_bytes(payload, label=label))


def _validate_orchestrator_profile(value: str) -> str:
    profile = str(value or "").strip()
    if profile not in ORCHESTRATOR_PROFILES:
        raise ValueError(
            f"unknown orchestrator profile {profile!r}; expected one of "
            + ", ".join(ORCHESTRATOR_PROFILES)
        )
    return profile


def _delivery_request(
    *,
    prompt: str,
    spec: ModelSpec,
    run_id: str,
    orchestrator_profile: str,
    provider_context_length: int,
) -> dict[str, Any]:
    """Build the paid delivery request with an explicit release profile."""
    profile = _validate_orchestrator_profile(orchestrator_profile)
    return {
        "prompt": prompt,
        "provider": spec.provider,
        "model": spec.model,
        "run_id": run_id,
        "profile": profile,
        "provider_context_length": provider_context_length,
    }


def _run_row(
    spec: ModelSpec,
    scenario: dict[str, Any],
    oracle_asset: dict[str, Any],
    row_dir: Path,
    *,
    key: str,
    key_source: str,
    router: OpenRouterClient,
    cost_cap: float,
    init_timeout: float,
    gate_timeout: float,
    g4_build_timeout: float,
    command_timeout: float,
    orchestrator_profile: str,
    engine: dict[str, Any],
    hashes: dict[str, str],
    provider_model_metadata: dict[str, Any],
    funded_context: FundedRunContext,
    oracle_context: FundedRunContext,
) -> dict[str, Any]:
    orchestrator_profile = _validate_orchestrator_profile(orchestrator_profile)
    workspace = row_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    run_id = f"matrix-{spec.alias}-{uuid.uuid4().hex[:12]}"
    row: dict[str, Any] = {
        "schema": "signalos.backend-matrix.row.v1",
        "status": "running",
        "started_at": _utc_now(),
        "provider": spec.provider,
        "model_alias": spec.alias,
        "model_cohort": spec.cohort,
        "model": spec.model,
        "key_source": key_source,
        "run_id": run_id,
        "scenario": scenario["id"],
        "profile": scenario["profile"],
        "stack_profile": scenario["profile"],
        "orchestrator_profile": orchestrator_profile,
        "engine": engine,
        "hashes": hashes,
        "provider_route": provider_model_metadata,
        "calls": [],
        "gates": [],
    }
    result_path = row_dir / "result.json"
    row_secrets = tuple(
        dict.fromkeys(
            (
                *funded_context.redaction_secrets(key),
                *oracle_context.redaction_secrets(),
            )
        )
    )
    attestation_needles = (
        *funded_context.attestation_scan_needles(),
        *oracle_context.attestation_scan_needles(),
    )
    _safe_json_write(result_path, row, row_secrets)

    tool_env: dict[str, str] = {}
    sidecar_env: dict[str, str] = {}
    sidecar: SidecarClient | None = None
    cost: CostGuard | None = None
    try:
        tool_env = _tool_subprocess_env(row_dir / "tool-runtime-home")
        baseline = _snapshot_product_tree(workspace)
        release_origin = row_dir / "release-origin.git"
        sidecar_env = funded_context.sidecar_environment(
            row_dir / "runtime-home",
            spec=spec,
            provider_key=key,
            expected_git_remote=release_origin,
        )
        sidecar = SidecarClient(workspace, sidecar_env, row_secrets)

        def invoke(command: str, args: Any, timeout: float, *, guarded: bool = True) -> dict[str, Any]:
            started = time.monotonic()
            events, terminal = sidecar.call(
                command,
                args,
                timeout=timeout,
                guard=(lambda: cost.check()) if guarded and cost is not None else None,
            )
            if guarded and cost is not None:
                cost.check(force=True)
            evidence = _terminal_evidence(
                command, terminal, events, time.monotonic() - started, row_secrets
            )
            row["calls"].append(evidence)
            _safe_json_write(result_path, row, row_secrets)
            return terminal

        capabilities = _require_ok(
            "capabilities", invoke("capabilities", [], 30, guarded=False)
        )
        required_commands = {
            "gate0:approve",
            "agent:deliver",
            "agent:verdict",
            "agent:cancel",
            "agent:resume",
        }
        if capabilities.get("protocol") != 1:
            raise InfrastructureError(
                f"unsupported sidecar protocol: {capabilities.get('protocol')!r}"
            )
        advertised = set(capabilities.get("commands") or [])
        if not required_commands.issubset(advertised):
            raise InfrastructureError(
                "sidecar capabilities omit: " + ", ".join(sorted(required_commands - advertised))
            )
        row["capabilities"] = {
            "version": capabilities.get("version"),
            "protocol": capabilities.get("protocol"),
            "required_commands": sorted(required_commands),
        }

        init_terminal = invoke(
            "signal-init",
            [
                "--mode",
                "keep",
                "--name",
                str(scenario["name"]),
                "--profile",
                str(scenario["profile"]),
            ],
            init_timeout,
            guarded=False,
        )
        _require_ok("signal-init", init_terminal)
        marker = workspace / ".signalos" / "INIT_COMPLETE.json"
        if not marker.is_file():
            raise InfrastructureError("signal-init returned success without INIT_COMPLETE.json")
        # The Tauri onboarding surface normally persists this identity.  This
        # backend-only harness simulates that same host boundary explicitly so
        # G0 can exercise the real exact-consent transaction rather than a raw
        # signer-role shortcut.
        _safe_json_write(
            workspace / ".signalos" / "identity.json",
            {
                "name": "SignalOS Backend Matrix (simulated founder)",
                "role": "PO",
            },
        )
        row["release_origin"] = _prepare_local_release_remote(
            workspace,
            release_origin,
            env=tool_env,
        )
        _safe_json_write(result_path, row, row_secrets)
        baseline = _snapshot_product_tree(workspace)

        # Start cost accounting only after local initialization.  Every model
        # call made by this delivery contributes to this one cumulative cap.
        cost = CostGuard(router, cost_cap)

        deliver = invoke(
            "agent:deliver",
            _delivery_request(
                prompt=str(scenario["prompt"]),
                spec=spec,
                run_id=run_id,
                orchestrator_profile=orchestrator_profile,
                provider_context_length=int(
                    provider_model_metadata["context_length"]
                ),
            ),
            gate_timeout,
        )
        deliver_data = _require_ok("agent:deliver", deliver)
        if deliver_data.get("run_id") != run_id or deliver_data.get("gate") != "G0":
            raise ProductFailure("agent:deliver returned the wrong run or first gate")
        persisted_start = _load_delivery(workspace, run_id)
        persisted_profile = persisted_start.get("profile")
        row["persisted_orchestrator_profile"] = persisted_profile
        if persisted_profile != orchestrator_profile:
            raise ProductFailure(
                "agent:deliver profile provenance mismatch: requested "
                f"{orchestrator_profile!r}, persisted {persisted_profile!r}"
            )

        trace_gates = set(scenario.get("trace_requirements_through_gates") or [])
        for index, gate in enumerate(GATES):
            signed_before = list(GATES[:index])
            state = _load_delivery(workspace, run_id)
            dependency_receipt: dict[str, Any] | None = None
            if gate == "G4":
                # G4 owns dependency materialization through the real backend.
                # Verify that receipt at the first G4 checkpoint, before any
                # review or approval can treat the workspace as build-ready.
                dependency_receipt = funded_context.verify_materialized_after_init(
                    workspace
                )
            _validate_review_checkpoint(
                state, run_id=run_id, gate=gate, signed_before=signed_before
            )
            gate_evidence: dict[str, Any] = {
                "gate": gate,
                "checkpoint_status": state.get("status"),
                "last_outcome": state.get("last_outcome"),
                "signed_before": signed_before,
            }
            if dependency_receipt is not None:
                gate_evidence["funded_dependencies"] = dependency_receipt
            if gate in trace_gates:
                # Cumulative corpus: a requirement registered at an earlier
                # traced gate stays traceable here (artifacts persist on disk),
                # so scan every traced gate up to and including this one.
                cumulative = [g for g in GATES[: index + 1] if g in trace_gates]
                trace = _gate_requirement_trace(
                    workspace,
                    gate,
                    scenario["requirement_ids"],
                    scan_gates=cumulative,
                )
                gate_evidence["requirement_trace"] = trace
                if not trace["ok"]:
                    raise ProductFailure(
                        f"{gate} governance artifacts omit requirement IDs: "
                        + ", ".join(trace["missing_requirement_ids"])
                    )

            if gate == "G0":
                approval = invoke(
                    "gate0:approve",
                    [json.dumps({
                        "consent": "I approve Gate 0 as sole founder",
                        "via": "simulation",
                        "expected_workspace": str(workspace.resolve()),
                        "expected_project_id": "default",
                        "approval_id": f"matrix-{run_id}-g0",
                    })],
                    60,
                    guarded=False,
                )
                approval_data = _require_ok("gate0:approve", approval)
                if approval_data.get("signed") is not True:
                    raise ProductFailure(
                        f"the simulated founder G0 approval was refused: {approval_data!r}"
                    )
                gate_evidence["authority_approval"] = approval_data

            # G4's verdict runs the funded build + a four-seat verification fleet,
            # so it gets the larger build budget; every other gate uses the
            # per-gate default (fast-fail on a hang).
            verdict_timeout = g4_build_timeout if gate == "G4" else gate_timeout
            verdict = invoke(
                "agent:verdict",
                {
                    "run_id": run_id,
                    "gate_id": gate,
                    "verdict": "approve",
                    "feedback": "",
                },
                verdict_timeout,
            )
            verdict_data = _require_ok("agent:verdict", verdict)
            strict = _strict_gate(workspace, gate)
            gate_evidence["strict_signature"] = strict
            gate_evidence["verdict_result"] = verdict_data
            row["gates"].append(gate_evidence)
            _safe_json_write(result_path, row, row_secrets)
            if not strict["signed"]:
                raise ProductFailure(
                    f"{gate} did not pass strict signature validation: "
                    + "; ".join(strict["reasons"][:3])
                )
            if gate != "G5":
                expected_next = GATES[index + 1]
                if verdict_data.get("status") != "advanced" or verdict_data.get("gate") != expected_next:
                    raise ProductFailure(
                        f"{gate} approval did not advance exactly to {expected_next}: {verdict_data!r}"
                    )
            else:
                finalization = verdict_data.get("release_finalization")
                if (
                    verdict_data.get("status") != "complete"
                    or verdict_data.get("ready") is not True
                    or verdict_data.get("waived") != []
                    or verdict_data.get("conditions") != {}
                    or not isinstance(finalization, dict)
                    or finalization.get("status") != "succeeded"
                ):
                    raise ProductFailure(
                        f"G5 returned a non-ready or conditional completion: {verdict_data!r}"
                    )

        final_state = _load_delivery(workspace, run_id)
        if (
            final_state.get("status") != "complete"
            or final_state.get("signed") != list(GATES)
            or final_state.get("waived")
            or final_state.get("conditions")
            or final_state.get("profile") != orchestrator_profile
        ):
            raise ProductFailure("persisted final delivery state is not an unconditional completion")
        release_evidence = final_state.get("release_evidence")
        if not isinstance(release_evidence, dict):
            raise ProductFailure("persisted final delivery omits release evidence")
        finalization = release_evidence.get("release_finalization")
        if not isinstance(finalization, dict) or finalization.get("status") != "succeeded":
            raise ProductFailure("persisted release finalization did not succeed")
        if orchestrator_profile == "production":
            security = release_evidence.get("security_gate")
            runtime = release_evidence.get("runtime_proof")
            if not isinstance(security, dict) or security.get("status") != "passed":
                raise ProductFailure("production delivery lacks passing security-gate evidence")
            if not isinstance(runtime, dict) or runtime.get("ok") is not True:
                raise ProductFailure("production delivery lacks passing runtime proof")

        gates_terminal = invoke("state:gates", [], 45, guarded=False)
        if gates_terminal.get("ok") is not True or not isinstance(gates_terminal.get("data"), list):
            raise ProductFailure("state:gates did not return a gate list")
        state_gates = gates_terminal["data"]
        if len(state_gates) != len(GATES) or any(g.get("status") != "signed" for g in state_gates):
            raise ProductFailure("state:gates does not report every gate strictly signed")
        row["state_gates"] = state_gates

        # Freeze the evidence boundary before reading/copying product bytes.
        # The sidecar owns every generated command process; closing its tree
        # here prevents a leftover G4 child from racing the snapshot, secret
        # scan, reparse-point checks, clean-room build, or browser oracle.
        row["sidecar_stderr_tail"] = sidecar.stderr_tail()
        sidecar.close()
        sidecar = None

        local_final_tree = _snapshot_product_tree(workspace)
        release_checkout = row_dir / "release-checkout"
        row["release_checkout"] = _checkout_pushed_release(
            Path(row["release_origin"]["path"]),
            release_checkout,
            finalization,
            env=tool_env,
        )
        final_tree = _snapshot_product_tree(release_checkout)
        if final_tree != local_final_tree:
            raise ProductFailure(
                "pushed release product bytes differ from the finalized local workspace"
            )
        changed_sources = _changed_product_sources(baseline, final_tree)
        if not changed_sources:
            raise ProductFailure("this run produced no new or changed real product source files")
        row["product_tree"] = {
            "baseline": baseline,
            "final": final_tree,
            "local_matches_remote": True,
            "changed_source_files": changed_sources,
        }

        scan = _secret_scan(
            release_checkout,
            key,
            exact_values=attestation_needles,
        )
        row["secret_scan"] = scan
        if not scan["ok"]:
            raise InfrastructureError(
                "selected provider key or key-shaped text was written into the workspace"
            )

        acceptance: dict[str, Any] = {"commands": {}}
        row["acceptance"] = acceptance
        _clean_room_acceptance(
            release_checkout,
            row_dir / "clean-room",
            oracle_asset,
            scenario_id=str(scenario["id"]),
            timeout=command_timeout,
            secrets=row_secrets,
            funded_context=funded_context,
            oracle_context=oracle_context,
            evidence=acceptance,
        )
        final_spent = cost.check(force=True)
        row["provider_usage"] = {
            "start": cost.started_usage,
            "end": cost.last_usage,
            "spent": final_spent,
            "cap": cost_cap,
            "backward_observations": cost.backward_observations,
        }
        row["status"] = "pass"
    except ProductFailure as exc:
        row["status"] = "fail"
        row["failure_type"] = "product"
        row["error"] = str(redact(str(exc), row_secrets))
    except (InfrastructureError, CostGuardError, ValueError) as exc:
        row["status"] = "error"
        row["failure_type"] = "infrastructure" if not isinstance(exc, CostGuardError) else "cost-guard"
        row["error"] = str(redact(str(exc), row_secrets))
    except Exception as exc:  # Never make a row disappear without evidence.
        row["status"] = "error"
        row["failure_type"] = "unexpected-harness-error"
        row["error"] = str(redact(f"{type(exc).__name__}: {exc}", row_secrets))
    finally:
        close_error = ""
        try:
            if sidecar is not None:
                row["sidecar_stderr_tail"] = sidecar.stderr_tail()
                sidecar.close()
        except Exception as exc:
            close_error = str(
                redact(
                    f"{type(exc).__name__}: {exc}",
                    row_secrets,
                )
            )
        finally:
            _clear_parent_environment(sidecar_env)
            _clear_parent_environment(tool_env)
        if close_error:
            row["status"] = "error"
            row["failure_type"] = "process-cleanup"
            row["error"] = "source sidecar cleanup failed: " + close_error
        # Scan the entire retained row (workspace, isolated runtime home,
        # clean-room, and evidence), even after a partial failure.  This must
        # happen after the sidecar stops so it cannot write a secret after our
        # check.
        if row_dir.exists():
            final_scan = _secret_scan(
                row_dir,
                key,
                exact_values=attestation_needles,
            )
            final_scan["scope"] = "entire-retained-row"
            row["secret_scan"] = final_scan
            if not final_scan["ok"]:
                row["status"] = "error"
                row["failure_type"] = "credential-leak"
                row["error"] = "selected provider key or key-shaped text was written into the workspace"
        if cost is not None:
            row.setdefault(
                "provider_usage",
                {
                    "start": cost.started_usage,
                    "end": cost.last_usage,
                    "spent": cost.spent,
                    "cap": cost_cap,
                    "backward_observations": cost.backward_observations,
                },
            )
        row["finished_at"] = _utc_now()
        _safe_json_write(result_path, row, row_secrets)
    return row


def _bounded_dependency_timeout(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("dependency timeout must be a number") from exc
    if not 1.0 <= seconds <= 3600.0:
        raise argparse.ArgumentTypeError(
            "dependency timeout must be between 1 and 3600 seconds"
        )
    return seconds


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the versioned SignalOS backend journey model matrix."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list-models", action="store_true", help="List configured models; no network or key needed.")
    action.add_argument("--preflight", action="store_true", help="Verify local tools, key, and live model availability without generation.")
    action.add_argument("--live", action="store_true", help="Explicitly enable paid provider calls and product generation.")
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help=(
            "Aliases/model IDs or cohort names (primary, challenger, exploratory) "
            "in order, comma-separated values, or all."
        ),
    )
    parser.add_argument("--models-config", type=Path, default=DEFAULT_MODELS_CONFIG)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument(
        "--dependency-policy",
        type=Path,
        default=DEFAULT_DEPENDENCY_POLICY,
        help="Reviewed funded dependency policy (live runs require the repository default).",
    )
    parser.add_argument(
        "--orchestrator-profile",
        choices=ORCHESTRATOR_PROFILES,
        default=DEFAULT_ORCHESTRATOR_PROFILE,
        help=(
            "Release-enforcement profile sent explicitly to agent:deliver; "
            "benchmark is the stable comparison default."
        ),
    )
    parser.add_argument("--env-file", type=Path, default=None, help="Explicit dotenv path; never copied into results.")
    parser.add_argument("--output-root", type=Path, default=Path(tempfile.gettempdir()) / "signalos-backend-matrix")
    parser.add_argument("--max-cost-per-model", type=float, default=None, metavar="USD")
    parser.add_argument("--acknowledge-key-exposure", action="store_true")
    parser.add_argument("--init-timeout", type=float, default=240.0, metavar="SECONDS")
    parser.add_argument("--gate-timeout", type=float, default=1800.0, metavar="SECONDS")
    # G4 is not a normal gate: its verdict runs the funded build (Docker
    # dependency materialization + npm ci through the proxy + Vite build) AND a
    # verification fleet of four seat sub-agents (build/test/review/security),
    # each a full LLM turn. That is ~10x a single-agent gate, so it gets its own
    # budget rather than the per-gate default (run 11 signed G0-G3 then hit the
    # 30-min gate_timeout ~29 min into a steadily-progressing G4).
    parser.add_argument("--g4-build-timeout", type=float, default=5400.0, metavar="SECONDS")
    parser.add_argument("--command-timeout", type=float, default=900.0, metavar="SECONDS")
    parser.add_argument(
        "--dependency-timeout",
        type=_bounded_dependency_timeout,
        default=900.0,
        metavar="SECONDS",
    )
    parser.add_argument("--fail-fast", action="store_true")
    return parser


def _print_models(catalog: Sequence[ModelSpec]) -> None:
    print("alias\tcohort\tprovider\tmodel")
    for spec in catalog:
        print(f"{spec.alias}\t{spec.cohort}\t{spec.provider}\t{spec.model}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    key = ""
    active_secrets: tuple[str, ...] = ()
    router: OpenRouterClient | None = None
    try:
        models_config_path = args.models_config.expanduser().resolve()
        if args.list_models:
            catalog = load_model_catalog(models_config_path)
            _print_models(catalog)
            return 0

        if not args.preflight and not args.live:
            parser.error("choose --preflight or explicitly opt in to paid work with --live")
        if args.live:
            if not args.acknowledge_key_exposure:
                parser.error("--live requires --acknowledge-key-exposure; see the backend matrix README")
            if args.max_cost_per_model is None or args.max_cost_per_model <= 0:
                parser.error("--live requires a positive --max-cost-per-model USD cap")
            if models_config_path != DEFAULT_MODELS_CONFIG.resolve():
                raise ValueError(
                    "live funded runs require the repository-owned model catalog"
                )
        dependency_policy = args.dependency_policy.expanduser().resolve()
        if args.live and dependency_policy != DEFAULT_DEPENDENCY_POLICY.resolve():
            raise ValueError(
                "live funded runs require the repository-owned dependency policy"
            )
        scenario_path = args.scenario.expanduser().resolve()
        orchestrator_profile = _validate_orchestrator_profile(args.orchestrator_profile)
        engine = _engine_metadata()
        _require_reproducible_engine(engine, live=bool(args.live))

        if args.live:
            commit = str(engine.get("commit") or "").lower()
            models_file = _regular_owned_file(
                models_config_path,
                DEFAULT_MODELS_CONFIG.parent,
                label="matrix model catalog",
            )
            reviewed_models_bytes = _committed_file_bytes(
                models_file,
                commit=commit,
            )
            if models_file.read_bytes() != reviewed_models_bytes:
                raise InfrastructureError(
                    "matrix model catalog differs from the CI-attested commit"
                )
        else:
            reviewed_models_bytes = models_config_path.read_bytes()
        catalog = _load_model_catalog_bytes(
            reviewed_models_bytes,
            label=str(models_config_path),
        )

        # Selection uses the sealed catalog and deliberately precedes key
        # lookup and every provider/GitHub request.  Only local Git identity
        # discovery is allowed ahead of it for a live run.
        selected = select_models(catalog, args.models)
        if any(
            spec.key_env != selected[0].key_env
            or spec.provider != selected[0].provider
            for spec in selected
        ):
            raise ValueError(
                "a single run may use only one provider and API key environment"
            )

        scenario_file = _regular_owned_file(
            scenario_path,
            SCENARIO_ROOT,
            label="matrix scenario",
        )
        current_scenario_bytes = scenario_file.read_bytes()
        if args.live:
            reviewed_scenario_bytes = _committed_file_bytes(
                scenario_file,
                commit=str(engine.get("commit") or "").lower(),
            )
            if current_scenario_bytes != reviewed_scenario_bytes:
                raise InfrastructureError(
                    "matrix scenario differs from the CI-attested commit"
                )
        else:
            reviewed_scenario_bytes = current_scenario_bytes
        scenario = _load_scenario_bytes(
            reviewed_scenario_bytes,
            label=str(scenario_file),
        )

        # This authoritative GitHub check deliberately precedes provider-key
        # lookup, backend-sidecar startup, and every provider request.  A
        # caller-supplied SHA is not evidence that CI passed.
        ci_attestation = (
            _verify_ci_attestation(engine) if args.live else None
        )
        oracle_asset = _trusted_oracle_asset(
            scenario_path,
            scenario,
            engine=engine,
            live=bool(args.live),
            scenario_source=reviewed_scenario_bytes,
        )
        reviewed_policy_bytes = dependency_policy.read_bytes()
        oracle_dependency_policy = DEFAULT_ORACLE_DEPENDENCY_POLICY.resolve()
        reviewed_oracle_policy_bytes = oracle_dependency_policy.read_bytes()
        if args.live:
            committed_policy = _committed_file_bytes(
                dependency_policy,
                commit=str(engine.get("commit") or "").lower(),
            )
            if reviewed_policy_bytes != committed_policy:
                raise InfrastructureError(
                    "dependency policy differs from the CI-attested commit"
                )
            reviewed_policy_bytes = committed_policy
            committed_oracle_policy = _committed_file_bytes(
                oracle_dependency_policy,
                commit=str(engine.get("commit") or "").lower(),
            )
            if reviewed_oracle_policy_bytes != committed_oracle_policy:
                raise InfrastructureError(
                    "oracle dependency policy differs from the CI-attested commit"
                )
            reviewed_oracle_policy_bytes = committed_oracle_policy
        reviewed_policy_sha256 = hashlib.sha256(reviewed_policy_bytes).hexdigest()
        reviewed_oracle_policy_sha256 = hashlib.sha256(
            reviewed_oracle_policy_bytes
        ).hexdigest()
        output_root = (
            _require_external_output_root(args.output_root)
            if args.live
            else args.output_root.expanduser().resolve()
        )
        local = _local_preflight()
        if args.live:
            _require_engine_unchanged(engine)
        final_output: dict[str, Any] | None = None
        manifest_path: Path | None = None
        exit_code = 0
        with (
            FundedRunContext.prepare(
                dependency_policy,
                timeout=args.dependency_timeout,
                expected_profile=str(scenario["profile"]),
            ) as funded_context,
            FundedRunContext.prepare(
                oracle_dependency_policy,
                timeout=args.dependency_timeout,
                expected_profile=ORACLE_RUNTIME_PROFILE,
            ) as oracle_context,
        ):
            active_secrets = tuple(
                dict.fromkeys(
                    (
                        *funded_context.redaction_secrets(),
                        *oracle_context.redaction_secrets(),
                    )
                )
            )
            if (
                funded_context.public_evidence().get("policy_sha256")
                != reviewed_policy_sha256
                or _sha256_file(dependency_policy) != reviewed_policy_sha256
            ):
                raise InfrastructureError(
                    "prepared dependency receipt is not bound to the reviewed policy bytes"
                )
            if (
                oracle_context.public_evidence().get("policy_sha256")
                != reviewed_oracle_policy_sha256
                or _sha256_file(oracle_dependency_policy)
                != reviewed_oracle_policy_sha256
            ):
                raise InfrastructureError(
                    "prepared oracle receipt is not bound to the reviewed policy bytes"
                )
            if args.live:
                _require_engine_unchanged(engine)
            backend = _backend_preflight(
                scenario,
                init_timeout=args.init_timeout,
                dependency_timeout=args.dependency_timeout,
                funded_context=funded_context,
            )
            oracle_runtime = oracle_context.browser_runtime_probe(
                timeout=args.command_timeout,
            )
            # Credential lookup and all provider construction happen only
            # after both keyless funded paths prove scaffold, dependency,
            # offline execution, and browser/Chromium readiness.
            if args.live:
                _require_engine_unchanged(engine)
            key, key_source = _resolve_api_key(selected[0], args.env_file)
            funded_context.register_exact_secret("exact-selected-key", key)
            active_secrets = tuple(
                dict.fromkeys(
                    (
                        *funded_context.redaction_secrets(key),
                        *oracle_context.redaction_secrets(),
                    )
                )
            )
            router = OpenRouterClient(key)
            provider = _provider_preflight(
                router,
                selected,
                required_remaining=float(args.max_cost_per_model or 0.0)
                * len(selected),
                require_provider_limit=bool(args.live),
            )
            provider_stack = _provider_stack_preflight(
                selected,
                provider["models"],
            )
            if args.live:
                _require_engine_unchanged(engine)
            if args.preflight:
                final_output = {
                    "status": "pass",
                    "mode": "preflight",
                    "key_source": key_source,
                    "local": local,
                    "backend": backend,
                    "dependencies": funded_context.public_evidence(),
                    "oracle_dependencies": oracle_context.public_evidence(),
                    "oracle_runtime": oracle_runtime,
                    "provider": provider,
                    "provider_stack": provider_stack,
                    "orchestrator_profile": orchestrator_profile,
                }
            else:
                stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                run_root = output_root / f"{stamp}-{uuid.uuid4().hex[:8]}"
                run_root.mkdir(parents=True, exist_ok=False)
                funded_context.register_scan_root(run_root)
                oracle_context.register_scan_root(run_root)
                oracle_hashes = {
                    f"oracle_{name}": value
                    for name, value in oracle_context.evidence_hashes().items()
                }
                hashes = {
                    "models_config_sha256": hashlib.sha256(
                        reviewed_models_bytes
                    ).hexdigest(),
                    "scenario_sha256": hashlib.sha256(
                        reviewed_scenario_bytes
                    ).hexdigest(),
                    "oracle_sha256": str(oracle_asset["sha256"]),
                    "driver_sha256": _sha256_file(Path(__file__).resolve()),
                    "sidecar_sha256": _sha256_file(SIDECAR),
                    "windows_job_bootstrap_sha256": _sha256_file(
                        WINDOWS_JOB_BOOTSTRAP
                    ),
                    "ci_policy_sha256": _sha256_file(DEFAULT_CI_POLICY),
                    **funded_context.evidence_hashes(),
                    **oracle_hashes,
                }
                manifest = {
                    "schema": "signalos.backend-matrix.run.v1",
                    "started_at": _utc_now(),
                    "status": "running",
                    "run_root": str(run_root),
                    "scenario": scenario["id"],
                    "stack_profile": scenario["profile"],
                    "orchestrator_profile": orchestrator_profile,
                    "models": [dataclasses.asdict(spec) for spec in selected],
                    "engine": engine,
                    "ci_attestation": ci_attestation,
                    "hashes": hashes,
                    "preflight": {
                        "local": local,
                        "backend": backend,
                        "dependencies": funded_context.public_evidence(),
                        "oracle_dependencies": oracle_context.public_evidence(),
                        "oracle_runtime": oracle_runtime,
                        "provider": provider,
                        "provider_stack": provider_stack,
                        "key_source": key_source,
                    },
                    "results": [],
                }
                manifest_path = run_root / "matrix-result.json"
                _safe_json_write(manifest_path, manifest, active_secrets)
                provider_models = {
                    str(row["id"]): row for row in provider["models"]
                }
                for spec in selected:
                    _require_engine_unchanged(engine)
                    print(f"[{spec.alias}] starting fresh backend journey", flush=True)
                    row = _run_row(
                        spec,
                        scenario,
                        oracle_asset,
                        run_root / spec.alias,
                        key=key,
                        key_source=key_source,
                        router=router,
                        cost_cap=float(args.max_cost_per_model),
                        init_timeout=args.init_timeout,
                        gate_timeout=args.gate_timeout,
                        g4_build_timeout=args.g4_build_timeout,
                        command_timeout=args.command_timeout,
                        orchestrator_profile=orchestrator_profile,
                        engine=engine,
                        hashes=hashes,
                        provider_model_metadata=provider_models[spec.model],
                        funded_context=funded_context,
                        oracle_context=oracle_context,
                    )
                    _require_engine_unchanged(engine)
                    manifest["results"].append(
                        {
                            "alias": spec.alias,
                            "cohort": spec.cohort,
                            "model": spec.model,
                            "status": row.get("status"),
                            "failure_type": row.get("failure_type"),
                            "error": row.get("error"),
                            "result": str(
                                (run_root / spec.alias / "result.json").relative_to(
                                    run_root
                                )
                            ),
                        }
                    )
                    _safe_json_write(manifest_path, manifest, active_secrets)
                    print(f"[{spec.alias}] {row.get('status')}", flush=True)
                    if args.fail_fast and row.get("status") != "pass":
                        break
                exit_code = results_exit_code(manifest["results"])
                manifest["status"] = "finalizing"
                manifest["provisional_status"] = (
                    "pass" if exit_code == 0 else "fail"
                )
                _safe_json_write(manifest_path, manifest, active_secrets)
                retained_scan = _secret_scan(
                    run_root,
                    key,
                    exact_values=(
                        *funded_context.attestation_scan_needles(),
                        *oracle_context.attestation_scan_needles(),
                    ),
                )
                if not retained_scan["ok"]:
                    _purge_external_owned_root(run_root)
                    raise InfrastructureError(
                        "unsafe secret evidence was found; retained run output was removed"
                    )

                # A result may become final only after the shared dependency
                # scratch is absent, registered secrets are scanned, and the
                # mutable key owner is zeroed.  __exit__ calls close again;
                # close is deliberately idempotent.
                product_attestation_hex = funded_context.redaction_secrets()[-1]
                oracle_attestation_hex = oracle_context.redaction_secrets()[-1]
                oracle_cleanup_evidence = oracle_context.close()
                product_cleanup_evidence = funded_context.close()
                manifest.pop("provisional_status", None)
                manifest["status"] = "pass" if exit_code == 0 else "fail"
                manifest["finished_at"] = _utc_now()
                product_cleanup_scan = (
                    product_cleanup_evidence.get("secret_scan") or {}
                )
                oracle_cleanup_scan = (
                    oracle_cleanup_evidence.get("secret_scan") or {}
                )
                manifest["dependency_cleanup"] = {
                    "product": {
                        "secret_scan": {
                            "ok": product_cleanup_scan.get("ok") is True,
                            "files_scanned": int(
                                product_cleanup_scan.get("files_scanned") or 0
                            ),
                            "error_count": len(
                                product_cleanup_scan.get("errors") or []
                            ),
                        },
                        "scratch_removed": product_cleanup_evidence.get(
                            "scratch_removed"
                        )
                        is True,
                        "key_zeroed": product_cleanup_evidence.get("key_zeroed")
                        is True,
                    },
                    "oracle": {
                        "secret_scan": {
                            "ok": oracle_cleanup_scan.get("ok") is True,
                            "files_scanned": int(
                                oracle_cleanup_scan.get("files_scanned") or 0
                            ),
                            "error_count": len(
                                oracle_cleanup_scan.get("errors") or []
                            ),
                        },
                        "scratch_removed": oracle_cleanup_evidence.get(
                            "scratch_removed"
                        )
                        is True,
                        "key_zeroed": oracle_cleanup_evidence.get("key_zeroed")
                        is True,
                    },
                }
                _safe_json_write(manifest_path, manifest, active_secrets)
                final_scan = _secret_scan(
                    run_root,
                    key,
                    exact_values=(
                        (
                            "exact-product-dependency-attestation-key-hex",
                            product_attestation_hex.encode("ascii"),
                        ),
                        (
                            "exact-oracle-dependency-attestation-key-hex",
                            oracle_attestation_hex.encode("ascii"),
                        ),
                    ),
                )
                if not final_scan["ok"]:
                    _purge_external_owned_root(run_root)
                    raise InfrastructureError(
                        "final result secret scan failed; retained run output was removed"
                    )

        if final_output is not None:
            print(
                json.dumps(
                    redact(final_output, active_secrets),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if manifest_path is None:
            raise InfrastructureError("live run completed without a result bundle")
        print(f"Result bundle: {manifest_path}")
        return exit_code
    except (ValueError, HarnessError, OSError) as exc:
        print(
            f"backend matrix error: {redact(str(exc), active_secrets)}",
            file=sys.stderr,
        )
        return 2
    finally:
        if router is not None:
            router._key = ""
        key = ""
        active_secrets = ()


if __name__ == "__main__":
    raise SystemExit(main())
