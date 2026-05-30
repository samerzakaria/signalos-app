"""Tests for P8 Agent Execution Bridge: agent_packets + repair_loop."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from signalos_lib.product.agent_packets import (
    build_skills_catalog,
    build_agent_packet,
    validate_agent_result,
    write_agent_packet,
)
from signalos_lib.product.agent_dispatch import _build_agent_prompt
from signalos_lib.orchestrator import _SKILL_KEY_TO_PATH
from signalos_lib.product.repair_loop import (
    build_repair_packet,
    run_repair_loop,
    write_repair_packet,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a minimal repo root with .signalos/ directory."""
    (tmp_path / ".signalos").mkdir()
    return tmp_path


@pytest.fixture
def intent() -> dict:
    return {
        "product_name": "TestApp",
        "product_type": "task-management",
        "entities": ["task", "project"],
        "primary_workflows": ["create task", "assign task"],
        "ux_surfaces": ["dashboard", "list"],
    }


@pytest.fixture
def acceptance_matrix() -> dict:
    return {
        "schema_version": "signalos.acceptance_matrix.v1",
        "criteria": [
            {"id": "AC-001", "description": "CRUD for task", "status": "pending"},
            {"id": "AC-002", "description": "CRUD for project", "status": "pending"},
        ],
        "test_scenarios": [],
    }


@pytest.fixture
def tasks() -> list[dict]:
    return [
        {"id": "T1", "title": "Create task list", "description": "Build a task list view"},
        {"id": "T2", "title": "Add task form", "description": "Build form for adding tasks"},
    ]


@pytest.fixture
def allowed_paths() -> list[str]:
    return ["src/components/*", "src/types.ts"]


@pytest.fixture
def packet(repo, intent, acceptance_matrix, tasks, allowed_paths) -> dict:
    """Build a default packet for test reuse."""
    return build_agent_packet(
        repo_root=repo,
        intent=intent,
        blueprint=None,
        acceptance_matrix=acceptance_matrix,
        profile="generic",
        wave="1",
        tasks=tasks,
        allowed_paths=allowed_paths,
    )


# ---------------------------------------------------------------------------
# build_agent_packet
# ---------------------------------------------------------------------------

