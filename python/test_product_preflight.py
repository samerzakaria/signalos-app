"""Build-gate preflight: every precondition verified BEFORE the first model
dispatch -- a broken repo costs zero LLM spend and fails with an actionable
diagnostic naming the exact broken precondition (the anti-silent-degrade rule).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import seed_signed_artifact  # noqa: E402
from signalos_lib.artifacts import expected_gate_artifacts  # noqa: E402
from signalos_lib.product.preflight import validate_build_readiness  # noqa: E402
from signalos_lib.product.subagent_build import run_subagent_driven_build  # noqa: E402


def _ready_repo() -> Path:
    """A repo that passes preflight: G0-G3 signed, react-vite stack markers,
    no structured plan (acceptance fallback -> no plan-test contract)."""
    root = Path(tempfile.mkdtemp())
    for gate in ("G0", "G1", "G2", "G3"):
        for row in expected_gate_artifacts(gate):
            seed_signed_artifact(root, row.rel_path, gate,
                                 content=f"# {row.label}\n\nReal content here.\n")
    # react-vite markers + build/test scripts so the validation plan exists
    (root / "package.json").write_text(
        '{"dependencies": {"react": "18", "vite": "5"}, '
        '"scripts": {"build": "x", "test": "x"}}', encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    return root


class TestValidateBuildReadiness(unittest.TestCase):
    def test_ready_repo_returns_no_problems(self):
        self.assertEqual(validate_build_readiness(_ready_repo()), [])

    def test_unsigned_artifact_is_named_exactly(self):
        root = _ready_repo()
        # strip the signature block from G2's artifact
        p = root / "core" / "strategy" / "EXPECTATION_MAP.md"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.split("## Signatures")[0], encoding="utf-8")
        problems = validate_build_readiness(root)
        self.assertTrue(any("G2" in x and "UNSIGNED" in x and "EXPECTATION_MAP" in x
                            for x in problems), problems)

    def test_missing_artifact_is_named_exactly(self):
        root = _ready_repo()
        (root / "core" / "execution" / "PLAN.md").unlink()
        problems = validate_build_readiness(root)
        self.assertTrue(any("G3" in x and "missing" in x and "PLAN.md" in x
                            for x in problems), problems)

    def test_unresolvable_plan_test_is_named(self):
        root = _ready_repo()
        (root / "core" / "execution" / "PLAN.md").write_text(
            "# Plan\n\n### T1 — Store\n**Files:** `src/s.ts`\n"
            "**Test:** `core/execution/tests/T1.test.ts`\nworks\n\n"
            "### T2 — Form\n**Files:** `src/f.tsx`\n"
            "**Test:** `core/execution/tests/T2.test.ts`\nworks\n",
            encoding="utf-8")
        # re-sign the rewritten artifact (content changed after signing)
        seed_signed_artifact(root, "core/execution/PLAN.md", "G3",
                             content=(root / "core" / "execution" / "PLAN.md")
                             .read_text(encoding="utf-8"))
        t = root / "core" / "execution" / "tests"
        t.mkdir(parents=True, exist_ok=True)
        (t / "T1.test.ts").write_text("test('x', () => {});\n", encoding="utf-8")
        # T2's authored test deliberately absent
        problems = validate_build_readiness(root)
        self.assertTrue(any("T2" in x and "not found" in x for x in problems), problems)
        self.assertFalse(any("T1" in x for x in problems), problems)


class TestPreflightGatesTheBuild(unittest.TestCase):
    def test_broken_repo_dispatches_no_model(self):
        """The no-spend guarantee: preflight failure returns BEFORE any agent
        dispatch -- zero calls, status error, diagnostics in final_text."""
        root = Path(tempfile.mkdtemp())  # nothing signed, no stack
        calls = []

        def recorder(role, adapter, system_prompt, user_message):
            calls.append(role)
            return "Status: DONE"

        res = run_subagent_driven_build(root, adapter="A", prompt="x",
                                        run_agent=recorder)
        self.assertEqual(calls, [])                      # zero LLM spend
        self.assertEqual(res.status, "error")
        self.assertEqual(res.tool_calls_made, 0)
        self.assertIn("preflight failed", res.error)
        # the diagnostic names the broken precondition (missing or unsigned)
        self.assertTrue("missing" in res.final_text or "UNSIGNED" in res.final_text,
                        res.final_text)

    def test_injected_build_check_skips_preflight(self):
        """Unit-test seam preserved: an injected objective check means the
        caller owns repo-state simulation; preflight must not block it."""
        root = Path(tempfile.mkdtemp())
        calls = []

        def recorder(role, adapter, system_prompt, user_message):
            calls.append(role)
            return "Status: DONE"

        res = run_subagent_driven_build(
            root, adapter="A", prompt="x", run_agent=recorder,
            build_check=lambda r, only_test=None: (True, ""))
        self.assertNotEqual(calls, [])
        self.assertEqual(res.status, "completed")


if __name__ == "__main__":
    unittest.main()
