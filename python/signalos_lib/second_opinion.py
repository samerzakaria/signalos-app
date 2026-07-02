"""
cli/signalos_lib/second_opinion.py — SignalOS Second Opinion (AMD-CORE-036) / W15.
Independent cross-model review: agree / disagree / risk-identified.
Records in DECISION-DNA for audit trail.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

__all__ = [
    "SECOND_OPINION_INDEX_RELATIVE",
    "VALID_VERDICTS",
    "vendor_of",
    "choose_cross_vendor_reviewer",
    "SecondOpinionRecord",
    "request_second_opinion",
    "record_verdict",
    "second_opinion_list",
    "check_second_opinion_wired",
    "_next_id",
    "_append_record",
    "_iso_now",
    "_rewrite_record",
]

SECOND_OPINION_INDEX_RELATIVE = ".signalos/second-opinion/index.jsonl"
VALID_VERDICTS = ("agree", "disagree", "risk-identified", "pending")

def vendor_of(model: str, default: str | None = None) -> str:
    """Derive a model's vendor WITHOUT hardcoding model names (families go stale).

    Only two authoritative sources are used: a LiteLLM-style *structural* prefix
    (``gemini/``, ``ollama/``, ``openai/`` ... -- the text before ``/``, a routing
    convention, not a model name), else the caller-supplied *default* (the
    provider the model was configured/discovered under). Never guessed from the
    model name string. Returns ``"unknown"`` when neither is available.
    """
    m = (model or "").strip().lower()
    if "/" in m:
        return m.split("/", 1)[0]
    return (default or "unknown").strip().lower() or "unknown"


def choose_cross_vendor_reviewer(
    author_model: str,
    candidates: list[str],
    *,
    vendors: dict[str, str] | None = None,
    default: str | None = None,
) -> str | None:
    """Return the first candidate model whose vendor differs from the author's,
    so a critique is graded by a different vendor than produced the artifact
    (FR-10.3). Vendor is resolved from the explicit *vendors* map (model ->
    vendor, sourced from provider config/discovery) when supplied, else from a
    structural prefix, else *default* -- never from the model name. Returns
    ``None`` when no second vendor is configured; the caller then keeps the
    critique same-vendor rather than pretending independence."""
    lookup = {str(k).strip().lower(): str(v).strip().lower()
              for k, v in (vendors or {}).items()}

    def _vendor(model: str) -> str:
        key = (model or "").strip().lower()
        return lookup.get(key) or vendor_of(model, default)

    author_vendor = _vendor(author_model)
    for candidate in candidates:
        if _vendor(candidate) != author_vendor:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SecondOpinionRecord:
    id: str                  # "so-001"
    subject: str             # what is being reviewed
    verdict: str             # "agree" | "disagree" | "risk-identified" | "pending"
    new_risk: str            # populated when verdict == "risk-identified"
    wave: str
    ts: str                  # ISO-8601
    decision_dna_ref: str    # optional reference into DECISION-DNA

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


def _rewrite_record(index_path: Path, record_id: str, updates: dict) -> "SecondOpinionRecord | None":
    """Read JSONL, find record by id, apply *updates*, rewrite file. Returns updated record or None."""
    try:
        raw = index_path.read_text(encoding="utf-8")
    except OSError:
        return None

    lines = raw.splitlines()
    updated: "SecondOpinionRecord | None" = None
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
            updated = SecondOpinionRecord(
                id=entry["id"],
                subject=entry.get("subject", ""),
                verdict=entry.get("verdict", "pending"),
                new_risk=entry.get("new_risk", ""),
                wave=entry.get("wave", ""),
                ts=entry.get("ts", ""),
                decision_dna_ref=entry.get("decision_dna_ref", ""),
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

def request_second_opinion(
    repo_root: Path,
    subject: str,
    wave: str,
    note: str = "",
) -> SecondOpinionRecord:
    """Create a new SecondOpinionRecord with verdict='pending'.

    *subject* describes what is under review (e.g. the plan title, diff, decision).
    *note* is stored as part of the subject for context.
    """
    index_path = repo_root / SECOND_OPINION_INDEX_RELATIVE
    record_id = _next_id(index_path, "so")
    full_subject = f"{subject} | {note}" if note else subject
    record = SecondOpinionRecord(
        id=record_id,
        subject=full_subject,
        verdict="pending",
        new_risk="",
        wave=wave,
        ts=_iso_now(),
        decision_dna_ref="",
    )
    _append_record(index_path, record.as_dict())
    return record


def record_verdict(
    repo_root: Path,
    opinion_id: str,
    verdict: str,
    new_risk: str = "",
    decision_dna_ref: str = "",
) -> "SecondOpinionRecord | None":
    """Update a SecondOpinionRecord with the second model's verdict.

    *verdict* must be one of ``VALID_VERDICTS``.
    Returns the updated record, or None if not found or invalid verdict.
    Raises ValueError if verdict is not valid.
    """
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"verdict must be one of {VALID_VERDICTS}, got {verdict!r}")
    index_path = repo_root / SECOND_OPINION_INDEX_RELATIVE
    updates = {
        "verdict": verdict,
        "new_risk": new_risk,
        "decision_dna_ref": decision_dna_ref,
    }
    return _rewrite_record(index_path, opinion_id, updates)


def second_opinion_list(
    repo_root: Path,
    wave: Optional[str] = None,
) -> list[SecondOpinionRecord]:
    """Read second-opinion JSONL, optionally filter by wave.

    Defensive: OSError → [], JSONDecodeError/blank/KeyError → skip line.
    """
    index_path = repo_root / SECOND_OPINION_INDEX_RELATIVE
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    results: list[SecondOpinionRecord] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            d = json.loads(stripped)
            record = SecondOpinionRecord(
                id=d["id"],
                subject=d["subject"],
                verdict=d["verdict"],
                new_risk=d["new_risk"],
                wave=d["wave"],
                ts=d["ts"],
                decision_dna_ref=d["decision_dna_ref"],
            )
        except (json.JSONDecodeError, KeyError):
            continue
        if wave is not None and record.wave != wave:
            continue
        results.append(record)

    return results


def check_second_opinion_wired(repo_root: Path | None = None) -> tuple[bool, str]:
    """C21 (0.4): verify the second-opinion capability is actually *callable* --
    the library functions import and are callable and the CLI wrapper imports --
    not merely that files sit at some path. ``repo_root`` is accepted for
    signature compatibility but capability is layout-independent."""
    import importlib

    problems: list[str] = []
    try:
        mod = importlib.import_module("signalos_lib.second_opinion")
        for fn in ("request_second_opinion", "record_verdict",
                   "choose_cross_vendor_reviewer"):
            if not callable(getattr(mod, fn, None)):
                problems.append(f"signalos_lib.second_opinion.{fn} not callable")
    except Exception as exc:  # pragma: no cover - import failure path
        problems.append(f"cannot import signalos_lib.second_opinion: {exc}")
    try:
        cmd = importlib.import_module("signalos_lib.commands.second_opinion")
        if not callable(getattr(cmd, "cmd_signal_second_opinion", None)):
            problems.append("cmd_signal_second_opinion not callable")
    except Exception as exc:  # pragma: no cover - import failure path
        problems.append(f"cannot import command wrapper: {exc}")
    if problems:
        return False, "; ".join(problems)
    return True, "second-opinion capability importable and callable"
