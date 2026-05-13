# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.3 — Rule-based context compressor (AMD-CORE-005, W1.3).
#
# Layer policy (mandated by AMD-CORE-005):
#   1. VERBATIM — last 2 turns always carried verbatim.
#   2. SUMMARY  — turns 3-10: 1-paragraph rule-based summary per turn
#                 (no LLM call; regex + heuristics).
#   3. HEADLINE — turns 11+: one-line headline per turn
#                 (first sentence of the turn, truncated at 120 chars).
#   4. DISCARD  — redacted secrets (matching redact.py rule set) and tool-
#                 output blobs >= 8 KB are dropped from the compressed
#                 window entirely, replaced by a placeholder line.
#
# Invariant: disk-truth (journal.jsonl, metrics.jsonl, AUDIT_TRAIL.jsonl)
# is NEVER compressed. Only the editor-facing context window is. Every
# public entrypoint calls _reject_disk_truth_input() first; the
# pre-session-compress.sh guard hook is the second layer of defence.
#
# Never-compress list (T3): any transcript metadata field matching the
# allowlist in _never_compress() is carried through verbatim no matter
# which layer applies. A violation raises RuntimeError (exit code 3).
#
# This module is stdlib-only (Python 3.11+); no new runtime deps.


from __future__ import annotations

__all__ = ["compress", "expand"]  # W-2: explicit public API

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Layer policy constants (mirror the stub docstring — do not change without
# an AMD-CORE amendment)
# ---------------------------------------------------------------------------

VERBATIM_TURNS = 2
SUMMARY_TURNS_UPPER = 10
LARGE_BLOB_THRESHOLD_BYTES = 8 * 1024
HEADLINE_MAX_CHARS = 120
SUMMARY_MAX_CHARS = 400


# ---------------------------------------------------------------------------
# Never-compress allowlist (T3)
# ---------------------------------------------------------------------------

_NEVER_COMPRESS_KEYS = (
    "gate_exit_criteria",
    "trust_tier_sheet",
    "active_constitution",
    "pending_amendments",
)


def _never_compress() -> tuple[str, ...]:
    """Return the hardcoded T3 never-compress key allowlist.

    A single violation of this list raises RuntimeError. The list is
    deliberately module-local and not configurable — any change requires
    an AMD-CORE amendment and co-signer review (see AMD-CORE-005).
    """
    return _NEVER_COMPRESS_KEYS


# ---------------------------------------------------------------------------
# Disk-truth refusal
# ---------------------------------------------------------------------------

_DISK_TRUTH_PATTERNS = (
    re.compile(r"\.signalos/sessions/[^/]+/journal\.jsonl$"),
    re.compile(r"\.signalos/sessions/[^/]+/metrics\.jsonl$"),
    re.compile(r"\.signalos/AUDIT_TRAIL\.jsonl$"),
)


def _reject_disk_truth_input(path: Path) -> None:
    """Raise RuntimeError if `path` looks like a disk-truth file.

    Mirrors core/execution/hooks/pre-session-compress/pre-session-compress.sh.
    This module-level check is the Python-side backstop so the compressor
    cannot be invoked directly on a journal/metrics/audit stream even
    when the shell guard is skipped.
    """
    s = str(path).replace(os.sep, "/")
    for pat in _DISK_TRUTH_PATTERNS:
        if pat.search(s):
            raise RuntimeError(
                "signalos context: disk-truth file refused — "
                f"{s} is append-only and must never be compressed "
                "(pre-session-compress.sh contract / AMD-CORE-001)"
            )


# ---------------------------------------------------------------------------
# Redaction (delegates to the shared hook-side filter)
# ---------------------------------------------------------------------------

# We embed a tiny subset of the redact.py rule set inline so the
# compressor works without shelling out. The shared redact.py is still
# the canonical T3 mutator on the journal write path — this is a
# read-side convenience for the in-memory transcript.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"claude-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bgh[ous]_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b[pr]k_live_[A-Za-z0-9]{20,}\b"),
)

_KEY_NAME_RE = re.compile(
    r"(?i)_(key|token|secret|password|credential|passphrase)$"
)


def _contains_secret(text: str) -> bool:
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            return True
    return False


def _scrub_secrets(text: str) -> str:
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


# ---------------------------------------------------------------------------
# Layer primitives
# ---------------------------------------------------------------------------

_SUMMARY_LINE_RE = re.compile(
    r"(?im)^(gate|result|outcome|verdict|todo|done)[:\s]"
)
_HEADING_RE = re.compile(r"^#{2,3}\s")
_SENTENCE_RE = re.compile(r"([^.!?\n]+[.!?])")


