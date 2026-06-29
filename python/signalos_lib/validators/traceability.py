"""App-native traceability validators.

These validators mirror the SignalOS.NET traceability concepts without taking
on .NET or ABP dependencies. They operate on local markdown artifacts:

* ``.signalos/PRD_TRACEABILITY.md`` links PRD claims to BELIEF/BUILD/DEC/DEFER.
* ``.signalos/TRACEABILITY_MATRIX.md`` links product Belief files to source
  artifacts and required provenance fields.
"""

from __future__ import annotations

__all__ = [
    "validate_prd_traceability",
    "validate_product_traceability",
]

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PRD_SCHEMA_VERSION = "signalos.validate_prd_traceability.v1"
PRODUCT_SCHEMA_VERSION = "signalos.validate_traceability.v1"

_ALLOWED_DESTINATIONS = {"BELIEF", "BUILD", "DEC", "DEFER"}
_BELIEF_ID_RE = re.compile(r"^B-[A-Za-z]?\d+(?:\.\d+)?$")
_BUILD_ID_RE = re.compile(r"^(?P<belief>B-[A-Za-z]?\d+(?:\.\d+)?)\s*/\s*[A-Z]+-[A-Za-z0-9]+$")
_DEC_ID_RE = re.compile(r"^DEC-\d+$")
# Case-sensitive on purpose: mirrors the SignalOS.NET DeferIdRegex, which only
# accepts the canonical uppercase ``DEFER`` token (lowercase ``defer`` is rejected).
_DEFER_ID_RE = re.compile(r"^DEFER\s*(?:->|\u2192)\s*(?:W\d+|never)$")
_PRINCIPLE_RE = re.compile(
    r"\b(principle|pillar|tenet|doctrine|invariant|foundation|norm|axiom|"
    r"value|creed|canon|ethos|posture|philosophy|shall\s+always|must\s+always|"
    r"must\s+not\s+ever|never\s+allow)\b",
    re.I,
)
_MEASURABLE_THRESHOLD_RE = re.compile(
    r"\|\s*[^|]+\s*\|\s*(?:>=|<=|!=|=|>|<|\u2265|\u2264|\u2260)\s*\|\s*[\d.+-]+[^|]*\|",
    re.I,
)
_MIN_N_RE = re.compile(r"MIN N[^:\n]*:\*\*\s*(?P<value>\d+)|MIN N[^:\n]*:\s*(?P<value2>\d+)", re.I)

_REQUIRED_FRONT_MATTER = (
    "belief_id",
    "wave",
    "scale_track",
    "delivery_mode",
    "designation",
    "source_artifact",
    "source_section",
    "source_kind",
    "source_reference",
    "build_size",
    "author",
    "date",
)
_ALLOWED_SOURCE_KINDS = {"PrdSection", "DeferHarvest", "ClientFeedback", "WaveSurprise"}
_REQUIRED_BELIEF_SECTIONS = (
    "Problem",
    "Disproof condition",
    "Bet Score",
    "Smallest Testable Build",
    "Signal threshold",
    "Confidence bar",
    "User served (primary)",
    "Zone history",
)
_SOURCE_REFERENCE_RE = re.compile(r"^(PRD\s+.+|Wave\s+\d+.*|CSL-\d{3,}.*|WD-\d{3,}.*)$", re.I)


@dataclass(frozen=True)
class TraceabilityIssue:
    code: str
    message: str
    severity: str = "HALT"
    line: int | None = None
    evidence: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
            "evidence": list(self.evidence),
            "details": dict(self.details),
        }


