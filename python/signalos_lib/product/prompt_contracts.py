"""Prompt-contract validation for SignalOS agent seats.

The bundled agent prompts are executable contracts, not copy text.  This
module keeps the contract shape machine-checkable so success criteria,
evidence, forbidden rules, and escalation behavior cannot silently drift out
of the hired-agent surface.
"""

from __future__ import annotations

__all__ = [
    "AGENT_PROMPT_FILES",
    "REQUIRED_AGENT_PROMPT_SECTIONS",
    "validate_agent_prompt_directory",
    "validate_prompt_contract",
]

from pathlib import Path
from typing import Any


AGENT_PROMPT_FILES: tuple[str, ...] = (
    "onboarding.md",
    "brainstorm.md",
    "plan.md",
    "design.md",
    "build.md",
    "test.md",
    "review.md",
    "security.md",
    "release.md",
    "observability.md",
    "worktree-sync.md",
)


REQUIRED_AGENT_PROMPT_SECTIONS: tuple[str, ...] = (
    "Purpose",
    "Expertise frame",
    "Activates at",
    "Prerequisites",
    "Inputs",
    "Outputs",
    "Success criteria",
    "Evidence required",
    "Forbidden rules",
    "Repair/rework policy",
    "Refusal conditions",
    "Handoff",
    "Trust Tier ceiling",
)


def validate_prompt_contract(path: Path) -> dict[str, Any]:
    """Validate one agent prompt markdown file.

    A section passes when the file contains a level-2 markdown heading whose
    text starts with the required section name.  This allows existing headings
    such as ``## Handoff (who receives...)`` while still enforcing the
    required contract surface.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "valid": False,
            "path": str(path),
            "missing": list(REQUIRED_AGENT_PROMPT_SECTIONS),
            "error": str(exc),
        }

    headings = _level_two_headings(text)
    missing = [
        section for section in REQUIRED_AGENT_PROMPT_SECTIONS
        if not any(heading.lower().startswith(section.lower()) for heading in headings)
    ]
    return {
        "valid": not missing,
        "path": str(path),
        "missing": missing,
        "headings": headings,
    }


def validate_agent_prompt_directory(agent_dir: Path) -> dict[str, Any]:
    """Validate every canonical bundled agent prompt in *agent_dir*."""
    agent_dir = Path(agent_dir)
    results = [
        validate_prompt_contract(agent_dir / name)
        for name in AGENT_PROMPT_FILES
    ]
    missing_files = [
        result["path"] for result in results
        if result.get("error")
    ]
    invalid = [
        result for result in results
        if not result["valid"]
    ]
    return {
        "valid": not invalid,
        "agent_dir": str(agent_dir),
        "checked": len(results),
        "missing_files": missing_files,
        "invalid": invalid,
        "results": results,
    }


def _level_two_headings(text: str) -> list[str]:
    headings: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("## "):
            continue
        # Ignore deeper headings such as "### Example".
        if line.startswith("### "):
            continue
        headings.append(line[3:].strip())
    return headings
