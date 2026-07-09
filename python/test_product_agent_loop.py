# test_product_agent_loop.py
# v4 Phase 2 — Agent loop, provider adapter, and governance tests.
#
# Everything here uses the deterministic AgentTestProvider (INV-6): no network,
# no litellm, no Tauri. Covers T05/T07/T08-style capability checks, tool
# read/write/edit/command/search, governance denials (forbidden paths/actions,
# trust-tier), audit ledger, idempotency, text-only degradation, and secret
# redaction.

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.harness import (
    AgentResponse,
    AgentTestProvider,
    TokenUsage,
    ToolCall,
)
from signalos_lib.product.agent_loop import (
    AgentLoop,
    build_tool_definitions,
    redact_secrets,
)
from signalos_lib.product.enforcement_state import (
    DEFAULT_TRUST_TIER_PATHS,
    RUNTIME_RULES,
    StaticEnforcementProvider,
    load_trust_tier_paths,
    seed_trust_tier_paths,
)
from signalos_lib.product.provider_adapter import (
    ProviderAdapter,
    ProviderAuthError,
    ProviderCapabilities,
    classify_error_scenario,
    detect_capabilities,
)


# ---------------------------------------------------------------------------
# helpers
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


def _adapter(provider, supports_tool_calls=True, model="claude-sonnet-4-5"):
    caps = ProviderCapabilities(
        model=model,
        supports_tool_calls=supports_tool_calls,
        supports_streaming=True,
        context_length=200_000,
    )
    return ProviderAdapter(model=model, provider=provider, capabilities=caps)


class _RaisingProvider:
    """A provider whose chat() always raises -- for 1.10 incident-card tests."""
    def __init__(self, exc: Exception):
        self._exc = exc

    def chat(self, messages, model, tools=None, stream=False):
        raise self._exc


def _loop(tmp: Path, provider, enforcement=None, **kw) -> AgentLoop:
    return AgentLoop(
        adapter=_adapter(provider),
        repo_root=tmp,
        enforcement_provider=enforcement or StaticEnforcementProvider(trust_tier="T3"),
        run_id="test-run",
        **kw,
    )


# ---------------------------------------------------------------------------
# 1.10: provider failures surface as plain-words incident cards, not bare errors
# ---------------------------------------------------------------------------


class TestErrorScenarioClassification(unittest.TestCase):
    def test_provider_auth_error_is_credential_revoked(self):
        self.assertEqual(
            classify_error_scenario(ProviderAuthError("nope")), "credential-revoked")

    def test_401_message_is_credential_revoked(self):
        self.assertEqual(
            classify_error_scenario(RuntimeError("401 Unauthorized")), "credential-revoked")

    def test_rate_limit_message_is_integration_outage(self):
        self.assertEqual(
            classify_error_scenario(RuntimeError("Rate limit exceeded")), "integration-outage")

    def test_quota_message_is_integration_outage(self):
        self.assertEqual(
            classify_error_scenario(RuntimeError("insufficient_quota")), "integration-outage")

    def test_connection_error_is_integration_outage(self):
        self.assertEqual(
            classify_error_scenario(ConnectionError("connection refused")), "integration-outage")

    def test_unrelated_error_is_unclassified(self):
        self.assertIsNone(classify_error_scenario(ValueError("bad input")))


class TestProviderErrorIncidentCard(unittest.TestCase):
    """Live wiring proof: a real provider failure during a gate run emits a
    plain-words incident card with recovery options, not just a bare error."""

    def test_rate_limit_failure_emits_integration_outage_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            events: list[dict] = []
            provider = _RaisingProvider(RuntimeError("Rate limit exceeded, quota used"))
            loop = _loop(Path(tmp), provider, emit=events.append)
            result = loop.run("system", "do the thing")
            self.assertEqual(result.status, "error")
            incidents = [e for e in events if e.get("type") == "incident"]
            self.assertTrue(incidents, "no incident card emitted")
            self.assertEqual(incidents[0]["scenario"], "integration-outage")
            self.assertTrue(incidents[0]["recovery_options"])

    def test_auth_failure_emits_credential_revoked_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            events: list[dict] = []
            provider = _RaisingProvider(ProviderAuthError("401 invalid api key"))
            loop = _loop(Path(tmp), provider, emit=events.append)
            loop.run("system", "do the thing")
            incidents = [e for e in events if e.get("type") == "incident"]
            self.assertEqual(incidents[0]["scenario"], "credential-revoked")

    def test_unclassified_failure_still_emits_a_safe_fallback_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            events: list[dict] = []
            provider = _RaisingProvider(ValueError("something weird"))
            loop = _loop(Path(tmp), provider, emit=events.append)
            loop.run("system", "do the thing")
            incidents = [e for e in events if e.get("type") == "incident"]
            self.assertTrue(incidents, "unclassified errors must still get a card, never a bare stall")
            self.assertTrue(incidents[0]["recovery_options"])


