"""Tests for the Gate-4 subagent-driven build (assembled from bundled skills).

Covers: acceptance -> task decomposition, reviewer verdict / implementer status
parsing, and the full per-task orchestration (implementer -> spec-compliance
reviewer -> code-quality reviewer -> bounded rework), with an injected fake
`run_agent` so no live LLM is needed. Also asserts reviews run on the
INDEPENDENT reviewer adapter when one is configured.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalos_lib.product import subagent_build as sb
from signalos_lib.product.subagent_build import (
    Task,
    decompose_tasks,
    parse_implementer_status,
    parse_verdict,
    run_subagent_driven_build,
    task_dod_violations,
    is_vacuous_test,
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


def _repo_with_scaffold_and_feature() -> Path:
    """A plan-driven repo whose FIRST task is a toolchain/scaffold step with NO
    acceptance test (the failure mode from run 13) and whose SECOND task is a
    real product feature carrying its own test."""
    d = Path(tempfile.mkdtemp())
    plan = d / "core" / "execution" / "PLAN.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        "# Plan\n\n"
        "### T1 — Scaffold the toolchain\n"
        "**Files:** `package.json`\n"
        "Set up the build tool and test runner\n\n"
        "### T2 — Add an expense and see it in the list\n"
        "**Files:** `src/Expense.tsx`\n"
        "**Test:** `core/execution/tests/T2.test.ts`\n"
        "Acceptance: adding an expense shows it in the list\n",
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

    def test_red_after_budget_fails_fast_no_review_no_evidence(self):
        """FAIL FAST: a definitively-red integration stops the build -- review
        and evidence would be paid spend on a build the gate must refuse (the
        missing evidence artifact keeps the sign fail-closed)."""
        rec = Recorder()
        res = run_subagent_driven_build(
            _repo(), adapter="A", prompt="x", run_agent=rec,
            build_check=lambda r, only_test=None: (False, "still broken"), repair_cycles=3,
        )
        roles = rec.roles()
        self.assertEqual(roles.count("fixer"), 3)  # exhausts the repair budget
        self.assertNotIn("spec-reviewer", roles)   # never green -> no review
        self.assertNotIn("evidence", roles)        # fail-fast: no paid evidence pass
        self.assertEqual(res.status, "budget_exhausted")
        self.assertIn("STOPPED (fail-fast)", res.final_text)

    def test_failed_task_continues_independents_skips_dependents(self):
        """A task whose plan test stays red after its fix budget does NOT stop
        the build: independent tasks still run (bounded spend, real signal);
        only DEPENDENT tasks are skipped for free; and with any task red the
        doomed phases (integration/review/evidence) are cut."""
        d = _repo_with_plan()
        plan = d / "core" / "execution" / "PLAN.md"
        plan.write_text(plan.read_text(encoding="utf-8") +
                        "\n### T3 — Report\n**Files:** `src/r.tsx`\n"
                        "**Test:** `core/execution/tests/T3.test.ts`\n"
                        "**Dependencies:** T1\nneeds the store\n",
                        encoding="utf-8")

        def check(_r, only_test=None):
            if only_test and only_test.endswith("T1.test.ts"):
                return (False, "T1 stays red")
            return (True, "")
        rec = Recorder()
        res = run_subagent_driven_build(d, adapter="A", prompt="x",
                                        run_agent=rec, build_check=check)
        roles = rec.roles()
        # T1 attempted (1 impl + fixers); the SAME error with no source change is
        # a hard STALL, so the progress gate stops it after STALL_ROUNDS (=2)
        # no-progress cycles -- not at a fixed pass count. T2 independent ->
        # pre-check green, skipped free; T3 depends on failed T1 -> blocked.
        self.assertEqual(roles.count("implementer"), 1)
        self.assertEqual(roles.count("fixer"), 2)  # STALL_ROUNDS, progress-gated
        self.assertNotIn("evidence", roles)          # red build: doomed phases cut
        self.assertEqual(res.status, "budget_exhausted")
        self.assertIn("T1", res.error)
        self.assertIn("blocked=T3", res.final_text)  # dependent skipped, named
        self.assertIn("T2 test_green=True", res.final_text)  # independent still ran

    def test_already_green_task_is_skipped_not_paid(self):
        """Resume economics: a task whose plan test already passes is skipped
        by the objective pre-check -- no implementer dispatch."""
        rec = Recorder()
        run_subagent_driven_build(_repo_with_plan(), adapter="A", prompt="x",
                                  run_agent=rec, build_check=_green)
        self.assertEqual(rec.roles().count("implementer"), 0)  # both tasks pre-green

    def test_no_test_task_in_plan_driven_build_is_skipped_not_dispatched(self):
        """FAIRNESS FIX (run 13): in a plan-driven build a task that carries NO
        acceptance test of its own is not product-feature work -- it is a
        scaffold/setup step whose toolchain is already provisioned before the
        gate. It is marked pre-existing-green and NEVER dispatched to a build
        seat; only the real feature task (which HAS a test) is built. Detected
        STRUCTURALLY (absence of a RED test), never by matching the task name."""
        d = _repo_with_scaffold_and_feature()
        # T2's test is red on the pre-check (forcing exactly one dispatch), then
        # green after the implementer runs.
        state = {"pre": True}

        def check(_r, only_test=None):
            if only_test and only_test.endswith("T2.test.ts") and state["pre"]:
                state["pre"] = False
                return (False, "T2 red")
            return (True, "")

        rec = Recorder()
        res = run_subagent_driven_build(d, adapter="A", prompt="x",
                                        run_agent=rec, build_check=check)
        self.assertEqual(res.status, "completed")
        # exactly ONE build seat, and it is the FEATURE task -- the no-test
        # scaffold task was never dispatched.
        self.assertEqual(rec.roles().count("implementer"), 1)
        dispatched = [m for (role, _), m in zip(rec.calls, rec.messages)
                      if role in ("implementer", "fixer")]
        # the dispatch's TASK HEADER (`# Task <id>: ...`) uniquely marks WHICH
        # task a seat was given -- T1 (the no-test scaffold task) is never it.
        self.assertTrue(all("# Task T1:" not in m for m in dispatched),
                        "the no-test scaffold task must never reach a build seat")
        self.assertTrue(any("# Task T2:" in m for m in dispatched))
        # T1 recorded pre-existing-green (skipped, not built) in the trace.
        tr = _trace(d)
        t1 = next(r for r in tr["rows"] if r["task"] == "T1")
        self.assertEqual(t1["status"], "pre-existing-green")
        self.assertIn("not dispatched", res.final_text)

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

    def test_repair_test_import_depths_is_deterministic(self):
        """The plan may ship a test with a wrong relative import DEPTH; the
        correct number of `../` is computed from the file's directory depth and
        fixed in Python -- the model never touches the spec. Assertions and
        already-correct imports are byte-untouched."""
        from signalos_lib.product.subagent_build import (
            decompose_plan_tasks, repair_test_import_depths)
        d = Path(tempfile.mkdtemp())
        # a plan task whose test sits 5 dirs deep but imports at 2 levels
        plan = d / "core" / "execution" / "PLAN.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "### T1 — Store\n**Files:** `src/store/s.ts`\n"
            "**Test:** `core/execution/tests/skeletons/wave-1/T1.test.ts`\n\n"
            "### T2 — Ok\n**Files:** `src/f.ts`\n"
            "**Test:** `core/execution/tests/skeletons/wave-1/T2.test.ts`\n",
            encoding="utf-8")
        tdir = d / "core" / "execution" / "tests" / "skeletons" / "wave-1"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "T1.test.ts").write_text(
            "import { s } from '../../src/store/s';\n"
            "test('a', () => expect(s).toBe(42));\n", encoding="utf-8")
        (tdir / "T2.test.ts").write_text(  # already correct depth
            "import { f } from '../../../../../src/f';\ntest('b', () => {});\n",
            encoding="utf-8")
        tasks = decompose_plan_tasks(d, "default")
        reps = repair_test_import_depths(d, tasks, "src", "default")
        self.assertEqual(len(reps), 1)                          # only T1 wrong
        self.assertEqual(reps[0][1:], ("../../", "../../../../../"))
        t1 = (tdir / "T1.test.ts").read_text(encoding="utf-8")
        self.assertIn("'../../../../../src/store/s'", t1)       # depth fixed
        self.assertIn("expect(s).toBe(42)", t1)                 # assertion intact
        t2 = (tdir / "T2.test.ts").read_text(encoding="utf-8")
        self.assertIn("'../../../../../src/f'", t2)             # correct left alone

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
        # red for the pre-check AND the post-implementer check, green after the
        # fixer's pass -- exercising the full red->fix->green cycle.
        state = {"n": 2}

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


# ---------------------------------------------------------------------------
# FIX 5 — the seat gets its environment up front (fixed facts + repo map +
# efficient-process directives), all resolved BY THE HARNESS from the adapter.
# ---------------------------------------------------------------------------


def _react_stack():
    """A resolved-values stack context mirroring what _resolve_stack builds from
    the React+Vite adapter (test command + install command come from the
    adapter's validation_plan, never hand-authored here)."""
    from signalos_lib.product import subagent_build as sb
    st = sb._StackContext(
        profile="react-vite", source_dir="src",
        build_cmds=["npm run build"], test_cmds=["npm test"],
        install_cmds=["npm install --legacy-peer-deps"],
    )
    st.dep_dir = sb._resolve_dep_dir(st.install_cmds)
    return st


class TestSeatEnvironmentUpFront(unittest.TestCase):
    def test_system_prompt_has_env_facts_block_with_workspace_and_test_cmd(self):
        # (a) the env-facts block is prepended, states the /workspace cwd
        # (resolved from the sandbox, NOT the host path), the pre-installed deps,
        # and the resolved test command.
        from signalos_lib.product import subagent_build as sb
        from signalos_lib.product.sandbox import CONTAINER_WORKSPACE
        impl = sb._implementer_system_prompt("", _react_stack())
        self.assertIn("Environment (fixed facts", impl)
        self.assertIn(CONTAINER_WORKSPACE, impl)           # /workspace
        self.assertIn("do NOT run touch/df/mount", impl)
        self.assertIn("PRE-INSTALLED", impl)
        self.assertIn("node_modules", impl)                # resolved dep dir
        self.assertIn("Run tests with: npm test", impl)    # resolved test command
        # process coaching is appended (HOW to spend effort, not WHAT to build).
        self.assertIn("How to spend your effort", impl)
        self.assertIn("explore ONCE", impl)

    def test_system_prompt_env_facts_are_resolved_not_hardcoded(self):
        # The block adapts to the resolved values: the cwd is fixed (sandbox) but
        # the test/install commands come from the adapter -- change them and the
        # env-facts block changes (asserted on the block itself, since the bundled
        # TDD skill legitimately contains `npm test` in its examples).
        from signalos_lib.product import subagent_build as sb
        st = sb._StackContext(source_dir="app", test_cmds=["pytest -q"],
                              install_cmds=["pip install -e ."])
        facts = sb._env_facts_block(st)
        self.assertIn("Run tests with: pytest -q", facts)
        self.assertIn("pip install -e .", facts)
        self.assertNotIn("npm", facts)
        self.assertNotIn("node_modules", facts)
        # A non-node install command yields no node_modules claim.
        self.assertEqual(sb._resolve_dep_dir(["pip install -e ."]), "")

    def test_first_message_carries_workspace_digest_and_test_command(self):
        # (c) message 1 (the FIRST dispatch, no fix feedback) carries a workspace
        # map + config digest + the resolved test command.
        from signalos_lib.product import subagent_build as sb
        d = Path(tempfile.mkdtemp())
        (d / "src" / "components").mkdir(parents=True)
        (d / "src" / "App.tsx").write_text("export const App = () => null",
                                           encoding="utf-8")
        (d / "src" / "components" / "Foo.tsx").write_text(
            "export const Foo = () => null", encoding="utf-8")
        (d / "package.json").write_text(json.dumps(
            {"scripts": {"test": "vitest run"},
             "dependencies": {"react": "^18"},
             "devDependencies": {"vitest": "^1"}}), encoding="utf-8")
        (d / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}',
                                         encoding="utf-8")
        # dependency noise dir must be excluded from the map.
        (d / "node_modules").mkdir()
        (d / "node_modules" / "junk.js").write_text("x", encoding="utf-8")
        task = Task(id="T1", name="Build Foo", text="do it",
                    files=["src/components/Foo.tsx"], test="")
        msg = sb._implementer_message(task, "ctx", d, _react_stack())
        self.assertIn("Workspace map", msg)
        self.assertIn("src/components/Foo.tsx", msg)       # existing module path
        self.assertIn("package.json", msg)                 # key config surfaced
        self.assertIn("npm test", msg)                     # resolved test command
        self.assertNotIn("node_modules", msg.split("Workspace map", 1)[1]
                         .split("Work from", 1)[0])        # noise dir excluded

    def test_first_message_has_no_host_path_and_uses_container_path(self):
        # (b) the misleading `Work from: <windows host path>` line is gone; the
        # seat is told the CONTAINER path, never the host path.
        from signalos_lib.product import subagent_build as sb
        from signalos_lib.product.sandbox import CONTAINER_WORKSPACE
        d = Path(tempfile.mkdtemp())
        (d / "src").mkdir(parents=True)
        task = Task(id="T1", name="X", text="do it", files=[], test="")
        msg = sb._implementer_message(task, "ctx", d, _react_stack())
        self.assertIn(f"Work from: {CONTAINER_WORKSPACE}", msg)
        self.assertNotIn(str(d), msg)                      # no host path leak
        self.assertNotIn("Work from: " + str(d), msg)

    def test_fixer_and_evidence_messages_use_container_path_not_host(self):
        # The whole message cluster (fixer / DoD-fixer / evidence) shows the
        # container path, never the host path.
        from signalos_lib.product import subagent_build as sb
        from signalos_lib.product.sandbox import CONTAINER_WORKSPACE
        d = Path(tempfile.mkdtemp())
        st = _react_stack()
        fixer = sb._fixer_message("some error", d, st)
        self.assertIn(f"Work from: {CONTAINER_WORKSPACE}", fixer)
        self.assertNotIn(str(d), fixer)
        ev = sb._evidence_message(d, "default", st, green=True)
        self.assertIn(f"Work from: {CONTAINER_WORKSPACE}", ev)
        self.assertNotIn(str(d), ev)


