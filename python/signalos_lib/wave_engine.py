"""Wave engine — state machine + INSPECT + scope-drift detection (M-W2).

Per WAVE-ENGINE-DESIGN §3.1 / §6 / §7. Owns the full G0→G5 lifecycle as
a single state machine:

    ENTRY → INSPECT → DECIDE → DISPATCH → AWAIT_USER_CONFIRM
          → SIGN → ADVANCE → (next gate's DISPATCH | COMPLETE)
                    ↓
                SCOPE_DRIFT → 4-WAY PROMPT → (a/b/c/d) → re-enter

This module covers M-W2 (state machine + INSPECT + scope-drift detection).
Per-gate agent dispatch (M-W3..M-W5) and translator-mode (M-W6) layer on
top: the engine's `dispatch()` returns a `fire-agent-Gn` action; later
milestones wire that action to actually load and invoke the agent file.

Scope-drift detection runs cheap heuristics first and, when ambiguous,
falls back to an LLM-judge hook. The hook is wired (call site exists)
but the LLM call itself is stubbed for M-W2 — heuristics-only verdict
ships today, LLM-judge plugs in via the `llm_judge` callable parameter.

Design ties:
  - §3.1 wave state machine — `WaveState` enum + `WaveEngine.transition`
  - §3.2 multi-project plumbing — every entry point takes `project_id`
  - §6 scope-drift detection — `detect_scope_drift`
  - §7 inspect-first / fast-forward — `inspect` returns per-gate
        signed status + artifact snippets so callers can fast-forward
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any, Callable


__all__ = [
    "WaveState",
    "GATE_ORDER",
    "inspect",
    "detect_scope_drift",
    "WaveEngine",
    "classify_user_reply",
    "AFFIRMATION_ALLOWLIST",
    "build_system_bubble",
]


# Gate order per WAVE-ENGINE-DESIGN §2. G4 is the build gate, dispatched
# in parallel by the existing orchestrator.run_wave. The engine treats
# all six the same way at the state-machine level.
GATE_ORDER: list[str] = ["G0", "G1", "G2", "G3", "G4", "G5"]


# Per-gate artifact paths (mirrors status._detect_gates). Centralised
# here so the engine doesn't have to duplicate the layout knowledge.
_GATE_ARTIFACT_PATHS: dict[str, list[tuple[str, ...]]] = {
    "G0": [("core", "governance", "Governance", "SOUL-DOCUMENT.md")],
    "G1": [
        ("core", "strategy", "BELIEF.md"),
        ("core", "strategy", "BELIEF_LITE.md"),
    ],
    "G2": [("core", "strategy", "EXPECTATION_MAP.md")],
    "G3": [("core", "strategy", "DESIGN_NOTE.md")],
    "G4": [("core", "execution", "TRUST_TIER.md")],
    "G5": [("core", "governance", "QUALITY_CHECK.md")],
}


class WaveState(str, Enum):
    """Wave-engine states per WAVE-ENGINE-DESIGN §3.1."""

    ENTRY = "ENTRY"
    INSPECT = "INSPECT"
    DECIDE = "DECIDE"
    DISPATCH = "DISPATCH"
    AWAIT_USER_CONFIRM = "AWAIT_USER_CONFIRM"
    SIGN = "SIGN"
    ADVANCE = "ADVANCE"
    SCOPE_DRIFT = "SCOPE_DRIFT"
    COMPLETE = "COMPLETE"


# Legal state transitions per §3.1. Used by `WaveEngine.transition` to
# fail closed on illegal moves — the engine never silently jumps states.
_LEGAL_TRANSITIONS: dict[WaveState, set[WaveState]] = {
    WaveState.ENTRY:              {WaveState.INSPECT},
    WaveState.INSPECT:            {WaveState.DECIDE, WaveState.SCOPE_DRIFT},
    WaveState.DECIDE:             {WaveState.DISPATCH, WaveState.COMPLETE},
    WaveState.DISPATCH:           {WaveState.AWAIT_USER_CONFIRM, WaveState.SIGN},
    WaveState.AWAIT_USER_CONFIRM: {WaveState.SIGN, WaveState.DISPATCH},
    WaveState.SIGN:               {WaveState.ADVANCE},
    WaveState.ADVANCE:            {WaveState.INSPECT, WaveState.COMPLETE},
    WaveState.SCOPE_DRIFT:        {WaveState.INSPECT, WaveState.ENTRY},
    WaveState.COMPLETE:           set(),
}


# ---------------------------------------------------------------------------
# INSPECT — read .signalos/ to discover existing artifacts (§7)
# ---------------------------------------------------------------------------

def _read_snippet(path: Path, max_chars: int = 240) -> str:
    """Return the first non-empty content of *path* trimmed to *max_chars*."""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    cleaned: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<!--") or stripped.startswith("---"):
            continue
        cleaned.append(stripped)
        if sum(len(s) for s in cleaned) > max_chars:
            break
    return " ".join(cleaned)[:max_chars]


def inspect(
    repo_root: Path,
    project_id: str = "default",
) -> dict[str, Any]:
    """Read the workspace and report what artifacts exist per gate.

    Per WAVE-ENGINE-DESIGN §7. Used by the engine at every wave entry to
    decide whether to fire a gate-agent or fast-forward past gates whose
    artifact already exists and is signed.

    With project_id == "default" (today's only value) the paths are
    workspace-root, matching `status._detect_gates`. When a future M
    exposes per-project scoping via the UI, the project_id will namespace
    artifacts under `core/governance/projects/<project_id>/...` (per §3.2).
    The plumbing is here so that switch doesn't require an engine rewrite.

    Returns:
        {
            "project_id": str,
            "gates": {"G0": bool, "G1": bool, ...},
            "artifacts": {
                "G0": {
                    "path": str | None,
                    "exists": bool,
                    "signed": bool,
                    "snippet": str,
                },
                ...
            },
            "next_gate": str | None,    # first unsigned gate in G0..G5
            "all_signed": bool,         # True iff every G0..G5 is signed
        }
    """
    from .status import get_wave_status

    status = get_wave_status(repo_root, project_id=project_id)
    gates = status.get("gates") or {}

    artifacts: dict[str, dict[str, Any]] = {}
    for gate in GATE_ORDER:
        signed = bool(gates.get(gate))
        path_obj: Path | None = None
        snippet = ""
        for parts in _GATE_ARTIFACT_PATHS.get(gate, []):
            candidate = repo_root.joinpath(*parts)
            if candidate.is_file():
                path_obj = candidate
                snippet = _read_snippet(candidate)
                break
        artifacts[gate] = {
            "path": str(path_obj) if path_obj else None,
            "exists": path_obj is not None,
            "signed": signed,
            "snippet": snippet,
        }

    next_gate = next((g for g in GATE_ORDER if not gates.get(g)), None)
    return {
        "project_id": project_id,
        "gates": {g: bool(gates.get(g)) for g in GATE_ORDER},
        "artifacts": artifacts,
        "next_gate": next_gate,
        "all_signed": next_gate is None,
    }


# ---------------------------------------------------------------------------
# Scope-drift detection (§6) — heuristics + LLM-judge fallback
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "build", "but", "by", "do",
    "for", "from", "have", "i", "in", "into", "is", "it", "make", "me",
    "my", "of", "on", "or", "our", "she", "so", "that", "the", "their",
    "this", "to", "want", "was", "we", "with", "you", "your", "would",
    "could", "should", "can", "will", "just", "some", "any", "all", "us",
    "them", "they", "he", "him", "her", "what", "when", "where", "why",
    "how", "if", "then", "than", "also",
}


def _tokenize(text: str) -> set[str]:
    """Lowercase word-tokens with stopwords removed. Returns a set."""
    if not text:
        return set()
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _summarise(text: str, max_chars: int = 80) -> str:
    """One-line summary of *text* for display in the 4-way drift prompt."""
    if not text:
        return "—"
    flat = re.sub(r"\s+", " ", text.strip())
    return flat[:max_chars]


def detect_scope_drift(
    repo_root: Path,
    user_request: str,
    project_id: str = "default",
    *,
    llm_judge: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Detect whether *user_request* drifts from the signed Soul.

    Per WAVE-ENGINE-DESIGN §6. Strategy:

      1. Cheap heuristic first — token overlap between user_request and
         the signed Soul body. High overlap → no drift. Zero overlap with
         high-confidence stakeholder/scope signals → drift.
      2. When the heuristic is ambiguous AND *llm_judge* is supplied,
         call it for a verdict. Otherwise return "ambiguous" so the
         caller can decide whether to ask the user explicitly or assume
         no drift (conservative default).
      3. When no signed Soul exists, never report drift — there's nothing
         to drift from yet; let G0 fire normally.

    The *llm_judge* callable contract:

        def llm_judge(soul_text: str, user_request: str) -> {
            "drifted": bool,
            "confidence": float,
            "reasoning": str,
        }

    With *llm_judge* omitted (the M-W2 default), drift is heuristics-only.
    M-W3+ plug in the real LLM-judge.

    Returns:
        {
            "drifted": bool,                   # True iff drift confirmed
            "confidence": float,               # 0.0-1.0
            "method": "no-soul" | "heuristic" | "llm-judged" | "ambiguous",
            "current_soul_summary": str,
            "new_request_summary": str,
            "signals": list[str],              # which heuristics fired
            "recommended_action": "keep" | "amend" | "new-project" | "ambiguous",
        }
    """
    inspection = inspect(repo_root, project_id=project_id)
    soul = inspection["artifacts"]["G0"]

    base = {
        "current_soul_summary": _summarise(soul["snippet"]),
        "new_request_summary": _summarise(user_request),
        "signals": [],
    }

    # No signed Soul → no drift possible (G0 hasn't fired yet).
    if not soul["signed"] or not soul["snippet"]:
        return {
            **base,
            "drifted": False,
            "confidence": 1.0,
            "method": "no-soul",
            "recommended_action": "keep",
        }

    soul_tokens = _tokenize(soul["snippet"])
    request_tokens = _tokenize(user_request)
    if not request_tokens:
        # Empty request — defer to caller, no drift signal.
        return {
            **base,
            "drifted": False,
            "confidence": 0.5,
            "method": "heuristic",
            "recommended_action": "keep",
            "signals": ["empty-request"],
        }

    overlap = soul_tokens & request_tokens
    overlap_ratio = len(overlap) / max(len(request_tokens), 1)
    signals: list[str] = []

    # Heuristic 1 — token overlap.
    if overlap_ratio >= 0.4:
        signals.append(f"token-overlap={overlap_ratio:.2f}")
        return {
            **base,
            "drifted": False,
            "confidence": min(1.0, 0.5 + overlap_ratio),
            "method": "heuristic",
            "recommended_action": "keep",
            "signals": signals,
        }

    # Heuristic 2 — stakeholder mismatch ("for me" vs "for the team").
    soul_lower = soul["snippet"].lower()
    request_lower = user_request.lower()
    stakeholder_signals: list[str] = []
    if any(phrase in soul_lower for phrase in ("for me", "personal", "myself", "for myself", "for my own")):
        if any(phrase in request_lower for phrase in (
            "team", "company", "customers", "clients", "users",
            "stakeholder", "everyone", "the public",
        )):
            stakeholder_signals.append("stakeholder-mismatch:personal→shared")
    if any(phrase in soul_lower for phrase in (
        "for the team", "for my team", "for clients", "for customers", "for users",
    )):
        if any(phrase in request_lower for phrase in (
            "just for me", "personal use only", "myself",
        )):
            stakeholder_signals.append("stakeholder-mismatch:shared→personal")
    signals.extend(stakeholder_signals)

    # Heuristic 3 — overlap is very low (<= 0.1) → likely a different domain.
    if overlap_ratio <= 0.1:
        signals.append(f"token-overlap-low={overlap_ratio:.2f}")
        # Strong drift signal — combine with stakeholder mismatch if any.
        drifted = True
        confidence = 0.8 if not stakeholder_signals else 0.9
        return {
            **base,
            "drifted": drifted,
            "confidence": confidence,
            "method": "heuristic",
            "recommended_action": "new-project" if stakeholder_signals else "amend",
            "signals": signals,
        }

    # Ambiguous zone: 0.1 < overlap < 0.4. Defer to LLM-judge if available.
    signals.append(f"token-overlap-ambiguous={overlap_ratio:.2f}")
    if llm_judge is None:
        return {
            **base,
            "drifted": False,  # conservative default — don't false-positive
            "confidence": 0.4,
            "method": "ambiguous",
            "recommended_action": "ambiguous",
            "signals": signals,
        }

    try:
        verdict = llm_judge(soul["snippet"], user_request)
    except Exception as exc:  # noqa: BLE001 — defensive; LLM calls can fail
        signals.append(f"llm-judge-failed:{type(exc).__name__}")
        return {
            **base,
            "drifted": False,
            "confidence": 0.3,
            "method": "ambiguous",
            "recommended_action": "ambiguous",
            "signals": signals,
        }

    drifted = bool(verdict.get("drifted"))
    confidence = float(verdict.get("confidence", 0.7))
    signals.append("llm-judge-resolved")
    return {
        **base,
        "drifted": drifted,
        "confidence": confidence,
        "method": "llm-judged",
        "recommended_action": "amend" if drifted else "keep",
        "signals": signals,
    }


# ---------------------------------------------------------------------------
# Affirmation classifier (§8) — M-W3 auto-sign trigger
# ---------------------------------------------------------------------------
#
# v1 is strict — an allowlist of unambiguous affirmation phrases per the
# design's "false-positive over false-negative" rule (a wrong "yes" silently
# signs and damages integrity; a wrong "no" just costs one extra turn).
# v2 (after the classifier has earned trust per design §13.3) lets an LLM
# judge from richer replies.

AFFIRMATION_ALLOWLIST: frozenset[str] = frozenset({
    "yes", "y", "yeah", "yep",
    "confirm", "confirmed",
    "approve", "approved",
    "looks good", "lgtm",
    "proceed", "go", "go ahead",
    "sign", "sign it",
    "ok", "okay",
    "ship it", "ship",
})

# Reply tokens that signal the user wants to refine, not sign.
_REFINEMENT_HINTS: frozenset[str] = frozenset({
    "change", "instead", "actually", "wait", "no", "not", "but", "rather",
    "different", "reword", "rewrite", "redo", "again", "tweak", "edit",
    "amend", "modify",
})


def classify_user_reply(reply: str) -> dict[str, Any]:
    """Categorise a user's chat reply as affirm / refine / question / other.

    Per WAVE-ENGINE-DESIGN §8 — the engine auto-signs the current gate
    when the reply is unambiguously affirmative. Anything else falls
    through to the chat layer for normal handling (refine the agent's
    output, answer a question, etc).

    Returns:
        {
            "kind": "affirm" | "refine" | "question" | "other",
            "matched_phrase": str | None,  # which allowlist phrase matched
            "raw": str,
        }
    """
    base = {"raw": reply, "matched_phrase": None}
    if not reply:
        return {**base, "kind": "other"}

    normalised = reply.strip().lower()
    # Strip terminal punctuation that wouldn't change intent.
    normalised_stripped = normalised.rstrip(".!?,;: ")

    # Question wins outright — never auto-sign on a "?" reply.
    if normalised.endswith("?"):
        return {**base, "kind": "question"}

    # Exact-match against the allowlist (case-insensitive).
    if normalised_stripped in AFFIRMATION_ALLOWLIST:
        return {**base, "kind": "affirm", "matched_phrase": normalised_stripped}

    # Multi-word allowlist phrases ("looks good", "ship it") match when
    # the reply starts with the phrase and the remainder is short padding.
    for phrase in AFFIRMATION_ALLOWLIST:
        if " " in phrase and normalised_stripped.startswith(phrase):
            remainder = normalised_stripped[len(phrase):].strip()
            if len(remainder) <= 12:
                return {**base, "kind": "affirm", "matched_phrase": phrase}

    # Refinement signal: any of the hint words present.
    tokens = set(re.findall(r"[a-z']+", normalised))
    if tokens & _REFINEMENT_HINTS:
        return {**base, "kind": "refine"}

    return {**base, "kind": "other"}


# ---------------------------------------------------------------------------
# System bubble builder (§5) — re-route + sign messages for the chat layer
# ---------------------------------------------------------------------------

_GATE_HUMAN_NAMES: dict[str, str] = {
    "G0": "Soul",
    "G1": "Belief",
    "G2": "Plan",
    "G3": "Design",
    "G4": "Build",
    "G5": "Ship",
}


def build_system_bubble(
    *,
    kind: str,
    gate: str | None = None,
    detail: str = "",
) -> dict[str, Any]:
    """Construct a structured system-kind chat bubble per §5.

    The engine returns these inside its action results so the chat layer
    can render a small "system" message ahead of the agent's reply
    ("Build isn't done yet — kicking off the build agent first.").

    *kind* values used today:
      - "reroute"      — engine is firing a prior gate's agent first
      - "sign-recorded"— gate was just signed; user-evidence captured
      - "scope-drift"  — render the 4-way drift prompt
      - "complete"     — all gates signed; wave finished

    Returns:
        {
            "kind": str,                  # one of the values above
            "gate": "G0".."G5" | None,
            "text": "<rendered user-facing one-liner>",
            "detail": "<optional longer explanation>",
        }
    """
    gate_name = _GATE_HUMAN_NAMES.get(gate or "", "")
    label = f"{gate_name} ({gate})" if gate and gate_name else (gate or "")

    if kind == "reroute":
        text = (
            f"{label} isn't signed yet — firing that agent first."
            if label else "Routing to the next gate's agent first."
        )
    elif kind == "sign-recorded":
        text = f"Captured your answer as {label} sign-off — saved to audit trail." \
            if label else "Sign-off captured — saved to audit trail."
    elif kind == "scope-drift":
        text = "This new request feels different from the signed Soul — see options."
    elif kind == "complete":
        text = "All gates signed — wave complete."
    else:
        text = detail or kind

    return {
        "kind": kind,
        "gate": gate,
        "text": text,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Wave engine — state holder + transitions (§3.1)
# ---------------------------------------------------------------------------

class WaveEngine:
    """Wave-engine state machine per WAVE-ENGINE-DESIGN §3.1.

    Holds wave state in memory. Persistence (writing state to disk so the
    engine survives a process restart) is deferred to a later milestone;
    today the engine is reconstructed per-turn from `inspect()` reading
    .signalos/.

    Usage sketch (callers wire this in M-W3+):

        engine = WaveEngine(repo_root, project_id="default")
        result = engine.begin(user_request="Build me a todo app")
        # result["action"] tells the caller what to do next.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        project_id: str = "default",
        session_id: str | None = None,
        llm_judge: Callable[[str, str], dict[str, Any]] | None = None,
    ):
        self.repo_root = repo_root
        self.project_id = project_id
        self.session_id = session_id
        self.llm_judge = llm_judge

        self.state: WaveState = WaveState.ENTRY
        self.current_gate: str | None = None
        self.last_inspection: dict[str, Any] | None = None
        self.last_drift: dict[str, Any] | None = None
        self.last_user_request: str | None = None
        self.history: list[tuple[WaveState, WaveState]] = []

    # -- state-machine primitive -------------------------------------------

    def transition(self, target: WaveState) -> None:
        """Move to *target* state. Fail closed if illegal per §3.1."""
        legal = _LEGAL_TRANSITIONS.get(self.state, set())
        if target not in legal:
            raise RuntimeError(
                f"Illegal wave-engine transition: {self.state.value} → "
                f"{target.value}. Legal next states: "
                f"{sorted(s.value for s in legal) or '∅ (terminal)'}"
            )
        self.history.append((self.state, target))
        self.state = target

    # -- high-level entry points -------------------------------------------

    def begin(self, user_request: str) -> dict[str, Any]:
        """ENTRY → INSPECT → DECIDE. Returns next action for the caller.

        Result shape:
            {
                "action": "fire-agent-Gn" | "complete" | "scope-drift-prompt",
                "current_gate": "G0".."G5" | None,
                "inspection": <full inspect() result>,
                "drift": <detect_scope_drift() result or None>,
                "agent": <load_agent() result> | None,  # M-W3 — loaded gate agent
                "system_bubble": <build_system_bubble() result> | None,
            }
        """
        self.last_user_request = user_request
        self.transition(WaveState.INSPECT)
        self.last_inspection = inspect(self.repo_root, project_id=self.project_id)

        # Check for scope drift against signed Soul (if any).
        drift = detect_scope_drift(
            self.repo_root, user_request,
            project_id=self.project_id, llm_judge=self.llm_judge,
        )
        self.last_drift = drift

        if drift["drifted"]:
            self.transition(WaveState.SCOPE_DRIFT)
            return {
                "action": "scope-drift-prompt",
                "current_gate": None,
                "inspection": self.last_inspection,
                "drift": drift,
                "agent": None,
                "system_bubble": build_system_bubble(kind="scope-drift"),
            }

        self.transition(WaveState.DECIDE)
        if self.last_inspection["all_signed"]:
            self.transition(WaveState.COMPLETE)
            return {
                "action": "complete",
                "current_gate": None,
                "inspection": self.last_inspection,
                "drift": drift,
                "agent": None,
                "system_bubble": build_system_bubble(kind="complete"),
            }

        next_gate = self.last_inspection["next_gate"]
        self.current_gate = next_gate
        self.transition(WaveState.DISPATCH)

        # M-W3: load the gate's agent .md as the LLM system prompt.
        from .agent_loader import load_agent
        agent = load_agent(next_gate)

        # Re-route bubble: when the user asked for a later gate (e.g.
        # "ship this") but a prior gate is unsigned, emit a system bubble
        # naming the re-route so the chat UI can show transparent status.
        # Today we don't yet parse the user_request for explicit intent;
        # the bubble fires whenever we fire any agent before all gates
        # are signed, so the user always sees what the engine is doing.
        system_bubble = build_system_bubble(kind="reroute", gate=next_gate)
        return {
            "action": f"fire-agent-{next_gate}",
            "current_gate": next_gate,
            "inspection": self.last_inspection,
            "drift": drift,
            "agent": agent,
            "system_bubble": system_bubble,
        }

    def resolve_scope_drift(self, choice: str) -> dict[str, Any]:
        """User picked one of the 4 drift-prompt options. Re-enter accordingly.

        *choice* is one of:
          - "a" / "amend"       — re-fire G0 in amend-mode (same project)
          - "b" / "new-parallel"— create new project_id in same workspace
          - "c" / "new-folder"  — new workspace folder (caller handles path)
          - "d" / "keep"        — treat new request as refinement; no change
        """
        if self.state is not WaveState.SCOPE_DRIFT:
            raise RuntimeError(
                f"resolve_scope_drift only valid in SCOPE_DRIFT state, "
                f"current state: {self.state.value}"
            )

        norm = (choice or "").strip().lower()
        if norm in {"a", "amend"}:
            self.transition(WaveState.INSPECT)
            return {"action": "fire-agent-G0", "mode": "amend",
                    "current_gate": "G0"}
        if norm in {"b", "new-parallel", "new-project-parallel"}:
            self.transition(WaveState.ENTRY)
            return {"action": "new-project-same-workspace",
                    "current_gate": None}
        if norm in {"c", "new-folder", "new-project-folder"}:
            self.transition(WaveState.ENTRY)
            return {"action": "new-project-new-workspace",
                    "current_gate": None}
        if norm in {"d", "keep", "same-project"}:
            self.transition(WaveState.INSPECT)
            return {"action": "treat-as-refinement",
                    "current_gate": self.current_gate}

        raise ValueError(
            f"Unknown scope-drift choice: {choice!r}. "
            "Expected one of: a/b/c/d, amend/new-parallel/new-folder/keep."
        )

    # -- gate sign / advance ------------------------------------------------

    def sign_current_gate(self, evidence: str) -> dict[str, Any]:
        """DISPATCH/AWAIT_USER_CONFIRM → SIGN → ADVANCE → next DISPATCH or COMPLETE.

        Records the gate sign in memory; the actual audit-trail write
        (and signature-block update in the artifact markdown) is owned
        by `sign.py` and remains the source of truth. The engine merely
        notes the transition so it can route to the next gate.

        Returns a result dict including a `system_bubble` field (§5)
        that the chat layer renders as a small "sign-off captured" or
        "wave complete" message.
        """
        if self.state not in (WaveState.DISPATCH, WaveState.AWAIT_USER_CONFIRM):
            raise RuntimeError(
                f"sign_current_gate only valid in DISPATCH or "
                f"AWAIT_USER_CONFIRM state, current: {self.state.value}"
            )
        if not self.current_gate:
            raise RuntimeError("No current_gate set; nothing to sign.")

        # If we were in DISPATCH, jump to SIGN via AWAIT_USER_CONFIRM
        # (legal per §3.1 — DISPATCH → AWAIT_USER_CONFIRM → SIGN).
        if self.state is WaveState.DISPATCH:
            self.transition(WaveState.AWAIT_USER_CONFIRM)
        self.transition(WaveState.SIGN)
        signed_gate = self.current_gate
        self.transition(WaveState.ADVANCE)

        sign_bubble = build_system_bubble(
            kind="sign-recorded", gate=signed_gate,
            detail=f"evidence: {evidence!r}",
        )

        # Determine next unsigned gate (caller should pass updated inspection).
        idx = GATE_ORDER.index(signed_gate)
        next_gates = GATE_ORDER[idx + 1:]
        if not next_gates:
            self.transition(WaveState.COMPLETE)
            return {
                "action": "complete",
                "signed_gate": signed_gate,
                "current_gate": None,
                "evidence": evidence,
                "system_bubble": sign_bubble,
                "complete_bubble": build_system_bubble(kind="complete"),
            }

        next_gate = next_gates[0]
        self.current_gate = next_gate
        self.transition(WaveState.INSPECT)
        return {
            "action": f"fire-agent-{next_gate}",
            "signed_gate": signed_gate,
            "current_gate": next_gate,
            "evidence": evidence,
            "system_bubble": sign_bubble,
        }

    # -- violation-confirmation flow (M-W7) -------------------------------

    def request_violation_confirmation(
        self,
        *,
        violation_kind: str,
        findings: list[str] | None = None,
        gate: str | None = None,
    ) -> dict[str, Any]:
        """Surface a 3-way violation prompt (§8) for the chat layer.

        Wraps refusal_taxonomy.build_violation_prompt with a
        wave-engine system bubble so the chat layer's rendering path
        is the same as the rest of the engine's transparent-status
        messages.
        """
        from .refusal_taxonomy import build_violation_prompt

        prompt = build_violation_prompt(
            violation_kind=violation_kind,
            findings=findings,
            gate=gate or self.current_gate,
        )
        bubble = build_system_bubble(
            kind="reroute", gate=gate or self.current_gate,
            detail=prompt["text"],
        )
        return {"prompt": prompt, "system_bubble": bubble}

    def confirm_violation(
        self,
        *,
        violation_kind: str,
        choice: str,
        user_reply: str,
        findings: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record the user's response to a violation prompt (§8).

        Returns the audit-trail entry the caller appends to
        .signalos/AUDIT_TRAIL.jsonl. The engine never logs the
        confirmation itself — the audit-write path is owned by the
        orchestrator's _append_audit_entry helper. This separation
        keeps the engine pure and the audit writer the single source
        of truth for trail file shape.
        """
        from .refusal_taxonomy import record_violation_confirmation

        entry = record_violation_confirmation(
            violation_kind=violation_kind,
            choice=choice,
            user_reply=user_reply,
            gate=self.current_gate,
            findings=findings,
        )

        # System bubble describing what just happened so the chat layer
        # shows the user that their choice was captured.
        if entry["choice"] == "fix-now":
            text = "Holding ship — re-running after fixes."
        elif entry["choice"] == "defer":
            text = "Deferring to next wave — tracked in backlog, shipped as-is."
        else:  # override-with-log
            text = (
                "Override recorded — shipping with violation logged "
                "in the audit trail."
            )
        bubble = build_system_bubble(
            kind="sign-recorded", gate=self.current_gate, detail=text,
        )
        bubble["text"] = text
        return {"audit_entry": entry, "system_bubble": bubble}

    # -- translator-mode (M-W6) -------------------------------------------

    def translate_external(
        self,
        artifact: str,
        *,
        gate: str | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        """Ingest an external artifact for translator-mode (§7).

        When the user supplies an artifact in non-SignalOS format
        (markdown belief doc, Figma URL, PDF brief, .docx requirements
        doc), the engine extracts plain text and returns it alongside
        a system bubble so the chat layer can show "translating your
        external doc into the SignalOS format".

        The wave-engine caller then passes the returned `translation`
        into the gate-agent invocation as additional context, marked
        `mode=translator`. The agent emits a SignalOS-format artifact;
        the user confirms; `handle_user_reply("yes")` auto-signs.

        Returns:
            {
                "translation": <translator.translate() result>,
                "gate": gate or self.current_gate,
                "system_bubble": <system bubble describing the action>,
            }
        """
        # Defer import — translator is a sibling module; keep wave_engine
        # importable even when the package layout reshuffles.
        from .translator import translate

        translation = translate(artifact, max_chars=max_chars)
        target_gate = gate or self.current_gate
        fmt = translation.get("format", "unknown")

        if translation.get("supported"):
            detail = (
                f"Translating {fmt!r} input into the SignalOS format "
                f"for {target_gate or 'the next gate'}."
            )
            bubble = build_system_bubble(
                kind="reroute", gate=target_gate, detail=detail,
            )
        else:
            hint = translation.get("install_hint") or translation.get("error") or "see translator error"
            detail = (
                f"Cannot translate {fmt!r} input automatically: {hint}. "
                "Either install the missing dependency or paste the "
                "content directly into chat."
            )
            bubble = build_system_bubble(
                kind="reroute", gate=target_gate, detail=detail,
            )

        return {
            "translation": translation,
            "gate": target_gate,
            "system_bubble": bubble,
        }

    # -- G5 handoff (M-W5) -------------------------------------------------

    def run_g5_handoff(
        self,
        wave_id: str,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Fire the post-G5 automation: the M4 auto-commit of wave output.

        Per WAVE-ENGINE-DESIGN §2 — G5 is the ship gate. The engine
        integrates with the M4 git-automation already in
        `orchestrator._auto_commit_wave` so a G5 sign triggers the
        local commit of the wave's output as a clean reviewable change.
        Push is intentionally manual (per the harness's "user owns
        hard-to-reverse actions" rule); the engine surfaces a follow-up
        bubble suggesting the user push when ready.

        Idempotent — if the workspace is already clean (nothing to
        commit) the underlying helper returns
        `{"status": "skipped", "reason": "clean-tree"}`. If git fails
        (pre-commit hook reject, etc.) the wave is NOT failed retroactively;
        the engine returns the failure for the caller to surface.
        """
        # Defer import so wave_engine remains independent of the orchestrator
        # at module-load time. (Avoids circular-import surprises.)
        from .orchestrator import _auto_commit_wave

        outcome = _auto_commit_wave(self.repo_root, wave_id, summary)

        if outcome.get("status") == "committed":
            bubble = build_system_bubble(
                kind="complete", gate="G5",
                detail=(
                    f"Wave {wave_id} auto-committed locally — review with "
                    "`git log -1`, then push when ready."
                ),
            )
        elif outcome.get("status") == "skipped":
            bubble = build_system_bubble(
                kind="complete", gate="G5",
                detail=f"Wave {wave_id} complete; nothing new to commit.",
            )
        else:
            bubble = build_system_bubble(
                kind="complete", gate="G5",
                detail=(
                    f"Wave {wave_id} complete but auto-commit failed: "
                    f"{outcome.get('reason', 'unknown')}. Address and "
                    "commit manually before pushing."
                ),
            )
        return {"commit_outcome": outcome, "system_bubble": bubble}

    # -- user reply interpretation (M-W3 auto-sign) ------------------------

    def handle_user_reply(self, reply: str) -> dict[str, Any]:
        """Interpret a user chat reply and auto-route per WAVE-ENGINE-DESIGN §8.

        - Affirmative reply (yes/confirm/approve/...) → auto-sign current
          gate, return the sign result so the caller can ship the audit
          entry + advance to the next gate's agent.
        - Refinement reply (change/instead/rewrite/...) → return
          {action: "refine"} so the caller re-invokes the same agent with
          the refinement as input.
        - Question reply (ends in "?") → return {action: "answer-question"}
          so the caller answers conversationally without advancing.
        - Anything else → return {action: "ambiguous"} so the chat layer
          can ask for explicit confirmation rather than guessing.

        This is the auto-sign trigger. The engine never silently signs
        on ambiguity — the design's "false-positive over false-negative"
        rule (§8) is the integrity guarantee.
        """
        classification = classify_user_reply(reply)
        kind = classification["kind"]

        if kind == "affirm":
            if self.state not in (WaveState.DISPATCH, WaveState.AWAIT_USER_CONFIRM):
                # User said "yes" when the engine wasn't waiting for a sign —
                # treat as ambiguous so the chat layer asks what they meant.
                return {
                    "action": "ambiguous",
                    "reason": "affirmation outside DISPATCH/AWAIT_USER_CONFIRM",
                    "classification": classification,
                }
            sign_result = self.sign_current_gate(evidence=reply)
            return {
                **sign_result,
                "auto_signed": True,
                "classification": classification,
            }

        if kind == "question":
            return {
                "action": "answer-question",
                "classification": classification,
            }
        if kind == "refine":
            return {
                "action": "refine",
                "current_gate": self.current_gate,
                "classification": classification,
            }
        return {
            "action": "ambiguous",
            "classification": classification,
        }
