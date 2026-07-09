"""Tests for the Gate-4 subagent-driven build (assembled from bundled skills).

Covers: acceptance -> task decomposition, reviewer verdict / implementer status
parsing, and the full per-task orchestration (implementer -> spec-compliance
reviewer -> code-quality reviewer -> bounded rework), with an injected fake
`run_agent` so no live LLM is needed. Also asserts reviews run on the
INDEPENDENT reviewer adapter when one is configured.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from signalos_lib.product.subagent_build import (
    Task,
    decompose_tasks,
    parse_implementer_status,
    parse_verdict,
    run_subagent_driven_build,
)


def _repo(criteria_md: str | None = None) -> Path:
    d = Path(tempfile.mkdtemp())
    if criteria_md is not None:
        acc = d / "core" / "execution" / "ACCEPTANCE_CRITERIA.md"
        acc.parent.mkdir(parents=True, exist_ok=True)
        acc.write_text(criteria_md, encoding="utf-8")
    return d


class Recorder:
    """Fake run_agent: records (role, adapter, user_message) per call and
    returns scripted responses per role (defaults: implementers DONE,
    reviewers PASS)."""

    def __init__(self, script: dict[str, list[str]] | None = None):
        self.calls: list[tuple[str, object]] = []
        self.messages: list[str] = []
        self.script = {k: list(v) for k, v in (script or {}).items()}

    def __call__(self, role: str, adapter, system_prompt: str, user_message: str) -> str:
        self.calls.append((role, adapter))
        self.messages.append(user_message)
        queued = self.script.get(role)
        if queued:
            return queued.pop(0)
        return "Status: DONE" if role == "implementer" else "VERDICT: PASS"

    def roles(self) -> list[str]:
        return [r for r, _ in self.calls]


class TestDecompose(unittest.TestCase):
    def test_fallback_single_task_when_no_criteria(self):
        tasks = decompose_tasks(_repo(), "build an expense tracker")
        self.assertEqual(len(tasks), 1)
        self.assertIn("expense tracker", tasks[0].text)

    def test_table_criteria_become_tasks(self):
        md = (
            "# Acceptance\n\n"
            "| ID | Criterion | Status |\n"
            "|----|-----------|--------|\n"
            "| AC1 | User can add an expense with amount and category | todo |\n"
            "| AC2 | User can delete an expense from the list | todo |\n"
            "| AC3 | User can mark an expense as reconciled | todo |\n"
        )
        tasks = decompose_tasks(_repo(md), "expenses")
        self.assertEqual(len(tasks), 3)
        self.assertTrue(any("delete an expense" in t.text for t in tasks))
        # the bare id/status cells are not chosen as the criterion body
        self.assertFalse(any(t.text.strip().lower() in ("ac1", "todo") for t in tasks))

    def test_prefers_ac_headings_over_checkboxes(self):
        md = (
            "# Acceptance Criteria\n\n"
            "### AC-1: Store Implementation\n"
            "- [ ] Store exports useExpenseStore hook\n"
            "- [ ] addExpense creates a new expense\n\n"
            "### AC-2: Expense Form\n"
            "- [ ] Form validates amount is positive\n"
            "- [ ] Submitting adds the expense to the list\n"
        )
        tasks = decompose_tasks(_repo(md), "expenses")
        # per HEADING, not per checkbox -> 2 tasks, each carrying its checkboxes
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].name, "AC-1: Store Implementation")
        self.assertIn("useExpenseStore", tasks[0].text)
        self.assertIn("validates amount", tasks[1].text)

    def test_bullet_and_numbered_criteria(self):
        md = (
            "## Criteria\n"
            "- [ ] The user can create a new note\n"
            "- The user can archive an existing note\n"
            "1. The user can search notes by title\n"
        )
        tasks = decompose_tasks(_repo(md), "notes")
        self.assertEqual(len(tasks), 3)
        self.assertTrue(any("archive an existing note" in t.text for t in tasks))

    def test_tail_criteria_folded_not_dropped(self):
        rows = "\n".join(f"- The user can do action number {i} in the app" for i in range(20))
        tasks = decompose_tasks(_repo("# C\n" + rows), "many")
        self.assertLessEqual(len(tasks), 12)
        # nothing silently dropped: the overflow is folded into the last task
        self.assertTrue(tasks[-1].extra)
        self.assertEqual(len(tasks) - 1 + 1 + len(tasks[-1].extra), 20)


class TestVerdictParsing(unittest.TestCase):
    def test_explicit_verdict_token(self):
        self.assertEqual(parse_verdict("... VERDICT: PASS"), "PASS")
        self.assertEqual(parse_verdict("issues...\nVERDICT: FAIL: missing delete"), "FAIL")

    def test_emoji_convention_fallback(self):
        self.assertEqual(parse_verdict("✅ Spec compliant - all good"), "PASS")
        self.assertEqual(parse_verdict("❌ Issues found: missing X"), "FAIL")

    def test_defaults_fail_closed(self):
        self.assertEqual(parse_verdict(""), "FAIL")
        self.assertEqual(parse_verdict("I looked at it, seems fine maybe"), "FAIL")

    def test_implementer_status(self):
        self.assertEqual(parse_implementer_status("Status: DONE"), "DONE")
        self.assertEqual(parse_implementer_status("... BLOCKED: cannot proceed"), "BLOCKED")
        self.assertEqual(parse_implementer_status("DONE_WITH_CONCERNS: file large"), "DONE_WITH_CONCERNS")
        self.assertEqual(parse_implementer_status("no status word here"), "DONE")


def _green(_repo_root, only_test=None):
    return (True, "")


def _red_then_green(n_red: int):
    state = {"n": n_red}

    def check(_repo_root, only_test=None):
        if state["n"] > 0:
            state["n"] -= 1
            return (False, "src/components/Foo.test.tsx(4,25): error TS2307: Cannot find module './Foo'")
        return (True, "")
    return check


def _repo_with_plan() -> Path:
    d = Path(tempfile.mkdtemp())
    plan = d / "core" / "execution" / "PLAN.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        "# Plan\n\n"
        "### T1 — Store\n"
        "**Files:** `src/store/s.ts`\n"
        "**Test:** `core/execution/tests/T1.test.ts`\n"
        "Acceptance: the store works\n\n"
        "### T2 — Form\n"
        "**Files:** `src/components/F.tsx`\n"
        "**Test:** `core/execution/tests/T2.test.ts`\n"
        "Acceptance: the form works\n",
        encoding="utf-8")
    return d


class TestOrchestration(unittest.TestCase):
    def test_happy_path_phases(self):
        # green immediately: 1 fallback task -> draft, (no repair), spec+code
        # review, evidence.
        rec = Recorder()
        res = run_subagent_driven_build(
            _repo(), adapter="A", reviewer_adapter="B", prompt="build a thing",
            run_agent=rec, build_check=_green,
        )
        self.assertEqual(res.status, "completed")
        self.assertIn("green=True", res.final_text)
        self.assertEqual(
            rec.roles(),
            ["implementer", "spec-reviewer", "code-reviewer", "evidence"],
        )

    def test_objective_repair_loop_runs_until_green(self):
        rec = Recorder()
        run_subagent_driven_build(
            _repo(), adapter="A", prompt="x", run_agent=rec,
            build_check=_red_then_green(2),
        )
        roles = rec.roles()
        # draft, fixer, fixer (now green), spec, code, evidence
        self.assertEqual(roles.count("fixer"), 2)
        self.assertEqual(roles[0], "implementer")
        self.assertLess(roles.index("fixer"), roles.index("spec-reviewer"))

    def test_red_after_budget_skips_review_but_still_evidences(self):
        rec = Recorder()
        res = run_subagent_driven_build(
            _repo(), adapter="A", prompt="x", run_agent=rec,
            build_check=lambda r, only_test=None: (False, "still broken"), repair_cycles=3,
        )
        roles = rec.roles()
        self.assertEqual(roles.count("fixer"), 3)  # exhausts the repair budget
        self.assertNotIn("spec-reviewer", roles)   # never green -> no review
        self.assertIn("evidence", roles)           # evidence still recorded
        self.assertIn("green=False", res.final_text)

    def test_reviews_run_on_independent_adapter(self):
        rec = Recorder()
        run_subagent_driven_build(
            _repo(), adapter="PRIMARY", reviewer_adapter="CRITIC", prompt="x",
            run_agent=rec, build_check=_green,
        )
        for role, adapter in rec.calls:
            if role.endswith("reviewer"):
                self.assertEqual(adapter, "CRITIC", f"{role} must use the independent adapter")
            else:
                self.assertEqual(adapter, "PRIMARY")

    def test_reviewer_falls_back_to_primary_when_no_critic(self):
        rec = Recorder()
        run_subagent_driven_build(_repo(), adapter="PRIMARY", prompt="x",
                                  run_agent=rec, build_check=_green)
        self.assertTrue(all(a == "PRIMARY" for _, a in rec.calls))

    def test_each_task_gets_one_draft_implementer(self):
        md = (
            "- The user can add an item to the list\n"
            "- The user can remove an item from the list\n"
        )
        rec = Recorder()
        run_subagent_driven_build(_repo(md), adapter="A", prompt="x",
                                  run_agent=rec, build_check=_green)
        roles = rec.roles()
        self.assertEqual(roles.count("implementer"), 2)  # one draft per task
        self.assertEqual(roles.count("spec-reviewer"), 1)  # whole-product review, once
        self.assertEqual(roles.count("code-reviewer"), 1)

    def test_review_fail_triggers_a_fixer_pass(self):
        rec = Recorder(script={"spec-reviewer": ["VERDICT: FAIL: missing delete"]})
        run_subagent_driven_build(_repo(), adapter="A", prompt="x",
                                  run_agent=rec, build_check=_green)
        roles = rec.roles()
        # spec-review FAIL -> a fixer pass right after
        self.assertIn("fixer", roles)
        self.assertGreater(roles.index("fixer"), roles.index("spec-reviewer"))

    def test_stalled_integration_changes_strategy_then_model(self):
        """Same input -> same model -> most likely same output: a stalled pass
        (identical error signature) must CHANGE the prompt strategy, and a
        second stall must switch the fixer to the independent reviewer model."""
        rec = Recorder()
        run_subagent_driven_build(
            _repo(), adapter="PRIMARY", reviewer_adapter="CRITIC", prompt="x",
            run_agent=rec,
            build_check=lambda r, only_test=None: (False, "SAME ERROR every time"),
            repair_cycles=3,
        )
        fixer_calls = [(a, m) for (role, a), m in zip(rec.calls, rec.messages)
                       if role == "fixer"]
        self.assertEqual(len(fixer_calls), 3)
        # pass 1: fresh errors -> normal prompt on the primary model
        self.assertEqual(fixer_calls[0][0], "PRIMARY")
        self.assertNotIn("STALLED", fixer_calls[0][1])
        # pass 2: stall 1 -> changed strategy, still primary
        self.assertEqual(fixer_calls[1][0], "PRIMARY")
        self.assertIn("STALLED", fixer_calls[1][1])
        # pass 3: stall 2 -> different model (the independent reviewer)
        self.assertEqual(fixer_calls[2][0], "CRITIC")
        self.assertIn("FRESH reviewer-model pass", fixer_calls[2][1])

    def test_decompose_prefers_plan_tasks_with_files_and_test(self):
        tasks = decompose_tasks(_repo_with_plan(), "x")
        self.assertEqual([t.id for t in tasks], ["T1", "T2"])
        self.assertEqual(tasks[0].files, ["src/store/s.ts"])
        self.assertEqual(tasks[0].test, "core/execution/tests/T1.test.ts")
        self.assertEqual(tasks[1].test, "core/execution/tests/T2.test.ts")

    def test_per_task_green_gate_runs_each_plan_test_then_integration(self):
        seen: list = []

        def check(_r, only_test=None):
            seen.append(only_test)
            return (True, "")
        rec = Recorder()
        run_subagent_driven_build(_repo_with_plan(), adapter="A", prompt="x",
                                  run_agent=rec, build_check=check)
        # each task's plan test is checked individually (per-task gate)...
        self.assertIn("core/execution/tests/T1.test.ts", seen)
        self.assertIn("core/execution/tests/T2.test.ts", seen)
        # ...and the whole product is checked at integration (only_test=None)
        self.assertIn(None, seen)

    def test_per_task_fixer_runs_when_a_task_test_is_red(self):
        state = {"n": 1}

        def check(_r, only_test=None):
            if only_test and only_test.endswith("T1.test.ts") and state["n"] > 0:
                state["n"] -= 1
                return (False, "T1 assertion failed")
            return (True, "")
        rec = Recorder()
        run_subagent_driven_build(_repo_with_plan(), adapter="A", prompt="x",
                                  run_agent=rec, build_check=check)
        roles = rec.roles()
        self.assertEqual(roles[0], "implementer")   # T1 draft
        self.assertEqual(roles[1], "fixer")         # T1 per-task fix (test was red)
        self.assertIn("evidence", roles)

    def test_bundled_skills_are_loaded_into_prompts(self):
        # sanity: the assembled implementer/reviewer prompts carry the bundled
        # skill content (TDD iron law + the required VERDICT contract).
        from signalos_lib.product import subagent_build as sb
        impl = sb._implementer_system_prompt("", sb._StackContext())
        self.assertIn("NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST", impl)
        spec = sb._reviewer_system_prompt("spec")
        self.assertIn("VERDICT: PASS", spec)
        self.assertIn("Do Not Trust the Report", spec)  # bundled spec-reviewer content


if __name__ == "__main__":
    unittest.main()