def _first_sentence(text: str) -> str:
    s = text.strip()
    if not s:
        return ""
    m = _SENTENCE_RE.search(s)
    if m:
        return m.group(1).strip()
    # No sentence terminator — use the first line, trimmed.
    return s.splitlines()[0].strip()


def _summarize(text: str) -> str:
    """Rule-based summary for SUMMARY layer (turns 3-10).

    Concatenate: first sentence + any ## / ### heading line + any line
    starting with Gate/Result/Outcome/Verdict/TODO/DONE (case-insensitive).
    Truncate to SUMMARY_MAX_CHARS.
    """
    parts: list[str] = []
    first = _first_sentence(text)
    if first:
        parts.append(first)
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if _HEADING_RE.match(line):
            parts.append(line.strip())
        elif _SUMMARY_LINE_RE.match(line):
            parts.append(line.strip())
    # Deduplicate adjacent repeats while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        deduped.append(p)
    summary = " ".join(deduped).strip()
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[: SUMMARY_MAX_CHARS - 1].rstrip() + "\u2026"
    return summary


def _headline(text: str) -> str:
    s = _first_sentence(text)
    if len(s) > HEADLINE_MAX_CHARS:
        s = s[: HEADLINE_MAX_CHARS - 1].rstrip() + "\u2026"
    return s


def _is_large_blob(content: str) -> bool:
    return len(content.encode("utf-8")) >= LARGE_BLOB_THRESHOLD_BYTES


# ---------------------------------------------------------------------------
# Transcript IO
# ---------------------------------------------------------------------------

def _read_transcript(path: Path) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line:
                continue
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"signalos context: invalid transcript line in {path}: {exc}"
                ) from exc
    return turns


