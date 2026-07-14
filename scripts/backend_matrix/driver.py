#!/usr/bin/env python3
"""Repeatable, fail-closed SignalOS backend journey benchmark.

This is intentionally an external driver.  It talks to the same long-lived
NDJSON sidecar used by the desktop host, but it does not import or construct the
GateOrchestrator.  That keeps the system boundary under test honest while still
allowing deterministic, independent review of persisted gate evidence.

Live runs spend provider credit and currently expose the selected provider key
to product subprocesses spawned by the backend.  Consequently live execution
requires both an explicit ``--live`` switch and an explicit acknowledgement.
No credential is accepted on the command line or written to result bundles.
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
import shutil
import signal
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, deque
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS_CONFIG = Path(__file__).with_name("models.json")
DEFAULT_SCENARIO = Path(__file__).with_name("scenarios") / "expense_tracker.json"
SIDECAR = ROOT / "python" / "signalos_ipc_server.py"
GATES = ("G0", "G1", "G2", "G3", "G4", "G5")
ORCHESTRATOR_PROFILES = ("benchmark", "production")
DEFAULT_ORCHESTRATOR_PROFILE = "benchmark"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
REDACTED = "[REDACTED]"

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


def load_model_catalog(path: Path) -> list[ModelSpec]:
    """Load and validate the versioned provider/model catalog."""

    raw = _read_json(path)
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


def _default_playwright_browsers_path() -> str | None:
    explicit = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if explicit:
        return explicit
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        return str(Path(local) / "ms-playwright") if local else None
    home = os.environ.get("HOME", "").strip()
    if sys.platform == "darwin":
        return str(Path(home) / "Library" / "Caches" / "ms-playwright") if home else None
    return str(Path(home) / ".cache" / "ms-playwright") if home else None


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
    browsers = _default_playwright_browsers_path()
    if browsers:
        env["PLAYWRIGHT_BROWSERS_PATH"] = browsers
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return _scrub_provider_keys(env)


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
            raise CostGuardError(
                "provider usage counter moved backward; cost attribution is unreliable"
            )
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
        env=env,
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


def _local_preflight() -> dict[str, Any]:
    node = _command_exists("node")
    npm = _command_exists("npm")
    if not SIDECAR.is_file():
        raise InfrastructureError(f"source sidecar is missing: {SIDECAR}")
    node_version = _run_command([node, "--version"], cwd=ROOT, timeout=15)
    npm_version = _run_command([npm, "--version"], cwd=ROOT, timeout=15)
    browser_probe = _run_command(
        [
            node,
            "-e",
            (
                "const {chromium}=require('playwright');"
                "(async()=>{const b=await chromium.launch({headless:true});"
                "const p=await b.newPage();await p.setContent('<title>ok</title>');"
                "if(await p.title()!=='ok')throw new Error('page probe failed');"
                "await b.close()})().catch(e=>{console.error(e.message);process.exit(2)})"
            ),
        ],
        cwd=ROOT,
        timeout=60,
    )
    result = {
        "python": sys.version.split()[0],
        "sidecar": str(SIDECAR.relative_to(ROOT)).replace("\\", "/"),
        "node": node_version,
        "npm": npm_version,
        "playwright_chromium": browser_probe,
    }
    if not all((node_version["ok"], npm_version["ok"], browser_probe["ok"])):
        raise InfrastructureError("local Node/npm/Playwright preflight failed")
    return result


def _backend_preflight(scenario: dict[str, Any], *, init_timeout: float) -> dict[str, Any]:
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
        env = _isolated_subprocess_env(base / "runtime-home")
        sidecar = SidecarClient(workspace, env, ())
        try:
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
            return {
                "ready": True,
                "protocol": capabilities.get("protocol"),
                "version": capabilities.get("version"),
                "required_commands": sorted(required),
                "signal_init_profile": scenario["profile"],
                "init_complete": True,
            }
        finally:
            sidecar.close()


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
        self.secrets = tuple(secrets)
        creationflags = 0
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(SIDECAR)],
            cwd=str(self.workspace),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            **kwargs,
        )
        self._stdout: queue.Queue[str | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=250)
        self._counter = 0
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        ready = self._wait_for("init", timeout=90, guard=None)
        if not ready or not ready[1] or ready[1].get("ok") is not True:
            self.terminate_tree()
            raise InfrastructureError("source sidecar did not produce a valid init handshake")
        data = ready[1].get("data") or {}
        if not isinstance(data, dict) or data.get("ready") is not True:
            self.terminate_tree()
            raise InfrastructureError("source sidecar init handshake did not report ready=true")

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
            self.cancel_and_stop(args.get("run_id") if isinstance(args, dict) else None)
            raise InfrastructureError(f"sidecar command {command} timed out or ended without a response")
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
        if self.proc.poll() is not None:
            return
        if os.name == "nt":
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                    shell=False,
                )
        else:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=5)
            if self.proc.poll() is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        with contextlib.suppress(Exception):
            self.proc.kill()
        with contextlib.suppress(Exception):
            self.proc.wait(timeout=5)

    def close(self) -> None:
        # Terminate the owned tree while the parent PID is still live; closing
        # stdin first can let a fast sidecar exit and orphan a background child
        # before Windows taskkill has a tree root to target.
        self.terminate_tree()
        with contextlib.suppress(Exception):
            if self.proc.stdin:
                self.proc.stdin.close()

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


def _require_ok(command: str, terminal: dict[str, Any]) -> dict[str, Any]:
    if terminal.get("ok") is not True:
        raise ProductFailure(f"{command} failed: {terminal.get('error') or 'unknown sidecar error'}")
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
) -> dict[str, Any]:
    _lazy_signalos_path()
    from signalos_lib.artifacts import resolve_gate_artifacts

    paths = resolve_gate_artifacts(workspace, gate, project_id="default")
    combined: list[str] = []
    present: list[str] = []
    for artifact in paths:
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
        reason = (state.get("last_outcome") or {}).get("reason")
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


def _secret_scan(workspace: Path, exact_secret: str) -> dict[str, Any]:
    exact = exact_secret.encode("utf-8")
    hits: list[dict[str, str]] = []
    skipped = {".git", "node_modules"}
    for path in _owned_tree_files(workspace):
        with contextlib.suppress(OSError):
            rel = path.relative_to(workspace)
            if any(part in skipped for part in rel.parts) or not path.is_file():
                continue
            size = path.stat().st_size
            kind = "exact-selected-key" if exact and _file_contains_bytes(path, exact) else ""
            if not kind:
                # Generic shape matching is diagnostic and intentionally skips
                # dependency/cache content where package documentation often
                # contains fake key examples.  Exact selected-key matching
                # above still scans every byte of every owned regular file.
                generic_excluded = {"cache", ".cache", "npm", "pip"}
                if size > 20 * 1024 * 1024 or any(part.lower() in generic_excluded for part in rel.parts):
                    continue
                data = path.read_bytes()
                text = data.decode("utf-8", errors="ignore")
                if any(pattern.search(text) for pattern in SECRET_SHAPES):
                    kind = "provider-key-shaped-text"
            if kind:
                hits.append({"path": rel.as_posix(), "kind": kind})
    return {"ok": not hits, "hits": hits}


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


def _clean_room_acceptance(
    workspace: Path,
    clean_room: Path,
    scenario_path: Path,
    *,
    timeout: float,
    secrets: Iterable[str],
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

    npm = _command_exists("npm")
    node = _command_exists("node")
    clean_env = _isolated_subprocess_env(
        clean_room.parent / "clean-runtime-home",
        extra={"CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"},
    )
    commands["npm_ci"] = _run_command(
        [npm, "ci", "--no-audit", "--no-fund"],
        cwd=clean_room,
        env=clean_env,
        timeout=timeout,
        secrets=secrets,
    )
    if not commands["npm_ci"]["ok"]:
        raise ProductFailure("clean-room npm ci failed")
    commands["npm_test"] = _run_command(
        [npm, "test"], cwd=clean_room, env=clean_env, timeout=timeout, secrets=secrets
    )
    if not commands["npm_test"]["ok"]:
        raise ProductFailure("clean-room generated-product tests failed")

    dist = clean_room / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    build_started_ns = time.time_ns()
    commands["npm_build"] = _run_command(
        [npm, "run", "build"], cwd=clean_room, env=clean_env, timeout=timeout, secrets=secrets
    )
    index = dist / "index.html"
    if not commands["npm_build"]["ok"] or not index.is_file():
        raise ProductFailure("clean-room production build failed or produced no dist/index.html")
    if index.stat().st_mtime_ns + 2_000_000_000 < build_started_ns:
        raise ProductFailure("dist/index.html is not fresh evidence from the clean-room build")

    scenario = _read_json(scenario_path)
    oracle_rel = str(scenario.get("oracle") or "") if isinstance(scenario, dict) else ""
    oracle = scenario_path.parent.parent / oracle_rel
    if not oracle.is_file():
        raise InfrastructureError(f"scenario oracle is missing: {oracle}")
    evidence_path = clean_room.parent / "oracle-evidence.json"
    artifacts_path = clean_room.parent / "oracle-artifacts"
    oracle_result = _run_command(
        [
            node,
            str(oracle),
            "--dist",
            str(dist),
            "--evidence",
            str(evidence_path),
            "--artifacts",
            str(artifacts_path),
        ],
        cwd=ROOT,
        env=clean_env,
        timeout=timeout,
        secrets=secrets,
    )
    commands["browser_oracle"] = oracle_result
    oracle_evidence: dict[str, Any] = {}
    if evidence_path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            loaded = json.loads(evidence_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                oracle_evidence = loaded
    evidence["oracle_evidence"] = oracle_evidence
    if oracle_result.get("returncode") == 2:
        raise InfrastructureError("browser oracle infrastructure failed")
    if not oracle_result["ok"]:
        raise ProductFailure("browser oracle rejected the generated product")
    if oracle_evidence.get("status") not in ("pass", "passed"):
        raise ProductFailure("browser oracle did not write an explicit passing evidence status")
    evidence["clean_tree"] = _snapshot_product_tree(clean_room)
    return evidence


def _engine_metadata() -> dict[str, Any]:
    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20, check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else "unknown"

    status = git("status", "--porcelain")
    commit = git("rev-parse", "HEAD")
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    upstream_commit = git("rev-parse", "@{upstream}")
    pushed = False
    if commit != "unknown" and upstream != "unknown":
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "@{upstream}"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20, check=False,
        )
        pushed = ancestry.returncode == 0
    return {
        "commit": commit,
        "tree": git("rev-parse", "HEAD^{tree}"),
        "branch": git("branch", "--show-current"),
        "upstream": upstream,
        "upstream_commit": upstream_commit,
        "pushed": pushed,
        "dirty": bool(status),
        "dirty_paths": [line[3:] for line in status.splitlines() if len(line) > 3],
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }


def _require_reproducible_engine(
    engine: dict[str, Any], *, live: bool, verified_ci_sha: str | None = None
) -> None:
    """Paid evidence must map to one reconstructable committed code tree."""
    if not live:
        return
    if engine.get("commit") in (None, "", "unknown") or engine.get("tree") in (
        None,
        "",
        "unknown",
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
    if not engine.get("pushed") or engine.get("upstream") in (None, "", "unknown"):
        raise InfrastructureError(
            "live matrix requires HEAD to be pushed to its configured upstream"
        )
    commit = str(engine.get("commit") or "").lower()
    attested = str(verified_ci_sha or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", attested):
        raise InfrastructureError(
            "live matrix requires --ci-verified-sha (or SIGNALOS_CI_VERIFIED_SHA) "
            "for the exact green commit"
        )
    if attested != commit:
        raise InfrastructureError(
            "CI-verified SHA does not match the current engine HEAD"
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


def _require_engine_unchanged(
    expected: dict[str, Any], *, verified_ci_sha: str
) -> dict[str, Any]:
    """Re-check the paid engine boundary before and after every model row."""

    current = _engine_metadata()
    _require_reproducible_engine(
        current, live=True, verified_ci_sha=verified_ci_sha
    )
    if current.get("commit") != expected.get("commit") or current.get("tree") != expected.get("tree"):
        raise InfrastructureError(
            "engine commit/tree changed during the funded matrix; aborting"
        )
    return current


def _load_scenario(path: Path) -> dict[str, Any]:
    raw = _read_json(path)
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
    scenario_path: Path,
    row_dir: Path,
    *,
    key: str,
    key_source: str,
    router: OpenRouterClient,
    cost_cap: float,
    init_timeout: float,
    gate_timeout: float,
    command_timeout: float,
    orchestrator_profile: str,
    engine: dict[str, Any],
    hashes: dict[str, str],
    provider_model_metadata: dict[str, Any],
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
    _safe_json_write(result_path, row, (key,))

    sidecar_env = _isolated_subprocess_env(
        row_dir / "runtime-home",
        extra={
            spec.key_env: key,
            "SIGNALOS_LLM_PROVIDER": spec.provider,
            "SIGNALOS_LLM_MODEL": spec.model,
        },
    )
    # _isolated_subprocess_env removes every provider key after applying extra;
    # add back only the one credential selected for this row.
    sidecar_env[spec.key_env] = key
    sidecar: SidecarClient | None = None
    cost: CostGuard | None = None
    try:
        baseline = _snapshot_product_tree(workspace)
        sidecar = SidecarClient(workspace, sidecar_env, (key,))

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
                command, terminal, events, time.monotonic() - started, (key,)
            )
            row["calls"].append(evidence)
            _safe_json_write(result_path, row, (key,))
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
            row_dir / "release-origin.git",
            env=sidecar_env,
        )
        _safe_json_write(result_path, row, (key,))
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
            _validate_review_checkpoint(
                state, run_id=run_id, gate=gate, signed_before=signed_before
            )
            gate_evidence: dict[str, Any] = {
                "gate": gate,
                "checkpoint_status": state.get("status"),
                "last_outcome": state.get("last_outcome"),
                "signed_before": signed_before,
            }
            if gate in trace_gates:
                trace = _gate_requirement_trace(
                    workspace, gate, scenario["requirement_ids"]
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

            verdict = invoke(
                "agent:verdict",
                {
                    "run_id": run_id,
                    "gate_id": gate,
                    "verdict": "approve",
                    "feedback": "",
                },
                gate_timeout,
            )
            verdict_data = _require_ok("agent:verdict", verdict)
            strict = _strict_gate(workspace, gate)
            gate_evidence["strict_signature"] = strict
            gate_evidence["verdict_result"] = verdict_data
            row["gates"].append(gate_evidence)
            _safe_json_write(result_path, row, (key,))
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
            env=sidecar_env,
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

        scan = _secret_scan(release_checkout, key)
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
            scenario_path,
            timeout=command_timeout,
            secrets=(key,),
            evidence=acceptance,
        )
        final_spent = cost.check(force=True)
        row["provider_usage"] = {
            "start": cost.started_usage,
            "end": cost.last_usage,
            "spent": final_spent,
            "cap": cost_cap,
        }
        row["status"] = "pass"
    except ProductFailure as exc:
        row["status"] = "fail"
        row["failure_type"] = "product"
        row["error"] = str(redact(str(exc), (key,)))
    except (InfrastructureError, CostGuardError, ValueError) as exc:
        row["status"] = "error"
        row["failure_type"] = "infrastructure" if not isinstance(exc, CostGuardError) else "cost-guard"
        row["error"] = str(redact(str(exc), (key,)))
    except Exception as exc:  # Never make a row disappear without evidence.
        row["status"] = "error"
        row["failure_type"] = "unexpected-harness-error"
        row["error"] = str(redact(f"{type(exc).__name__}: {exc}", (key,)))
    finally:
        if sidecar is not None:
            row["sidecar_stderr_tail"] = sidecar.stderr_tail()
            sidecar.close()
        # Scan the entire retained row (workspace, isolated runtime home,
        # clean-room, and evidence), even after a partial failure.  This must
        # happen after the sidecar stops so it cannot write a secret after our
        # check.
        if row_dir.exists():
            final_scan = _secret_scan(row_dir, key)
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
                },
            )
        row["finished_at"] = _utc_now()
        _safe_json_write(result_path, row, (key,))
    return row


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
    parser.add_argument(
        "--ci-verified-sha",
        default=os.environ.get("SIGNALOS_CI_VERIFIED_SHA", ""),
        help="Exact 40-character HEAD SHA whose CI run was verified green.",
    )
    parser.add_argument("--max-cost-per-model", type=float, default=None, metavar="USD")
    parser.add_argument("--acknowledge-key-exposure", action="store_true")
    parser.add_argument("--init-timeout", type=float, default=240.0, metavar="SECONDS")
    parser.add_argument("--gate-timeout", type=float, default=1800.0, metavar="SECONDS")
    parser.add_argument("--command-timeout", type=float, default=900.0, metavar="SECONDS")
    parser.add_argument("--fail-fast", action="store_true")
    return parser


def _print_models(catalog: Sequence[ModelSpec]) -> None:
    print("alias\tcohort\tprovider\tmodel")
    for spec in catalog:
        print(f"{spec.alias}\t{spec.cohort}\t{spec.provider}\t{spec.model}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        catalog = load_model_catalog(args.models_config)
        if args.list_models:
            _print_models(catalog)
            return 0

        # Model selection deliberately precedes key lookup and all network or
        # subprocess work.  A typo must fail without spending or touching state.
        selected = select_models(catalog, args.models)
        if not args.preflight and not args.live:
            parser.error("choose --preflight or explicitly opt in to paid work with --live")
        if args.live:
            if not args.acknowledge_key_exposure:
                parser.error("--live requires --acknowledge-key-exposure; see the backend matrix README")
            if args.max_cost_per_model is None or args.max_cost_per_model <= 0:
                parser.error("--live requires a positive --max-cost-per-model USD cap")
        scenario_path = args.scenario.resolve()
        scenario = _load_scenario(scenario_path)
        orchestrator_profile = _validate_orchestrator_profile(args.orchestrator_profile)
        engine = _engine_metadata()
        _require_reproducible_engine(
            engine,
            live=bool(args.live),
            verified_ci_sha=args.ci_verified_sha,
        )
        output_root = (
            _require_external_output_root(args.output_root)
            if args.live
            else args.output_root.expanduser().resolve()
        )
        key, key_source = _resolve_api_key(selected[0], args.env_file)
        if any(spec.key_env != selected[0].key_env or spec.provider != selected[0].provider for spec in selected):
            raise ValueError("a single run may use only one provider and API key environment")
        router = OpenRouterClient(key)
        local = _local_preflight()
        backend = _backend_preflight(scenario, init_timeout=args.init_timeout)
        provider = _provider_preflight(
            router,
            selected,
            required_remaining=float(args.max_cost_per_model or 0.0) * len(selected),
            require_provider_limit=bool(args.live),
        )
        provider_stack = _provider_stack_preflight(selected, provider["models"])
        if args.preflight:
            print(
                json.dumps(
                    redact(
                        {
                            "status": "pass",
                            "mode": "preflight",
                            "key_source": key_source,
                            "local": local,
                            "backend": backend,
                            "provider": provider,
                            "provider_stack": provider_stack,
                            "orchestrator_profile": orchestrator_profile,
                        },
                        (key,),
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_root = output_root / f"{stamp}-{uuid.uuid4().hex[:8]}"
        run_root.mkdir(parents=True, exist_ok=False)
        oracle_path = scenario_path.parent.parent / str(scenario["oracle"])
        hashes = {
            "models_config_sha256": _sha256_file(args.models_config.resolve()),
            "scenario_sha256": _sha256_file(scenario_path),
            "oracle_sha256": _sha256_file(oracle_path),
            "driver_sha256": _sha256_file(Path(__file__).resolve()),
            "sidecar_sha256": _sha256_file(SIDECAR),
        }
        manifest: dict[str, Any] = {
            "schema": "signalos.backend-matrix.run.v1",
            "started_at": _utc_now(),
            "status": "running",
            "run_root": str(run_root),
            "scenario": scenario["id"],
            "stack_profile": scenario["profile"],
            "orchestrator_profile": orchestrator_profile,
            "models": [dataclasses.asdict(spec) for spec in selected],
            "engine": engine,
            "ci_attestation": {"verified_sha": args.ci_verified_sha.lower()},
            "hashes": hashes,
            "preflight": {
                "local": local,
                "backend": backend,
                "provider": provider,
                "provider_stack": provider_stack,
                "key_source": key_source,
            },
            "results": [],
        }
        manifest_path = run_root / "matrix-result.json"
        _safe_json_write(manifest_path, manifest, (key,))
        provider_models = {
            str(row["id"]): row for row in provider["models"]
        }
        for spec in selected:
            _require_engine_unchanged(
                engine, verified_ci_sha=args.ci_verified_sha
            )
            print(f"[{spec.alias}] starting fresh backend journey", flush=True)
            row = _run_row(
                spec,
                scenario,
                scenario_path,
                run_root / spec.alias,
                key=key,
                key_source=key_source,
                router=router,
                cost_cap=float(args.max_cost_per_model),
                init_timeout=args.init_timeout,
                gate_timeout=args.gate_timeout,
                command_timeout=args.command_timeout,
                orchestrator_profile=orchestrator_profile,
                engine=engine,
                hashes=hashes,
                provider_model_metadata=provider_models[spec.model],
            )
            _require_engine_unchanged(
                engine, verified_ci_sha=args.ci_verified_sha
            )
            manifest["results"].append(
                {
                    "alias": spec.alias,
                    "cohort": spec.cohort,
                    "model": spec.model,
                    "status": row.get("status"),
                    "failure_type": row.get("failure_type"),
                    "error": row.get("error"),
                    "result": str((run_root / spec.alias / "result.json").relative_to(run_root)),
                }
            )
            _safe_json_write(manifest_path, manifest, (key,))
            print(f"[{spec.alias}] {row.get('status')}", flush=True)
            if args.fail_fast and row.get("status") != "pass":
                break
        exit_code = results_exit_code(manifest["results"])
        manifest["status"] = "pass" if exit_code == 0 else "fail"
        manifest["finished_at"] = _utc_now()
        _safe_json_write(manifest_path, manifest, (key,))
        print(f"Result bundle: {manifest_path}")
        return exit_code
    except (ValueError, HarnessError, OSError) as exc:
        print(f"backend matrix error: {redact(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
