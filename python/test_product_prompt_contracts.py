"""Tests for SignalOS agent prompt contract enforcement."""

from __future__ import annotations

from pathlib import Path

from signalos_lib.product.prompt_contracts import (
    AGENT_PROMPT_FILES,
    REQUIRED_AGENT_PROMPT_SECTIONS,
    validate_agent_prompt_directory,
    validate_prompt_contract,
)


AGENTS_DIR = (
    Path(__file__).resolve().parent
    / "signalos_lib"
    / "_bundle"
    / "core"
    / "execution"
    / "agents"
)

STRATEGY_TEMPLATES_DIR = (
    Path(__file__).resolve().parent
    / "signalos_lib"
    / "_bundle"
    / "core"
    / "strategy"
    / "Templates"
)


def test_bundled_agent_prompts_have_required_contract_sections() -> None:
    result = validate_agent_prompt_directory(AGENTS_DIR)
    assert result["checked"] == len(AGENT_PROMPT_FILES)
    assert result["valid"] is True, result["invalid"]


def test_missing_success_criteria_fails_contract(tmp_path: Path) -> None:
    path = tmp_path / "build.md"
    path.write_text(
        "\n".join(
            f"## {section}"
            for section in REQUIRED_AGENT_PROMPT_SECTIONS
            if section != "Success criteria"
        ),
        encoding="utf-8",
    )

    result = validate_prompt_contract(path)

    assert result["valid"] is False
    assert result["missing"] == ["Success criteria"]


def test_heading_suffixes_are_allowed(tmp_path: Path) -> None:
    path = tmp_path / "build.md"
    path.write_text(
        "\n".join(
            f"## {section} (details)"
            for section in REQUIRED_AGENT_PROMPT_SECTIONS
        ),
        encoding="utf-8",
    )

    result = validate_prompt_contract(path)

    assert result["valid"] is True


def _card(name: str) -> str:
    return (AGENTS_DIR / name).read_text(encoding="utf-8")


def test_onboarding_card_treats_greenfield_as_first_class() -> None:
    # Regression (funded canary): the G0 card required a stakeholder
    # transcript that cannot exist in a greenfield run while also saying
    # "Prerequisites: None", and kept greenfield in a parenthetical -- a
    # literal model refused the whole delivery as "out of scope".
    text = _card("onboarding.md")
    assert "or a greenfield product brief" in text
    assert "in scope, not a refusal reason" in text
    # The transcript is a conditional input, never a precondition.
    assert "At least one stakeholder transcript filed" not in text
    assert "never a precondition" in text


def test_belief_carries_requirement_traceability_home() -> None:
    # Regression (funded canary, OA-14): the driver's requirement-trace check
    # scans G1's artifacts (BELIEF.md + ROLE_ACTIVATION_CARD.md) for every
    # REQ-* id the brief enumerates, but neither the belief template nor the
    # onboarding card ever told the model to enumerate them -- and the card's
    # "deliberately small" instruction actively discouraged it. G1 has no
    # natural requirement home the way G0/G2/G3 do (SURFACE_INVENTORY /
    # EXPECTATION_MAP / ACCEPTANCE_CRITERIA), so a literal model wove in only
    # the cross-cutting REQs and dropped the feature-CRUD ids. Give the belief
    # a template-sanctioned traceability block and direct onboarding to fill it.
    belief_template = (STRATEGY_TEMPLATES_DIR / "belief-template.md").read_text(
        encoding="utf-8"
    )
    assert "Requirements committed (traceability)" in belief_template
    assert "`REQ-*`" in belief_template

    onboarding = _card("onboarding.md")
    assert "Requirements committed" in onboarding
    assert "traceable from Gate 1" in onboarding
    # Gate 0 is the requirements register (the deterministic anchor for the
    # driver's cumulative requirement-trace check): onboarding must register
    # every REQ-* id from the brief up front.
    assert "Gate 0 requirements register" in onboarding
    assert "register **every** one here" in onboarding


def test_plan_card_forbids_scaffold_toolchain_tasks() -> None:
    # FAIRNESS FIX (run 13): the selected stack's toolchain (build tool + test
    # runner + entry point + dependencies) is materialized and installed BEFORE
    # the Build gate, so the plan must never emit a scaffold/setup/toolchain
    # task -- a Build seat that re-stands-up the toolchain burns its whole
    # budget on infrastructure the harness already owns. Every buildable task
    # must be a product-feature vertical slice with its own failing acceptance
    # test. The instruction stays stack-agnostic (never names a framework).
    text = _card("plan.md")
    assert "toolchain is already provisioned" in text
    assert "MUST NOT emit any task whose job is to scaffold" in text
    # a matching Forbidden rule makes it enforceable, not just advisory prose
    assert "Do not emit any scaffold, setup, toolchain" in text
    assert (
        "the stack's build tool, test runner, entry point, and dependencies are "
        "already provisioned and installed before the Build gate"
    ) in text
    # stays stack-agnostic -- does not hardcode a specific framework id
    assert "react-vite" not in text.lower()


def test_observability_card_does_not_require_release_signal_log() -> None:
    # Same unsatisfiable-prerequisite class at G5: the signal-log is opened
    # by a Release agent that a pre-deploy governed delivery never runs.
    text = _card("observability.md")
    assert (
        "- `Governance/signal-logs/wave-{N}-signal-log.md` opened by Release agent"
        not in text
    )
    assert "Do not refuse for a missing signal-log" in text


def test_seat_cards_accept_local_release_changesets() -> None:
    # "Build PR exists" is unsatisfiable when the governed delivery releases
    # to a local origin (no GitHub PR); the prerequisite names both forms.
    for name in ("test.md", "review.md", "security.md"):
        text = _card(name)
        assert "governed local delivery" in text, name
    assert "- Build PR exists" not in _card("security.md")
    assert "- Build PR exists with Build agent's HAND entry logged" not in _card("test.md")
    assert "- Build PR with Test agent's HAND entry logged" not in _card("review.md")
