"""Tests for brownfield governance application."""

from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.product.brownfield import apply_governance, audit_existing_repo


def _bare_repo(tmp_path: Path) -> Path:
    # A minimal repo with a package.json marker but none of the governance basics.
    (tmp_path / "package.json").write_text('{"name":"x","dependencies":{}}', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.js").write_text("export const x = 1;\n", encoding="utf-8")
    return tmp_path


def _well_governed_repo(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("# x", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("MIT", encoding="utf-8")
    (tmp_path / "SECURITY.md").write_text("report to ...", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("node_modules\n.env\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.test.js").write_text("test('x', () => {});", encoding="utf-8")
    return tmp_path


def test_bare_repo_flags_core_gaps(tmp_path):
    audit = audit_existing_repo(_bare_repo(tmp_path))
    areas = {f["area"] for f in audit["findings"]}
    for expected in ("testing", "ci", "license", "dependencies", "docs"):
        assert expected in areas, f"expected a {expected} finding"
    assert audit["summary"]["high"] >= 2  # testing + ci
    assert audit["summary"]["governed"] is False


def test_findings_sorted_high_first(tmp_path):
    audit = audit_existing_repo(_bare_repo(tmp_path))
    rank = {"high": 0, "medium": 1, "low": 2}
    ranks = [rank[f["severity"]] for f in audit["findings"]]
    assert ranks == sorted(ranks)


def test_detects_hardcoded_secret(tmp_path):
    repo = _bare_repo(tmp_path)
    # Build the fake secret at runtime so the literal never lands in the repo.
    secret_line = "const apiKey = " + '"' + ("a" * 24) + '";\n'
    (repo / "src" / "leak.js").write_text(secret_line, encoding="utf-8")
    audit = audit_existing_repo(repo)
    assert any(f["area"] == "secrets" for f in audit["findings"])


def test_well_governed_repo_is_clean(tmp_path):
    audit = audit_existing_repo(_well_governed_repo(tmp_path))
    assert audit["summary"]["governed"] is True
    assert audit["findings"] == []


def test_gitignore_without_env_is_flagged(tmp_path):
    repo = _well_governed_repo(tmp_path)
    (repo / ".gitignore").write_text("node_modules\n", encoding="utf-8")  # no .env
    audit = audit_existing_repo(repo)
    assert any(f["area"] == "ignore-rules" for f in audit["findings"])


def test_apply_governance_writes_artifacts_and_audit(tmp_path):
    repo = _bare_repo(tmp_path)
    result = apply_governance(repo)
    # Scaffold + baseline written, source preserved.
    assert (repo / ".signalos" / "profile.json").is_file()
    assert (repo / ".signalos" / "GOVERNANCE_BASELINE.md").is_file()
    assert "src" in result["preserved"]
    assert result["audit"]["summary"]["total"] >= 1
    # Audit trail records the application (replayable by E3).
    trail = (repo / ".signalos" / "AUDIT_TRAIL.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(l) for l in trail.splitlines() if l.strip()]
    assert any(r["action"] == "brownfield.governance-applied" for r in rows)


def test_apply_governance_does_not_touch_source(tmp_path):
    repo = _bare_repo(tmp_path)
    before = (repo / "src" / "index.js").read_text(encoding="utf-8")
    apply_governance(repo)
    assert (repo / "src" / "index.js").read_text(encoding="utf-8") == before


def test_baseline_for_clean_repo_says_so(tmp_path):
    repo = _well_governed_repo(tmp_path)
    apply_governance(repo)
    baseline = (repo / ".signalos" / "GOVERNANCE_BASELINE.md").read_text(encoding="utf-8")
    assert "already meets the baseline" in baseline
