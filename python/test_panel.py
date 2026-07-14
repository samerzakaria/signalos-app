"""Offline contract tests for the universal consult-panel council and IPC."""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import panel  # noqa: E402
import signalos_ipc_server as srv  # noqa: E402


def _candidate_ids(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'"candidate_id":"(A\d+)"', text)))


class ScriptedPanel:
    """Deterministic model transport; any real network access is impossible."""

    def __init__(
        self,
        *,
        failures: set[tuple[str, str]] | None = None,
        invalid_ballots: set[str] | None = None,
        audit_status: str = "pass",
        follow_up_audit_status: str = "pass",
        decision_state: str = "provisional_majority",
        cost: float | None = 0.01,
    ) -> None:
        self.failures = failures or set()
        self.invalid_ballots = invalid_ballots or set()
        self.audit_status = audit_status
        self.follow_up_audit_status = follow_up_audit_status
        self.decision_state = decision_state
        self.cost = cost
        self.calls: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def __call__(self, key: str, model: str, system: str, user: str):
        match = re.search(r"\[CONSULT_PANEL_STAGE:([^\]]+)\]", system)
        assert match, f"missing stage marker in {system!r}"
        stage = match.group(1)
        with self._lock:
            self.calls.append({"key": key, "model": model, "stage": stage, "user": user})
        if (stage, model) in self.failures:
            raise RuntimeError(f"simulated failure containing credential {key}")

        ids = _candidate_ids(user) or ["A01", "A02", "A03"]
        if stage in {"advice", "revision"}:
            value: Any = {
                "position": "Proceed with safeguards",
                "recommendation": "Proceed after the measurable exit criteria pass.",
                "assumptions": ["The supplied evidence is current"],
                "risks": ["Rollback may be incomplete"],
                "alternatives": ["Run a bounded pilot"],
                "uncertainties": ["Production load is unknown"],
                "confidence": 0.74,
            }
        elif stage == "critique":
            value = {
                "critiques": [
                    {
                        "candidate_id": candidate_id,
                        "fatal_errors": [],
                        "major_concerns": ["Validate rollback"],
                        "minor_concerns": [],
                        "strongest_point": "Uses measurable criteria",
                        "verification_needed": ["Exercise rollback"],
                        "verdict": "revise",
                    }
                    for candidate_id in ids
                ],
                "claim_conflicts": [],
                "verification_needed": ["Run the stated checks"],
            }
        elif stage == "dissent":
            value = {
                "thesis": "The apparent readiness may hide a shared test blind spot.",
                "counter_recommendation": "Delay until an independent rollback exercise succeeds.",
                "severity": "material",
                "evidence": ["All candidates depend on the same supplied evidence"],
                "failure_modes": ["Shared evidence can create correlated error"],
                "conditions_that_make_it_right": ["Rollback evidence is stale"],
            }
        elif stage == "jury":
            if model in self.invalid_ballots:
                return ("not-json", self.cost)
            value = {
                "scores": [
                    {
                        "candidate_id": candidate_id,
                        "correctness": 9 if candidate_id == "A01" else 8,
                        "evidence": 8,
                        "feasibility": 8,
                        "risk_governance": 8,
                        "completeness": 8,
                    }
                    for candidate_id in ids
                ],
                "ranking": sorted(ids),
                "preferred_candidate_id": "A01",
                "abstain": False,
                "abstain_reason": "",
                "vetoes": [],
                "vetoed_candidate_ids": [],
                "unresolved_risks": ["Production evidence remains bounded"],
                "confidence": 0.8,
            }
        elif stage in {"chair", "chair_revision"}:
            value = {
                "decision_state": self.decision_state,
                "selected_candidate_id": "A01",
                "recommendation": "Run the bounded pilot, then ship only if rollback passes.",
                "rationale": "The jury favored A01, but the red-team objection remains material.",
                "consensus": ["A rollback check is required"],
                "disagreements": ["Whether current evidence is sufficient"],
                "dissent_summary": "A shared evidence blind spot could invalidate the majority.",
                "response_to_dissent": "Require a separately executed rollback exercise.",
                "dissent_disposition": "mitigated_with_evidence",
                "dissent_evidence": ["The rollback exercise is an independent release gate"],
                "override_reason": None,
                "conditions_to_reconsider": ["Rollback or load evidence fails"],
                "next_actions": ["Run the pilot", "Capture rollback evidence"],
                "confidence": 0.78,
            }
        elif stage in {"audit", "audit_revision"}:
            status = self.audit_status if stage == "audit" else self.follow_up_audit_status
            value = {
                "status": status,
                "issues": [] if status == "pass" else ["Clarify the dissent response"],
                "omissions": [],
                "overstatements": [],
                "required_changes": [] if status == "pass" else ["Retain the rollback condition"],
            }
        elif stage == "dissent_reconciliation":
            objection_ids = list(dict.fromkeys(re.findall(r'"objection_id":"(D\d+)"', user)))
            value = {
                "status": "resolved",
                "rationale": "The independent rollback gate directly addresses the shared-evidence risk.",
                "addressed_objection_ids": objection_ids,
                "evidence_references": [
                    {
                        "objection_id": objection_id,
                        "references": ["Final decision: independent rollback release gate"],
                    }
                    for objection_id in objection_ids
                ],
                "unresolved_objection_ids": [],
                "evidence_gaps": [],
            }
        else:  # pragma: no cover - catches protocol drift loudly
            raise AssertionError(f"unexpected stage {stage}")
        return json.dumps(value), self.cost


