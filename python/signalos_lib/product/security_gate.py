"""Security gate for the SignalOS Product Delivery Bridge.

Runs security validation on generated product code as part of the
delivery pipeline.  Scans for injection risks, generates threat models
when security constraints are present, detects PII/GDPR requirements,
plants canary tokens, and checks security posture declarations.

This module never crashes the delivery -- failures are captured and
returned as status="warning" with details.
"""

from __future__ import annotations

__all__ = [
    "detect_pii_entities",
    "get_compliance_requirements",
    "load_security_result",
    "run_security_gate",
    "write_security_result",
]

import json
import re
import traceback
from pathlib import Path
from typing import Any

from ..security import (
    generate_owasp_stride,
    plant_canary_token,
    scan_injection_risks,
)

# ---------------------------------------------------------------------------
# PII indicator words
# ---------------------------------------------------------------------------

_PII_INDICATORS = {
    "patient", "user", "person", "customer", "client",
    "employee", "member", "contact", "account", "profile",
    "email", "phone", "address", "ssn", "dob", "name",
}

# ---------------------------------------------------------------------------
# Additional injection patterns for TSX/JS files
# ---------------------------------------------------------------------------

_JS_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"dangerouslySetInnerHTML"),
        "XSS risk via dangerouslySetInnerHTML",
    ),
    (
        re.compile(r"\.innerHTML\s*="),
        "XSS risk via innerHTML assignment",
    ),
    (
        re.compile(r"document\.write\s*\("),
        "XSS risk via document.write",
    ),
]

_JS_EXTENSIONS = {".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs"}


# ---------------------------------------------------------------------------
# PII detection
# ---------------------------------------------------------------------------

def detect_pii_entities(
    entities: list[str],
    llm_pii: list[str] | None = None,
) -> list[str]:
    """Return entity names that likely contain PII.

    If *llm_pii* is provided (from the LLM refinement pass), use it
    directly -- it supersedes the static indicator list.  Otherwise
    fall back to matching against ``_PII_INDICATORS``.
    """
    if llm_pii:
        # LLM already classified which entities hold PII
        entity_lower = {e.lower() for e in entities}
        return [p for p in llm_pii if p.lower() in entity_lower or p in entities]

    results: list[str] = []
    for entity in entities:
        words = re.split(r"[^a-zA-Z]+", entity.lower())
        for word in words:
            if word in _PII_INDICATORS:
                results.append(entity)
                break
    return results


# ---------------------------------------------------------------------------
# Compliance requirement derivation
# ---------------------------------------------------------------------------

def get_compliance_requirements(intent: dict[str, Any]) -> list[str]:
    """Derive compliance requirements from intent.

    Sources: security_constraints, auth_requirements, and PII detection.
    """
    requirements: list[str] = []

    # Direct from security_constraints
    for constraint in intent.get("security_constraints", []):
        upper = constraint.upper().replace("-", "").replace("_", "").replace(" ", "")
        if "HIPAA" in upper:
            requirements.append("HIPAA")
        elif "GDPR" in upper:
            requirements.append("GDPR")
        elif "SOC2" in upper or "SOC 2" in upper:
            requirements.append("SOC2")
        elif "PCI" in upper:
            requirements.append("PCI")
        elif "COMPLIANCE" in upper:
            requirements.append("COMPLIANCE")
        else:
            requirements.append(constraint.upper())

    # Auth requirements that imply compliance
    auth_reqs = intent.get("auth_requirements", [])
    if "rbac" in auth_reqs and "RBAC" not in requirements:
        requirements.append("RBAC")

    # PII detection implies GDPR
    entities = intent.get("entities", [])
    pii = detect_pii_entities(entities)
    if pii and "GDPR" not in requirements:
        requirements.append("GDPR")

    return requirements


# ---------------------------------------------------------------------------
# Security gate runner
# ---------------------------------------------------------------------------

