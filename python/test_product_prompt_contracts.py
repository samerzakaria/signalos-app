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
