"""Formal mirror between PLAN_SCHEMA.json and plan.py's hand-written validator
(Wave 0.5).

`plan.py` validates task documents with a hand-written `validate_tasks` rather
than loading the JSON Schema (the codebase is intentionally stdlib-only, no
`jsonschema` dependency). That is fine -- but the schema file and the validator
can silently drift apart. This test makes the mirror *formal*: if the schema's
enums stop matching the validator's constants, CI fails, so the two are kept in
lockstep by machine, not by memory.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import plan

_SCHEMA = (
    Path(plan.__file__).resolve().parent
    / "_bundle" / "core" / "execution" / "plan" / "PLAN_SCHEMA.json"
)


class PlanSchemaMirrorTests(unittest.TestCase):
    def _task_props(self) -> dict:
        schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
        return schema["definitions"]["Task"]["properties"]

    def test_schema_file_exists_and_parses(self):
        self.assertTrue(_SCHEMA.is_file(), f"schema not found: {_SCHEMA}")

    def test_status_enum_matches_validator(self):
        status_enum = set(self._task_props()["status"]["enum"])
        self.assertEqual(
            status_enum, set(plan.VALID_STATUSES),
            "PLAN_SCHEMA.json status enum drifted from plan.VALID_STATUSES",
        )

    def test_tier_enum_matches_validator(self):
        tier_enum = set(self._task_props()["tier"]["enum"])
        self.assertEqual(
            tier_enum, set(plan.VALID_TIERS),
            "PLAN_SCHEMA.json tier enum drifted from plan.VALID_TIERS",
        )


if __name__ == "__main__":
    unittest.main()
