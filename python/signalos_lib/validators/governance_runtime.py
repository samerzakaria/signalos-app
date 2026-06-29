"""Technology-neutral governance runtime validators.

These validators mirror the SignalOS.NET governance behaviors without binding
the app to .NET, ABP, Bash, or a specific product stack. They validate
SignalOS-owned artifacts: generation packets, agent packets, agent results,
and git diffs when a repository is available.
"""

from __future__ import annotations

__all__ = [
    "detect_governance_bypass",
    "resolve_guidance_obligations",
    "validate_guidance_obligations",
]

import fnmatch
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_REQUIRED_GENERATION_VALIDATORS = {
    "gate-signature-guard",
    "trust-tier-guard",
    "artifact-shape-guard",
    "path-consistency-guard",
}

_REQUIRED_FORBIDDEN_PATHS = {
    ".signalos",
    ".git",
    ".env",
    ".env.local",
    "*.pem",
    "*.key",
}

_SKIP_MARKERS = (
    "[skip-gate]",
    "[skip-proof]",
    "[skip-signalos]",
    "[no-verify]",
    "[skip-validator]",
)

# ---------------------------------------------------------------------------
# .NET-only analyzer-suppression patterns. INACTIVE on non-.NET stacks.
#
# These match ``dotnet_diagnostic.*.severity`` / ``dotnet_analyzer_diagnostic.*``
# lines in ``.editorconfig`` / ``.globalconfig``, which only exist in .NET
# repositories. On this Python/Rust repo they never fire, so they MUST NOT be
# mistaken for active suppression coverage. The Python-stack equivalent
# (``# type: ignore`` / ``# noqa`` / ``# nosec`` etc.) is enforced separately by
# ``_PY_SUPPRESSION_RE`` below.
# ---------------------------------------------------------------------------
_ANALYZER_SEVERITY_RE = re.compile(
    r"dotnet_diagnostic\.(?P<rule>[A-Za-z0-9_.-]+)\.severity\s*=\s*"
    r"(?P<level>none|silent|suggestion)",
    re.IGNORECASE,
)

_ANALYZER_CATEGORY_RE = re.compile(
    r"dotnet_analyzer_diagnostic\.category-(?P<category>[\w.-]+)\.severity\s*=\s*none",
    re.IGNORECASE,
)

# Active Python-stack suppression directives. Fired only on ADDED diff lines
# (same conservative posture as the .NET analyzer checks above) when the
# suppression carries no inline justification comment after it.
#   - # type: ignore         (mypy / pyright blanket ignore)
#   - # noqa                  (flake8 / ruff blanket ignore)
#   - # nosec                 (bandit security suppression)
# A trailing rationale (e.g. ``# noqa: E501  # narrow URL`` or
# ``# type: ignore[arg-type]  # upstream stub bug``) is treated as justified.
_PY_SUPPRESSION_RE = re.compile(
    r"#\s*(?P<directive>type:\s*ignore|noqa|nosec)(?P<args>(?:\[[^\]]*\])?(?::[^#\n]*)?)",
    re.IGNORECASE,
)
# A pyproject/ruff/flake8 added ``per-file-ignores`` line is also a suppression.
_PY_PER_FILE_IGNORES_RE = re.compile(r"per-file-ignores", re.IGNORECASE)

_DATE_RE = re.compile(r"\b(?P<date>20\d{2}-\d{2}-\d{2})\b")

_BUNDLE_ROOT = Path(__file__).resolve().parents[1] / "_bundle"
_DEFAULT_GUIDANCE_CATALOG = (
    _BUNDLE_ROOT / "core" / "tool-adapters" / "_shared" / "guidance-catalog.json"
)
_DEFAULT_OBLIGATIONS = (
    _BUNDLE_ROOT / "core" / "tool-adapters" / "_shared" / "obligations.json"
)


