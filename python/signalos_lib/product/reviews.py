# signalos_lib/product/reviews.py
"""Architecture review and review-readiness artifacts for product delivery."""

from __future__ import annotations

__all__ = [
    "ARCH_REVIEW_FIELDS",
    "ARCH_REVIEW_FILENAME",
    "ARCH_REVIEW_SCHEMA_VERSION",
    "REVIEW_READINESS_FIELDS",
    "REVIEW_READINESS_FILENAME",
    "REVIEW_READINESS_SCHEMA_VERSION",
    "REVIEW_READINESS_STATUS_FIELDS",
    "build_arch_review",
    "build_review_readiness",
    "load_arch_review",
    "load_review_readiness",
    "validate_arch_review",
    "validate_review_readiness",
    "write_arch_review",
    "write_review_readiness",
]

from pathlib import Path
from typing import Any
import json


ARCH_REVIEW_SCHEMA_VERSION = "signalos.arch_review.v1"
REVIEW_READINESS_SCHEMA_VERSION = "signalos.review_readiness.v1"

ARCH_REVIEW_FILENAME = "ARCH_REVIEW.yaml"
REVIEW_READINESS_FILENAME = "REVIEW_READINESS.yaml"

ARCH_REVIEW_FIELDS = (
    "system_boundaries",
    "data_flow",
    "state_transitions",
    "failure_modes",
    "trust_boundaries",
    "edge_cases",
    "test_strategy",
    "open_risks",
    "blocking_findings",
)

REVIEW_READINESS_STATUS_FIELDS = (
    "strategy_status",
    "scope_status",
    "architecture_status",
    "design_status",
    "build_status",
    "test_status",
    "browser_qa_status",
    "security_status",
    "docs_status",
    "handoff_status",
)

REVIEW_READINESS_FIELDS = REVIEW_READINESS_STATUS_FIELDS + (
    "blocking_items",
    "ready",
)


def build_arch_review(
    system_boundaries: Any = None,
    data_flow: Any = None,
    state_transitions: Any = None,
    failure_modes: Any = None,
    trust_boundaries: Any = None,
    edge_cases: Any = None,
    test_strategy: Any = None,
    open_risks: Any = None,
    blocking_findings: Any = None,
) -> dict[str, Any]:
    """Build a complete ``ARCH_REVIEW.yaml`` artifact dictionary."""
    return {
        "schema_version": ARCH_REVIEW_SCHEMA_VERSION,
        "system_boundaries": _as_list(system_boundaries),
        "data_flow": _as_list(data_flow),
        "state_transitions": _as_list(state_transitions),
        "failure_modes": _as_list(failure_modes),
        "trust_boundaries": _as_list(trust_boundaries),
        "edge_cases": _as_list(edge_cases),
        "test_strategy": _as_list(test_strategy),
        "open_risks": _as_list(open_risks),
        "blocking_findings": _as_list(blocking_findings),
    }


def validate_arch_review(artifact: dict[str, Any] | None) -> dict[str, Any]:
    """Validate an architecture review artifact.

    ``valid`` covers structural validity. ``passes`` is false when valid
    structure still contains blocking findings.
    """
    errors: list[str] = []
    missing_sections: list[str] = []

    if not isinstance(artifact, dict):
        return {
            "valid": False,
            "passes": False,
            "blocked": False,
            "blockers": [],
            "missing_sections": list(ARCH_REVIEW_FIELDS),
            "errors": ["ARCH_REVIEW.yaml must be a YAML mapping"],
        }

    for field in ARCH_REVIEW_FIELDS:
        if _missing_section(artifact.get(field, None), field in artifact):
            missing_sections.append(field)

    if missing_sections:
        errors.append(
            "ARCH_REVIEW.yaml is missing required sections: "
            + ", ".join(missing_sections)
        )

    blockers = _as_text_list(artifact.get("blocking_findings", []))
    valid = not errors
    blocked = bool(blockers)

    return {
        "valid": valid,
        "passes": valid and not blocked,
        "blocked": blocked,
        "blockers": blockers,
        "missing_sections": missing_sections,
        "errors": errors,
    }


def write_arch_review(artifact: dict[str, Any], signalos_dir: Path) -> Path:
    """Write ``.signalos/product/ARCH_REVIEW.yaml``."""
    path = signalos_dir / "product" / ARCH_REVIEW_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_yaml(artifact), encoding="utf-8")
    return path


