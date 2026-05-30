# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/validate_cmd.py
# W3.5 — Operator validator runner (AMD-CORE-018)
#
# Runs all governance validators with severity-labelled output,
# mirroring deliver.sh's VALIDATOR_SEVERITY map.

from __future__ import annotations

__all__ = [
    "VALIDATOR_SEVERITY",
    "DEFAULT_SEVERITY",
    "ValidatorResult",
    "run_validators",
    "run_layer1_validators",
    "get_severity",
]

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# Mirror of deliver.sh's VALIDATOR_SEVERITY map
VALIDATOR_SEVERITY: dict[str, str] = {
    "gate-signature-guard":         "HALT",
    "constitution-amendment-guard": "HALT",
    "ownership-guard":              "HALT",
    "trust-tier-guard":             "BLOCK_MERGE",
    "tier-sheet-guard":             "BLOCK_MERGE",
    "artifact-shape-guard":         "BLOCK_MERGE",
    "path-consistency-guard":       "BLOCK_MERGE",
    "expectation-redline-guard":    "BLOCK_MERGE",
    "decision-dna-guard":           "WARN",
    "client-signal-verbatim-guard": "WARN",
    "metrics-config-validator":     "WARN",
}

DEFAULT_SEVERITY = "BLOCK_MERGE"


def get_severity(name: str) -> str:
    """Return the severity for *name*, falling back to DEFAULT_SEVERITY."""
    return VALIDATOR_SEVERITY.get(name, DEFAULT_SEVERITY)


@dataclass
class ValidatorResult:
    """Result of running one validator script."""
    name: str
    severity: str
    exit_code: int
    group: str = "core"
    stdout: str = ""
    stderr: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    skipped: bool = False
    skip_reason: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 or self.skipped

    @property
    def status_label(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.exit_code == 0 else "FAIL"


def run_validators(
    repo_root: Optional[Path] = None,
    validator_name: Optional[str] = None,
    group: Optional[str] = None,
) -> list[ValidatorResult]:
    """Run all (or one named) validator scripts under core/governance/Validators/.

    Returns a list of :class:`ValidatorResult` in alphabetical order by name.

    Exit-code semantics preserved from deliver.sh: exit 0 = pass, anything
    else = fail.  Scripts that do not exist are reported as ``skipped``.
    """
    if group in {"layer1", "factory-layer1"}:
        return run_layer1_validators(repo_root=repo_root, validator_name=validator_name)
    if group not in {None, "", "core"}:
        raise ValueError(f"unknown validator group: {group}")

    if repo_root is None:
        repo_root = Path.cwd()
    repo_root = Path(repo_root)

    validators_dir = repo_root / "core" / "governance" / "Validators"

    if validator_name:
        names = [validator_name]
    else:
        if validators_dir.exists():
            names = sorted(
                p.stem for p in validators_dir.glob("*.sh")
                if p.stem != "wiring-guard"  # wiring-guard is run by health
            )
        else:
            names = list(VALIDATOR_SEVERITY.keys())

    results: list[ValidatorResult] = []
    for name in names:
        script = validators_dir / f"{name}.sh"
        severity = get_severity(name)

        if not script.exists():
            results.append(ValidatorResult(
                name=name, severity=severity, exit_code=0,
                skipped=True, skip_reason=f"script not found: {script}"
            ))
            continue

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                ["bash", str(script), "--repo-root", str(repo_root)],
                capture_output=True, text=True, timeout=120,
                cwd=str(repo_root),
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            results.append(ValidatorResult(
                name=name,
                severity=severity,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
            ))
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - t0) * 1000)
            results.append(ValidatorResult(
                name=name, severity=severity, exit_code=1,
                stderr="timed out after 120s", duration_ms=duration_ms,
            ))
        except Exception as exc:
            results.append(ValidatorResult(
                name=name, severity=severity, exit_code=1,
                stderr=str(exc),
            ))

    return results