def _run(script: ScriptedPanel, question: str = "Should we ship?") -> dict[str, Any]:
    return panel.consult(
        question,
        key="sk-unit-test-secret",
        _ask=script,
        _usage=lambda _key: None,
    )


def test_default_council_runs_complete_bounded_protocol():
    script = ScriptedPanel()
    result = _run(script)

    assert result["status"] == "complete"
    assert result["protocol_version"] == "council/1.2"
    assert result["decision_state"] == "provisional_majority"
    assert result["decision"]["recommendation"].startswith("Run the bounded pilot")
    assert result["dissent"]["status"] == "available"
    assert result["dissent"]["counter_recommendation"].startswith("Delay")
    assert result["cost_usd"] == pytest.approx(0.14)
    assert result["cost"]["source"] == "per_response_usage"

    stages = [call["stage"] for call in script.calls]
    assert stages.count("advice") == 3
    assert stages.count("critique") == 1
    assert stages.count("revision") == 3
    assert stages.count("dissent") == 1
    assert stages.count("jury") == 3
    assert stages.count("chair") == 1
    assert stages.count("audit") == 1
    assert stages.count("dissent_reconciliation") == 1
    assert len(stages) == 14

    assert "qwen/qwen3.7-max" in result["models"]
    assert result["roles"]["chair"]["model"] == "openai/gpt-5.6-sol-pro"
    assert result["roles"]["verifier"]["model"] == "anthropic/claude-fable-5"
    assert result["roles"]["red_team"]["model"] == "x-ai/grok-4.5"


def test_advice_is_sealed_and_jury_packet_is_anonymous():
    question = "Is the supplied architecture justified?"
    script = ScriptedPanel()
    _run(script, question)

    advice_calls = [call for call in script.calls if call["stage"] == "advice"]
    assert len(advice_calls) == 3
    assert all(call["user"] == question for call in advice_calls)

    hidden_ids = {
        "anthropic/claude-sonnet-5",
        "deepseek/deepseek-v4-pro",
        "qwen/qwen3.7-max",
        "openai/gpt-5.6-sol-pro",
        "anthropic/claude-fable-5",
        "x-ai/grok-4.5",
        "google/gemini-3.1-pro-preview",
        "z-ai/glm-5.2",
        "xiaomi/mimo-v2.5-pro",
    }
    for call in script.calls:
        if call["stage"] in {"critique", "dissent", "jury", "chair", "audit"}:
            assert all(model_id not in call["user"] for model_id in hidden_ids)


def test_audit_revision_is_bounded_to_one_extra_chair_call():
    script = ScriptedPanel(audit_status="revise")
    result = _run(script)
    stages = [call["stage"] for call in script.calls]
    assert stages.count("audit") == 1
    assert stages.count("chair_revision") == 1
    assert stages.count("audit_revision") == 1
    assert stages.count("dissent_reconciliation") == 1
    assert result["status"] == "complete"
    assert result["cost_usd"] == pytest.approx(0.16)