# ---------------------------------------------------------------------------
# FIX 3 — a stack WITHOUT a per-test runner must not fake green
# ---------------------------------------------------------------------------


class TestSingleTestHonestWhenNoRunner(unittest.TestCase):
    def test_no_runner_stack_returns_false_not_fake_green(self):
        from signalos_lib.product.subagent_build import _run_single_test, _StackContext
        d = Path(tempfile.mkdtemp())
        stack = _StackContext()  # test_file_command is None (no per-test runner)
        ok, reason = _run_single_test(d, "core/execution/tests/T1.test.ts", stack)
        self.assertFalse(ok)                          # NOT fake-green
        self.assertNotEqual((ok, reason), (True, ""))  # the old bug
        self.assertTrue(reason.strip())               # honest, non-empty reason

    def test_real_runner_pass_and_fail_unchanged(self):
        # The React/vitest-style path (test_file_command IS set) still returns a
        # real pass/fail from the runner's exit code.
        from signalos_lib.product.subagent_build import _run_single_test, _StackContext
        d = Path(tempfile.mkdtemp())
        tpath = "core/execution/tests/T1.test.ts"
        (d / "core" / "execution" / "tests").mkdir(parents=True, exist_ok=True)
        (d / tpath).write_text("test('x', () => {})", encoding="utf-8")
        stack_pass = _StackContext(
            test_file_command=lambda repo, rel: [sys.executable, "-c", "raise SystemExit(0)"])
        self.assertEqual(_run_single_test(d, tpath, stack_pass), (True, ""))
        stack_fail = _StackContext(
            test_file_command=lambda repo, rel: [
                sys.executable, "-c", "print('FAIL: boom'); raise SystemExit(1)"])
        ok, reason = _run_single_test(d, tpath, stack_fail)
        self.assertFalse(ok)
        self.assertIn("FAIL", reason)

    def test_real_runner_uses_selected_container_verifier(self):
        from signalos_lib.product.sandbox import CommandOutput
        from signalos_lib.product.subagent_build import _run_single_test, _StackContext

        class _Verifier:
            def __init__(self):
                self.calls = []

            def run(self, command, repo_root, timeout_s, env):
                self.calls.append((command, repo_root, timeout_s, env))
                return 0, CommandOutput(stdout="PASS", stderr="")

        d = Path(tempfile.mkdtemp())
        # Exercise a lexical workspace alias so this regression is deterministic
        # on every platform.  The production failure appeared on Windows when
        # canonical path resolution did not lexically match the caller's root.
        alias_component = d.parent / f"{d.name}-path-alias"
        alias_component.mkdir()
        repo_root = alias_component / ".." / d.name
        tpath = "core/execution/tests/T1.test.ts"
        (d / tpath).parent.mkdir(parents=True, exist_ok=True)
        (d / tpath).write_text("test('x', () => {})", encoding="utf-8")
        stack = _StackContext(
            test_file_command=lambda repo, rel: [
                "npx.cmd", "vitest", "run", rel,
            ]
        )
        verifier = _Verifier()

        with mock.patch(
            "signalos_lib.product.validation._select_verifier_runner",
            return_value=verifier,
        ), mock.patch(
            "signalos_lib.product.subagent_build.subprocess.run",
        ) as host_run:
            self.assertEqual(_run_single_test(repo_root, tpath, stack), (True, ""))

        host_run.assert_not_called()
        self.assertEqual(len(verifier.calls), 1)
        command, repo_root, timeout_s, env = verifier.calls[0]
        self.assertEqual(command, "npx vitest run core/execution/tests/T1.test.ts")
        self.assertNotIn("\\", command)
        self.assertEqual(repo_root, alias_component / ".." / d.name)
        self.assertEqual(timeout_s, 240)
        self.assertEqual(env, {"CI": "1", "FORCE_COLOR": "0"})

    def test_container_failure_is_typed_as_infrastructure(self):
        from signalos_lib.product.sandbox import SandboxUnavailableError
        from signalos_lib.product.subagent_build import (
            ExecutionInfrastructureError,
            _run_single_test,
            _StackContext,
        )

        class _BrokenVerifier:
            def run(self, *args, **kwargs):
                raise SandboxUnavailableError("daemon unavailable")

        d = Path(tempfile.mkdtemp())
        tpath = "core/execution/tests/T1.test.ts"
        (d / tpath).parent.mkdir(parents=True, exist_ok=True)
        (d / tpath).write_text("test('x', () => {})", encoding="utf-8")
        stack = _StackContext(
            test_file_command=lambda repo, rel: ["npx.cmd", "vitest", "run", rel]
        )

        with mock.patch(
            "signalos_lib.product.validation._select_verifier_runner",
            return_value=_BrokenVerifier(),
        ), self.assertRaises(ExecutionInfrastructureError) as raised:
            _run_single_test(d, tpath, stack)

        self.assertEqual(raised.exception.failure_type, "sandbox-unavailable")

    def test_no_runner_stack_does_not_stamp_pre_existing_green(self):
        # Caller path (CONTROL DECISION 2): with the honest no-runner result the
        # per-task pre-check is NOT green, so the implementer IS dispatched and
        # no task is stamped "pre-existing-green". Contrast with
        # test_already_green_task_is_skipped_not_paid (fake-green -> 0 implementers).
        from signalos_lib.product.subagent_build import _default_build_check, _StackContext
        d = _repo_with_plan()
        no_tfc = _StackContext()

        def check(r, only_test=None):
            return _default_build_check(r, only_test, stack=no_tfc)

        rec = Recorder()
        res = run_subagent_driven_build(d, adapter="A", prompt="x",
                                        run_agent=rec, build_check=check)
        self.assertGreaterEqual(rec.roles().count("implementer"), 1)
        self.assertNotIn("pre-existing", res.final_text)


