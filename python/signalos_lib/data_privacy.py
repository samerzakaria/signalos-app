"""GDPR Article 17/30 tooling — data-subject access and erasure (AMD-CORE-026, W6.2).

Public API
----------
export_subject(subject, root=None) -> list[dict]
    Return all .jsonl entries that reference *subject*.

purge_subject(subject, reason, root=None) -> dict
    Redact *subject* from all .jsonl entries; return summary.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from .git_process import run_git

__all__ = [
    "DataPrivacyError",
    "export_subject",
    "purge_subject",
]

# The replacement tag written in place of a redacted value.
REDACT_TAG = "[REDACTED:GDPR17]"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _default_repo_root() -> Path:
    try:
        proc = run_git(
            ["rev-parse", "--show-toplevel"],
            cwd=Path.cwd(),
            runner=subprocess.run,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def _signalos_dir(root: Path) -> Path:
    return root / ".signalos"


def _find_jsonl_files(root: Path) -> list[Path]:
    """Return all .jsonl files under .signalos/ (recursive)."""
    d = _signalos_dir(root)
    if not d.is_dir():
        return []
    return sorted(d.rglob("*.jsonl"))


def _entry_matches(entry: dict[str, Any], subject_lower: str) -> bool:
    """Return True if any string value in *entry* contains *subject_lower*."""
    for v in entry.values():
        if isinstance(v, str) and subject_lower in v.lower():
            return True
    return False


def _redact_entry(
    entry: dict[str, Any],
    subject: str,
    pattern: re.Pattern[str],
) -> tuple[dict[str, Any], bool]:
    """Replace all case-insensitive occurrences of *subject* with REDACT_TAG.

    Returns (modified_entry, changed_flag).  Internal-only ``_source_file``
    key is passed through unchanged.
    """
    changed = False
    result: dict[str, Any] = {}
    for k, v in entry.items():
        if k == "_source_file":
            result[k] = v
            continue
        if isinstance(v, str) and subject.lower() in v.lower():
            new_v = pattern.sub(REDACT_TAG, v)
            result[k] = new_v
            changed = True
        else:
            result[k] = v
    return result, changed


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DataPrivacyError(Exception):
    """Raised when an export or purge operation cannot proceed."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_subject(
    subject: str,
    root: Path | None = None,
    *,
    _repo_root_fn: Callable[[], Path] | None = None,
) -> list[dict[str, Any]]:
    """Return all journal / audit-trail entries that reference *subject*.

    Searches every ``.jsonl`` file under ``.signalos/`` (recursively).  Each
    matching line is parsed and returned with an extra ``_source_file`` key
    (relative to *root*) indicating which file it came from.  Lines that
    cannot be parsed as JSON are silently skipped.

    Parameters
    ----------
    subject:
        Name (or partial name) of the data subject.  Case-insensitive.
    root:
        Repo root directory.  Defaults to ``git rev-parse --show-toplevel``.
    """
    if not subject or not subject.strip():
        raise DataPrivacyError("subject must be a non-empty string")

    effective_root = root if root is not None else (_repo_root_fn or _default_repo_root)()
    subject_lower = subject.strip().lower()

    matches: list[dict[str, Any]] = []
    for path in _find_jsonl_files(effective_root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and _entry_matches(entry, subject_lower):
                entry["_source_file"] = path.relative_to(effective_root).as_posix()
                matches.append(entry)
    return matches


def purge_subject(
    subject: str,
    reason: str,
    root: Path | None = None,
    *,
    _repo_root_fn: Callable[[], Path] | None = None,
) -> dict[str, Any]:
    """Redact all references to *subject* from every .jsonl file under .signalos/.

    Each affected file is rewritten in place.  Every string field containing
    *subject* (case-insensitive) is replaced with ``[REDACTED:GDPR17]``.

    A ``gdpr-purge`` record is appended to ``.signalos/AUDIT_TRAIL.jsonl``
    confirming the action (the subject name itself is not stored in the purge
    record — only the redaction tag and the entry/file counts).

    Returns ``{"files_modified": int, "entries_redacted": int}``.

    Parameters
    ----------
    subject:
        Name to erase.
    reason:
        Stated reason (e.g. ``"GDPR Article 17"``).
    root:
        Repo root directory.  Defaults to ``git rev-parse --show-toplevel``.
    """
    if not subject or not subject.strip():
        raise DataPrivacyError("subject must be a non-empty string")
    if not reason or not reason.strip():
        raise DataPrivacyError("reason must be a non-empty string")

    effective_root = root if root is not None else (_repo_root_fn or _default_repo_root)()
    subject_stripped = subject.strip()
    subject_lower = subject_stripped.lower()
    pattern = re.compile(re.escape(subject_stripped), re.IGNORECASE)

    files_modified = 0
    entries_redacted = 0

    for path in _find_jsonl_files(effective_root):
        try:
            original_text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        new_lines: list[str] = []
        file_changed = False

        for raw in original_text.splitlines():
            stripped = raw.strip()
            if not stripped:
                new_lines.append(raw)
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                new_lines.append(raw)
                continue

            if isinstance(entry, dict) and _entry_matches(entry, subject_lower):
                redacted, changed = _redact_entry(entry, subject_stripped, pattern)
                redacted.pop("_source_file", None)
                new_lines.append(json.dumps(redacted, separators=(",", ":")))
                if changed:
                    entries_redacted += 1
                    file_changed = True
            else:
                new_lines.append(raw)

        if file_changed:
            path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            files_modified += 1

    # Append purge record to audit trail (subject name is NOT stored).
    signalos_dir = _signalos_dir(effective_root)
    signalos_dir.mkdir(parents=True, exist_ok=True)
    audit_path = signalos_dir / "AUDIT_TRAIL.jsonl"
    purge_record: dict[str, Any] = {
        "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor": "system",
        "role": "system",
        "action": "gdpr-purge",
        "subject": REDACT_TAG,
        "reason": reason,
        "entries_redacted": entries_redacted,
        "files_modified": files_modified,
        "verdict": "APPROVED",
        "message": (
            f"GDPR Article 17 erasure — {entries_redacted} entries redacted "
            f"across {files_modified} file(s)"
        ),
    }
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(purge_record, separators=(",", ":")) + "\n")

    return {"files_modified": files_modified, "entries_redacted": entries_redacted}