class TestBuildAgentPacket:
    def test_returns_all_required_fields(self, packet):
        required = [
            "schema_version", "run_id", "created_at", "intent_summary",
            "agent_role", "expertise_frame", "quality_bar",
            "success_criteria", "evidence_required", "forbidden_rules",
            "repair_policy", "escalation_policy", "source_policy",
            "team_contract", "blueprint_id", "profile", "wave", "tasks",
            "acceptance_criteria", "allowed_paths", "forbidden_paths",
            "forbidden_actions", "validation_commands", "skills_catalog",
            "applicable_skills", "result_schema",
        ]
        for field in required:
            assert field in packet, f"Missing field: {field}"

    def test_schema_version(self, packet):
        assert packet["schema_version"] == "signalos.agent_packet.v1"

    def test_run_id_is_valid_uuid(self, packet):
        # Should not raise
        parsed = uuid.UUID(packet["run_id"])
        assert str(parsed) == packet["run_id"]

    def test_forbidden_paths_defaults(self, packet):
        expected = [".signalos/", "node_modules/", ".git/",
                    ".env", ".env.local", "*.pem", "*.key"]
        assert packet["forbidden_paths"] == expected

    def test_forbidden_actions_defaults(self, packet):
        expected = ["git push", "npm publish", "deploy", "rm -rf"]
        assert packet["forbidden_actions"] == expected

    def test_custom_forbidden_actions(self, repo, intent, acceptance_matrix, tasks, allowed_paths):
        pkt = build_agent_packet(
            repo_root=repo,
            intent=intent,
            blueprint=None,
            acceptance_matrix=acceptance_matrix,
            profile="generic",
            wave="1",
            tasks=tasks,
            allowed_paths=allowed_paths,
            forbidden_actions=["curl", "wget"],
        )
        assert pkt["forbidden_actions"] == ["curl", "wget"]

    def test_intent_summary_trimmed(self, packet, intent):
        summary = packet["intent_summary"]
        assert summary["product_name"] == intent["product_name"]
        assert summary["entities"] == intent["entities"]
        # Should not include all intent fields
        assert "auth_requirements" not in summary

    def test_acceptance_criteria_from_matrix(self, packet, acceptance_matrix):
        assert packet["acceptance_criteria"] == acceptance_matrix["criteria"]

    def test_packet_contract_fields_are_enforced(self, packet):
        assert packet["quality_bar"]["completion_rule"]
        assert any("No forbidden" in item for item in packet["success_criteria"])
        assert any("RESULT.json" in item for item in packet["evidence_required"])
        assert any("Never write outside" in item for item in packet["forbidden_rules"])
        assert packet["repair_policy"]["forbidden_violation"].startswith("Reject")
        assert any("Escalate" in item for item in packet["escalation_policy"])
        assert "current_external_claims" in packet["source_policy"]
        assert packet["team_contract"]["agents_are_signalos_team"] is True
        assert packet["team_contract"]["user_manages_agents"] is False
        assert packet["team_contract"]["signalos_orchestrates_team"] is True
        assert "validation_results" in packet["result_schema"]["required"]

    def test_success_criteria_include_tasks_and_acceptance(self, packet):
        assert any("Task T1" in item for item in packet["success_criteria"])
        assert any("Acceptance AC-001" in item for item in packet["success_criteria"])

    def test_blueprint_id_none_when_no_blueprint(self, packet):
        assert packet["blueprint_id"] is None

    def test_blueprint_id_set_when_blueprint(self, repo, intent, acceptance_matrix, tasks, allowed_paths):
        bp = {"id": "bp-123"}
        pkt = build_agent_packet(
            repo_root=repo, intent=intent, blueprint=bp,
            acceptance_matrix=acceptance_matrix, profile="generic",
            wave="2", tasks=tasks, allowed_paths=allowed_paths,
        )
        assert pkt["blueprint_id"] == "bp-123"

    def test_includes_expert_role_frame(self, packet):
        assert packet["agent_role"] == "SignalOS Build agent"
        frame = packet["expertise_frame"].lower()
        assert "highest-level software engineer ever" in frame
        assert "product domain" in frame
        assert "stop and report a blocker" in frame

    def test_skills_catalog_exposes_every_routable_skill(self, packet):
        catalog = packet["skills_catalog"]
        assert len(catalog) == len(_SKILL_KEY_TO_PATH)
        assert {entry["key"] for entry in catalog} == set(_SKILL_KEY_TO_PATH)
        assert all(entry["path"].endswith("SKILL.md") for entry in catalog)

    def test_build_skills_catalog_uses_orchestrator_catalog(self):
        catalog = build_skills_catalog()
        by_key = {entry["key"]: entry for entry in catalog}
        for key, (label, path) in _SKILL_KEY_TO_PATH.items():
            assert by_key[key]["name"] == label
            assert by_key[key]["path"] == path

    def test_applicable_skills_include_default_build_skills(self, packet):
        keys = {entry["key"] for entry in packet["applicable_skills"]}
        assert "test-driven-development" in keys
        assert "test-generation" in keys
        assert "verification-before-completion" in keys
        assert all(entry.get("content") for entry in packet["applicable_skills"])

    def test_ui_and_security_context_adds_domain_skills(
        self, repo, intent, acceptance_matrix, tasks, allowed_paths
    ):
        pkt = build_agent_packet(
            repo_root=repo,
            intent={**intent, "auth_requirements": ["login"]},
            blueprint=None,
            acceptance_matrix=acceptance_matrix,
            profile="generic",
            wave="1",
            tasks=[
                *tasks,
                {
                    "id": "T3",
                    "title": "Build secure dashboard UI",
                    "description": "Add login-gated dashboard screen",
                },
            ],
            allowed_paths=allowed_paths,
        )
        keys = {entry["key"] for entry in pkt["applicable_skills"]}
        assert "design" in keys
        assert "e2e-testing" in keys
        assert "security-audit" in keys


# ---------------------------------------------------------------------------
# write_agent_packet
# ---------------------------------------------------------------------------

