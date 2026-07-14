"""Project-bound governance audit evidence regressions.

The workspace has one audit chain but can host multiple virtual projects.  A
canonical artifact path and content hash can therefore be identical in two
projects; the audit project's identity is part of the authorization decision.
"""

from __future__ import annotations

import json
from pathlib import Path

from signalos_lib import sign
from signalos_lib.artifacts import resolve_gate_artifacts
from signalos_lib.commands.validate_gate import validate_gate
from signalos_lib.harness import AgentResponse, TokenUsage
from signalos_lib.product.enforcement_state import StaticEnforcementProvider
from signalos_lib.product.gate_orchestrator import GateOrchestrator, _default_sign


def _seed_gate(root: Path, gate: str, project_id: str) -> None:
    for artifact in resolve_gate_artifacts(root, gate, project_id=project_id):
        artifact.path.parent.mkdir(parents=True, exist_ok=True)
        artifact.path.write_text(
            f"# {artifact.label}\n\nFinal governance content.\n",
            encoding="utf-8",
        )


def _audit_rows(root: Path) -> list[dict]:
    path = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _copy_gate(root: Path, gate: str, source: str, target: str) -> None:
    source_entries = resolve_gate_artifacts(root, gate, project_id=source)
    target_entries = resolve_gate_artifacts(root, gate, project_id=target)
    for source_entry, target_entry in zip(source_entries, target_entries, strict=True):
        target_entry.path.parent.mkdir(parents=True, exist_ok=True)
        target_entry.path.write_bytes(source_entry.path.read_bytes())


def test_mixed_role_sign_rows_are_bound_to_nondefault_project(tmp_path: Path) -> None:
    _seed_gate(tmp_path, "G3", "alpha")

    signed = _default_sign(
        tmp_path,
        "G3",
        "Delivery Agent",
        "PE",
        "APPROVED",
        "",
        project_id="alpha",
    )

    expected = {entry.rel_path for entry in resolve_gate_artifacts(tmp_path, "G3")}
    assert set(signed) == expected
    rows = [row for row in _audit_rows(tmp_path) if row.get("action") == "sign"]
    assert {row["artifact"] for row in rows} == expected
    assert {row["project_id"] for row in rows} == {"alpha"}
    assert {row["role"] for row in rows} == {"PO", "PE"}
    assert sign.verify_audit_chain(
        tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl"
    ) == []
    assert validate_gate(
        tmp_path, "G3", project_id="alpha", write_evidence=False
    )["ok"] is True


def test_same_path_and_hash_from_project_a_cannot_authorize_project_b(
    tmp_path: Path,
) -> None:
    _seed_gate(tmp_path, "G3", "alpha")
    _default_sign(
        tmp_path,
        "G3",
        "Delivery Agent",
        "PE",
        "APPROVED",
        "",
        project_id="alpha",
    )
    # Copy the exact signed bytes.  Every canonical rel_path, artifact hash,
    # verdict, and in-file signature now matches; only the project differs.
    _copy_gate(tmp_path, "G3", "alpha", "beta")

    assert validate_gate(
        tmp_path, "G3", project_id="alpha", write_evidence=False
    )["ok"] is True
    replay = validate_gate(
        tmp_path, "G3", project_id="beta", write_evidence=False
    )

    assert replay["ok"] is False
    linked = next(
        check for check in replay["checks"] if check["id"] == "gate-audit-linked"
    )
    assert set(linked["details"]["missing_links"]) == {
        entry.rel_path for entry in resolve_gate_artifacts(tmp_path, "G3")
    }
    assert linked["details"]["project_id"] == "beta"


def test_legacy_unbound_rows_are_default_only(tmp_path: Path) -> None:
    _seed_gate(tmp_path, "G1", "default")
    audit = tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    for artifact in resolve_gate_artifacts(tmp_path, "G1"):
        sign.sign_artifact(
            artifact.path, "Legacy PO", "PO", "G1", "APPROVED"
        )
        sign.append_audit_event(audit, {
            "action": "sign",
            "actor": "Legacy PO",
            "role": "PO",
            "gate": "Gate 1",
            "artifact": artifact.rel_path,
            "hash": sign._compute_hash(artifact.path),
            "verdict": "APPROVED",
            # Deliberately no project_id: this is a pre-project migration row,
            # but it still uses the tamper-evident authority chain.
        })
    _copy_gate(tmp_path, "G1", "default", "alpha")

    assert validate_gate(
        tmp_path, "G1", project_id="default", write_evidence=False
    )["ok"] is True
    assert validate_gate(
        tmp_path, "G1", project_id="alpha", write_evidence=False
    )["ok"] is False


def test_audit_revocation_is_project_bound_after_marker_loss(tmp_path: Path) -> None:
    for project_id in ("alpha", "beta"):
        _seed_gate(tmp_path, "G2", project_id)
        _default_sign(
            tmp_path,
            "G2",
            f"{project_id} owner",
            "PO",
            "APPROVED",
            "",
            project_id=project_id,
        )
    sign.revoke_gate(
        tmp_path, "G2", project_id="alpha", reason="alpha plan reopened",
    )
    marker = (
        tmp_path / ".signalos" / "projects" / "alpha"
        / "gate-revocations.json"
    )
    marker.unlink()

    assert validate_gate(
        tmp_path, "G2", project_id="alpha", write_evidence=False,
    )["ok"] is False
    assert validate_gate(
        tmp_path, "G2", project_id="beta", write_evidence=False,
    )["ok"] is True


class _EndAdapter:
    supports_tool_calls = True

    def chat(self, messages, model="test", tools=None, stream=False):
        return AgentResponse(
            content="done", tool_calls=None, stop_reason="end_turn", usage=TokenUsage()
        )


def test_reopen_and_g5_outcome_events_keep_project_and_chain(tmp_path: Path) -> None:
    orch = GateOrchestrator(
        tmp_path,
        _EndAdapter(),
        lambda _event: None,
        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
        sign_fn=lambda *_args, **_kwargs: ["artifact.md"],
        prompt="build it",
        project_id="alpha",
        run_id="audit-alpha-run",
    )
    orch.state.status = "complete"
    orch.state.signed = ["G3", "G4"]
    orch.state.current_gate = "G5"

    result = orch.reopen_gate("G3", "design changed")
    assert result["status"] == "reopened"

    sign._record_g5_seal_outcome(tmp_path, "ok", project_id="alpha")
    sign._record_g5_commit_outcome(tmp_path, "committed", project_id="alpha")
    sign._record_g5_push_outcome(tmp_path, "ok", project_id="alpha")

    rows = _audit_rows(tmp_path)
    scoped = [
        row
        for row in rows
        if row.get("action") in {
            "gate.revoke",
            "gate.reopen",
            "gate.unsign",
            "g5-seal-result",
            "g5-commit-result",
            "g5-push-result",
        }
    ]
    assert scoped
    assert {row.get("project_id") for row in scoped} == {"alpha"}
    assert sign.verify_audit_chain(
        tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl"
    ) == []