def overall_exit_code(results: list[ValidatorResult]) -> int:
    """Return 0/1/2 exit code from a list of results.

    0 = all pass, 1 = any HALT failure, 2 = any BLOCK_MERGE failure, else 0
    (WARN failures don't change exit code — they're informational).
    """
    has_halt = any(not r.passed and r.severity == "HALT" for r in results)
    has_block = any(not r.passed and r.severity == "BLOCK_MERGE" for r in results)
    if has_halt:
        return 1
    if has_block:
        return 2
    return 0


Layer1Check = tuple[str, str, Callable[[Path], tuple[bool, str, dict[str, Any]]]]


def run_layer1_validators(
    repo_root: Optional[Path] = None,
    validator_name: Optional[str] = None,
) -> list[ValidatorResult]:
    """Run built-in Layer 1 structural validators.

    These checks validate the factory-created/adopted product repo shape. They
    intentionally live behind ``signalos validate --group layer1`` so the
    existing shell-validator behavior remains unchanged.
    """
    if repo_root is None:
        repo_root = Path.cwd()
    repo_root = Path(repo_root)

    checks = _layer1_checks()
    if validator_name:
        checks = [check for check in checks if check[0] == validator_name]
        if not checks:
            return [
                ValidatorResult(
                    name=validator_name,
                    group="layer1",
                    severity=get_severity(validator_name),
                    exit_code=0,
                    skipped=True,
                    skip_reason=f"layer1 validator not found: {validator_name}",
                )
            ]

    results: list[ValidatorResult] = []
    for name, severity, fn in checks:
        t0 = time.monotonic()
        try:
            passed, message, details = fn(repo_root)
            duration_ms = int((time.monotonic() - t0) * 1000)
            results.append(
                ValidatorResult(
                    name=name,
                    group="layer1",
                    severity=severity,
                    exit_code=0 if passed else 1,
                    message=message,
                    details=details,
                    stderr="" if passed else message,
                    duration_ms=duration_ms,
                )
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            results.append(
                ValidatorResult(
                    name=name,
                    group="layer1",
                    severity=severity,
                    exit_code=1,
                    message=f"{exc.__class__.__name__}: {exc}",
                    stderr=f"{exc.__class__.__name__}: {exc}",
                    duration_ms=duration_ms,
                )
            )
    return results


def _layer1_checks() -> list[Layer1Check]:
    return [
        ("layer1-workspace-root", "HALT", _check_workspace_root),
        ("layer1-runtime-state", "HALT", _check_runtime_state),
        ("layer1-audit-trail", "HALT", _check_audit_trail),
        ("layer1-governance-docs", "BLOCK_MERGE", _check_governance_docs),
        ("layer1-gates-readable", "BLOCK_MERGE", _check_gates_readable),
        ("layer1-source-traceability", "BLOCK_MERGE", _check_source_traceability),
        ("layer1-unknowns", "BLOCK_MERGE", _check_unknowns),
        ("layer1-profile", "BLOCK_MERGE", _check_profile),
        ("layer1-path-safety", "HALT", _check_path_safety),
        ("agent-prompt-contracts", "BLOCK_MERGE", _check_agent_prompt_contracts),
        ("constitution-integrity", "BLOCK_MERGE", _check_constitution_integrity),
    ]


def _check_constitution_integrity(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    from signalos_lib.validators.constitution_integrity import check_constitution_integrity
    return check_constitution_integrity(repo_root)


def _check_agent_prompt_contracts(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    from signalos_lib.product.prompt_contracts import validate_agent_prompt_directory

    agent_dir = repo_root / "core" / "execution" / "agents"
    if not agent_dir.is_dir():
        return (
            True,
            "agent prompt directory not installed in repo",
            {"agent_dir": str(agent_dir), "present": False},
        )
    result = validate_agent_prompt_directory(agent_dir)
    return (
        bool(result["valid"]),
        (
            "agent prompt contracts satisfy required sections"
            if result["valid"]
            else "agent prompt contracts missing required sections"
        ),
        result,
    )


def _check_workspace_root(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    exists = repo_root.exists()
    is_dir = repo_root.is_dir()
    return (
        exists and is_dir,
        "workspace root exists and is a directory" if exists and is_dir else "workspace root is missing or not a directory",
        {"repo_root": str(repo_root), "exists": exists, "is_dir": is_dir},
    )


def _check_runtime_state(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    required = [
        ".signalos",
        ".signalos/sessions",
        ".signalos/worktree-state.json",
    ]
    details = _required_paths(repo_root, required)
    state_path = repo_root / ".signalos" / "worktree-state.json"
    if state_path.exists() and state_path.is_file():
        try:
            json.loads(state_path.read_text(encoding="utf-8"))
            details["json_valid"] = True
        except json.JSONDecodeError as exc:
            details["json_valid"] = False
            details["json_error"] = str(exc)
    else:
        details["json_valid"] = False
    passed = all(item["exists"] for item in details["paths"]) and bool(details["json_valid"])
    return (
        passed,
        ".signalos runtime state is present" if passed else ".signalos runtime state is incomplete",
        details,
    )


def _check_audit_trail(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    audit = repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    details: dict[str, Any] = {
        "path": ".signalos/AUDIT_TRAIL.jsonl",
        "exists": audit.exists(),
        "is_file": audit.is_file(),
        "jsonl_valid": True,
        "appendable": False,
    }
    if not audit.exists() or not audit.is_file():
        return False, "audit trail is missing", details

    invalid_lines: list[int] = []
    with audit.open("r", encoding="utf-8") as fh:
        for idx, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                invalid_lines.append(idx)
    details["jsonl_valid"] = not invalid_lines
    details["invalid_lines"] = invalid_lines[:10]

    try:
        with audit.open("a", encoding="utf-8"):
            pass
        details["appendable"] = True
    except OSError as exc:
        details["appendable"] = False
        details["append_error"] = str(exc)

    passed = bool(details["jsonl_valid"] and details["appendable"])
    return (
        passed,
        "audit trail exists and is appendable" if passed else "audit trail is invalid or not appendable",
        details,
    )


def _check_governance_docs(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    required = [
        "core/governance/Governance/SOUL-DOCUMENT.md",
        "core/governance/Governance/CONSTITUTION.md",
        "core/governance/Governance/DECISION-DNA.md",
    ]
    details = _required_paths(repo_root, required, require_nonempty=True)
    passed = all(item["exists"] and item.get("nonempty", False) for item in details["paths"])
    return (
        passed,
        "required governance docs are present" if passed else "required governance docs are missing or empty",
        details,
    )


def _check_gates_readable(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    try:
        from signalos_lib.sign import GATE_MAP
    except Exception as exc:
        return False, f"gate map could not be imported: {exc}", {"import_error": str(exc)}

    expected = [f"G{i}" for i in range(6)]
    missing = [gate for gate in expected if gate not in GATE_MAP]
    artifact_count = {
        gate: len(GATE_MAP.get(gate, []))
        for gate in expected
    }
    unsafe_paths: list[str] = []
    for entries in GATE_MAP.values():
        for rel_path, _roles, _label in entries:
            if not _is_relative_safe_path(rel_path):
                unsafe_paths.append(rel_path)
                continue
            target = repo_root / rel_path
            if not _is_inside(repo_root, target):
                unsafe_paths.append(rel_path)
    passed = not missing and not unsafe_paths
    return (
        passed,
        "G0-G5 gate map is readable" if passed else "gate map is missing gates or contains unsafe paths",
        {"gates": expected, "missing": missing, "artifact_count": artifact_count, "unsafe_paths": unsafe_paths},
    )


def _check_source_traceability(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    sources_dir = repo_root / ".signalos" / "sources"
    candidates = [
        ".signalos/sources/initial-intent.json",
        ".signalos/sources/source-intent.json",
        ".signalos/source-intent.json",
        ".signalos/SOURCE_PROMPT.md",
        ".signalos/product/INTENT.json",
        ".signalos/product.json",
    ]
    existing = [rel for rel in candidates if (repo_root / rel).is_file()]
    if sources_dir.is_dir():
        existing.extend(
            f".signalos/sources/{p.name}"
            for p in sorted(sources_dir.iterdir())
            if p.is_file() and p.name not in {Path(rel).name for rel in existing}
        )
    passed = bool(existing)
    return (
        passed,
        "source intent is traceable" if passed else "source intent is not recorded",
        {"candidates": candidates, "existing": existing},
    )


def _check_unknowns(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    candidates = [
        ".signalos/unknowns.json",
        ".signalos/adoption/unknowns.json",
    ]
    existing = [rel for rel in candidates if (repo_root / rel).is_file()]
    details: dict[str, Any] = {"candidates": candidates, "existing": existing, "json_valid": False}
    if not existing:
        return False, "unknowns file is not recorded", details

    errors: list[str] = []
    for rel in existing:
        path = repo_root / rel
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, (list, dict)):
                errors.append(f"{rel}: expected JSON array or object")
        except json.JSONDecodeError as exc:
            errors.append(f"{rel}: {exc}")
    details["json_valid"] = not errors
    details["errors"] = errors
    return (
        not errors,
        "unknowns are recorded" if not errors else "unknowns file is invalid",
        details,
    )


def _check_profile(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    profile_path = repo_root / ".signalos" / "profile.json"
    details: dict[str, Any] = {
        "path": ".signalos/profile.json",
        "exists": profile_path.is_file(),
    }
    if not profile_path.is_file():
        details["profile_id"] = "generic"
        return True, "no explicit profile; generic fallback applies", details

    try:
        parsed = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        details["json_error"] = str(exc)
        return False, "profile metadata is invalid JSON", details

    profile_id = str(parsed.get("profile_id") or "").strip()
    details["profile_id"] = profile_id
    if not profile_id:
        return False, "profile metadata is missing profile_id", details

    try:
        from signalos_lib.profiles import load_profile, validate_profile_contract

        profile = load_profile(profile_id)
        report = validate_profile_contract(profile)
    except Exception as exc:
        details["error"] = str(exc)
        return False, f"profile validation failed: {exc}", details

    required_paths = [template.destination for template in profile.required_templates if template.required]
    if profile.ci.enabled:
        required_paths.extend(profile.ci.files)
    missing = [rel for rel in required_paths if not (repo_root / rel).exists()]
    details["validation"] = report.to_dict()
    details["required_paths"] = required_paths
    details["missing"] = missing
    ok = report.ok and not missing
    return (
        ok,
        "profile metadata and generated files validate" if ok else "profile validation failed",
        details,
    )


def _check_path_safety(repo_root: Path) -> tuple[bool, str, dict[str, Any]]:
    generated_roots = [
        ".signalos",
        "core",
    ]
    checked: list[dict[str, Any]] = []
    unsafe: list[str] = []
    for rel in generated_roots:
        path = repo_root / rel
        if not path.exists():
            checked.append({"path": rel, "exists": False, "inside_workspace": True})
            continue
        inside = _is_inside(repo_root, path)
        checked.append({"path": rel, "exists": True, "inside_workspace": inside})
        if not inside:
            unsafe.append(rel)
    return (
        not unsafe,
        "generated roots resolve inside workspace" if not unsafe else "generated roots escape workspace",
        {"checked": checked, "unsafe": unsafe},
    )


def _required_paths(repo_root: Path, rel_paths: list[str], require_nonempty: bool = False) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for rel in rel_paths:
        path = repo_root / rel
        item: dict[str, Any] = {
            "path": rel,
            "exists": path.exists(),
            "is_dir": path.is_dir(),
            "is_file": path.is_file(),
        }
        if require_nonempty and path.is_file():
            item["nonempty"] = path.stat().st_size > 0
        items.append(item)
    return {"paths": items}


def _is_relative_safe_path(rel_path: str) -> bool:
    path = Path(rel_path)
    return not path.is_absolute() and ".." not in path.parts


def _is_inside(repo_root: Path, target: Path) -> bool:
    try:
        root = repo_root.resolve()
        resolved = target.resolve()
        resolved.relative_to(root)
        return True
    except Exception:
        return False
