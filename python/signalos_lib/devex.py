"""
cli/signalos_lib/devex.py — SignalOS DevEx + Global Retro (AMD-CORE-034) / W13.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

__all__ = [
    "DEVEX_PLANS_RELATIVE",
    "DEVEX_METRICS_RELATIVE",
    "VALID_MODES",
    "DevExPlan",
    "DevExMetric",
    "devex_plan",
    "devex_measure",
    "devex_list",
    "devex_plan_list",
    "retro_global",
    "check_devex_wired",
    "_next_id",
    "_append_record",
    "_mode_items",
]

DEVEX_PLANS_RELATIVE = ".signalos/devex/plans.jsonl"
DEVEX_METRICS_RELATIVE = ".signalos/devex/metrics.jsonl"
VALID_MODES = ("EXPANSION", "POLISH", "TRIAGE")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DevExPlan:
    id: str          # "dxplan-001"
    mode: str        # "EXPANSION" | "POLISH" | "TRIAGE"
    wave: str
    ts: str          # ISO-8601
    items: list      # list of str focus areas generated from mode

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DevExMetric:
    id: str          # "dxm-001"
    metric: str      # e.g. "TTHW"
    value_seconds: float
    wave: str
    ts: str
    note: str

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _next_id(index_path: Path, prefix: str) -> str:
    """Read JSONL, find highest N in {prefix}-NNN IDs, return next. Never raises."""
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return f"{prefix}-001"

    ids: list[int] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            raw = d.get("id", "")
            if raw.startswith(f"{prefix}-"):
                suffix = raw[len(prefix) + 1:]
                ids.append(int(suffix))
        except json.JSONDecodeError:
            continue
        except ValueError:
            continue

    nxt = max(ids, default=0) + 1
    return f"{prefix}-{nxt:03d}"


def _append_record(index_path: Path, record: dict) -> None:
    """Create parent dirs and append JSON line to index."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _mode_items(mode: str) -> list[str]:
    """Return focus items based on mode."""
    if mode == "EXPANSION":
        return ["New onboarding flow", "API discoverability", "Quickstart guide update"]
    if mode == "POLISH":
        return ["Error message clarity", "CLI output formatting", "Docs consistency pass"]
    if mode == "TRIAGE":
        return ["Blocking bug review", "P0 friction audit", "Hotfix pipeline check"]
    return [f"Custom mode: {mode}"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def devex_plan(repo_root: Path, mode: str, wave: str) -> DevExPlan:
    """Create a DevEx plan, save to plans JSONL, and return it."""
    mode = mode.upper()
    plans_path = repo_root / DEVEX_PLANS_RELATIVE
    plan_id = _next_id(plans_path, "dxplan")
    items = _mode_items(mode)
    plan = DevExPlan(
        id=plan_id,
        mode=mode,
        wave=wave,
        ts=_iso_now(),
        items=items,
    )
    _append_record(plans_path, plan.as_dict())
    return plan


def devex_measure(
    repo_root: Path,
    metric: str,
    value_seconds: float,
    wave: str,
    note: str = "",
) -> DevExMetric:
    """Create and save a DevEx metric. Returns the created metric."""
    metrics_path = repo_root / DEVEX_METRICS_RELATIVE
    metric_id = _next_id(metrics_path, "dxm")
    entry = DevExMetric(
        id=metric_id,
        metric=metric,
        value_seconds=value_seconds,
        wave=wave,
        ts=_iso_now(),
        note=note,
    )
    _append_record(metrics_path, entry.as_dict())
    return entry


def devex_list(repo_root: Path, wave: Optional[str] = None) -> list[DevExMetric]:
    """Read metrics JSONL, filter by wave if given. Defensive against errors."""
    metrics_path = repo_root / DEVEX_METRICS_RELATIVE
    try:
        lines = metrics_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    results: list[DevExMetric] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            entry = DevExMetric(
                id=d["id"],
                metric=d["metric"],
                value_seconds=float(d["value_seconds"]),
                wave=d["wave"],
                ts=d["ts"],
                note=d["note"],
            )
            if wave is None or entry.wave == wave:
                results.append(entry)
        except json.JSONDecodeError:
            continue
        except KeyError:
            continue
        except ValueError:
            continue

    return results


def devex_plan_list(repo_root: Path, wave: Optional[str] = None) -> list[DevExPlan]:
    """Read plans JSONL, filter by wave if given. Defensive against errors."""
    plans_path = repo_root / DEVEX_PLANS_RELATIVE
    try:
        lines = plans_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    results: list[DevExPlan] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            entry = DevExPlan(
                id=d["id"],
                mode=d["mode"],
                wave=d["wave"],
                ts=d["ts"],
                items=d["items"],
            )
            if wave is None or entry.wave == wave:
                results.append(entry)
        except json.JSONDecodeError:
            continue
        except KeyError:
            continue

    return results


def retro_global(repo_root: Path, query: str, wave: str) -> list[dict]:
    """Query brain index for cross-wave retrospective insights.

    Reads .signalos/brain/index.jsonl directly (no brain module import).
    Returns entries whose 'content' field contains query (case-insensitive).
    Falls back to [] if brain index is absent or has errors.
    """
    brain_path = repo_root / ".signalos" / "brain" / "index.jsonl"
    if not brain_path.exists():
        return []

    try:
        lines = brain_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    results: list[dict] = []
    query_lower = query.lower()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            content = d.get("content", "")
            if query_lower in content.lower():
                results.append(d)
        except json.JSONDecodeError:
            continue

    return results


def check_devex_wired(repo_root: Path) -> tuple[bool, str]:
    """C19: checks that devex lib and command specs all exist."""
    required = [
        repo_root / "cli" / "signalos_lib" / "devex.py",
        repo_root / "core" / "execution" / "commands" / "signal-devex-plan.md",
        repo_root / "core" / "execution" / "commands" / "signal-devex.md",
        repo_root / "core" / "execution" / "commands" / "signal-retro-global.md",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, "devex wired"