def run_security_gate(
    repo_root: Path,
    intent: dict[str, Any],
    generated_files: list[str],
    profile: str,
) -> dict[str, Any]:
    """Run security validation on the generated product.

    Steps:
    1. Run injection scan on all generated source files
    2. If intent has security_constraints -> run threat model on detected surfaces
    3. If intent has entities that look like PII -> flag GDPR requirement
    4. Plant a canary token for the product
    5. Check security posture declaration exists

    Returns a result dict with status, scan details, and recommendations.
    Never raises -- errors produce status="warning".
    """
    result: dict[str, Any] = {
        "status": "passed",
        "injection_scan": {
            "files_scanned": 0,
            "issues_found": [],
        },
        "threat_model": None,
        "gdpr_required": False,
        "gdpr_reason": None,
        "canary_planted": False,
        "security_posture_declared": False,
        "compliance_requirements": [],
        "recommendations": [],
    }

    try:
        # --- 1. Injection scan ---
        all_issues: list[dict[str, Any]] = []
        files_scanned = 0

        for file_path in generated_files:
            if not file_path:
                continue

            path = Path(file_path)
            if not path.is_absolute():
                path = repo_root / file_path

            if not path.exists():
                continue

            files_scanned += 1

            # Use the existing scan_injection_risks from security.py
            issues = scan_injection_risks(repo_root, file_path)
            all_issues.extend(issues)

            # Additional JS/TS-specific patterns
            suffix = path.suffix.lower()
            if suffix in _JS_EXTENSIONS:
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    lines = content.splitlines()
                    for lineno, line in enumerate(lines, start=1):
                        for compiled, risk in _JS_INJECTION_PATTERNS:
                            if compiled.search(line):
                                all_issues.append({
                                    "file": file_path,
                                    "line": lineno,
                                    "pattern": compiled.pattern,
                                    "risk": risk,
                                })
                except OSError:
                    pass

        result["injection_scan"]["files_scanned"] = files_scanned
        result["injection_scan"]["issues_found"] = all_issues

        # --- 2. Threat model ---
        security_constraints = intent.get("security_constraints", [])
        if security_constraints:
            surfaces = intent.get("ux_surfaces", []) + intent.get("api_surfaces", [])
            if not surfaces:
                surfaces = ["default"]

            threats_generated = 0
            high_severity = 0

            for surface in surfaces:
                try:
                    entries = generate_owasp_stride(
                        surface=surface,
                        wave="product-delivery",
                        repo_root=repo_root,
                    )
                    threats_generated += len(entries)
                    high_severity += sum(
                        1 for e in entries
                        if e.severity in ("high", "critical")
                    )
                except Exception:
                    pass

            result["threat_model"] = {
                "surfaces_scanned": len(surfaces),
                "threats_generated": threats_generated,
                "high_severity": high_severity,
            }

        # --- 3. GDPR / PII detection ---
        entities = intent.get("entities", [])
        llm_pii = intent.get("pii_entities")  # from LLM refinement, if available
        pii_entities = detect_pii_entities(entities, llm_pii=llm_pii)
        if pii_entities:
            result["gdpr_required"] = True
            result["gdpr_reason"] = (
                f"Intent contains PII entities: {', '.join(pii_entities)}"
            )

        # --- 4. Canary token ---
        try:
            plant_canary_token(repo_root, label="product")
            result["canary_planted"] = True
        except Exception:
            result["canary_planted"] = False

        # --- 5. Security posture check ---
        # Use the security-posture-guard validator (same logic as Layer 1)
        # to check for explicit security_surfaces declaration.
        try:
            from ..validate_cmd import _check_security_posture
            posture_passed, _posture_msg, posture_details = _check_security_posture(repo_root)
            if posture_passed:
                result["security_posture_declared"] = True
            result["security_posture_details"] = posture_details
        except Exception:
            # Fallback: simple content check on constitution
            constitution_path = repo_root / "core" / "governance" / "Governance" / "CONSTITUTION.md"
            if not constitution_path.exists():
                constitution_path = repo_root / ".signalos" / "CONSTITUTION.md"
            if constitution_path.exists():
                try:
                    content = constitution_path.read_text(encoding="utf-8", errors="replace")
                    if re.search(r"(?i)security[_\- ]surfaces", content):
                        result["security_posture_declared"] = True
                except OSError:
                    pass

        if not result["security_posture_declared"] and security_constraints:
            # Having declared security constraints in intent counts
            result["security_posture_declared"] = True

        # --- 6. Compliance requirements ---
        result["compliance_requirements"] = get_compliance_requirements(intent)

        # --- 7. Determine overall status ---
        if all_issues:
            # Any injection issue = failed
            result["status"] = "failed"
            result["recommendations"].append(
                "Fix injection vulnerabilities before deployment"
            )
        if result["gdpr_required"]:
            result["recommendations"].append(
                "Implement GDPR data handling: consent, export, and purge capabilities"
            )
        if not result["security_posture_declared"]:
            result["recommendations"].append(
                "Declare security posture in .signalos/CONSTITUTION.md"
            )
        if result["threat_model"] and result["threat_model"]["high_severity"] > 0:
            result["recommendations"].append(
                "Review high-severity threats in .signalos/security/threats.jsonl"
            )

    except Exception:
        # Never crash delivery -- degrade gracefully
        result["status"] = "warning"
        result["recommendations"].append(
            f"Security gate encountered an error: {traceback.format_exc().splitlines()[-1]}"
        )

    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_security_result(result: dict[str, Any], signalos_dir: Path) -> Path:
    """Write to .signalos/product/SECURITY_RESULT.json."""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "SECURITY_RESULT.json"
    path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_security_result(signalos_dir: Path) -> dict[str, Any] | None:
    """Load security result from .signalos/product/SECURITY_RESULT.json."""
    path = signalos_dir / "product" / "SECURITY_RESULT.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
