# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/intent.py
# W3.3 — Natural language intent router (AMD-CORE-016)
#
# Pure stdlib — no LLM call on the routing path.
# Classifies free-form user input against 10 known intents using
# weighted keyword matching, returns a confidence score, and either
# routes to the matching signalos command or asks one clarifying question.

from __future__ import annotations

__all__ = [
    "INTENTS",
    "CONFIDENCE_THRESHOLD",
    "DEFAULT_MAX_PROMPT_BYTES",
    "DEFAULT_MAX_SOURCE_BYTES",
    "IntentMatch",
    "SourceIntentError",
    "classify",
    "import_source_document",
    "persist_prompt_source",
    "top_match",
    "route_or_clarify",
]

import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from typing import Optional


CONFIDENCE_THRESHOLD = 0.70  # below this → ask clarifying question
SOURCE_SCHEMA_VERSION = "signalos.source-intent.v1"
DEFAULT_MAX_PROMPT_BYTES = 256 * 1024
DEFAULT_MAX_SOURCE_BYTES = 2 * 1024 * 1024
SOURCE_KINDS = {"prompt", "prd", "spec", "document"}


class SourceIntentError(ValueError):
    """Raised when source intent capture cannot be completed safely."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalise_repo_root(repo_root: str | Path | None) -> Path:
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise SourceIntentError(f"repo root is not a directory: {root}")
    return root


def _relative_to_repo(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise SourceIntentError(f"source output path escapes repo root: {path}") from exc


def _sources_dir(repo_root: Path) -> Path:
    signalos_dir = repo_root / ".signalos"
    if signalos_dir.exists():
        _relative_to_repo(repo_root, signalos_dir)
        if not signalos_dir.is_dir():
            raise SourceIntentError(f".signalos path is not a directory: {signalos_dir}")
    else:
        signalos_dir.mkdir()
    target = signalos_dir / "sources"
    if target.exists():
        _relative_to_repo(repo_root, target)
        if not target.is_dir():
            raise SourceIntentError(f"sources path is not a directory: {target}")
    else:
        target.mkdir()
    _relative_to_repo(repo_root, target)
    return target


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _safe_source_kind(source_kind: str) -> str:
    kind = source_kind.strip().lower()
    if kind not in SOURCE_KINDS:
        raise SourceIntentError(f"unsupported source kind: {source_kind}")
    return kind


def _safe_suffix(source_path: Path) -> str:
    suffix = source_path.suffix.lower()
    if not suffix or len(suffix) > 16:
        return ".source"
    if not suffix.startswith("."):
        return ".source"
    if not all(ch.isalnum() or ch == "." for ch in suffix):
        return ".source"
    return suffix


def _bounded_positive_limit(max_bytes: int) -> int:
    if max_bytes <= 0:
        raise SourceIntentError("max source bytes must be greater than zero")
    return max_bytes


def persist_prompt_source(
    phrase: str,
    *,
    repo_root: str | Path | None = None,
    classification: dict[str, Any] | None = None,
    max_bytes: int = DEFAULT_MAX_PROMPT_BYTES,
) -> dict[str, Any]:
    """Persist the initial prompt source under `.signalos/sources`."""
    text = phrase.strip()
    if not text:
        raise SourceIntentError("prompt source must not be empty")
    limit = _bounded_positive_limit(max_bytes)
    data = text.encode("utf-8")
    if len(data) > limit:
        raise SourceIntentError(f"prompt source exceeds {limit} bytes")

    root = _normalise_repo_root(repo_root)
    sources = _sources_dir(root)
    record_path = sources / "initial-intent.json"
    digest = _sha256(data)
    payload: dict[str, Any] = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "kind": "prompt",
        "source_type": "prompt",
        "text": text,
        "bytes": len(data),
        "fingerprint": {
            "algorithm": "sha256",
            "value": digest,
        },
        "record_path": _relative_to_repo(root, record_path),
    }
    if classification is not None:
        payload["classification"] = classification
    _write_json(record_path, payload)
    return dict(payload)


def import_source_document(
    source_file: str | Path,
    *,
    repo_root: str | Path | None = None,
    source_kind: str = "document",
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    """Copy a PRD/spec/document into `.signalos/sources` with metadata."""
    kind = _safe_source_kind(source_kind)
    if kind == "prompt":
        raise SourceIntentError("file source kind must be prd, spec, or document")
    limit = _bounded_positive_limit(max_bytes)

    source_path = Path(source_file).expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        raise SourceIntentError(f"source file is not a file: {source_file}")
    size = source_path.stat().st_size
    if size > limit:
        raise SourceIntentError(f"source file exceeds {limit} bytes")

    data = source_path.read_bytes()
    if len(data) > limit:
        raise SourceIntentError(f"source file exceeds {limit} bytes")

    root = _normalise_repo_root(repo_root)
    sources = _sources_dir(root)
    digest = _sha256(data)
    short = digest[:16]
    suffix = _safe_suffix(source_path)
    stored_path = sources / f"{kind}-{short}{suffix}"
    record_path = sources / f"source-{kind}-{short}.json"
    _relative_to_repo(root, stored_path)
    _relative_to_repo(root, record_path)

    stored_path.write_bytes(data)
    media_type, _encoding = mimetypes.guess_type(source_path.name)
    payload: dict[str, Any] = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "kind": kind,
        "source_type": kind,
        "original_name": source_path.name,
        "stored_path": _relative_to_repo(root, stored_path),
        "record_path": _relative_to_repo(root, record_path),
        "bytes": len(data),
        "media_type": media_type or "application/octet-stream",
        "fingerprint": {
            "algorithm": "sha256",
            "value": digest,
        },
    }
    _write_json(record_path, payload)
    return dict(payload)

# ---------------------------------------------------------------------------
# Intent definitions
# ---------------------------------------------------------------------------
# Each entry:
#   name      — canonical intent identifier
#   command   — suggested signalos command (may include <placeholder> tokens)
#   clarify   — question to ask when confidence is below threshold
#   patterns  — list of (regex, weight); higher weight = stronger signal
#   max_score — auto-computed; set by _finalise_intents()

INTENTS: list[dict] = [
    {
        "name": "onboard",
        "command": "signalos session start",
        "clarify": "Did you mean to start a new session or onboard a product?",
        "patterns": [
            (r"\bonboard\b",                        3),
            (r"\bnew\s+session\b",                 3),
            (r"\bstart\s+fresh\b",                 2),
            (r"\bset\s*up\b|\bsetup\b",           1),
            (r"\biniti[ae]li[sz]",                   2),
            (r"\bnew\b.{0,20}\bproduct\b",        2),
        ],
    },
    {
        "name": "brainstorm",
        "command": "signalos harness call --step brainstorm",
        "clarify": "Did you mean to brainstorm ideas for a new feature or product?",
        "patterns": [
            (r"\bbrainstorm\b",                     4),
            (r"\bideas?\b",                         2),
            (r"\bexplore\b|\bthink\s+about\b",   1),
            (r"\bwhat\s+if\b",                     2),
            (r"\bnew\b.{0,20}\bfeature\b",        2),
            (r"\badd\b.{0,25}\b(feature|support|functionality)\b", 2),
            (r"\bi\s+want\s+to\b.{0,30}\b(build|create|make)\b", 1),
        ],
    },
    {
        "name": "plan",
        "command": "signalos orchestrate --wave <wave-id> --plan PLAN.md",
        "clarify": "Did you mean to create or kick off a plan for the current wave?",
        "patterns": [
            (r"\bplan\b|\bplanning\b",            3),
            (r"\bbacklog\b|\bschedule\b",         2),
            (r"\bcreate\b.{0,20}\btasks?\b",      2),
            (r"\bwave\b.{0,15}\bstart\b",         2),
            (r"\borchestrate\b",                    3),
            (r"\bwork\s+order\b",                  2),
        ],
    },
    {
        "name": "execute",
        "command": "signalos orchestrate --wave <wave-id> --plan PLAN.md",
        "clarify": "Did you mean to execute / run the current wave tasks?",
        "patterns": [
            (r"\bexecut",                            2),
            (r"\brun\b.{0,15}\b(task|step|wave)\b", 2),
            (r"\bimplement\b",                      2),
            (r"\bdeliver\b|\bdeployment\b",       2),
            (r"\bstart\b.{0,15}\b(work|task|step)\b", 2),
            (r"\bwrite\b.{0,15}\bcode\b",         1),
        ],
    },
    {
        "name": "review",
        "command": "signalos harness call --step review",
        "clarify": "Did you mean to review the wave output or run quality checks?",
        "patterns": [
            (r"\breview\b",                         4),
            (r"\bquality\b.{0,15}\bcheck\b",     3),
            (r"\bvalidate\b|\bverif[yi]",          2),
            (r"\bQA\b|\bquality\s+assurance\b",  3),
            (r"\bdebrief\b",                        2),
            (r"\btest\b.{0,15}\bresult\b",        1),
        ],
    },
    {
        "name": "status",
        "command": "signalos status",
        "clarify": "Did you mean to check the current wave status?",
        "patterns": [
            (r"\bstatus\b|\bprogress\b",          4),
            (r"\bwhere\b.{0,15}\b(are we|am i)\b", 3),
            (r"\bwhat.{0,10}(happening|going on)\b", 2),
            (r"\bcurrent\b.{0,15}\b(state|phase|gate)\b", 2),
            (r"\bhow\b.{0,10}\b(far|much)\b",    2),
        ],
    },
    {
        "name": "sign",
        "command": "signalos sign <gate>",
        "clarify": "Did you mean to sign a gate artifact (G0–G5)?",
        "patterns": [
            (r"\bsign\b(?!.{0,10}(out|in|up|language))", 4),
            (r"\bapprove\b|\bapproval\b",         3),
            (r"\bgate\b.{0,20}\b(sign|approv|clos|pass)\b", 3),
            (r"\bG[0-5]\b",                         2),
            (r"\bsign\s+off\b|\bsignature\b",    3),
        ],
    },
    {
        "name": "pause",
        "command": "signalos pause list",
        "clarify": "Did you mean to pause a step or list paused steps?",
        "patterns": [
            (r"\bpaus[ei]\b",                       4),
            (r"\bstop\b.{0,15}\bstep\b",         2),
            (r"\bhold\b.{0,15}\bstep\b",         2),
        ],
    },
    {
        "name": "resume",
        "command": "signalos pause resume <step-id>",
        "clarify": "Did you mean to resume a paused step?",
        "patterns": [
            (r"\bresume\b",                         4),
            (r"\bunpause\b|\bunblock\b",           3),
            (r"\bcontinue\b.{0,15}\bstep\b",     3),
            (r"\bpick\s+up\b.{0,20}\bwhere\b",  2),
        ],
    },
    {
        "name": "compress",
        "command": "signalos context compress <input-file>",
        "clarify": "Did you mean to compress the session context?",
        "patterns": [
            (r"\bcompress\b",                       4),
            (r"\bsummariz[ei]\b.{0,20}\bcontext\b", 3),
            (r"\bcontext\b.{0,25}\b(too long|shrink|reduc)\b", 3),
            (r"\btokens?\b.{0,20}\b(too many|limit|exceed)\b", 2),
            (r"\btruncate\b|\bshorten\b",         2),
        ],
    },
    {
        "name": "signal-qa",
        "command": "signalos signal-qa",
        "clarify": "Do you want to run the full gating QA suite (/signal-qa) or a fast non-gating check (/signal-qa-only)?",
        "patterns": [
            (r"\b(run|start|execute|trigger)\b.{0,20}\b(qa|quality|browser.test|scenario)\b", 4),
            (r"\b(qa|quality.assurance)\b.{0,20}\b(run|suite|pass|gate|check)\b", 4),
            (r"\bsignal.qa\b(?!.{0,5}only)",                                               5),
            (r"\bqa.gate\b|\bgate.5\b|\bquality.check\b",                              4),
            (r"\bbrowser.scenario\b|\bscenario.suite\b",                                 3),
        ],
    },
    {
        "name": "signal-qa-only",
        "command": "signalos signal-qa-only",
        "clarify": "Do you want a fast non-gating QA check without Gate 5 ceremony?",
        "patterns": [
            (r"\bqa.only\b|\bsignal.qa.only\b",                                         5),
            (r"\b(quick|fast|non.gating)\b.{0,20}\b(qa|test|scenario|browser)\b",       4),
            (r"\b(sanity.check|smoke.test)\b",                                             3),
        ],
    },
    {
        "name": "signal-pre-design",
        "command": "signalos pre-design",
        "clarify": "Do you want to start the design scoping ceremony and fill the PO Brief?",
        "patterns": [
            (r"\bpre.design\b|\bpo.brief\b|\bsignal.pre.design\b",                   5),
            (r"\b(start|begin|open|kick.?off)\b.{0,20}\bdesign\b",                     3),
            (r"\bscoping.ceremon\b|\bforcing.question\b",                               4),
            (r"\bdesign.mode\b.{0,20}\b(explore|validate|iterate|land)\b",             4),
        ],
    },
    {
        "name": "signal-design",
        "command": "signalos design",
        "clarify": "Do you want to explore, approve, or iterate on a design variant?",
        "patterns": [
            (r"\bsignal.design\b(?!.{0,10}(review|html|pre))",                           5),
            (r"\b(explore|approve|iterate)\b.{0,20}\b(design|variant|mockup)\b",       4),
            (r"\bdesign.variant\b|\bhtml.archetype\b|\bmockup\b",                    3),
        ],
    },
    {
        "name": "signal-design-review",
        "command": "signalos design-review",
        "clarify": "Do you want to run the 8-dimension design review rubric on a variant?",
        "patterns": [
            (r"\bdesign.review\b|\bsignal.design.review\b",                            5),
            (r"\b(review|score|rate|rubric)\b.{0,20}\b(design|variant|mockup)\b",      4),
            (r"\bslop.detect\b|\bai.slop\b|\b8.dimension\b",                         4),
        ],
    },
    {
        "name": "signal-design-html",
        "command": "signalos design-html",
        "clarify": "Do you want to promote an approved design variant to production HTML?",
        "patterns": [
            (r"\bdesign.html\b|\bsignal.design.html\b",                                5),
            (r"\b(promote|export|generate)\b.{0,20}\b(html|jsx|svelte|production)\b", 4),
            (r"\bproduction.html\b|\bpromote.variant\b",                               4),
        ],
    },
    {
        "name": "brain",
        "command": "signalos brain",
        "clarify": "Do you want to put, search, list, prune, export, or upgrade the brain index?",
        "patterns": [
            (r"\bbrain\b.{0,20}\b(put|store|index|ingest|add|save)\b", 4),
            (r"\b(put|store|index|ingest|add|save)\b.{0,20}\bbrain\b", 4),
            (r"\bbrain\b.{0,20}\b(search|find|query|recall|retrieve)\b", 4),
            (r"\b(search|find|query|recall|retrieve)\b.{0,20}\bbrain\b", 4),
            (r"\bbrain\b.{0,20}\b(list|show|export|prune|upgrade)\b", 3),
            (r"\bknowledge index\b|\bmemory index\b",                 3),
            (r"\bsignalos brain\b",                                     5),
        ],
    },
    {
        "name": "signal-learn",
        "command": "signalos signal-learn",
        "clarify": "Do you want to review, search, prune, or export brain entries via signal-learn?",
        "patterns": [
            (r"\bsignal.?learn\b",                                      5),
            (r"\b(review|audit)\b.{0,20}\b(brain|memory|entries)\b", 4),
            (r"\b(prune|clean|remove)\b.{0,20}\bbrain\b",            4),
            (r"\blearn\b.{0,20}\b(review|search|prune|export)\b",    3),
        ],
    },
]


def _max_score(intent: dict) -> float:
    return float(sum(w for _, w in intent["patterns"]))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

@dataclass
class IntentMatch:
    """Result of classifying one phrase against one intent."""
    name: str
    command: str
    clarify: str
    raw_score: float
    max_score: float
    confidence: float  # raw_score / max_score, clamped to [0, 1]
    matched_patterns: list[str] = field(default_factory=list)

    @property
    def routable(self) -> bool:
        return self.confidence >= CONFIDENCE_THRESHOLD


def _score_intent(phrase: str, intent: dict) -> IntentMatch:
    """Score one intent against *phrase*."""
    phrase_lower = phrase.lower()
    raw = 0.0
    matched: list[str] = []
    for pattern, weight in intent["patterns"]:
        if re.search(pattern, phrase_lower):
            raw += weight
            matched.append(pattern)
    mx = _max_score(intent)
    conf = min(raw / mx, 1.0) if mx > 0 else 0.0
    return IntentMatch(
        name=intent["name"],
        command=intent["command"],
        clarify=intent["clarify"],
        raw_score=raw,
        max_score=mx,
        confidence=conf,
        matched_patterns=matched,
    )


def classify(phrase: str) -> list[IntentMatch]:
    """
    Score *phrase* against all intents.
    Returns a list sorted by confidence descending.
    """
    results = [_score_intent(phrase, intent) for intent in INTENTS]
    results.sort(key=lambda m: m.confidence, reverse=True)
    return results


def top_match(phrase: str) -> IntentMatch:
    """Return the single highest-confidence IntentMatch for *phrase*."""
    return classify(phrase)[0]


def route_or_clarify(phrase: str) -> dict:
    """
    High-level routing result.

    Returns a dict with keys:
        routed    bool   True if confidence >= CONFIDENCE_THRESHOLD
        intent    str    intent name
        command   str    suggested signalos command
        confidence float
        clarify   str    question to ask if not routed (empty if routed)
        top2      list   top-2 intent names (for disambiguation UI)
    """
    ranked = classify(phrase)
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    return {
        "routed": best.routable,
        "intent": best.name,
        "command": best.command,
        "confidence": round(best.confidence, 3),
        "clarify": "" if best.routable else best.clarify,
        "top2": [m.name for m in ranked[:2]],
    }
