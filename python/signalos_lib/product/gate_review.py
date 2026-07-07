# signalos_lib/product/gate_review.py
# Gate Review: classify user replies, handle REQUEST-CHANGES and REJECTED verdicts.
#
# Budgeted loops: request-changes uses the shared gate rework budget, rejections
# stay capped separately, then escalate to a human decision-maker.

from __future__ import annotations

__all__ = [
    "classify_review",
    "handle_request_changes",
    "handle_rejection",
    "build_rework_packet",
    "build_rejection_packet",
    "record_review_event",
    "latest_review_cycle",
]

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .budgets import resolve_gate_rework_budget


# ---------------------------------------------------------------------------
# classify_review
# ---------------------------------------------------------------------------

# Pattern groups (order matters -- first match wins)
_REJECT_PHRASES = (
    "reject",
    "rejected",
    "start over",
    "completely wrong",
    "wrong direction",
    "scrap it",
    "scrap this",
    "not what i want",
    "not what i asked",
    "throw it away",
    "redo from scratch",
)

_WAIVE_PHRASES = (
    "skip",
    "waive",
    "not needed",
    "skip this gate",
    "skip this",
    "don't need this",
    "not required",
    "pass on this",
)

_APPROVE_PHRASES = (
    "approve",
    "approved",
    "yes",
    "lgtm",
    "looks good",
    "looks great",
    "ship it",
    "go ahead",
    "confirm",
    "good to go",
    "all good",
    "perfect",
)

_CHANGE_VERBS = (
    "change",
    "fix",
    "update",
    "modify",
    "adjust",
    "revise",
    "rename",
    "replace",
    "move",
    "remove",
    "add",
    "switch",
    "use",
    "make it",
    "should be",
    "instead of",
    "rather than",
)


def classify_review(user_reply: str) -> dict[str, Any]:
    """Classify a user's reply at a gate review point.

    Returns:
    {
        "verdict": "approve" | "approve-with-conditions" | "request-changes" | "reject" | "waive",
        "feedback": str,
        "specific_items": list[str],
        "confidence": float,
    }
    """
    raw = user_reply.strip()
    lower = raw.lower()

    if not raw:
        return {
            "verdict": "approve",
            "feedback": "",
            "specific_items": [],
            "confidence": 0.3,
        }

    # --- Waive (check before reject because "skip" is unambiguous) ---
    for phrase in _WAIVE_PHRASES:
        if phrase in lower:
            return {
                "verdict": "waive",
                "feedback": raw,
                "specific_items": [],
                "confidence": 0.9,
            }

    # --- Reject (strong negation + intent to restart) ---
    # Pure "no" by itself is reject
    if lower.rstrip("!., ") == "no":
        return {
            "verdict": "reject",
            "feedback": raw,
            "specific_items": [],
            "confidence": 0.8,
        }
    for phrase in _REJECT_PHRASES:
        if phrase in lower:
            return {
                "verdict": "reject",
                "feedback": raw,
                "specific_items": [],
                "confidence": 0.85,
            }

    # --- Approve-with-conditions ("yes but...", "approve with...") ---
    approve_but_pattern = re.compile(
        r"^(yes|approve|approved|lgtm|looks good)\s+(but|with|however|though)\b",
        re.IGNORECASE,
    )
    if approve_but_pattern.match(lower):
        conditions = raw
        return {
            "verdict": "approve-with-conditions",
            "feedback": conditions,
            "specific_items": [],
            "confidence": 0.85,
        }

    # --- Request-changes (action verb present + specific instruction) ---
    for verb in _CHANGE_VERBS:
        # Look for verb at word boundary
        pattern = r"\b" + re.escape(verb) + r"\b"
        if re.search(pattern, lower):
            items = _extract_specific_items(raw)
            return {
                "verdict": "request-changes",
                "feedback": raw,
                "specific_items": items,
                "confidence": 0.8,
            }

    # --- "no, <something>" pattern (disagree + instruction = request-changes) ---
    no_then_instruction = re.compile(r"^no[,.]?\s+(.+)", re.IGNORECASE)
    m = no_then_instruction.match(raw)
    if m:
        instruction = m.group(1)
        items = _extract_specific_items(instruction)
        return {
            "verdict": "request-changes",
            "feedback": raw,
            "specific_items": items if items else [instruction],
            "confidence": 0.75,
        }

    # --- Approve (simple affirmation) ---
    for phrase in _APPROVE_PHRASES:
        if phrase in lower:
            return {
                "verdict": "approve",
                "feedback": raw,
                "specific_items": [],
                "confidence": 0.9,
            }

    # --- Fallback: treat as request-changes (user typed something specific) ---
    items = _extract_specific_items(raw)
    return {
        "verdict": "request-changes",
        "feedback": raw,
        "specific_items": items if items else [raw],
        "confidence": 0.5,
    }