def resolve_guidance_obligations(
    repo_root: Path,
    touched_paths: list[str],
    *,
    action: str = "edit",
    stack: str = "any",
    catalog_path: Path | None = None,
    obligations_path: Path | None = None,
) -> dict[str, Any]:
    """Resolve guidance obligations for touched paths.

    Returns a deterministic report using the same conceptual contract as the
    SignalOS.NET resolver: catalog entries, obligation rules, path globs,
    action filters, stack filters, required guidance IDs, and mode.
    """

    repo_root = Path(repo_root)
    effective_catalog = Path(catalog_path) if catalog_path else _DEFAULT_GUIDANCE_CATALOG
    effective_obligations = Path(obligations_path) if obligations_path else _DEFAULT_OBLIGATIONS
    report: dict[str, Any] = {
        "schema_version": "signalos.guidance_obligation_resolution.v1",
        "repo_root": str(repo_root.resolve()),
        "guidance_catalog_path": _rel(effective_catalog, repo_root),
        "obligations_path": _rel(effective_obligations, repo_root),
        "action": _normalise_token(action),
        "stack": _normalise_token(stack),
        "touched_paths": sorted(dict.fromkeys(_normalise_rel_path(path) for path in touched_paths if str(path).strip())),
        "resolved": [],
        "errors": [],
    }

    catalog_raw, catalog_error = _read_json(effective_catalog)
    obligations_raw, obligations_error = _read_json(effective_obligations)
    if catalog_error is not None:
        report["errors"].append(f"guidance catalog unreadable: {catalog_error}")
        return report
    if obligations_error is not None:
        report["errors"].append(f"obligations unreadable: {obligations_error}")
        return report
    if not isinstance(catalog_raw, list):
        report["errors"].append("guidance catalog must be a JSON list")
        return report
    if not isinstance(obligations_raw, list):
        report["errors"].append("obligations must be a JSON list")
        return report

    catalog: dict[str, dict[str, Any]] = {}
    for entry in catalog_raw:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            catalog[entry["id"]] = entry

    action_token = report["action"]
    stack_token = report["stack"]
    resolved: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for obligation in obligations_raw:
        if not isinstance(obligation, dict):
            report["errors"].append("obligation entry must be a JSON object")
            continue
        rule_id = str(obligation.get("rule_id") or "").strip()
        when = obligation.get("when")
        if not rule_id or not isinstance(when, dict):
            report["errors"].append("obligation missing rule_id or when object")
            continue
        if not _token_matches(action_token, _as_str_list(when.get("action_any_of"))):
            continue
        if not _token_matches(stack_token, _as_str_list(when.get("stack_any_of"))):
            continue
        path_globs = _as_str_list(when.get("path_globs"))
        matched_paths = [
            path
            for path in report["touched_paths"]
            if _path_matches_globs(path, path_globs)
        ]
        if not matched_paths:
            continue

        mode = str(obligation.get("mode") or "autoload").strip() or "autoload"
        for guidance_id in _as_str_list(obligation.get("require")):
            catalog_entry = catalog.get(guidance_id)
            if catalog_entry is None:
                report["errors"].append(
                    f"{rule_id} requires unknown guidance id: {guidance_id}"
                )
                continue
            if catalog_entry.get("active") is not True:
                report["errors"].append(
                    f"{rule_id} requires inactive guidance id: {guidance_id}"
                )
                continue
            catalog_stack = _normalise_token(str(catalog_entry.get("stack") or "any"))
            if catalog_stack not in {"any", stack_token} and stack_token != "any":
                report["errors"].append(
                    f"{rule_id} requires stack-incompatible guidance id: {guidance_id}"
                )
                continue
            dedupe_key = (rule_id, guidance_id, mode)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            resolved.append({
                "id": guidance_id,
                "mode": mode,
                "rule_id": rule_id,
                "title": str(obligation.get("title") or ""),
                "paths": matched_paths,
                "guidance_path": str(catalog_entry.get("path") or ""),
                "category": str(catalog_entry.get("category") or ""),
                "stack": str(catalog_entry.get("stack") or "any"),
            })

    report["resolved"] = sorted(
        resolved,
        key=lambda item: (str(item["rule_id"]), str(item["id"]), str(item["mode"])),
    )
    return report


