# test_agent_ipc.py
# Phase 3 Stream A — agent loop IPC handler tests.
#
# Drives signalos_ipc_server.handle({"command": "agent:run", ...}) with a
# deterministic AgentTestProvider (INV-6: no network, no litellm, no Tauri)
# injected via the module-level seam, captures stdout, and asserts:
#   - agent-event lines are emitted with kind=="agent-event" and the run_id
#   - a final ok SidecarResponse carries the run summary
#   - agent:verdict normalizes a verdict via gate_review
#   - agent:cancel / agent:resume return ok
#
# Injection seam (documented in signalos_ipc_server.py):
#   _AGENT_ADAPTER_FACTORY(model, provider=None) -> ProviderAdapter   (test double here)
#   _AGENT_ENFORCEMENT_FACTORY()  -> EnforcementProvider
# Tests set these on the module, run a command, then restore them in tearDown.

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_ipc_server as srv  # noqa: E402

from signalos_lib.harness import (  # noqa: E402
    AgentResponse,
    AgentTestProvider,
    TokenUsage,
    ToolCall,
)
from signalos_lib.product.enforcement_state import (  # noqa: E402
    StaticEnforcementProvider,
)
from signalos_lib.product.provider_adapter import (  # noqa: E402
    ProviderAdapter,
    ProviderCapabilities,
)


# ---------------------------------------------------------------------------
# helpers (mirror test_product_agent_loop._tool_resp / _end_resp / _adapter)
# ---------------------------------------------------------------------------


def _tool_resp(name: str, args: dict, call_id: str = "c1") -> AgentResponse:
    return AgentResponse(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        stop_reason="tool_use",
        usage=TokenUsage(1, 1),
    )


def _end_resp(text: str = "done") -> AgentResponse:
    return AgentResponse(
        content=text,
        tool_calls=None,
        stop_reason="end_turn",
        usage=TokenUsage(1, 1),
    )


def _adapter_factory(script):
    """Return a factory(model, provider) -> ProviderAdapter backed by AgentTestProvider."""
    def factory(model: str, provider: str | None = None) -> ProviderAdapter:
        provider = AgentTestProvider(script=list(script))
        caps = ProviderCapabilities(
            model=model,
            supports_tool_calls=True,
            supports_streaming=True,
            context_length=200_000,
        )
        return ProviderAdapter(model=model, provider=provider, capabilities=caps)
    return factory


def _agent_args(**payload) -> str:
    base = {"provider": "openai", "model": "gpt-test"}
    base.update(payload)
    return json.dumps(base)