# ---------------------------------------------------------------------------
# FIX 1 (part 6) — the run wrapper surfaces a "narrated, wrote nothing" outcome
# ---------------------------------------------------------------------------


class TestRunWrapperSurfacesNoWork(unittest.TestCase):
    def test_stalled_no_tool_run_emits_system_event(self):
        from signalos_lib.product.subagent_build import _default_run_agent
        from signalos_lib.harness import AgentResponse, AgentTestProvider, TokenUsage
        from signalos_lib.product.provider_adapter import (
            ProviderAdapter, ProviderCapabilities)
        from signalos_lib.product.enforcement_state import (
            StaticEnforcementProvider, seed_trust_tier_paths)

        d = Path(tempfile.mkdtemp())
        seed_trust_tier_paths(d)
        events: list[dict] = []
        narration = AgentResponse(
            content="I will build it now (prose only, no tool call).",
            tool_calls=None, stop_reason="end_turn", usage=TokenUsage(1, 1))
        provider = AgentTestProvider(script=[narration])
        caps = ProviderCapabilities(
            model="m", supports_tool_calls=True, supports_streaming=True,
            context_length=200_000)
        adapter = ProviderAdapter(model="m", provider=provider, capabilities=caps)
        run = _default_run_agent(
            d, StaticEnforcementProvider(trust_tier="T3"), events.append,
            "default", [0, 1, 2, 3])

        report = run("implementer", adapter, "sys", "build it")  # must not raise

        system_texts = [e.get("text", "") for e in events if e.get("type") == "system"]
        self.assertTrue(any("incomplete" in t.lower() for t in system_texts),
                        f"no no-work signal surfaced; got {system_texts}")

    def test_provider_failure_raises_typed_g4_infrastructure_error(self):
        from signalos_lib.product.subagent_build import (
            ProviderExecutionError,
            _default_run_agent,
        )
        from signalos_lib.product.enforcement_state import (
            StaticEnforcementProvider,
            seed_trust_tier_paths,
        )
        from signalos_lib.product.provider_adapter import (
            ProviderAdapter,
            ProviderCapabilities,
        )

        class _TimeoutProvider:
            def chat(self, *args, **kwargs):
                raise TimeoutError("provider connection timed out")

        d = Path(tempfile.mkdtemp())
        seed_trust_tier_paths(d)
        caps = ProviderCapabilities(
            model="m", supports_tool_calls=True, supports_streaming=False,
            context_length=200_000,
        )
        adapter = ProviderAdapter(
            model="m", provider=_TimeoutProvider(), capabilities=caps,
        )
        run = _default_run_agent(
            d, StaticEnforcementProvider(trust_tier="T3"), lambda _e: None,
            "default", [0, 1, 2, 3],
        )

        with self.assertRaises(ProviderExecutionError) as raised:
            run("implementer", adapter, "sys", "build it")
        self.assertEqual(raised.exception.failure_type, "provider-transport")