def _extract_specific_items(text: str) -> list[str]:
    """Extract specific actionable items from user text.

    Splits on numbered lists, bullet points, commas (if multiple clauses),
    or returns the whole text as one item.
    """
    items: list[str] = []

    # Numbered items: "1. foo 2. bar" or "1) foo 2) bar"
    numbered = re.split(r"\d+[.)]\s+", text)
    numbered = [s.strip() for s in numbered if s.strip()]
    if len(numbered) > 1:
        return numbered

    # Bullet points
    bulleted = re.split(r"[\n\r]+\s*[-*]\s+", text)
    bulleted = [s.strip() for s in bulleted if s.strip()]
    if len(bulleted) > 1:
        return bulleted

    # Semicolons
    if ";" in text:
        parts = [s.strip() for s in text.split(";") if s.strip()]
        if len(parts) > 1:
            return parts

    # Commas with "and"
    if ", " in text and " and " in text:
        # Split on ", " and " and "
        parts = re.split(r",\s+|\s+and\s+", text)
        parts = [s.strip() for s in parts if s.strip()]
        if len(parts) > 1:
            return parts

    # Single item
    if text.strip():
        return [text.strip()]
    return []


# ---------------------------------------------------------------------------
# latest_review_cycle
# ---------------------------------------------------------------------------

def latest_review_cycle(
    repo_root: Path,
    gate_id: str,
    packet_type: str = "rework",
) -> int:
    """Return the highest review cycle already dispatched for a gate.

    The review packets ARE the persistence: each dispatched cycle writes
    .signalos/product/reviews/<gate_id>/cycle-<n>/<packet_type>-packet.json,
    so callers that span IPC requests (e.g. the standalone agent:verdict
    path) recover the prior cycle by scanning those directories instead of
    keeping in-memory counters. Returns 0 when no packet exists yet.

    packet_type: "rework" (request-changes cycles) or "regenerate"
    (rejection cycles) -- the two share the cycle-<n> directory namespace,
    so each budget only counts its own packet kind.
    """
    review_dir = repo_root / ".signalos" / "product" / "reviews" / gate_id
    latest = 0
    if not review_dir.is_dir():
        return latest
    for child in review_dir.iterdir():
        m = re.fullmatch(r"cycle-(\d+)", child.name)
        if m and (child / f"{packet_type}-packet.json").is_file():
            latest = max(latest, int(m.group(1)))
    return latest


# ---------------------------------------------------------------------------
# handle_request_changes
# ---------------------------------------------------------------------------

def handle_request_changes(
    repo_root: Path,
    gate_id: str,
    feedback: str,
    specific_items: list[str],
    current_artifact: dict | None = None,
    max_cycles: int | None = None,
    cycle: int = 0,
) -> dict[str, Any]:
    """Handle a REQUEST-CHANGES verdict.

    Creates a rework packet for the agent with the user's specific feedback.
    The agent must address each item. After rework, the gate is re-presented.

    Returns:
    {
        "status": "rework_dispatched" | "max_cycles_reached" | "escalated",
        "cycle": int,
        "rework_packet": dict | None,
        "packet_path": str | None,
    }
    """
    cycle_budget = resolve_gate_rework_budget(max_cycles)
    next_cycle = cycle + 1

    if next_cycle > cycle_budget:
        record_review_event(repo_root, gate_id, "REQUEST-CHANGES", feedback, next_cycle)
        return {
            "status": "max_cycles_reached",
            "cycle": next_cycle,
            "rework_packet": None,
            "packet_path": None,
        }

    # Load governance context for this gate
    governance_context = _load_governance_context(repo_root, gate_id)

    packet = build_rework_packet(
        gate_id=gate_id,
        feedback=feedback,
        specific_items=specific_items,
        current_artifact=current_artifact,
        governance_context=governance_context,
    )

    # Write packet to disk
    packet_path = _write_review_packet(repo_root, gate_id, packet, next_cycle)

    # Record audit event
    record_review_event(repo_root, gate_id, "REQUEST-CHANGES", feedback, next_cycle)

    return {
        "status": "rework_dispatched",
        "cycle": next_cycle,
        "rework_packet": packet,
        "packet_path": str(packet_path),
    }


