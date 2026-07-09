# signalos_lib/product/enforcement_state.py
# v4 Phase 2 — Governance authority interface.
#
# Architecture Decision Q2: Rust is the single source of truth for governance
# rules. Python reads the rule set ONCE at agent-loop start (not per call) and
# caches it for the loop's duration. The canonical fetch is the Rust IPC
# `enforcement::get_enforcement_state` + `ipc::validate_workspace_write`.
#
# This module abstracts that fetch behind a small interface so the agent loop
# and pytest never need a live Tauri runtime (the Rust commands cannot run in
# the sidecar's own process). The real wiring (RustEnforcementProvider) calls
# back into the Tauri host via the IPC server; CI uses StaticEnforcementProvider
# (a deterministic test double, INV-6).
#
# 12 runtime rules (NOT 13) — mirrors enforcement.rs RULE_* constants.
# INV-4: no silent failures. A failed fetch raises; the loop surfaces it.

from __future__ import annotations

__all__ = [
    "RUNTIME_RULES",
    "CORE_INVARIANTS_PY",
    "DEFAULT_TRUST_TIER_PATHS",
    "EnforcementState",
    "EnforcementProvider",
    "StaticEnforcementProvider",
    "FileEnforcementProvider",
    "load_trust_tier_paths",
    "seed_trust_tier_paths",
    "TRUST_TIER_PATHS_REL",
    "ENFORCEMENT_REL",
]

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# The 12 runtime rules, mirroring src-tauri/src/enforcement.rs RULE_* consts.
# This is the authoritative count the plan calls out: 12 rules, not 13.
RUNTIME_RULES: tuple[str, ...] = (
    "gate-gating",
    "plan-gating",
    "trust-tier",
    "audit-append",
    "secret-block",
    "role-sign",
    "stack-contract",
    "wave-freeze",
    "test-first",
    "gate-compliance",
    "zero-manual-regression",
    "mutation-threshold",
)

# Core invariants that may NEVER be disabled — mirrors enforcement.rs
# CORE_INVARIANTS *exactly* (see test_core_invariants_py_matches_rust, which
# fails if these two floors ever drift). Rust validates every mode before it
# hits disk; Python treats disk as untrusted and RE-APPLIES this floor on read
# so a hand-edited enforcement.json can never seed a core rule to "off".
CORE_INVARIANTS_PY: frozenset[str] = frozenset(
    {
        "gate-gating",
        "plan-gating",
        "trust-tier",
        "audit-append",
        "secret-block",
        "role-sign",
        "test-first",
        "gate-compliance",
    }
)

# Relative location of the per-tier path allowlist config, seeded by
# `signalos init` (see the plan's "Trust-tier path allowlists" decision).
TRUST_TIER_PATHS_REL = ".signalos/trust-tier-paths.json"

# Relative location of the persisted enforcement snapshot written by the Rust
# EnforcementStore (rule modes + wave_frozen). FileEnforcementProvider reads it.
ENFORCEMENT_REL = ".signalos/enforcement.json"