# ---------------------------------------------------------------------------
# FIX 3 (Claim 5) — G4 consumes the CANONICAL machine plan (PLAN.tasks.yaml),
# not only the rendered markdown. Prefer it; keep the markdown parser for
# back-compat (the benchmark fixture's plan is markdown-shaped).
# ---------------------------------------------------------------------------

# Two valid ULIDs (26 chars, Crockford base32 — no I/L/O/U).
_ULID_A = "0000000000000000000000000A"
_ULID_B = "0000000000000000000000000B"


def _write_canonical_plan(d: Path, body: str) -> None:
    p = d / "core" / "execution" / "PLAN.tasks.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


class TestCanonicalPlanTasks(unittest.TestCase):
    def test_canonical_two_task_plan_parsed_with_files_and_deps(self):
        from signalos_lib.product.subagent_build import decompose_plan_tasks
        d = Path(tempfile.mkdtemp())
        _write_canonical_plan(d, (
            "wave: W1\n"
            "tasks:\n"
            f'  - id: "{_ULID_A}"\n'
            "    title: Zustand store\n"
            "    status: pending\n"
            "    tier: T1\n"
            "    files:\n"
            "      - src/store/s.ts\n"
            f'  - id: "{_ULID_B}"\n'
            "    title: Expense form\n"
            "    status: pending\n"
            "    tier: T2\n"
            "    depends_on:\n"
            f'      - "{_ULID_A}"\n'
            "    files:\n"
            "      - src/components/F.tsx\n"
            "      - src/components/__tests__/F.test.tsx\n"
        ))
        tasks = decompose_plan_tasks(d, "default")
        self.assertEqual([t.id for t in tasks], [_ULID_A, _ULID_B])
        self.assertEqual(tasks[0].name, "Zustand store")
        self.assertEqual(tasks[0].files, ["src/store/s.ts"])
        self.assertEqual(tasks[0].test, "")            # no test-shaped file
        self.assertEqual(tasks[1].deps, [_ULID_A])     # dependency carried
        # the test-shaped file becomes the acceptance-test path, not an impl file
        self.assertEqual(tasks[1].files, ["src/components/F.tsx"])
        self.assertEqual(tasks[1].test, "src/components/__tests__/F.test.tsx")

    def test_decompose_tasks_prefers_canonical_over_markdown(self):
        # BOTH a markdown PLAN.md and a canonical PLAN.tasks.yaml exist; the
        # canonical machine plan wins.
        d = _repo_with_plan()  # writes core/execution/PLAN.md (T1/T2 markdown)
        _write_canonical_plan(d, (
            "wave: W1\n"
            "tasks:\n"
            f'  - id: "{_ULID_A}"\n'
            "    title: Canonical task\n"
            "    status: pending\n"
            "    tier: T1\n"
        ))
        tasks = decompose_tasks(d, "x")
        self.assertEqual([t.id for t in tasks], [_ULID_A])  # canonical, not T1/T2

    def test_explicit_test_key_is_honored(self):
        from signalos_lib.product.subagent_build import decompose_plan_tasks
        d = Path(tempfile.mkdtemp())
        _write_canonical_plan(d, (
            "wave: W1\n"
            "tasks:\n"
            f'  - id: "{_ULID_A}"\n'
            "    title: A\n"
            "    status: pending\n"
            "    tier: T1\n"
            "    test: core/execution/tests/A.test.tsx\n"
            f'  - id: "{_ULID_B}"\n'
            "    title: B\n"
            "    status: pending\n"
            "    tier: T1\n"
        ))
        tasks = decompose_plan_tasks(d, "default")
        self.assertEqual(tasks[0].test, "core/execution/tests/A.test.tsx")

    def test_markdown_plan_still_parses_when_no_canonical(self):
        # No PLAN.tasks.yaml -> the markdown parser is the fallback (unchanged).
        tasks = decompose_tasks(_repo_with_plan(), "x")
        self.assertEqual([t.id for t in tasks], ["T1", "T2"])
        self.assertEqual(tasks[0].files, ["src/store/s.ts"])
        self.assertEqual(tasks[0].test, "core/execution/tests/T1.test.ts")

    def test_invalid_canonical_falls_back_to_markdown(self):
        # A canonical file missing the required 'wave' key does not parse; the
        # build must not break -- it falls back to the markdown plan.
        d = _repo_with_plan()
        _write_canonical_plan(d, "tasks: []\n")  # no 'wave' -> load_tasks raises
        tasks = decompose_tasks(d, "x")
        self.assertEqual([t.id for t in tasks], ["T1", "T2"])  # markdown fallback