def validate_prd_traceability(
    repo_root: Path | str | None = None,
    *,
    matrix_path: Path | str | None = None,
    write_evidence: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root or Path.cwd()).expanduser().resolve()
    matrix = _resolve_matrix_path(root, matrix_path, ".signalos/PRD_TRACEABILITY.md")
    issues: list[TraceabilityIssue] = []

    if not matrix.is_file():
        issues.append(
            TraceabilityIssue(
                code="prd-traceability-matrix-missing",
                message=f"PRD traceability matrix is missing: {_display_path(matrix, root)}",
                evidence=[_display_path(matrix, root)],
            )
        )
        return _payload(
            PRD_SCHEMA_VERSION,
            "validate-prd-traceability",
            root,
            issues,
            {"matrix_path": _display_path(matrix, root), "row_count": 0},
            write_evidence=write_evidence,
        )

    rows, malformed = _parse_prd_matrix(matrix)
    for bad in malformed:
        issues.append(
            TraceabilityIssue(
                code="prd-traceability-row-malformed",
                message=(
                    f"Row {bad['line']}: PRD traceability row has {bad['cells']} columns; "
                    f"expected {bad['expected']}. Malformed rows are rejected so an "
                    f"unvalidated claim cannot be smuggled past the gate."
                ),
                line=bad["line"],
                evidence=[_display_path(matrix, root)],
                details={"cells": bad["cells"], "expected": bad["expected"]},
            )
        )
    if not rows and not malformed:
        issues.append(
            TraceabilityIssue(
                code="prd-traceability-matrix-empty",
                message=f"PRD traceability matrix has no data rows: {_display_path(matrix, root)}",
                evidence=[_display_path(matrix, root)],
            )
        )

    belief_index = _belief_index(root)
    decision_dna = _decision_dna_path(root)
    decision_text = decision_dna.read_text(encoding="utf-8", errors="replace") if decision_dna.is_file() else ""

    for row in rows:
        issues.extend(_validate_prd_row(root, row, belief_index, decision_dna, decision_text))

    return _payload(
        PRD_SCHEMA_VERSION,
        "validate-prd-traceability",
        root,
        issues,
        {
            "matrix_path": _display_path(matrix, root),
            "row_count": len(rows),
            "belief_count": len(belief_index),
            "decision_dna": _display_path(decision_dna, root),
        },
        write_evidence=write_evidence,
    )


def validate_product_traceability(
    repo_root: Path | str | None = None,
    *,
    write_evidence: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root or Path.cwd()).expanduser().resolve()
    issues: list[TraceabilityIssue] = []
    matrix = root / ".signalos" / "TRACEABILITY_MATRIX.md"
    belief_index = _belief_index(root)

    if not belief_index:
        issues.append(
            TraceabilityIssue(
                code="product-beliefs-missing",
                message="no product Belief artifacts found under .signalos/Beliefs or core/governance/Beliefs",
                evidence=[".signalos/Beliefs", "core/governance/Beliefs"],
            )
        )
    if not matrix.is_file():
        issues.append(
            TraceabilityIssue(
                code="traceability-matrix-missing",
                message="traceability matrix is missing: .signalos/TRACEABILITY_MATRIX.md",
                evidence=[".signalos/TRACEABILITY_MATRIX.md"],
            )
        )
        return _payload(
            PRODUCT_SCHEMA_VERSION,
            "validate-traceability",
            root,
            issues,
            {"matrix_path": ".signalos/TRACEABILITY_MATRIX.md", "belief_count": len(belief_index)},
            write_evidence=write_evidence,
        )

    matrix_rows, malformed = _parse_traceability_matrix(matrix)
    for bad in malformed:
        issues.append(
            TraceabilityIssue(
                code="traceability-row-malformed",
                message=(
                    f"Row {bad['line']}: traceability matrix row has {bad['cells']} columns; "
                    f"expected {bad['expected']}. Malformed rows are rejected so an "
                    f"unvalidated provenance row cannot be smuggled past the gate."
                ),
                line=bad["line"],
                evidence=[".signalos/TRACEABILITY_MATRIX.md"],
                details={"cells": bad["cells"], "expected": bad["expected"]},
            )
        )
    if not matrix_rows and not malformed:
        issues.append(
            TraceabilityIssue(
                code="traceability-matrix-empty",
                message="traceability matrix is empty or unreadable: .signalos/TRACEABILITY_MATRIX.md",
                evidence=[".signalos/TRACEABILITY_MATRIX.md"],
            )
        )

    for belief_id, path in sorted(belief_index.items()):
        issues.extend(_validate_belief_file(root, belief_id, path, matrix_rows))

    return _payload(
        PRODUCT_SCHEMA_VERSION,
        "validate-traceability",
        root,
        issues,
        {
            "matrix_path": ".signalos/TRACEABILITY_MATRIX.md",
            "belief_count": len(belief_index),
            "matrix_row_count": len(matrix_rows),
        },
        write_evidence=write_evidence,
    )


