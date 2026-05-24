"""Tests for signalos_lib.product.security_gate module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.security_gate import (
    detect_pii_entities,
    get_compliance_requirements,
    load_security_result,
    run_security_gate,
    write_security_result,
)


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a minimal repo structure for testing."""
    signalos_dir = tmp_path / ".signalos"
    signalos_dir.mkdir(parents=True)
    (signalos_dir / "product").mkdir(parents=True)
    (signalos_dir / "security").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def base_intent() -> dict:
    return {
        "product_name": "TestApp",
        "product_type": "custom",
        "entities": [],
        "primary_workflows": [],
        "ux_surfaces": [],
        "api_surfaces": [],
        "security_constraints": [],
        "auth_requirements": [],
        "audit_requirements": [],
    }


# ---------------------------------------------------------------------------
# Clean files -> passed
# ---------------------------------------------------------------------------


def test_clean_files_return_passed(tmp_repo: Path, base_intent: dict):
    """Clean generated files produce status=passed."""
    clean_file = tmp_repo / "src" / "app.py"
    clean_file.parent.mkdir(parents=True, exist_ok=True)
    clean_file.write_text("def main():\n    print('hello')\n", encoding="utf-8")

    result = run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=["src/app.py"],
        profile="python",
    )
    assert result["status"] == "passed"
    assert result["injection_scan"]["files_scanned"] == 1
    assert result["injection_scan"]["issues_found"] == []


# ---------------------------------------------------------------------------
# Injection detection: eval
# ---------------------------------------------------------------------------


def test_eval_detected(tmp_repo: Path, base_intent: dict):
    """File containing eval( returns status=failed with injection issue."""
    bad_file = tmp_repo / "src" / "danger.py"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("result = eval(user_input)\n", encoding="utf-8")

    result = run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=["src/danger.py"],
        profile="python",
    )
    assert result["status"] == "failed"
    issues = result["injection_scan"]["issues_found"]
    assert len(issues) >= 1
    assert any("eval" in i["risk"].lower() for i in issues)


# ---------------------------------------------------------------------------
# Injection detection: dangerouslySetInnerHTML
# ---------------------------------------------------------------------------


def test_dangerously_set_inner_html_detected(tmp_repo: Path, base_intent: dict):
    """TSX file with dangerouslySetInnerHTML is flagged."""
    bad_file = tmp_repo / "src" / "Component.tsx"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text(
        '<div dangerouslySetInnerHTML={{__html: data}} />\n',
        encoding="utf-8",
    )

    result = run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=["src/Component.tsx"],
        profile="react-vite",
    )
    assert result["status"] == "failed"
    issues = result["injection_scan"]["issues_found"]
    assert any("dangerouslySetInnerHTML" in i["risk"] for i in issues)


# ---------------------------------------------------------------------------
# Injection detection: os.system
# ---------------------------------------------------------------------------


def test_os_system_detected(tmp_repo: Path, base_intent: dict):
    """File with os.system( is flagged."""
    bad_file = tmp_repo / "src" / "runner.py"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("import os\nos.system(cmd)\n", encoding="utf-8")

    result = run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=["src/runner.py"],
        profile="python",
    )
    assert result["status"] == "failed"
    issues = result["injection_scan"]["issues_found"]
    assert any("os.system" in i["risk"].lower() for i in issues)


# ---------------------------------------------------------------------------
# PII detection
# ---------------------------------------------------------------------------


def test_detect_pii_entities_finds_patient():
    """detect_pii_entities finds 'Patient' as PII."""
    assert detect_pii_entities(["Patient"]) == ["Patient"]


def test_detect_pii_entities_empty_for_non_pii():
    """Non-PII entities like 'Task', 'Project' return empty."""
    assert detect_pii_entities(["Task", "Project", "Sprint"]) == []


# ---------------------------------------------------------------------------
# GDPR flagging
# ---------------------------------------------------------------------------


def test_gdpr_flagged_with_pii_entities(tmp_repo: Path, base_intent: dict):
    """GDPR is flagged when intent has PII entities."""
    base_intent["entities"] = ["Patient", "User"]

    result = run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=[],
        profile="python",
    )
    assert result["gdpr_required"] is True
    assert "Patient" in result["gdpr_reason"]


def test_gdpr_not_flagged_for_non_pii(tmp_repo: Path, base_intent: dict):
    """GDPR not flagged for non-PII products."""
    base_intent["entities"] = ["Task", "Project", "Board"]

    result = run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=[],
        profile="python",
    )
    assert result["gdpr_required"] is False
    assert result["gdpr_reason"] is None