# ---------------------------------------------------------------------------
# Self-repair accounting (panel ask): "converged in 0 repairs" must be
# distinguishable from "needed N", logged to a machine-readable field a grader
# reads, and BENCHMARK-SAFE (a clean/green build does zero extra work and its
# behaviour is unchanged).
# ---------------------------------------------------------------------------


def _trace(repo_root: Path) -> dict:
    """The build's machine-readable traceability snapshot (grader-visible)."""
    p = repo_root / ".signalos" / "traceability.json"
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# FIX 3/4: the per-task fix loop is PROGRESS-gated, not fixed-count. A red task
# that keeps progressing keeps earning cycles; a stalled one stops after
# STALL_ROUNDS; and a seat that ends 'exhausted' is resumable via the objective
# on-disk gate (its work is never discarded).
# ---------------------------------------------------------------------------


class TestProgressSignature(unittest.TestCase):
    def test_same_failure_normalizes_to_same_signature(self):
        # Volatile noise (paths, line:col numbers, durations) must NOT count as
        # a changed failure -- 'the same error again' has to read as no progress.
        a = sb._normalize_error_signature(
            "C:/ws/src/Foo.test.ts(4,25): error TS2307 Cannot find './Foo' (12 ms)")
        b = sb._normalize_error_signature(
            "C:/ws/src/Foo.test.ts(9,3): error TS2307 Cannot find './Foo' (44 ms)")
        self.assertEqual(a, b)

    def test_different_failure_changes_signature(self):
        a = sb._normalize_error_signature("Cannot find module './Widget'")
        b = sb._normalize_error_signature("Cannot find module './Store'")
        self.assertNotEqual(a, b)

    def test_gate_stalls_only_after_consecutive_no_progress(self):
        d = Path(tempfile.mkdtemp())
        gate = sb._TaskProgressGate(d, "src", stall_rounds=2)
        gate.observe(False, "same error")   # baseline
        self.assertFalse(gate.stalled())
        gate.observe(False, "same error")   # no progress #1
        self.assertFalse(gate.stalled())
        gate.observe(False, "same error")   # no progress #2 -> stalled
        self.assertTrue(gate.stalled())

    def test_gate_never_stalls_while_failure_signature_moves(self):
        d = Path(tempfile.mkdtemp())
        gate = sb._TaskProgressGate(d, "src", stall_rounds=2)
        gate.observe(False, "error alpha")
        for word in ("beta", "gamma", "delta", "epsilon", "zeta"):
            gate.observe(False, f"error {word}")
            self.assertFalse(gate.stalled())   # a moving failure = progress


class TestProgressGatedFixLoop(unittest.TestCase):
    def test_progressing_red_task_keeps_cycling_past_the_old_fixed_cap(self):
        """A task that stays red but PROGRESSES (a different failure each cycle)
        keeps earning fixer cycles -- far past the old fixed 3 and past
        STALL_ROUNDS -- because forward motion, not a call count, governs."""
        d = _repo_with_plan()
        words = ["alpha", "beta", "gamma", "delta", "epsilon",
                 "zeta", "eta", "theta"]
        state = {"n": 0}

        def check(_r, only_test=None):
            if only_test and only_test.endswith("T1.test.ts"):
                i = state["n"]
                state["n"] += 1
                # red for: pre-check(i0) + baseline(i1) + 5 fixer checks(i2..i6);
                # the 6th fixer's check (i7) is GREEN -> exactly 6 fixer passes.
                if i < 7:
                    return (False, f"T1 failing at module {words[i]}")
                return (True, "")
            return (True, "")                    # T2 pre-green, integration green
        rec = Recorder()
        res = run_subagent_driven_build(d, adapter="A", prompt="x",
                                        run_agent=rec, build_check=check)
        self.assertEqual(res.status, "completed")
        fixers = rec.roles().count("fixer")
        self.assertGreater(fixers, 3)            # NOT capped at the old fixed 3
        self.assertEqual(fixers, 6)              # ran until the objective test went green

    def test_stalled_red_task_stops_after_stall_rounds_not_a_call_count(self):
        """The SAME failure with no source change is a hard STALL: the loop stops
        after STALL_ROUNDS no-progress cycles -- and the count TRACKS
        STALL_ROUNDS, proving it is the control (not a magic number)."""
        for rounds in (2, 4):
            with self.subTest(stall_rounds=rounds):
                d = _repo_with_plan()

                def check(_r, only_test=None):
                    if only_test and only_test.endswith("T1.test.ts"):
                        return (False, "T1 stays red, unchanged")
                    return (True, "")            # T2 pre-green
                rec = Recorder()
                with mock.patch.dict(
                    os.environ,
                    {"SIGNALOS_BUILD_TASK_STALL_ROUNDS": str(rounds)}):
                    res = run_subagent_driven_build(
                        d, adapter="A", prompt="x", run_agent=rec, build_check=check)
                self.assertEqual(res.status, "budget_exhausted")
                # exactly STALL_ROUNDS fixer passes, then the honest stall.
                self.assertEqual(rec.roles().count("fixer"), rounds)
                self.assertIn("on disk", res.final_text)   # work not discarded

    def test_exhausted_seat_is_resumable_via_on_disk_gate(self):
        """RESUMABILITY (FIX 4): a seat can end 'exhausted' (it reports it hit a
        limit), but its work is ON DISK. The per-task loop trusts only the
        OBJECTIVE test against that on-disk state -- which is green -- so the task
        SUCCEEDS. Work is never discarded because the seat did not finish
        cleanly."""
        d = _repo_with_plan()
        pre = {"T1": True}

        def check(_r, only_test=None):
            if only_test and only_test.endswith("T1.test.ts") and pre["T1"]:
                pre["T1"] = False
                return (False, "T1 red on the pre-check")   # forces one dispatch
            return (True, "")                                # on-disk GREEN after the seat
        rec = Recorder(script={"implementer": [
            "Status: BLOCKED\nI ran out of budget before I could finish."]})
        res = run_subagent_driven_build(d, adapter="A", prompt="x",
                                        run_agent=rec, build_check=check)
        self.assertEqual(res.status, "completed")          # on-disk green wins
        self.assertEqual(rec.roles().count("fixer"), 0)    # gate green right after the seat
        tr = _trace(d)
        t1 = next(r for r in tr["rows"] if r["task"] == "T1")
        self.assertEqual(t1["status"], "green")