# Default per-tier allowlists. Typed (explicit strings) for forbidden_always,
# globs allowed for the per-tier write source dirs. Matches the plan verbatim.
DEFAULT_TRUST_TIER_PATHS: dict[str, Any] = {
    "T1": {
        "read": ["**"],
        "write": [],
        "execute": [],
    },
    "T2": {
        "read": ["**"],
        "write": [
            "core/governance/**",
            "core/strategy/**",
            "core/execution/**",
            "src/**",
            "public/**",
            "tests/**",
            "package.json",
            "tsconfig.json",
        ],
        "execute": [
            "npm install",
            "npm run build",
            "npm test",
            "npm run test",
            "npm run dev",
            # Verification runners (single-file test runs, type checks) across
            # the supported stacks. The build gate's per-task green loop tells
            # the agent to run exactly these; an allowlist that rejects them
            # forces the agent to work blind (observed: 77 trust-tier denials
            # of `npx vitest run <plan-test>` in one G4 walk). All are
            # non-destructive verification commands; forbidden_always still
            # applies on top.
            "npx vitest",
            "npx tsc",
            "npx vite",
            # Read-only shell idioms models reach for; denying them only burns
            # a turn re-routing to the equivalent tool (read allowlist is **
            # at T2 anyway, so these grant nothing new).
            "ls",
            "dir",
            "cat",
            "pwd",
            "head",
            "tail",
            "wc",
            "pytest",
            "python -m pytest",
            "go test",
            "cargo test",
            "dotnet test",
            "mvn test",
            "gradle test",
            "git status",
            "git diff",
            "git log",
        ],
    },
    "T3": {
        "read": ["**"],
        "write": ["**"],
        "execute": ["**"],
    },
    # Typed, explicit (not globs) for the always-forbidden set. These hold no
    # matter the trust tier — they are the governance/secret/destructive deny
    # list. Note ".signalos/" prefix handling lives in agent_loop's matcher.
    "forbidden_always": {
        "write": [
            ".signalos/AUDIT_TRAIL.jsonl",
            ".signalos/gates.json",
            ".env",
            ".env.local",
            "*.pem",
            "*.key",
        ],
        "execute": [
            "rm -rf",
            "git push --force",
            "git reset --hard",
            "npm publish",
            "docker push",
        ],
    },
}


@dataclass
class EnforcementState:
    """Snapshot of governance rules cached for one agent-loop run (Q2).

    Read ONCE at loop start. `trust_tier` is the active tier (T1/T2/T3) that
    selects the allowlists in trust-tier-paths.json. `rule_modes` maps each of
    the 12 runtime rules to its mode ("strict"|"warn"|"off"). `forbidden_paths`
    and `forbidden_actions` are the always-on deny lists.
    """

    trust_tier: str = "T2"
    rule_modes: dict[str, str] = field(default_factory=dict)
    forbidden_paths: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    wave_frozen: bool = False
    signed_gates: list[int] = field(default_factory=list)
    trust_tier_paths: dict[str, Any] = field(default_factory=dict)

    def rule_mode(self, rule: str) -> str:
        return self.rule_modes.get(rule, "strict")

    def rule_enabled(self, rule: str) -> bool:
        return self.rule_mode(rule) != "off"

    def tier_paths(self, kind: str) -> list[str]:
        """Allowlist for `kind` ("read"|"write"|"execute") at the active tier."""
        tier = self.trust_tier_paths.get(self.trust_tier, {})
        return list(tier.get(kind, []))


@runtime_checkable
class EnforcementProvider(Protocol):
    """Fetches the canonical enforcement state (Rust is the authority, Q2)."""

    def get_enforcement_state(self, repo_root: Path) -> EnforcementState:
        ...


class StaticEnforcementProvider:
    """Deterministic EnforcementProvider for CI (INV-6).

    Builds an EnforcementState from explicit args + the trust-tier-paths.json
    on disk (or DEFAULT_TRUST_TIER_PATHS). No Tauri, no network. The real
    RustEnforcementProvider (Phase 3, not in this stream) will call back to the
    Tauri host; tests inject this double.
    """

    def __init__(
        self,
        trust_tier: str = "T2",
        rule_modes: dict[str, str] | None = None,
        wave_frozen: bool = False,
        signed_gates: list[int] | None = None,
    ) -> None:
        self._trust_tier = trust_tier
        self._rule_modes = rule_modes or {r: "strict" for r in RUNTIME_RULES}
        self._wave_frozen = wave_frozen
        self._signed_gates = list(signed_gates or [])

    def get_enforcement_state(self, repo_root: Path) -> EnforcementState:
        tier_paths = load_trust_tier_paths(repo_root)
        forbidden = tier_paths.get("forbidden_always", {})
        return EnforcementState(
            trust_tier=self._trust_tier,
            rule_modes=dict(self._rule_modes),
            forbidden_paths=list(forbidden.get("write", [])),
            forbidden_actions=list(forbidden.get("execute", [])),
            wave_frozen=self._wave_frozen,
            signed_gates=list(self._signed_gates),
            trust_tier_paths=tier_paths,
        )