def _parse_lines(captured: str) -> list[dict]:
    out = []
    for line in captured.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class _AgentIpcBase(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        # Deterministic enforcement: T3, no network.
        srv._AGENT_ENFORCEMENT_FACTORY = lambda: StaticEnforcementProvider(
            trust_tier="T3"
        )
        srv._AGENT_ADAPTER_FACTORY = None  # set per-test
        srv._AGENT_CANCEL_FLAGS.clear()
        srv._ACTIVE_DELIVERIES.clear()

    def tearDown(self) -> None:
        srv._AGENT_ADAPTER_FACTORY = None
        srv._AGENT_ENFORCEMENT_FACTORY = None
        srv._AGENT_CANCEL_FLAGS.clear()
        srv._ACTIVE_DELIVERIES.clear()
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    def _run(self, req: dict) -> tuple[dict, list[dict]]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            resp = srv.handle(req)
        return resp, _parse_lines(buf.getvalue())


# ---------------------------------------------------------------------------
# agent:run
# ---------------------------------------------------------------------------


class TestAgentRun(_AgentIpcBase):
    def test_run_emits_agent_events_and_ok(self):
        srv._AGENT_ADAPTER_FACTORY = _adapter_factory(
            [_tool_resp("search_files", {"pattern": "*.py"}), _end_resp("finished")]
        )
        resp, events = self._run(
            {
                "command": "agent:run",
                "id": "req-1",
                "args": [_agent_args(prompt="find files", run_id="run-A")],
            }
        )
        # Final response: ok, with the run summary.
        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(resp["id"], "req-1")
        summary = resp["data"]
        self.assertEqual(summary["run_id"], "run-A")
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["tool_calls_made"], 1)

        # agent-event lines: every one has kind=="agent-event" and run_id.
        agent_events = [e for e in events if e.get("kind") == "agent-event"]
        self.assertTrue(agent_events, msg=f"no agent-event lines in {events}")
        for ev in agent_events:
            self.assertEqual(ev["kind"], "agent-event")
            self.assertEqual(ev["run_id"], "run-A")
            self.assertIn("type", ev)
        types = {e["type"] for e in agent_events}
        # The tool_done + end_turn loop events must surface.
        self.assertIn("tool_done", types)
        self.assertIn("end_turn", types)

    def test_run_accepts_parsed_dict_args(self):
        # Transport may hand us the already-parsed object instead of a string.
        srv._AGENT_ADAPTER_FACTORY = _adapter_factory([_end_resp("ok")])
        resp, events = self._run(
            {
                "command": "agent:run",
                "id": "req-2",
                "args": {"prompt": "hello", "run_id": "run-B", "provider": "openai", "model": "gpt-test"},
            }
        )
        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(resp["data"]["run_id"], "run-B")
        self.assertTrue(any(e.get("run_id") == "run-B" for e in events))

    def test_run_passes_selected_provider_and_model_to_factory(self):
        seen: list[tuple[str, str | None]] = []

        def factory(model: str, provider: str | None = None) -> ProviderAdapter:
            seen.append((model, provider))
            test_provider = AgentTestProvider(script=[_end_resp("ok")])
            caps = ProviderCapabilities(
                model=model,
                supports_tool_calls=True,
                supports_streaming=True,
                context_length=200_000,
            )
            return ProviderAdapter(model=model, provider=test_provider, capabilities=caps)

        srv._AGENT_ADAPTER_FACTORY = factory
        resp, _ = self._run(
            {
                "command": "agent:run",
                "id": "req-route",
                "args": [_agent_args(prompt="route this", run_id="run-route", provider="openrouter", model="openai/gpt-4o")],
            }
        )
        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(seen, [("openai/gpt-4o", "openrouter")])

    def test_run_missing_prompt_is_error(self):
        srv._AGENT_ADAPTER_FACTORY = _adapter_factory([_end_resp()])
        resp, _ = self._run(
            {"command": "agent:run", "id": "req-3", "args": [_agent_args()]}
        )
        self.assertFalse(resp["ok"])
        self.assertIn("prompt", resp["error"])

    def test_run_missing_model_is_error_before_provider_init(self):
        called = False

        def factory(model: str, provider: str | None = None) -> ProviderAdapter:
            nonlocal called
            called = True
            return ProviderAdapter(
                model=model,
                provider=AgentTestProvider(script=[_end_resp("should not call")]),
                capabilities=ProviderCapabilities(model=model),
            )

        srv._AGENT_ADAPTER_FACTORY = factory
        resp, events = self._run(
            {
                "command": "agent:run",
                "id": "req-no-model",
                "args": [json.dumps({"prompt": "go", "provider": "openai", "run_id": "run-no-model"})],
            }
        )

        self.assertFalse(resp["ok"], msg=resp)
        self.assertFalse(called)
        self.assertIn("selected AI model", resp["error"])
        self.assertTrue(any(e.get("type") == "error" for e in events))


    def test_run_provider_failure_surfaces_error_event_and_non_ok(self):
        # INV-4: a provider that raises during chat surfaces an error agent-event
        # AND a non-ok response. AgentLoop catches the chat exception and emits
        # {"type":"error"} itself; our envelope wraps it.
        class _Boom:
            def chat(self, *a, **k):
                raise RuntimeError("provider exploded")

        def factory(model):
            caps = ProviderCapabilities(
                model=model, supports_tool_calls=True,
                supports_streaming=True, context_length=200_000,
            )
            return ProviderAdapter(model=model, provider=_Boom(), capabilities=caps)

        srv._AGENT_ADAPTER_FACTORY = factory
        resp, events = self._run(
            {
                "command": "agent:run",
                "id": "req-4",
                "args": [_agent_args(prompt="go", run_id="run-C")],
            }
        )
        self.assertFalse(resp["ok"], msg=resp)
        self.assertEqual(resp["data"]["status"], "error")
        error_events = [
            e for e in events
            if e.get("kind") == "agent-event" and e.get("type") == "error"
        ]
        self.assertTrue(error_events, msg=f"expected an error agent-event in {events}")
        self.assertEqual(error_events[0]["run_id"], "run-C")

    def test_run_cannot_write_product_files(self):
        srv._AGENT_ADAPTER_FACTORY = _adapter_factory(
            [
                _tool_resp(
                    "write_file",
                    {"path": "src/App.tsx", "content": "export default 1"},
                ),
                _end_resp("finished"),
            ]
        )
        resp, events = self._run(
            {
                "command": "agent:run",
                "id": "req-write",
                "args": [_agent_args(prompt="change product files", run_id="run-W")],
            }
        )
        self.assertTrue(resp["ok"], msg=resp)
        self.assertFalse((Path(os.getcwd()) / "src" / "App.tsx").exists())
        denied = [
            e for e in events
            if e.get("kind") == "agent-event" and e.get("type") == "tool_denied"
        ]
        self.assertTrue(denied, msg=f"expected write denial event in {events}")
        self.assertIn("governed delivery", denied[0]["reason"])