def _extract_metadata(turns: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pull a metadata-only leading object (role == 'metadata') if present.

    The transcript may have an optional leading `{"role":"metadata", ...}`
    record. Its keys are checked against the never-compress allowlist.
    """
    if turns and str(turns[0].get("role", "")).lower() == "metadata":
        return turns[0], turns[1:]
    return {}, turns


def _sort_by_turn_index(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Stable sort; if turn_index is missing, preserve original order.
    def key(t: dict[str, Any]) -> int:
        v = t.get("turn_index")
        if isinstance(v, int):
            return v
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    return sorted(turns, key=key)


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

def _apply_layer(
    turn: dict[str, Any],
    *,
    age: int,
) -> tuple[dict[str, Any], str]:
    """Apply the correct layer to a single turn.

    `age` is 1-indexed recency (1 = most recent, 2 = second most recent,
    etc). Returns (new_turn, layer_tag) where layer_tag is one of
    'verbatim', 'summary', 'headline', 'discard'.
    """
    content = str(turn.get("content", ""))

    # DISCARD — secrets: scrub them first so the placeholder survives.
    scrubbed = content
    had_secret = _contains_secret(content)
    if had_secret:
        scrubbed = _scrub_secrets(content)
    # DISCARD — huge tool outputs: replace with placeholder BEFORE applying
    # a size-insensitive layer. Measured on the ORIGINAL bytes.
    if _is_large_blob(content):
        placeholder = f"[TOOL-OUTPUT DISCARDED \u2014 {len(content.encode('utf-8'))} bytes]"
        new = dict(turn)
        new["content"] = placeholder
        new["_compression_layer"] = "discard"
        return new, "discard"

    # If the turn was redacted entirely (all-secret content), drop it.
    # NOTE: unreachable with the current _scrub_secrets implementation (always
    # emits "[REDACTED]", never empty). Kept as a guard for future scrubbers.
    # Covered in test_defensive_branches.py via _scrub_secrets mock.
    if had_secret and not scrubbed.strip():
        new = dict(turn)
        new["content"] = "[REDACTED]"
        new["_compression_layer"] = "discard"
        return new, "discard"

    working = scrubbed

    if age <= VERBATIM_TURNS:
        new = dict(turn)
        new["content"] = working
        new["_compression_layer"] = "verbatim"
        return new, "verbatim"

    if age <= SUMMARY_TURNS_UPPER:
        new = dict(turn)
        new["content"] = _summarize(working)
        new["_compression_layer"] = "summary"
        return new, "summary"

    new = dict(turn)
    new["content"] = _headline(working)
    new["_compression_layer"] = "headline"
    return new, "headline"


def _enforce_never_compress(metadata: dict[str, Any]) -> None:
    """Sanity-check the metadata record against the allowlist.

    Implementation note: metadata keys that are in the allowlist are
    passed through verbatim by `compress_transcript_to`. This function
    raises RuntimeError if a caller ever tried to smuggle a compressed
    variant of an allowlisted field back into the metadata record
    (e.g. by prefixing with `_compressed_`). See AMD-CORE-005 §T3.
    """
    allow = _never_compress()
    for key in metadata.keys():
        if not isinstance(key, str):
            continue
        # Reject tampered variants of allowlisted keys.
        for forbidden in allow:
            if key != forbidden and key.endswith(forbidden) and key != f"_{forbidden}":
                # e.g. "compressed_gate_exit_criteria" is not allowed —
                # the original key must be preserved verbatim.
                raise RuntimeError(
                    "signalos context: never-compress violation — "
                    f"metadata key {key!r} shadows allowlisted {forbidden!r}"
                )


def _layer_counts(tags: Iterable[str]) -> dict[str, int]:
    counts = {"verbatim": 0, "summary": 0, "headline": 0, "discard": 0}
    for t in tags:
        if t in counts:
            counts[t] += 1
    return counts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compress_transcript(path: Path) -> dict[str, Any]:
    """Compress a session transcript JSONL and return a summary dict.

    Does not write anywhere. Returns:
        {
          "original_bytes": int,
          "compressed_bytes": int,
          "ratio": float,   # 1 - compressed/original
          "layers": {
            "verbatim_turns": int,
            "summary_turns": int,
            "headline_turns": int,
            "discarded_turns": int,
          },
        }
    """
    path = Path(path)
    _reject_disk_truth_input(path)
    if not path.is_file():
        raise RuntimeError(f"signalos context: transcript not found: {path}")

    raw = path.read_bytes()
    original_bytes = len(raw)

    turns = _read_transcript(path)
    metadata, body = _extract_metadata(turns)
    _enforce_never_compress(metadata)
    body_sorted = _sort_by_turn_index(body)

    compressed_records: list[dict[str, Any]] = []
    if metadata:
        compressed_records.append(metadata)

    tags: list[str] = []
    total = len(body_sorted)
    for idx, turn in enumerate(body_sorted):
        # Age: 1 = most-recent, grows with distance from the end.
        age = total - idx
        new_turn, tag = _apply_layer(turn, age=age)
        tags.append(tag)
        compressed_records.append(new_turn)

    # Serialize to measure size.
    compressed_payload = "".join(
        json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n"
        for r in compressed_records
    )
    compressed_bytes = len(compressed_payload.encode("utf-8"))

    ratio = 0.0 if original_bytes == 0 else 1.0 - (compressed_bytes / original_bytes)
    counts = _layer_counts(tags)
    return {
        "original_bytes": original_bytes,
        "compressed_bytes": compressed_bytes,
        "ratio": ratio,
        "layers": {
            "verbatim_turns": counts["verbatim"],
            "summary_turns": counts["summary"],
            "headline_turns": counts["headline"],
            "discarded_turns": counts["discard"],
        },
    }


def compress_transcript_to(path: Path, out_path: Path) -> dict[str, Any]:
    """Compress a transcript and write the result to `out_path`.

    Returns the same summary dict as `compress_transcript`. The output
    path must not be a disk-truth file.
    """
    path = Path(path)
    out_path = Path(out_path)
    _reject_disk_truth_input(path)
    _reject_disk_truth_input(out_path)

    if not path.is_file():
        raise RuntimeError(f"signalos context: transcript not found: {path}")

    raw = path.read_bytes()
    original_bytes = len(raw)

    turns = _read_transcript(path)
    metadata, body = _extract_metadata(turns)
    _enforce_never_compress(metadata)
    body_sorted = _sort_by_turn_index(body)

    compressed_records: list[dict[str, Any]] = []
    if metadata:
        compressed_records.append(metadata)

    tags: list[str] = []
    total = len(body_sorted)
    for idx, turn in enumerate(body_sorted):
        age = total - idx
        new_turn, tag = _apply_layer(turn, age=age)
        tags.append(tag)
        compressed_records.append(new_turn)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in compressed_records:
            fh.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False))
            fh.write("\n")
    os.replace(tmp, out_path)

    compressed_bytes = out_path.stat().st_size
    ratio = 0.0 if original_bytes == 0 else 1.0 - (compressed_bytes / original_bytes)
    counts = _layer_counts(tags)
    return {
        "original_bytes": original_bytes,
        "compressed_bytes": compressed_bytes,
        "ratio": ratio,
        "layers": {
            "verbatim_turns": counts["verbatim"],
            "summary_turns": counts["summary"],
            "headline_turns": counts["headline"],
            "discarded_turns": counts["discard"],
        },
    }


# ---------------------------------------------------------------------------
# Decompression — byte-identical expansion from disk
# ---------------------------------------------------------------------------

def _repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / ".signalos").is_dir():
            return cand
    # Fallback: walk up looking for the core/ directory, useful when
    # expand_scope is invoked from a checkout without a live .signalos.
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "core" / "governance").is_dir():
            return cand
    raise RuntimeError(
        "signalos context: no .signalos/ or core/governance/ ancestor; "
        "run from inside a SignalOS repo"
    )


_WAVE_RE = re.compile(r"^W\d+(\.\d+)+$")


def expand_scope(scope: str, root: Path | None = None) -> str:
    """Return the byte-identical on-disk content for a compressed scope.

    Lookup order:
      1. Waves:      core/governance/Retro/waves/<scope>/  — returns the
                     concatenation of every file in the directory (sorted
                     by name), each prefixed with a relative-path banner.
                     If a directory contains a single file we return its
                     exact bytes with no banner.
      2. Beliefs:    core/governance/Beliefs/<scope>.md
      3. Amendments: scan core/governance/Retro/AMENDMENTS.md for a table
                     row containing the AMD# exactly; return that row.

    Returns bytes from disk. Raises RuntimeError if the scope cannot be
    resolved.
    """
    if not scope or not isinstance(scope, str):
        raise ValueError("signalos context: scope is required")

    r = _repo_root(root)

    # 1. Wave
    wave_dir = r / "core" / "governance" / "Retro" / "waves" / scope
    if wave_dir.is_dir():
        files = sorted([p for p in wave_dir.iterdir() if p.is_file()])
        if len(files) == 1:
            return files[0].read_text(encoding="utf-8")
        if len(files) > 1:
            parts: list[str] = []
            for f in files:
                parts.append(f"<!-- {f.relative_to(r).as_posix()} -->\n")
                parts.append(f.read_text(encoding="utf-8"))
                if not parts[-1].endswith("\n"):
                    parts.append("\n")
            return "".join(parts)

    # 2. Belief
    belief_path = r / "core" / "governance" / "Beliefs" / f"{scope}.md"
    if belief_path.is_file():
        return belief_path.read_text(encoding="utf-8")

    # 3. Amendment row
    amendments_path = r / "core" / "governance" / "Retro" / "AMENDMENTS.md"
    if amendments_path.is_file():
        for raw in amendments_path.read_text(encoding="utf-8").splitlines():
            # The amendment rows are markdown table rows starting with
            # "| AMD-..." — scan by literal containment.
            if raw.startswith("|") and f"| {scope} " in raw:
                return raw + "\n"
            # Also accept scope as the cell value without spaces.
            if raw.startswith("|") and f"|{scope}|" in raw.replace(" ", ""):
                return raw + "\n"

    raise RuntimeError(
        f"signalos context: scope not found: {scope!r} "
        "(checked waves/, Beliefs/, AMENDMENTS.md)"
    )


# ---------------------------------------------------------------------------
# CLI-friendly error marker
# ---------------------------------------------------------------------------

class NeverCompressViolation(RuntimeError):
    """Raised when the never-compress allowlist is violated (exit code 3)."""


class DiskTruthRefused(RuntimeError):
    """Raised when a disk-truth input is refused (exit code 2)."""


# Re-wire the private helpers to raise the typed exceptions so the CLI
# layer can distinguish the two failure modes without string-matching.
_raw_reject = _reject_disk_truth_input


def _reject_disk_truth_input(path: Path) -> None:  # type: ignore[no-redef]
    s = str(path).replace(os.sep, "/")
    for pat in _DISK_TRUTH_PATTERNS:
        if pat.search(s):
            raise DiskTruthRefused(
                "signalos context: disk-truth file refused — "
                f"{s} is append-only and must never be compressed "
                "(pre-session-compress.sh contract / AMD-CORE-001)"
            )


_raw_enforce = _enforce_never_compress


def _enforce_never_compress(metadata: dict[str, Any]) -> None:  # type: ignore[no-redef]
    allow = _never_compress()
    for key in metadata.keys():
        if not isinstance(key, str):
            continue
        for forbidden in allow:
            if key != forbidden and key.endswith(forbidden) and key != f"_{forbidden}":
                raise NeverCompressViolation(
                    "signalos context: never-compress violation — "
                    f"metadata key {key!r} shadows allowlisted {forbidden!r}"
                )


if __name__ == "__main__":  # pragma: no cover
    sys.stderr.write(
        "signalos_lib.context is a library; use `signalos context ...` on the CLI\n"
    )
    sys.exit(2)
