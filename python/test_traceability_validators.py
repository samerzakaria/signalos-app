from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.cli import _build_parser, main as cli_main
from signalos_lib.validators.traceability import (
    validate_prd_traceability,
    validate_product_traceability,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commands() -> set[str]:
    parser = _build_parser()
    for action in parser._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            return set(action.choices)
    return set()


def _seed_valid_belief(root: Path) -> None:
    _write(root / "docs" / "prd.md", "# PRD\n\n## 1\nClaim source.\n")
    _write(
        root / ".signalos" / "Beliefs" / "B-N1.md",
        "```yaml\n"
        "belief_id: B-N1\n"
        "wave: W01\n"
        "scale_track: wave\n"
        "delivery_mode: fresh-wave\n"
        "designation: PRIMARY\n"
        "source_artifact: docs/prd.md\n"
        "source_section: PRD 1\n"
        "source_kind: PrdSection\n"
        "source_reference: PRD Section 1\n"
        "build_size: S\n"
        "author: PO\n"
        "date: 2026-06-29\n"
        "```\n\n"
        "## Problem\nUsers need traceable delivery claims.\n\n"
        "## Disproof condition\nA claim cannot resolve to evidence.\n\n"
        "## Bet Score\n"
        "| Component | Value |\n"
        "| --- | --- |\n"
        "| Risk (1-5) | 4 |\n"
        "| Impact (1-5) | 5 |\n"
        "| Test Cost (1-5) | 3 |\n"
        "| Bet Score | 6.7 |\n\n"
        "## Smallest Testable Build\nA validator command.\n\n"
        "## Signal threshold\n"
        "| Metric | Direction | Threshold | Window | Signal lag |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| traced claims | >= | 1 | W01 | 1 day |\n\n"
        "## Confidence bar\nMedium.\n\n"
        "## User served (primary)\n**MIN N:** 3 users.\n\n"
        "## Zone history\n| Wave | Zone |\n| --- | --- |\n| W01 | learning |\n",
    )
    _write(
        root / ".signalos" / "TRACEABILITY_MATRIX.md",
        "| Belief ID | Source Artifact | Source Section | Wave Anchor | Coverage Status | Notes |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| B-N1 | docs/prd.md | PRD 1 | W01 | covered | seed |\n",
    )
    _write(
        root / "core" / "governance" / "Governance" / "DECISION-DNA.md",
        "# Decision DNA\n\n## DEC-001\nApproved product traceability rule.\n",
    )


def _seed_valid_prd_traceability(root: Path) -> None:
    _write(
        root / ".signalos" / "PRD_TRACEABILITY.md",
        "| PRD Section | Claim | Destination | Target ID | Wave Anchor | Status | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| PRD 1 | Users can trace claims to evidence | BELIEF | B-N1 | W01 | covered | |\n"
        "| PRD 2 | Build the validator surface | BUILD | B-N1 / T-001 | W01 | covered | |\n"
        "| PRD 3 | This governance rule is settled | DEC | DEC-001 | W01 | covered | |\n"
        "| PRD 4 | Later dashboard polish | DEFER | DEFER -> W02 | W02 | deferred | backlog |\n"
        "| PRD 5 | Never support silent trace gaps | DEFER | DEFER -> never | W01 | closed | doctrine |\n",
    )


def test_validate_product_traceability_passes_with_source_matrix_and_belief(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    _seed_valid_belief(tmp_path)

    payload = validate_product_traceability(tmp_path)

    assert payload["ok"] is True
    assert payload["details"]["belief_count"] == 1
    assert payload["issues"] == []
    evidence = tmp_path / ".signalos" / "evidence" / "traceability" / "validate-traceability.json"
    assert evidence.is_file()
    assert json.loads(evidence.read_text(encoding="utf-8"))["evidence_path"] == payload["evidence_path"]


def test_validate_product_traceability_fails_on_missing_required_belief_evidence(tmp_path: Path) -> None:
    (tmp_path / ".signalos" / "Beliefs").mkdir(parents=True)
    _write(
        tmp_path / ".signalos" / "Beliefs" / "B-N1.md",
        "```yaml\nbelief_id: B-N1\nsource_artifact: docs/missing.md\n```\n\n## Problem\nOnly one section.\n",
    )
    _write(
        tmp_path / ".signalos" / "TRACEABILITY_MATRIX.md",
        "| Belief ID | Source Artifact | Source Section | Wave Anchor | Coverage Status | Notes |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| B-N1 | docs/other.md | PRD 1 | W01 | covered | seed |\n",
    )

    payload = validate_product_traceability(tmp_path, write_evidence=False)

    assert payload["ok"] is False
    codes = {issue["code"] for issue in payload["issues"]}
    assert "belief-front-matter-missing" in codes
    assert "belief-section-missing" in codes
    assert "belief-traceability-source-mismatch" in codes


def test_validate_product_traceability_fails_on_bet_score_arithmetic_mismatch(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    _seed_valid_belief(tmp_path)
    belief = tmp_path / ".signalos" / "Beliefs" / "B-N1.md"
    belief.write_text(
        belief.read_text(encoding="utf-8").replace("| Bet Score | 6.7 |", "| Bet Score | 9 |"),
        encoding="utf-8",
    )

    payload = validate_product_traceability(tmp_path, write_evidence=False)

    assert payload["ok"] is False
    codes = {issue["code"] for issue in payload["issues"]}
    assert "belief-bet-score-mismatch" in codes


def test_validate_product_traceability_fails_on_bet_score_without_components(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    _seed_valid_belief(tmp_path)
    belief = tmp_path / ".signalos" / "Beliefs" / "B-N1.md"
    text = belief.read_text(encoding="utf-8")
    start = text.index("## Bet Score")
    end = text.index("## Smallest Testable Build")
    belief.write_text(text[:start] + "## Bet Score\n7\n\n" + text[end:], encoding="utf-8")

    payload = validate_product_traceability(tmp_path, write_evidence=False)

    assert payload["ok"] is False
    codes = {issue["code"] for issue in payload["issues"]}
    assert "belief-bet-score-components-missing" in codes


def test_validate_prd_traceability_passes_for_live_destinations(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    _seed_valid_belief(tmp_path)
    _seed_valid_prd_traceability(tmp_path)

    payload = validate_prd_traceability(tmp_path)

    assert payload["ok"] is True
    assert payload["details"]["row_count"] == 5
    evidence = tmp_path / ".signalos" / "evidence" / "traceability" / "validate-prd-traceability.json"
    assert evidence.is_file()


def test_validate_prd_traceability_fails_on_orphan_targets(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    _write(
        tmp_path / ".signalos" / "PRD_TRACEABILITY.md",
        "| PRD Section | Claim | Destination | Target ID | Wave Anchor | Status | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| PRD 1 | Missing belief | BELIEF | B-N404 | W01 | open | |\n"
        "| PRD 2 | Missing decision | DEC | DEC-404 | W01 | open | |\n"
        "| PRD 3 | Unsupported destination | UNKNOWN | X-1 | W01 | open | |\n"
        "| PRD 4 | No justification | DEFER | DEFER -> never | W01 | open | |\n",
    )

    payload = validate_prd_traceability(tmp_path, write_evidence=False)

    assert payload["ok"] is False
    codes = {issue["code"] for issue in payload["issues"]}
    assert "prd-belief-target-missing" in codes
    assert "prd-decision-dna-missing" in codes
    assert "prd-destination-invalid" in codes
    assert "prd-defer-never-unjustified" in codes


def test_validate_prd_traceability_defer_id_is_case_sensitive(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    _write(
        tmp_path / ".signalos" / "PRD_TRACEABILITY.md",
        "| PRD Section | Claim | Destination | Target ID | Wave Anchor | Status | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| PRD 1 | Lowercase defer token | DEFER | defer -> W02 | W02 | open | backlog |\n",
    )

    payload = validate_prd_traceability(tmp_path, write_evidence=False)

    # Lowercase 'defer' must be rejected to mirror the .NET case-sensitive DeferIdRegex.
    assert payload["ok"] is False
    codes = {issue["code"] for issue in payload["issues"]}
    assert "prd-defer-id-invalid" in codes


def test_validate_prd_traceability_fails_on_malformed_row(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    _write(
        tmp_path / ".signalos" / "PRD_TRACEABILITY.md",
        "| PRD Section | Claim | Destination | Target ID | Wave Anchor | Status | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| PRD 1 | Smuggled claim | BELIEF |\n",  # malformed: only 3 columns, not 7
    )

    payload = validate_prd_traceability(tmp_path, write_evidence=False)

    assert payload["ok"] is False
    codes = {issue["code"] for issue in payload["issues"]}
    assert "prd-traceability-row-malformed" in codes
    malformed = [i for i in payload["issues"] if i["code"] == "prd-traceability-row-malformed"]
    assert malformed and malformed[0]["severity"] == "HALT"


def test_validate_product_traceability_fails_on_malformed_matrix_row(tmp_path: Path) -> None:
    (tmp_path / ".signalos").mkdir()
    _seed_valid_belief(tmp_path)
    # Overwrite the matrix with a malformed (column-count-mismatched) row.
    _write(
        tmp_path / ".signalos" / "TRACEABILITY_MATRIX.md",
        "| Belief ID | Source Artifact | Source Section | Wave Anchor | Coverage Status | Notes |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| B-N1 | docs/prd.md | PRD 1 |\n",  # malformed: 3 columns, not 6
    )

    payload = validate_product_traceability(tmp_path, write_evidence=False)

    assert payload["ok"] is False
    codes = {issue["code"] for issue in payload["issues"]}
    assert "traceability-row-malformed" in codes


def test_traceability_commands_are_exposed_and_dispatch_json(tmp_path: Path, capsys) -> None:
    (tmp_path / ".signalos").mkdir()
    _seed_valid_belief(tmp_path)
    _seed_valid_prd_traceability(tmp_path)

    commands = _commands()
    assert "validate-traceability" in commands
    assert "validate-prd-traceability" in commands

    rc = cli_main([
        "signalos",
        "validate-prd-traceability",
        "--repo-root",
        str(tmp_path),
        "--json",
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["schema_version"] == "signalos.validate_prd_traceability.v1"

    rc = cli_main([
        "signalos",
        "validate-traceability",
        "--repo-root",
        str(tmp_path),
        "--json",
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["schema_version"] == "signalos.validate_traceability.v1"
