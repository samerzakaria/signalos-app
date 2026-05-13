"""
cli/signalos_lib/investigate.py — SignalOS Iron-Law Debugging Protocol (AMD-CORE-036) / W15.

Five iron laws enforced:
  1. Reproduce first — no hypothesis without a confirmed reproduction step.
  2. One variable at a time — each test changes exactly one thing.
  3. Log everything — every action is recorded in INVESTIGATION.md.
  4. No assumption without evidence — claims must cite observations.
  5. Write regression before fix — test must fail before patch is applied.

Produces INVESTIGATION.md with hypothesis / evidence / conclusion sections.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

__all__ = [
    "INVESTIGATION_INDEX_RELATIVE",
    "INVESTIGATION_DOCS_RELATIVE",
    "INVESTIGATION_TEMPLATE",
    "InvestigationRecord",
    "open_investigation",
    "confirm_reproduction",
    "confirm_regression",
    "close_investigation",
    "investigation_list",
    "check_investigate_wired",
    "_next_id",
    "_append_record",
    "_iso_now",
    "_rewrite_record",
]

INVESTIGATION_INDEX_RELATIVE = ".signalos/investigations/index.jsonl"
INVESTIGATION_DOCS_RELATIVE = ".signalos/investigations"

INVESTIGATION_TEMPLATE = """\
# INVESTIGATION — {title}

**ID:** {id}
**Wave:** {wave}
**Opened:** {ts}
**Status:** open

---

## Iron Laws Checklist

- [ ] Reproduction confirmed
- [ ] One variable at a time
- [ ] Log everything
- [ ] No assumption without evidence
- [ ] Regression written before fix

---

## Hypothesis

> _State the hypothesis here. What do you think is causing the bug?_

## Reproduction Steps

1. _Step 1_
2. _Step 2_

## Evidence

| Observation | Variable Changed | Result |
|-------------|-----------------|--------|
| | | |

## Regression Test

```
# Write the failing test here before applying any fix.
```

## Conclusion

> _Root cause, fix applied, and lessons learned._
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class InvestigationRecord:
    id: str                      # "inv-001"
    title: str
    status: str                  # "open" | "closed"
    wave: str
    ts: str                      # ISO-8601 opened timestamp
    reproduction_confirmed: bool
    regression_written: bool

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _next_id(index_path: Path, prefix: str) -> str:
    """Read JSONL at *index_path*, find highest N in ``{prefix}-NNN`` IDs, return next."""
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return f"{prefix}-001"

    highest = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            raw = d.get("id", "")
            if raw.startswith(f"{prefix}-"):
                suffix = raw[len(prefix) + 1:]
                n = int(suffix)
                if n > highest:
                    highest = n
        except (json.JSONDecodeError, ValueError):
            continue

    return f"{prefix}-{highest + 1:03d}"


def _append_record(index_path: Path, record: dict) -> None:
    """Create parent dirs and append JSON line to index."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _rewrite_record(index_path: Path, record_id: str, updates: dict) -> "InvestigationRecord | None":
    """Read JSONL, find record by id, apply *updates*, rewrite file. Returns updated record or None."""
    try:
        raw = index_path.read_text(encoding="utf-8")
    except OSError:
        return None

    lines = raw.splitlines()
    updated: "InvestigationRecord | None" = None
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            new_lines.append(line)
            continue
        if entry.get("id") == record_id:
            entry.update(updates)
            updated = InvestigationRecord(
                id=entry["id"],
                title=entry.get("title", ""),
                status=entry.get("status", "open"),
                wave=entry.get("wave", ""),
                ts=entry.get("ts", ""),
                reproduction_confirmed=bool(entry.get("reproduction_confirmed", False)),
                regression_written=bool(entry.get("regression_written", False)),
            )
            new_lines.append(json.dumps(entry))
        else:
            new_lines.append(line)

    if updated is None:
        return None

    index_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def open_investigation(
    repo_root: Path,
    title: str,
    wave: str,
) -> InvestigationRecord:
    """Create a new InvestigationRecord and write its INVESTIGATION.md template.

    The document is written to
    ``.signalos/investigations/{id}-INVESTIGATION.md``.
    """
    index_path = repo_root / INVESTIGATION_INDEX_RELATIVE
    record_id = _next_id(index_path, "inv")
    ts = _iso_now()
    record = InvestigationRecord(
        id=record_id,
        title=title,
        status="open",
        wave=wave,
        ts=ts,
        reproduction_confirmed=False,
        regression_written=False,
    )
    _append_record(index_path, record.as_dict())

    # Write the INVESTIGATION.md template
    docs_dir = repo_root / INVESTIGATION_DOCS_RELATIVE
    docs_dir.mkdir(parents=True, exist_ok=True)
    doc_path = docs_dir / f"{record_id}-INVESTIGATION.md"
    doc_path.write_text(
        INVESTIGATION_TEMPLATE.format(id=record_id, title=title, wave=wave, ts=ts),
        encoding="utf-8",
    )

    return record


def confirm_reproduction(
    repo_root: Path,
    inv_id: str,
) -> "InvestigationRecord | None":
    """Mark an investigation's reproduction step as confirmed (Iron Law 1).

    Returns the updated record, or None if not found.
    """
    index_path = repo_root / INVESTIGATION_INDEX_RELATIVE
    return _rewrite_record(index_path, inv_id, {"reproduction_confirmed": True})


def confirm_regression(
    repo_root: Path,
    inv_id: str,
) -> "InvestigationRecord | None":
    """Mark an investigation's regression test as written (Iron Law 5).

    Returns the updated record, or None if not found.
    """
    index_path = repo_root / INVESTIGATION_INDEX_RELATIVE
    return _rewrite_record(index_path, inv_id, {"regression_written": True})


def close_investigation(
    repo_root: Path,
    inv_id: str,
) -> "InvestigationRecord | None":
    """Close an investigation. Returns the updated record, or None if not found."""
    index_path = repo_root / INVESTIGATION_INDEX_RELATIVE
    return _rewrite_record(index_path, inv_id, {"status": "closed"})


def investigation_list(
    repo_root: Path,
    wave: Optional[str] = None,
) -> list[InvestigationRecord]:
    """Read investigations JSONL, optionally filter by wave.

    Defensive: OSError → [], JSONDecodeError/blank/KeyError/ValueError → skip line.
    """
    index_path = repo_root / INVESTIGATION_INDEX_RELATIVE
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    results: list[InvestigationRecord] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            d = json.loads(stripped)
            record = InvestigationRecord(
                id=d["id"],
                title=d["title"],
                status=d["status"],
                wave=d["wave"],
                ts=d["ts"],
                reproduction_confirmed=bool(d["reproduction_confirmed"]),
                regression_written=bool(d["regression_written"]),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if wave is not None and record.wave != wave:
            continue
        results.append(record)

    return results


def check_investigate_wired(repo_root: Path) -> tuple[bool, str]:
    """C21 (investigate half): check that investigate lib and command spec exist."""
    required = [
        repo_root / "cli" / "signalos_lib" / "investigate.py",
        repo_root / "core" / "execution" / "commands" / "signal-investigate.md",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, "investigate wired"