class TestRepairAccounting(unittest.TestCase):
    def test_green_build_logs_zero_repairs_and_is_behavior_unchanged(self):
        """A build green after its tasks records repair_attempts=0 (per-task 0,
        integration 0, review 0) AND does zero extra work: the dispatch sequence
        is identical to the pre-change happy path -- no added fixer, no forced
        rerun."""
        d = _repo()
        rec = Recorder()
        res = run_subagent_driven_build(
            d, adapter="A", reviewer_adapter="B", prompt="build a thing",
            run_agent=rec, build_check=_green,
        )
        self.assertEqual(res.status, "completed")
        # behaviour-unchanged: exact same roles as test_happy_path_phases
        self.assertEqual(
            rec.roles(),
            ["implementer", "spec-reviewer", "code-reviewer", "evidence"],
        )
        self.assertNotIn("fixer", rec.roles())  # a green build never repairs
        # logged in the run summary / final_text ...
        self.assertIn("repair_attempts=0", res.final_text)
        self.assertIn("per_task=0, integration=0, review=0", res.final_text)
        # ... and in the machine-readable traceability snapshot the grader reads
        tr = _trace(d)
        self.assertEqual(tr["repair_attempts"], 0)
        self.assertEqual(tr["repairs_by_phase"],
                         {"per_task": 0, "integration": 0, "review": 0})

    def test_green_build_evidence_pass_gets_the_repair_count(self):
        """The evidence dispatch is told the (zero) repair count so
        BUILD_EVIDENCE.md can state it -- surfaced, not hidden."""
        rec = Recorder()
        run_subagent_driven_build(_repo(), adapter="A", prompt="x",
                                  run_agent=rec, build_check=_green)
        evidence_msgs = [m for (role, _), m in zip(rec.calls, rec.messages)
                         if role == "evidence"]
        self.assertEqual(len(evidence_msgs), 1)
        self.assertIn("Repair attempts:", evidence_msgs[0])
        self.assertIn("total 0", evidence_msgs[0])

    def test_per_task_repairs_are_counted(self):
        """A plan task that goes red->green via a fixer records a per-task
        repair (the initial implementer draft is NOT a repair)."""
        state = {"n": 2}  # red on pre-check + post-implementer, green after 1 fix

        def check(_r, only_test=None):
            if only_test and only_test.endswith("T1.test.ts") and state["n"] > 0:
                state["n"] -= 1
                return (False, "T1 assertion failed")
            return (True, "")
        d = _repo_with_plan()
        rec = Recorder()
        res = run_subagent_driven_build(d, adapter="A", prompt="x",
                                        run_agent=rec, build_check=check)
        self.assertEqual(res.status, "completed")
        tr = _trace(d)
        self.assertEqual(tr["repairs_by_phase"]["per_task"], 1)  # one fix pass
        self.assertEqual(tr["repairs_by_phase"]["integration"], 0)
        self.assertEqual(tr["repair_attempts"], 1)
        self.assertIn("repair_attempts=1", res.final_text)

    def test_red_integration_logs_bounded_repairs_and_stays_unverified(self):
        """Integration red after the bounded budget: the attempts are made and
        COUNTED, and the build stays NOT-verified -- never forced green."""
        d = _repo()
        rec = Recorder()
        res = run_subagent_driven_build(
            d, adapter="A", prompt="x", run_agent=rec,
            build_check=lambda r, only_test=None: (False, "still broken"),
            repair_cycles=3,
        )
        # NOT verified: bounded budget exhausted, no forced green
        self.assertEqual(res.status, "budget_exhausted")
        self.assertEqual(rec.roles().count("fixer"), 3)  # bounded at the budget
        self.assertNotIn("evidence", rec.roles())        # a red build cannot sign
        # count logged in final_text ...
        self.assertIn("repair_attempts=3", res.final_text)
        self.assertIn("integration=3", res.final_text)
        # ... and in the machine-readable snapshot, with the honest red state
        tr = _trace(d)
        self.assertEqual(tr["repairs_by_phase"]["integration"], 3)
        self.assertEqual(tr["repair_attempts"], 3)
        self.assertFalse(tr["integration_green"])

    def test_review_fix_counts_as_a_repair(self):
        """A FAIL from an independent reviewer triggers one fixer pass; that is
        a review-phase repair and is counted."""
        d = _repo()
        rec = Recorder(script={"spec-reviewer": ["VERDICT: FAIL: missing delete"]})
        res = run_subagent_driven_build(d, adapter="A", prompt="x",
                                        run_agent=rec, build_check=_green)
        self.assertEqual(res.status, "completed")
        tr = _trace(d)
        self.assertEqual(tr["repairs_by_phase"]["review"], 1)
        self.assertGreaterEqual(tr["repair_attempts"], 1)


# ---------------------------------------------------------------------------
# Reviewer HARD WALL: a reviewer FAIL must gate completion. Running one fixer
# and re-confirming the mechanical build is green does NOT clear an unresolved
# reviewer finding -- the reviewer must RE-RUN and return PASS. And a reviewer
# must be constructed READ-ONLY (no write tools), enforced by the tool set, not
# just prompt text.
# ---------------------------------------------------------------------------


class TestReviewerHardWall(unittest.TestCase):
    def test_persistent_reviewer_fail_blocks_completion_even_when_tests_green(self):
        """RED-FIRST: with the pre-fix code a spec-review FAIL runs ONE fixer,
        re-checks green, and the build still COMPLETES + writes evidence -- the
        verdict never gates. The wall: an unresolved FAIL must NOT complete and
        must NOT reach the paid evidence pass."""
        rec = Recorder(script={"spec-reviewer": ["VERDICT: FAIL: missing delete"] * 12})
        res = run_subagent_driven_build(_repo(), adapter="A", prompt="x",
                                        run_agent=rec, build_check=_green)
        self.assertNotEqual(res.status, "completed")   # unresolved FAIL -> not done
        self.assertEqual(res.status, "budget_exhausted")
        self.assertNotIn("evidence", rec.roles())      # fail-fast before paid evidence
        self.assertIn("reviewer hard wall", res.error or "")
        # the reviewer was actually RE-RUN (not judged once) -- more than one call
        self.assertGreater(rec.roles().count("spec-reviewer"), 1)

    def test_reviewer_fail_then_pass_on_rerun_completes(self):
        """A FAIL the fixer resolves: the RE-RUN reviewer PASSES, so the build
        completes and reaches evidence. This locks the required re-run+PASS."""
        rec = Recorder(script={"spec-reviewer": ["VERDICT: FAIL: missing delete",
                                                 "VERDICT: PASS"]})
        res = run_subagent_driven_build(_repo(), adapter="A", prompt="x",
                                        run_agent=rec, build_check=_green)
        self.assertEqual(res.status, "completed")
        roles = rec.roles()
        # spec: FAIL -> fixer -> re-run spec (PASS) -> code-review -> evidence
        self.assertEqual(roles.count("spec-reviewer"), 2)  # re-ran to confirm PASS
        self.assertIn("fixer", roles)
        self.assertIn("evidence", roles)

    def test_review_blocked_finding_is_persisted_to_traceability(self):
        """The blocking finding is written to the machine-readable trace so a
        grader/audit can see WHY the build stopped."""
        rec = Recorder(script={"code-reviewer": ["VERDICT: FAIL: God object in App.tsx"] * 12})
        d = _repo()
        res = run_subagent_driven_build(d, adapter="A", prompt="x",
                                        run_agent=rec, build_check=_green)
        self.assertEqual(res.status, "budget_exhausted")
        tr = _trace(d)
        self.assertEqual(tr.get("phase"), "review-blocked")
        blocked = tr.get("review_blocked") or []
        self.assertTrue(any("code:" in b for b in blocked), blocked)