def validate_guidance_obligations(
    repo_root: Path,
    *,
    loaded_path: Path | None = None,
    staged: bool = False,
    diff_range: str | None = None,
    touched_paths: list[str] | None = None,
    action: str = "edit",
    stack: str = "any",
    catalog_path: Path | None = None,
    obligations_path: Path | None = None,
    write_evidence: bool = True,
) -> tuple[bool, str, dict[str, Any]]:
    """Validate that product agents received required SignalOS guidance.

    The app does not assume the SignalOS.NET guidance-catalog resolver exists
    in every generated product. Instead, it enforces the app-native obligation:
    generation packets must carry strict governance instructions, and agent
    packets must expose the skill guidance/catalog needed for the run.
    """

    repo_root = Path(repo_root)
    checked_at = _now()
    violations: list[str] = []
    warnings: list[str] = []
    checked_packets: list[dict[str, Any]] = []
    required_loaded_ids: set[str] = set()
    enforced_loaded_ids: set[str] = set()
    soft_loaded_ids: set[str] = set()
    resolved_obligations: dict[str, Any] | None = None

    effective_touched_paths = list(touched_paths or [])
    if touched_paths is None and (staged or diff_range):
        effective_touched_paths, touched_error = _collect_git_touched_paths(
            repo_root,
            staged=staged,
            diff_range=diff_range,
        )
        if touched_error:
            warnings.append(touched_error)

    if effective_touched_paths:
        resolved_obligations = resolve_guidance_obligations(
            repo_root,
            effective_touched_paths,
            action=action,
            stack=stack,
            catalog_path=catalog_path,
            obligations_path=obligations_path,
        )
        violations.extend(resolved_obligations.get("errors", []))
        for item in resolved_obligations.get("resolved", []):
            guidance_id = str(item.get("id") or "").strip()
            if not guidance_id:
                continue
            required_loaded_ids.add(guidance_id)
            if str(item.get("mode") or "") == "autoload_enforce":
                enforced_loaded_ids.add(guidance_id)
            else:
                soft_loaded_ids.add(guidance_id)

    packet_files = _collect_packet_files(repo_root)
    if not packet_files and not effective_touched_paths:
        details = {
            "schema_version": "signalos.guidance_obligations.v1",
            "status": "PASS",
            "checked_at": checked_at,
            "repo_root": str(repo_root.resolve()),
            "mode": "artifact",
            "action": action,
            "stack": stack,
            "diff_range": diff_range,
            "touched_paths": [],
            "resolved_obligations": None,
            "checked_packets": [],
            "violations": [],
            "warnings": [
                "no product generation or agent packet artifacts found; nothing to enforce"
            ],
        }
        if write_evidence:
            details["evidence_path"] = _write_evidence(
                repo_root,
                "VALIDATE_GUIDANCE_OBLIGATIONS.json",
                details,
            )
        return True, "no product guidance obligations to enforce", details

    for path, packet_kind in packet_files:
        rel = _rel(path, repo_root)
        parsed, error = _read_json(path)
        record: dict[str, Any] = {
            "path": rel,
            "kind": packet_kind,
            "json_valid": error is None,
            "checks": [],
        }
        if error is not None:
            violations.append(f"{rel}: invalid JSON: {error}")
            checked_packets.append(record)
            continue
        if not isinstance(parsed, dict):
            violations.append(f"{rel}: expected JSON object")
            checked_packets.append(record)
            continue

        if packet_kind == "generation":
            _check_generation_guidance(parsed, rel, record, violations)
        elif packet_kind == "agent_scope":
            _check_agent_scope_guidance(parsed, rel, record, violations, required_loaded_ids)
            generation = parsed.get("generation")
            if isinstance(generation, dict):
                _check_generation_guidance(
                    generation,
                    f"{rel}#generation",
                    record,
                    violations,
                )
        checked_packets.append(record)

    loaded_ids: set[str] | None = None
    effective_loaded_path = loaded_path
    if effective_loaded_path is None:
        default_loaded = repo_root / ".signalos" / "loaded-guidance.txt"
        if default_loaded.is_file():
            effective_loaded_path = default_loaded

    if effective_loaded_path is not None:
        loaded_ids, load_error = _read_loaded_guidance(Path(effective_loaded_path))
        if load_error is not None:
            violations.append(f"{_rel(Path(effective_loaded_path), repo_root)}: {load_error}")
        else:
            missing_enforced = sorted(enforced_loaded_ids - loaded_ids)
            missing_soft = sorted(soft_loaded_ids - loaded_ids)
            packet_only = sorted((required_loaded_ids - enforced_loaded_ids - soft_loaded_ids) - loaded_ids)
            if missing_enforced:
                violations.append(
                    "loaded guidance file is missing enforced guidance IDs: "
                    + ", ".join(missing_enforced)
                )
            if missing_soft:
                warnings.append(
                    "loaded guidance file is missing autoload guidance IDs: "
                    + ", ".join(missing_soft)
                )
            if packet_only:
                warnings.append(
                    "loaded guidance file does not list packet-embedded guidance IDs: "
                    + ", ".join(packet_only)
                )
    elif enforced_loaded_ids:
        violations.append(
            "loaded guidance evidence is required for enforced obligations: "
            + ", ".join(sorted(enforced_loaded_ids))
        )
    elif required_loaded_ids:
        warnings.append(
            ".signalos/loaded-guidance.txt not present; packet-embedded guidance is treated as loaded"
        )

    passed = not violations
    details = {
        "schema_version": "signalos.guidance_obligations.v1",
        "status": "PASS" if passed else "FAIL",
        "checked_at": checked_at,
        "repo_root": str(repo_root.resolve()),
        "mode": "diff" if diff_range else ("staged" if staged else "artifact"),
        "action": action,
        "stack": stack,
        "diff_range": diff_range,
        "touched_paths": sorted(dict.fromkeys(_normalise_rel_path(path) for path in effective_touched_paths)),
        "resolved_obligations": resolved_obligations,
        "checked_packets": checked_packets,
        "required_loaded_ids": sorted(required_loaded_ids),
        "enforced_loaded_ids": sorted(enforced_loaded_ids),
        "soft_loaded_ids": sorted(soft_loaded_ids),
        "loaded_guidance_path": (
            _rel(Path(effective_loaded_path), repo_root)
            if effective_loaded_path is not None
            else None
        ),
        "loaded_guidance_ids": sorted(loaded_ids) if loaded_ids is not None else None,
        "violations": violations,
        "warnings": warnings,
    }
    if write_evidence:
        details["evidence_path"] = _write_evidence(
            repo_root,
            "VALIDATE_GUIDANCE_OBLIGATIONS.json",
            details,
        )
    message = (
        "guidance obligations satisfied"
        if passed
        else "guidance obligations not satisfied"
    )
    return passed, message, details