# ---------------------------------------------------------------------------
# handle_rejection
# ---------------------------------------------------------------------------

def handle_rejection(
    repo_root: Path,
    gate_id: str,
    reason: str,
    max_rejections: int = 2,
    rejection_count: int = 0,
) -> dict[str, Any]:
    """Handle a REJECTED verdict.

    The agent must regenerate the artifact from scratch with the rejection
    reason as context. Bounded: max 2 rejections before forcing escalation.

    Returns:
    {
        "status": "regenerate_dispatched" | "max_rejections_reached" | "escalated",
        "rejection_count": int,
        "regenerate_packet": dict | None,
    }
    """
    next_count = rejection_count + 1

    if next_count > max_rejections:
        record_review_event(repo_root, gate_id, "REJECTED", reason, next_count)
        return {
            "status": "max_rejections_reached",
            "rejection_count": next_count,
            "regenerate_packet": None,
        }

    governance_context = _load_governance_context(repo_root, gate_id)

    packet = build_rejection_packet(
        gate_id=gate_id,
        reason=reason,
        governance_context=governance_context,
    )

    # Write packet to disk
    packet_path = _write_review_packet(repo_root, gate_id, packet, next_count)

    # Record audit event
    record_review_event(repo_root, gate_id, "REJECTED", reason, next_count)

    return {
        "status": "regenerate_dispatched",
        "rejection_count": next_count,
        "regenerate_packet": packet,
    }


# ---------------------------------------------------------------------------
# build_rework_packet
# ---------------------------------------------------------------------------

def build_rework_packet(
    gate_id: str,
    feedback: str,
    specific_items: list[str],
    current_artifact: dict | None,
    governance_context: str,
) -> dict[str, Any]:
    """Build a rework packet for the agent.

    The packet tells the agent:
    - What gate artifact to fix
    - What specific items the user flagged
    - The user's verbatim feedback
    - The governance context (what the gate requires)
    """
    return {
        "schema_version": "signalos.rework_packet.v1",
        "type": "rework",
        "packet_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "gate_id": gate_id,
        "feedback": feedback,
        "items_to_fix": specific_items,
        "current_artifact": current_artifact,
        "governance": governance_context,
        "instruction": "Fix the following items. Keep everything else unchanged.",
    }


# ---------------------------------------------------------------------------
# build_rejection_packet
# ---------------------------------------------------------------------------

def build_rejection_packet(
    gate_id: str,
    reason: str,
    governance_context: str,
) -> dict[str, Any]:
    """Build a full regeneration packet after rejection.

    Returns a packet instructing the agent to regenerate from scratch.
    """
    return {
        "schema_version": "signalos.rejection_packet.v1",
        "type": "regenerate",
        "packet_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "gate_id": gate_id,
        "rejection_reason": reason,
        "governance": governance_context,
        "instruction": "The previous artifact was rejected. Regenerate from scratch.",
    }


# ---------------------------------------------------------------------------
# record_review_event
# ---------------------------------------------------------------------------

def record_review_event(
    repo_root: Path,
    gate_id: str,
    verdict: str,
    feedback: str,
    cycle: int,
) -> None:
    """Record the review event in the audit trail."""
    audit_path = repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    event = {
        "event": "gate_review",
        "gate_id": gate_id,
        "verdict": verdict,
        "feedback": feedback,
        "cycle": cycle,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_governance_context(repo_root: Path, gate_id: str) -> str:
    """Load governance context for a gate from the delivery state."""
    # Try loading from the generation manifest or delivery state
    manifest_path = repo_root / ".signalos" / "product" / "GENERATION_MANIFEST.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            return json.dumps(manifest.get("governance", {}), indent=2)
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: just return gate_id context
    return f"Gate {gate_id} governance requirements apply."


def _write_review_packet(
    repo_root: Path,
    gate_id: str,
    packet: dict,
    cycle: int,
) -> Path:
    """Write a review packet to the review directory."""
    review_dir = (
        repo_root / ".signalos" / "product" / "reviews" / gate_id / f"cycle-{cycle}"
    )
    review_dir.mkdir(parents=True, exist_ok=True)

    packet_type = packet.get("type", "review")
    filename = f"{packet_type}-packet.json"
    packet_path = review_dir / filename

    packet_path.write_text(
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return packet_path
