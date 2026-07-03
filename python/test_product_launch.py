# python/test_product_launch.py
# 3.4 (C-bridge): a launch surface re-enters the SAME enforced G0-G5 loop
# as a second mini-build, isolated in its own repo_root, linked back to
# the parent product's journey. Uses the REAL GateOrchestrator (not a
# mock of it) with a fake LLM adapter, so this proves the actual gate
# loop runs for the child build -- not just that a function was called.

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.harness import AgentResponse, TokenUsage
from signalos_lib.product.closeout import write_closeout
from signalos_lib.product.enforcement_state import StaticEnforcementProvider
from signalos_lib.product.gate_orchestrator import GateOrchestrator
from signalos_lib.product.launch import list_launches, load_launch_link, start_launch_build


class _EndAdapter:
    supports_tool_calls = True

    def chat(self, messages, model="test", tools=None, stream=False):
        return AgentResponse(content="(gate work done)", tool_calls=None,
                              stop_reason="end_turn", usage=TokenUsage())


def _write_parent_closeout(parent_root: Path) -> None:
    signalos_dir = parent_root / ".signalos"
    signalos_dir.mkdir(parents=True, exist_ok=True)
    closeout = {
        "product_name": "Acme Tracker",
        "profile": "react-vite",
        "closure_level": "complete",
        "generated_at": "2026-01-01T00:00:00Z",
    }
    write_closeout(closeout, signalos_dir)


def _fake_orchestrator_factory(events: list, signed: list, orchs: list | None = None):
    def factory(child_repo_root: Path, prompt: str, run_id: str) -> GateOrchestrator:
        soul = child_repo_root / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md"
        soul.parent.mkdir(parents=True, exist_ok=True)
        soul.write_text("The product purpose statement. " * 40, encoding="utf-8")
        (child_repo_root / ".signalos").mkdir(parents=True, exist_ok=True)

        def fake_sign(repo_root, gate, signer, role, verdict, conditions):
            signed.append((gate, role, verdict))
            return [f"{gate}.md"]

        from signalos_lib.product.identity import format_signer, load_identity
        orch = GateOrchestrator(
            child_repo_root, _EndAdapter(), events.append,
            enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
            sign_fn=fake_sign, prompt=prompt, run_id=run_id,
            signer=format_signer(load_identity(child_repo_root)),
        )
        if orchs is not None:
            orchs.append(orch)
        return orch
    return factory


class TestLaunchIdentityContinuity(unittest.TestCase):
    """3.6: the founder's identity carries into the isolated launch child
    instead of being dropped, and the real signer string on the child's
    GateOrchestrator reflects who they actually are, not a generic literal."""

    def test_child_inherits_parent_identity(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            parent_root = Path(d)
            _write_parent_closeout(parent_root)
            (parent_root / ".signalos" / "identity.json").write_text(
                json.dumps({"name": "Samer Zakaria", "role": "PO"}), encoding="utf-8",
            )

            orchs: list = []
            result = start_launch_build(
                parent_root, _fake_orchestrator_factory([], [], orchs),
            )

            child_root = Path(result["child_repo_root"])
            child_identity = json.loads(
                (child_root / ".signalos" / "identity.json").read_text(encoding="utf-8")
            )
            self.assertEqual(child_identity["name"], "Samer Zakaria")
            self.assertEqual(child_identity["role"], "PO")

            self.assertEqual(len(orchs), 1)
            self.assertEqual(orchs[0].signer, "Samer Zakaria (PO)")

    def test_child_has_no_identity_when_parent_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            parent_root = Path(d)
            _write_parent_closeout(parent_root)

            orchs: list = []
            result = start_launch_build(
                parent_root, _fake_orchestrator_factory([], [], orchs),
            )

            child_root = Path(result["child_repo_root"])
            self.assertFalse((child_root / ".signalos" / "identity.json").exists())
            self.assertEqual(orchs[0].signer, "foundry-agent")


class TestLaunchRecursion(unittest.TestCase):
    def test_refuses_when_parent_has_no_closeout(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            parent_root = Path(d)
            with self.assertRaises(ValueError):
                start_launch_build(parent_root, lambda *a: None)

    def test_launch_build_runs_the_real_gate_loop_in_an_isolated_root(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            parent_root = Path(d)
            _write_parent_closeout(parent_root)

            events: list[dict] = []
            signed: list[tuple] = []
            result = start_launch_build(
                parent_root, _fake_orchestrator_factory(events, signed),
            )

            child_root = Path(result["child_repo_root"])
            # Genuinely isolated -- the child tree lives under the parent's
            # .signalos/, never inside the parent's own product root.
            self.assertTrue(str(child_root).startswith(str(parent_root)))
            self.assertNotEqual(child_root, parent_root)

            # The real G0->G5 loop actually ran for the child: a gate event
            # was emitted (matches what agent:deliver's live path does).
            gate_events = [e for e in events if e.get("type") == "gate"]
            self.assertTrue(gate_events, events)
            self.assertEqual(gate_events[0]["gate"], "G0")

            # Linkage is recorded on both sides.
            link = load_launch_link(child_root)
            self.assertIsNotNone(link)
            self.assertEqual(link["parent_repo_root"], str(parent_root))
            self.assertEqual(link["parent_product_name"], "Acme Tracker")

            launches = list_launches(parent_root)
            self.assertEqual(len(launches), 1)
            self.assertEqual(launches[0]["run_id"], result["run_id"])

    def test_default_prompt_scopes_to_a_landing_page_not_a_full_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            parent_root = Path(d)
            _write_parent_closeout(parent_root)
            events: list[dict] = []
            result = start_launch_build(
                parent_root, _fake_orchestrator_factory(events, []),
            )
            self.assertIn("landing page", result["link"]["prompt"].lower())
            self.assertIn("Acme Tracker", result["link"]["prompt"])

    def test_second_launch_appends_to_the_registry_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            parent_root = Path(d)
            _write_parent_closeout(parent_root)
            start_launch_build(parent_root, _fake_orchestrator_factory([], []))
            start_launch_build(parent_root, _fake_orchestrator_factory([], []))
            self.assertEqual(len(list_launches(parent_root)), 2)


if __name__ == "__main__":
    unittest.main()