# ---------------------------------------------------------------------------
# agent:verdict
# ---------------------------------------------------------------------------


class TestAgentVerdict(_AgentIpcBase):
    def test_verdict_approve(self):
        resp, _ = self._run(
            {
                "command": "agent:verdict",
                "id": "v-1",
                "args": [json.dumps({"run_id": "run-A", "verdict": "looks good"})],
            }
        )
        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(resp["data"]["verdict"], "approve")
        # record_review_event wrote to the audit trail.
        audit = Path(os.getcwd()) / ".signalos" / "AUDIT_TRAIL.jsonl"
        self.assertTrue(audit.is_file())

    def test_verdict_request_changes(self):
        resp, _ = self._run(
            {
                "command": "agent:verdict",
                "id": "v-2",
                "args": [
                    json.dumps(
                        {
                            "run_id": "run-A",
                            "verdict": "change the header color",
                            "feedback": "change the header color",
                        }
                    )
                ],
            }
        )
        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(resp["data"]["verdict"], "request-changes")
        self.assertEqual(resp["data"]["handled"]["status"], "rework_dispatched")

    def test_verdict_reject(self):
        resp, _ = self._run(
            {
                "command": "agent:verdict",
                "id": "v-3",
                "args": [json.dumps({"run_id": "run-A", "verdict": "reject this"})],
            }
        )
        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(resp["data"]["verdict"], "reject")
        self.assertEqual(
            resp["data"]["handled"]["status"], "regenerate_dispatched"
        )

    def test_verdict_requires_run_id(self):
        resp, _ = self._run(
            {
                "command": "agent:verdict",
                "id": "v-4",
                "args": [json.dumps({"verdict": "approve"})],
            }
        )
        self.assertFalse(resp["ok"])
        self.assertIn("run_id", resp["error"])


