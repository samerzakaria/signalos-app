from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.cli import _build_parser, main as cli_main
from signalos_lib.commands import test_automation


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_test_command_is_registered_in_cli() -> None:
    parser = _build_parser()
    choices = {}
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices)
    assert "test" in choices


def test_dry_run_all_phases_writes_evidence_and_can_emit_audit(tmp_path: Path) -> None:
    rc = test_automation.main([
        "all",
        "--repo-root",
        str(tmp_path),
        "--dry-run",
        "--emit-audit",
        "--json",
    ])

    assert rc == test_automation.EXIT_OK
    all_path = tmp_path / ".signalos" / "quality" / "test-automation" / "all.json"
    assert all_path.is_file()
    payload = json.loads(all_path.read_text(encoding="utf-8"))
    assert payload["status"] == "dry-run"
    assert len(payload["phases"]) == len(test_automation.PHASES)
    audit = tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl"
    rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert {row["action"] for row in rows} >= {
        "test.unit.executed",
        "test.integration.executed",
        "test.governance.executed",
    }


def test_all_phase_status_distinguishes_not_applicable_from_passed() -> None:
    statuses = ["pass"] * 10 + ["not-applicable", "not-applicable"]

    assert test_automation._aggregate_status(statuses) == "pass-with-not-applicable"


def test_unit_phase_accepts_existing_validation_result(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".signalos" / "product" / "VALIDATION_RESULT.json",
        {
            "schema_version": "signalos.validation_result.v1",
            "results": {"test": {"status": "passed", "output": "ok"}},
        },
    )

    payload = test_automation.run_test_phase(tmp_path, phase="unit")

    assert payload["status"] == "pass"
    assert payload["ok"] is True
    assert payload["checks"][0]["id"] == "validation-test"
    assert (tmp_path / ".signalos" / "quality" / "test-automation" / "unit.json").is_file()


def test_missing_contract_evidence_is_threshold_violation(tmp_path: Path) -> None:
    rc = test_automation.main(["contract", "--repo-root", str(tmp_path), "--json"])

    assert rc == test_automation.EXIT_THRESHOLD_VIOLATION
    payload = json.loads(
        (tmp_path / ".signalos" / "quality" / "test-automation" / "contract.json")
        .read_text(encoding="utf-8")
    )
    assert payload["status"] == "blocked"
    assert payload["blockers"]


def test_audit_row_can_satisfy_evidence_phase(tmp_path: Path) -> None:
    audit = tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        json.dumps({
            "ts": "2026-06-29T00:00:00Z",
            "action": "test.integration.executed",
            "status": "pass",
        }) + "\n",
        encoding="utf-8",
    )

    payload = test_automation.run_test_phase(tmp_path, phase="integration")

    assert payload["status"] == "pass"
    assert payload["checks"][0]["id"] == "audit-evidence"


def test_governance_phase_validates_audit_and_artifacts(tmp_path: Path) -> None:
    audit = tmp_path / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        json.dumps({"ts": "2026-06-29T00:00:00Z", "action": "init", "status": "pass"}) + "\n",
        encoding="utf-8",
    )
    constitution = tmp_path / "core" / "governance" / "Governance" / "CONSTITUTION.md"
    constitution.parent.mkdir(parents=True, exist_ok=True)
    constitution.write_text("# Constitution\n", encoding="utf-8")

    payload = test_automation.run_test_phase(tmp_path, phase="governance")

    assert payload["status"] == "pass"
    assert {check["id"] for check in payload["checks"]} >= {
        "audit-trail-jsonl",
        "governance-artifacts",
    }


def test_top_level_cli_reaches_test_command(tmp_path: Path) -> None:
    rc = cli_main([
        "signalos",
        "test",
        "governance",
        "--repo-root",
        str(tmp_path),
        "--dry-run",
    ])

    assert rc == test_automation.EXIT_OK
    assert (tmp_path / ".signalos" / "quality" / "test-automation" / "governance.json").is_file()


