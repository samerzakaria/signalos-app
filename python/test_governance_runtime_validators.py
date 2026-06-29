from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.product.agent_packets import build_agent_packet, write_agent_packet
from signalos_lib.product.generation import prepare_generation
from signalos_lib.validators.governance_runtime import (
    detect_governance_bypass,
    resolve_guidance_obligations,
    validate_guidance_obligations,
)


def _intent() -> dict:
    return {
        "product_name": "Governed Tasks",
        "product_type": "task-management",
        "entities": ["task"],
        "primary_workflows": ["create task"],
        "ux_surfaces": ["task list"],
        "api_surfaces": ["tasks api"],
    }


def _acceptance_matrix() -> dict:
    return {
        "schema_version": "signalos.acceptance_matrix.v1",
        "criteria": [
            {
                "id": "AC-001",
                "description": "Users can create a task",
                "status": "pending",
            }
        ],
        "test_scenarios": [],
    }


def _blueprint() -> dict:
    return {
        "id": "task-management",
        "entities": [{"name": "Task", "fields": ["title"]}],
        "workflows": [{"name": "create task"}],
        "ui": ["task-list"],
        "api": ["tasks"],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_guidance_obligations_pass_for_generation_packet(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    prepare_generation(
        repo_root=tmp_path,
        intent=_intent(),
        blueprint=_blueprint(),
        profile="generic",
        acceptance_matrix=_acceptance_matrix(),
    )

    passed, message, details = validate_guidance_obligations(tmp_path)

    assert passed is True, details
    assert message == "guidance obligations satisfied"
    assert details["status"] == "PASS"
    assert (tmp_path / ".signalos" / "product" / "VALIDATE_GUIDANCE_OBLIGATIONS.json").is_file()


def test_guidance_obligations_fail_when_generation_enforcement_is_weakened(
    tmp_path: Path,
) -> None:
    (tmp_path / ".signalos").mkdir()
    prepare_generation(
        repo_root=tmp_path,
        intent=_intent(),
        blueprint=_blueprint(),
        profile="generic",
        acceptance_matrix=_acceptance_matrix(),
    )
    packet_path = tmp_path / ".signalos" / "product" / "GENERATION_PACKET.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["governance_enforcement"]["constitution_required"] = False
    _write_json(packet_path, packet)

    passed, _message, details = validate_guidance_obligations(tmp_path)

    assert passed is False
    assert details["status"] == "FAIL"
    assert any("constitution_required" in violation for violation in details["violations"])


def test_guidance_obligation_resolver_matches_touched_ui_paths(tmp_path: Path) -> None:
    report = resolve_guidance_obligations(
        tmp_path,
        ["src/components/TaskList.tsx"],
        action="edit",
        stack="react-vite",
    )

    assert report["errors"] == []
    resolved_ids = {item["id"] for item in report["resolved"]}
    assert {
        "test-driven-development",
        "test-generation",
        "verification-before-completion",
        "design",
        "e2e-testing",
    }.issubset(resolved_ids)
    assert any(item["rule_id"] == "OBL-APP-003" for item in report["resolved"])


def test_guidance_obligations_fail_when_enforced_touched_path_is_not_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / ".signalos").mkdir()

    passed, _message, details = validate_guidance_obligations(
        tmp_path,
        touched_paths=["src/components/TaskList.tsx"],
        action="edit",
        stack="react-vite",
    )

    assert passed is False
    assert details["status"] == "FAIL"
    assert "design" in details["enforced_loaded_ids"]
    assert any("loaded guidance evidence is required" in item for item in details["violations"])


def test_guidance_obligations_pass_when_enforced_touched_path_is_loaded(
    tmp_path: Path,
) -> None:
    (tmp_path / ".signalos").mkdir()
    loaded = tmp_path / ".signalos" / "loaded-guidance.txt"
    loaded.write_text(
        "\n".join(
            [
                "test-driven-development",
                "test-generation",
                "verification-before-completion",
                "design",
                "e2e-testing",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    passed, _message, details = validate_guidance_obligations(
        tmp_path,
        touched_paths=["src/components/TaskList.tsx"],
        action="edit",
        stack="react-vite",
        loaded_path=loaded,
    )

    assert passed is True, details
    assert details["status"] == "PASS"
    assert details["resolved_obligations"]["resolved"]
    assert details["loaded_guidance_ids"] == [
        "design",
        "e2e-testing",
        "test-driven-development",
        "test-generation",
        "verification-before-completion",
    ]


def test_detect_bypass_fails_for_forbidden_agent_result(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    packet = build_agent_packet(
        repo_root=tmp_path,
        intent=_intent(),
        blueprint=_blueprint(),
        acceptance_matrix=_acceptance_matrix(),
        profile="generic",
        wave="1",
        tasks=[{"id": "T1", "title": "Implement task creation"}],
        allowed_paths=["src/**", "tests/**"],
    )
    run_dir = write_agent_packet(packet, tmp_path)
    _write_json(
        run_dir / "RESULT.json",
        {
            "run_id": packet["run_id"],
            "status": "completed",
            "files_written": [".signalos/hack.json"],
            "actions_taken": ["wrote forbidden governance file"],
            "validation_results": {},
        },
    )

    passed, message, details = detect_governance_bypass(tmp_path, diff_text="")

    assert passed is False
    assert message == "governance-bypass signature detected"
    assert any("agent result" in violation for violation in details["violations"])
    assert details["checks"]
    assert (tmp_path / ".signalos" / "product" / "VALIDATE_BYPASS_DETECTION.json").is_file()


def test_detect_bypass_fails_for_commit_skip_marker(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    message_path = tmp_path / "COMMIT_EDITMSG"
    message_path.write_text("ship it [no-verify]\n", encoding="utf-8")

    passed, _message, details = detect_governance_bypass(
        tmp_path,
        diff_text="",
        message_file=message_path,
    )

    assert passed is False
    assert any("[no-verify]" in violation for violation in details["violations"])


def test_detect_bypass_flags_added_unjustified_suppression(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    diff_text = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,4 @@
 import os
+value = os.environ["X"]  # type: ignore
+result = compute()  # noqa
"""

    passed, _message, details = detect_governance_bypass(tmp_path, diff_text=diff_text)

    assert passed is False
    suppression = [
        v for v in details["violations"] if "suppression without justification" in v
    ]
    assert len(suppression) == 2
    assert any("type: ignore" in v for v in suppression)
    assert any("noqa" in v for v in suppression)


def test_detect_bypass_allows_justified_suppression_and_clean_diff(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    diff_text = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,3 @@
 import os
+value = os.environ["X"]  # type: ignore[index]  # upstream stub has no Mapping type
"""

    passed, _message, details = detect_governance_bypass(tmp_path, diff_text=diff_text)

    assert passed is True, details
    assert not any("suppression" in v for v in details["violations"])

    # No diff at all must also pass (conservative: only added lines are inspected).
    passed_empty, _m, details_empty = detect_governance_bypass(tmp_path, diff_text="")
    assert passed_empty is True, details_empty
    assert not any("suppression" in v for v in details_empty["violations"])


def test_resolve_guidance_obligations_surfaces_missing_catalog(tmp_path: Path) -> None:
    # A missing/unreadable catalog or obligations path must surface an error,
    # never silently pass (SignalOS enforces, never advises).
    report = resolve_guidance_obligations(
        tmp_path,
        ["src/components/TaskList.tsx"],
        action="edit",
        stack="react-vite",
        catalog_path=tmp_path / "does-not-exist-catalog.json",
    )
    assert report["errors"]
    assert any("catalog unreadable" in err for err in report["errors"])
    assert report["resolved"] == []

    report2 = resolve_guidance_obligations(
        tmp_path,
        ["src/components/TaskList.tsx"],
        action="edit",
        stack="react-vite",
        obligations_path=tmp_path / "does-not-exist-obligations.json",
    )
    assert report2["errors"]
    assert any("obligations unreadable" in err for err in report2["errors"])
    assert report2["resolved"] == []


def test_validate_guidance_obligations_blocks_on_unreadable_obligations(tmp_path: Path) -> None:
    # The higher-level validator must FAIL (not pass) when obligation resolution errors.
    (tmp_path / ".signalos").mkdir()

    passed, _message, details = validate_guidance_obligations(
        tmp_path,
        touched_paths=["src/components/TaskList.tsx"],
        action="edit",
        stack="react-vite",
        obligations_path=tmp_path / "does-not-exist-obligations.json",
        write_evidence=False,
    )

    assert passed is False
    assert details["status"] == "FAIL"
    assert any("unreadable" in v for v in details["violations"])


def test_detect_bypass_fails_for_audit_trail_rewrite_diff(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    diff_text = """diff --git a/.signalos/AUDIT_TRAIL.jsonl b/.signalos/AUDIT_TRAIL.jsonl
index 1111111..2222222 100644
--- a/.signalos/AUDIT_TRAIL.jsonl
+++ b/.signalos/AUDIT_TRAIL.jsonl
@@ -1,2 +1,1 @@
-{"event":"old"}
 {"event":"new"}
"""

    passed, _message, details = detect_governance_bypass(tmp_path, diff_text=diff_text)

    assert passed is False
    assert any("AUDIT_TRAIL" in violation for violation in details["violations"])