# ---------------------------------------------------------------------------
# Capability detection (T08)
# ---------------------------------------------------------------------------


class TestCapabilityDetection(unittest.TestCase):
    def test_offline_detection_no_litellm(self):
        caps = detect_capabilities("gpt-4o", litellm_module=None)
        self.assertIsInstance(caps, ProviderCapabilities)
        self.assertTrue(caps.supports_tool_calls)
        self.assertGreater(caps.context_length, 0)

    def test_instruct_model_no_tools(self):
        caps = detect_capabilities("some-instruct-model", litellm_module=None)
        self.assertFalse(caps.supports_tool_calls)

    def test_known_context_length(self):
        caps = detect_capabilities("claude-sonnet-4-5", litellm_module=None)
        self.assertEqual(caps.context_length, 200_000)

    def test_adapter_drops_tools_when_unsupported(self):
        provider = AgentTestProvider(script=[_end_resp("text only")])
        adapter = _adapter(provider, supports_tool_calls=False)
        self.assertFalse(adapter.supports_tool_calls)
        adapter.chat(messages=[{"role": "user", "content": "hi"}], tools=[{"x": 1}])
        # The provider must have received tools=None.
        self.assertIsNone(provider.calls[0]["tools"])


# ---------------------------------------------------------------------------
# 12 runtime rules + trust-tier config
# ---------------------------------------------------------------------------


