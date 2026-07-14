"""Fail-closed delivery profile and restart/resume regression tests.

No provider or network is used.  The IPC tests run in a temporary workspace
through the same persisted delivery.json boundary used after a sidecar restart.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import signalos_ipc_server as srv
from signalos_lib.harness import AgentResponse, TokenUsage
from signalos_lib.product.enforcement_state import StaticEnforcementProvider
from signalos_lib.product.gate_orchestrator import GateOrchestrator, resume_delivery


class _EndAdapter:
    supports_tool_calls = True

    def chat(self, messages, model="test", tools=None, stream=False):
        return AgentResponse(
            content="(no tool work)",
            tool_calls=None,
            stop_reason="end_turn",
            usage=TokenUsage(),
        )


def _payload(command: str, **values):
    request_project_id = values.pop("_request_project_id", None)
    payload = {
        "id": f"test-{command}",
        "command": command,
        "args": [json.dumps(values)],
    }
    if request_project_id is not None:
        payload["project_id"] = request_project_id
    return payload


@pytest.fixture
def ipc_seams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(srv, "_AGENT_ADAPTER_FACTORY", lambda model: _EndAdapter())
    monkeypatch.setattr(
        srv,
        "_AGENT_ENFORCEMENT_FACTORY",
        lambda: StaticEnforcementProvider(trust_tier="T3"),
    )
    signed: list[str] = []
    monkeypatch.setattr(
        srv,
        "_DELIVERY_SIGN_FN",
        lambda root, gate, signer, role, verdict, conditions: signed.append(gate)
        or [f"{gate}.md"],
    )
    srv._ACTIVE_DELIVERIES.clear()
    yield tmp_path, signed
    srv._ACTIVE_DELIVERIES.clear()


def _persisted_orchestrator(
    root: Path,
    *,
    run_id: str,
    profile: str = "production",
    signer: str = "Founder <founder@example.test>",
    project_id: str = "default",
) -> GateOrchestrator:
    return GateOrchestrator(
        root,
        _EndAdapter(),
        lambda event: None,
        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
        sign_fn=lambda *args, **kwargs: ["signed.md"],
        prompt="build a governed product",
        run_id=run_id,
        profile=profile,
        signer=signer,
        project_id=project_id,
    )


def test_unknown_fresh_profile_is_rejected_in_engine_and_ipc(ipc_seams) -> None:
    root, _signed = ipc_seams
    with pytest.raises(ValueError, match="unknown orchestrator profile"):
        _persisted_orchestrator(root, run_id="bad-engine", profile="prodution")

    for index, invalid in enumerate(("prodution", "", None, False)):
        run_id = f"bad-ipc-{index}"
        response = srv.handle(
            _payload(
                "agent:deliver",
                prompt="build",
                run_id=run_id,
                provider="openai",
                model="gpt-test",
                profile=invalid,
            )
        )
        assert response["ok"] is False
        assert "profile invalid" in response["error"]
        assert not (
            root / ".signalos" / "agent-runs" / run_id / "delivery.json"
        ).exists()


def test_desktop_fresh_delivery_persists_explicit_production_default(ipc_seams) -> None:
    root, _signed = ipc_seams
    response = srv.handle(
        _payload(
            "agent:deliver",
            prompt="build a tracker",
            run_id="desktop-production",
            provider="openai",
            model="gpt-test",
        )
    )
    assert response["ok"] is True
    state = json.loads(
        (root / ".signalos" / "agent-runs" / "desktop-production" / "delivery.json")
        .read_text(encoding="utf-8")
    )
    assert state["profile"] == "production"
    assert state["signer"]


def test_resume_preserves_blocked_state_and_cannot_sign(ipc_seams) -> None:
    root, signed = ipc_seams
    orch = _persisted_orchestrator(root, run_id="blocked-run")
    orch.state.status = "blocked"
    orch.state.last_outcome = {
        "gate": "G0",
        "ok": False,
        "reason": "agent wrote no reviewable artifacts",
        "loop_status": "stalled_no_tool",
    }
    orch._persist()

    resumed = srv.handle(
        _payload(
            "agent:resume",
            run_id="blocked-run",
            provider="openai",
            model="gpt-test",
        )
    )
    assert resumed["ok"] is True
    assert resumed["data"]["status"] == "blocked"
    assert srv._ACTIVE_DELIVERIES["blocked-run"].state.status == "blocked"
    persisted = json.loads(
        (root / ".signalos" / "agent-runs" / "blocked-run" / "delivery.json")
        .read_text(encoding="utf-8")
    )
    assert persisted["status"] == "blocked"

    verdict = srv.handle(
        _payload("agent:verdict", run_id="blocked-run", gate_id="G0", verdict="approve")
    )
    assert verdict["ok"] is True
    assert verdict["data"]["status"] == "not-reviewable"
    assert signed == []


def test_production_resume_preserves_identity_namespace_and_release_evidence(
    ipc_seams,
) -> None:
    root, signed = ipc_seams
    original_signer = "Founder <founder@example.test>"
    orch = _persisted_orchestrator(
        root,
        run_id="production-resume",
        signer=original_signer,
        project_id="tenant-alpha",
    )
    orch.state.current_gate = "G5"
    orch.state.status = "awaiting-verdict"
    orch.state.signed = ["G0", "G1", "G2", "G3", "G4"]
    orch.state.release_evidence = {
        "g4_verify": {"ok": True, "profile": "react-vite"},
        "security_gate": {"status": "passed"},
        "runtime_proof": {
            "status": "passed",
            "stack": "generic",
            "ux_required": False,
            "ux_status": "skipped",
            "ux_executed": False,
            "ux_schema_version": "signalos.ux-browser-proof.v1",
            "ok": True,
        },
    }
    orch._persist()

    response = srv.handle(
        _payload(
            "agent:resume",
            run_id="production-resume",
            provider="openai",
            model="gpt-test",
            _request_project_id="tenant-alpha",
        )
    )
    assert response["ok"] is True
    assert response["data"]["profile"] == "production"
    assert response["data"]["project_id"] == "tenant-alpha"
    loaded = srv._ACTIVE_DELIVERIES["production-resume"]
    assert loaded.profile == "production"
    assert loaded.state.profile == "production"
    assert loaded.signer == original_signer
    assert loaded.state.signer == original_signer
    assert loaded.project_id == "tenant-alpha"
    assert loaded._g4_verify == {"ok": True, "profile": "react-vite"}
    assert loaded._last_runtime_ok is True

    loaded._repo_has_real_product_src = lambda: True
    # Current-tree attribution is covered by the dedicated G4 provenance
    # suite.  This test isolates restart restoration of the already-recorded
    # production profile/runtime decision inputs.
    loaded._g4_verification_for_current_tree = lambda: {"ok": True}
    verdict = srv.handle(
        _payload(
            "agent:verdict",
            run_id="production-resume",
            gate_id="G5",
            verdict="approve",
            _request_project_id="tenant-alpha",
        )
    )
    assert verdict["ok"] is True
    assert verdict["data"]["status"] == "complete"
    assert verdict["data"]["ready"] is True
    assert signed == ["G5"]
    final = json.loads(
        (root / ".signalos" / "agent-runs" / "production-resume" / "delivery.json")
        .read_text(encoding="utf-8")
    )
    assert final["profile"] == "production"
    assert final["signer"] == original_signer
    assert final["release_evidence"]["release_verification"]["ok"] is True


def test_production_proof_is_checkpointed_before_a_signing_failure(
    tmp_path: Path,
) -> None:
    def fail_sign(*args, **kwargs):
        raise RuntimeError("signer unavailable")

    orch = GateOrchestrator(
        tmp_path,
        _EndAdapter(),
        lambda event: None,
        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
        sign_fn=fail_sign,
        prompt="build a governed product",
        run_id="proof-before-sign",
        profile="production",
    )
    orch.state.current_gate = "G4"
    orch.state.status = "awaiting-verdict"
    orch._g4_verify = {"ok": True}

    def record_proof():
        orch.state.release_evidence["security_gate"] = {
            "status": "passed",
            "issue_count": 0,
        }
        orch.state.release_evidence["runtime_proof"] = {
            "status": "passed",
            "stack": "generic",
            "ux_required": False,
            "ux_status": "skipped",
            "ux_executed": False,
            "ux_schema_version": "signalos.ux-browser-proof.v1",
            "ok": True,
        }
        return None

    orch._run_post_build_stages = record_proof
    result = orch.apply_verdict("approve")

    assert result["status"] == "sign-failed"
    state = json.loads(
        (tmp_path / ".signalos" / "agent-runs" / "proof-before-sign" / "delivery.json")
        .read_text(encoding="utf-8")
    )
    assert state["profile"] == "production"
    assert state["release_evidence"]["security_gate"]["status"] == "passed"
    assert state["release_evidence"]["runtime_proof"]["ok"] is True


def test_resume_rejects_profile_downgrade_and_unknown_persisted_profile(
    tmp_path: Path,
) -> None:
    orch = _persisted_orchestrator(tmp_path, run_id="profile-contract")
    orch.state.status = "awaiting-verdict"
    orch._persist()

    with pytest.raises(ValueError, match="resume profile mismatch"):
        resume_delivery(
            tmp_path,
            "profile-contract",
            _EndAdapter(),
            lambda event: None,
            profile="benchmark",
        )

    state_path = (
        tmp_path / ".signalos" / "agent-runs" / "profile-contract" / "delivery.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for invalid in ("not-a-profile", None):
        state["profile"] = invalid
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with pytest.raises(ValueError, match="unknown orchestrator profile"):
            resume_delivery(
                tmp_path,
                "profile-contract",
                _EndAdapter(),
                lambda event: None,
            )


def test_ipc_resume_rejects_invalid_or_mismatched_expected_profile(
    ipc_seams,
) -> None:
    root, _signed = ipc_seams
    orch = _persisted_orchestrator(root, run_id="resume-profile-input")
    orch.state.status = "awaiting-verdict"
    orch._persist()

    for invalid in ("benchmark", "prodution", "", None, False):
        response = srv.handle(
            _payload(
                "agent:resume",
                run_id="resume-profile-input",
                provider="openai",
                model="gpt-test",
                profile=invalid,
            )
        )
        assert response["ok"] is False
        assert (
            "profile invalid" in response["error"]
            or "profile mismatch" in response["error"]
        )
        assert "resume-profile-input" not in srv._ACTIVE_DELIVERIES


def test_legacy_resume_migrates_profile_and_signer_durably(tmp_path: Path) -> None:
    orch = _persisted_orchestrator(tmp_path, run_id="legacy-state")
    orch.state.status = "awaiting-verdict"
    orch._persist()
    state_path = tmp_path / ".signalos" / "agent-runs" / "legacy-state" / "delivery.json"
    legacy = json.loads(state_path.read_text(encoding="utf-8"))
    legacy.pop("profile")
    legacy.pop("signer")
    state_path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = resume_delivery(
        tmp_path,
        "legacy-state",
        _EndAdapter(),
        lambda event: None,
        signer="Current Founder",
        legacy_profile="production",
    )
    assert loaded.profile == "production"
    assert loaded.signer == "Current Founder"
    migrated = json.loads(state_path.read_text(encoding="utf-8"))
    assert migrated["profile"] == "production"
    assert migrated["signer"] == "Current Founder"


def test_active_checkpoint_is_rerun_never_promoted_directly(ipc_seams) -> None:
    root, _signed = ipc_seams
    orch = _persisted_orchestrator(root, run_id="interrupted-active")
    orch.state.status = "active"
    orch._persist()

    response = srv.handle(
        _payload(
            "agent:resume",
            run_id="interrupted-active",
            provider="openai",
            model="gpt-test",
        )
    )
    assert response["ok"] is True
    assert response["data"]["reran_active_gate"] is True
    assert response["data"]["status"] != "awaiting-verdict"
    assert srv._ACTIVE_DELIVERIES["interrupted-active"].state.status == "blocked"
