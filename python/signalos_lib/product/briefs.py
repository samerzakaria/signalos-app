"""Plain-words gate briefs (Wave 1.8).

Every gate presents exactly four plain-language fields -- what you are signing /
what changes after / the one risk / the question worth asking -- authored by a
*critic* (a different agent, ideally a different vendor, than the one that
produced the artifact). This module owns the brief *contract* and its
enforcement; the LLM that fills the fields is the runtime author. A brief that
fails this contract must not reach the founder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REQUIRED_FIELDS = (
    "what_you_are_signing",
    "what_changes_after",
    "the_one_risk",
    "question_worth_asking",
)


@dataclass
class BriefProvenance:
    author_agent: str = ""     # agent that produced the artifact
    author_model: str = ""
    reviewer_agent: str = ""   # critic agent that authored this brief
    reviewer_model: str = ""
    artifact: str = ""


@dataclass
class Brief:
    what_you_are_signing: str
    what_changes_after: str
    the_one_risk: str
    question_worth_asking: str
    provenance: BriefProvenance = field(default_factory=BriefProvenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "what_you_are_signing": self.what_you_are_signing,
            "what_changes_after": self.what_changes_after,
            "the_one_risk": self.the_one_risk,
            "question_worth_asking": self.question_worth_asking,
            "provenance": vars(self.provenance),
        }


def validate_brief(brief: Brief) -> list[str]:
    """Return contract violations; empty means the brief may reach the founder.

    Enforces: all four plain-words fields present, and critic independence --
    the brief's author (reviewer) must differ from the artifact's author, and,
    when both models are known, be a different vendor (ties to Wave 1.4)."""
    problems: list[str] = []
    for name in REQUIRED_FIELDS:
        if not str(getattr(brief, name, "")).strip():
            problems.append(f"brief missing '{name}'")

    p = brief.provenance
    if p.author_agent and p.reviewer_agent and p.author_agent == p.reviewer_agent:
        problems.append(
            "brief author must differ from the artifact author (critic independence)")

    if p.author_model and p.reviewer_model:
        from ..second_opinion import vendor_of
        av, rv = vendor_of(p.author_model), vendor_of(p.reviewer_model)
        if av != "unknown" and av == rv:
            problems.append(
                "brief should be authored by a different vendor than the artifact")
    return problems


def require_valid_brief(brief: Brief) -> None:
    """Fail-closed: raise unless the brief satisfies the contract."""
    problems = validate_brief(brief)
    if problems:
        raise ValueError("invalid gate brief: " + "; ".join(problems))


_BRIEF_INSTRUCTION = (
    "You are the Critic. Read the artifact and write a plain-words gate brief as "
    "STRICT JSON with exactly these keys: what_you_are_signing, what_changes_after, "
    "the_one_risk, question_worth_asking. No acronyms the founder wouldn't know. "
    "Return only the JSON object.\n\nARTIFACT:\n"
)


def build_brief_prompt(artifact_content: str) -> str:
    """The instruction handed to the critic model to author a brief."""
    return _BRIEF_INSTRUCTION + (artifact_content or "")


def _parse_brief_fields(raw: str) -> dict[str, str]:
    """Parse the critic's JSON reply, tolerating markdown code fences."""
    import json
    import re
    text = (raw or "").strip()
    fenced = re.search(r"\{.*\}", text, re.DOTALL)
    if fenced:
        text = fenced.group(0)
    try:
        data = json.loads(text)
    except Exception:
        data = {}
    return {k: str(data.get(k, "")).strip() for k in REQUIRED_FIELDS} if isinstance(data, dict) else {
        k: "" for k in REQUIRED_FIELDS
    }


def author_brief(
    artifact_content: str,
    critic_chat: Any,
    *,
    author_agent: str = "",
    author_model: str = "",
    reviewer_agent: str = "Critic",
    reviewer_model: str = "",
    artifact: str = "",
) -> Brief:
    """Have a critic model author a 4-field brief for an artifact.

    ``critic_chat`` is any object with a ``.chat(messages)`` method (the same
    adapter shape the gate agents use) -- a real LLM in production, a stub in
    tests. The critic should be a different agent/vendor than the artifact's
    author; ``validate_brief`` enforces that on the result.
    """
    prompt = build_brief_prompt(artifact_content)
    resp = critic_chat.chat([{"role": "user", "content": prompt}])
    fields = _parse_brief_fields(getattr(resp, "content", "") or "")
    return Brief(
        what_you_are_signing=fields["what_you_are_signing"],
        what_changes_after=fields["what_changes_after"],
        the_one_risk=fields["the_one_risk"],
        question_worth_asking=fields["question_worth_asking"],
        provenance=BriefProvenance(
            author_agent=author_agent, author_model=author_model,
            reviewer_agent=reviewer_agent, reviewer_model=reviewer_model,
            artifact=artifact,
        ),
    )