class TestWriteAgentPacket:
    def test_creates_all_expected_files(self, repo, packet):
        run_dir = write_agent_packet(packet, repo)
        expected_files = [
            "PACKET.md", "scope.json", "skills-catalog.json",
            "applicable-skills.md", "files-allowed.txt",
            "commands-allowed.txt", "validation-plan.json",
            "result.schema.json",
        ]
        for name in expected_files:
            assert (run_dir / name).is_file(), f"Missing file: {name}"

    def test_run_dir_under_signalos(self, repo, packet):
        run_dir = write_agent_packet(packet, repo)
        assert ".signalos" in str(run_dir)
        assert "agent-runs" in str(run_dir)
        assert packet["run_id"] in str(run_dir)

    def test_scope_json_roundtrips(self, repo, packet):
        run_dir = write_agent_packet(packet, repo)
        reloaded = json.loads((run_dir / "scope.json").read_text(encoding="utf-8"))
        assert reloaded["run_id"] == packet["run_id"]
        assert reloaded["schema_version"] == packet["schema_version"]
        assert reloaded["tasks"] == packet["tasks"]
        assert reloaded["forbidden_paths"] == packet["forbidden_paths"]

    def test_packet_md_human_readable(self, repo, packet):
        run_dir = write_agent_packet(packet, repo)
        md = (run_dir / "PACKET.md").read_text(encoding="utf-8")
        assert "Agent Packet" in md
        assert packet["run_id"] in md
        # Contains intent summary
        assert "TestApp" in md
        # Contains task titles
        assert "Create task list" in md
        assert "Add task form" in md
        # Contains role/expertise framing for external agents
        assert "## Agent Role" in md
        assert "SignalOS Build agent" in md
        assert "highest-level software engineer ever" in md
        assert "## Quality Bar" in md
        assert "## Success Criteria" in md
        assert "## Evidence Required" in md
        assert "## Forbidden Rules" in md
        assert "## Repair/Rework Policy" in md
        assert "## SignalOS Team Contract" in md
        assert "Agents are SignalOS team members managed by SignalOS" in md
        assert "## SignalOS Skills Catalog" in md
        assert "`test-driven-development`" in md
        assert "## Applicable SignalOS Skills" in md
        assert "Test-Driven Development" in md

    def test_skills_files_are_written_for_packet_only_agents(self, repo, packet):
        run_dir = write_agent_packet(packet, repo)
        catalog = json.loads(
            (run_dir / "skills-catalog.json").read_text(encoding="utf-8")
        )
        applicable = (run_dir / "applicable-skills.md").read_text(encoding="utf-8")
        assert len(catalog) == len(_SKILL_KEY_TO_PATH)
        assert "test-driven-development" in {entry["key"] for entry in catalog}
        assert "Applicable SignalOS Skills" in applicable
        assert "Verification Before Completion" in applicable

    def test_live_agent_prompt_receives_applicable_skills(self, packet):
        prompt = _build_agent_prompt(packet, governance={})
        assert "## Applicable SignalOS Skills" in prompt
        assert "Test-Driven Development" in prompt
        assert "## Available SignalOS Skill Catalog" in prompt
        assert "`verification-before-completion`" in prompt
        assert "## Success Criteria" in prompt
        assert "## Evidence Required" in prompt
        assert "## Forbidden Rules (Hard Walls)" in prompt
        assert "## Repair/Rework Policy" in prompt
        assert "## SignalOS Team Contract" in prompt
        assert "not as a separate user-managed agent" in prompt

    def test_files_allowed_txt(self, repo, packet):
        run_dir = write_agent_packet(packet, repo)
        content = (run_dir / "files-allowed.txt").read_text(encoding="utf-8")
        assert "src/components/*" in content
        assert "src/types.ts" in content

    def test_validation_plan_includes_contract_fields(self, repo, packet):
        run_dir = write_agent_packet(packet, repo)
        plan = json.loads(
            (run_dir / "validation-plan.json").read_text(encoding="utf-8")
        )
        assert plan["success_criteria"] == packet["success_criteria"]
        assert plan["evidence_required"] == packet["evidence_required"]
        assert plan["forbidden_rules"] == packet["forbidden_rules"]
        assert plan["repair_policy"] == packet["repair_policy"]


# ---------------------------------------------------------------------------
# validate_agent_result
# ---------------------------------------------------------------------------