class TestAgentVerdictReworkBudget(_AgentIpcBase):
    """P0 regression: the standalone agent:verdict path must persist the
    rework cycle across IPC calls (previously every call restarted at cycle 1,
    making the rework budget unreachable) and refuse once the shared gate
    rework budget is exhausted."""

    def _verdict(self, req_id: str, feedback: str) -> dict:
        resp, _ = self._run(
            {
                "command": "agent:verdict",
                "id": req_id,
                "args": [
                    json.dumps(
                        {
                            "run_id": "run-B",
                            "verdict": "request-changes",
                            "feedback": feedback,
                        }
                    )
                ],
            }
        )
        self.assertTrue(resp["ok"], msg=resp)
        return resp

    def test_repeated_request_changes_increment_cycle_and_hit_budget(self):
        os.environ["SIGNALOS_GATE_REWORK_BUDGET"] = "2"
        try:
            r1 = self._verdict("rb-1", "fix the header")
            self.assertEqual(r1["data"]["handled"]["status"], "rework_dispatched")
            self.assertEqual(r1["data"]["handled"]["cycle"], 1)

            r2 = self._verdict("rb-2", "fix the footer")
            self.assertEqual(r2["data"]["handled"]["status"], "rework_dispatched")
            self.assertEqual(r2["data"]["handled"]["cycle"], 2)  # persisted, not reset

            # Budget (2) exhausted -> standalone mirror of "max-rework".
            r3 = self._verdict("rb-3", "still broken")
            self.assertEqual(r3["data"]["handled"]["status"], "max_cycles_reached")
            self.assertIsNone(r3["data"]["handled"]["rework_packet"])

            # And it STAYS refused on further calls.
            r4 = self._verdict("rb-4", "again")
            self.assertEqual(r4["data"]["handled"]["status"], "max_cycles_reached")
        finally:
            os.environ.pop("SIGNALOS_GATE_REWORK_BUDGET", None)

    def test_repeated_rejects_increment_count_and_hit_bound(self):
        def reject(req_id: str) -> dict:
            resp, _ = self._run(
                {
                    "command": "agent:verdict",
                    "id": req_id,
                    "args": [
                        json.dumps(
                            {
                                "run_id": "run-C",
                                "verdict": "reject",
                                "feedback": "wrong direction",
                            }
                        )
                    ],
                }
            )
            self.assertTrue(resp["ok"], msg=resp)
            return resp

        r1 = reject("rj-1")
        self.assertEqual(r1["data"]["handled"]["status"], "regenerate_dispatched")
        self.assertEqual(r1["data"]["handled"]["rejection_count"], 1)
        r2 = reject("rj-2")
        self.assertEqual(r2["data"]["handled"]["rejection_count"], 2)
        # max_rejections default is 2 (same bound as the orchestrator).
        r3 = reject("rj-3")
        self.assertEqual(r3["data"]["handled"]["status"], "max_rejections_reached")

    def test_rework_and_reject_cycles_do_not_cross_count(self):
        os.environ["SIGNALOS_GATE_REWORK_BUDGET"] = "2"
        try:
            # One rejection writes cycle-1/regenerate-packet.json; it must NOT
            # advance the rework counter for the same gate.
            resp, _ = self._run(
                {
                    "command": "agent:verdict",
                    "id": "x-1",
                    "args": [
                        json.dumps(
                            {
                                "run_id": "run-B",
                                "verdict": "reject",
                                "feedback": "start over",
                            }
                        )
                    ],
                }
            )
            self.assertTrue(resp["ok"], msg=resp)
            r1 = self._verdict("x-2", "fix the nav")
            self.assertEqual(r1["data"]["handled"]["cycle"], 1)
        finally:
            os.environ.pop("SIGNALOS_GATE_REWORK_BUDGET", None)


# ---------------------------------------------------------------------------
# agent:cancel + agent:resume
# ---------------------------------------------------------------------------