def detect_governance_bypass(
    repo_root: Path,
    *,
    staged: bool = True,
    diff_range: str | None = None,
    diff_text: str | None = None,
    message_file: Path | None = None,
    write_evidence: bool = True,
) -> tuple[bool, str, dict[str, Any]]:
    """Detect governance-bypass signatures in diffs and agent output."""

    repo_root = Path(repo_root)
    checked_at = _now()
    violations: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []

    if diff_text is None:
        diff_text, git_error = _collect_git_diff(repo_root, staged=staged, diff_range=diff_range)
        if git_error:
            warnings.append(git_error)
            checks.append({
                "name": "git_diff_available",
                "passed": True,
                "skipped": True,
                "detail": git_error,
            })
    else:
        checks.append({
            "name": "git_diff_available",
            "passed": True,
            "skipped": False,
            "detail": "provided diff text",
        })

    if diff_text is not None:
        _check_diff_bypass(repo_root, diff_text, violations, checks)

    _check_commit_message(repo_root, message_file, violations, warnings, checks)
    _check_agent_result_bypass(repo_root, violations, warnings, checks)

    passed = not violations
    details = {
        "schema_version": "signalos.governance_bypass_detection.v1",
        "status": "PASS" if passed else "FAIL",
        "checked_at": checked_at,
        "repo_root": str(repo_root.resolve()),
        "mode": "diff" if diff_range else ("staged" if staged else "working-tree"),
        "diff_range": diff_range,
        "checks": checks,
        "violations": violations,
        "warnings": warnings,
    }
    if write_evidence:
        details["evidence_path"] = _write_evidence(
            repo_root,
            "VALIDATE_BYPASS_DETECTION.json",
            details,
        )
    message = (
        "no governance-bypass signature detected"
        if passed
        else "governance-bypass signature detected"
    )
    return passed, message, details