# ---------------------------------------------------------------------------
# Compliance requirements
# ---------------------------------------------------------------------------


def test_compliance_from_security_constraints():
    """Compliance requirements derived from security_constraints."""
    intent = {
        "security_constraints": ["hipaa", "gdpr"],
        "auth_requirements": [],
        "entities": [],
    }
    reqs = get_compliance_requirements(intent)
    assert "HIPAA" in reqs
    assert "GDPR" in reqs


def test_hipaa_in_intent_yields_hipaa_compliance():
    """'HIPAA' in intent produces 'HIPAA' in compliance_requirements."""
    intent = {
        "security_constraints": ["hipaa"],
        "auth_requirements": [],
        "entities": [],
    }
    reqs = get_compliance_requirements(intent)
    assert "HIPAA" in reqs


# ---------------------------------------------------------------------------
# Canary token
# ---------------------------------------------------------------------------


def test_canary_planted(tmp_repo: Path, base_intent: dict):
    """Canary token file exists after run."""
    run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=[],
        profile="python",
    )
    canary_path = tmp_repo / ".signalos" / "security" / "canary-product.json"
    assert canary_path.exists()
    data = json.loads(canary_path.read_text(encoding="utf-8"))
    assert "token" in data


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


def test_write_load_security_result(tmp_repo: Path):
    """Security result round-trips through write/load."""
    signalos_dir = tmp_repo / ".signalos"
    result = {
        "status": "passed",
        "injection_scan": {"files_scanned": 3, "issues_found": []},
        "threat_model": None,
        "gdpr_required": False,
        "gdpr_reason": None,
        "canary_planted": True,
        "security_posture_declared": True,
        "compliance_requirements": [],
        "recommendations": [],
    }
    path = write_security_result(result, signalos_dir)
    assert path.exists()

    loaded = load_security_result(signalos_dir)
    assert loaded == result


# ---------------------------------------------------------------------------
# Threat model
# ---------------------------------------------------------------------------


def test_threat_model_generated_with_constraints(tmp_repo: Path, base_intent: dict):
    """Threat model is generated when security_constraints present."""
    base_intent["security_constraints"] = ["hipaa"]
    base_intent["ux_surfaces"] = ["dashboard"]

    result = run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=[],
        profile="python",
    )
    assert result["threat_model"] is not None
    assert result["threat_model"]["surfaces_scanned"] >= 1
    assert result["threat_model"]["threats_generated"] > 0


def test_threat_model_not_generated_without_constraints(tmp_repo: Path, base_intent: dict):
    """Threat model NOT generated when no security constraints."""
    result = run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=[],
        profile="python",
    )
    assert result["threat_model"] is None


# ---------------------------------------------------------------------------
# Medical records (full security treatment)
# ---------------------------------------------------------------------------


def test_medical_records_full_security(tmp_repo: Path):
    """Medical records intent with HIPAA + PII gets full security treatment."""
    intent = {
        "product_name": "MedRecords",
        "product_type": "custom",
        "entities": ["Patient", "MedicalRecord", "Appointment"],
        "primary_workflows": ["record patient intake"],
        "ux_surfaces": ["form", "table", "detail"],
        "api_surfaces": ["rest-api"],
        "security_constraints": ["hipaa"],
        "auth_requirements": ["rbac"],
        "audit_requirements": ["audit-trail"],
    }

    result = run_security_gate(
        repo_root=tmp_repo,
        intent=intent,
        generated_files=[],
        profile="react-vite",
    )
    assert result["gdpr_required"] is True
    assert result["threat_model"] is not None
    assert "HIPAA" in result["compliance_requirements"]
    assert result["canary_planted"] is True


# ---------------------------------------------------------------------------
# Recommendations non-empty when issues found
# ---------------------------------------------------------------------------


def test_recommendations_nonempty_on_issues(tmp_repo: Path, base_intent: dict):
    """Recommendations list is non-empty when issues found."""
    bad_file = tmp_repo / "src" / "bad.py"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("x = eval(input())\n", encoding="utf-8")

    result = run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=["src/bad.py"],
        profile="python",
    )
    assert len(result["recommendations"]) > 0


# ---------------------------------------------------------------------------
# Empty generated_files doesn't crash
# ---------------------------------------------------------------------------


def test_empty_generated_files_no_crash(tmp_repo: Path, base_intent: dict):
    """Empty generated_files list doesn't crash (returns passed with 0 scanned)."""
    result = run_security_gate(
        repo_root=tmp_repo,
        intent=base_intent,
        generated_files=[],
        profile="python",
    )
    assert result["status"] == "passed"
    assert result["injection_scan"]["files_scanned"] == 0
