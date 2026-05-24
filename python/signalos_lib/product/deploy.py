"""Deploy decision and evidence recording for SignalOS-governed product repos.

This module records deploy decisions and evidence - it does NOT execute
deployments.  Deploy is always opt-in, explicit, and evidence-backed.
"""

from __future__ import annotations

__all__ = [
    "make_deploy_decision",
    "prepare_deploy_evidence",
    "write_deploy_decision",
    "load_deploy_decision",
    "write_deploy_evidence",
    "load_deploy_evidence",
]

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "signalos.deploy_decision.v1"
VALID_MODES = {"none", "prepare", "live"}

_REACT_VITE_CHECKLIST = [
    "Run production build",
    "Configure hosting provider",
    "Set environment variables",
    "Run smoke test on staging",
]

_GENERIC_CHECKLIST = [
    "Review generated artifacts",
    "Determine deployment target",
]

_COMMON_CHECKLIST = [
    "Get stakeholder approval",
    "Verify all tests pass",
]


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Deploy decision
# ---------------------------------------------------------------------------

def make_deploy_decision(
    mode: str,
    validation_result: dict[str, Any] | None,
    repo_root: Path,
) -> dict[str, Any]:
    """Make and record a deploy decision.

    The function only records the decision - it never performs a deployment.

    Parameters
    ----------
    mode
        One of ``"none"``, ``"prepare"``, or ``"live"``.
    validation_result
        Output of product validation (e.g. ``check_product_closure``).
        May be *None* when validation has not been run.
    repo_root
        Repository root path (used for context only, nothing is written here).

    Returns
    -------
    dict
        A deploy-decision payload with schema version, mode, timestamps,
        allow/block status, reason, blockers, and evidence summary.
    """

    if mode not in VALID_MODES:
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": mode,
            "decided_at": _utc_now(),
            "validation_status": "unknown",
            "deploy_allowed": False,
            "reason": f"Invalid mode: {mode!r} - expected one of {sorted(VALID_MODES)}",
            "blockers": [f"Invalid mode: {mode!r}"],
            "evidence": {
                "validation_closeable": None,
                "validation_level": None,
            },
            "error": True,
        }

    validation_status = _extract_validation_status(validation_result)
    closeable = _extract_closeable(validation_result)
    level = _extract_level(validation_result)

    if mode == "none":
        return _build(
            mode=mode,
            validation_status=validation_status,
            deploy_allowed=False,
            reason="No deployment requested",
            blockers=[],
            closeable=closeable,
            level=level,
        )

    if mode == "prepare":
        return _build(
            mode=mode,
            validation_status=validation_status,
            deploy_allowed=False,
            reason="Prepare mode - evidence created, no live deploy",
            blockers=[],
            closeable=closeable,
            level=level,
        )

    # mode == "live"
    blockers: list[str] = []

    if validation_result is None:
        blockers.append("No validation result")
        return _build(
            mode=mode,
            validation_status=validation_status,
            deploy_allowed=False,
            reason="Live deploy blocked",
            blockers=blockers,
            closeable=closeable,
            level=level,
        )

    if not closeable:
        blockers.append(f"Validation not ready: {level}")
        return _build(
            mode=mode,
            validation_status=validation_status,
            deploy_allowed=False,
            reason="Live deploy blocked",
            blockers=blockers,
            closeable=closeable,
            level=level,
        )

    return _build(
        mode=mode,
        validation_status=validation_status,
        deploy_allowed=True,
        reason="Validation passed, live deploy authorized",
        blockers=[],
        closeable=closeable,
        level=level,
    )


def _build(
    *,
    mode: str,
    validation_status: str,
    deploy_allowed: bool,
    reason: str,
    blockers: list[str],
    closeable: bool | None,
    level: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "decided_at": _utc_now(),
        "validation_status": validation_status,
        "deploy_allowed": deploy_allowed,
        "reason": reason,
        "blockers": blockers,
        "evidence": {
            "validation_closeable": closeable,
            "validation_level": level,
        },
    }


def _extract_validation_status(result: dict[str, Any] | None) -> str:
    if result is None:
        return "unknown"
    return str(result.get("status", result.get("validation_status", "unknown")))


def _extract_closeable(result: dict[str, Any] | None) -> bool | None:
    if result is None:
        return None
    return bool(result.get("closeable", result.get("ok", False)))


def _extract_level(result: dict[str, Any] | None) -> str | None:
    if result is None:
        return None
    return str(result.get("level", result.get("status", "unknown")))


# ---------------------------------------------------------------------------
# Deploy evidence (prepare mode)
# ---------------------------------------------------------------------------

def prepare_deploy_evidence(
    repo_root: Path,
    decision: dict[str, Any],
    product_name: str,
    profile: str,
) -> dict[str, Any]:
    """Create deploy preparation evidence (for *prepare* mode).

    Writes deploy notes to ``.signalos/product/DEPLOY_EVIDENCE.json``.
    The function never performs a deployment.

    Returns a dict with product name, profile, decision snapshot, release
    notes, deploy checklist, and timestamp.
    """

    checklist = _build_checklist(profile)
    release_notes = _generate_release_notes(product_name, decision)

    evidence: dict[str, Any] = {
        "product_name": product_name,
        "profile": profile,
        "decision": decision,
        "release_notes": release_notes,
        "deploy_checklist": checklist,
        "created_at": _utc_now(),
    }

    signalos_dir = repo_root / ".signalos"
    write_deploy_evidence(evidence, signalos_dir)

    return evidence


def _build_checklist(profile: str) -> list[str]:
    if profile == "react-vite":
        return _REACT_VITE_CHECKLIST + _COMMON_CHECKLIST
    return _GENERIC_CHECKLIST + _COMMON_CHECKLIST


def _generate_release_notes(product_name: str, decision: dict[str, Any]) -> str:
    mode = decision.get("mode", "unknown")
    allowed = decision.get("deploy_allowed", False)
    status = "authorized" if allowed else "not authorized"
    return (
        f"Deploy decision for {product_name}: mode={mode}, deploy {status}. "
        f"Reason: {decision.get('reason', 'none')}."
    )


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def write_deploy_decision(decision: dict[str, Any], signalos_dir: Path) -> Path:
    """Write deploy decision to ``.signalos/product/DEPLOY_DECISION.json``."""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "DEPLOY_DECISION.json"
    path.write_text(
        json.dumps(decision, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_deploy_decision(signalos_dir: Path) -> dict[str, Any] | None:
    """Load deploy decision from ``.signalos/product/DEPLOY_DECISION.json``."""
    path = signalos_dir / "product" / "DEPLOY_DECISION.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_deploy_evidence(evidence: dict[str, Any], signalos_dir: Path) -> Path:
    """Write deploy evidence to ``.signalos/product/DEPLOY_EVIDENCE.json``."""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "DEPLOY_EVIDENCE.json"
    path.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_deploy_evidence(signalos_dir: Path) -> dict[str, Any] | None:
    """Load deploy evidence from ``.signalos/product/DEPLOY_EVIDENCE.json``."""
    path = signalos_dir / "product" / "DEPLOY_EVIDENCE.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