class TestReviewerReadOnlyTools(unittest.TestCase):
    def test_reviewer_roles_are_readonly_wrapped_writers_are_not(self):
        """RED-FIRST: the read-only wrapper/selector did not exist, so reviewers
        ran with the full write-capable adapter. Reviewers must be wrapped;
        implementer/fixer/evidence must keep the real adapter."""
        from signalos_lib.product.subagent_build import (
            _ReadOnlyReviewerAdapter, _loop_adapter_for_role)
        base = object()
        self.assertIsInstance(_loop_adapter_for_role("spec-reviewer", base),
                              _ReadOnlyReviewerAdapter)
        self.assertIsInstance(_loop_adapter_for_role("code-reviewer", base),
                              _ReadOnlyReviewerAdapter)
        # writers keep the real, write-capable adapter (identity preserved)
        self.assertIs(_loop_adapter_for_role("implementer", base), base)
        self.assertIs(_loop_adapter_for_role("fixer", base), base)
        self.assertIs(_loop_adapter_for_role("evidence", base), base)

    def test_readonly_adapter_strips_write_and_command_tools(self):
        """The reviewer's AgentLoop is handed a tool set WITHOUT write_file /
        edit_file / run_command -- enforced by the schema, not the prompt."""
        from signalos_lib.product.agent_loop import AGENT_TOOLS
        from signalos_lib.product.subagent_build import _ReadOnlyReviewerAdapter

        class _RecordingInner:
            supports_tool_calls = True

            def __init__(self):
                self.tools_seen = None
                self.tool_choice_seen = "unset"

            def chat(self, *, messages, tools=None, tool_choice=None, **kw):
                self.tools_seen = tools
                self.tool_choice_seen = tool_choice
                return "resp"

        inner = _RecordingInner()
        wrapped = _ReadOnlyReviewerAdapter(inner)
        all_tools = [t.as_openai_tool() for t in AGENT_TOOLS]
        self.assertTrue(any(t["function"]["name"] == "write_file" for t in all_tools))

        wrapped.chat(messages=[], tools=all_tools)
        names = {t["function"]["name"] for t in inner.tools_seen}
        self.assertNotIn("write_file", names)
        self.assertNotIn("edit_file", names)
        self.assertNotIn("run_command", names)
        self.assertEqual(names, {"read_file", "search_files", "list_directory"})
        # delegation + escalation path intact
        self.assertTrue(wrapped.supports_tool_calls)
        wrapped.chat(messages=[], tools=all_tools, tool_choice="required")
        self.assertEqual(inner.tool_choice_seen, "required")
        # a text-only turn (tools=None) stays None
        wrapped.chat(messages=[], tools=None)
        self.assertIsNone(inner.tools_seen)

    def test_default_run_agent_runs_reviewer_with_only_readonly_tools(self):
        """End-to-end through _default_run_agent: a reviewer role's real loop
        only ever advertises read-only tools to the model."""
        from signalos_lib.product.subagent_build import _default_run_agent
        from signalos_lib.harness import AgentResponse, TokenUsage
        from signalos_lib.harness import ToolCall
        from signalos_lib.product.provider_adapter import (
            ProviderAdapter, ProviderCapabilities)
        from signalos_lib.product.enforcement_state import (
            StaticEnforcementProvider, seed_trust_tier_paths)

        d = Path(tempfile.mkdtemp())
        seed_trust_tier_paths(d)

        class _RecordingProvider:
            supports_tool_calls = True
            supports_streaming = False

            def __init__(self):
                self.tools_seen: list = []
                self._script = [
                    AgentResponse(content="", tool_calls=[
                        ToolCall(id="c1", name="list_directory", arguments={"path": "."})],
                        stop_reason="tool_use", usage=TokenUsage(1, 1)),
                    AgentResponse(content="VERDICT: PASS", tool_calls=None,
                                  stop_reason="end_turn", usage=TokenUsage(1, 1)),
                ]

            def chat(self, *, messages, model=None, tools=None, stream=False, tool_choice=None):
                self.tools_seen.append(tools)
                return self._script.pop(0)

        provider = _RecordingProvider()
        caps = ProviderCapabilities(model="m", supports_tool_calls=True,
                                    supports_streaming=False, context_length=200_000)
        adapter = ProviderAdapter(model="m", provider=provider, capabilities=caps)
        run = _default_run_agent(
            d, StaticEnforcementProvider(trust_tier="T3"), lambda _e: None,
            "default", [0, 1, 2, 3])

        report = run("spec-reviewer", adapter, "sys", "review it")
        self.assertIn("VERDICT: PASS", report)
        # every advertised tool set the reviewer saw excluded writes
        for tools in provider.tools_seen:
            if not tools:
                continue
            names = {t["function"]["name"] for t in tools}
            self.assertNotIn("write_file", names)
            self.assertNotIn("edit_file", names)
            self.assertNotIn("run_command", names)
            self.assertIn("read_file", names)


# ---------------------------------------------------------------------------
# Per-task DEFINITION-OF-DONE hard gate
# ---------------------------------------------------------------------------

class TestVacuousTestDetection(unittest.TestCase):
    def test_no_assertion_is_vacuous(self):
        self.assertTrue(is_vacuous_test("it('x', () => { doThing(); });"))

    def test_literal_tautology_is_vacuous(self):
        self.assertTrue(is_vacuous_test("it('a', () => { expect(true).toBe(true); });"))
        self.assertTrue(is_vacuous_test("test('b', () => { expect(1).toBe(1); });"))

    def test_real_assertion_is_not_vacuous(self):
        self.assertFalse(is_vacuous_test(
            "it('x', () => { render(<App/>); "
            "expect(screen.getByRole('button')).toBeInTheDocument(); });"))
        self.assertFalse(is_vacuous_test(
            "it('y', () => { expect(add(2, 3)).toBe(5); });"))

    def test_non_test_text_is_not_flagged(self):
        self.assertFalse(is_vacuous_test("export const x = 1;\n"))