class FileEnforcementProvider:
    """Production EnforcementProvider — reads the Rust-persisted snapshot.

    The Rust EnforcementStore writes `.signalos/enforcement.json` on every
    mutation (rule modes + wave_frozen). This provider reads it back so the
    sidecar's agent loop enforces the same modes the user toggled in the app.

    Defense in depth (matches the plan's INVs):
      * Every mode was already validated by Rust before it hit disk, but Python
        treats disk as untrusted and RE-APPLIES the core-invariant floor: any
        core rule set to "off" on disk reads back as "strict".
      * A missing file defaults every runtime rule to strict (== the static
        provider's default), so absence never weakens.
      * A corrupt/unreadable file raises RuntimeError (INV-4: no silent
        fallback) — this fails safe, because a raise blocks the run rather than
        proceeding with an unknown policy.
    """

    def get_enforcement_state(self, repo_root: Path) -> EnforcementState:
        repo_root = Path(repo_root)
        tier_paths = load_trust_tier_paths(repo_root)
        forbidden = tier_paths.get("forbidden_always", {})

        modes: dict[str, str] = {r: "strict" for r in RUNTIME_RULES}
        wave_frozen = False

        path = repo_root / ENFORCEMENT_REL
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"enforcement.json is unreadable at {path}: {exc}. "
                    "Fix or delete it; the run is blocked until the policy is legible."
                ) from exc
            if not isinstance(raw, dict):
                raise RuntimeError(
                    f"enforcement.json at {path} must be a JSON object."
                )
            file_modes = raw.get("rule_modes", {})
            if isinstance(file_modes, dict):
                for rule, mode in file_modes.items():
                    if rule in RUNTIME_RULES and mode in ("strict", "warn", "off"):
                        modes[rule] = mode
            wave_frozen = bool(raw.get("wave_frozen", False))

        # Re-apply the floor: a core invariant can be "warn" but never "off".
        for rule in CORE_INVARIANTS_PY:
            if modes.get(rule) == "off":
                modes[rule] = "strict"

        return EnforcementState(
            trust_tier="T2",
            rule_modes=modes,
            forbidden_paths=list(forbidden.get("write", [])),
            forbidden_actions=list(forbidden.get("execute", [])),
            wave_frozen=wave_frozen,
            trust_tier_paths=tier_paths,
        )


def load_trust_tier_paths(repo_root: Path) -> dict[str, Any]:
    """Load .signalos/trust-tier-paths.json, falling back to the defaults.

    INV-4: a present-but-corrupt config raises rather than silently using
    defaults — a broken allowlist must be visible, not swallowed.
    """
    path = repo_root / TRUST_TIER_PATHS_REL
    if not path.is_file():
        return json.loads(json.dumps(DEFAULT_TRUST_TIER_PATHS))  # deep copy
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"trust-tier-paths.json is unreadable at {path}: {exc}. "
            "Fix or delete it to fall back to defaults."
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(
            f"trust-tier-paths.json at {path} must be a JSON object."
        )
    # Always ensure forbidden_always present (defense in depth).
    if "forbidden_always" not in data:
        data["forbidden_always"] = json.loads(
            json.dumps(DEFAULT_TRUST_TIER_PATHS["forbidden_always"])
        )
    return data


def seed_trust_tier_paths(repo_root: Path, force: bool = False) -> Path:
    """Write the default trust-tier-paths.json (seeded by `signalos init`).

    Returns the path. No-op if it already exists unless force=True.
    """
    path = repo_root / TRUST_TIER_PATHS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return path
    path.write_text(
        json.dumps(DEFAULT_TRUST_TIER_PATHS, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