def _validate_prd_row(
    root: Path,
    row: dict[str, Any],
    belief_index: dict[str, Path],
    decision_dna: Path,
    decision_text: str,
) -> list[TraceabilityIssue]:
    issues: list[TraceabilityIssue] = []
    line = int(row["line"])
    prd_section = str(row.get("PRD Section", "")).strip()
    claim = str(row.get("Claim", "")).strip()
    destination = str(row.get("Destination", "")).strip().upper()
    target = str(row.get("Target ID", "")).strip()
    notes = str(row.get("Notes", "")).strip()

    if not prd_section:
        issues.append(TraceabilityIssue("prd-section-empty", f"Row {line}: PRD Section is empty.", line=line))
    if not claim:
        issues.append(TraceabilityIssue("prd-claim-empty", f"Row {line}: Claim is empty.", line=line))
    if destination not in _ALLOWED_DESTINATIONS:
        issues.append(
            TraceabilityIssue(
                "prd-destination-invalid",
                f"Row {line}: Destination '{row.get('Destination', '')}' must be one of BELIEF | BUILD | DEC | DEFER.",
                line=line,
                details={"destination": row.get("Destination", "")},
            )
        )
        return issues
    if not target:
        issues.append(
            TraceabilityIssue(
                "prd-target-empty",
                f"Row {line}: Target ID is empty for destination {destination}.",
                line=line,
            )
        )
        return issues

    if destination == "BELIEF":
        if not _BELIEF_ID_RE.match(target):
            issues.append(
                TraceabilityIssue(
                    "prd-belief-id-invalid",
                    f"Row {line}: BELIEF Target ID '{target}' is not a valid B-NNN or B-NNN.M identifier.",
                    line=line,
                )
            )
        elif target not in belief_index:
            issues.append(
                TraceabilityIssue(
                    "prd-belief-target-missing",
                    f"Row {line}: BELIEF '{target}' is not present in .signalos/Beliefs or core/governance/Beliefs.",
                    line=line,
                    evidence=[f".signalos/Beliefs/{target}.md", f"core/governance/Beliefs/{target}.md"],
                )
            )
        elif _PRINCIPLE_RE.search(claim):
            text = belief_index[target].read_text(encoding="utf-8", errors="replace")
            if not _has_measurable_signal_threshold(text):
                issues.append(
                    TraceabilityIssue(
                        "prd-principle-belief-unmeasurable",
                        (
                            f"Row {line}: principle-shaped claim '{_truncate(claim)}' points to BELIEF "
                            f"'{target}' without a measurable Signal threshold."
                        ),
                        line=line,
                        evidence=[_display_path(belief_index[target], root)],
                    )
                )
    elif destination == "BUILD":
        match = _BUILD_ID_RE.match(target)
        if not match:
            issues.append(
                TraceabilityIssue(
                    "prd-build-id-invalid",
                    f"Row {line}: BUILD Target ID '{target}' must be formatted as 'B-NNN / T-NNN'.",
                    line=line,
                )
            )
        elif match.group("belief") not in belief_index:
            issues.append(
                TraceabilityIssue(
                    "prd-build-parent-belief-missing",
                    f"Row {line}: BUILD parent Belief '{match.group('belief')}' is missing.",
                    line=line,
                )
            )
    elif destination == "DEC":
        if not _DEC_ID_RE.match(target):
            issues.append(
                TraceabilityIssue(
                    "prd-dec-id-invalid",
                    f"Row {line}: DEC Target ID '{target}' must be DEC-NNN.",
                    line=line,
                )
            )
        elif not decision_dna.is_file():
            issues.append(
                TraceabilityIssue(
                    "prd-decision-dna-missing",
                    f"Row {line}: DEC '{target}' cannot resolve because DECISION-DNA.md is missing.",
                    line=line,
                    evidence=[_display_path(decision_dna, root)],
                )
            )
        elif not re.search(rf"^##\s*{re.escape(target)}\b", decision_text, re.M):
            issues.append(
                TraceabilityIssue(
                    "prd-dec-target-missing",
                    f"Row {line}: DEC '{target}' has no matching block in DECISION-DNA.md.",
                    line=line,
                    evidence=[_display_path(decision_dna, root)],
                )
            )
    elif destination == "DEFER":
        if not _DEFER_ID_RE.match(target):
            issues.append(
                TraceabilityIssue(
                    "prd-defer-id-invalid",
                    f"Row {line}: DEFER Target ID '{target}' must be 'DEFER -> W{{NN}}' or 'DEFER -> never'.",
                    line=line,
                )
            )
        elif target.lower().endswith("never") and not notes:
            issues.append(
                TraceabilityIssue(
                    "prd-defer-never-unjustified",
                    f"Row {line}: DEFER -> never must carry a non-empty Notes justification.",
                    line=line,
                )
            )
    return issues