def _check_generation_guidance(
    packet: dict[str, Any],
    rel: str,
    record: dict[str, Any],
    violations: list[str],
) -> None:
    checks = record.setdefault("checks", [])

    instructions = packet.get("governance_instructions")
    instructions_ok = isinstance(instructions, dict) and bool(instructions)
    checks.append({
        "name": "governance_instructions_present",
        "passed": instructions_ok,
    })
    if not instructions_ok:
        violations.append(f"{rel}: governance_instructions missing or empty")
    else:
        keys = [str(key).replace("\\", "/").lower() for key in instructions]
        has_constitution = any(key.endswith("constitution.md") for key in keys)
        checks.append({
            "name": "constitution_guidance_loaded",
            "passed": has_constitution,
        })
        if not has_constitution:
            violations.append(f"{rel}: constitution guidance is not loaded")

    enforcement = packet.get("governance_enforcement")
    enforcement_ok = isinstance(enforcement, dict)
    checks.append({
        "name": "governance_enforcement_present",
        "passed": enforcement_ok,
    })
    if not enforcement_ok:
        violations.append(f"{rel}: governance_enforcement missing")
    else:
        expected = {
            "mode": "strict",
            "constitution_required": True,
            "refusal_on_violation": True,
        }
        for key, expected_value in expected.items():
            passed = enforcement.get(key) == expected_value
            checks.append({
                "name": f"enforcement_{key}",
                "passed": passed,
                "actual": enforcement.get(key),
                "expected": expected_value,
            })
            if not passed:
                violations.append(
                    f"{rel}: governance_enforcement.{key} must be {expected_value!r}"
                )

        validators = set(_as_str_list(enforcement.get("validators")))
        missing_validators = sorted(_REQUIRED_GENERATION_VALIDATORS - validators)
        checks.append({
            "name": "required_governance_validators",
            "passed": not missing_validators,
            "missing": missing_validators,
        })
        if missing_validators:
            violations.append(
                f"{rel}: missing governance validators: "
                + ", ".join(missing_validators)
            )

    _check_path_contract(packet, rel, checks, violations)


def _check_agent_scope_guidance(
    packet: dict[str, Any],
    rel: str,
    record: dict[str, Any],
    violations: list[str],
    required_loaded_ids: set[str],
) -> None:
    checks = record.setdefault("checks", [])

    for field in (
        "success_criteria",
        "evidence_required",
        "forbidden_rules",
        "repair_policy",
        "escalation_policy",
        "source_policy",
        "team_contract",
    ):
        value = packet.get(field)
        passed = value not in (None, "", [], {})
        checks.append({
            "name": f"agent_contract_{field}",
            "passed": passed,
        })
        if not passed:
            violations.append(f"{rel}: agent packet missing {field}")

    repair_policy = packet.get("repair_policy")
    repair_ok = isinstance(repair_policy, dict) and bool(repair_policy.get("forbidden_violation"))
    checks.append({
        "name": "forbidden_violation_repair_policy",
        "passed": repair_ok,
    })
    if not repair_ok:
        violations.append(f"{rel}: repair_policy.forbidden_violation missing")

    team_contract = packet.get("team_contract")
    team_ok = (
        isinstance(team_contract, dict)
        and team_contract.get("agents_are_signalos_team") is True
        and team_contract.get("signalos_orchestrates_team") is True
    )
    checks.append({
        "name": "signalos_team_contract",
        "passed": team_ok,
    })
    if not team_ok:
        violations.append(f"{rel}: SignalOS team contract is not explicit")

    catalog = packet.get("skills_catalog")
    catalog_ok = isinstance(catalog, list) and bool(catalog)
    checks.append({
        "name": "skills_catalog_present",
        "passed": catalog_ok,
    })
    if not catalog_ok:
        violations.append(f"{rel}: skills_catalog missing or empty")

    applicable = packet.get("applicable_skills")
    applicable_ok = isinstance(applicable, list) and bool(applicable)
    checks.append({
        "name": "applicable_skills_present",
        "passed": applicable_ok,
    })
    if not applicable_ok:
        violations.append(f"{rel}: applicable_skills missing or empty")
    else:
        for entry in applicable:
            if isinstance(entry, dict):
                key = str(entry.get("key") or entry.get("name") or "").strip()
                if key:
                    required_loaded_ids.add(key)

    _check_path_contract(packet, rel, checks, violations)


def _check_path_contract(
    packet: dict[str, Any],
    rel: str,
    checks: list[dict[str, Any]],
    violations: list[str],
) -> None:
    allowed = _as_str_list(packet.get("allowed_paths"))
    allowed_ok = bool(allowed)
    checks.append({
        "name": "allowed_paths_present",
        "passed": allowed_ok,
    })
    if not allowed_ok:
        violations.append(f"{rel}: allowed_paths missing or empty")

    forbidden = _normalise_path_tokens(_as_str_list(packet.get("forbidden_paths")))
    missing_forbidden = sorted(_REQUIRED_FORBIDDEN_PATHS - forbidden)
    checks.append({
        "name": "forbidden_paths_cover_governance_and_secrets",
        "passed": not missing_forbidden,
        "missing": missing_forbidden,
    })
    if missing_forbidden:
        violations.append(
            f"{rel}: forbidden_paths missing required entries: "
            + ", ".join(missing_forbidden)
        )


