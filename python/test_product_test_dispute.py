"""Test-dispute escape valve: a deadlocked build task is diagnosed (deterministic
health, then a fresh-context second-opinion classify) rather than silently
blamed on the code -- and a broken test is recorded as a dispute, never edited."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.subagent_build import Task, _diagnose_deadlock  # noqa: E402
from signalos_lib.product.test_dispute import (  # noqa: E402
    arbiter_messages,
    deterministic_test_health,
    parse_arbiter_verdict,
    record_dispute,
)

_ALWAYS_FAIL = (
    "import { describe, it, expect } from 'vitest';\n"
    "describe('T1', () => {\n"
    "  it('a', () => { expect(true).toBe(false); });\n"
    "  it('b', () => { expect(true).toBe(false); });\n"
    "});\n"
)
_REAL = (
    "import { describe, it, expect } from 'vitest';\n"
    "import { add } from '../../../../../src/math';\n"
    "describe('T1', () => {\n"
    "  it('adds', () => { expect(add(2, 3)).toBe(5); });\n"
    "  it('adds neg', () => { expect(add(-1, 1)).toBe(0); });\n"
    "});\n"
)


class TestDeterministicHealth(unittest.TestCase):
    def test_literal_always_fail_is_broken(self):
        h = deterministic_test_health(_ALWAYS_FAIL, "")
        self.assertTrue(h["broken"])
        self.assertEqual(h["confidence"], "high")

    def test_real_assertions_not_broken(self):
        h = deterministic_test_health(_REAL, "Cannot find module '../../../../../src/math'")
        self.assertFalse(h["broken"])   # impl gap, not a broken test

    def test_cases_without_assertions_broken(self):
        src = "describe('x', () => { it('todo', () => {}); it('todo2', () => {}); });"
        self.assertTrue(deterministic_test_health(src, "")["broken"])

    def test_no_suite_collected_broken(self):
        self.assertTrue(deterministic_test_health(_REAL, "Error: No test suite found in file")["broken"])

    def test_commented_stub_not_counted_as_assertion(self):
        # An always-fail behind a comment is not active -> the (empty) suite is
        # flagged for having no active assertions, still broken, still not a
        # false 'passable' read.
        src = "it('a', () => {\n // expect(true).toBe(false);\n});"
        self.assertTrue(deterministic_test_health(src, "")["broken"])

    def test_equal_literals_are_not_always_fail(self):
        # expect(1).toBe(1) is a tautology (passes), NOT an always-fail; a suite
        # that also has a real assertion is not 'broken'.
        src = ("import {expect,it} from 'vitest';\n"
               "it('a', () => { expect(1).toBe(1); expect(sum([1,2])).toBe(3); });")
        self.assertFalse(deterministic_test_health(src, "")["broken"])


class TestArbiterVerdict(unittest.TestCase):
    def test_parses_clean_json(self):
        v = parse_arbiter_verdict('{"test_broken": true, "reason": "asserts 25 as a string"}')
        self.assertTrue(v["test_broken"])
        self.assertIn("string", v["reason"])

    def test_extracts_json_from_prose(self):
        v = parse_arbiter_verdict('Here is my verdict:\n{"test_broken": false, "reason": "impl missing"}\nDone.')
        self.assertFalse(v["test_broken"])

    def test_fails_closed_on_garbage(self):
        for bad in ("", "not json at all", "{broken", "the test is broken"):
            self.assertFalse(parse_arbiter_verdict(bad)["test_broken"],
                             f"garbage must fail-closed: {bad!r}")

    def test_messages_carry_test_not_transcript(self):
        sysm, usrm = arbiter_messages("T1 add", _REAL, "AssertionError: expected 5", "// src/math.ts")
        self.assertIn("expect(add(2, 3))", usrm)   # the test is present
        self.assertIn("test_broken", usrm)          # asks for the verdict
        self.assertIn("impartial", sysm.lower())


class TestDiagnoseDeadlock(unittest.TestCase):
    def _repo_with_test(self, body: str) -> "tuple[Path, Task]":
        d = Path(tempfile.mkdtemp())
        rel = "core/execution/tests/skeletons/wave-1/T1.test.ts"
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return d, Task(id="T1.1", name="Setup", text="do it", test=rel, files=["src/math.ts"])

    def test_deterministic_dispute_needs_no_arbiter(self):
        d, task = self._repo_with_test(_ALWAYS_FAIL)
        called = []
        def fake_run(role, adapter, sysm, usrm):
            called.append(role); return '{"test_broken": false}'
        # reviewer is adapter (same) -> would not call arbiter anyway, but the
        # deterministic check fires first regardless.
        res = _diagnose_deadlock(d, task, "", reviewer="M", adapter="M",
                                 run=fake_run, stack=None, project_id="default")
        self.assertTrue(res["disputed"])
        self.assertEqual(res["source"], "deterministic")
        self.assertFalse(res["used_arbiter"])
        self.assertEqual(called, [])   # no LLM spend on the obvious case
        # dispute recorded
        rec = (d / ".signalos" / "product" / "TEST_DISPUTES.jsonl").read_text("utf-8")
        self.assertIn("T1.1", rec)

    def test_arbiter_used_only_when_model_differs(self):
        d, task = self._repo_with_test(_REAL)
        seen = []
        def fake_run(role, adapter, sysm, usrm):
            seen.append(role); return '{"test_broken": true, "reason": "asserts impossible sum"}'
        res = _diagnose_deadlock(d, task, "AssertionError", reviewer="B", adapter="A",
                                 run=fake_run, stack=None, project_id="default")
        self.assertTrue(res["disputed"])
        self.assertEqual(res["source"], "arbiter")
        self.assertTrue(res["used_arbiter"])
        self.assertEqual(seen, ["test-arbiter"])

    def test_same_model_no_arbiter_no_dispute_on_real_test(self):
        d, task = self._repo_with_test(_REAL)
        def fake_run(role, adapter, sysm, usrm):
            raise AssertionError("must not be called when reviewer is adapter")
        res = _diagnose_deadlock(d, task, "AssertionError", reviewer="A", adapter="A",
                                 run=fake_run, stack=None, project_id="default")
        self.assertFalse(res["disputed"])       # real test + no second opinion -> impl fault
        self.assertFalse(res["used_arbiter"])

    def test_arbiter_says_impl_wrong_no_dispute(self):
        d, task = self._repo_with_test(_REAL)
        def fake_run(role, adapter, sysm, usrm):
            return '{"test_broken": false, "reason": "implementation incomplete"}'
        res = _diagnose_deadlock(d, task, "AssertionError", reviewer="B", adapter="A",
                                 run=fake_run, stack=None, project_id="default")
        self.assertFalse(res["disputed"])
        self.assertTrue(res["used_arbiter"])     # asked, but ruled impl-wrong


if __name__ == "__main__":
    unittest.main()