class TestAgentCancelResume(_AgentIpcBase):
    def test_cancel_sets_flag_and_returns_ok(self):
        # Cancellation is bound to a real active/persisted run; unknown ids
        # are refused so callers cannot pre-seed control markers for a future
        # run.  A plain AgentLoop checkpoint is sufficient for this contract.
        run_dir = (
            Path(os.getcwd()) / ".signalos" / "agent-runs" / "run-X"
        )
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(
            json.dumps({
                "run_id": "run-X",
                "project_id": "default",
                "status": "running",
                "tool_calls_made": 0,
            }) + "\n",
            encoding="utf-8",
        )
        (run_dir / "conversation.jsonl").write_text(
            json.dumps({"role": "system", "content": "pending"}) + "\n",
            encoding="utf-8",
        )
        resp, _ = self._run(
            {
                "command": "agent:cancel",
                "id": "c-1",
                "args": [json.dumps({"run_id": "run-X"})],
            }
        )
        self.assertTrue(resp["ok"], msg=resp)
        self.assertTrue(resp["data"]["cancel_requested"])
        self.assertTrue(srv._AGENT_CANCEL_FLAGS.get("run-X"))
        marker = (
            Path(os.getcwd())
            / ".signalos"
            / "agent-runs"
            / "run-X"
            / "cancel-requested.json"
        )
        self.assertTrue(marker.is_file())

    def test_resume_missing_state_is_error(self):
        resp, _ = self._run(
            {
                "command": "agent:resume",
                "id": "r-1",
                "args": [json.dumps({"run_id": "nope"})],
            }
        )
        self.assertFalse(resp["ok"])
        self.assertIn("no persisted state", resp["error"])

    def test_resume_reads_persisted_state(self):
        # A completed run persists state.json; resume should return its
        # terminal state without appending a new prompt.
        srv._AGENT_ADAPTER_FACTORY = _adapter_factory([_end_resp("done")])
        run_resp, _ = self._run(
            {
                "command": "agent:run",
                "id": "rr-1",
                "args": [_agent_args(prompt="hi", run_id="run-R")],
            }
        )
        self.assertTrue(run_resp["ok"], msg=run_resp)
        state_file = (
            Path(os.getcwd()) / ".signalos" / "agent-runs" / "run-R" / "state.json"
        )
        self.assertTrue(state_file.is_file())

        resp, _ = self._run(
            {
                "command": "agent:resume",
                "id": "r-2",
                "args": [_agent_args(run_id="run-R")],
            }
        )
        self.assertTrue(resp["ok"], msg=resp)
        self.assertTrue(resp["data"]["resumed"])
        self.assertEqual(resp["data"]["run_id"], "run-R")
        self.assertEqual(resp["data"]["status"], "completed")

    def _seed_running_run(self, run_id: str):
        run_dir = Path(os.getcwd()) / ".signalos" / "agent-runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "state.json").write_text(
            json.dumps({
                "run_id": run_id,
                "status": "running",
                "tool_calls_made": 1,
                "trust_tier": "T3",
                "updated_at": "2026-06-02T00:00:00Z",
            }),
            encoding="utf-8",
        )
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "continue"},
            {"role": "assistant", "content": "prior"},
        ]
        (run_dir / "conversation.jsonl").write_text(
            "\n".join(json.dumps(m) for m in messages) + "\n",
            encoding="utf-8",
        )

    def test_resume_continues_running_state(self):
        self._seed_running_run("run-live-resume")
        srv._AGENT_ADAPTER_FACTORY = _adapter_factory([_end_resp("resumed")])

        resp, events = self._run(
            {
                "command": "agent:resume",
                "id": "r-3",
                "args": [_agent_args(run_id="run-live-resume")],
            }
        )

        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(resp["data"]["status"], "completed")
        self.assertEqual(resp["data"]["tool_calls_made"], 1)
        self.assertTrue(any(e.get("type") == "text" and e.get("text") == "resumed" for e in events))
        self.assertTrue(any(e.get("type") == "end_turn" for e in events))

    def test_resume_honors_persisted_cancel_marker(self):
        self._seed_running_run("run-cancel-resume")
        srv._AGENT_ADAPTER_FACTORY = _adapter_factory([_end_resp("should not call")])
        cancel_resp, _ = self._run(
            {
                "command": "agent:cancel",
                "id": "c-2",
                "args": [_agent_args(run_id="run-cancel-resume")],
            }
        )
        self.assertTrue(cancel_resp["ok"], msg=cancel_resp)
        srv._AGENT_CANCEL_FLAGS.clear()  # simulate sidecar memory loss

        resp, events = self._run(
            {
                "command": "agent:resume",
                "id": "r-4",
                "args": [_agent_args(run_id="run-cancel-resume")],
            }
        )

        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(resp["data"]["status"], "cancelled")
        self.assertTrue(any(e.get("type") == "cancelled" for e in events))


class TestBuildAgentEnforcement(unittest.TestCase):
    """Edit 1.5 — production returns the real FileEnforcementProvider; the test
    seam still wins when set."""

    def setUp(self) -> None:
        self._prev = srv._AGENT_ENFORCEMENT_FACTORY
        srv._AGENT_ENFORCEMENT_FACTORY = None

    def tearDown(self) -> None:
        srv._AGENT_ENFORCEMENT_FACTORY = self._prev

    def test_build_agent_enforcement_returns_file_provider(self):
        from signalos_lib.product.enforcement_state import FileEnforcementProvider

        srv._AGENT_ENFORCEMENT_FACTORY = None
        provider = srv._build_agent_enforcement()
        self.assertIsInstance(provider, FileEnforcementProvider)

    def test_seam_still_wins(self):
        sentinel = StaticEnforcementProvider(trust_tier="T3")
        srv._AGENT_ENFORCEMENT_FACTORY = lambda: sentinel
        self.assertIs(srv._build_agent_enforcement(), sentinel)


if __name__ == "__main__":
    unittest.main()
