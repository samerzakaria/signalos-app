"""Plain-words gate brief contract (Wave 1.8)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.briefs import (
    Brief, BriefProvenance, validate_brief, require_valid_brief,
    author_brief, build_brief_prompt,
)


class _Resp:
    def __init__(self, content):
        self.content = content


class _StubCritic:
    """Stands in for a real LLM critic; returns a fixed 4-field brief as JSON."""
    def __init__(self, content):
        self._content = content

    def chat(self, messages):
        return _Resp(self._content)


def _good_brief() -> Brief:
    return Brief(
        what_you_are_signing="You are approving the product's core purpose.",
        what_changes_after="The build plan and budget commit to this direction.",
        the_one_risk="If the purpose is too broad, scope will balloon.",
        question_worth_asking="Is this the single most important outcome?",
        provenance=BriefProvenance(
            author_agent="Product Strategist", author_model="anthropic/claude",
            reviewer_agent="Critic", reviewer_model="openai/gpt-4o",
            artifact="SOUL-DOCUMENT.md",
        ),
    )


class BriefContractTests(unittest.TestCase):
    def test_complete_independent_brief_passes(self):
        self.assertEqual(validate_brief(_good_brief()), [])

    def test_missing_field_is_flagged(self):
        b = _good_brief()
        b.the_one_risk = "  "
        problems = validate_brief(b)
        self.assertTrue(any("the_one_risk" in p for p in problems))

    def test_same_agent_author_and_reviewer_is_flagged(self):
        b = _good_brief()
        b.provenance.reviewer_agent = b.provenance.author_agent
        problems = validate_brief(b)
        self.assertTrue(any("critic independence" in p for p in problems))

    def test_same_vendor_author_and_reviewer_is_flagged(self):
        b = _good_brief()
        b.provenance.reviewer_model = "anthropic/claude-2"  # same vendor as author
        problems = validate_brief(b)
        self.assertTrue(any("different vendor" in p for p in problems))

    def test_require_valid_raises_on_bad_brief(self):
        b = _good_brief()
        b.what_you_are_signing = ""
        with self.assertRaises(ValueError):
            require_valid_brief(b)

    def test_require_valid_passes_good_brief(self):
        require_valid_brief(_good_brief())  # must not raise


class BriefAuthoringTests(unittest.TestCase):
    def test_authoring_pipeline_parses_a_valid_brief(self):
        critic = _StubCritic(
            '{"what_you_are_signing": "the core purpose", '
            '"what_changes_after": "the build commits", '
            '"the_one_risk": "scope too broad", '
            '"question_worth_asking": "is this the key outcome?"}'
        )
        brief = author_brief(
            "artifact text here", critic,
            author_agent="Product Strategist", author_model="anthropic/claude",
            reviewer_agent="Critic", reviewer_model="openai/gpt-4o",
            artifact="SOUL-DOCUMENT.md",
        )
        self.assertEqual(validate_brief(brief), [])
        self.assertEqual(brief.the_one_risk, "scope too broad")

    def test_authoring_tolerates_markdown_fenced_json(self):
        critic = _StubCritic(
            '```json\n{"what_you_are_signing":"a","what_changes_after":"b",'
            '"the_one_risk":"c","question_worth_asking":"d"}\n```'
        )
        brief = author_brief("x", critic, author_model="anthropic/claude",
                             reviewer_model="openai/gpt-4o")
        self.assertEqual(brief.what_you_are_signing, "a")

    def test_authored_brief_missing_fields_fails_the_contract(self):
        critic = _StubCritic('{"what_you_are_signing": "only this one"}')
        brief = author_brief("x", critic)
        self.assertTrue(validate_brief(brief))  # incomplete -> contract violations

    def test_prompt_includes_the_artifact(self):
        self.assertIn("ARTIFACT", build_brief_prompt("hello"))
        self.assertIn("hello", build_brief_prompt("hello"))


if __name__ == "__main__":
    unittest.main()