def _validate_belief_file(
    root: Path,
    belief_id: str,
    path: Path,
    matrix_rows: dict[str, dict[str, str]],
) -> list[TraceabilityIssue]:
    issues: list[TraceabilityIssue] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = _display_path(path, root)
    front_matter = _parse_front_matter(text)

    for key in _REQUIRED_FRONT_MATTER:
        if not str(front_matter.get(key, "")).strip():
            issues.append(
                TraceabilityIssue(
                    "belief-front-matter-missing",
                    f"{belief_id} is missing required front-matter field '{key}'.",
                    evidence=[rel],
                    details={"field": key},
                )
            )
    if front_matter.get("belief_id") and front_matter.get("belief_id") != belief_id:
        issues.append(
            TraceabilityIssue(
                "belief-id-mismatch",
                f"{belief_id} front-matter belief_id does not match filename.",
                evidence=[rel],
                details={"front_matter_belief_id": front_matter.get("belief_id")},
            )
        )
    if front_matter.get("designation", "").upper() not in {"PRIMARY", "SECONDARY"}:
        issues.append(
            TraceabilityIssue(
                "belief-designation-invalid",
                f"{belief_id} has invalid designation '{front_matter.get('designation', '')}'. Use PRIMARY or SECONDARY.",
                evidence=[rel],
            )
        )
    if front_matter.get("build_size", "").upper() not in {"S", "M", "L"}:
        issues.append(
            TraceabilityIssue(
                "belief-build-size-invalid",
                f"{belief_id} has invalid build_size '{front_matter.get('build_size', '')}'. Use S, M, or L.",
                evidence=[rel],
            )
        )
    source_kind = front_matter.get("source_kind", "")
    if source_kind and source_kind not in _ALLOWED_SOURCE_KINDS:
        issues.append(
            TraceabilityIssue(
                "belief-source-kind-invalid",
                f"{belief_id} has invalid source_kind '{source_kind}'.",
                evidence=[rel],
                details={"allowed": sorted(_ALLOWED_SOURCE_KINDS)},
            )
        )
    source_reference = front_matter.get("source_reference", "")
    if source_reference and not _SOURCE_REFERENCE_RE.match(source_reference):
        issues.append(
            TraceabilityIssue(
                "belief-source-reference-invalid",
                f"{belief_id} source_reference '{source_reference}' does not match a canonical pattern.",
                evidence=[rel],
            )
        )
    source_artifact = front_matter.get("source_artifact", "")
    if source_artifact:
        source_path, safe = _safe_workspace_path(root, source_artifact)
        if not safe or not source_path.is_file():
            issues.append(
                TraceabilityIssue(
                    "belief-source-artifact-missing",
                    f"{belief_id} source_artifact points to a missing or unsafe file: {source_artifact}",
                    evidence=[source_artifact],
                )
            )

    for section in _REQUIRED_BELIEF_SECTIONS:
        body = _section_body(text, section)
        if not body.strip():
            issues.append(
                TraceabilityIssue(
                    "belief-section-missing",
                    f"{belief_id} is missing required section '## {section}'.",
                    evidence=[rel],
                    details={"section": section},
                )
            )
    signal_body = _section_body(text, "Signal threshold")
    if signal_body and "signal lag" not in signal_body.lower():
        issues.append(
            TraceabilityIssue(
                "belief-signal-lag-missing",
                f"{belief_id} Signal threshold table must carry an explicit 'Signal lag' column.",
                evidence=[rel],
            )
        )
    user_body = _section_body(text, "User served (primary)")
    if user_body and not _MIN_N_RE.search(user_body):
        issues.append(
            TraceabilityIssue(
                "belief-min-n-missing",
                f"{belief_id} User served section must declare an explicit numeric MIN N.",
                evidence=[rel],
            )
        )
    zone_body = _section_body(text, "Zone history")
    if zone_body and "|" not in zone_body:
        issues.append(
            TraceabilityIssue(
                "belief-zone-history-table-missing",
                f"{belief_id} Zone history must contain at least one markdown table row.",
                evidence=[rel],
            )
        )

    row = matrix_rows.get(belief_id)
    if not row:
        issues.append(
            TraceabilityIssue(
                "belief-traceability-row-missing",
                f"{belief_id} is missing from .signalos/TRACEABILITY_MATRIX.md.",
                evidence=[".signalos/TRACEABILITY_MATRIX.md", rel],
            )
        )
    else:
        for field_name in ("Source Artifact", "Source Section", "Coverage Status"):
            if not row.get(field_name):
                issues.append(
                    TraceabilityIssue(
                        "belief-traceability-row-incomplete",
                        f"{belief_id} traceability row is missing '{field_name}'.",
                        evidence=[".signalos/TRACEABILITY_MATRIX.md"],
                        details={"field": field_name},
                    )
                )
        row_source = row.get("Source Artifact", "")
        if source_artifact and row_source and row_source != source_artifact:
            issues.append(
                TraceabilityIssue(
                    "belief-traceability-source-mismatch",
                    (
                        f"{belief_id} source_artifact mismatch between Belief file "
                        f"('{source_artifact}') and traceability matrix ('{row_source}')."
                    ),
                    evidence=[".signalos/TRACEABILITY_MATRIX.md", rel],
                )
            )
        if row_source:
            row_path, safe = _safe_workspace_path(root, row_source)
            if not safe or not row_path.is_file():
                issues.append(
                    TraceabilityIssue(
                        "belief-traceability-source-missing",
                        f"{belief_id} traceability row points to a missing or unsafe source artifact: {row_source}.",
                        evidence=[".signalos/TRACEABILITY_MATRIX.md", row_source],
                    )
                )
    return issues


