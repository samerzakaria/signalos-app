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
    ProviderCapabilities,
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


def _loop(tmp: Path, provider, enforcement=None, **kw) -> AgentLoop:
    return AgentLoop(
        adapter=_adapter(provider),
        repo_root=tmp,
        enforcement_provider=enforcement or StaticEnforcementProvider(trust_tier="T3"),
        run_id="test-run",
        **kw,
    )


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
# Tool-call limit (2.7)
# ---------------------------------------------------------------------------


class TestToolLimit(unittest.TestCase):
    def test_tool_limit_returns_control(self):
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
            self.assertEqual(result.status, "tool_limit")
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
