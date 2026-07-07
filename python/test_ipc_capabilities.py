# test_ipc_capabilities.py
# IPC wiring for four backend capabilities:
#   audit:replay-timeline  -> audit_replay.build_timeline pass-through
#   share:export           -> share_export.write_share_bundle
#   brownfield:audit       -> brownfield.audit_existing_repo / apply_governance
#   competitor:analyze     -> competitor.build_matrix (LLM-gated)
# plus the agent:deliver brownfield auto-trigger.
#
# Contract shapes live in the response's `data` object ({"status": "ok", ...});
# domain failures are {"status": "error", "error": ...} inside data.

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_ipc_server as srv  # noqa: E402

from test_agent_ipc import (  # noqa: E402
    _AgentIpcBase,
    _adapter_factory,
    _agent_args,
    _end_resp,
)


def _handle(command: str, args: list | None = None) -> dict:
    return srv.handle({
        "id": "test-req",
        "command": command,
        "args": args or [],
        "cwd": os.getcwd(),
    })


def _seed_audit_trail(root: Path, n: int = 3) -> None:
    d = root / ".signalos"
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"ts": f"2026-06-01T10:00:{i:02d}Z", "action": "wave.start", "wave": "W1"})
        for i in range(n - 1)
    ]
    lines.append(json.dumps(
        {"ts": "2026-06-01T11:00:00Z", "action": "gate.signed", "gate": "G2", "role": "PO"}
    ))
    (d / "AUDIT_TRAIL.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# audit:replay-timeline
# ---------------------------------------------------------------------------


def test_replay_timeline_empty_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = _handle("audit:replay-timeline")
    assert resp["ok"], resp
    assert resp["data"] == {"status": "ok", "frames": [], "truncated": False}


def test_replay_timeline_returns_full_frames(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_audit_trail(tmp_path, n=3)
    resp = _handle("audit:replay-timeline")
    assert resp["ok"], resp
    data = resp["data"]
    assert data["status"] == "ok"
    assert data["truncated"] is False
    frames = data["frames"]
    assert len(frames) == 3
    # Frame shape: index / ts / summary / entry / state_after.
    for key in ("index", "ts", "summary", "entry", "state_after"):
        assert key in frames[0]
    assert frames[-1]["state_after"]["gates"]["G2"]["signed"] is True
    assert frames[-1]["index"] == 2


def test_replay_timeline_limit_returns_last_frames(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_audit_trail(tmp_path, n=5)
    resp = _handle("audit:replay-timeline", [json.dumps({"limit": 2})])
    assert resp["ok"], resp
    data = resp["data"]
    assert data["truncated"] is True
    assert [f["index"] for f in data["frames"]] == [3, 4]


def test_replay_timeline_accepts_bare_int_arg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_audit_trail(tmp_path, n=4)
    resp = _handle("audit:replay-timeline", ["1"])
    assert resp["ok"], resp
    assert [f["index"] for f in resp["data"]["frames"]] == [3]
    assert resp["data"]["truncated"] is True


def test_replay_timeline_hard_cap_1000(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / ".signalos"
    d.mkdir(parents=True)
    rows = "\n".join(
        json.dumps({"ts": "t", "action": "wave.tick", "wave": "W1"})
        for _ in range(1005)
    )
    (d / "AUDIT_TRAIL.jsonl").write_text(rows + "\n", encoding="utf-8")

    resp = _handle("audit:replay-timeline")
    assert resp["ok"], resp
    assert len(resp["data"]["frames"]) == 1000
    assert resp["data"]["truncated"] is True
    assert resp["data"]["frames"][0]["index"] == 5  # last 1000 of 1005

    # A limit above the cap is still capped.
    resp = _handle("audit:replay-timeline", [json.dumps({"limit": 5000})])
    assert len(resp["data"]["frames"]) == 1000
    assert resp["data"]["truncated"] is True


def test_replay_timeline_invalid_limit_is_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = _handle("audit:replay-timeline", ["not-a-number"])
    assert not resp["ok"]
    assert "limit" in resp["error"]
    resp = _handle("audit:replay-timeline", [json.dumps({"limit": -3})])
    assert not resp["ok"]


def test_replay_timeline_is_read_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_audit_trail(tmp_path, n=2)
    before = (tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl").read_text(encoding="utf-8")
    _handle("audit:replay-timeline")
    after = (tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl").read_text(encoding="utf-8")
    assert before == after


# ---------------------------------------------------------------------------
# share:export
# ---------------------------------------------------------------------------


def test_share_export_writes_bundle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_audit_trail(tmp_path, n=2)
    resp = _handle("share:export")
    assert resp["ok"], resp
    data = resp["data"]
    assert data["status"] == "ok"
    assert data["files"] == ["share.html", "share.json"]
    bundle_dir = Path(data["path"])
    assert bundle_dir.is_absolute()
    for name in data["files"]:
        assert (bundle_dir / name).is_file()
    parsed = json.loads((bundle_dir / "share.json").read_text(encoding="utf-8"))
    assert parsed["read_only"] is True


def test_share_export_empty_workspace_still_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = _handle("share:export")
    assert resp["ok"], resp
    assert resp["data"]["status"] == "ok"


def test_share_export_excludes_env_secrets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_audit_trail(tmp_path, n=2)
    # Built at runtime so no secret-shaped literal lands in the repo.
    secret = "sk-" + ("z" * 30)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=" + secret + "\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("VAULT_TOKEN=" + secret + "\n", encoding="utf-8")
    resp = _handle("share:export")
    assert resp["ok"], resp
    bundle_dir = Path(resp["data"]["path"])
    for name in resp["data"]["files"]:
        content = (bundle_dir / name).read_text(encoding="utf-8")
        assert secret not in content
    # The bundle contains only its own two files (no .env copied along).
    assert sorted(p.name for p in bundle_dir.iterdir()) == ["share.html", "share.json"]


def test_share_export_failure_is_error_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Occupy the bundle path with a FILE so mkdir fails.
    (tmp_path / ".signalos").mkdir()
    (tmp_path / ".signalos" / "share").write_text("not a dir", encoding="utf-8")
    resp = _handle("share:export")
    assert resp["ok"], resp  # transport ok; domain failure is in data
    assert resp["data"]["status"] == "error"
    assert resp["data"]["error"]


# ---------------------------------------------------------------------------
# brownfield:audit
# ---------------------------------------------------------------------------


def _seed_bare_repo(root: Path) -> None:
    (root / "package.json").write_text('{"name":"x","dependencies":{}}', encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "index.js").write_text("export const x = 1;\n", encoding="utf-8")


def test_brownfield_audit_reports_without_applying(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_bare_repo(tmp_path)
    resp = _handle("brownfield:audit")
    assert resp["ok"], resp
    data = resp["data"]
    assert data["status"] == "ok"
    assert data["applied"] is False
    assert data["report"]["summary"]["total"] >= 1
    areas = {f["area"] for f in data["report"]["findings"]}
    assert "testing" in areas
    # Nothing was written.
    assert not (tmp_path / ".signalos" / "GOVERNANCE_BASELINE.md").exists()
    assert not (tmp_path / ".signalos" / "profile.json").exists()


def test_brownfield_audit_apply_false_explicit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_bare_repo(tmp_path)
    resp = _handle("brownfield:audit", [json.dumps({"apply": False})])
    assert resp["ok"], resp
    assert resp["data"]["applied"] is False
    assert not (tmp_path / ".signalos" / "GOVERNANCE_BASELINE.md").exists()


def test_brownfield_audit_apply_true_writes_governance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_bare_repo(tmp_path)
    resp = _handle("brownfield:audit", [json.dumps({"apply": True})])
    assert resp["ok"], resp
    data = resp["data"]
    assert data["status"] == "ok"
    assert data["applied"] is True
    assert data["report"]["summary"]["total"] >= 1
    assert (tmp_path / ".signalos" / "GOVERNANCE_BASELINE.md").is_file()
    assert (tmp_path / ".signalos" / "profile.json").is_file()
    # Source untouched.
    assert (tmp_path / "src" / "index.js").read_text(encoding="utf-8") == "export const x = 1;\n"
    # The application is on the audit trail.
    trail = (tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl").read_text(encoding="utf-8")
    assert "brownfield.governance-applied" in trail


def test_brownfield_audit_empty_workspace_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = _handle("brownfield:audit")
    assert resp["ok"], resp
    assert resp["data"]["status"] == "ok"
    assert resp["data"]["report"]["summary"]["governed"] is False


def test_brownfield_audit_malformed_payload_is_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = _handle("brownfield:audit", ["{not json"])
    assert not resp["ok"]
    assert "brownfield:audit" in resp["error"]


# ---------------------------------------------------------------------------
# competitor:analyze
# ---------------------------------------------------------------------------

_PAGE_A = """
<html><head>
  <title>Acme — Project management for teams</title>
  <meta name="description" content="Plan, track, and ship work.">
</head><body>
  <h1>Run your team on Acme</h1>
  <h2>Boards</h2><h2>Timelines</h2><h3>Reports</h3>
  <a href="/signup">Start free trial</a>
  <span>$12/mo</span>
</body></html>
"""


def _enable_fake_llm(monkeypatch):
    """LLM 'available' with a deterministic insights response (no SDK/network)."""
    from signalos_lib.product import llm_provider

    monkeypatch.setattr(llm_provider, "is_llm_available", lambda root=None: True)
    monkeypatch.setattr(
        llm_provider,
        "call_llm",
        lambda prompt, provider_name=None, model=None, root=None: types.SimpleNamespace(
            success=True, text="- differentiate on speed"
        ),
    )


def test_competitor_analyze_llm_unavailable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
    calls = []
    monkeypatch.setattr(srv, "_COMPETITOR_FETCH_FN", lambda url, timeout=10.0: calls.append(url))
    resp = _handle("competitor:analyze", [json.dumps({"urls": ["https://acme.test"]})])
    assert resp["ok"], resp
    assert resp["data"] == {"status": "llm-unavailable"}
    assert calls == []  # gated BEFORE any fetch
    assert not (tmp_path / ".signalos" / "product" / "COMPETITORS.json").exists()


def test_competitor_analyze_builds_and_persists_matrix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _enable_fake_llm(monkeypatch)

    def fake_fetch(url, timeout=10.0):
        return _PAGE_A if "acme" in url else None

    monkeypatch.setattr(srv, "_COMPETITOR_FETCH_FN", fake_fetch)
    resp = _handle("competitor:analyze", [json.dumps({
        "urls": ["https://acme.test", "https://down.test"],
    })])
    assert resp["ok"], resp
    data = resp["data"]
    assert data["status"] == "ok"
    # Per-URL failure collected, call still succeeds.
    assert len(data["errors"]) == 1
    assert data["errors"][0]["url"] == "https://down.test"
    assert data["errors"][0]["error"]
    # The matrix comes from competitor.build_matrix's real shape.
    matrix = data["matrix"]
    assert len(matrix["matrix"]) == 1
    row = matrix["matrix"][0]
    assert row["url"] == "https://acme.test"
    assert row["has_pricing"] == "yes"
    assert matrix["llm_authored"] is True
    assert "differentiate" in matrix["insights"]
    # Persisted for the design phase.
    persisted_path = tmp_path / ".signalos" / "product" / "COMPETITORS.json"
    assert persisted_path.is_file()
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted["matrix"] == matrix["matrix"]
    assert persisted["urls"] == ["https://acme.test", "https://down.test"]


def test_competitor_analyze_all_fetches_fail_still_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _enable_fake_llm(monkeypatch)
    monkeypatch.setattr(srv, "_COMPETITOR_FETCH_FN", lambda url, timeout=10.0: None)
    resp = _handle("competitor:analyze", [json.dumps({
        "urls": ["https://a.test", "https://b.test"],
    })])
    assert resp["ok"], resp
    assert resp["data"]["status"] == "ok"
    assert len(resp["data"]["errors"]) == 2
    assert resp["data"]["matrix"]["matrix"] == []


def test_competitor_analyze_fetch_exception_is_collected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _enable_fake_llm(monkeypatch)

    def exploding_fetch(url, timeout=10.0):
        raise RuntimeError("boom")

    monkeypatch.setattr(srv, "_COMPETITOR_FETCH_FN", exploding_fetch)
    resp = _handle("competitor:analyze", [json.dumps({"urls": ["https://a.test"]})])
    assert resp["ok"], resp
    assert resp["data"]["status"] == "ok"
    assert resp["data"]["errors"][0]["error"].startswith("RuntimeError")


def test_competitor_analyze_malformed_urls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _enable_fake_llm(monkeypatch)
    for bad_args in (
        [],                                     # no payload at all
        [json.dumps({})],                       # missing urls
        [json.dumps({"urls": []})],             # empty list
        [json.dumps({"urls": "https://x"})],    # not a list
        [json.dumps({"urls": [123]})],          # non-string entry
        [json.dumps({"urls": ["  "]})],         # blank entry
        ["{not json"],                          # unparseable payload
    ):
        resp = _handle("competitor:analyze", bad_args)
        assert not resp["ok"], bad_args
        assert "competitor:analyze" in resp["error"]


# ---------------------------------------------------------------------------
# agent:deliver brownfield auto-trigger
# ---------------------------------------------------------------------------


class TestBrownfieldAutoTrigger(_AgentIpcBase):
    def _deliver(self, run_id: str):
        srv._AGENT_ADAPTER_FACTORY = _adapter_factory([_end_resp("(gate work done)")])
        return self._run({
            "command": "agent:deliver",
            "id": f"req-{run_id}",
            "args": [_agent_args(prompt="build a task tracker", run_id=run_id)],
        })

    def _seed_code(self) -> None:
        root = Path(os.getcwd())
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "index.js").write_text("export const x = 1;\n", encoding="utf-8")
        (root / "package.json").write_text('{"name":"x"}', encoding="utf-8")

    def _audit_actions(self) -> list[str]:
        trail = Path(os.getcwd()) / ".signalos" / "AUDIT_TRAIL.jsonl"
        if not trail.is_file():
            return []
        return [
            json.loads(line).get("action", "")
            for line in trail.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @staticmethod
    def _brownfield_events(events: list[dict]) -> list[dict]:
        return [
            e for e in events
            if e.get("kind") == "agent-event"
            and e.get("type") == "system"
            and "Existing code detected" in str(e.get("message", ""))
        ]

    def test_fires_on_preexisting_ungoverned_code(self):
        self._seed_code()
        resp, events = self._deliver("run-bf-1")
        self.assertTrue(resp["ok"], msg=resp)
        notices = self._brownfield_events(events)
        self.assertEqual(len(notices), 1, msg=events)
        self.assertIn("governance finding", notices[0]["message"])
        self.assertIn("brownfield", notices[0])
        self.assertIn("brownfield.audit-detected", self._audit_actions())

    def test_silent_on_fresh_workspace(self):
        resp, events = self._deliver("run-bf-2")
        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(self._brownfield_events(events), [])
        self.assertNotIn("brownfield.audit-detected", self._audit_actions())

    def test_silent_on_already_governed_workspace(self):
        self._seed_code()
        signalos = Path(os.getcwd()) / ".signalos"
        signalos.mkdir(parents=True, exist_ok=True)
        (signalos / "profile.json").write_text('{"profile":"existing-repo"}', encoding="utf-8")
        resp, events = self._deliver("run-bf-3")
        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(self._brownfield_events(events), [])
        self.assertNotIn("brownfield.audit-detected", self._audit_actions())

    def test_notifies_only_once(self):
        # The detected-notice itself lands on the audit trail, which is a
        # governance marker: the second deliver stays silent.
        self._seed_code()
        _, events1 = self._deliver("run-bf-4a")
        _, events2 = self._deliver("run-bf-4b")
        self.assertEqual(len(self._brownfield_events(events1)), 1)
        self.assertEqual(self._brownfield_events(events2), [])
        actions = self._audit_actions()
        self.assertEqual(actions.count("brownfield.audit-detected"), 1)

    def test_never_raises_and_never_blocks_delivery(self):
        self._seed_code()
        from signalos_lib.product import brownfield as bf

        original = bf.audit_existing_repo

        def exploding(_root):
            raise RuntimeError("audit exploded")

        bf.audit_existing_repo = exploding
        try:
            resp, events = self._deliver("run-bf-5")
        finally:
            bf.audit_existing_repo = original
        # Delivery proceeded despite the brownfield failure.
        self.assertTrue(resp["ok"], msg=resp)
        self.assertEqual(self._brownfield_events(events), [])
        # Recorded honestly.
        self.assertIn("brownfield.audit-error", self._audit_actions())


if __name__ == "__main__":
    import unittest

    unittest.main()
