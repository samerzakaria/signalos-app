"""Per-project namespacing of the PLAN file, the worktree state writer, and
the signed gate artifacts (WAVE-ENGINE-DESIGN §3.2 milestone).

Pins three contracts:

1. Fix 3 — worktree-manager.sh writes worktree-state.json to the SAME path
   projects.project_state_dir resolves for the Python readers: invocation
   plumbing (`--project-id` appended only for non-default projects) plus a
   textual resolver-agreement check on the bash script (invoking real bash
   on Windows CI is fragile, so the writer/reader agreement is pinned at
   the shared-resolver level).

2. Fix 4 — PLAN.tasks.yaml resolves through projects.project_plan_path
   everywhere: run_wave's no-worktree fallback, status plan loading, and
   the writing-plans skill validator. Default project is byte-identical;
   a missing per-project plan behaves like a missing root plan.

3. Fix 5 — the §3.2 invariant: a gate signed as project "alpha" is seen
   signed by alpha's sign --check / wave_engine.inspect / status /
   orchestrator gating, and NOT by the default project. All readers and
   writers share ONE resolver (projects.project_governance_dir), so the
   engine and the status board cannot disagree.

4. Creation side — the delivery bridge (GateOrchestrator -> AgentLoop)
   GENERATES gate artifacts in the same namespace it signs them: writes
   addressed to the canonical gate-artifact subtrees (core/governance/**,
   core/strategy/**, core/execution/**) physically rebase under
   project_governance_dir for a non-default project, product-source writes
   never rebase, the default project stays byte-identical, and trust-tier /
   `.signalos/` enforcement is neither bypassed nor loosened.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import seed_signed_gate
from signalos_lib import orchestrator, projects, sign, status as status_lib, wave_engine
from signalos_lib.artifacts import resolve_gate_artifacts, resolve_workspace_path
from signalos_lib.harness import AgentResponse, TokenUsage, ToolCall
from signalos_lib.plan import PlanDoc, Task, dump_tasks
from signalos_lib.product.agent_loop import AgentLoop
from signalos_lib.product.enforcement_state import StaticEnforcementProvider
from signalos_lib.product.gate_orchestrator import GateOrchestrator, resume_delivery
from signalos_lib.projects import (
    project_governance_dir,
    project_plan_path,
    project_state_dir,
)
from signalos_lib.skill_validators import validate_skill_artifacts

_ULID = "01HABCDEFGHJKMNPQRSTVWXYZ0"

# Non-template content: _is_non_template needs >= 3 filled lines that are
# neither comments nor headings and no template markers.
_REAL_CONTENT = (
    "Real content line one describing the product.\n"
    "Real content line two with concrete decisions.\n"
    "Real content line three, definitely not a scaffold.\n"
)


def _write(path: Path, content: str = _REAL_CONTENT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _workspace(d: str) -> Path:
    root = Path(d).resolve()
    (root / ".signalos").mkdir(parents=True, exist_ok=True)
    return root


def _plan_doc(wave: str, title: str) -> PlanDoc:
    return PlanDoc(
        wave=wave,
        tasks=[Task(id=_ULID, title=title, status="pending", tier="T1",
                    description=title)],
    )


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------

class ResolverTests(unittest.TestCase):
    def test_project_plan_path_default_is_workspace_root(self) -> None:
        root = Path("R")
        self.assertEqual(project_plan_path(root), root / "PLAN.tasks.yaml")

    def test_project_plan_path_namespaces_other_ids(self) -> None:
        root = Path("R")
        self.assertEqual(
            project_plan_path(root, "alpha"),
            project_state_dir(root, "alpha") / "PLAN.tasks.yaml",
        )

    def test_project_governance_dir_default_is_root_itself(self) -> None:
        root = Path("R")
        self.assertEqual(project_governance_dir(root), root)

    def test_project_governance_dir_namespaces_other_ids(self) -> None:
        root = Path("R")
        self.assertEqual(
            project_governance_dir(root, "alpha"),
            root / ".signalos" / "projects" / "alpha" / "governance",
        )

    def test_resolve_workspace_path_keeps_rel_structure_identical(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            rel = "core/strategy/EXPECTATION_MAP.md"
            default_path = resolve_workspace_path(root, rel)
            alpha_path = resolve_workspace_path(root, rel, project_id="alpha")
            self.assertEqual(default_path, (root / rel).resolve())
            self.assertEqual(
                alpha_path,
                (project_governance_dir(root, "alpha") / rel).resolve(),
            )
            # Same canonical rel structure under both bases.
            self.assertTrue(str(alpha_path).replace("\\", "/").endswith(rel))
            # Namespaced paths still cannot escape the workspace.
            with self.assertRaises(ValueError):
                resolve_workspace_path(root, "../escape.md", project_id="alpha")


# ---------------------------------------------------------------------------
# Fix 3 — worktree-manager.sh state routing
# ---------------------------------------------------------------------------

_WM_SCRIPT = (
    Path(__file__).resolve().parent
    / "signalos_lib" / "_bundle" / "core" / "execution" / "build"
    / "worktree-manager.sh"
)


class WorktreeStateWriterTests(unittest.TestCase):
    def test_script_state_path_agrees_with_python_resolver(self) -> None:
        """The bash writer and the Python reader must derive the SAME
        worktree-state.json path from a project id. The script's two
        branches are pinned against project_state_dir's two branches."""
        text = _WM_SCRIPT.read_text(encoding="utf-8")
        root = Path("R")

        default_rel = project_state_dir(root, "default") / "worktree-state.json"
        default_suffix = default_rel.relative_to(root).as_posix()
        self.assertIn(
            'STATE_FILE="${REPO_ROOT}/' + default_suffix + '"', text,
            "default branch of the script must write the resolver's default path",
        )

        alpha_rel = project_state_dir(root, "alpha") / "worktree-state.json"
        alpha_suffix = alpha_rel.relative_to(root).as_posix()
        templated = 'STATE_FILE="${REPO_ROOT}/' + alpha_suffix.replace(
            "/alpha/", "/${PROJECT_ID}/",
        ) + '"'
        self.assertIn(
            templated, text,
            "non-default branch of the script must write the resolver's "
            "namespaced path",
        )
        # And the script must actually accept the flag + env var.
        self.assertIn("--project-id", text)
        self.assertIn("SIGNALOS_PROJECT_ID", text)

    def test_run_wm_appends_project_id_only_for_non_default(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            wm = root / "core" / "execution" / "build" / "worktree-manager.sh"
            _write(wm, "#!/usr/bin/env bash\n")
            captured: list[list[str]] = []

            def fake_run(cmd, **kwargs):
                captured.append(list(cmd))
                return mock.Mock(returncode=0)

            with mock.patch.object(orchestrator.subprocess, "run", fake_run):
                orchestrator._run_wm(root, "status", "--wave", "1")
                orchestrator._run_wm(
                    root, "status", "--wave", "1", project_id="default",
                )
                orchestrator._run_wm(
                    root, "status", "--wave", "1", project_id="alpha",
                )

        self.assertNotIn("--project-id", captured[0],
                         "default invocation must stay byte-identical")
        self.assertNotIn("--project-id", captured[1])
        self.assertIn("--project-id", captured[2])
        self.assertEqual(
            captured[2][captured[2].index("--project-id") + 1], "alpha",
        )

    def test_orchestrator_reader_resolves_same_path_the_script_writes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            state = project_state_dir(root, "alpha") / "worktree-state.json"
            _write(state, json.dumps({"worktrees": [
                {"task": "1", "branch": "wave-1/task-1", "step_id": "s1",
                 "wave": "1", "status": "active"},
            ]}))
            tasks = orchestrator._read_tasks(root, project_id="alpha")
            self.assertEqual(len(tasks), 1)
            # The default project must not see alpha's tasks.
            self.assertEqual(orchestrator._read_tasks(root), [])


# ---------------------------------------------------------------------------
# Fix 4 — PLAN.tasks.yaml per-project
# ---------------------------------------------------------------------------

class PlanNamespacingTests(unittest.TestCase):
    def test_status_loads_the_projects_own_plan(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            dump_tasks(_plan_doc("7", "Root task"), root / "PLAN.tasks.yaml")
            alpha_plan = project_plan_path(root, "alpha")
            alpha_plan.parent.mkdir(parents=True, exist_ok=True)
            dump_tasks(_plan_doc("9", "Alpha task"), alpha_plan)

            root_tasks, root_wave = status_lib._load_plan_doc(root)
            alpha_tasks, alpha_wave = status_lib._load_plan_doc(
                root, project_id="alpha",
            )
            self.assertEqual(root_wave, "7")
            self.assertEqual([t["title"] for t in root_tasks], ["Root task"])
            self.assertEqual(alpha_wave, "9")
            self.assertEqual([t["title"] for t in alpha_tasks], ["Alpha task"])

    def test_missing_per_project_plan_behaves_like_missing_root_plan(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            # Root plan exists, but project "beta" has none: beta must NOT
            # fall back to the global plan (that is the bug being fixed).
            dump_tasks(_plan_doc("7", "Root task"), root / "PLAN.tasks.yaml")
            tasks, wave = status_lib._load_plan_doc(root, project_id="beta")
            self.assertEqual((tasks, wave), ([], ""))

    def test_run_wave_resolves_plan_through_project_plan_path(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            seen: dict[str, Path] = {}

            def fake_tasks_from_plan(plan_path, wave_id):
                seen["plan"] = Path(plan_path)
                return []

            with mock.patch.object(
                orchestrator, "_route_next_gate_action",
                return_value={"action": "build", "current_gate": "G4",
                              "evidence": "test"},
            ), mock.patch.object(
                orchestrator, "_resolve_provider", return_value=object(),
            ), mock.patch.object(
                orchestrator, "_bash_available", return_value=False,
            ), mock.patch.object(
                orchestrator, "_tasks_from_plan", fake_tasks_from_plan,
            ):
                result = orchestrator.run_wave(
                    "1", None, session_id="s", cwd=root, project_id="alpha",
                )
                self.assertEqual(result["status"], "empty")
                self.assertEqual(seen["plan"], project_plan_path(root, "alpha"))

                # Default project: byte-identical resolution to the
                # workspace-root PLAN.tasks.yaml.
                result = orchestrator.run_wave("1", None, session_id="s", cwd=root)
                self.assertEqual(seen["plan"], project_plan_path(root))
                self.assertEqual(seen["plan"], root / "PLAN.tasks.yaml")

    def test_writing_plans_validator_checks_the_projects_plan(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            alpha_plan = project_plan_path(root, "alpha")
            alpha_plan.parent.mkdir(parents=True, exist_ok=True)
            dump_tasks(_plan_doc("1", "Alpha task"), alpha_plan)

            # Alpha task: its own plan exists -> no violation, even though
            # the workspace root has no PLAN.tasks.yaml.
            violations = validate_skill_artifacts(
                skills=["writing-plans"],
                task={"task": "1", "project_id": "alpha"},
                root=root,
                written_files=[],
            )
            self.assertEqual(violations, [])

            # Default task: root plan missing -> violation (unchanged).
            violations = validate_skill_artifacts(
                skills=["writing-plans"],
                task={"task": "1"},
                root=root,
                written_files=[],
            )
            self.assertTrue(
                any(v.skill == "writing-plans" for v in violations),
            )


# ---------------------------------------------------------------------------
# Fix 5 — signed gate artifacts: one resolver, every reader agrees
# ---------------------------------------------------------------------------

class GovernanceNamespacingInvariantTests(unittest.TestCase):
    def _sign_alpha_g2(self, root: Path) -> Path:
        artifact = project_governance_dir(root, "alpha").joinpath(
            "core", "strategy", "EXPECTATION_MAP.md",
        )
        _write(artifact)
        signed = sign.sign_gate(
            root, "G2", "Alice Example", "PO", "APPROVED", project_id="alpha",
        )
        self.assertEqual(signed, ["core/strategy/EXPECTATION_MAP.md"])
        return artifact

    def test_sign_check_inspect_status_agree_for_alpha_and_not_default(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            artifact = self._sign_alpha_g2(root)
            self.assertIn("## Signatures", artifact.read_text(encoding="utf-8"))

            # sign --check (check_gate): alpha sees the signature.
            alpha_status = {
                s.rel_path: s for s in sign.check_gate(root, "G2", project_id="alpha")
            }["core/strategy/EXPECTATION_MAP.md"]
            self.assertTrue(alpha_status.exists)
            self.assertTrue(alpha_status.has_signatures)
            self.assertEqual(alpha_status.signers, ["Alice Example"])

            # ... and the default project does NOT.
            default_status = {
                s.rel_path: s for s in sign.check_gate(root, "G2")
            }["core/strategy/EXPECTATION_MAP.md"]
            self.assertFalse(default_status.exists)

            # wave_engine.inspect: same verdicts from the engine.
            alpha_insp = wave_engine.inspect(root, project_id="alpha")
            self.assertTrue(alpha_insp["gates"]["G2"])
            self.assertTrue(alpha_insp["artifacts"]["G2"]["exists"])
            self.assertIn(
                str(project_governance_dir(root, "alpha")),
                alpha_insp["artifacts"]["G2"]["path"],
            )
            default_insp = wave_engine.inspect(root)
            self.assertFalse(default_insp["gates"]["G2"])
            self.assertFalse(default_insp["artifacts"]["G2"]["exists"])

            # status: same verdicts from the board.
            self.assertTrue(
                status_lib.get_wave_status(root, project_id="alpha")["gates"]["G2"],
            )
            self.assertFalse(status_lib.get_wave_status(root)["gates"]["G2"])

    def test_orchestrator_gating_reads_the_projects_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            base = project_governance_dir(root, "alpha")
            # Materialise G0..G3 in alpha's namespace, FULLY signed — gate
            # detection is signature-based and fail-closed on the whole
            # manifest, so every required artifact of each gate is signed.
            for gate in ("G0", "G1", "G2", "G3"):
                seed_signed_gate(base, gate, default_content=_REAL_CONTENT)

            alpha_route = orchestrator._route_next_gate_action(
                root, "1", "s", project_id="alpha",
            )
            self.assertEqual(alpha_route["action"], "build")

            default_route = orchestrator._route_next_gate_action(root, "1", "s")
            self.assertEqual(default_route["action"], "fire-agent-G0")

    def test_gate_orchestrator_default_sign_uses_the_same_namespace(self) -> None:
        from signalos_lib.product.gate_orchestrator import _default_sign

        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            artifact = project_governance_dir(root, "alpha").joinpath(
                "core", "strategy", "EXPECTATION_MAP.md",
            )
            _write(artifact)
            signed = _default_sign(
                root, "G2", "Founder", "PO", "APPROVED", "", project_id="alpha",
            )
            self.assertEqual(signed, ["core/strategy/EXPECTATION_MAP.md"])
            self.assertIn("## Signatures", artifact.read_text(encoding="utf-8"))
            # Root layout untouched.
            self.assertFalse(
                (root / "core" / "strategy" / "EXPECTATION_MAP.md").exists(),
            )

    def test_default_project_layout_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            resolved = resolve_gate_artifacts(root, "G2")
            self.assertEqual(
                [a.path for a in resolved],
                [(root / "core" / "strategy" / "EXPECTATION_MAP.md").resolve()],
            )
            artifact = _write(root / "core" / "strategy" / "EXPECTATION_MAP.md")
            signed = sign.sign_gate(root, "G2", "Bob Example", "PO", "APPROVED")
            self.assertEqual(signed, ["core/strategy/EXPECTATION_MAP.md"])
            self.assertIn("## Signatures", artifact.read_text(encoding="utf-8"))
            # Alpha's namespace is untouched by a default-project sign.
            self.assertFalse(project_governance_dir(root, "alpha").exists())


# ---------------------------------------------------------------------------
# Creation side — the delivery bridge writes gate artifacts in-namespace
# ---------------------------------------------------------------------------

_G2_REL = "core/strategy/EXPECTATION_MAP.md"


class _ScriptedWriteAdapter:
    """Deterministic adapter double for the delivery bridge: the first
    tool-loop turn writes one gate artifact via the loop's own write path,
    every later turn (including brief/critic calls) ends immediately."""

    supports_tool_calls = True

    def __init__(self, rel_path: str, content: str) -> None:
        self._script = [
            AgentResponse(
                content=None,
                tool_calls=[ToolCall(
                    id="t-write", name="write_file",
                    arguments={"path": rel_path, "content": content},
                )],
                stop_reason="tool_use", usage=TokenUsage(),
            ),
        ]

    def chat(self, messages, model="test", tools=None, stream=False):
        if tools and self._script:
            return self._script.pop(0)
        return AgentResponse(content="(gate work done)", tool_calls=None,
                             stop_reason="end_turn", usage=TokenUsage())


class DeliveryBridgeCreationNamespacingTests(unittest.TestCase):
    def _delivery(self, root: Path, project_id: str) -> tuple[GateOrchestrator, list]:
        events: list[dict] = []
        orch = GateOrchestrator(
            root, _ScriptedWriteAdapter(_G2_REL, _REAL_CONTENT), events.append,
            enforcement_provider=StaticEnforcementProvider(),  # T2, all strict
            prompt="build task management", project_id=project_id,
        )
        return orch, events

    def test_non_default_delivery_generates_and_signs_in_its_namespace(self) -> None:
        """End-to-end alpha bridge: the gate agent's write lands under
        .signalos/projects/alpha/governance/core/..., the production sign
        path reads it there, and the default root stays clean."""
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            orch, events = self._delivery(root, "alpha")
            orch._run_gate("G2")

            expected = project_governance_dir(root, "alpha").joinpath(
                *_G2_REL.split("/"))
            self.assertTrue(expected.is_file(),
                            "gate-agent write must land in alpha's namespace")
            self.assertEqual(expected.read_text(encoding="utf-8"), _REAL_CONTENT)
            self.assertFalse((root / "core").exists(),
                             "default root must stay clean")

            # Production _default_sign (no sign_fn override) must find and
            # sign the artifact where the loop wrote it.
            res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "advanced")
            self.assertIn("G2", orch.state.signed)
            self.assertIn("## Signatures", expected.read_text(encoding="utf-8"))
            self.assertFalse((root / "core").exists())

            # Readers agree: alpha sees the signed gate, default does not.
            self.assertTrue(wave_engine.inspect(root, project_id="alpha")["gates"]["G2"])
            self.assertFalse(wave_engine.inspect(root)["gates"]["G2"])
            self.assertFalse(
                any(e.get("type") == "error" for e in events),
                f"unexpected error events: {[e for e in events if e.get('type') == 'error']}",
            )

    def test_default_delivery_writes_at_root_exactly_as_before(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            orch, _events = self._delivery(root, "default")
            orch._run_gate("G2")

            artifact = root.joinpath(*_G2_REL.split("/"))
            self.assertTrue(artifact.is_file(),
                            "default project must keep the root layout")
            self.assertFalse((root / ".signalos" / "projects").exists())

            res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "advanced")
            self.assertIn("## Signatures", artifact.read_text(encoding="utf-8"))
            self.assertTrue(wave_engine.inspect(root)["gates"]["G2"])

    def test_project_binding_survives_persist_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            orch, _events = self._delivery(root, "alpha")
            orch._run_gate("G2")
            persisted = json.loads(
                (root / ".signalos" / "agent-runs" / orch.state.run_id
                 / "delivery.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["project_id"], "alpha")

            resumed = resume_delivery(
                root, orch.state.run_id,
                _ScriptedWriteAdapter(_G2_REL, _REAL_CONTENT), lambda _e: None,
            )
            self.assertEqual(resumed.project_id, "alpha")
            self.assertEqual(resumed.state.project_id, "alpha")


class AgentLoopRebaseEnforcementTests(unittest.TestCase):
    """The rebase hook's path classes + the enforcement contract: rebased
    governance writes pass trust-tier (canonical rel_path is the policy
    identity), non-governance paths never rebase, `.signalos/` writes stay
    forbidden, traversal cannot steer the rebase."""

    def _loop(self, root: Path, project_id: str) -> AgentLoop:
        loop = AgentLoop(
            adapter=object(),  # governance checks never call the provider
            repo_root=root,
            enforcement_provider=StaticEnforcementProvider(),  # T2, all strict
            execution_context="delivery",
            project_id=project_id,
        )
        self.assertIsNone(loop._load_enforcement())
        return loop

    @staticmethod
    def _write(loop: AgentLoop, path: str, content: str = _REAL_CONTENT) -> str:
        return loop._dispatch_tool(ToolCall(
            id=f"t-{path}", name="write_file",
            arguments={"path": path, "content": content},
        ))

    def test_governance_write_rebases_and_passes_trust_tier(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            loop = self._loop(root, "alpha")
            rel = "core/governance/Governance/SOUL-DOCUMENT.md"
            res = self._write(loop, rel)
            self.assertTrue(res.startswith("OK"), res)
            base = project_governance_dir(root, "alpha")
            self.assertTrue(base.joinpath(*rel.split("/")).is_file())
            self.assertFalse((root / "core").exists())
            # The loop's own read path resolves the SAME rebased file.
            read = loop._dispatch_tool(ToolCall(
                id="t-read", name="read_file", arguments={"path": rel}))
            # write_text translates newlines on Windows; compare normalized.
            self.assertEqual(read.replace("\r\n", "\n"), _REAL_CONTENT)

    def test_search_files_finds_rebased_governance_artifacts(self) -> None:
        """search_files must agree with read_file/list_directory: a
        non-default project's gate artifacts are discoverable via glob and
        reported as canonical rel_paths; default's root artifacts and
        product-source matches are unaffected."""
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            loop = self._loop(root, "alpha")
            rel = "core/governance/Governance/SOUL-DOCUMENT.md"
            self._write(loop, rel)
            self._write(loop, "src/App.css", "body {}\n")
            out = loop._dispatch_tool(ToolCall(
                id="t-search", name="search_files",
                arguments={"pattern": "core/governance/**/*.md"}))
            self.assertIn(rel, out.splitlines())
            # Product-source glob still resolves from the workspace root.
            out_src = loop._dispatch_tool(ToolCall(
                id="t-search-src", name="search_files",
                arguments={"pattern": "src/*.css"}))
            self.assertIn("src/App.css", out_src.splitlines())
            # Default project: same glob finds nothing (alpha's artifacts
            # are invisible to default — the read-side invariant holds for
            # search too).
            default_loop = self._loop(root, "default")
            out_default = default_loop._dispatch_tool(ToolCall(
                id="t-search-def", name="search_files",
                arguments={"pattern": "core/governance/**/*.md"}))
            self.assertNotIn(rel, out_default.splitlines())

    def test_non_governance_write_is_untouched_by_rebasing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            loop = self._loop(root, "alpha")
            res = self._write(loop, "src/App.css", "body { color: red }\n")
            self.assertTrue(res.startswith("OK"), res)
            self.assertTrue((root / "src" / "App.css").is_file(),
                            "product-source writes must stay at the repo root")
            self.assertFalse(
                (project_governance_dir(root, "alpha") / "src").exists())

    def test_direct_signalos_write_stays_forbidden_for_any_project(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            loop = self._loop(root, "alpha")
            res = self._write(
                loop, ".signalos/projects/alpha/governance/core/strategy/X.md")
            self.assertTrue(res.startswith("DENIED"), res)

    def test_traversal_segments_never_steer_the_rebase(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            loop = self._loop(root, "alpha")
            res = self._write(loop, "core/strategy/../../../escape.md")
            self.assertTrue(res.startswith("DENIED"), res)
            self.assertFalse((root.parent / "escape.md").exists())

    def test_default_project_resolution_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _workspace(d)
            loop = self._loop(root, "default")
            rel = "core/strategy/EXPECTATION_MAP.md"
            res = self._write(loop, rel)
            self.assertTrue(res.startswith("OK"), res)
            self.assertTrue(root.joinpath(*rel.split("/")).is_file())
            self.assertFalse((root / ".signalos" / "projects").exists())


if __name__ == "__main__":
    unittest.main()