def _check_diff_bypass(
    repo_root: Path,
    diff_text: str,
    violations: list[str],
    checks: list[dict[str, Any]],
) -> None:
    audit_hunk = _extract_file_hunk(diff_text, ".signalos/AUDIT_TRAIL.jsonl")
    removed = _count_removed_lines(audit_hunk) if audit_hunk else 0
    checks.append({
        "name": "audit_trail_append_only",
        "passed": removed == 0,
        "removed_lines": removed,
        "skipped": audit_hunk is None,
    })
    if removed:
        violations.append(
            f"MG-12 bypass: AUDIT_TRAIL.jsonl is append-only; detected {removed} removed/modified line(s)"
        )

    dna_hunk = _extract_file_hunk(diff_text, "core/governance/Governance/DECISION-DNA.md")
    historical_rewrite = False
    head_date = _git_output(
        repo_root,
        ["log", "-1", "--format=%ad", "--date=short", "HEAD~1"],
    )
    if dna_hunk and head_date:
        for line in dna_hunk.splitlines():
            if not line.startswith("-") or line.startswith("---"):
                continue
            match = _DATE_RE.search(line)
            if match and match.group("date") < head_date.strip():
                historical_rewrite = True
                violations.append(
                    "MG-12 bypass: DECISION-DNA.md rewrites historical row dated "
                    f"{match.group('date')}"
                )
                break
    checks.append({
        "name": "decision_dna_historical_rewrite",
        "passed": not historical_rewrite,
        "skipped": dna_hunk is None or not head_date,
    })

    analyzer_violations: list[str] = []
    for path in (".editorconfig", ".globalconfig"):
        cfg_hunk = _extract_file_hunk(diff_text, path)
        if cfg_hunk is None:
            continue
        for line in cfg_hunk.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            severity = _ANALYZER_SEVERITY_RE.search(line)
            if severity:
                analyzer_violations.append(
                    f"{path}: analyzer rule {severity.group('rule')} severity lowered to {severity.group('level')}"
                )
            category = _ANALYZER_CATEGORY_RE.search(line)
            if category:
                analyzer_violations.append(
                    f"{path}: analyzer category {category.group('category')} severity set to none"
                )
    checks.append({
        "name": "analyzer_severity_not_weakened",
        "passed": not analyzer_violations,
        "violations": analyzer_violations,
    })
    for violation in analyzer_violations:
        violations.append(f"MG-12 bypass: {violation}")

    suppression_violations = _detect_unjustified_suppressions(diff_text)
    checks.append({
        "name": "no_unjustified_suppression_directives",
        "passed": not suppression_violations,
        "violations": suppression_violations,
    })
    for violation in suppression_violations:
        violations.append(f"MG-12 bypass: {violation}")

    constitution_touched = _hunk_touches(
        diff_text,
        "core/governance/Governance/CONSTITUTION.md",
    )
    amendment_touched = bool(re.search(
        r"\+\+\+\s+b/core/governance/(?:Governance/AMENDMENT-\d+\.md|Retro/AMENDMENTS\.md)",
        diff_text,
        re.IGNORECASE,
    ))
    checks.append({
        "name": "constitution_edits_have_amendment",
        "passed": not constitution_touched or amendment_touched,
        "skipped": not constitution_touched,
    })
    if constitution_touched and not amendment_touched:
        violations.append(
            "MG-12 bypass: CONSTITUTION.md modified without matching amendment artifact"
        )

    run_proof_hunk = _extract_file_hunk(diff_text, "proof/run_proof.sh")
    silent_removed: list[str] = []
    if run_proof_hunk is not None:
        for line in run_proof_hunk.splitlines():
            if not line.startswith("-") or line.startswith("---"):
                continue
            match = re.search(r"\"(?P<name>\d{2}_[A-Za-z0-9_]+)\"", line)
            if not match:
                continue
            name = match.group("name")
            script_path = repo_root / "proof" / "scenarios" / f"{name}.sh"
            deleted = re.search(
                rf"deleted file mode.*\n---\s+a/proof/scenarios/{re.escape(name)}\.sh",
                diff_text,
                re.MULTILINE,
            )
            if script_path.exists() and deleted is None:
                silent_removed.append(name)
    checks.append({
        "name": "proof_scenarios_not_silently_excluded",
        "passed": not silent_removed,
        "removed": silent_removed,
        "skipped": run_proof_hunk is None,
    })
    for name in silent_removed:
        violations.append(
            f"MG-12 bypass: proof scenario '{name}' removed from run_proof.sh without deleting its script"
        )


