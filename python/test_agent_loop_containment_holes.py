# test_agent_loop_containment_holes.py
# Confirmed containment/integrity holes in the governed AgentLoop and their
# fixes. Each class documents the RED (pre-fix escape) it proves is now closed:
#
#   1. run_command WRITES escaped write-governance entirely -- a `python -c`
#      could drop a secrets file, tamper `.signalos/AUDIT_TRAIL.jsonl`, or gut a
#      signed plan test with content_sha256=None and NO filesystem diff. Now the
#      loop snapshots the governed tree, diffs after the command, audits every
#      change (with a content hash), and reverts+denies forbidden/immutable/
#      secret writes.
#   2. A SIGNED core/** artifact was overwritable (only plan-test BYTES were
#      frozen). Now a signed artifact is immutable to write_file/edit_file AND to
#      a command write.
#   3. The G4 verification CONTRACT (package.json scripts.test/build, tsconfig
#      test include/exclude) was agent-mutable -- the model could rewrite `test`
#      to `exit 0` or exclude the frozen tests and "pass". Now those keys are
#      frozen, and validation confirms the frozen tests were actually collected.
#   4. wave_frozen was loaded but NEVER enforced in the engine. Now a frozen wave
#      denies every mutation (write/edit/command).
#   5. warn==strict: every governance branch hard-denied. Now warn LOGS+ALLOWS,
#      block denies, off is a no-op.
#
# Deterministic (INV-6): the command-write diff is exercised both end-to-end via
# a real `python -c` (skipped if python is absent) AND directly via
# _enforce_command_writes so the security logic never depends on an interpreter.

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.harness import AgentResponse, AgentTestProvider, TokenUsage, ToolCall
from signalos_lib.product.agent_loop import AgentLoop, ToolPolicyError
from signalos_lib.product.enforcement_state import (
    StaticEnforcementProvider,
    seed_trust_tier_paths,
)
from signalos_lib.product.provider_adapter import ProviderAdapter, ProviderCapabilities


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _tool_resp(name: str, args: dict, call_id: str = "c1") -> AgentResponse:
    return AgentResponse(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        stop_reason="tool_use",
        usage=TokenUsage(1, 1),
    )


def _end_resp(text: str = "done") -> AgentResponse:
    return AgentResponse(content=text, tool_calls=None, stop_reason="end_turn",
                         usage=TokenUsage(1, 1))


def _adapter(provider, model="claude-sonnet-4-5"):
    caps = ProviderCapabilities(model=model, supports_tool_calls=True,
                                supports_streaming=True, context_length=200_000)
    return ProviderAdapter(model=model, provider=provider, capabilities=caps)


def _loop(root: Path, provider, *, tier="T3", rule_modes=None, wave_frozen=False,
          active_gate=None, signed_gates=None, execution_context="delivery",
          emit=None, run_id="hole-run") -> AgentLoop:
    enf = StaticEnforcementProvider(
        trust_tier=tier, rule_modes=rule_modes, wave_frozen=wave_frozen,
        signed_gates=signed_gates,
    )
    return AgentLoop(
        adapter=_adapter(provider),
        repo_root=root,
        enforcement_provider=enf,
        run_id=run_id,
        active_gate=active_gate,
        signed_gates=signed_gates,
        execution_context=execution_context,
        emit=emit,
    )


def _tool_msgs(result) -> list[str]:
    return [m["content"] for m in result.messages if m.get("role") == "tool"]


def _ledger(root: Path, run_id="hole-run") -> list[dict]:
    p = root / ".signalos" / "agent-runs" / run_id / "tool-calls.jsonl"
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write_signed_artifact(root: Path, rel: str) -> Path:
    """Create a gate artifact with a real (non-DRAFT) signature block so
    sign.check_gate reports it SIGNED."""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# Artifact\n\noriginal signed body\n\n## Signatures\n\n```yaml\n"
        "- signer: Test PO\n  role: PO\n  date: 2026-07-11\n  gate: G1\n"
        "  verdict: APPROVED\n```\n",
        encoding="utf-8",
    )
    return p


_HAS_PYTHON = shutil.which("python") is not None
_GHP = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyzAB"  # unambiguous secret shape