def test_unresolved_audit_revision_cannot_report_complete():
    chair = panel.DEFAULT_CHAIR.model
    script = ScriptedPanel(
        audit_status="revise",
        failures={("chair_revision", chair)},
    )
    result = _run(script)
    assert result["status"] == "degraded"
    assert result["decision"] is not None
    assert any("revision failed" in warning.lower() for warning in result["warnings"])


def test_engine_downgrades_an_unsupported_verified_consensus_claim():
    script = ScriptedPanel(decision_state="verified_consensus")
    result = _run(script)
    assert result["decision_state"] == "provisional_majority"
    assert "engine_state_adjustment" in result["decision"]
    assert any("downgraded" in warning.lower() for warning in result["warnings"])


def test_two_critique_rounds_are_exact_and_three_is_rejected():
    script = ScriptedPanel()
    result = panel.consult(
        "Question",
        key="sk-unit",
        critique_rounds=2,
        _ask=script,
        _usage=lambda _key: None,
    )
    assert len(result["stages"]["critique"]) == 2
    assert [call["stage"] for call in script.calls].count("critique") == 2
    assert [call["stage"] for call in script.calls].count("revision") == 6
    with pytest.raises(ValueError, match="between 0 and 2"):
        panel.consult("Question", key="sk-unit", critique_rounds=3, _ask=script)


def test_independent_mode_only_queries_selected_advisers():
    script = ScriptedPanel()
    result = panel.consult(
        "Question",
        key="sk-unit",
        mode="independent",
        models="vendor/one,vendor/two",
        _ask=script,
        _usage=lambda _key: None,
    )
    assert result["status"] == "complete"
    assert result["protocol_version"] == "opinions/1.0"
    assert result["decision"] is None
    assert result["models"] == ["vendor/one", "vendor/two"]
    assert [call["stage"] for call in script.calls] == ["advice", "advice"]


def test_one_adviser_failure_is_soft_and_never_leaks_key():
    victim = "deepseek/deepseek-v4-pro"
    secret = "sk-or-v1-THIS-MUST-NEVER-LEAK-123456"
    script = ScriptedPanel(failures={("advice", victim)})
    result = panel.consult(
        "Question",
        key=secret,
        _ask=script,
        _usage=lambda _key: None,
    )
    by_model = {answer["model"]: answer for answer in result["answers"]}
    assert by_model[victim]["ok"] is False
    assert "[REDACTED]" in by_model[victim]["error"]
    assert secret not in json.dumps(result)
    assert sum(answer["ok"] for answer in result["answers"]) == 2
    assert result["decision"] is not None


def test_dissent_failure_forces_degraded_status():
    script = ScriptedPanel(failures={("dissent", panel.DEFAULT_RED_TEAM.model)})
    result = _run(script)
    assert result["status"] == "degraded"
    assert result["dissent"]["status"] == "unavailable"
    assert any("dissent" in warning.lower() for warning in result["warnings"])


def test_chair_failure_uses_deterministic_jury_fallback():
    script = ScriptedPanel(failures={("chair", panel.DEFAULT_CHAIR.model)})
    result = _run(script)
    assert result["status"] == "degraded"
    assert result["decision"]["fallback"] is True
    assert result["decision"]["selected_candidate_id"] == "A01"


def test_invalid_jury_ballots_are_visible_and_degrade():
    invalid = {spec.model for spec in panel.DEFAULT_JURY}
    script = ScriptedPanel(invalid_ballots=invalid)
    result = _run(script)
    assert result["status"] == "failed"
    assert result["stages"]["jury"]["aggregation"]["ballot_count"] == 0
    assert len([failure for failure in result["failures"] if failure["stage"] == "jury"]) == 3


def test_account_delta_is_used_only_when_response_cost_is_missing():
    script = ScriptedPanel(cost=None)
    usage = iter([10.0, 10.75])
    result = panel.consult(
        "Question",
        key="sk-unit",
        mode="independent",
        _ask=script,
        _usage=lambda _key: next(usage),
    )
    assert result["cost_usd"] == pytest.approx(0.75)
    assert result["cost"]["source"] == "account_usage_delta"
    assert "concurrent" in result["cost"]["warning"].lower()