class TestEnforcementConfig(unittest.TestCase):
    def test_twelve_rules(self):
        self.assertEqual(len(RUNTIME_RULES), 12)

    def test_seed_and_load_trust_tier_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = seed_trust_tier_paths(root)
            self.assertTrue(path.is_file())
            loaded = load_trust_tier_paths(root)
            self.assertIn("T2", loaded)
            self.assertIn("forbidden_always", loaded)

    def test_default_has_forbidden_always(self):
        self.assertIn("forbidden_always", DEFAULT_TRUST_TIER_PATHS)
        self.assertIn(".env", DEFAULT_TRUST_TIER_PATHS["forbidden_always"]["write"])

    def test_corrupt_config_raises(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / ".signalos"
            p.mkdir()
            (p / "trust-tier-paths.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_trust_tier_paths(root)


# ---------------------------------------------------------------------------
# Tool execution (T09-T13)
# ---------------------------------------------------------------------------


class TestToolExecution(unittest.TestCase):
    def test_read_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "hello.txt").write_text("world", encoding="utf-8")
            provider = AgentTestProvider(
                script=[_tool_resp("read_file", {"path": "hello.txt"}), _end_resp()]
            )
            loop = _loop(root, provider)
            result = loop.run("sys", "read hello.txt")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.tool_calls_made, 1)
            # tool result should appear in the messages
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("world", tool_msgs[0]["content"])

    def test_write_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "out.txt", "content": "hi"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider)
            result = loop.run("sys", "write out.txt")
            self.assertEqual(result.status, "completed")
            self.assertEqual((root / "out.txt").read_text(encoding="utf-8"), "hi")

    def test_edit_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.txt").write_text("title: old", encoding="utf-8")
            provider = AgentTestProvider(
                script=[
                    _tool_resp(
                        "edit_file",
                        {"path": "a.txt", "old_string": "old", "new_string": "new"},
                    ),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider)
            result = loop.run("sys", "edit a.txt")
            self.assertEqual(result.status, "completed")
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "title: new")

    def test_search_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "x.tsx").write_text("x", encoding="utf-8")
            (root / "y.tsx").write_text("y", encoding="utf-8")
            provider = AgentTestProvider(
                script=[_tool_resp("search_files", {"pattern": "*.tsx"}), _end_resp()]
            )
            loop = _loop(root, provider)
            result = loop.run("sys", "find tsx")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("x.tsx", tool_msgs[0]["content"])
            self.assertIn("y.tsx", tool_msgs[0]["content"])

    def test_run_command(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("run_command", {"command": "echo hello"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider)
            result = loop.run("sys", "run echo")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("hello", tool_msgs[0]["content"])
            self.assertIn("exit_code: 0", tool_msgs[0]["content"])

    def test_run_command_timeout_is_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("run_command", {"command": "long-running-command"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider)

            with patch(
                "signalos_lib.product.agent_loop.subprocess.run",
                side_effect=subprocess.TimeoutExpired("long-running-command", 120),
            ):
                result = loop.run("sys", "run a command that hangs")

            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("timed out", tool_msgs[0]["content"])
            self.assertIn("killed", tool_msgs[0]["content"])


# ---------------------------------------------------------------------------
# Governance denials (T16-T19, T25)
# ---------------------------------------------------------------------------


class TestGovernance(unittest.TestCase):
    def _run_single_tool(self, root, name, args, tier="T3", rule_modes=None):
        seed_trust_tier_paths(root)
        enf = StaticEnforcementProvider(trust_tier=tier, rule_modes=rule_modes)
        provider = AgentTestProvider(script=[_tool_resp(name, args), _end_resp()])
        loop = _loop(root, provider, enforcement=enf)
        result = loop.run("sys", "do it")
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        return result, (tool_msgs[0]["content"] if tool_msgs else "")

    def test_write_env_denied(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, content = self._run_single_tool(
                root, "write_file", {"path": ".env", "content": "SECRET=1"}
            )
            self.assertIn("DENIED", content)
            self.assertFalse((root / ".env").exists())

    def test_denial_emits_user_visible_event(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            events: list[dict] = []
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": ".env", "content": "SECRET=1"}),
                    _end_resp(),
                ]
            )
            loop = AgentLoop(
                adapter=_adapter(provider),
                repo_root=root,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="denial-run",
                emit=events.append,
            )

            loop.run("sys", "write .env")

            denial_events = [e for e in events if e.get("type") == "tool_denied"]
            self.assertEqual(len(denial_events), 1)
            self.assertEqual(denial_events[0]["tool"], "write_file")
            self.assertIn(".env", denial_events[0]["reason"])

    def test_write_signalos_denied(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, content = self._run_single_tool(
                root,
                "write_file",
                {"path": ".signalos/AUDIT_TRAIL.jsonl", "content": "x"},
            )
            self.assertIn("DENIED", content)

    def test_rm_rf_denied(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, content = self._run_single_tool(
                root, "run_command", {"command": "rm -rf /"}
            )
            self.assertIn("DENIED", content)

    def test_force_push_denied(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, content = self._run_single_tool(
                root, "run_command", {"command": "git push --force origin main"}
            )
            self.assertIn("DENIED", content)

    def test_trust_tier_write_block_T2(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # T2 only allows src/**, public/**, tests/**, package.json, tsconfig.json
            _, content = self._run_single_tool(
                root,
                "write_file",
                {"path": "secret/keys.txt", "content": "x"},
                tier="T2",
            )
            self.assertIn("DENIED", content)

    def test_trust_tier_write_allow_T2_src(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, content = self._run_single_tool(
                root,
                "write_file",
                {"path": "src/App.tsx", "content": "export default 1"},
                tier="T2",
            )
            self.assertIn("OK", content)
            self.assertTrue((root / "src" / "App.tsx").exists())

    def test_write_content_secret_scan_blocks_embedded_key(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            secret = "sk-ant-" + ("A" * 40)
            _, content = self._run_single_tool(
                root,
                "write_file",
                {
                    "path": "src/config.ts",
                    "content": f"export const apiKey = '{secret}';\n",
                },
            )
            self.assertIn("DENIED", content)
            self.assertIn("secret-block", content)
            self.assertFalse((root / "src" / "config.ts").exists())

    def test_edit_content_secret_scan_blocks_embedded_key(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "src").mkdir()
            target = root / "src" / "config.ts"
            target.write_text("export const value = 'safe';\n", encoding="utf-8")
            secret = "sk-ant-" + ("B" * 40)
            _, content = self._run_single_tool(
                root,
                "edit_file",
                {
                    "path": "src/config.ts",
                    "old_string": "safe",
                    "new_string": secret,
                },
            )
            self.assertIn("DENIED", content)
            self.assertIn("secret-block", content)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "export const value = 'safe';\n",
            )

    def test_conversation_context_denies_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "src/App.tsx", "content": "x"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider, execution_context="conversation")
            result = loop.run("sys", "write source")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("DENIED", tool_msgs[0]["content"])
            self.assertIn("governed delivery", tool_msgs[0]["content"])
            self.assertFalse((root / "src" / "App.tsx").exists())

    def test_conversation_context_denies_commands(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("run_command", {"command": "npm test"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider, execution_context="conversation")
            result = loop.run("sys", "run command")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("DENIED", tool_msgs[0]["content"])
            self.assertIn("governed delivery", tool_msgs[0]["content"])

    def _ledger_rules(self, root: Path) -> list:
        ledger = root / ".signalos" / "agent-runs" / "test-run" / "tool-calls.jsonl"
        return [
            json.loads(line).get("rule")
            for line in ledger.read_text().splitlines()
        ]

    def test_plan_gating_blocks_impl_write_without_g2(self):
        # #16 Edit 2.1: inside a governed delivery (G4), an implementation write
        # is blocked until the plan gate (G2) is signed. A missing G2 is cited
        # under plan-gating specifically (checked before the broader gate set).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "src/App.tsx", "content": "x"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider, active_gate="G4", signed_gates=[0, 1])
            result = loop.run("sys", "write source")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("DENIED", tool_msgs[0]["content"])
            self.assertIn("plan-gating", self._ledger_rules(root))
            self.assertFalse((root / "src" / "App.tsx").exists())

    def test_plan_gating_allows_after_g2_signed(self):
        # G2 (and the rest of the G4 required set) signed + test written first
        # → the implementation write is allowed; plan-gating does not block.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = AgentTestProvider(
                script=[
                    _tool_resp(
                        "write_file",
                        {"path": "src/App.test.tsx", "content": "test('x',()=>{})"},
                        "c1",
                    ),
                    _tool_resp(
                        "write_file",
                        {"path": "src/App.tsx", "content": "export default 1"},
                        "c2",
                    ),
                    _end_resp(),
                ]
            )
            loop = _loop(
                root, provider, active_gate="G4", signed_gates=[0, 1, 2, 3]
            )
            result = loop.run("sys", "write test then source")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("OK", tool_msgs[1]["content"])
            self.assertNotIn("plan-gating", self._ledger_rules(root))
            self.assertTrue((root / "src" / "App.tsx").is_file())

    def test_pre_build_gate_denies_implementation_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "src/App.tsx", "content": "x"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider, active_gate="G3")
            result = loop.run("sys", "write source")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("DENIED", tool_msgs[0]["content"])
            self.assertIn("not allowed during G3", tool_msgs[0]["content"])

    def test_g4_denies_implementation_when_prior_gates_missing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "src/App.tsx", "content": "x"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider, active_gate="G4", signed_gates=[0, 1, 2])
            result = loop.run("sys", "write source")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("DENIED", tool_msgs[0]["content"])
            self.assertIn("G3", tool_msgs[0]["content"])

    def test_g4_requires_test_first_for_implementation_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "src/App.tsx", "content": "x"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider, active_gate="G4", signed_gates=[0, 1, 2, 3])
            result = loop.run("sys", "write source")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("DENIED", tool_msgs[0]["content"])
            self.assertIn("test-first", tool_msgs[0]["content"])

    def test_g4_allows_implementation_after_test_write(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "src/App.test.tsx", "content": "test('x',()=>{})"}, "c1"),
                    _tool_resp("write_file", {"path": "src/App.tsx", "content": "export default 1"}, "c2"),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider, active_gate="G4", signed_gates=[0, 1, 2, 3])
            result = loop.run("sys", "write test then source")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("OK", tool_msgs[0]["content"])
            self.assertIn("OK", tool_msgs[1]["content"])
            self.assertTrue((root / "src" / "App.test.tsx").is_file())
            self.assertTrue((root / "src" / "App.tsx").is_file())

    def test_g4_plan_authored_test_satisfies_test_first(self):
        """Regression: the plan gate authors acceptance tests under
        core/execution/tests/**. A module referenced by such a test must be
        implementable WITHOUT writing another (duplicate) test first -- the
        old check ignored plan tests, denied the write, and forced the exact
        parallel-test drift the build forbids."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            skel = root / "core" / "execution" / "tests" / "skeletons" / "wave-1"
            skel.mkdir(parents=True, exist_ok=True)
            (skel / "T1.1_expense_store.test.ts").write_text(
                "import { useExpenseStore } from '../../src/store/expenseStore';\n"
                "test('store works', () => {});\n",
                encoding="utf-8")
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "src/store/expenseStore.ts",
                                              "content": "export const useExpenseStore = 1;"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider, active_gate="G4", signed_gates=[0, 1, 2, 3])
            result = loop.run("sys", "implement the store the plan test specifies")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("OK", tool_msgs[0]["content"])
            self.assertTrue((root / "src" / "store" / "expenseStore.ts").is_file())

    def test_loop_attributes_provider_turn_errors(self):
        """Regression: provider turns with finish_reason='error' are normalized
        away by litellm (logged as a warning) -- transport noise silently eats
        model attempts. The loop now attributes the count per run so a flaky
        provider can't read as model weakness in comparisons."""
        import logging

        class _NoisyProvider(AgentTestProvider):
            def chat(self, *a, **k):
                logging.getLogger("LiteLLM").warning(
                    "Unmapped finish_reason 'error', defaulting to 'stop'")
                return super().chat(*a, **k)

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = _NoisyProvider(script=[_end_resp()])
            loop = _loop(root, provider, active_gate="G4", signed_gates=[0, 1, 2, 3])
            result = loop.run("sys", "one noisy turn")
            self.assertEqual(result.provider_turn_errors, 1)
            self.assertEqual(result.as_dict()["provider_turn_errors"], 1)

    def test_loop_accumulates_token_usage_across_turns(self):
        """Regression: the loop previously DISCARDED per-turn TokenUsage; it now
        sums usage across every provider turn onto LoopResult (cost tracking /
        pricing-model input / 360 comparison)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            r1 = _tool_resp("run_command", {"command": "git status"})
            r1.usage = TokenUsage(input_tokens=1000, output_tokens=50)
            r2 = _end_resp()
            r2.usage = TokenUsage(input_tokens=1200, output_tokens=80)
            provider = AgentTestProvider(script=[r1, r2])
            loop = _loop(root, provider, active_gate="G4", signed_gates=[0, 1, 2, 3])
            result = loop.run("sys", "do a thing")
            self.assertEqual(result.tokens_in, 2200)
            self.assertEqual(result.tokens_out, 130)
            self.assertEqual(result.as_dict()["tokens_in"], 2200)

    def test_command_output_is_capped_head_and_tail(self):
        """Regression: uncapped test-runner output (50-100k chars) repeated in
        one conversation blew the provider context ceiling mid-build. Output is
        now head+tail truncated with an explicit marker."""
        from signalos_lib.product.agent_loop import (
            COMMAND_OUTPUT_CAP, _cap_command_output)
        big = "A" * 5_000 + "MIDDLE" + "Z" * 60_000
        capped = _cap_command_output(big)
        self.assertLess(len(capped), COMMAND_OUTPUT_CAP + 300)
        self.assertTrue(capped.startswith("A" * 100))   # head kept
        self.assertTrue(capped.endswith("Z" * 100))     # tail kept
        self.assertIn("chars truncated", capped)        # explicit marker
        self.assertEqual(_cap_command_output("short"), "short")  # small passthrough

    def test_t2_execute_allowlist_covers_verification_runners(self):
        """Regression: the per-task green gate tells the implementer to run its
        plan test (e.g. `npx vitest run <file>`); the T2 allowlist must permit
        the verification runners or the agent works blind (observed: 77
        trust-tier denials in one G4 walk)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("run_command", {"command": "npx vitest run core/execution/tests/skeletons/wave-1/T1.1_expense_store.test.ts"}, "c1"),
                    _tool_resp("run_command", {"command": "npm run test"}, "c2"),
                    _tool_resp("run_command", {"command": "npx tsc --noEmit"}, "c3"),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider, active_gate="G4", signed_gates=[0, 1, 2, 3])
            result = loop.run("sys", "verify the build")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            for i, msg in enumerate(tool_msgs[:3]):
                self.assertNotIn("DENIED", msg["content"],
                                 f"verification command {i} was denied: {msg['content'][:120]}")


# ---------------------------------------------------------------------------
# Audit ledger (T23)
# ---------------------------------------------------------------------------


class TestAuditLedger(unittest.TestCase):
    def test_denial_logged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": ".env", "content": "x"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider)
            loop.run("sys", "write env")
            ledger = (
                root / ".signalos" / "agent-runs" / "test-run" / "tool-calls.jsonl"
            )
            self.assertTrue(ledger.is_file())
            entries = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["status"], "denied")
            self.assertEqual(entries[0]["tool"], "write_file")

    def test_completed_logged_with_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "f.txt", "content": "data"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider)
            loop.run("sys", "write f")
            ledger = (
                root / ".signalos" / "agent-runs" / "test-run" / "tool-calls.jsonl"
            )
            entries = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual(entries[0]["status"], "completed")
            self.assertIsNotNone(entries[0]["content_sha256"])