# --------------------------------------------------------------------------- #
# FIX 1 -- command writes are governed, diffed, audited, reverted             #
# --------------------------------------------------------------------------- #


class TestCommandWriteGovernanceUnit(unittest.TestCase):
    """Deterministic: drive _enforce_command_writes directly, simulating what a
    command wrote, so the security logic never depends on an interpreter."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        seed_trust_tier_paths(self.root)
        self.loop = _loop(self.root, AgentTestProvider())
        self.loop._ensure_run_dir()
        self.assertIsNone(self.loop._load_enforcement())

    def tearDown(self):
        self._tmp.cleanup()

    def _snap(self):
        return self.loop._snapshot_governed_tree()

    def test_secret_file_written_by_command_is_reverted_and_denied(self):
        before = self._snap()
        (self.root / "secrets.txt").write_text(f"token = '{_GHP}'\n", encoding="utf-8")
        with self.assertRaises(ToolPolicyError) as ctx:
            self.loop._enforce_command_writes("python -c '...'", before)
        self.assertEqual(ctx.exception.rule, "secret-block")
        # Reverted: the offending NEW file is gone.
        self.assertFalse((self.root / "secrets.txt").exists())
        # Audited (tamper-evident): a command-file-change row with a real hash.
        rows = [r for r in _ledger(self.root) if r["status"] == "command-file-change"]
        self.assertTrue(any(r["args"].get("path") == "secrets.txt"
                            and r["content_sha256"] for r in rows))

    def test_audit_trail_tamper_by_command_is_reverted_and_denied(self):
        audit = self.root / ".signalos" / "AUDIT_TRAIL.jsonl"
        audit.write_text("original-audit-line\n", encoding="utf-8")
        before = self._snap()
        audit.write_text("TAMPERED\n", encoding="utf-8")
        with self.assertRaises(ToolPolicyError) as ctx:
            self.loop._enforce_command_writes("python -c '...'", before)
        self.assertEqual(ctx.exception.rule, "secret-block")
        # Restored to the pre-command bytes.
        self.assertEqual(audit.read_text(encoding="utf-8"), "original-audit-line\n")

    def test_gutting_signed_plan_test_via_command_is_reverted_at_g4(self):
        loop = _loop(self.root, AgentTestProvider(), active_gate="G4",
                     signed_gates=[0, 1, 2, 3])
        loop._ensure_run_dir()
        self.assertIsNone(loop._load_enforcement())
        test_path = (self.root / "core" / "execution" / "tests" / "skeletons"
                     / "wave-1" / "T1.test.ts")
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text("expect(real).toBe(spec)\n", encoding="utf-8")
        before = loop._snapshot_governed_tree()
        test_path.write_text("// gutted\n", encoding="utf-8")
        with self.assertRaises(ToolPolicyError) as ctx:
            loop._enforce_command_writes("python -c '...'", before)
        self.assertEqual(ctx.exception.rule, "spec-immutable")
        self.assertEqual(test_path.read_text(encoding="utf-8"),
                         "expect(real).toBe(spec)\n")

    def test_overwriting_signed_core_artifact_via_command_is_reverted(self):
        _write_signed_artifact(self.root, "core/strategy/BELIEF.md")
        loop = _loop(self.root, AgentTestProvider())
        loop._ensure_run_dir()
        self.assertIsNone(loop._load_enforcement())
        loop._signed_core_artifacts()  # primed pre-command (as _tool_run_command does)
        before = loop._snapshot_governed_tree()
        (self.root / "core" / "strategy" / "BELIEF.md").write_text(
            "hijacked\n", encoding="utf-8")
        with self.assertRaises(ToolPolicyError) as ctx:
            loop._enforce_command_writes("python -c '...'", before)
        self.assertEqual(ctx.exception.rule, "signed-immutable")
        self.assertIn("original signed body",
                      (self.root / "core" / "strategy" / "BELIEF.md")
                      .read_text(encoding="utf-8"))

    def test_legit_source_write_is_audited_not_reverted(self):
        before = self._snap()
        (self.root / "src").mkdir()
        (self.root / "src" / "generated.ts").write_text(
            "export const x = 1\n", encoding="utf-8")
        # No violation -> does not raise.
        self.loop._enforce_command_writes("python -c '...'", before)
        # File survives and is audited with a content hash.
        self.assertTrue((self.root / "src" / "generated.ts").is_file())
        rows = [r for r in _ledger(self.root)
                if r["status"] == "command-file-change"
                and r["args"].get("path") == "src/generated.ts"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["content_sha256"])

    def test_build_output_dirs_are_not_flagged(self):
        # A write under dist/ (build output) is PRUNED from the diff -- even a
        # secret-shaped byte there is neither flagged nor reverted, so a real
        # `npm run build` is never mistaken for an escape.
        (self.root / "dist").mkdir()
        before = self._snap()
        (self.root / "dist" / "out.js").write_text(f"const t = '{_GHP}'\n",
                                                    encoding="utf-8")
        self.loop._enforce_command_writes("npm run build", before)  # no raise
        self.assertTrue((self.root / "dist" / "out.js").is_file())  # not reverted
        rows = [r for r in _ledger(self.root) if r["status"] == "command-file-change"]
        self.assertFalse(rows, "build output must not be audited/flagged")


@unittest.skipUnless(_HAS_PYTHON, "python interpreter unavailable")
class TestCommandWriteGovernanceEndToEnd(unittest.TestCase):
    """The headline escape end-to-end: an ALLOWLISTED `python -c` cannot write a
    secret / `.signalos/` / signed path -- it is DENIED (not silently allowed)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        seed_trust_tier_paths(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, command, **kw):
        provider = AgentTestProvider(
            script=[_tool_resp("run_command", {"command": command}), _end_resp()])
        loop = _loop(self.root, provider, **kw)
        result = loop.run("sys", "run it")
        return result, _tool_msgs(result)

    def test_python_c_writing_secret_is_denied_and_reverted(self):
        # Writes `token = 'ghp_...'` (a quoted secret literal) via chr(39) to
        # avoid nested-quote parsing across shells.
        cmd = (f"python -c \"open('leak.txt','w').write('token = ' + chr(39) + "
               f"'{_GHP}' + chr(39))\"")
        _, msgs = self._run(cmd)
        self.assertTrue(msgs and "DENIED" in msgs[0], msgs)
        self.assertFalse((self.root / "leak.txt").exists())

    def test_python_c_tampering_signalos_is_denied_and_reverted(self):
        cmd = ("python -c \"open('.signalos/AUDIT_TRAIL.jsonl','w')"
               ".write('tampered')\"")
        _, msgs = self._run(cmd)
        self.assertTrue(msgs and "DENIED" in msgs[0], msgs)
        self.assertFalse((self.root / ".signalos" / "AUDIT_TRAIL.jsonl").exists())


