from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.cli import _build_parser, main as cli_main
from signalos_lib.product.blueprints.factory import (
    draft_blueprint,
    register_blueprint,
    review_blueprint,
)
from signalos_lib.product.blueprints.registry import (
    list_blueprints,
    load_blueprint,
    match_blueprint,
    validate_blueprint,
    validate_blueprint_registry,
)
from signalos_lib.product.delivery import run_delivery


def _write_intent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "product_name": "Field Service Operations",
            "product_type": "field-service",
            "entities": ["work order", "technician"],
            "primary_workflows": ["schedule work order", "close service visit"],
            "api_surfaces": ["/api/work-orders"],
            "ux_surfaces": ["dispatch board"],
        }) + "\n",
        encoding="utf-8",
    )


def test_custom_blueprint_draft_review_register_round_trip(tmp_path: Path) -> None:
    intent_path = tmp_path / ".signalos" / "product" / "INTENT.json"
    _write_intent(intent_path)

    draft = draft_blueprint(tmp_path, "field-service", from_intent=intent_path)
    assert draft["status"] == "drafted"
    draft_dir = tmp_path / ".signalos" / "product" / "blueprint-drafts" / "field-service"
    assert (draft_dir / "blueprint.json").is_file()
    for name in (
        "DOMAIN_LANGUAGE.md",
        "PROVIDER_REQUEST.json",
        "TOOL_SELECTION.json",
        "SKILL_ROUTING.yaml",
        "PRODUCT_PIPELINE.yaml",
        "SUBAGENT_HARNESS.json",
        "GOVERNANCE_GATES.json",
    ):
        assert (draft_dir / name).is_file(), name

    review = review_blueprint(tmp_path, "field-service", verdict="approve", notes="domain shape approved")
    assert review["status"] == "approved"
    assert review["ok"] is True
    gates = json.loads((draft_dir / "GOVERNANCE_GATES.json").read_text(encoding="utf-8"))
    by_gate = {gate["id"]: gate for gate in gates["gates"]}
    assert by_gate["G2-review"]["status"] == "pass"
    assert by_gate["G3-validation"]["status"] == "pass"

    registered = register_blueprint(tmp_path, "field-service")
    assert registered["status"] == "registered"
    assert registered["ok"] is True
    custom_dir = tmp_path / ".signalos" / "product" / "blueprints" / "custom" / "field-service"
    assert (custom_dir / "DOMAIN_LANGUAGE.md").is_file()
    custom_gates = json.loads((custom_dir / "GOVERNANCE_GATES.json").read_text(encoding="utf-8"))
    custom_by_gate = {gate["id"]: gate for gate in custom_gates["gates"]}
    assert custom_by_gate["G4-registration"]["status"] == "pass"
    assert custom_by_gate["G5-adoption-proof"]["status"] == "pass"

    entries = list_blueprints(tmp_path)
    custom = next(entry for entry in entries if entry["id"] == "field-service")
    assert custom["origin"] == "custom"

    blueprint = load_blueprint("field-service", tmp_path)
    assert blueprint is not None
    assert blueprint["display_name"] == "Field Service Operations"
    assert validate_blueprint(blueprint) == []
    assert match_blueprint({"product_type": "field-service", "entities": []}, repo_root=tmp_path) == "field-service"

    report = validate_blueprint_registry(tmp_path, "field-service")
    assert report["ok"] is True
    assert report["summary"]["valid"] == 1


def test_register_requires_approved_review(tmp_path: Path) -> None:
    draft_blueprint(tmp_path, "service-desk")
    try:
        register_blueprint(tmp_path, "service-desk")
    except ValueError as exc:
        assert "approved REVIEW.json" in str(exc)
    else:
        raise AssertionError("register_blueprint unexpectedly allowed an unreviewed draft")


def test_blueprint_review_request_changes_writes_bounded_rework_packet(tmp_path: Path) -> None:
    draft_blueprint(tmp_path, "service-desk")
    review = review_blueprint(tmp_path, "service-desk", verdict="request-changes", notes="tighten workflows")

    draft_dir = tmp_path / ".signalos" / "product" / "blueprint-drafts" / "service-desk"
    assert review["status"] == "rework-required"
    assert (draft_dir / "REWORK_1.md").is_file()
    gates = json.loads((draft_dir / "GOVERNANCE_GATES.json").read_text(encoding="utf-8"))
    by_gate = {gate["id"]: gate for gate in gates["gates"]}
    assert by_gate["G2-review"]["status"] == "blocked"


def test_registered_custom_blueprint_can_drive_delivery(tmp_path: Path) -> None:
    intent_path = tmp_path / ".signalos" / "product" / "INTENT.json"
    _write_intent(intent_path)
    draft_blueprint(tmp_path, "field-service", from_intent=intent_path)
    review_blueprint(tmp_path, "field-service", verdict="approve")
    register_blueprint(tmp_path, "field-service")

    closeout = run_delivery(
        prompt="Build a field service product for work orders",
        name="generated-field-service",
        repo_root=tmp_path,
        mode="greenfield",
        profile="generic",
        blueprint="field-service",
        deploy="none",
        dry_run=True,
    )

    intent = json.loads(
        (tmp_path / ".signalos" / "product" / "INTENT.json").read_text(encoding="utf-8")
    )
    assert closeout["blueprint"] == "field-service"
    assert intent["product_type"] == "field-service"
    assert "WorkOrder" in intent["entities"]


def test_product_blueprint_cli_workflow(tmp_path: Path, capsys) -> None:
    intent_path = tmp_path / ".signalos" / "product" / "INTENT.json"
    _write_intent(intent_path)

    parser = _build_parser()
    commands: set[str] = set()
    for action in parser._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            commands = set(action.choices)
            break
    assert "product" in commands

    rc = cli_main([
        "signalos",
        "product",
        "blueprint",
        "draft",
        "--repo-root",
        str(tmp_path),
        "--id",
        "field-service",
        "--from-intent",
        str(intent_path),
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "drafted"

    rc = cli_main([
        "signalos",
        "product",
        "blueprint",
        "review",
        "--repo-root",
        str(tmp_path),
        "--id",
        "field-service",
        "--verdict",
        "approve",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "approved"

    rc = cli_main([
        "signalos",
        "product",
        "blueprint",
        "register",
        "--repo-root",
        str(tmp_path),
        "--id",
        "field-service",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "registered"

    rc = cli_main([
        "signalos",
        "product",
        "blueprint",
        "inspect",
        "--repo-root",
        str(tmp_path),
        "--id",
        "field-service",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["id"] == "field-service"
    assert payload["origin"] == "custom"

    rc = cli_main([
        "signalos",
        "product",
        "blueprint",
        "validate",
        "--repo-root",
        str(tmp_path),
        "--id",
        "field-service",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "PASS"

    rc = cli_main([
        "signalos",
        "product",
        "blueprint",
        "list",
        "--repo-root",
        str(tmp_path),
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert any(item["id"] == "field-service" for item in payload["blueprints"])