# ---------------------------------------------------------------------------
# audit-append (#16 Edit 2.6) — a failed completed-audit aborts the tool
# ---------------------------------------------------------------------------


class TestAuditAppendEnforcement(unittest.TestCase):
    def test_audit_append_failure_aborts_tool(self):
        # When the *completed* audit append raises and audit-append is enabled,
        # the write must NOT be reported as OK — it becomes a hard ERROR so no
        # un-audited write is ever surfaced as success.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "f.txt", "content": "data"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider)

            real_audit = loop._audit

            def flaky_audit(tc, status, *a, **kw):
                if status == "completed":
                    raise OSError("simulated audit ledger write failure")
                return real_audit(tc, status, *a, **kw)

            loop._audit = flaky_audit
            result = loop.run("sys", "write f")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("ERROR", tool_msgs[0]["content"])
            self.assertIn("audit-append", tool_msgs[0]["content"])

    def test_audit_append_success_path_unchanged(self):
        # Regression: a normal write still returns OK and appends a completed row.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "f.txt", "content": "data"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider)
            result = loop.run("sys", "write f")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("OK", tool_msgs[0]["content"])
            ledger = (
                root / ".signalos" / "agent-runs" / "test-run" / "tool-calls.jsonl"
            )
            entries = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual(entries[-1]["status"], "completed")
            self.assertTrue((root / "f.txt").is_file())