# --------------------------------------------------------------------------- #
# FIX 2 -- signed core/** artifact immutability (write_file / edit_file)       #
# --------------------------------------------------------------------------- #


class TestSignedArtifactImmutability(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        seed_trust_tier_paths(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_overwrite_signed_artifact_is_denied(self):
        _write_signed_artifact(self.root, "core/strategy/BELIEF.md")
        provider = AgentTestProvider(script=[
            _tool_resp("write_file",
                       {"path": "core/strategy/BELIEF.md", "content": "rewritten"}),
            _end_resp()])
        loop = _loop(self.root, provider)
        result = loop.run("sys", "overwrite the signed belief")
        msgs = _tool_msgs(result)
        self.assertIn("DENIED", msgs[0])
        self.assertIn("signed-immutable", [r.get("rule") for r in _ledger(self.root)])
        self.assertIn("original signed body",
                      (self.root / "core" / "strategy" / "BELIEF.md")
                      .read_text(encoding="utf-8"))

    def test_unsigned_core_artifact_is_writable(self):
        # Same subtree, but no signature block -> writable (the guard keys off the
        # signature, so a reopen that strips it restores writability).
        p = self.root / "core" / "strategy" / "BELIEF.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("draft, unsigned\n", encoding="utf-8")
        provider = AgentTestProvider(script=[
            _tool_resp("write_file",
                       {"path": "core/strategy/BELIEF.md", "content": "revised body"}),
            _end_resp()])
        loop = _loop(self.root, provider)
        result = loop.run("sys", "revise the unsigned belief")
        self.assertIn("OK", _tool_msgs(result)[0])
        self.assertEqual(p.read_text(encoding="utf-8"), "revised body")


# --------------------------------------------------------------------------- #
# FIX 3 -- the G4 verification contract is frozen                             #
# --------------------------------------------------------------------------- #


class TestVerificationContractFreeze(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        seed_trust_tier_paths(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _g4_loop(self, provider):
        return _loop(self.root, provider, active_gate="G4", signed_gates=[0, 1, 2, 3])

    def test_rewriting_scripts_test_to_exit_0_is_denied(self):
        (self.root / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest run", "build": "vite build"}}),
            encoding="utf-8")
        provider = AgentTestProvider(script=[
            _tool_resp("write_file", {"path": "package.json",
                "content": json.dumps({"scripts": {"test": "exit 0",
                                                    "build": "vite build"}})}),
            _end_resp()])
        result = self._g4_loop(provider).run("sys", "neuter the test script")
        self.assertIn("DENIED", _tool_msgs(result)[0])
        self.assertIn("verification-frozen", [r.get("rule") for r in _ledger(self.root)])
        # Untouched on disk.
        self.assertEqual(json.loads((self.root / "package.json").read_text())
                         ["scripts"]["test"], "vitest run")

    def test_excluding_frozen_tests_via_tsconfig_is_denied(self):
        (self.root / "tsconfig.json").write_text(
            json.dumps({"include": ["src", "tests"], "exclude": ["node_modules"]}),
            encoding="utf-8")
        provider = AgentTestProvider(script=[
            _tool_resp("write_file", {"path": "tsconfig.json",
                "content": json.dumps({"include": ["src"],
                    "exclude": ["node_modules", "core/execution/tests"]})}),
            _end_resp()])
        result = self._g4_loop(provider).run("sys", "exclude the frozen tests")
        self.assertIn("DENIED", _tool_msgs(result)[0])
        self.assertIn("verification-frozen", [r.get("rule") for r in _ledger(self.root)])

    def test_creating_scripts_when_absent_is_not_frozen(self):
        # Freeze only kicks in once a value EXISTS -- initial authoring is fine.
        loop = self._g4_loop(AgentTestProvider())
        self.assertIsNone(loop._load_enforcement())
        self.assertIsNone(
            loop._verification_contract_violation(
                "package.json",
                json.dumps({"scripts": {"test": "vitest run"}}),
                None,  # no prior on-disk value
            ))

    def test_non_g4_gate_does_not_freeze_package_json(self):
        loop = _loop(self.root, AgentTestProvider(), active_gate="G2")
        self.assertIsNone(loop._load_enforcement())
        self.assertIsNone(
            loop._verification_contract_violation(
                "package.json",
                json.dumps({"scripts": {"test": "exit 0"}}),
                json.dumps({"scripts": {"test": "vitest run"}})))


# --------------------------------------------------------------------------- #
# FIX 4 -- a frozen wave denies every mutation in the engine                  #
# --------------------------------------------------------------------------- #


class TestWaveFreezeEnforced(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        seed_trust_tier_paths(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_frozen_wave_denies_write(self):
        provider = AgentTestProvider(script=[
            _tool_resp("write_file", {"path": "src/App.tsx", "content": "x"}),
            _end_resp()])
        result = _loop(self.root, provider, wave_frozen=True).run("sys", "write")
        self.assertIn("DENIED", _tool_msgs(result)[0])
        self.assertIn("wave-freeze", [r.get("rule") for r in _ledger(self.root)])
        self.assertFalse((self.root / "src" / "App.tsx").exists())

    def test_frozen_wave_denies_command(self):
        provider = AgentTestProvider(script=[
            _tool_resp("run_command", {"command": "npm test"}), _end_resp()])
        result = _loop(self.root, provider, wave_frozen=True).run("sys", "run")
        self.assertIn("DENIED", _tool_msgs(result)[0])

    def test_frozen_wave_still_allows_read(self):
        (self.root / "readme.md").write_text("hi", encoding="utf-8")
        provider = AgentTestProvider(script=[
            _tool_resp("read_file", {"path": "readme.md"}), _end_resp()])
        result = _loop(self.root, provider, wave_frozen=True).run("sys", "read")
        self.assertIn("hi", _tool_msgs(result)[0])
        self.assertNotIn("DENIED", _tool_msgs(result)[0])


# --------------------------------------------------------------------------- #
# FIX 5 -- observe / warn / block ladder                                       #
# --------------------------------------------------------------------------- #


class TestWarnBlockOff(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        seed_trust_tier_paths(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, path, content, rule_modes, tier="T2"):
        events: list[dict] = []
        provider = AgentTestProvider(script=[
            _tool_resp("write_file", {"path": path, "content": content}),
            _end_resp()])
        loop = _loop(self.root, provider, tier=tier, rule_modes=rule_modes,
                     emit=events.append)
        result = loop.run("sys", "write")
        return _tool_msgs(result), events

    def test_trust_tier_block_denies(self):
        # Default strict -> a write outside the T2 allowlist is hard-denied.
        msgs, _ = self._write("secret_dir/keys.txt", "value",
                              {"trust-tier": "strict"})
        self.assertIn("DENIED", msgs[0])
        self.assertFalse((self.root / "secret_dir" / "keys.txt").exists())

    def test_trust_tier_warn_logs_and_allows(self):
        # warn -> the same out-of-allowlist write is LOGGED and ALLOWED.
        msgs, events = self._write("secret_dir/keys.txt", "value",
                                   {"trust-tier": "warn"})
        self.assertIn("OK", msgs[0])
        self.assertTrue((self.root / "secret_dir" / "keys.txt").is_file())
        warns = [e for e in events if e.get("type") == "governance_warning"
                 and e.get("rule") == "trust-tier"]
        self.assertTrue(warns, "warn mode must emit a governance_warning")

    def test_trust_tier_off_allows_silently(self):
        msgs, events = self._write("secret_dir/keys.txt", "value",
                                   {"trust-tier": "off"})
        self.assertIn("OK", msgs[0])
        self.assertTrue((self.root / "secret_dir" / "keys.txt").is_file())
        self.assertFalse([e for e in events if e.get("type") == "governance_warning"])

    def test_secret_block_warn_allows_write_but_warns(self):
        msgs, events = self._write(
            "src/config.ts", f"const API_KEY = '{_GHP}'\n",
            {"secret-block": "warn"}, tier="T3")
        self.assertIn("OK", msgs[0])
        self.assertTrue((self.root / "src" / "config.ts").is_file())
        self.assertTrue([e for e in events if e.get("type") == "governance_warning"
                         and e.get("rule") == "secret-block"])

    def test_secret_block_strict_still_denies(self):
        msgs, _ = self._write("src/config.ts", f"const API_KEY = '{_GHP}'\n",
                              {"secret-block": "strict"}, tier="T3")
        self.assertIn("DENIED", msgs[0])
        self.assertFalse((self.root / "src" / "config.ts").exists())


# --------------------------------------------------------------------------- #
# G3 design-output carve-out                                                   #
# --------------------------------------------------------------------------- #


class TestDesignOutputCarveout(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        seed_trust_tier_paths(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, path, active_gate):
        provider = AgentTestProvider(script=[
            _tool_resp("write_file", {"path": path, "content": "id: candidate-a\n"}),
            _end_resp()])
        loop = _loop(self.root, provider, tier="T2", active_gate=active_gate)
        return _tool_msgs(loop.run("sys", "write design"))

    def test_g3_may_write_under_signalos_designs(self):
        msgs = self._write(".signalos/designs/w1/DESIGN_DECISIONS.yaml", "G3")
        self.assertIn("OK", msgs[0])
        self.assertTrue(
            (self.root / ".signalos" / "designs" / "w1" / "DESIGN_DECISIONS.yaml")
            .is_file())

    def test_g3_still_blocked_from_other_signalos_paths(self):
        msgs = self._write(".signalos/gates.json", "G3")
        self.assertIn("DENIED", msgs[0])

    def test_non_design_gate_cannot_write_signalos_designs(self):
        msgs = self._write(".signalos/designs/w1/DESIGN_DECISIONS.yaml", "G4")
        self.assertIn("DENIED", msgs[0])


if __name__ == "__main__":
    unittest.main()