def test_security_phase_runs_app_native_gate(tmp_path: Path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("print('safe')\n", encoding="utf-8")

    payload = test_automation.run_test_phase(tmp_path, phase="security")

    assert payload["phase"] == "security"
    assert payload["checks"][0]["id"] == "security-gate"
    assert (tmp_path / ".signalos" / "product" / "SECURITY_RESULT.json").is_file()


def test_contract_phase_passes_with_compatibility_verdict(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "contracts" / "openapi.json",
        {
            "openapi": "3.1.0",
            "info": {"title": "SignalOS Product API", "version": "1.0.0"},
            "paths": {"/health": {"get": {"responses": {"200": {"description": "ok"}}}}},
            # SignalOS enforces, never advises: an explicit compatibility
            # verdict is required for a contract pass.
            "breaking_changes": [],
        },
    )

    payload = test_automation.run_test_phase(tmp_path, phase="contract")

    assert payload["status"] == "pass"
    assert payload["checks"][0]["id"] == "contract-producer"


def test_contract_phase_blocks_openapi_presence_without_verdict(tmp_path: Path) -> None:
    # Negative: key-presence ("openapi" in data) alone must NOT be a pass.
    _write_json(
        tmp_path / "contracts" / "openapi.json",
        {
            "openapi": "3.1.0",
            "info": {"title": "SignalOS Product API", "version": "1.0.0"},
            "paths": {"/health": {"get": {"responses": {"200": {"description": "ok"}}}}},
        },
    )

    rc = test_automation.main(["contract", "--repo-root", str(tmp_path), "--json"])

    assert rc == test_automation.EXIT_THRESHOLD_VIOLATION
    payload = json.loads(
        (tmp_path / ".signalos" / "quality" / "test-automation" / "contract.json")
        .read_text(encoding="utf-8")
    )
    assert payload["status"] == "blocked"
    assert payload["checks"][0]["id"] == "contract-producer"
    assert payload["blockers"]


def test_contract_phase_fails_on_breaking_changes(tmp_path: Path) -> None:
    # Negative: an explicit breaking-change verdict must block (exit 8).
    _write_json(
        tmp_path / "contracts" / "openapi.json",
        {
            "openapi": "3.1.0",
            "info": {"title": "SignalOS Product API", "version": "1.0.0"},
            "breaking_changes": ["removed GET /v1/orders"],
        },
    )

    rc = test_automation.main(["contract", "--repo-root", str(tmp_path), "--json"])

    assert rc == test_automation.EXIT_THRESHOLD_VIOLATION
    payload = json.loads(
        (tmp_path / ".signalos" / "quality" / "test-automation" / "contract.json")
        .read_text(encoding="utf-8")
    )
    assert payload["status"] == "fail"
    assert payload["checks"][0]["id"] == "contract-producer"


def test_visual_phase_passes_with_report_and_baseline_comparison(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".signalos" / "quality" / "p05-visual" / "result.json",
        {"status": "passed", "failed": 0, "baseline": "main", "diffs": 0},
    )

    payload = test_automation.run_test_phase(tmp_path, phase="visual", profile="react-vite")

    assert payload["status"] == "pass"
    assert payload["checks"][0]["id"] == "visual-producer"


def test_visual_phase_blocks_screenshots_without_report(tmp_path: Path) -> None:
    # Negative: screenshot presence alone is NOT a pass; it must block (exit 8).
    shot = tmp_path / "tests" / "e2e" / "__screenshots__" / "dashboard.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")

    rc = test_automation.main(
        ["visual", "--repo-root", str(tmp_path), "--profile", "react-vite", "--json"]
    )

    assert rc == test_automation.EXIT_THRESHOLD_VIOLATION
    payload = json.loads(
        (tmp_path / ".signalos" / "quality" / "test-automation" / "visual.json")
        .read_text(encoding="utf-8")
    )
    assert payload["status"] == "blocked"
    assert payload["checks"][0]["id"] == "visual-producer"
    assert payload["blockers"]


def test_visual_phase_blocks_pass_verdict_without_baseline(tmp_path: Path) -> None:
    # Negative: a passing report with no baseline comparison proves nothing.
    _write_json(
        tmp_path / ".signalos" / "quality" / "p05-visual" / "result.json",
        {"status": "passed", "failed": 0},
    )

    payload = test_automation.run_test_phase(tmp_path, phase="visual", profile="react-vite")

    assert payload["status"] == "blocked"
    assert payload["checks"][0]["id"] == "visual-producer"


def test_visual_phase_fails_when_visual_report_has_diffs(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".signalos" / "quality" / "p05-visual" / "result.json",
        {"status": "failed", "failed": 2, "baseline": "main"},
    )

    payload = test_automation.run_test_phase(tmp_path, phase="visual", profile="react-vite")

    assert payload["status"] == "fail"
    assert payload["checks"][0]["id"] == "visual-producer"


def test_chaos_phase_parses_result_json(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".signalos" / "chaos" / "results.json",
        {"status": "passed", "experiments_total": 3, "experiments_failed": 0},
    )

    payload = test_automation.run_test_phase(tmp_path, phase="chaos")

    assert payload["status"] == "pass"
    assert payload["checks"][0]["id"] == "chaos-producer"