def _detect_unjustified_suppressions(diff_text: str) -> list[str]:
    """Flag newly-ADDED Python-stack suppression directives lacking justification.

    Conservative, fail-closed posture mirroring the .NET analyzer checks: only
    considers added (``+``) diff lines, ignores diff/file headers, and treats a
    directive as justified when it carries a trailing rationale comment. This is
    the active Python-stack counterpart to the inactive .NET analyzer regexes.
    """
    out: list[str] = []
    current_file: str | None = None
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ "):
            # Track the target file path (strip the "+++ b/" prefix).
            path = raw_line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current_file = path if path != "/dev/null" else None
            continue
        if not raw_line.startswith("+") or raw_line.startswith("+++"):
            continue
        added = raw_line[1:]

        if _PY_PER_FILE_IGNORES_RE.search(added):
            where = f"{current_file}: " if current_file else ""
            out.append(
                f"{where}added 'per-file-ignores' suppression without justification"
            )
            continue

        match = _PY_SUPPRESSION_RE.search(added)
        if not match:
            continue
        directive = re.sub(r"\s+", " ", match.group("directive").strip())
        # Justified when a rationale comment follows the directive's args, e.g.
        #   x = f()  # type: ignore[arg-type]  # upstream stub bug
        #   y = g()  # noqa: E501  # long external URL
        trailing = added[match.end():]
        justified = "#" in trailing and bool(trailing.split("#", 1)[1].strip())
        if not justified:
            where = f"{current_file}: " if current_file else ""
            out.append(
                f"{where}added '# {directive}' suppression without justification comment"
            )
    return out


def _check_commit_message(
    repo_root: Path,
    message_file: Path | None,
    violations: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
) -> None:
    msg_path = Path(message_file) if message_file is not None else repo_root / ".git" / "COMMIT_EDITMSG"
    if not msg_path.is_file():
        checks.append({
            "name": "commit_message_skip_markers",
            "passed": True,
            "skipped": True,
            "detail": f"message file not found: {_rel(msg_path, repo_root)}",
        })
        return
    try:
        text = msg_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warnings.append(f"could not read commit message file: {exc}")
        checks.append({
            "name": "commit_message_skip_markers",
            "passed": True,
            "skipped": True,
            "detail": str(exc),
        })
        return

    found = [marker for marker in _SKIP_MARKERS if marker.lower() in text.lower()]
    checks.append({
        "name": "commit_message_skip_markers",
        "passed": not found,
        "markers": found,
        "skipped": False,
    })
    for marker in found:
        violations.append(
            f"MG-12 bypass: commit message contains gate-skip marker '{marker}'"
        )


def _check_agent_result_bypass(
    repo_root: Path,
    violations: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
) -> None:
    runs_dir = repo_root / ".signalos" / "product" / "agent-runs"
    run_dirs = sorted(path for path in runs_dir.glob("*") if path.is_dir()) if runs_dir.is_dir() else []
    if not run_dirs:
        checks.append({
            "name": "agent_result_scope_bypass",
            "passed": True,
            "skipped": True,
            "detail": "no agent run directories found",
        })
        return

    from signalos_lib.product.agent_packets import validate_agent_result

    invalid_forbidden: list[dict[str, Any]] = []
    action_markers: list[dict[str, str]] = []
    for run_dir in run_dirs:
        rel_run = _rel(run_dir, repo_root)
        result_path = run_dir / "RESULT.json"
        if result_path.is_file():
            parsed, error = _read_json(result_path)
            if error is not None:
                warnings.append(f"{_rel(result_path, repo_root)}: invalid JSON: {error}")
            elif isinstance(parsed, dict):
                for action in _as_str_list(parsed.get("actions_taken")):
                    for marker in _SKIP_MARKERS:
                        if marker.lower() in action.lower():
                            action_markers.append({"run": rel_run, "marker": marker})

        validation = validate_agent_result(run_dir, repo_root, None)
        if validation.get("forbidden_violated"):
            invalid_forbidden.append({
                "run": rel_run,
                "violations": validation.get("violations", []),
            })

    checks.append({
        "name": "agent_result_scope_bypass",
        "passed": not invalid_forbidden,
        "runs_checked": len(run_dirs),
        "violations": invalid_forbidden,
    })
    for item in invalid_forbidden:
        violations.append(
            f"MG-12 bypass: agent result in {item['run']} violates packet scope: "
            + "; ".join(str(v) for v in item["violations"])
        )

    checks.append({
        "name": "agent_action_skip_markers",
        "passed": not action_markers,
        "markers": action_markers,
    })
    for item in action_markers:
        violations.append(
            f"MG-12 bypass: agent action in {item['run']} contains gate-skip marker '{item['marker']}'"
        )


