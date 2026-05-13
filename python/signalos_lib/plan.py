# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/plan.py
# W3.4 — Machine-readable task schema library (AMD-CORE-017)
#
# Manages PLAN.tasks.yaml: a ULID-keyed, dependency-aware, typed task list
# that is the authoritative task source for the orchestrator.
# PLAN.md is a *rendered view* generated from PLAN.tasks.yaml.

from __future__ import annotations

__all__ = [
    "VALID_STATUSES",
    "VALID_TIERS",
    "Task",
    "PlanDoc",
    "load_tasks",
    "validate_tasks",
    "render_plan_md",
    "dump_tasks",
    "make_ulid",
]

import json
import math
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STATUSES = frozenset({"pending", "in_progress", "done", "blocked", "skipped"})
VALID_TIERS = frozenset({"T1", "T2", "T3"})

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "core" / "execution" / "plan" / "PLAN_SCHEMA.json"

# Crockford base32 alphabet (upper-case, no I/L/O/U)
_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# ---------------------------------------------------------------------------
# ULID generation
# ---------------------------------------------------------------------------

def make_ulid() -> str:
    """Return a 26-character ULID (timestamp 10 + randomness 16, Crockford base32)."""
    ms = int(time.time() * 1000)
    # Encode 48-bit timestamp into 10 base32 chars
    ts_chars: list[str] = []
    for _ in range(10):
        ts_chars.append(_B32[ms & 0x1F])
        ms >>= 5
    ts_part = "".join(reversed(ts_chars))
    # Encode 80 bits of randomness into 16 base32 chars
    rand_val = random.getrandbits(80)
    rand_chars: list[str] = []
    for _ in range(16):
        rand_chars.append(_B32[rand_val & 0x1F])
        rand_val >>= 5
    rand_part = "".join(reversed(rand_chars))
    return ts_part + rand_part


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """One task entry in PLAN.tasks.yaml."""

    id: str
    title: str
    status: str
    tier: str
    owner: str = ""
    depends_on: list[str] = field(default_factory=list)
    effort_days: float = 1.0
    prompt_file: str = ""
    wave: str = ""
    branch: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for YAML round-trip."""
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "tier": self.tier,
        }
        if self.owner:
            d["owner"] = self.owner
        if self.depends_on:
            d["depends_on"] = list(self.depends_on)
        if self.effort_days != 1.0:
            d["effort_days"] = self.effort_days
        if self.prompt_file:
            d["prompt_file"] = self.prompt_file
        if self.wave:
            d["wave"] = self.wave
        if self.branch:
            d["branch"] = self.branch
        if self.notes:
            d["notes"] = self.notes
        return d

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Task":
        """Deserialise from a dict (e.g. from PyYAML load)."""
        return Task(
            id=str(raw.get("id", "")),
            title=str(raw.get("title", "")),
            status=str(raw.get("status", "pending")),
            tier=str(raw.get("tier", "T3")),
            owner=str(raw.get("owner", "")),
            depends_on=[str(x) for x in raw.get("depends_on", [])],
            effort_days=float(raw.get("effort_days", 1.0)),
            prompt_file=str(raw.get("prompt_file", "")),
            wave=str(raw.get("wave", "")),
            branch=str(raw.get("branch", "")),
            notes=str(raw.get("notes", "")),
        )


@dataclass
class PlanDoc:
    """Top-level container parsed from PLAN.tasks.yaml."""

    wave: str
    tasks: list[Task]
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"wave": self.wave, "tasks": [t.to_dict() for t in self.tasks]}
        if self.generated_at:
            d["generated_at"] = self.generated_at
        return d


# ---------------------------------------------------------------------------
# Load / dump
# ---------------------------------------------------------------------------

def load_tasks(path: str | Path) -> PlanDoc:
    """Load and return a :class:`PlanDoc` from a YAML file.

    Raises :class:`FileNotFoundError` if *path* does not exist.
    Raises :class:`ValueError` if the top-level structure is wrong.
    """
    try:
        import yaml  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("PyYAML is required: pip install pyyaml") from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"plan file not found: {p}")

    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"PLAN.tasks.yaml must be a YAML mapping, got {type(raw).__name__}")

    wave = str(raw.get("wave", ""))
    if not wave:
        raise ValueError("PLAN.tasks.yaml must have a non-empty 'wave' key")

    raw_tasks = raw.get("tasks", [])
    if not isinstance(raw_tasks, list):
        raise ValueError(f"'tasks' must be a list, got {type(raw_tasks).__name__}")

    tasks = [Task.from_dict(t) for t in raw_tasks if isinstance(t, dict)]
    return PlanDoc(
        wave=wave,
        tasks=tasks,
        generated_at=str(raw.get("generated_at", "")),
    )


def dump_tasks(doc: PlanDoc, path: str | Path) -> None:
    """Serialise *doc* to YAML at *path*."""
    try:
        import yaml  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("PyYAML is required: pip install pyyaml") from exc

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    # Build ordered YAML with a comment header
    header = (
        "# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.\n"
        "# SignalOS Core — PLAN.tasks.yaml  (machine-readable task list, AMD-CORE-017)\n"
        "# Edit this file to manage tasks; run `signalos plan render` to regenerate PLAN.md.\n"
        "#\n"
    )
    body = yaml.dump(
        doc.to_dict(),
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    p.write_text(header + body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    """Raised when a plan document fails validation."""


def validate_tasks(doc: PlanDoc) -> list[str]:
    """Validate *doc* and return a list of error strings (empty = valid).

    Checks:
    * Every task has a non-empty id matching the ULID pattern.
    * IDs are unique.
    * status in VALID_STATUSES.
    * tier in VALID_TIERS.
    * depends_on references exist.
    * No dependency cycles (simple DFS).
    """
    errors: list[str] = []
    ulid_re = re.compile(r"^[0-9A-Z]{26}$")
    seen_ids: dict[str, int] = {}  # id → first index

    for i, task in enumerate(doc.tasks):
        label = f"task[{i}] {task.id!r}"

        # --- id ---
        if not task.id:
            errors.append(f"{label}: 'id' is empty")
        elif not ulid_re.match(task.id):
            errors.append(f"{label}: 'id' does not match ULID pattern [0-9A-Z]{{26}}")
        if task.id in seen_ids:
            errors.append(f"{label}: duplicate id (first seen at index {seen_ids[task.id]})")
        else:
            seen_ids[task.id] = i

        # --- title ---
        if not task.title.strip():
            errors.append(f"{label}: 'title' is empty")

        # --- status ---
        if task.status not in VALID_STATUSES:
            errors.append(
                f"{label}: invalid status {task.status!r} "
                f"(valid: {sorted(VALID_STATUSES)})"
            )

        # --- tier ---
        if task.tier not in VALID_TIERS:
            errors.append(
                f"{label}: invalid tier {task.tier!r} (valid: T1, T2, T3)"
            )

        # --- effort_days ---
        if not math.isfinite(task.effort_days) or task.effort_days < 0:
            errors.append(f"{label}: effort_days must be a non-negative finite number")

    # --- depends_on references ---
    all_ids = set(seen_ids)
    for i, task in enumerate(doc.tasks):
        label = f"task[{i}] {task.id!r}"
        for dep in task.depends_on:
            if dep not in all_ids:
                errors.append(f"{label}: depends_on {dep!r} not found in task list")

    # --- cycle detection (DFS) ---
    if not errors:
        adj: dict[str, list[str]] = {t.id: list(t.depends_on) for t in doc.tasks}
        WHITE, GREY, BLACK = 0, 1, 2
        color: dict[str, int] = {tid: WHITE for tid in adj}

        def dfs(node: str) -> bool:
            color[node] = GREY
            for nbr in adj.get(node, []):
                if color[nbr] == GREY:
                    return True  # cycle
                if color[nbr] == WHITE and dfs(nbr):
                    return True
            color[node] = BLACK
            return False

        for tid in list(adj):
            if color[tid] == WHITE:
                if dfs(tid):
                    errors.append(f"dependency cycle detected involving task {tid!r}")
                    break

    return errors


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

_STATUS_ICON = {
    "done": "✓",
    "in_progress": "►",
    "blocked": "✗",
    "skipped": "–",
    "pending": "○",
}

_TIER_LABEL = {"T1": "T1 (auto)", "T2": "T2 (pause)", "T3": "T3 (human)"}


def render_plan_md(doc: PlanDoc) -> str:
    """Return a Markdown string rendered from *doc*.

    Structure:
      # Wave <id> — Task Plan
      Generated metadata line
      Summary table (total / done / in_progress / pending / blocked / skipped)
      Per-task sections with dependency list
    """
    import datetime

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    # ── Header ──────────────────────────────────────────────────────────────
    lines.append(f"# Wave {doc.wave} — Task Plan\n")
    lines.append(f"> Generated {now} from PLAN.tasks.yaml by `signalos plan render`.\n")
    lines.append("> **Do not edit this file directly** — edit PLAN.tasks.yaml instead.\n")
    lines.append("")

    # ── Summary table ───────────────────────────────────────────────────────
    total = len(doc.tasks)
    counts: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    for t in doc.tasks:
        counts[t.status] = counts.get(t.status, 0) + 1

    done = counts.get("done", 0)
    pct = f"{done / total * 100:.0f}%" if total else "—"
    lines.append("## Summary\n")
    lines.append(f"| Total | Done | In Progress | Pending | Blocked | Skipped | Progress |")
    lines.append(f"|-------|------|-------------|---------|---------|---------|----------|")
    lines.append(
        f"| {total} | {counts['done']} | {counts['in_progress']} "
        f"| {counts['pending']} | {counts['blocked']} | {counts['skipped']} | {pct} |"
    )
    lines.append("")

    # ── Task list ────────────────────────────────────────────────────────────
    lines.append("## Tasks\n")

    id_to_title: dict[str, str] = {t.id: t.title for t in doc.tasks}

    for task in doc.tasks:
        icon = _STATUS_ICON.get(task.status, "?")
        tier_label = _TIER_LABEL.get(task.tier, task.tier)
        effort = f"{task.effort_days:g}d"

        lines.append(f"### {icon} {task.title}\n")
        lines.append(f"| Field | Value |")
        lines.append(f"|-------|-------|")
        lines.append(f"| ID | `{task.id}` |")
        lines.append(f"| Status | {task.status} |")
        lines.append(f"| Tier | {tier_label} |")
        if task.owner:
            lines.append(f"| Owner | {task.owner} |")
        lines.append(f"| Effort | {effort} |")
        if task.wave:
            lines.append(f"| Wave | {task.wave} |")
        if task.branch:
            lines.append(f"| Branch | `{task.branch}` |")
        if task.prompt_file:
            lines.append(f"| Prompt | `{task.prompt_file}` |")

        if task.depends_on:
            dep_strs = []
            for dep_id in task.depends_on:
                dep_title = id_to_title.get(dep_id, "?")
                dep_strs.append(f"`{dep_id}` ({dep_title})")
            lines.append(f"| Depends on | {' · '.join(dep_strs)} |")

        lines.append("")
        if task.notes:
            lines.append(f"> {task.notes}\n")
            lines.append("")

    return "\n".join(lines) + "\n"