def test_chaos_phase_blocks_required_manifests_without_results(tmp_path: Path) -> None:
    constitution = tmp_path / ".signalos" / "CONSTITUTION.md"
    constitution.parent.mkdir(parents=True, exist_ok=True)
    constitution.write_text("chaos_testing_required: true\n", encoding="utf-8")
    manifest = tmp_path / "chaos" / "experiment.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("kind: ChaosEngine\n", encoding="utf-8")

    payload = test_automation.run_test_phase(tmp_path, phase="chaos")

    assert payload["status"] == "blocked"
    assert payload["checks"][0]["id"] == "chaos-producer"


def test_production_monitor_phase_parses_slo_metrics(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".signalos" / "observability" / "production-monitor.json",
        {"status": "passed", "burn_rate_5m": 0.2, "burn_rate_1h": 0.5, "burn_rate_threshold": 1.0},
    )

    payload = test_automation.run_test_phase(tmp_path, phase="production-monitor")

    assert payload["status"] == "pass"
    assert payload["checks"][0]["id"] == "production-monitor-producer"


def test_production_monitor_phase_fails_over_slo_threshold(tmp_path: Path) -> None:
    _write_json(
        tmp_path / ".signalos" / "observability" / "production-monitor.json",
        {"status": "passed", "burn_rate_5m": 1.5, "burn_rate_1h": 0.5, "burn_rate_threshold": 1.0},
    )

    payload = test_automation.run_test_phase(tmp_path, phase="production-monitor")

    assert payload["status"] == "fail"
    assert payload["checks"][0]["id"] == "production-monitor-producer"


def test_generic_profile_does_not_waive_ui_phases(tmp_path: Path) -> None:
    # Item 1: a generic/undeclared product must RUN e2e + visual (not silently
    # mark them not-applicable) and, lacking evidence, block via exit 8.
    e2e = test_automation.run_test_phase(tmp_path, phase="e2e", profile="generic")
    visual = test_automation.run_test_phase(tmp_path, phase="visual", profile="generic")

    assert e2e["status"] != "not-applicable"
    assert visual["status"] != "not-applicable"
    assert e2e["status"] in {"blocked", "pending", "fail"}
    assert visual["status"] in {"blocked", "pending", "fail"}
    # No ui-applicability waiver check should be present.
    assert all(check["id"] != "ui-applicability" for check in e2e["checks"])
    assert all(check["id"] != "ui-applicability" for check in visual["checks"])


def test_generic_profile_test_all_blocks_on_missing_ui_evidence(tmp_path: Path) -> None:
    # Default profile is "generic"; `test all` must NOT pass-with-not-applicable.
    rc = test_automation.main(["all", "--repo-root", str(tmp_path), "--json"])

    assert rc == test_automation.EXIT_THRESHOLD_VIOLATION
    payload = json.loads(
        (tmp_path / ".signalos" / "quality" / "test-automation" / "all.json")
        .read_text(encoding="utf-8")
    )
    assert payload["profile"] == "generic"
    assert payload["status"] != "pass-with-not-applicable"
    by_phase = {item["phase"]: item["status"] for item in payload["phases"]}
    assert by_phase["e2e"] != "not-applicable"
    assert by_phase["visual"] != "not-applicable"


def test_api_profile_still_waives_ui_phases(tmp_path: Path) -> None:
    # Only explicitly API-only profiles are treated as no-UI.
    e2e = test_automation.run_test_phase(tmp_path, phase="e2e", profile="fastapi-api")
    visual = test_automation.run_test_phase(tmp_path, phase="visual", profile="fastapi-api")

    assert e2e["status"] == "not-applicable"
    assert visual["status"] == "not-applicable"
    assert e2e["checks"][0]["id"] == "ui-applicability"
    assert visual["checks"][0]["id"] == "ui-applicability"


def test_human_output_surfaces_not_applicable_phases(tmp_path: Path, capsys) -> None:
    # Item 2: the human CLI output (not just JSON) must state what was waived.
    rc = test_automation.main([
        "all",
        "--repo-root",
        str(tmp_path),
        "--profile",
        "fastapi-api",
    ])

    out = capsys.readouterr().out
    assert rc == test_automation.EXIT_THRESHOLD_VIOLATION  # other phases still block
    assert "phase(s) not applicable:" in out
    assert "applicable phases passed" in out
    # The waived phase names must appear by name.
    assert "P4 E2E UI" in out
    assert "P5 Visual Regression" in out