def load_arch_review(signalos_dir: Path) -> dict[str, Any] | None:
    """Load ``.signalos/product/ARCH_REVIEW.yaml``, returning ``None`` if absent."""
    return _load_yaml_mapping(signalos_dir / "product" / ARCH_REVIEW_FILENAME)


def build_review_readiness(
    strategy_status: str = "pending",
    scope_status: str = "pending",
    architecture_status: str = "pending",
    design_status: str = "pending",
    build_status: str = "pending",
    test_status: str = "pending",
    browser_qa_status: str = "pending",
    security_status: str = "pending",
    docs_status: str = "pending",
    handoff_status: str = "pending",
    blocking_items: Any = None,
    ready: bool = False,
) -> dict[str, Any]:
    """Build a complete ``REVIEW_READINESS.yaml`` artifact dictionary."""
    return {
        "schema_version": REVIEW_READINESS_SCHEMA_VERSION,
        "strategy_status": strategy_status,
        "scope_status": scope_status,
        "architecture_status": architecture_status,
        "design_status": design_status,
        "build_status": build_status,
        "test_status": test_status,
        "browser_qa_status": browser_qa_status,
        "security_status": security_status,
        "docs_status": docs_status,
        "handoff_status": handoff_status,
        "blocking_items": _as_list(blocking_items),
        "ready": ready,
    }


def validate_review_readiness(artifact: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a review-readiness artifact."""
    errors: list[str] = []
    missing_sections: list[str] = []
    missing_statuses: list[str] = []

    if not isinstance(artifact, dict):
        return {
            "valid": False,
            "ready": False,
            "declared_ready": False,
            "passes": False,
            "blocked": False,
            "blockers": [],
            "missing_sections": list(REVIEW_READINESS_FIELDS),
            "missing_statuses": list(REVIEW_READINESS_STATUS_FIELDS),
            "errors": ["REVIEW_READINESS.yaml must be a YAML mapping"],
        }

    for field in REVIEW_READINESS_STATUS_FIELDS:
        if _missing_status(artifact.get(field, None), field in artifact):
            missing_statuses.append(field)

    for field in ("blocking_items", "ready"):
        if _missing_section(artifact.get(field, None), field in artifact):
            missing_sections.append(field)

    if missing_statuses:
        errors.append(
            "REVIEW_READINESS.yaml is missing required statuses: "
            + ", ".join(missing_statuses)
        )
    if missing_sections:
        errors.append(
            "REVIEW_READINESS.yaml is missing required sections: "
            + ", ".join(missing_sections)
        )

    blockers = _as_text_list(artifact.get("blocking_items", []))
    declared_ready = artifact.get("ready", False)

    if "ready" in artifact and not isinstance(declared_ready, bool):
        errors.append("REVIEW_READINESS.yaml field 'ready' must be boolean")
        declared_ready = False

    if declared_ready and blockers:
        errors.append(
            "REVIEW_READINESS.yaml declares ready=true while blocking_items "
            "is non-empty"
        )

    valid = not errors
    blocked = bool(blockers)
    ready = valid and bool(declared_ready) and not blocked

    return {
        "valid": valid,
        "ready": ready,
        "declared_ready": bool(declared_ready),
        "passes": ready,
        "blocked": blocked,
        "blockers": blockers,
        "missing_sections": missing_sections,
        "missing_statuses": missing_statuses,
        "errors": errors,
    }


def write_review_readiness(artifact: dict[str, Any], signalos_dir: Path) -> Path:
    """Write ``.signalos/product/REVIEW_READINESS.yaml``."""
    path = signalos_dir / "product" / REVIEW_READINESS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_yaml(artifact), encoding="utf-8")
    return path


def load_review_readiness(signalos_dir: Path) -> dict[str, Any] | None:
    """Load ``.signalos/product/REVIEW_READINESS.yaml``, or ``None`` if absent."""
    return _load_yaml_mapping(signalos_dir / "product" / REVIEW_READINESS_FILENAME)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _as_text_list(value: Any) -> list[str]:
    items = _as_list(value)
    return [str(item).strip() for item in items if str(item).strip()]


def _missing_section(value: Any, present: bool) -> bool:
    if not present or value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _missing_status(value: Any, present: bool) -> bool:
    if not present or value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _dump_yaml(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _load_yaml_mapping(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = _load_yaml_if_available(text)
    return data if isinstance(data, dict) else None


def _load_yaml_if_available(text: str) -> Any:
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return None

    try:
        return yaml.safe_load(text)
    except Exception:
        return None
