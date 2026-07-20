"""Credential-safe Git subprocess policy for backend-owned workspaces.

Every backend Git invocation must pass through this module.  Normal desktop
work keeps the user's Git configuration but never forwards model-provider or
dependency-attestation secrets.  Funded matrix work is stricter: Git receives
only a small operating-system allowlist, all hooks/config/credential helpers
are disabled, and release remotes are restricted to one driver-attested local
filesystem path.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


FUNDED_PROFILE_ENV = "SIGNALOS_SANDBOX_PROFILE"
EXPECTED_REMOTE_ENV = "SIGNALOS_FUNDED_EXPECTED_GIT_REMOTE"
DISABLED_HOOKS_ENV = "SIGNALOS_FUNDED_GIT_HOOKS_DIR"

_PROVIDER_KEYS = frozenset({
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
})
_BACKEND_SENSITIVE_EXACT = _PROVIDER_KEYS | frozenset({
    "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY",
})
_FUNDED_CREDENTIAL_EXACT = frozenset({
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "BITBUCKET_TOKEN",
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
})
_SENSITIVE_PREFIXES = (
    "SIGNALOS_LLM_",
    "SIGNALOS_DEPENDENCY_ATTESTATION_",
)
_FUNDED_ENV_ALLOWLIST = frozenset({
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PROCESSOR_ARCHITECTURE",
    "NUMBER_OF_PROCESSORS",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "APPDATA",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "TZ",
})
_FUNDED_EXTRA_ENV = frozenset({
    "GIT_INDEX_FILE",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_COMMITTER_DATE",
    # Read-only lock avoidance for release-tree queries (ls-files/ls-tree). This
    # only tells git to skip the OPTIONAL index-refresh lock; it grants no network
    # or filesystem capability, so it is safe under funded hardening. Suspected
    # cause of a multi-minute host-git block during a loaded (Windows) funded run.
    "GIT_OPTIONAL_LOCKS",
})


class GitProcessPolicyError(RuntimeError):
    """A Git process would cross the funded execution boundary."""


def is_funded_git_mode(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get(FUNDED_PROFILE_ENV) or "").strip().lower() == "funded"


def _is_sensitive_name(name: str, *, funded: bool) -> bool:
    upper = str(name).upper()
    return (
        upper in _BACKEND_SENSITIVE_EXACT
        or (funded and upper in _FUNDED_CREDENTIAL_EXACT)
        or any(
        upper.startswith(prefix) for prefix in _SENSITIVE_PREFIXES
        )
    )


def _validated_hooks_dir(
    source: Mapping[str, str], workspace: Path,
) -> Path:
    raw = str(source.get(DISABLED_HOOKS_ENV) or "").strip()
    if not raw:
        raise GitProcessPolicyError(
            "funded Git requires a driver-owned disabled-hooks directory"
        )
    path = Path(raw)
    if not path.is_absolute():
        raise GitProcessPolicyError(
            "funded Git disabled-hooks directory must be absolute"
        )
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GitProcessPolicyError(
            "funded Git disabled-hooks directory is unavailable"
        ) from exc
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    root = Path(workspace).resolve()
    lexical = os.path.normcase(os.path.abspath(str(path)))
    canonical = os.path.normcase(os.path.abspath(str(resolved)))
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or attributes & 0x0400
        or lexical != canonical
        or resolved == root
        or root in resolved.parents
    ):
        raise GitProcessPolicyError(
            "funded Git disabled-hooks directory is not independently owned"
        )
    try:
        if any(resolved.iterdir()):
            raise GitProcessPolicyError(
                "funded Git disabled-hooks directory is not empty"
            )
    except OSError as exc:
        raise GitProcessPolicyError(
            "funded Git disabled-hooks directory cannot be inspected"
        ) from exc
    return resolved


def _child_environment(
    source: Mapping[str, str],
    *,
    funded: bool,
    extra: Mapping[str, str] | None,
) -> dict[str, str]:
    if funded:
        child = {
            name: str(value)
            for name, value in source.items()
            if name.upper() in _FUNDED_ENV_ALLOWLIST
            and not _is_sensitive_name(name, funded=True)
        }
    else:
        child = {
            str(name): str(value)
            for name, value in source.items()
            if not _is_sensitive_name(name, funded=False)
        }
    for name, value in (extra or {}).items():
        upper = str(name).upper()
        if _is_sensitive_name(upper, funded=funded):
            raise GitProcessPolicyError(
                "a sensitive environment variable cannot be added to Git"
            )
        if funded and upper not in _FUNDED_EXTRA_ENV:
            raise GitProcessPolicyError(
                f"funded Git does not allow extra environment variable {upper}"
            )
        child[str(name)] = str(value)
    if funded:
        # Ignore system/global config, prompts, credential managers, and any
        # inherited GIT_* process controls.  Per-command -c values below still
        # govern the local repository safely.
        child.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
        })
    return child


def run_git(
    args: Sequence[str],
    *,
    cwd: Path,
    runner: Callable[..., Any] | None = None,
    env: Mapping[str, str] | None = None,
    extra_env: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    """Run Git with credential scrubbing and funded hardening."""

    source = dict(os.environ if env is None else env)
    funded = is_funded_git_mode(source)
    argv = ["git"]
    if funded:
        hooks = _validated_hooks_dir(source, Path(cwd))
        argv.extend([
            "-c", f"core.hooksPath={hooks}",
            "-c", "credential.helper=",
            "-c", "core.askPass=",
            "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false",
            "-c", "protocol.allow=never",
            "-c", "protocol.file.allow=always",
        ])
    argv.extend(str(value) for value in args)
    child_env = _child_environment(source, funded=funded, extra=extra_env)
    invoke = subprocess.run if runner is None else runner
    return invoke(argv, cwd=str(Path(cwd)), env=child_env, **kwargs)


def funded_expected_remote(
    workspace: Path,
    *,
    environ: Mapping[str, str] | None = None,
    require_exists: bool = True,
) -> Path | None:
    """Return the one permitted funded file remote, or ``None`` normally."""

    source = os.environ if environ is None else environ
    if not is_funded_git_mode(source):
        return None
    raw = str(source.get(EXPECTED_REMOTE_ENV) or "").strip()
    if not raw:
        raise GitProcessPolicyError(
            "funded Git release requires an expected local remote"
        )
    candidate = Path(raw)
    if not candidate.is_absolute() or "://" in raw:
        raise GitProcessPolicyError(
            "funded Git expected remote must be an absolute filesystem path"
        )
    root = Path(workspace).resolve()
    try:
        resolved = candidate.resolve(strict=require_exists)
    except OSError as exc:
        raise GitProcessPolicyError(
            "funded Git expected remote is unavailable"
        ) from exc
    if resolved == root or root in resolved.parents or resolved in root.parents:
        raise GitProcessPolicyError(
            "funded Git expected remote must be independently owned"
        )
    if require_exists:
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise GitProcessPolicyError(
                "funded Git expected remote is unavailable"
            ) from exc
        attributes = int(getattr(info, "st_file_attributes", 0) or 0)
        lexical = os.path.normcase(os.path.abspath(str(candidate)))
        canonical = os.path.normcase(os.path.abspath(str(resolved)))
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or attributes & 0x0400
            or lexical != canonical
        ):
            raise GitProcessPolicyError(
                "funded Git expected remote is not a regular directory"
            )
    return resolved


def configured_remote_matches_expected(
    configured: str,
    expected: Path,
) -> bool:
    raw = str(configured or "").strip()
    if not raw or "://" in raw:
        return False
    candidate = Path(raw)
    if not candidate.is_absolute():
        return False
    try:
        return candidate.resolve(strict=True) == Path(expected).resolve(strict=True)
    except OSError:
        return False