class TestTaskDodViolations(unittest.TestCase):
    def _repo(self) -> Path:
        return Path(tempfile.mkdtemp())

    def test_dead_code_is_flagged(self):
        d = self._repo()
        (d / "src").mkdir(parents=True)
        (d / "src" / "orphan.ts").write_text(
            "export const orphan = () => 42;\n", encoding="utf-8")
        task = Task(id="T1", name="T", text="", files=["src/orphan.ts"])
        viol = task_dod_violations(d, task, source_dir="src")
        self.assertTrue(any("dead" in v for v in viol), viol)

    def test_wired_code_is_not_flagged(self):
        d = self._repo()
        (d / "src").mkdir(parents=True)
        (d / "src" / "thing.ts").write_text(
            "export const thing = () => 1;\n", encoding="utf-8")
        (d / "src" / "consumer.ts").write_text(
            "import { thing } from './thing';\nthing();\n", encoding="utf-8")
        task = Task(id="T1", name="T", text="", files=["src/thing.ts"])
        self.assertEqual(task_dod_violations(d, task, source_dir="src"), [])

    def test_complexity_ceiling_is_flagged(self):
        d = self._repo()
        (d / "src").mkdir(parents=True)
        body = "\n".join(f"  if (x === {i}) return {i};" for i in range(60))
        (d / "src" / "big.ts").write_text(
            f"export function big(x: number) {{\n{body}\n  return 0;\n}}\n",
            encoding="utf-8")
        (d / "src" / "consumer.ts").write_text(
            "import { big } from './big';\nbig(1);\n", encoding="utf-8")
        task = Task(id="T1", name="T", text="", files=["src/big.ts"])
        viol = task_dod_violations(d, task, source_dir="src")
        self.assertTrue(any("complex" in v for v in viol), viol)

    def test_unlabeled_input_is_flagged_a11y(self):
        d = self._repo()
        (d / "src").mkdir(parents=True)
        (d / "src" / "Form.tsx").write_text(
            "export const Form = () => <form><input /></form>;\n", encoding="utf-8")
        (d / "src" / "App.tsx").write_text(
            "import { Form } from './Form';\nexport default () => <Form/>;\n",
            encoding="utf-8")
        task = Task(id="T1", name="T", text="", files=["src/Form.tsx"])
        viol = task_dod_violations(d, task, source_dir="src")
        self.assertTrue(any("a11y" in v for v in viol), viol)

    def test_labeled_wired_component_is_clean(self):
        d = self._repo()
        (d / "src").mkdir(parents=True)
        (d / "src" / "Form.tsx").write_text(
            'export const Form = () => (<form>'
            '<label htmlFor="n">Name</label><input id="n" />'
            '<button>Save</button></form>);\n', encoding="utf-8")
        (d / "src" / "App.tsx").write_text(
            "import { Form } from './Form';\nexport default () => <Form/>;\n",
            encoding="utf-8")
        task = Task(id="T1", name="T", text="", files=["src/Form.tsx"])
        self.assertEqual(task_dod_violations(d, task, source_dir="src"), [])


def _dod_plan_repo(test_body: str, *, impl_rel: str = "src/store/s.ts",
                   impl_body: str = "export const s = 1;\n") -> Path:
    """A signed-plan repo whose T1 carries a REAL on-disk impl file + test, so
    the DoD gate has files to evaluate. T2 is a trivial second task (the plan
    parser needs >=2 task headings)."""
    d = Path(tempfile.mkdtemp())
    # Make the repo resolve to the react-vite stack so the DoD scan targets the
    # 'src' source dir deterministically (source-dir detection is otherwise
    # stack-dependent).
    (d / "package.json").write_text(json.dumps({
        "dependencies": {"react": "^18.3.1"},
        "devDependencies": {"vite": "^5.4.0", "vitest": "^3.2.0"},
        "scripts": {"build": "tsc && vite build", "test": "vitest run"},
    }), encoding="utf-8")
    (d / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    plan = d / "core" / "execution" / "PLAN.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        "# Plan\n\n"
        f"### T1 — Store\n**Files:** `{impl_rel}`\n"
        "**Test:** `core/execution/tests/T1.test.ts`\n"
        "Acceptance: the store works\n\n"
        "### T2 — Other\n**Files:** `src/other.ts`\n"
        "**Test:** `core/execution/tests/T2.test.ts`\n"
        "Acceptance: the other works\n", encoding="utf-8")
    impl = d / impl_rel
    impl.parent.mkdir(parents=True, exist_ok=True)
    impl.write_text(impl_body, encoding="utf-8")
    t1 = d / "core" / "execution" / "tests" / "T1.test.ts"
    t1.parent.mkdir(parents=True, exist_ok=True)
    t1.write_text(test_body, encoding="utf-8")
    return d


def _red_once_for(test_suffix: str):
    """build_check that returns RED once for the given per-task test (so the
    impl loop runs and reaches the DoD gate), GREEN otherwise."""
    state = {"n": 1}

    def check(_r, only_test=None):
        if only_test and only_test.endswith(test_suffix) and state["n"] > 0:
            state["n"] -= 1
            return (False, "red")
        return (True, "")
    return check


class TestPerTaskDodGate(unittest.TestCase):
    def test_vacuous_test_blocks_the_build_fail_fast(self):
        # A task whose acceptance test is a literal tautology cannot be "done":
        # the DoD gate blocks it, the build fails fast, and NO evidence pass is
        # paid (the missing Build Evidence keeps the G4 sign fail-closed).
        d = _dod_plan_repo("it('x', () => { expect(true).toBe(true); });")
        rec = Recorder()
        res = run_subagent_driven_build(
            d, adapter="A", prompt="x", run_agent=rec,
            build_check=_red_once_for("T1.test.ts"))
        self.assertEqual(res.status, "budget_exhausted")
        self.assertNotIn("evidence", rec.roles())
        self.assertIn("dod_failed", res.final_text)

    def test_dead_code_blocks_when_unresolved(self):
        # A task that leaves an unwired module (dead code) is blocked by the DoD
        # gate after its bounded fixer budget cannot resolve it.
        d = _dod_plan_repo("it('x', () => { expect(s).toBe(1); });",
                           impl_rel="src/orphan.ts",
                           impl_body="export const orphan = () => 42;\n")
        rec = Recorder()  # the fake fixer never actually wires it in
        res = run_subagent_driven_build(
            d, adapter="A", prompt="x", run_agent=rec,
            build_check=_red_once_for("T1.test.ts"))
        self.assertEqual(res.status, "budget_exhausted")
        self.assertIn("fixer", rec.roles())          # DoD dispatched a fixer
        self.assertNotIn("evidence", rec.roles())
        self.assertIn("dod_failed", res.final_text)

    def test_clean_task_meets_dod_and_build_completes(self):
        # A real assertion + wired impl -> DoD is clean -> the build completes
        # (a genuinely-good task is NEVER false-blocked by the DoD gate).
        d = _dod_plan_repo("it('x', () => { expect(s).toBe(1); });")
        (d / "src" / "consumer.ts").write_text(
            "import { s } from './store/s';\ns;\n", encoding="utf-8")
        rec = Recorder()
        res = run_subagent_driven_build(
            d, adapter="A", prompt="x", run_agent=rec,
            build_check=_red_once_for("T1.test.ts"))
        self.assertEqual(res.status, "completed")
        self.assertIn("evidence", rec.roles())


if __name__ == "__main__":
    unittest.main()