def _collect_packet_files(repo_root: Path) -> list[tuple[Path, str]]:
    product_dir = repo_root / ".signalos" / "product"
    files: list[tuple[Path, str]] = []
    generation_packet = product_dir / "GENERATION_PACKET.json"
    if generation_packet.is_file():
        files.append((generation_packet, "generation"))
    runs_dir = product_dir / "agent-runs"
    if runs_dir.is_dir():
        for scope_path in sorted(runs_dir.glob("*/scope.json")):
            files.append((scope_path, "agent_scope"))
    return files


def _collect_git_diff(
    repo_root: Path,
    *,
    staged: bool,
    diff_range: str | None,
) -> tuple[str | None, str | None]:
    args = ["diff"]
    if diff_range:
        args.append(diff_range)
    elif staged:
        args.append("--cached")
    proc = _run_git(repo_root, args)
    if proc is None:
        return None, "git diff unavailable for this repo"
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"git diff exited {proc.returncode}"
        return None, detail
    return proc.stdout, None


def _collect_git_touched_paths(
    repo_root: Path,
    *,
    staged: bool,
    diff_range: str | None,
) -> tuple[list[str], str | None]:
    args = ["diff", "--name-only"]
    if diff_range:
        args.append(diff_range)
    elif staged:
        args.append("--cached")
    proc = _run_git(repo_root, args)
    if proc is None:
        return [], "git diff --name-only unavailable for this repo"
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"git diff --name-only exited {proc.returncode}"
        return [], detail
    paths = [
        _normalise_rel_path(line)
        for line in proc.stdout.splitlines()
        if line.strip()
    ]
    return sorted(dict.fromkeys(paths)), None


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git_output(repo_root: Path, args: list[str]) -> str | None:
    proc = _run_git(repo_root, args)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _extract_file_hunk(diff_text: str, path: str) -> str | None:
    pattern = rf"(?ms)^diff --git a/{re.escape(path)} b/{re.escape(path)}.*?(?=^diff --git |\Z)"
    match = re.search(pattern, diff_text)
    return match.group(0) if match else None


def _hunk_touches(diff_text: str, path: str) -> bool:
    return f"diff --git a/{path} b/{path}" in diff_text


def _count_removed_lines(hunk: str | None) -> int:
    if not hunk:
        return 0
    count = 0
    for line in hunk.splitlines():
        if line.startswith("---") or line.startswith("@@ "):
            continue
        if line.startswith("-"):
            count += 1
    return count


def _read_loaded_guidance(path: Path) -> tuple[set[str], str | None]:
    if not path.is_file():
        return set(), "loaded guidance file is missing"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return set(), f"loaded guidance file is unreadable: {exc}"
    loaded = {
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }
    return loaded, None


def _normalise_token(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _token_matches(actual: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    normalised = {_normalise_token(item) for item in allowed}
    return "any" in normalised or _normalise_token(actual) in normalised


def _normalise_rel_path(path: str) -> str:
    return str(path).replace("\\", "/").strip().lstrip("./")


def _path_matches_globs(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    normed = _normalise_rel_path(path)
    for pattern in patterns:
        pat = _normalise_rel_path(pattern)
        if fnmatch.fnmatch(normed, pat):
            return True
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/")
            if normed == prefix or normed.startswith(prefix + "/"):
                return True
        if "/**/" in pat:
            prefix, suffix = pat.split("/**/", 1)
            if normed.startswith(prefix.rstrip("/") + "/") and fnmatch.fnmatch(normed, f"*{suffix}"):
                return True
    return False


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, str(exc)
    except OSError as exc:
        return None, str(exc)


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _normalise_path_tokens(values: list[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        normed = value.replace("\\", "/").strip()
        if not normed:
            continue
        if normed.endswith("/**"):
            normed = normed[:-3]
        normed = normed.rstrip("/")
        tokens.add(normed)
    return tokens


def _write_evidence(repo_root: Path, filename: str, payload: dict[str, Any]) -> str:
    path = repo_root / ".signalos" / "product" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    rel = _rel(path, repo_root)
    payload["evidence_path"] = rel
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return rel


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except Exception:
        return str(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
