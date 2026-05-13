"""cli/signalos_lib/safety.py — SignalOS Safety Gates (AMD-CORE-035) / W14."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "CAREFUL_FLAG_RELATIVE",
    "FREEZE_DIR_RELATIVE",
    "CarefulRecord",
    "FreezeRecord",
    "careful_enable",
    "careful_disable",
    "careful_status",
    "freeze_dir",
    "guard_check",
    "unfreeze_dir",
    "check_safety_wired",
    "_target_hash",
    "_next_freeze_id",
]

CAREFUL_FLAG_RELATIVE = ".signalos/safety/careful.flag"
FREEZE_DIR_RELATIVE = ".signalos/safety/freeze"


@dataclass
class CarefulRecord:
    active: bool
    ts: str      # ISO-8601 when set, empty string when cleared
    note: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FreezeRecord:
    id: str          # "freeze-001"
    target: str      # directory path string (relative or absolute)
    wave: str
    ts: str          # ISO-8601
    note: str
    status: str      # "frozen" | "unfrozen"

    def as_dict(self) -> dict:
        return asdict(self)


def _iso_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    t = time.gmtime()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", t)


def _target_hash(target: str) -> str:
    """Short stable hash for freeze file naming."""
    return hashlib.md5(target.encode()).hexdigest()[:8]


def _next_freeze_id(repo_root: Path) -> str:
    """Scan freeze dir for highest N in freeze-NNN IDs, return next."""
    freeze_dir_path = repo_root / FREEZE_DIR_RELATIVE
    try:
        files = list(freeze_dir_path.glob("*.json"))
    except OSError:
        return "freeze-001"

    highest = 0
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            fid = data.get("id", "")
            if not fid:
                continue
            parts = fid.split("-")
            if len(parts) != 2:
                continue
            n = int(parts[1])
            if n > highest:
                highest = n
        except (json.JSONDecodeError, ValueError):
            continue

    return f"freeze-{highest + 1:03d}"


def careful_enable(repo_root: Path, note: str = "") -> CarefulRecord:
    """Write careful.flag and return CarefulRecord."""
    flag_path = repo_root / CAREFUL_FLAG_RELATIVE
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    ts = _iso_now()
    data = {"active": True, "ts": ts, "note": note}
    flag_path.write_text(json.dumps(data), encoding="utf-8")
    return CarefulRecord(active=True, ts=ts, note=note)


def careful_disable(repo_root: Path) -> CarefulRecord:
    """Remove the careful flag file if present."""
    flag_path = repo_root / CAREFUL_FLAG_RELATIVE
    flag_path.unlink(missing_ok=True)
    return CarefulRecord(active=False, ts="", note="")


def careful_status(repo_root: Path) -> CarefulRecord:
    """Read flag file if exists, return CarefulRecord."""
    flag_path = repo_root / CAREFUL_FLAG_RELATIVE
    try:
        data = json.loads(flag_path.read_text(encoding="utf-8"))
        return CarefulRecord(
            active=bool(data.get("active", False)),
            ts=data.get("ts", ""),
            note=data.get("note", ""),
        )
    except (OSError, json.JSONDecodeError):
        return CarefulRecord(active=False, ts="", note="")


def freeze_dir(repo_root: Path, target: str, wave: str, note: str = "") -> FreezeRecord:
    """Create freeze record for target directory."""
    freeze_dir_path = repo_root / FREEZE_DIR_RELATIVE
    freeze_dir_path.mkdir(parents=True, exist_ok=True)

    fid = _next_freeze_id(repo_root)
    ts = _iso_now()
    record = FreezeRecord(
        id=fid,
        target=target,
        wave=wave,
        ts=ts,
        note=note,
        status="frozen",
    )

    h = _target_hash(target)
    record_path = freeze_dir_path / f"{h}.json"
    record_path.write_text(json.dumps(record.as_dict()), encoding="utf-8")
    return record


def guard_check(repo_root: Path, target: str) -> dict:
    """Check if a directory is currently frozen.

    Direct lookup via _target_hash — freeze_dir writes to a deterministic
    path `<hash>.json`, so we read that one file instead of scanning the
    directory.
    """
    record_path = repo_root / FREEZE_DIR_RELATIVE / f"{_target_hash(target)}.json"
    if not record_path.exists():
        return {"target": target, "frozen": False, "id": None}
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
        if data.get("target") == target and data.get("status") == "frozen":
            return {"target": target, "frozen": True, "id": data.get("id")}
    except (OSError, json.JSONDecodeError):
        pass  # corrupt or unreadable — treat as not frozen
    return {"target": target, "frozen": False, "id": None}


def unfreeze_dir(repo_root: Path, target: str) -> bool:
    """Update freeze file for target to status='unfrozen'. Returns True if found.

    Direct lookup via _target_hash — same rationale as guard_check.
    """
    record_path = repo_root / FREEZE_DIR_RELATIVE / f"{_target_hash(target)}.json"
    if not record_path.exists():
        return False
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
        if data.get("target") == target:
            data["status"] = "unfrozen"
            record_path.write_text(json.dumps(data), encoding="utf-8")
            return True
    except (OSError, json.JSONDecodeError):
        return False
    return False


def check_safety_wired(repo_root: Path) -> tuple[bool, str]:
    """C20: check that safety lib and key command specs exist."""
    required = [
        "cli/signalos_lib/safety.py",
        "core/execution/commands/signal-careful.md",
        "core/execution/commands/signal-freeze.md",
    ]
    missing = [p for p in required if not (repo_root / p).exists()]
    if missing:
        return (False, "missing: " + ", ".join(missing))
    return (True, "safety wired")