def test_empty_question_missing_key_and_invalid_models_fail_before_calls(monkeypatch):
    script = ScriptedPanel()
    with pytest.raises(ValueError, match="non-empty"):
        panel.consult("   ", key="sk-unit", _ask=script)
    monkeypatch.setattr(panel, "load_key", lambda: "")
    with pytest.raises(ValueError, match="OpenRouter key"):
        panel.consult("Question", _ask=script)
    with pytest.raises(ValueError, match="Invalid OpenRouter model"):
        panel.consult("Question", key="sk-unit", models=["not-a-model"], _ask=script)
    with pytest.raises(ValueError, match="Duplicate adviser"):
        panel.consult(
            "Question", key="sk-unit", models=["vendor/a", "vendor/a"], _ask=script
        )
    assert script.calls == []


def test_potential_credentials_are_blocked_before_any_external_call():
    script = ScriptedPanel()
    with pytest.raises(ValueError, match="Potential provider token"):
        panel.consult(
            "Please inspect sk-or-v1-THISISAREALSECRETSHAPEDTOKEN123456",
            key="sk-unit",
            _ask=script,
        )
    with pytest.raises(ValueError, match="credential assignment"):
        panel.consult(
            "Question",
            key="sk-unit",
            system="Authorization policy: password=SuperSecretValue123",
            _ask=script,
        )
    assert script.calls == []


def test_usage_endpoint_failure_or_malformed_body_never_aborts_council():
    script = ScriptedPanel(cost=None)

    def broken_usage(_key):
        raise AttributeError("malformed credits response")

    result = panel.consult(
        "Question",
        key="sk-unit",
        mode="independent",
        _ask=script,
        _usage=broken_usage,
    )
    assert result["status"] == "complete"
    assert result["cost_usd"] is None
    assert result["cost"]["source"] == "unavailable"


def test_exact_jury_tie_requires_escalation_instead_of_manufacturing_a_winner():
    candidates = ["A02", "A01"]
    ballots = [
        {
            "scores": [
                {"candidate_id": candidate_id, **{name: 8 for name in panel.SCORE_WEIGHTS}}
                for candidate_id in candidates
            ],
            "ranking": candidates,
            "preferred_candidate_id": "A02",
        },
        {
            "scores": [
                {"candidate_id": candidate_id, **{name: 8 for name in panel.SCORE_WEIGHTS}}
                for candidate_id in candidates
            ],
            "ranking": list(reversed(candidates)),
            "preferred_candidate_id": "A01",
        },
    ]
    aggregation = panel._aggregate_ballots(ballots, candidates)
    assert aggregation["winner_candidate_id"] is None
    assert set(aggregation["tied_candidate_ids"]) == {"A01", "A02"}


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def read(self, size: int = -1) -> bytes:
        payload = json.dumps(self.payload).encode("utf-8")
        return payload if size < 0 else payload[:size]


def test_real_request_builder_asks_for_usage_without_exposing_key():
    captured: dict[str, Any] = {}

    def opener(request, timeout=None):
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "choices": [{"message": {"content": "answer"}}],
                "usage": {"cost": 0.123},
            }
        )

    text, cost = panel.ask_with_usage(
        "sk-secret", "vendor/model", "system", "question", opener=opener
    )
    assert (text, cost) == ("answer", 0.123)
    assert captured["body"]["usage"] == {"include": True}
    assert captured["body"]["messages"][-1]["content"] == "question"
    assert "sk-secret" not in json.dumps(captured["body"])


@pytest.mark.parametrize("payload", [[], {"data": []}, {"data": {"total_usage": "NaN"}}])
def test_total_usage_rejects_wrong_shapes_and_non_finite_values(payload):
    assert panel.total_usage("sk-unit", opener=lambda *_args, **_kwargs: _FakeResponse(payload)) is None