class TestValidateAgentResult:
    def _setup_run(self, repo, packet):
        """Write packet and return run_dir."""
        return write_agent_packet(packet, repo)

    def test_passes_for_result_within_allowed_paths(self, repo, packet):
        run_dir = self._setup_run(repo, packet)
        result = {
            "run_id": packet["run_id"],
            "status": "completed",
            "files_written": ["src/components/TaskList.tsx"],
            "actions_taken": [],
            "validation_results": {},
        }
        (run_dir / "RESULT.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        validation = validate_agent_result(run_dir, repo, None)
        assert validation["valid"] is True
        assert len(validation["violations"]) == 0
        assert validation["acceptance"] == "accepted"
        assert validation["next_action"] == "none"

    def test_fails_for_result_writing_to_forbidden_paths(self, repo, packet):
        run_dir = self._setup_run(repo, packet)
        result = {
            "run_id": packet["run_id"],
            "status": "completed",
            "files_written": [".signalos/hack.json"],
            "actions_taken": [],
            "validation_results": {},
        }
        (run_dir / "RESULT.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        validation = validate_agent_result(run_dir, repo, None)
        assert validation["valid"] is False
        assert any(".signalos" in v for v in validation["violations"])
        assert validation["forbidden_violated"] is True
        assert validation["acceptance"] == "rejected"
        assert validation["next_action"] == "regenerate_from_clean_packet"

    def test_fails_when_result_json_missing(self, repo, packet):
        run_dir = self._setup_run(repo, packet)
        # Don't create RESULT.json
        validation = validate_agent_result(run_dir, repo, None)
        assert validation["valid"] is False
        assert any("RESULT.json" in v for v in validation["violations"])

    def test_fails_for_invalid_json_result(self, repo, packet):
        run_dir = self._setup_run(repo, packet)
        (run_dir / "RESULT.json").write_text(
            "not valid json {{{{", encoding="utf-8"
        )
        validation = validate_agent_result(run_dir, repo, None)
        assert validation["valid"] is False
        assert any("invalid" in v.lower() or "JSON" in v for v in validation["violations"])

    def test_fails_when_result_missing_required_evidence_fields(self, repo, packet):
        run_dir = self._setup_run(repo, packet)
        result = {
            "run_id": packet["run_id"],
            "status": "completed",
            "files_written": ["src/components/TaskList.tsx"],
        }
        (run_dir / "RESULT.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        validation = validate_agent_result(run_dir, repo, None)
        assert validation["valid"] is False
        assert any("validation_results" in v for v in validation["violations"])
        assert validation["next_action"] == "repair_packet"

    def test_forbidden_file_modification_rejected(self, repo, packet):
        run_dir = self._setup_run(repo, packet)
        result = {
            "run_id": packet["run_id"],
            "status": "completed",
            "files_written": [".git/config", "secret.pem"],
            "actions_taken": [],
            "validation_results": {},
        }
        (run_dir / "RESULT.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        validation = validate_agent_result(run_dir, repo, None)
        assert validation["valid"] is False
        # Both .git/ and *.pem should be caught
        assert len(validation["violations"]) >= 2
        assert validation["next_action"] == "regenerate_from_clean_packet"

    def test_forbidden_actions_detected(self, repo, packet):
        run_dir = self._setup_run(repo, packet)
        result = {
            "run_id": packet["run_id"],
            "status": "completed",
            "files_written": ["src/components/Foo.tsx"],
            "actions_taken": ["git push origin main"],
            "validation_results": {},
        }
        (run_dir / "RESULT.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        validation = validate_agent_result(run_dir, repo, None)
        assert validation["valid"] is False
        assert any("git push" in v for v in validation["violations"])
        assert validation["forbidden_violated"] is True

    def test_env_file_forbidden(self, repo, packet):
        run_dir = self._setup_run(repo, packet)
        result = {
            "run_id": packet["run_id"],
            "status": "completed",
            "files_written": [".env"],
            "actions_taken": [],
            "validation_results": {},
        }
        (run_dir / "RESULT.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        validation = validate_agent_result(run_dir, repo, None)
        assert validation["valid"] is False
        assert any(".env" in v for v in validation["violations"])
        assert validation["next_action"] == "regenerate_from_clean_packet"

    def test_fails_when_packet_contract_missing(self, repo, packet):
        run_dir = self._setup_run(repo, packet)
        scope_path = run_dir / "scope.json"
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
        del scope["success_criteria"]
        scope_path.write_text(json.dumps(scope), encoding="utf-8")
        result = {
            "run_id": packet["run_id"],
            "status": "completed",
            "files_written": ["src/components/TaskList.tsx"],
            "actions_taken": [],
            "validation_results": {},
        }
        (run_dir / "RESULT.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        validation = validate_agent_result(run_dir, repo, None)
        assert validation["valid"] is False
        assert any("success_criteria" in v for v in validation["violations"])
        assert validation["next_action"] == "repair_packet"


# ---------------------------------------------------------------------------
# run_repair_loop
# ---------------------------------------------------------------------------

class TestRunRepairLoop:
    def _failed_validation(self) -> dict:
        return {
            "valid": False,
            "checks": [
                {"name": "result_valid_json", "passed": True, "detail": "ok"},
                {"name": "files_within_allowed", "passed": False,
                 "detail": "1 file(s) outside allowed paths"},
            ],
            "violations": ["File 'bad/path.txt' not within allowed paths"],
        }

    def test_mode_none_returns_manual_repair_needed(self, repo):
        result = run_repair_loop(
            repo_root=repo,
            validation_result=self._failed_validation(),
            profile="generic",
            agent_mode="none",
        )
        assert result["status"] == "manual_repair_needed"
        assert result["cycles_used"] == 1
        assert result["repairs"][0]["action"] == "skipped"

    def test_mode_packet_only_creates_repair_packet(self, repo, packet):
        # Write the original packet so repair_loop can find it
        write_agent_packet(packet, repo)

        result = run_repair_loop(
            repo_root=repo,
            validation_result=self._failed_validation(),
            profile="generic",
            agent_mode="packet-only",
        )
        assert result["status"] == "awaiting_agent"
        assert result["cycles_used"] == 1
        assert result["repairs"][0]["action"] == "packet_created"
        assert result["repairs"][0]["packet_path"] is not None
        assert result["repairs"][0]["repair_type"] == "regenerate_from_clean_packet"

    def test_respects_max_cycles_limit(self, repo):
        result = run_repair_loop(
            repo_root=repo,
            validation_result=self._failed_validation(),
            profile="generic",
            max_cycles=2,
            agent_mode="none",
        )
        assert result["cycles_used"] <= 2
        assert result["max_cycles"] == 2

    def test_max_cycles_zero_returns_immediately(self, repo):
        result = run_repair_loop(
            repo_root=repo,
            validation_result=self._failed_validation(),
            profile="generic",
            max_cycles=0,
        )
        assert result["status"] == "max_cycles_reached"
        assert result["cycles_used"] == 0
        assert result["repairs"] == []

    def test_already_valid_returns_repaired(self, repo):
        valid_result = {"valid": True, "checks": [], "violations": []}
        result = run_repair_loop(
            repo_root=repo,
            validation_result=valid_result,
            profile="generic",
        )
        assert result["status"] == "repaired"
        assert result["cycles_used"] == 0


# ---------------------------------------------------------------------------
# build_repair_packet / write_repair_packet
# ---------------------------------------------------------------------------

class TestRepairPacket:
    def test_repair_packet_includes_failure_context(self, repo):
        failures = ["File 'x.txt' outside allowed paths", "Forbidden action: deploy"]
        logs = json.dumps([{"name": "check", "passed": False}])
        original = {
            "run_id": str(uuid.uuid4()),
            "profile": "generic",
            "wave": "1",
            "intent_summary": {"product_name": "Test"},
            "tasks": [{"id": "T1", "title": "Fix"}],
            "allowed_paths": ["src/*"],
            "forbidden_paths": [".signalos/"],
            "forbidden_actions": ["deploy"],
            "validation_commands": [],
        }
        packet = build_repair_packet(
            repo_root=repo,
            cycle=1,
            failures=failures,
            validation_logs=logs,
            original_packet=original,
        )
        assert packet["schema_version"] == "signalos.repair_packet.v1"
        assert packet["repair_cycle"] == 1
        assert packet["failures"] == failures
        assert packet["validation_logs"] == logs
        assert packet["run_id"] == original["run_id"]
        assert packet["tasks"] == original["tasks"]

    def test_write_repair_packet_creates_files(self, repo):
        run_dir = repo / ".signalos" / "product" / "agent-runs" / "test-run"
        run_dir.mkdir(parents=True)
        packet = {
            "schema_version": "signalos.repair_packet.v1",
            "run_id": "test-run",
            "repair_cycle": 2,
            "created_at": "2026-01-01T00:00:00+00:00",
            "profile": "generic",
            "wave": "1",
            "failures": ["something broke"],
            "validation_logs": "[]",
            "intent_summary": {},
            "tasks": [],
            "allowed_paths": [],
            "forbidden_paths": [],
            "forbidden_actions": [],
            "validation_commands": [],
        }
        repair_dir = write_repair_packet(packet, run_dir, 2)
        assert (repair_dir / "repair-scope.json").is_file()
        assert (repair_dir / "REPAIR.md").is_file()
        md = (repair_dir / "REPAIR.md").read_text(encoding="utf-8")
        assert "Repair Cycle 2" in md
        assert "something broke" in md
