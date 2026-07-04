# test_intent_entity_identifiers.py
# #29: the deterministic extractor must never emit an entity name that is not a
# valid TS identifier. #24b freezes types.ts from these entities, so a garbled
# name (e.g. "Category;RunningTotal;Per-categoryBreakdown;MarkReimbursed" — a
# single blob from an un-split semicolon list) would freeze a syntactically
# invalid interface. The LLM refine pass normally cleans entities, but offline /
# billing-blocked runs rely on this deterministic floor.
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.intent import _to_pascal_case, extract_product_intent

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _entity_names(intent: dict) -> list[str]:
    out = []
    for e in intent.get("entities", []) or []:
        out.append(e if isinstance(e, str) else e.get("name"))
    return [n for n in out if n]


class TestEntityIdentifiers(unittest.TestCase):
    GARBLED_PROMPTS = [
        "A personal expense tracker: add expenses with a title, amount, and "
        "category; running total; per-category breakdown; mark reimbursed.",
        "Track invoices; clients; line-items; and payments due.",
        "A CRM with contacts, deals; pipelines/stages; and 2 dashboards.",
    ]

    def test_extracted_entity_names_are_valid_identifiers(self) -> None:
        for prompt in self.GARBLED_PROMPTS:
            intent = extract_product_intent(prompt)
            for name in _entity_names(intent):
                self.assertRegex(
                    name, _IDENT_RE,
                    msg=f"invalid TS identifier {name!r} from prompt {prompt!r}",
                )

    def test_semicolon_list_is_split_not_merged(self) -> None:
        intent = extract_product_intent(self.GARBLED_PROMPTS[0])
        names = _entity_names(intent)
        # The old bug produced ONE monster name containing every concept.
        self.assertFalse(
            any(";" in n for n in names), names,
        )
        # Category is recovered as its own entity (not glued to the tail).
        self.assertIn("Category", names)

    def test_to_pascal_case_strips_punctuation(self) -> None:
        self.assertEqual(_to_pascal_case("category; running total"), "CategoryRunningTotal")
        self.assertEqual(_to_pascal_case("per-category breakdown"), "PerCategoryBreakdown")
        self.assertEqual(_to_pascal_case("lab results"), "LabResult")

    def test_to_pascal_case_prefixes_leading_digit(self) -> None:
        out = _to_pascal_case("2fa tokens")
        self.assertRegex(out, _IDENT_RE)
        self.assertFalse(out[:1].isdigit())

    def test_frozen_contract_from_extracted_entities_is_valid_ts(self) -> None:
        # The #24b freeze renders types.ts directly from these entities; every
        # emitted `export interface <Name>` must be a valid identifier.
        from signalos_lib.product.agent_dispatch import _render_types

        intent = extract_product_intent(self.GARBLED_PROMPTS[0])
        entities = [
            {"name": n, "fields": ["id", "name"]} for n in _entity_names(intent)
        ]
        src = _render_types(entities)
        for m in re.finditer(r"export interface (\S+) \{", src):
            self.assertRegex(m.group(1), _IDENT_RE, msg=src)


if __name__ == "__main__":
    unittest.main()
