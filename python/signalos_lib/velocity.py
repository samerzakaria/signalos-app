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