def test_cli_json_is_parseable_and_cost_is_on_stderr(tmp_path, monkeypatch, capsys):
    question = tmp_path / "case.txt"
    question.write_text("Question", encoding="utf-8")
    fake_result = {
        "status": "complete",
        "protocol_version": "opinions/1.0",
        "answers": [],
        "decision": None,
        "dissent": {"status": "not_run"},
        "warnings": [],
        "cost_usd": 0.5,
        "cost": {"source": "per_response_usage", "calls": []},
    }
    monkeypatch.setattr(panel, "consult", lambda *_args, **_kwargs: fake_result)
    assert panel.main([str(question), "--json", "--mode", "independent"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "complete"
    assert "PANEL COST: $0.5000" in output.err


# ---------------------------------------------------------------------------
# SignalOS IPC compatibility and secret boundary
# ---------------------------------------------------------------------------


def test_ipc_panel_consult_envelope_options_and_no_key_leak(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    secret = "sk-or-v1-" + ("Z" * 48)
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    captured: dict[str, Any] = {}

    def fake_consult(question, **kwargs):
        captured.update(kwargs)
        captured["question"] = question
        return {
            "answers": [],
            "cost_usd": 0.0123,
            "models": [],
            "system": "",
            "status": "complete",
            "decision": None,
            "dissent": {"status": "not_run"},
        }

    monkeypatch.setattr(panel, "consult", fake_consult)
    response = srv.panel_consult(
        "req-panel-1",
        [
            json.dumps(
                {
                    "question": "Ship?",
                    "mode": "council",
                    "advisers": ["vendor/a", "vendor/b"],
                    "chair": "vendor/chair",
                    "critique_rounds": 2,
                }
            )
        ],
    )
    assert response["ok"] is True
    assert response["id"] == "req-panel-1"
    assert captured["key"] == secret
    assert captured["question"] == "Ship?"
    assert captured["mode"] == "council"
    assert captured["models"] == ["vendor/a", "vendor/b"]
    assert captured["chair"] == "vendor/chair"
    assert captured["critique_rounds"] == 2
    assert secret not in json.dumps(response)


def test_ipc_reads_workspace_env_local_and_never_returns_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    secret = "sk-or-v1-" + ("v" * 40)
    (tmp_path / ".env.local").write_text(
        f"OPENROUTER_API_KEY={secret}\n", encoding="utf-8"
    )
    captured: dict[str, Any] = {}

    def fake_consult(question, **kwargs):
        captured.update(kwargs)
        return {"answers": [], "cost_usd": None, "models": [], "system": ""}

    monkeypatch.setattr(panel, "consult", fake_consult)
    response = srv.panel_consult("req-panel-2", [json.dumps({"question": "Q?"})])
    assert response["ok"] is True
    assert captured["key"] == secret
    assert secret not in json.dumps(response)


def test_ipc_invalid_config_empty_question_and_missing_key_are_safe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-present")
    invalid = srv.panel_consult(
        "req-panel-config", [json.dumps({"question": "Q", "config": []})]
    )
    assert invalid["ok"] is False
    assert "config must be an object" in invalid["error"]

    empty = srv.panel_consult("req-panel-empty", [json.dumps({"question": "   "})])
    assert empty["ok"] is False

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(panel, "load_key", lambda: "")
    missing = srv.panel_consult("req-panel-key", [json.dumps({"question": "Q"})])
    assert missing["ok"] is False
    assert "OpenRouter key" in missing["error"]


def test_panel_command_is_advertised_and_routes_through_handle(tmp_path, monkeypatch):
    assert "panel:consult" in srv.ROUTED_COMMANDS
    assert "panel:consult" in srv._capabilities_payload()["commands"]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-present")
    seen: dict[str, Any] = {}

    def fake_consult(question, **kwargs):
        seen["question"] = question
        seen.update(kwargs)
        return {"answers": [], "cost_usd": 0.0, "models": [], "system": ""}

    monkeypatch.setattr(panel, "consult", fake_consult)
    response = srv.handle(
        {
            "id": "req-panel-route",
            "command": "panel:consult",
            "args": [json.dumps({"question": "Sound?", "mode": "independent"})],
            "cwd": str(tmp_path),
        }
    )
    assert response["ok"] is True
    assert seen["question"] == "Sound?"
    assert seen["mode"] == "independent"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
