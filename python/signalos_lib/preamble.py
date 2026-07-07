"""
cli/signalos_lib/preamble.py — Session preamble resolver (AMD-CORE-037, W16).

Reads `integrations/rules/signalos-preamble.mdc` (the template) and substitutes
the `{{VAR}}` placeholders with values pulled from local governance artifacts
plus session-specific overrides supplied by the caller.

The resolver runs from the `session-start` hook on every new session, before
the agent attaches. Output is written to `.signalos/session-preamble.md`
(per-machine runtime, gitignored). The session-start hook then prepends this
resolved file to the agent's context window via `brain-session-inject.sh`.

Design constraints:
- Stdlib-only. No new runtime Python deps.
- Graceful degradation: a missing source file substitutes a clearly-marked
  placeholder ("(no constitution)", "—", etc.) rather than failing.
- Session-specific vars (SCOPE, OUTPUTS, END_RULE, etc.) accept caller
  overrides and fall back to "(set by orchestrator)" when not provided.
- Idempotent: same inputs → same output.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional


__all__ = [
    "resolve_preamble",
    "write_resolved_preamble",
    "PREAMBLE_TEMPLATE_RELATIVE",
    "RESOLVED_OUTPUT_RELATIVE",
]

PREAMBLE_TEMPLATE_RELATIVE = "integrations/rules/signalos-preamble.mdc"
# IMPORTANT: this path is also written by `core/execution/hooks/session-start`
# as its legacy minimal-preamble output. The resolver runs AFTER that emit and
# OVERWRITES the file with the full resolved template, so `brain-session-inject.sh
# --preamble` (which reads this same path) sees the substituted values, not the
# literal {{...}} or the minimal placeholder. Two writers, one canonical path.
RESOLVED_OUTPUT_RELATIVE = ".signalos-session-preamble.md"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_preamble(
    repo_root: Path,
    *,
    session_type: str = "session",
    agent_name: str = "agent",
    agent_owner: str = "PE",
    trust_tier: str = "T2",
    task_surface: str = "core/execution/",
    wave_id: Optional[str] = None,
    scope: Optional[str] = None,
    out_of_scope: Optional[str] = None,
    inputs: Optional[str] = None,
    outputs: Optional[str] = None,
    end_rule: Optional[str] = None,
    embedded_gates: Optional[str] = None,
    template_text: Optional[str] = None,
) -> str:
    """Resolve the preamble template against local artifacts + session args.

    Returns the resolved markdown text. Caller decides where to write it
    (production: write_resolved_preamble; tests: capture the string).
    """
    if template_text is None:
        template_path = repo_root / PREAMBLE_TEMPLATE_RELATIVE
        if not template_path.is_file():
            return ""
        template_text = template_path.read_text(encoding="utf-8")

    vars_resolved: dict[str, str] = {
        # Product / framework identity (stable across the session)
        "PRODUCT_NAME": _read_product_name(repo_root),
        "CONSTITUTION_HASH": _constitution_hash(repo_root),
        "SCALE_TRACK": _read_soul_field(repo_root, "scale_track") or "wave",
        "DELIVERY_MODE": _read_soul_field(repo_root, "delivery_mode") or "fresh-wave",

        # Wave / session attachment (from PLAN or caller override)
        "WAVE_ID": wave_id or _wave_id_from_plan(repo_root),

        # Agent identity (from caller; orchestrator knows who's running)
        "AGENT_NAME": agent_name,
        "AGENT_OWNER": agent_owner,
        "SESSION_TYPE": session_type,

        # Trust + surface (from caller; bounded by Trust Tier sheet)
        "TRUST_TIER": trust_tier,
        "TASK_SURFACE": task_surface,
        "TIER_CONSTRAINT_LINE": _tier_constraint_line(trust_tier),
        "TRUST_TIER_TABLE": _trust_tier_table_pointer(repo_root),

        # Belief context (read from BELIEF.md if present)
        "BELIEF_SUMMARY": _read_belief_first_line(repo_root) or "(belief not yet seeded)",
        "DISPROOF_CONDITION": _read_belief_field(repo_root, "Disproof") or "(disproof not yet seeded)",

        # Session-specific (orchestrator fills these per session; default placeholders)
        "SCOPE": scope or "(set by orchestrator)",
        "OUT_OF_SCOPE": out_of_scope or "(set by orchestrator)",
        "INPUTS": inputs or "(set by orchestrator)",
        "OUTPUTS": outputs or "(set by orchestrator)",
        "END_RULE": end_rule or "(set by orchestrator)",
        "EMBEDDED_GATES": embedded_gates or "(set by orchestrator)",
    }

    return _substitute(template_text, vars_resolved)


def write_resolved_preamble(
    repo_root: Path,
    resolved: str,
) -> Path:
    """Write *resolved* to .signalos/session-preamble.md and return its path.

    Creates the parent directory if missing. Idempotent — overwrites any
    prior session's preamble with the current one.
    """
    out_path = repo_root / RESOLVED_OUTPUT_RELATIVE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(resolved, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Substitution engine
# ---------------------------------------------------------------------------

_VAR_PATTERN = re.compile(r"\{\{([A-Z_][A-Z_0-9]*)\}\}")


def _substitute(text: str, vars: dict[str, str]) -> str:
    """Replace every {{KEY}} with vars[KEY]. Unknown keys → '—'."""
    def repl(m: "re.Match[str]") -> str:
        return vars.get(m.group(1), "—")
    return _VAR_PATTERN.sub(repl, text)


# ---------------------------------------------------------------------------
# Source readers — graceful degradation everywhere
# ---------------------------------------------------------------------------

def _constitution_hash(repo_root: Path) -> str:
    p = repo_root / "core/governance/Governance/CONSTITUTION.md"
    if not p.is_file():
        return "(no constitution)"
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    except OSError:
        return "(constitution read error)"


def _read_product_name(repo_root: Path) -> str:
    """Look in (1) .signalos/config.json, (2) Soul Document, (3) repo dir name."""
    cfg = repo_root / ".signalos/config.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            name = data.get("product_name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except (json.JSONDecodeError, OSError):
            pass
    soul_name = _read_soul_field(repo_root, "product_name")
    if soul_name:
        return soul_name
    return repo_root.resolve().name


def _read_soul_field(repo_root: Path, field: str) -> str:
    """Read `field: value` from SOUL-DOCUMENT.md front-matter or first match."""
    soul = repo_root / "core/governance/Governance/SOUL-DOCUMENT.md"
    if not soul.is_file():
        return ""
    pattern = re.compile(rf"(?im)^{re.escape(field)}\s*:\s*(\S.*?)\s*$")
    try:
        text = soul.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        m = pattern.match(line)
        if m:
            # Strip surrounding double or single quotes (YAML may quote values).
            return m.group(1).strip().strip('"').strip("'")
    return ""


def _wave_id_from_plan(repo_root: Path, project_id: str = "default") -> str:
    """Read `wave: <id>` from PLAN.tasks.yaml (top-level YAML scalar).

    Default project keeps the historical core/execution/PLAN.tasks.yaml
    location; any other id resolves through projects.project_plan_path
    (.signalos/projects/<id>/PLAN.tasks.yaml) per §3.2.
    """
    if project_id == "default":
        plan = repo_root / "core/execution/PLAN.tasks.yaml"
    else:
        from signalos_lib.projects import project_plan_path

        plan = project_plan_path(repo_root, project_id)
    if not plan.is_file():
        return "(no current wave)"
    try:
        for line in plan.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^wave:\s*(\S+)", line.strip())
            if m:
                return m.group(1).strip().strip('"').strip("'")
    except OSError:
        pass
    return "(no current wave)"


def _read_belief_first_line(repo_root: Path) -> str:
    """First non-empty line after a Problem heading.

    Accepts these heading variants (all case-insensitive, optional trailing colon):
      - `# Problem`, `## Problem`, `### Problem`, `## Problem:`
      - `**Problem**`, `**Problem:**`
      - `_Problem_`, `Problem:` (line by itself)
    """
    for p in [
        repo_root / "core/strategy/BELIEF.md",
        repo_root / "core/strategy/BELIEF_LITE.md",
    ]:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        in_problem = False
        for line in text.splitlines():
            s = line.strip()
            if _matches_section_heading(s, "Problem"):
                in_problem = True
                continue
            if in_problem and s and not s.startswith("#"):
                clean = re.sub(r"\*{1,2}|_{1,2}|`", "", s)
                return clean[:200]
    return ""


def _read_belief_field(repo_root: Path, heading: str) -> str:
    """First non-empty line under a section labeled by *heading*.

    Accepts the same heading variants as `_read_belief_first_line` (markdown
    headings 1-3, bold markers, bold-with-colon, bare-with-colon).
    """
    for p in [
        repo_root / "core/strategy/BELIEF.md",
        repo_root / "core/strategy/BELIEF_LITE.md",
    ]:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        in_section = False
        for line in text.splitlines():
            s = line.strip()
            if _matches_section_heading(s, heading):
                in_section = True
                continue
            if in_section and s and not s.startswith("#"):
                return re.sub(r"\*{1,2}|_{1,2}|`", "", s)[:200]
    return ""


def _matches_section_heading(line: str, heading: str) -> bool:
    """Return True if *line* is a section heading matching *heading*.

    Recognises (case-insensitive):
      - `# Heading`, `## Heading`, `### Heading`, `## Heading:`
      - `**Heading**`, `**Heading:**`, `__Heading__`, `__Heading:__`
      - `Heading:` (bare label)
    """
    h = re.escape(heading)
    pattern = re.compile(
        rf"(?i)^"
        rf"(?:#{{1,3}}\s+{h}\s*:?\s*"          # markdown heading 1-3, optional colon
        rf"|\*\*{h}\s*:?\*\*\s*:?\s*"          # **Heading** / **Heading:** / **Heading:**:
        rf"|__{h}\s*:?__\s*:?\s*"              # __Heading__ variants
        rf"|{h}\s*:\s*"                         # bare `Heading:` label
        rf")$"
    )
    return bool(pattern.match(line))


def _trust_tier_table_pointer(repo_root: Path) -> str:
    """Return a pointer to TRUST_TIER.md or a default explanation."""
    p = repo_root / "core/execution/TRUST_TIER.md"
    if p.is_file():
        return f"(see {p.relative_to(repo_root)})"
    return "(see core/execution/TRUST_TIER.md — not yet seeded; defaults: T1 read-only, T2 bounded writes, T3 high-trust two-actor)"


def _tier_constraint_line(tier: str) -> str:
    """One-liner Trust Tier constraint reminder for the agent."""
    constraints = {
        "T1": "T1: read-only. No writes outside .signalos/.",
        "T2": "T2: bounded writes per Surface Inventory.",
        "T3": "T3: high-trust surface. Two-actor sign-off required for any write.",
    }
    return constraints.get(tier, f"{tier}: see core/execution/TRUST_TIER.md.")
