"""End-to-end contract tests for the desktop's explicit G0 approval seam."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import signalos_ipc_server as ipc
import signalos_lib.sign as sign_module
from signalos_lib.artifacts import resolve_gate_artifacts
from signalos_lib.sign import (
    SOLO_FOUNDER_GATE0_CONSENT,
    _append_audit,
    append_audit_event,
    approve_gate0_as_solo_founder,
    check_gate_signed_strict,
    sign_artifact,
    sign_gate,
    verify_audit_chain,
)


def _workspace(
    root: Path,
    *,
    role: str = "PO",
    missing_last: bool = False,
    project_id: str = "default",
) -> Path:
    signalos = root / ".signalos"
    signalos.mkdir(parents=True, exist_ok=True)
    (signalos / "identity.json").write_text(
        json.dumps({"name": "Founder", "role": role}), encoding="utf-8"
    )
    entries = resolve_gate_artifacts(root, "G0", project_id=project_id)
    if missing_last:
        entries = entries[:-1]
    for index, entry in enumerate(entries, start=1):
        entry.path.parent.mkdir(parents=True, exist_ok=True)
        entry.path.write_text(
            f"# {entry.label}\n\nReviewed governance content {index}.\nOwner: Founder.\n",
            encoding="utf-8",
        )
    return root


def _approve(root: Path, **overrides: str) -> dict:
    args = {
        "consent": SOLO_FOUNDER_GATE0_CONSENT,
        "via": "button",
        "expected_workspace": str(root),
        "approval_id": "approval-test-1",
        "project_id": "default",
        "expected_project_id": "default",
    }
    args.update(overrides)
    return approve_gate0_as_solo_founder(root, **args)


def test_valid_backend_transaction_signs_all_artifacts_and_audit_links(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = _approve(root)

    assert result["signed"] is True, result
    assert len(result["signed_paths"]) == 4
    strict = check_gate_signed_strict(root, "G0")
    assert strict.signed, strict.reasons
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    authority = [row for row in rows if row.get("action") == "authority:solo-founder-g0-declared"]
    assert len(authority) == 1
    assert authority[0]["actor"] == "Founder"
    assert authority[0]["role"] == "PO"
    assert authority[0]["delegated_role"] == "PE"
    assert authority[0]["consent"] == SOLO_FOUNDER_GATE0_CONSENT
    assert verify_audit_chain(audit) == []


def test_ipc_returns_strict_fresh_numeric_gate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    payload = {
        "consent": SOLO_FOUNDER_GATE0_CONSENT,
        "via": "chat",
        "expected_workspace": str(root),
        "approval_id": "ipc-approval-1",
        "expected_project_id": "default",
    }

    response = ipc.handle(
        {
            "id": "req-g0",
            "command": "gate0:approve",
            "args": [json.dumps(payload)],
            "cwd": str(root),
        }
    )

    assert response["ok"] is True, response
    assert response["data"]["signed"] is True
    gate0 = next(gate for gate in response["data"]["gates"] if gate["id"] == 0)
    assert gate0["status"] == "signed"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("consent", "not approve", "exact consent"),
        ("via", "script", "source"),
        ("expected_workspace", "C:/different-workspace", "different workspace"),
    ],
)
def test_transaction_rejects_untrusted_request_context(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    root = _workspace(tmp_path)

    with pytest.raises(ValueError, match=message):
        _approve(root, **{field: value})

    assert not check_gate_signed_strict(root, "G0").signed


def test_non_po_identity_cannot_assert_solo_founder_delegation(tmp_path: Path) -> None:
    root = _workspace(tmp_path, role="QA")

    with pytest.raises(ValueError, match="persisted PO"):
        _approve(root)

    assert not check_gate_signed_strict(root, "G0").signed


def test_strict_validator_enforces_roles_per_artifact_not_gate_union(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    # PO is valid for two G0 artifacts but not Surface Inventory/Permanently T3.
    # Directly craft audit-linked PO signatures over all four to prove the
    # canonical validator enforces each artifact's own role declaration.
    for entry in resolve_gate_artifacts(root, "G0"):
        sign_artifact(entry.path, "Forged PO", "PO", "G0", "APPROVED")
        _append_audit(
            audit, "Forged PO", "PO", "G0", entry.rel_path, entry.path, "APPROVED"
        )

    strict = check_gate_signed_strict(root, "G0")

    assert strict.signed is False
    assert any("PE" in reason and "SURFACE_INVENTORY" in reason for reason in strict.reasons)


def test_missing_artifact_fails_before_any_signature_is_written(tmp_path: Path) -> None:
    root = _workspace(tmp_path, missing_last=True)

    result = _approve(root)

    assert result["signed"] is False
    assert "missing" in result["reason"]
    assert not (root / ".signalos" / "AUDIT_TRAIL.jsonl").exists()
    assert all("## Signatures" not in entry.path.read_text(encoding="utf-8")
               for entry in resolve_gate_artifacts(root, "G0") if entry.path.exists())


def test_unresolved_template_fails_before_any_signature_is_written(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    first = resolve_gate_artifacts(root, "G0")[0].path
    first.write_text("# Soul\n\nOwner: {PO}\nDate: [DATE]\n", encoding="utf-8")

    result = _approve(root)

    assert result["signed"] is False
    assert "template" in result["reason"]
    assert not (root / ".signalos" / "AUDIT_TRAIL.jsonl").exists()


def test_idempotent_retry_does_not_append_duplicate_authority_or_signatures(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    first = _approve(root)
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    before = audit.read_text(encoding="utf-8")

    second = _approve(root)

    assert first["signed"] is True
    assert second["signed"] is True
    assert second["already_signed"] is True
    assert audit.read_text(encoding="utf-8") == before


def test_live_transaction_lock_fails_closed(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    lock = root / ".signalos" / "locks" / "gate0-approval.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps({"approval_id": "other", "pid": os.getpid(), "created_at": __import__("time").time()}),
        encoding="utf-8",
    )

    result = _approve(root)

    assert result["signed"] is False
    assert "already in progress" in result["reason"]
    assert not check_gate_signed_strict(root, "G0").signed


def test_authority_and_idempotency_are_project_bound(tmp_path: Path) -> None:
    root = _workspace(tmp_path, project_id="alpha")
    _workspace(root, project_id="beta")

    alpha = _approve(
        root,
        project_id="alpha",
        expected_project_id="alpha",
        approval_id="shared-approval-id",
    )
    with pytest.raises(ValueError, match="another context"):
        _approve(
            root,
            project_id="beta",
            expected_project_id="beta",
            approval_id="shared-approval-id",
        )
    beta = _approve(
        root,
        project_id="beta",
        expected_project_id="beta",
        approval_id="beta-approval-id",
    )

    assert alpha["signed"] is True
    assert beta["signed"] is True
    rows = _audit_rows(root)
    authority = [
        row for row in rows
        if row.get("action") == "authority:solo-founder-g0-declared"
    ]
    assert [row["project_id"] for row in authority] == ["alpha", "beta"]
    assert check_gate_signed_strict(root, "G0", project_id="alpha").signed
    assert check_gate_signed_strict(root, "G0", project_id="beta").signed


def test_project_binding_mismatch_is_rejected_before_writes(tmp_path: Path) -> None:
    root = _workspace(tmp_path, project_id="alpha")

    with pytest.raises(ValueError, match="different project"):
        _approve(root, project_id="alpha", expected_project_id="beta")

    assert not (root / ".signalos" / "AUDIT_TRAIL.jsonl").exists()


def test_generic_audit_api_cannot_forge_the_reserved_authority_event(tmp_path: Path) -> None:
    root = _workspace(tmp_path, role="QA")
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"

    with pytest.raises(ValueError, match="reserved Gate 0 authority"):
        append_audit_event(
            audit,
            {
                "actor": "Forged Founder",
                "role": "PO",
                "action": "authority:solo-founder-g0-declared",
                "gate": "Gate 0",
                "approval_id": "forged",
                "delegated_role": "PE",
                "scope": "G0 only",
                "via": "script",
                "consent": SOLO_FOUNDER_GATE0_CONSENT,
                "workspace": str(root.resolve()),
                "project_id": "default",
            },
        )

    assert not audit.exists()


def test_unchained_authority_row_never_satisfies_strict_g0(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    forged = {
        "actor": "Forged Founder",
        "role": "PO",
        "action": "authority:solo-founder-g0-declared",
        "gate": "Gate 0",
        "approval_id": "forged-tail-row",
        "delegated_role": "PE",
        "scope": "G0 only",
        "via": "button",
        "consent": SOLO_FOUNDER_GATE0_CONSENT,
        "workspace": str(root.resolve()),
        "project_id": "default",
        "ts": "2026-01-01T00:00:00Z",
    }
    audit.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    sign_gate(root, "G0", "Forged PE", "PE", "APPROVED", audit_log=audit)

    strict = check_gate_signed_strict(root, "G0")

    assert strict.signed is False
    assert any("authority declaration" in reason for reason in strict.reasons)


def test_audit_chain_tamper_invalidates_strict_gate_truth(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    assert _approve(root)["signed"] is True
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    rows = audit.read_text(encoding="utf-8").splitlines()
    authority = json.loads(rows[0])
    authority["consent"] = "I did not approve anything"
    rows[0] = json.dumps(authority)
    audit.write_text("\n".join(rows) + "\n", encoding="utf-8")

    strict = check_gate_signed_strict(root, "G0")

    assert strict.signed is False
    assert any("audit integrity" in reason for reason in strict.reasons)


def test_partial_sign_failure_rolls_back_every_artifact_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    original = sign_module._append_audit
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected audit disk failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(sign_module, "_append_audit", fail_second)

    with pytest.raises(OSError, match="injected"):
        _approve(root)

    assert not (root / ".signalos" / "AUDIT_TRAIL.jsonl").exists()
    assert not (
        root / ".signalos" / "transactions" / "gate0-approval.json"
    ).exists()
    for entry in resolve_gate_artifacts(root, "G0"):
        assert "## Signatures" not in entry.path.read_text(encoding="utf-8")


def test_next_approval_recovers_a_prepared_crash_journal(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    entries = resolve_gate_artifacts(root, "G0")
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    snapshots = dict(
        sign_module._snapshot_file(root, path)
        for path in [
            *(entry.path for entry in entries),
            audit,
            sign_module._gate_revocations_path(root, "default"),
        ]
    )
    journal = sign_module._gate0_transaction_path(root, "default")
    sign_module._atomic_write_json(
        journal,
        {
            "schema": "signalos.gate0-approval.v1",
            "phase": "prepared",
            "transaction_id": "c" * 32,
            "approval_id": "crashed-approval",
            "workspace": str(root.resolve()),
            "project_id": "default",
            "created_at": "2026-01-01T00:00:00Z",
            "snapshots": snapshots,
        },
    )
    entries[0].path.write_text("partial/corrupt write", encoding="utf-8")
    audit.write_text('{"partial":true}\n', encoding="utf-8")

    result = _approve(root)

    assert result["signed"] is True
    assert not journal.exists()
    assert verify_audit_chain(audit) == []


def test_recovery_journal_cannot_write_outside_exact_transaction_allowlist(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    git_config = root / ".git" / "config"
    git_config.parent.mkdir()
    git_config.write_text("SAFE", encoding="utf-8")
    snapshots = dict(
        sign_module._snapshot_file(root, path)
        for path in sign_module._gate0_snapshot_paths(root, "default")
    )
    snapshots[".git/config"] = {
        "exists": True,
        "content_b64": "TUFMSUNJT1VT",
    }
    journal = sign_module._gate0_transaction_path(root, "default")
    sign_module._atomic_write_json(
        journal,
        {
            "schema": "signalos.gate0-approval.v1",
            "phase": "prepared",
            "transaction_id": "d" * 32,
            "approval_id": "malicious-journal",
            "workspace": str(root.resolve()),
            "project_id": "default",
            "created_at": "2026-01-01T00:00:00Z",
            "snapshots": snapshots,
        },
    )

    with pytest.raises(ValueError, match="snapshot allowlist"):
        _approve(root)

    assert git_config.read_text(encoding="utf-8") == "SAFE"


def test_preflight_is_inside_lock_and_detects_racing_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    target = resolve_gate_artifacts(root, "G0")[0].path
    original = sign_module._try_acquire_gate0_approval_lock

    def acquire_then_mutate(*args, **kwargs):
        lock = original(*args, **kwargs)
        target.write_text("# Soul\n\nDate: [DATE]\n", encoding="utf-8")
        return lock

    monkeypatch.setattr(
        sign_module, "_try_acquire_gate0_approval_lock", acquire_then_mutate
    )

    result = _approve(root)

    assert result["signed"] is False
    assert "template" in result["reason"]
    assert not (root / ".signalos" / "AUDIT_TRAIL.jsonl").exists()


def test_existing_valid_signatures_still_record_authority_declaration(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    sign_gate(root, "G0", "Independent PE", "PE", "APPROVED", audit_log=audit)
    assert not check_gate_signed_strict(root, "G0").signed

    result = _approve(root)

    assert result["signed"] is True
    authority = [
        row for row in _audit_rows(root)
        if row.get("action") == "authority:solo-founder-g0-declared"
    ]
    assert len(authority) == 1
    assert authority[0]["project_id"] == "default"


def test_single_brace_examples_are_not_false_positive_placeholders(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    target = resolve_gate_artifacts(root, "G0")[0].path
    target.write_text(
        target.read_text(encoding="utf-8") + "\nExample syntax: {user_name}\n",
        encoding="utf-8",
    )

    assert _approve(root)["signed"] is True


def _audit_rows(root: Path) -> list[dict]:
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    return [
        json.loads(line)
        for line in audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
