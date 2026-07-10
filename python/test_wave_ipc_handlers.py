"""test_wave_ipc_handlers.py — IPC wiring for WaveEngine.

Per WAVE-ENGINE-DESIGN §3.1 + §5 + §8. Exercises the wave:* handlers
in signalos_ipc_server through the request/response shape the chat
layer uses, including the per-request engine reconstruction model
(state is on disk via inspect(), not in the IPC process).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import signalos_ipc_server as ipc
from conftest import seed_signed_gate


@contextmanager
def _in_workspace(soul_text: str | None = None):
    """Run a test in a temp workspace, restoring cwd on exit."""
    orig_cwd = os.getcwd()
    root = Path(tempfile.mkdtemp(prefix="signalos-ipc-")).resolve()
    (root / ".signalos").mkdir()
    if soul_text is not None:
        if soul_text.count("\n") < 3:
            soul_text = (
                soul_text.rstrip("\n")
                + "\nOwner: PO.\nReviewer: lead engineer.\nReady when signed.\n"
            )
        # Gate detection is signature-based and fail-closed on the whole G0
        # manifest: seed+sign ALL four G0 artifacts (Soul carries the body)
        # so it counts as a passed G0, not just a drafted/partly-signed file.
        seed_signed_gate(
            root, "G0",
            bodies={"core/governance/Governance/SOUL-DOCUMENT.md": soul_text},
        )
    os.chdir(str(root))
    try:
        yield root
    finally:
        os.chdir(orig_cwd)


def _handle(command: str, args: list, project_id: str = "default") -> dict:
    return ipc.handle({
        "id": "test-req",
        "command": command,
        "args": args,
        "cwd": os.getcwd(),
        "project_id": project_id,
    })


# ---------------------------------------------------------------------------
# wave:begin
# ---------------------------------------------------------------------------

class WaveBeginHandlerTests(unittest.TestCase):
    def test_empty_workspace_dispatches_g0(self):
        with _in_workspace():
            resp = _handle("wave:begin", ["Build a todo app"])
        self.assertTrue(resp["ok"], resp)
        data = resp["data"]
        self.assertEqual(data["action"], "fire-agent-G0")
        self.assertEqual(data["current_gate"], "G0")
        self.assertTrue(data["agent"]["exists"])
        self.assertEqual(data["system_bubble"]["kind"], "reroute")

    def test_missing_user_request_errors(self):
        with _in_workspace():
            resp = _handle("wave:begin", [])
        self.assertFalse(resp["ok"])
        self.assertIn("user_request", resp["error"])


# ---------------------------------------------------------------------------
# wave:reply (auto-sign on affirm)
# ---------------------------------------------------------------------------

class WaveReplyHandlerTests(unittest.TestCase):
    def test_affirmative_reply_auto_signs_current_gate(self):
        with _in_workspace():
            # Don't need a prior wave:begin — the IPC handler reconstructs
            # the engine fresh and uses current_gate from the args.
            resp = _handle("wave:reply", ["yes", "G0"])
        self.assertTrue(resp["ok"], resp)
        data = resp["data"]
        self.assertTrue(data.get("auto_signed"))
        self.assertEqual(data["signed_gate"], "G0")
        self.assertEqual(data["current_gate"], "G1")

    def test_refine_reply_keeps_gate(self):
        with _in_workspace():
            resp = _handle("wave:reply", ["change the title to X", "G0"])
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["data"]["action"], "refine")

    def test_invalid_gate_returns_error_action(self):
        with _in_workspace():
            resp = _handle("wave:reply", ["yes", "G99"])
        self.assertTrue(resp["ok"])  # IPC succeeded but engine refused
        self.assertEqual(resp["data"]["action"], "error")
        self.assertIn("Unknown gate", resp["data"]["error"])

    def test_missing_args_returns_ipc_error(self):
        with _in_workspace():
            resp = _handle("wave:reply", ["yes"])  # missing current_gate
        self.assertFalse(resp["ok"])


# ---------------------------------------------------------------------------
# wave:scope-drift-resolve
# ---------------------------------------------------------------------------

class WaveScopeDriftHandlerTests(unittest.TestCase):
    def test_drift_resolution_returns_action(self):
        soul = (
            "Personal todo app for me — daily tasks reminders only, "
            "nothing else, just for my own use day to day."
        )
        with _in_workspace(soul):
            resp = _handle("wave:scope-drift-resolve", [
                "Customer-facing enterprise dashboard for our clients",
                "a",
            ])
        self.assertTrue(resp["ok"], resp)
        data = resp["data"]
        # "a" → amend → fire-agent-G0 in amend mode
        self.assertEqual(data["action"], "fire-agent-G0")
        self.assertEqual(data["mode"], "amend")

    def test_no_drift_returns_no_longer_drifted(self):
        with _in_workspace():
            # Empty workspace — no soul, no drift possible.
            resp = _handle("wave:scope-drift-resolve", ["any request", "a"])
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["data"]["action"], "no-longer-drifted")


# ---------------------------------------------------------------------------
# wave:translate-external
# ---------------------------------------------------------------------------

class WaveTranslateExternalHandlerTests(unittest.TestCase):
    def test_translates_local_markdown(self):
        with _in_workspace() as root:
            belief = root / "ext-belief.md"
            belief.write_text("External belief text.\n", encoding="utf-8")
            resp = _handle("wave:translate-external", [str(belief), "G1"])
        self.assertTrue(resp["ok"], resp)
        data = resp["data"]
        self.assertTrue(data["translation"]["supported"])
        self.assertEqual(data["gate"], "G1")
        self.assertIn("External belief", data["translation"]["text"])

    def test_unknown_format_returns_unsupported(self):
        with _in_workspace():
            resp = _handle("wave:translate-external", ["random.unknown"])
        self.assertTrue(resp["ok"])
        self.assertFalse(resp["data"]["translation"]["supported"])


# ---------------------------------------------------------------------------
# wave:violation-request / wave:violation-confirm
# ---------------------------------------------------------------------------

class WaveViolationHandlerTests(unittest.TestCase):
    def test_request_returns_prompt_and_bubble(self):
        with _in_workspace():
            payload = json.dumps({
                "violation_kind": "code-review",
                "findings": ["uses eval()", "missing null check"],
                "gate": "G4",
            })
            resp = _handle("wave:violation-request", [payload])
        self.assertTrue(resp["ok"], resp)
        data = resp["data"]
        self.assertEqual(data["prompt"]["violation_kind"], "code-review")
        self.assertEqual(data["prompt"]["gate"], "G4")
        self.assertEqual(data["system_bubble"]["gate"], "G4")

    def test_confirm_writes_audit_entry_to_trail(self):
        with _in_workspace() as root:
            payload = json.dumps({
                "violation_kind": "security-audit",
                "choice": "c",
                "user_reply": "risk accepted; fix in W7.2",
                "gate": "G4",
                "findings": ["xss in title"],
            })
            resp = _handle("wave:violation-confirm", [payload])
        self.assertTrue(resp["ok"], resp)
        # Audit trail contains a violation entry with the user_reply.
        trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
        self.assertTrue(trail.is_file())
        entries = [json.loads(l) for l in trail.read_text().splitlines() if l.strip()]
        violation_entries = [
            e for e in entries
            if e.get("action") == "violation:security-audit:override-with-log"
        ]
        self.assertEqual(len(violation_entries), 1)
        self.assertIn("risk accepted", violation_entries[0]["evidence"])

    def test_confirm_invalid_choice_returns_error(self):
        with _in_workspace():
            payload = json.dumps({
                "violation_kind": "x",
                "choice": "maybe",
                "user_reply": "huh",
            })
            resp = _handle("wave:violation-confirm", [payload])
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["data"]["action"], "error")

    def test_missing_violation_kind_in_request_returns_error(self):
        with _in_workspace():
            resp = _handle("wave:violation-request", [json.dumps({"findings": []})])
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["data"]["action"], "error")


# ---------------------------------------------------------------------------
# wave:g5-handoff
# ---------------------------------------------------------------------------

class WaveG5HandoffHandlerTests(unittest.TestCase):
    def test_handoff_without_git_returns_skipped(self):
        with _in_workspace():
            resp = _handle("wave:g5-handoff", ["W7.1", json.dumps({"tasks": []})])
        self.assertTrue(resp["ok"], resp)
        self.assertEqual(resp["data"]["commit_outcome"]["status"], "skipped")
        self.assertIn("no-git-dir", resp["data"]["commit_outcome"]["reason"])


if __name__ == "__main__":
    unittest.main()
