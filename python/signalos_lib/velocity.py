"""
cli/signalos_lib/velocity.py — SignalOS Velocity Primitives (AMD-CORE-032)
AutoPlan task generation, checkpoint save/restore, doc drift detection.
No runtime third-party dependencies.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "AutoPlanTask", "autoplan", "autoplan_load",
    "CheckpointEntry", "checkpoint_save", "checkpoint_list", "checkpoint_restore",
    "detect_doc_drift", "DocDriftEntry", "check_velocity_wired",
    "CHECKPOINT_INDEX_RELATIVE",
    # Phase 13 — wave-velocity metrics (sessions/day, scope burndown, ETA)
    "WAVE_SESSION_START_ACTIONS",
    "compute_wave_velocity",
    "compute_sessions_per_day",
    "compute_scope_card_burndown",
    "compute_eta_days",
    "iter_audit_entries",
]

CHECKPOINT_INDEX_RELATIVE = ".signalos/checkpoints/index.jsonl"


# ---------------------------------------------------------------------------
# AutoPlan
# ---------------------------------------------------------------------------

@dataclass
class AutoPlanTask:
    id: str
    title: str
    description: str
    wave: str
    tier: str
    depends_on: list = field(default_factory=list)
    effort_days: float = 0.5
    status: str = "pending"

    def as_dict(self) -> dict:
        return asdict(self)


def autoplan(feature_description: str, wave: str, repo_root: Path) -> list[AutoPlanTask]:
    """Parse feature_description into structured AutoPlanTask list, save to YAML, return list."""
    lines = feature_description.splitlines()
    # Filter empty lines and lines that are only bullets/whitespace
    stripped_lines: list[str] = []
    for line in lines:
        cleaned = line.lstrip("- *•\t ")
        if cleaned:
            stripped_lines.append(cleaned)

    tasks: list[AutoPlanTask] = []
    if not stripped_lines:
        tasks.append(AutoPlanTask(
            id="task-001",
            title="Implement feature",
            description=feature_description[:80],
            wave=wave,
            tier="T2",
            effort_days=0.5,
            status="pending",
        ))
    else:
        for i, line in enumerate(stripped_lines):
            tasks.append(AutoPlanTask(
                id=f"task-{i + 1:03d}",
                title=line[:80],
                description=line,
                wave=wave,
                tier="T2",
                effort_days=0.5,
                status="pending",
            ))

    # Save to YAML-like file
    plan_path = repo_root / f".signalos/plans/autoplan-{wave}.yaml"
    plan_path.parent.mkdir(parents=True, exist_ok=True)

    lines_out: list[str] = []
    for task in tasks:
        lines_out.append("---")
        lines_out.append(f"id: {task.id}")
        lines_out.append(f"title: {task.title}")
        lines_out.append(f"description: {task.description}")
        lines_out.append(f"wave: \"{task.wave}\"")
        lines_out.append(f"tier: {task.tier}")
        lines_out.append(f"effort_days: {task.effort_days}")
        lines_out.append(f"status: {task.status}")
        lines_out.append("")

    plan_path.write_text("\n".join(lines_out), encoding="utf-8")
    return tasks


def autoplan_load(wave: str, repo_root: Path) -> list[AutoPlanTask]:
    """Load tasks from .signalos/plans/autoplan-<wave>.yaml. Returns [] if missing."""
    plan_path = repo_root / f".signalos/plans/autoplan-{wave}.yaml"
    if not plan_path.exists():
        return []

    content = plan_path.read_text(encoding="utf-8")
    # Split on --- separators
    blocks = content.split("---")
    tasks: list[AutoPlanTask] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        data: dict[str, Any] = {}
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                data[key.strip()] = value.strip().strip('"')

        if "id" not in data:
            continue

        task = AutoPlanTask(
            id=data.get("id", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            wave=data.get("wave", wave),
            tier=data.get("tier", "T2"),
            effort_days=float(data.get("effort_days", 0.5)),
            status=data.get("status", "pending"),
        )
        tasks.append(task)

    return tasks


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

@dataclass
class CheckpointEntry:
    id: str
    wave: str
    label: str
    context_path: str
    ts: str
    note: str

    def as_dict(self) -> dict:
        return asdict(self)


def _next_checkpoint_id(repo_root: Path) -> str:
    """Read checkpoint index and return next ckpt-NNN id."""
    index_path = repo_root / CHECKPOINT_INDEX_RELATIVE
    if not index_path.exists():
        return "ckpt-001"

    highest = 0
    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            ckpt_id = record.get("id", "")
            if ckpt_id.startswith("ckpt-"):
                num = int(ckpt_id[5:])
                if num > highest:
                    highest = num
        except (json.JSONDecodeError, ValueError):
            continue

    return f"ckpt-{highest + 1:03d}"


def _append_checkpoint(repo_root: Path, record: dict) -> None:
    """Append a JSON record to the checkpoint index."""
    index_path = repo_root / CHECKPOINT_INDEX_RELATIVE
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def checkpoint_save(
    repo_root: Path,
    wave: str,
    label: str,
    context_path: str,
    note: str = "",
) -> CheckpointEntry:
    """Save a checkpoint entry and copy/create the context file."""
    ckpt_id = _next_checkpoint_id(repo_root)
    ts = str(int(time.time()))

    entry = CheckpointEntry(
        id=ckpt_id,
        wave=wave,
        label=label,
        context_path=context_path,
        ts=ts,
        note=note,
    )

    _append_checkpoint(repo_root, entry.as_dict())

    dest = repo_root / f".signalos/checkpoints/{ckpt_id}/context.md"
    dest.parent.mkdir(parents=True, exist_ok=True)

    src = Path(context_path)
    if not src.is_absolute():
        src = repo_root / context_path
    if src.is_file():
        shutil.copy2(src, dest)
    else:
        dest.write_text(f"# Checkpoint {ckpt_id}\n{note}", encoding="utf-8")

    return entry


def checkpoint_list(
    repo_root: Path, wave: Optional[str] = None
) -> list[CheckpointEntry]:
    """List checkpoints, optionally filtered by wave."""
    index_path = repo_root / CHECKPOINT_INDEX_RELATIVE
    if not index_path.exists():
        return []

    entries: list[CheckpointEntry] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        entry = CheckpointEntry(
            id=data.get("id", ""),
            wave=data.get("wave", ""),
            label=data.get("label", ""),
            context_path=data.get("context_path", ""),
            ts=data.get("ts", ""),
            note=data.get("note", ""),
        )
        if wave is None or entry.wave == wave:
            entries.append(entry)

    return entries


def checkpoint_restore(
    repo_root: Path, checkpoint_id: str, output_path: Path
) -> bool:
    """Restore a checkpoint context.md to output_path. Returns True if found."""
    src = repo_root / f".signalos/checkpoints/{checkpoint_id}/context.md"
    if not src.exists():
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, output_path)
    return True


# ---------------------------------------------------------------------------
# Doc Drift
# ---------------------------------------------------------------------------

@dataclass
class DocDriftEntry:
    file: str
    status: str  # fresh | stale | missing
    last_modified: float
    note: str

    def as_dict(self) -> dict:
        return asdict(self)


def detect_doc_drift(
    repo_root: Path,
    docs_dir: str = "docs",
    max_age_days: float = 30.0,
) -> list[DocDriftEntry]:
    """Scan docs directory for stale markdown files."""
    full_path = repo_root / docs_dir

    if not full_path.exists():
        return [DocDriftEntry(
            file=docs_dir,
            status="missing",
            last_modified=0.0,
            note="docs directory not found",
        )]

    md_files = list(full_path.glob("*.md"))
    if not md_files:
        return [DocDriftEntry(
            file=str(docs_dir),
            status="missing",
            last_modified=0.0,
            note="no markdown files found",
        )]

    entries: list[DocDriftEntry] = []
    now = time.time()
    for path in md_files:
        mtime = path.stat().st_mtime
        age_days = (now - mtime) / 86400
        status = "fresh" if age_days <= max_age_days else "stale"
        entries.append(DocDriftEntry(
            file=str(path.relative_to(repo_root)),
            status=status,
            last_modified=mtime,
            note=f"age: {age_days:.1f} days",
        ))

    return sorted(entries, key=lambda e: e.last_modified)


# ---------------------------------------------------------------------------
# Wiring check
# ---------------------------------------------------------------------------

def check_velocity_wired(repo_root: Path) -> tuple[bool, str]:
    """C17: both signal-autoplan.md and signal-context-restore.md must exist."""
    base = repo_root / "core/execution/commands"
    required = ["signal-autoplan.md", "signal-context-restore.md"]
    missing = [name for name in required if not (base / name).exists()]
    if missing:
        return False, f"missing W11 command specs: {', '.join(missing)}"
    return True, "velocity primitives wired"


# ---------------------------------------------------------------------------
# Phase 13 — Wave-velocity metrics (sessions/day, burndown, ETA prediction)
# ---------------------------------------------------------------------------
#
# Derived view over existing state. No new persistence is written by these
# helpers — they read .signalos/AUDIT_TRAIL.jsonl + the existing
# wave_engine state file(s) + the existing autoplan tasks (scope cards) and
# return a plain dict suitable for JSON emission to the desktop sidebar.
#
# Per Phase 13 of docs/SIGNALOS_FACTORY_GOVERNANCE_IMPLEMENTATION_PLAN.md:
#   sessions_per_day      — float, count of session-start events / 14 days
#   scope_card_burndown   — list[{wave, total, completed}] per autoplan wave
#   eta_days              — float | None, predicted days to clear remaining
#   last_session_at       — ISO timestamp | null
#
# Everything fails closed: missing audit trail / missing wave-state /
# malformed audit line all return zero / null values rather than raising.

import datetime as _datetime

# Audit-trail action strings that mark the start of a new working session
# on a wave. The wave engine emits "wave:begin" from begin(); historical
# audit lines may also use these synonymous markers when older versions
# of the engine wrote them. Treat any of them as a session-start signal.
WAVE_SESSION_START_ACTIONS: frozenset[str] = frozenset({
    "wave:begin",
    "wave-begin",
    "wave:start",
    "wave-start",
    "session:start",
    "session-start",
})


def iter_audit_entries(repo_root: Path):
    """Yield parsed JSON dicts from `.signalos/AUDIT_TRAIL.jsonl`.

    Silently skips:
      - missing trail file
      - blank lines
      - lines that are not valid JSON
      - lines that decode to non-dict values

    The audit trail is append-only and may contain partially-written tail
    bytes during a crash; we never raise on a bad line so the dashboard
    keeps rendering.
    """
    trail = repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not trail.is_file():
        return
    try:
        text = trail.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            yield parsed


def _parse_audit_timestamp(value) -> Optional[_datetime.datetime]:
    """Return a UTC-aware datetime for an audit-trail `ts` field, or None.

    Accepts:
      - ISO-8601 strings (with or without trailing 'Z')
      - integer / float epoch seconds (sign.py older entries)
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return _datetime.datetime.fromtimestamp(float(value), tz=_datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # fromisoformat() in stdlib doesn't accept the literal 'Z' until 3.11+.
    # Normalise to '+00:00' which works everywhere we support.
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return parsed


def compute_sessions_per_day(
    repo_root: Path,
    *,
    window_days: int = 14,
    now: Optional[_datetime.datetime] = None,
) -> tuple[float, Optional[str]]:
    """Return (sessions_per_day, last_session_iso) over the last *window_days*.

    Counts audit-trail entries whose `action` is in
    `WAVE_SESSION_START_ACTIONS` and whose timestamp falls inside the
    rolling window ending at *now* (UTC). Returns (0.0, None) when there
    are no qualifying entries.
    """
    if window_days <= 0:
        window_days = 1
    if now is None:
        now = _datetime.datetime.now(tz=_datetime.timezone.utc)
    cutoff = now - _datetime.timedelta(days=window_days)

    count = 0
    last_seen: Optional[_datetime.datetime] = None
    for entry in iter_audit_entries(repo_root):
        action = entry.get("action")
        if action not in WAVE_SESSION_START_ACTIONS:
            continue
        ts = _parse_audit_timestamp(entry.get("ts"))
        if ts is None:
            continue
        if last_seen is None or ts > last_seen:
            last_seen = ts
        if ts >= cutoff and ts <= now:
            count += 1

    per_day = count / float(window_days) if count else 0.0
    last_iso = last_seen.astimezone(_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if last_seen else None
    return per_day, last_iso


def _enumerate_autoplan_waves(repo_root: Path) -> list[str]:
    """Return the wave labels we have autoplan data for, sorted."""
    plans_dir = repo_root / ".signalos" / "plans"
    if not plans_dir.is_dir():
        return []
    waves: list[str] = []
    for child in plans_dir.iterdir():
        name = child.name
        if not name.startswith("autoplan-") or not name.endswith(".yaml"):
            continue
        label = name[len("autoplan-"):-len(".yaml")]
        if label:
            waves.append(label)
    waves.sort()
    return waves


def compute_scope_card_burndown(repo_root: Path) -> list[dict]:
    """Return per-wave scope-card totals + completion counts.

    Each entry is ``{"wave": str, "total": int, "completed": int}``.
    A task counts as completed when its `status` field is one of
    {"done", "completed", "signed", "shipped"} — matching the
    common autoplan vocabulary.
    """
    completed_states = {"done", "completed", "signed", "shipped"}
    burndown: list[dict] = []
    for wave in _enumerate_autoplan_waves(repo_root):
        try:
            tasks = autoplan_load(wave, repo_root)
        except Exception:
            continue
        total = len(tasks)
        if total == 0:
            continue
        completed = sum(1 for t in tasks if (t.status or "").lower() in completed_states)
        burndown.append({
            "wave": wave,
            "total": total,
            "completed": completed,
        })
    return burndown


def compute_eta_days(
    *,
    sessions_per_day: float,
    burndown: list[dict],
) -> Optional[float]:
    """Predict days to clear remaining scope cards given recent velocity.

    Returns None when there is not enough data to compute a meaningful
    estimate (zero velocity, no open cards, or negative inputs).
    Velocity is approximated as ``sessions_per_day`` cards burned per day
    — a single working session typically retires one scope card in the
    SignalOS wave model.
    """
    if sessions_per_day <= 0:
        return None
    remaining = 0
    for row in burndown:
        try:
            total = int(row.get("total", 0))
            completed = int(row.get("completed", 0))
        except (TypeError, ValueError):
            continue
        if total <= 0:
            continue
        remaining += max(0, total - completed)
    if remaining <= 0:
        return None
    return round(remaining / sessions_per_day, 2)


def compute_wave_velocity(
    repo_root: Path,
    *,
    window_days: int = 14,
    now: Optional[_datetime.datetime] = None,
) -> dict:
    """Top-level metrics payload for the dashboard sidebar.

    Returns a JSON-serialisable dict with:
      sessions_per_day      float
      scope_card_burndown   list[{wave, total, completed}]
      eta_days              float | None
      last_session_at       str | None  (ISO-8601 UTC, trailing 'Z')
      window_days           int  (echo the input — frontend tooltip)
      generated_at          str  (ISO-8601 UTC)
    """
    if now is None:
        now = _datetime.datetime.now(tz=_datetime.timezone.utc)
    sessions_per_day, last_session = compute_sessions_per_day(
        repo_root, window_days=window_days, now=now,
    )
    burndown = compute_scope_card_burndown(repo_root)
    eta = compute_eta_days(sessions_per_day=sessions_per_day, burndown=burndown)
    return {
        "sessions_per_day": round(sessions_per_day, 4),
        "scope_card_burndown": burndown,
        "eta_days": eta,
        "last_session_at": last_session,
        "window_days": window_days,
        "generated_at": now.astimezone(_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