# ---------------------------------------------------------------------------
# Idempotency (Q5a / INV-5)
# ---------------------------------------------------------------------------


class TestIdempotency(unittest.TestCase):
    def test_rewrite_same_content_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "f.txt").write_text("same", encoding="utf-8")
            provider = AgentTestProvider(
                script=[
                    _tool_resp("write_file", {"path": "f.txt", "content": "same"}),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider)
            result = loop.run("sys", "write f")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("idempotent", tool_msgs[0]["content"].lower())
            ledger = (
                root / ".signalos" / "agent-runs" / "test-run" / "tool-calls.jsonl"
            )
            entries = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual(entries[0]["status"], "skipped-idempotent")


# ---------------------------------------------------------------------------
# Text-only degradation (INV-7 / T07)
# ---------------------------------------------------------------------------


class TestTextOnly(unittest.TestCase):
    def test_text_only_mode(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            provider = AgentTestProvider(script=[_end_resp("I would write a file")])
            adapter = _adapter(provider, supports_tool_calls=False)
            loop = AgentLoop(
                adapter=adapter,
                repo_root=root,
                enforcement_provider=StaticEnforcementProvider(),
                run_id="text-run",
            )
            result = loop.run("sys", "build me an app")
            self.assertEqual(result.status, "text_only")
            self.assertTrue(result.text_only)
            self.assertIn("write a file", result.final_text)


# ---------------------------------------------------------------------------
# Secret redaction (T21)
# ---------------------------------------------------------------------------


class TestRedaction(unittest.TestCase):
    def test_redact_openai_key(self):
        text = "key is sk-abcdefghijklmnopqrstuvwx1234 done"
        self.assertNotIn("abcdefghijklmnop", redact_secrets(text))
        self.assertIn("[REDACTED]", redact_secrets(text))

    def test_redact_assignment(self):
        text = "API_KEY=supersecretvalue123"
        self.assertIn("[REDACTED]", redact_secrets(text))


# ---------------------------------------------------------------------------
# Content secret-block (#20a): a write whose CONTENT embeds a secret is DENIED
# in the product runtime, audited under the secret-block rule. This is the
# product-side analogue of pre-tool-use-guard.sh Check 2 (redact --scan-diff).
# ---------------------------------------------------------------------------


class TestContentSecretBlock(unittest.TestCase):
    def _ledger_rules(self, root: Path) -> list:
        ledger = root / ".signalos" / "agent-runs" / "test-run" / "tool-calls.jsonl"
        return [
            json.loads(line).get("rule")
            for line in ledger.read_text().splitlines()
        ]

    def _ledger_statuses(self, root: Path) -> list:
        ledger = root / ".signalos" / "agent-runs" / "test-run" / "tool-calls.jsonl"
        return [
            json.loads(line).get("status")
            for line in ledger.read_text().splitlines()
        ]

    def _run_write(self, root: Path, path: str, content: str, **kw):
        provider = AgentTestProvider(
            script=[
                _tool_resp("write_file", {"path": path, "content": content}),
                _end_resp(),
            ]
        )
        loop = _loop(root, provider, **kw)
        result = loop.run("sys", "write a file")
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        return tool_msgs, result

    def test_embedded_anthropic_key_is_denied(self):
        # A test-path write clears every path/gate/tier check (G4 all signed),
        # so the ONLY thing that can block it is the content secret scan.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            secret = "sk-ant-" + "a1b2c3d4e5f6g7h8i9j0k1l2"
            tool_msgs, _ = self._run_write(
                root,
                "src/App.test.tsx",
                f"const KEY = '{secret}'\ntest('x',()=>{{}})",
                active_gate="G4",
                signed_gates=[0, 1, 2, 3],
            )
            self.assertIn("DENIED", tool_msgs[0]["content"])
            self.assertIn("secret-block", self._ledger_rules(root))
            self.assertIn("denied", self._ledger_statuses(root))
            # The file must NOT have been written.
            self.assertFalse((root / "src" / "App.test.tsx").is_file())

    def test_embedded_github_pat_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            secret = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"
            tool_msgs, _ = self._run_write(
                root, "src/util.test.ts", f"const t = '{secret}'",
                active_gate="G4", signed_gates=[0, 1, 2, 3],
            )
            self.assertIn("DENIED", tool_msgs[0]["content"])
            self.assertIn("secret-block", self._ledger_rules(root))

    def test_embedded_key_assignment_is_denied(self):
        # *_KEY / *_TOKEN / *_SECRET / *_PASSWORD assignment shape — the
        # pattern that _redact_string alone (value-only) would miss.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            tool_msgs, _ = self._run_write(
                root, "src/config.test.ts",
                'const STRIPE_SECRET_KEY = "hunter2plaintextvalue"',
                active_gate="G4", signed_gates=[0, 1, 2, 3],
            )
            self.assertIn("DENIED", tool_msgs[0]["content"])
            self.assertIn("secret-block", self._ledger_rules(root))

    def test_edit_with_embedded_secret_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            (root / "src").mkdir(parents=True, exist_ok=True)
            (root / "src" / "App.test.tsx").write_text(
                "const OLD = 1", encoding="utf-8"
            )
            secret = "sk-ant-" + "z9y8x7w6v5u4t3s2r1q0p9o8"
            provider = AgentTestProvider(
                script=[
                    _tool_resp(
                        "edit_file",
                        {
                            "path": "src/App.test.tsx",
                            "old_string": "const OLD = 1",
                            "new_string": f"const OLD = '{secret}'",
                        },
                    ),
                    _end_resp(),
                ]
            )
            loop = _loop(
                root, provider, active_gate="G4", signed_gates=[0, 1, 2, 3]
            )
            result = loop.run("sys", "edit a file")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("DENIED", tool_msgs[0]["content"])
            self.assertIn("secret-block", self._ledger_rules(root))
            # The file must be unchanged (secret not persisted).
            self.assertEqual(
                (root / "src" / "App.test.tsx").read_text(encoding="utf-8"),
                "const OLD = 1",
            )

    def test_clean_content_write_passes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            tool_msgs, _ = self._run_write(
                root, "src/App.test.tsx",
                "test('renders', () => { expect(1).toBe(1) })",
                active_gate="G4", signed_gates=[0, 1, 2, 3],
            )
            self.assertIn("OK", tool_msgs[0]["content"])
            self.assertNotIn("secret-block", self._ledger_rules(root))
            self.assertTrue((root / "src" / "App.test.tsx").is_file())


# ---------------------------------------------------------------------------
# Tool-call limit (2.7)
# ---------------------------------------------------------------------------


class TestToolLimit(unittest.TestCase):
    def test_budget_exhaustion_returns_control(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # Provider always asks for a search (never ends) -> hit the limit.
            script = [
                _tool_resp("search_files", {"pattern": "*.x"}, call_id=f"c{i}")
                for i in range(10)
            ]
            provider = AgentTestProvider(script=script)
            loop = _loop(root, provider, tool_call_limit=3)
            result = loop.run("sys", "loop forever")
            self.assertEqual(result.status, "budget_exhausted")
            self.assertEqual(result.tool_calls_made, 3)

    def test_budget_exhausted_resume_does_not_call_provider(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            provider = _RaisingProvider(AssertionError("provider must not be called"))
            loop = _loop(root, provider, tool_call_limit=3)
            loop.run_dir.mkdir(parents=True, exist_ok=True)
            loop.state_path.write_text(
                json.dumps(
                    {
                        "run_id": loop.run_id,
                        "status": "running",
                        "tool_calls_made": 3,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            loop.conversation_path.write_text(
                json.dumps({"role": "system", "content": "sys"}) + "\n"
                + json.dumps({"role": "user", "content": "resume"}) + "\n",
                encoding="utf-8",
            )

            result = loop.resume()

            self.assertEqual(result.status, "budget_exhausted")
            self.assertEqual(result.tool_calls_made, 3)


# ---------------------------------------------------------------------------
# Durable resume (P3 3.3-3.4)
# ---------------------------------------------------------------------------


class TestResume(unittest.TestCase):
    def _seed_running_run(self, root: Path, run_id: str, tool_calls_made: int = 1):
        run_dir = root / ".signalos" / "agent-runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "state.json").write_text(
            json.dumps({
                "run_id": run_id,
                "status": "running",
                "tool_calls_made": tool_calls_made,
                "trust_tier": "T3",
                "updated_at": "2026-06-02T00:00:00Z",
            }),
            encoding="utf-8",
        )
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "continue this run"},
            {"role": "assistant", "content": "prior work"},
        ]
        (run_dir / "conversation.jsonl").write_text(
            "\n".join(json.dumps(m) for m in messages) + "\n",
            encoding="utf-8",
        )
        return messages

    def test_resume_continues_persisted_conversation_without_new_user_turn(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            messages = self._seed_running_run(root, "resume-run")
            provider = AgentTestProvider(script=[_end_resp("resumed")])
            loop = AgentLoop(
                adapter=_adapter(provider),
                repo_root=root,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="resume-run",
            )

            result = loop.resume()

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.final_text, "resumed")
            self.assertEqual(result.tool_calls_made, 1)
            self.assertEqual(provider.calls[0]["messages"], messages)
            state = json.loads(
                (root / ".signalos" / "agent-runs" / "resume-run" / "state.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "completed")

    def test_resume_honors_cancel_check_before_provider_call(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._seed_running_run(root, "cancel-run")
            provider = AgentTestProvider(script=[_end_resp("should not call")])
            events: list[dict] = []
            loop = AgentLoop(
                adapter=_adapter(provider),
                repo_root=root,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="cancel-run",
                cancel_check=lambda: True,
                emit=events.append,
            )

            result = loop.resume()

            self.assertEqual(result.status, "cancelled")
            self.assertEqual(provider.calls, [])
            self.assertTrue(any(e.get("type") == "cancelled" for e in events))
            state = json.loads(
                (root / ".signalos" / "agent-runs" / "cancel-run" / "state.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "cancelled")


# ---------------------------------------------------------------------------
# Security scan on write (T24 / 2.9)
# ---------------------------------------------------------------------------


class TestSecurityScan(unittest.TestCase):
    def test_eval_flagged_on_write(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            provider = AgentTestProvider(
                script=[
                    _tool_resp(
                        "write_file",
                        {"path": "src/app.js", "content": "eval(userInput)"},
                    ),
                    _end_resp(),
                ]
            )
            loop = _loop(root, provider)
            result = loop.run("sys", "write app.js")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertIn("SECURITY WARNING", tool_msgs[0]["content"])


# ---------------------------------------------------------------------------
# Tool definitions shape (T-misc)
# ---------------------------------------------------------------------------


class TestToolDefinitions(unittest.TestCase):
    def test_all_tools_present(self):
        names = {t.name for t in build_tool_definitions()}
        self.assertEqual(
            names,
            {"read_file", "write_file", "edit_file", "run_command",
             "search_files", "list_directory"},
        )

    def test_openai_tool_shape(self):
        t = build_tool_definitions()[0].as_openai_tool()
        self.assertEqual(t["type"], "function")
        self.assertIn("name", t["function"])
        self.assertIn("parameters", t["function"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# list_directory tool (Gap 5) + trust-tier-paths seed (Gap 6)
# ---------------------------------------------------------------------------


class TestDiffEvent(unittest.TestCase):
    def test_write_emits_diff_event(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            events = []
            provider = AgentTestProvider(
                script=[_tool_resp("write_file", {"path": "a.txt", "content": "hello"}), _end_resp()]
            )
            loop = _loop(root, provider)
            loop._emit = events.append  # capture emitted events
            loop.run("sys", "write a.txt")
            diffs = [e for e in events if e.get("type") == "diff"]
            self.assertEqual(len(diffs), 1)
            self.assertEqual(diffs[0]["path"], "a.txt")
            self.assertEqual(diffs[0]["before"], "")
            self.assertEqual(diffs[0]["after"], "hello")


class TestListDirectoryAndSeed(unittest.TestCase):
    def test_list_directory_lists_entries(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sub").mkdir()
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "b.txt").write_text("b", encoding="utf-8")
            provider = AgentTestProvider(
                script=[_tool_resp("list_directory", {"path": "."}), _end_resp()]
            )
            loop = _loop(root, provider)
            result = loop.run("sys", "list root")
            self.assertEqual(result.status, "completed")
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            out = tool_msgs[0]["content"]
            self.assertIn("[dir] sub", out)
            self.assertIn("[file] a.txt", out)
            self.assertIn("[file] b.txt", out)

    def test_list_directory_is_sixth_tool(self):
        names = {t.name for t in build_tool_definitions()}
        self.assertIn("list_directory", names)
        self.assertEqual(len(names), 6)

    def test_list_directory_read_allowlist_enforced(self):
        # On T2, reads outside the read allowlist (which is ** by default, so
        # craft a tier with a narrow read list) are denied.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "secrets").mkdir()
            enf = StaticEnforcementProvider(trust_tier="T2")
            # Seed a restrictive read allowlist for T2.
            import json as _json
            sig = root / ".signalos"
            sig.mkdir(parents=True, exist_ok=True)
            (sig / "trust-tier-paths.json").write_text(
                _json.dumps({"T2": {"read": ["src/**"], "write": [], "execute": []}}),
                encoding="utf-8",
            )
            provider = AgentTestProvider(
                script=[_tool_resp("list_directory", {"path": "secrets"}), _end_resp()]
            )
            loop = _loop(root, provider, enforcement=enf)
            result = loop.run("sys", "peek")
            # denial is surfaced as a tool message (INV-4), not a crash
            tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
            self.assertTrue(
                any("not in the T2 read allowlist" in m["content"] for m in tool_msgs)
            )

    def test_seed_trust_tier_paths_idempotent(self):
        from signalos_lib.product.enforcement_state import (
            seed_trust_tier_paths,
            load_trust_tier_paths,
        )
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = seed_trust_tier_paths(root)
            self.assertTrue(p.is_file())
            data = load_trust_tier_paths(root)
            self.assertEqual(
                sorted(data.keys()), ["T1", "T2", "T3", "forbidden_always"]
            )
            self.assertIn("rm -rf", data["forbidden_always"]["execute"])
            # second call is a no-op (does not raise, file still valid)
            seed_trust_tier_paths(root)
            self.assertTrue(p.is_file())