def _parse_prd_matrix(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse the PRD traceability matrix.

    Returns ``(rows, malformed)``. A column-count-mismatched data row is NOT
    silently dropped: it is recorded in ``malformed`` so the validator can
    fail-closed instead of letting an unvalidated claim slip past the gate.
    """
    rows: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    headers: list[str] | None = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw_line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = _split_markdown_row(line)
        if any(cell.lower() == "prd section" for cell in cells):
            headers = cells
            continue
        if headers is None:
            continue
        if len(cells) != len(headers):
            malformed.append({"line": line_number, "cells": len(cells), "expected": len(headers)})
            continue
        row = {header: cells[idx] for idx, header in enumerate(headers)}
        if not row.get("PRD Section", "").strip():
            continue
        row["line"] = line_number
        rows.append(row)
    return rows, malformed


def _parse_traceability_matrix(path: Path) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    """Parse the product traceability matrix.

    Returns ``(rows, malformed)``. A column-count-mismatched data row is NOT
    silently dropped: it is recorded in ``malformed`` so the validator can
    fail-closed instead of accepting an unvalidated provenance row.
    """
    rows: dict[str, dict[str, str]] = {}
    malformed: list[dict[str, Any]] = []
    headers: list[str] | None = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw_line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = _split_markdown_row(line)
        if headers is None:
            headers = cells
            continue
        if len(cells) != len(headers):
            malformed.append({"line": line_number, "cells": len(cells), "expected": len(headers)})
            continue
        row = {header: cells[idx] for idx, header in enumerate(headers)}
        belief_id = row.get("Belief ID", "").strip()
        if belief_id:
            rows[belief_id] = row
    return rows, malformed


def _split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _belief_index(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for directory in (root / ".signalos" / "Beliefs", root / "core" / "governance" / "Beliefs"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            out.setdefault(path.stem, path)
    return out


def _decision_dna_path(root: Path) -> Path:
    candidates = [
        root / "core" / "governance" / "Governance" / "DECISION-DNA.md",
        root / "core" / "governance" / "DECISION-DNA.md",
        root / ".signalos" / "DECISION-DNA.md",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def _parse_front_matter(text: str) -> dict[str, str]:
    fenced = re.search(r"```yaml\s*(?P<body>.*?)```", text, re.I | re.S)
    if fenced:
        body = fenced.group("body")
    else:
        yaml_block = re.match(r"---\s*\n(?P<body>.*?)\n---", text, re.S)
        body = yaml_block.group("body") if yaml_block else ""
    result: dict[str, str] = {}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def _section_body(text: str, title: str) -> str:
    pattern = rf"(?ms)^##\s+{re.escape(title)}\s*$\s*(?P<body>.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, text)
    return match.group("body").strip() if match else ""


def _has_measurable_signal_threshold(belief_text: str) -> bool:
    return bool(_MEASURABLE_THRESHOLD_RE.search(_section_body(belief_text, "Signal threshold")))


def _resolve_matrix_path(root: Path, matrix_path: Path | str | None, default_rel: str) -> Path:
    if matrix_path is None or str(matrix_path).strip() == "":
        return root / default_rel
    raw = Path(matrix_path)
    return raw.expanduser().resolve() if raw.is_absolute() else (root / raw).resolve()


def _safe_workspace_path(root: Path, rel_path: str) -> tuple[Path, bool]:
    candidate = (root / rel_path).resolve(strict=False)
    safe = _is_relative_to(candidate, root.resolve(strict=False))
    return candidate, safe


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _payload(
    schema_version: str,
    validator: str,
    root: Path,
    issues: list[TraceabilityIssue],
    details: dict[str, Any],
    *,
    write_evidence: bool,
) -> dict[str, Any]:
    issue_dicts = [issue.to_dict() for issue in issues]
    blockers = [issue for issue in issue_dicts if issue["severity"] in {"HALT", "BLOCK_MERGE"}]
    ok = not blockers
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "validator": validator,
        "repo_root": str(root),
        "ok": ok,
        "pass": ok,
        "status": "PASS" if ok else "FAIL",
        "summary": {
            "issues": len(issue_dicts),
            "blockers": len(blockers),
        },
        "details": details,
        "issues": issue_dicts,
        "blockers": blockers,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "evidence_path": None,
    }
    if write_evidence:
        evidence_path = _write_evidence(root, validator, payload)
        payload["evidence_path"] = evidence_path
    return payload


def _write_evidence(root: Path, validator: str, payload: dict[str, Any]) -> str | None:
    if not (root / ".signalos").is_dir():
        return None
    evidence_dir = root / ".signalos" / "evidence" / "traceability"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / f"{validator}.json"
    try:
        display_path = path.relative_to(root).as_posix()
    except ValueError:
        display_path = str(path)
    payload["evidence_path"] = display_path
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return display_path


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _truncate(value: str, max_len: int = 80) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= max_len else value[: max_len - 3] + "..."
