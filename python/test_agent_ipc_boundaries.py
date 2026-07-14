"""Adversarial contracts for agent IPC identity and checkpoint binding."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import signalos_ipc_server as srv
from signalos_lib.product.agent_loop import AgentLoop
from signalos_lib.product.enforcement_state import StaticEnforcementProvider
from signalos_lib.product.gate_orchestrator import GateOrchestrator, resume_delivery
from signalos_lib.product.run_ids import agent_run_dir, validate_run_id


class _Adapter:
    supports_tool_calls = True

    def chat(self, **_kwargs):
        return SimpleNamespace(
            content="done",
            tool_calls=None,
            stop_reason="end_turn",
            usage=None,
        )


@pytest.mark.parametrize(
    "run_id",
    [
        "../outside",
        "..\\outside",
        "C:/outside",
        "/absolute",
        "two/segments",
        "two\\segments",
        ".",
        "CON",
        "trailing.",
        "space in id",
        "x" * 129,
    ],
)
def test_run_ids_are_one_canonical_contained_path_segment(
    tmp_path: Path, run_id: str
) -> None:
    with pytest.raises(ValueError):
        validate_run_id(run_id)
    with pytest.raises(ValueError):
        agent_run_dir(tmp_path, run_id)
    with pytest.raises(ValueError):
        AgentLoop(_Adapter(), tmp_path, run_id=run_id)
    with pytest.raises(ValueError):
        GateOrchestrator(tmp_path, _Adapter(), lambda _event: None, run_id=run_id)
    with pytest.raises(ValueError):
        resume_delivery(tmp_path, run_id, _Adapter(), lambda _event: None)


def test_agent_run_storage_refuses_a_signalos_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    try:
        (tmp_path / ".signalos").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(ValueError, match="symlink|outside"):
        agent_run_dir(tmp_path, "safe-looking-run")


def test_agent_run_storage_refuses_same_base_run_alias(tmp_path: Path) -> None:
    base = tmp_path / ".signalos" / "agent-runs"
    real = base / "real-run"
    real.mkdir(parents=True)
    alias = base / "alias-run"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable on this platform")
    with pytest.raises(ValueError, match="symlink|junction"):
        agent_run_dir(tmp_path, "alias-run")


def test_explicit_nonexistent_cwd_is_rejected_without_reusing_prior_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        srv,
        "route",
        lambda *_args, **_kwargs: pytest.fail(
            "an invalid explicit cwd must not dispatch in the prior workspace"
        ),
    )
    missing = tmp_path / "does-not-exist"
    response = srv.handle({
        "id": "missing-cwd", "command": "ping", "cwd": str(missing), "args": [],
    })
    assert response["ok"] is False
    assert "cwd" in response["error"]
    assert Path.cwd() == tmp_path


def _write_minimal_delivery(
    root: Path,
    run_id: str,
    *,
    stored_run_id: str | None = None,
    project_id: str = "default",
) -> Path:
    run_dir = agent_run_dir(root, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    delivery = run_dir / "delivery.json"
    delivery.write_text(
        json.dumps({
            "run_id": stored_run_id if stored_run_id is not None else run_id,
            "project_id": project_id,
            "status": "awaiting-verdict",
        }),
        encoding="utf-8",
    )
    return delivery


def _write_plain_checkpoint(
    root: Path,
    run_id: str,
    *,
    stored_run_id: str | None = None,
    project_id: str = "default",
) -> Path:
    run_dir = agent_run_dir(root, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    state = run_dir / "state.json"
    state.write_text(
        json.dumps({
            "run_id": stored_run_id if stored_run_id is not None else run_id,
            "project_id": project_id,
            "status": "running",
            "tool_calls_made": 0,
        }),
        encoding="utf-8",
    )
    (run_dir / "conversation.jsonl").write_text(
        json.dumps({"role": "system", "content": "continue"}) + "\n",
        encoding="utf-8",
    )
    return state


def test_plain_ipc_run_persists_nondefault_project_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(srv, "_AGENT_ADAPTER_FACTORY", lambda *_args: _Adapter())
    monkeypatch.setattr(
        srv,
        "_AGENT_ENFORCEMENT_FACTORY",
        lambda: StaticEnforcementProvider(trust_tier="T3"),
    )

    response = srv.agent_run(
        "plain-project-run",
        {
            "run_id": "plain-alpha",
            "prompt": "finish",
            "provider": "openai",
            "model": "test",
        },
        project_id="alpha",
    )

    assert response["ok"] is True, response
    state = json.loads(
        (agent_run_dir(tmp_path, "plain-alpha") / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["run_id"] == "plain-alpha"
    assert state["project_id"] == "alpha"


def test_plain_ipc_run_rejects_invalid_project_before_provider_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    constructed = 0

    def provider_must_not_start(*_args, **_kwargs):
        nonlocal constructed
        constructed += 1
        raise AssertionError("provider constructed before project validation")

    monkeypatch.setattr(srv, "_AGENT_ADAPTER_FACTORY", provider_must_not_start)
    response = srv.agent_run(
        "plain-invalid-project",
        {
            "run_id": "plain-invalid-project",
            "prompt": "finish",
            "provider": "openai",
            "model": "test",
        },
        project_id="../other-project",
    )

    assert response["ok"] is False
    assert "project_id invalid" in response["error"]
    assert constructed == 0


@pytest.mark.parametrize(
    ("stored_run_id", "stored_project", "requested_project", "message"),
    [
        ("different-run", "alpha", "alpha", "run_id does not match"),
        ("plain-bound", "alpha", "beta", "belongs to project"),
    ],
)
def test_plain_resume_validates_run_and_project_before_provider_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stored_run_id: str,
    stored_project: str,
    requested_project: str,
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_plain_checkpoint(
        tmp_path,
        "plain-bound",
        stored_run_id=stored_run_id,
        project_id=stored_project,
    )
    constructed = 0

    def provider_must_not_start(*_args, **_kwargs):
        nonlocal constructed
        constructed += 1
        raise AssertionError("provider constructed before plain checkpoint validation")

    monkeypatch.setattr(srv, "_AGENT_ADAPTER_FACTORY", provider_must_not_start)
    response = srv.agent_resume(
        "plain-resume-boundary",
        {"run_id": "plain-bound", "provider": "openai", "model": "test"},
        project_id=requested_project,
    )

    assert response["ok"] is False
    assert message in response["error"]
    assert constructed == 0


def test_plain_resume_refuses_state_symlink_before_provider_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = agent_run_dir(tmp_path, "linked-plain-state")
    run_dir.mkdir(parents=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-state.json"
    outside.write_text(json.dumps({
        "run_id": "linked-plain-state",
        "project_id": "default",
        "status": "running",
    }), encoding="utf-8")
    state = run_dir / "state.json"
    try:
        state.symlink_to(outside)
    except (OSError, NotImplementedError):
        outside.unlink(missing_ok=True)
        pytest.skip("file symlinks are unavailable on this platform")
    constructed = 0

    def provider_must_not_start(*_args, **_kwargs):
        nonlocal constructed
        constructed += 1
        raise AssertionError("provider constructed through redirected plain state")

    monkeypatch.setattr(srv, "_AGENT_ADAPTER_FACTORY", provider_must_not_start)
    try:
        response = srv.agent_resume(
            "linked-plain-resume",
            {
                "run_id": "linked-plain-state",
                "provider": "openai",
                "model": "test",
            },
        )
    finally:
        state.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)

    assert response["ok"] is False
    assert "symlink" in response["error"] or "junction" in response["error"]
    assert constructed == 0


def test_plain_resume_refuses_conversation_symlink_before_provider_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_plain_checkpoint(tmp_path, "linked-plain-conversation")
    run_dir = agent_run_dir(tmp_path, "linked-plain-conversation")
    conversation = run_dir / "conversation.jsonl"
    conversation.unlink()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-conversation.jsonl"
    outside.write_text(
        json.dumps({"role": "system", "content": "redirected"}) + "\n",
        encoding="utf-8",
    )
    try:
        conversation.symlink_to(outside)
    except (OSError, NotImplementedError):
        outside.unlink(missing_ok=True)
        pytest.skip("file symlinks are unavailable on this platform")
    constructed = 0

    def provider_must_not_start(*_args, **_kwargs):
        nonlocal constructed
        constructed += 1
        raise AssertionError("provider constructed through redirected transcript")

    monkeypatch.setattr(srv, "_AGENT_ADAPTER_FACTORY", provider_must_not_start)
    try:
        response = srv.agent_resume(
            "linked-plain-conversation-resume",
            {
                "run_id": "linked-plain-conversation",
                "provider": "openai",
                "model": "test",
            },
        )
    finally:
        conversation.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)

    assert response["ok"] is False
    assert "symlink" in response["error"] or "junction" in response["error"]
    assert constructed == 0


@pytest.mark.parametrize(
    ("stored_run_id", "stored_project", "requested_project", "message"),
    [
        ("different-run", "default", "default", "run_id does not match"),
        ("bound-run", "alpha", "default", "belongs to project"),
    ],
)
def test_resume_validates_persisted_run_and_project_before_provider_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stored_run_id: str,
    stored_project: str,
    requested_project: str,
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_minimal_delivery(
        tmp_path,
        "bound-run",
        stored_run_id=stored_run_id,
        project_id=stored_project,
    )
    constructed = 0

    def provider_must_not_start(*_args, **_kwargs):
        nonlocal constructed
        constructed += 1
        raise AssertionError("provider constructed before delivery identity validation")

    monkeypatch.setattr(srv, "_AGENT_ADAPTER_FACTORY", provider_must_not_start)
    response = srv.agent_resume(
        "resume-boundary",
        {"run_id": "bound-run", "provider": "openai", "model": "test"},
        project_id=requested_project,
    )

    assert response["ok"] is False
    assert message in response["error"]
    assert constructed == 0


def test_resume_refuses_a_delivery_json_symlink_before_provider_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = agent_run_dir(tmp_path, "linked-delivery")
    run_dir.mkdir(parents=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-delivery.json"
    outside.write_text(json.dumps({
        "run_id": "linked-delivery",
        "project_id": "default",
        "status": "awaiting-verdict",
    }), encoding="utf-8")
    delivery = run_dir / "delivery.json"
    try:
        delivery.symlink_to(outside)
    except (OSError, NotImplementedError):
        outside.unlink(missing_ok=True)
        pytest.skip("file symlinks are unavailable on this platform")

    constructed = 0

    def provider_must_not_start(*_args, **_kwargs):
        nonlocal constructed
        constructed += 1
        raise AssertionError("provider constructed through redirected delivery state")

    monkeypatch.setattr(srv, "_AGENT_ADAPTER_FACTORY", provider_must_not_start)
    try:
        response = srv.agent_resume(
            "linked-resume",
            {"run_id": "linked-delivery", "provider": "openai", "model": "test"},
        )
    finally:
        delivery.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)

    assert response["ok"] is False
    assert "symlink" in response["error"] or "junction" in response["error"]
    assert constructed == 0


def test_direct_resume_refuses_a_mismatched_persisted_run_id(tmp_path: Path) -> None:
    _write_minimal_delivery(
        tmp_path, "direct-bound-run", stored_run_id="different-run",
    )

    with pytest.raises(ValueError, match="run_id does not match"):
        resume_delivery(
            tmp_path, "direct-bound-run", _Adapter(), lambda _event: None,
        )


def test_direct_resume_refuses_a_delivery_json_symlink(tmp_path: Path) -> None:
    run_dir = agent_run_dir(tmp_path, "direct-linked-delivery")
    run_dir.mkdir(parents=True)
    outside = tmp_path.parent / f"{tmp_path.name}-direct-delivery.json"
    outside.write_text(json.dumps({
        "run_id": "direct-linked-delivery",
        "project_id": "default",
        "status": "awaiting-verdict",
    }), encoding="utf-8")
    delivery = run_dir / "delivery.json"
    try:
        delivery.symlink_to(outside)
    except (OSError, NotImplementedError):
        outside.unlink(missing_ok=True)
        pytest.skip("file symlinks are unavailable on this platform")

    try:
        with pytest.raises(ValueError, match="symlink|junction"):
            resume_delivery(
                tmp_path, "direct-linked-delivery", _Adapter(),
                lambda _event: None,
            )
    finally:
        delivery.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)


def test_cancel_refuses_a_redirected_marker_without_touching_its_target(
    tmp_path: Path,
) -> None:
    _write_minimal_delivery(tmp_path, "linked-cancel")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-cancel.json"
    sentinel = "outside authority must remain unchanged\n"
    outside.write_text(sentinel, encoding="utf-8")
    marker = agent_run_dir(tmp_path, "linked-cancel") / "cancel-requested.json"
    try:
        marker.symlink_to(outside)
    except (OSError, NotImplementedError):
        outside.unlink(missing_ok=True)
        pytest.skip("file symlinks are unavailable on this platform")

    try:
        response = srv.agent_cancel(
            "linked-cancel-request",
            {"run_id": "linked-cancel"},
            repo_root=tmp_path,
        )
        assert response["ok"] is False
        assert "symlink" in response["error"] or "junction" in response["error"]
        assert outside.read_text(encoding="utf-8") == sentinel
        assert "linked-cancel" not in srv._AGENT_CANCEL_FLAGS
    finally:
        marker.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)


def test_plain_cancel_refuses_cross_project_checkpoint(
    tmp_path: Path,
) -> None:
    _write_plain_checkpoint(
        tmp_path, "plain-alpha-cancel", project_id="alpha"
    )

    response = srv.agent_cancel(
        "cross-project-plain-cancel",
        {"run_id": "plain-alpha-cancel"},
        project_id="beta",
        repo_root=tmp_path,
    )

    assert response["ok"] is False
    assert "belongs to project" in response["error"]
    assert not (
        agent_run_dir(tmp_path, "plain-alpha-cancel")
        / "cancel-requested.json"
    ).exists()


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("agent:deliver", {"prompt": "build", "provider": "openai", "model": "test"}),
        ("agent:run", {"prompt": "inspect", "provider": "openai", "model": "test"}),
        ("agent:verdict", {"verdict": "approve", "gate_id": "G0"}),
        ("agent:cancel", {}),
        ("agent:resume", {"provider": "openai", "model": "test"}),
        ("agent:reopen-gate", {"gate": "G0", "reason": "revisit"}),
    ],
)
def test_every_agent_ipc_rejects_traversal_run_id_before_filesystem_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    extra: dict,
) -> None:
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "signalos-run-id-escape"
    response = srv.handle(
        {
            "id": f"boundary-{command}",
            "command": command,
            "args": [json.dumps({"run_id": "../../signalos-run-id-escape", **extra})],
        }
    )

    assert response["ok"] is False, response
    assert "run_id" in response["error"]
    assert not outside.exists()


class _ActiveDelivery:
    def __init__(self, root: Path | None = None, project_id: str = "default") -> None:
        self.repo_root = (root or Path.cwd()).resolve()
        self.project_id = project_id
        self.state = SimpleNamespace(current_gate="G5", status="awaiting-verdict")
        self.apply_verdict = mock.Mock(return_value={"status": "complete", "ready": True})


def test_active_delivery_verdict_is_bound_to_the_rendered_gate() -> None:
    orch = _ActiveDelivery()
    srv._ACTIVE_DELIVERIES["bound-run"] = orch
    try:
        stale = srv.agent_verdict(
            "stale",
            {"run_id": "bound-run", "gate_id": "G4", "verdict": "approve"},
        )
        missing = srv.agent_verdict(
            "missing",
            {"run_id": "bound-run", "verdict": "approve"},
        )
        accepted = srv.agent_verdict(
            "current",
            {"run_id": "bound-run", "gate_id": "G5", "verdict": "approve"},
        )
    finally:
        srv._ACTIVE_DELIVERIES.clear()

    assert stale["ok"] is False
    assert "stale gate verdict" in stale["error"]
    assert missing["ok"] is False
    assert "requires 'gate_id'" in missing["error"]
    assert accepted["ok"] is True
    orch.apply_verdict.assert_called_once_with("approve", "")


def test_active_run_lookup_cannot_cross_workspace_or_project(tmp_path: Path) -> None:
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    orch = _ActiveDelivery(workspace_b, project_id="beta")
    srv._ACTIVE_DELIVERIES["shared-run"] = orch
    old = Path.cwd()
    try:
        __import__("os").chdir(workspace_a)
        response = srv.agent_verdict(
            "cross-workspace",
            {"run_id": "shared-run", "gate_id": "G5", "verdict": "approve"},
            project_id="alpha",
        )
    finally:
        __import__("os").chdir(old)
        srv._ACTIVE_DELIVERIES.clear()

    assert response["ok"] is False
    assert "different workspace or project" in response["error"]
    orch.apply_verdict.assert_not_called()


def test_fresh_delivery_cannot_reuse_persisted_or_active_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    persisted = agent_run_dir(tmp_path, "duplicate-run")
    persisted.mkdir(parents=True)
    payload = {
        "prompt": "build",
        "run_id": "duplicate-run",
        "provider": "openai",
        "model": "test",
    }
    response = srv.agent_deliver("persisted", payload)
    assert response["ok"] is False
    assert "persisted state" in response["error"]

    srv._ACTIVE_DELIVERIES["active-run"] = _ActiveDelivery(tmp_path)
    try:
        response = srv.agent_deliver(
            "active", {**payload, "run_id": "active-run"}
        )
    finally:
        srv._ACTIVE_DELIVERIES.clear()
    assert response["ok"] is False
    assert "already active" in response["error"]


def test_cancel_refuses_unknown_run_without_creating_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    response = srv.agent_cancel("unknown", {"run_id": "never-existed"})
    assert response["ok"] is False
    assert "no active or persisted" in response["error"]
    assert not (tmp_path / ".signalos" / "agent-runs" / "never-existed").exists()


def test_inflight_cancel_keeps_workspace_lock_until_worker_terminalizes(
    tmp_path: Path,
) -> None:
    first = GateOrchestrator(
        tmp_path, _Adapter(), lambda _event: None,
        sign_fn=lambda *_args, **_kwargs: ["x"], run_id="worker-one",
        prompt="one", cancel_check=lambda: srv._agent_cancel_requested(
            tmp_path, "worker-one"
        ),
    )
    first.state.status = "active"
    first._persist()
    assert first._acquire_delivery_lock() is None
    srv._ACTIVE_DELIVERIES["worker-one"] = first
    srv._ACTIVE_DELIVERY_WORKERS.add("worker-one")
    try:
        cancelled = srv.agent_cancel(
            "cancel", {"run_id": "worker-one"}, repo_root=tmp_path,
        )
        assert cancelled["ok"] is True
        assert first.state.status == "active"
        assert first._delivery_lock_path().is_file()

        contender = GateOrchestrator(
            tmp_path, _Adapter(), lambda _event: None,
            sign_fn=lambda *_args, **_kwargs: ["x"], run_id="worker-two",
            prompt="two",
        )
        blocked = contender._acquire_delivery_lock()
        assert blocked is not None
        assert blocked["active_run_id"] == "worker-one"

        # The worker reaches its next safe boundary and exclusively owns the
        # terminal state/lock transition.
        terminal = first._cancel_at_boundary("blocked tool return")
        assert terminal and terminal["status"] == "cancelled"
        assert not first._delivery_lock_path().exists()
        assert contender._acquire_delivery_lock() is None
        contender._release_delivery_lock()
    finally:
        srv._ACTIVE_DELIVERY_WORKERS.discard("worker-one")
        srv._ACTIVE_DELIVERIES.pop("worker-one", None)
        srv._AGENT_CANCEL_FLAGS.pop("worker-one", None)
        srv._clear_agent_cancel_marker(tmp_path, "worker-one")


def test_persisted_cancel_terminalizes_before_provider_or_release_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    orch = GateOrchestrator(
        tmp_path, _Adapter(), lambda _event: None,
        sign_fn=lambda *_args, **_kwargs: ["x"], run_id="idle-cancel",
        prompt="cancel me",
    )
    orch.state.status = "awaiting-verdict"
    orch.state.last_outcome = {"gate": "G0", "ok": True}
    orch._persist()
    requested = srv.agent_cancel("request", {"run_id": "idle-cancel"})
    assert requested["ok"] is True

    def provider_must_not_start(*_args, **_kwargs):
        raise AssertionError("provider constructed before persisted cancellation")

    monkeypatch.setattr(srv, "_AGENT_ADAPTER_FACTORY", provider_must_not_start)
    resumed = srv.agent_resume(
        "resume",
        {"run_id": "idle-cancel", "provider": "openai", "model": "test"},
    )
    assert resumed["ok"] is True, resumed
    assert resumed["data"]["status"] == "cancelled"
    stored = json.loads(
        (agent_run_dir(tmp_path, "idle-cancel") / "delivery.json").read_text()
    )
    assert stored["status"] == "cancelled"


def test_request_project_id_is_a_canonical_path_segment(tmp_path: Path) -> None:
    response = srv.handle(
        {
            "id": "bad-project",
            "command": "state:gates",
            "cwd": str(tmp_path),
            "project_id": "../../outside",
            "args": [],
        }
    )
    assert response["ok"] is False
    assert "project_id invalid" in response["error"]


def test_launch_surface_uses_production_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(srv, "_AGENT_ADAPTER_FACTORY", lambda *_args: _Adapter())
    captured: dict[str, str] = {}

    def fake_start(_root, factory, prompt=None):
        child = tmp_path / ".signalos" / "launch-child"
        child.mkdir(parents=True)
        orch = factory(child, prompt or "launch", "launch-deadbeef")
        captured["profile"] = orch.profile
        return {"gate_result": {"status": "blocked", "run_id": orch.state.run_id}}

    monkeypatch.setattr("signalos_lib.product.launch.start_launch_build", fake_start)
    try:
        response = srv.agent_launch(
            "launch",
            {"provider": "openai", "model": "test", "prompt": "launch it"},
        )
    finally:
        srv._AGENT_ADAPTER_FACTORY = None
        srv._ACTIVE_DELIVERIES.clear()

    assert response["ok"] is True, response
    assert captured["profile"] == "production"
