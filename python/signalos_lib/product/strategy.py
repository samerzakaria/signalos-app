"""Strategy and scope decision artifacts for SignalOS product delivery.

The artifacts are deterministic gate inputs: agents can propose options and
decisions, but SignalOS validation decides whether the artifacts are complete
enough for approval and traceability.
"""

from __future__ import annotations

__all__ = [
    "STRATEGY_REVIEW_SCHEMA_VERSION",
    "SCOPE_DECISIONS_SCHEMA_VERSION",
    "STRATEGY_REVIEW_FILENAME",
    "SCOPE_DECISIONS_FILENAME",
    "VALID_DISPOSITIONS",
    "build_strategy_review",
    "build_scope_decisions",
    "validate_strategy_review",
    "validate_scope_decisions",
    "write_strategy_review",
    "load_strategy_review",
    "write_scope_decisions",
    "load_scope_decisions",
    "strategy_review_path",
    "scope_decisions_path",
]

import json
from pathlib import Path
from typing import Any

STRATEGY_REVIEW_SCHEMA_VERSION = "signalos.strategy_review.v1"
SCOPE_DECISIONS_SCHEMA_VERSION = "signalos.scope_decisions.v1"

STRATEGY_REVIEW_FILENAME = "STRATEGY_REVIEW.yaml"
SCOPE_DECISIONS_FILENAME = "SCOPE_DECISIONS.yaml"

VALID_DISPOSITIONS = frozenset({"accepted", "rejected", "deferred"})

_STRATEGY_REQUIRED_FIELDS = (
    "product_thesis",
    "target_user",
    "job_to_be_done",
    "literal_request_risk",
    "ten_star_options",
    "scope_reduction_options",
    "required_questions",
    "assumptions",
)

_STRATEGY_TEXT_FIELDS = (
    "product_thesis",
    "target_user",
    "job_to_be_done",
    "literal_request_risk",
)

_STRATEGY_LIST_FIELDS = (
    "ten_star_options",
    "scope_reduction_options",
    "required_questions",
    "assumptions",
)

_TRACE_KEYS = (
    "tickets",
    "ticket_refs",
    "acceptance_criteria",
    "acceptance_criteria_refs",
    "acceptance_ids",
)


def build_strategy_review(
    *,
    product_thesis: str,
    target_user: str,
    job_to_be_done: str,
    literal_request_risk: str,
    ten_star_options: list[Any] | None = None,
    scope_reduction_options: list[Any] | None = None,
    required_questions: list[Any] | None = None,
    assumptions: list[Any] | None = None,
) -> dict[str, Any]:
    """Build a ``STRATEGY_REVIEW.yaml`` artifact payload.

    ``ten_star_options`` and ``scope_reduction_options`` may contain strings
    or dicts. String options are converted to deferred option dicts. Dicts are
    copied and receive deterministic ids/default ``deferred`` dispositions
    when omitted.
    """

    return {
        "schema_version": STRATEGY_REVIEW_SCHEMA_VERSION,
        "product_thesis": product_thesis,
        "target_user": target_user,
        "job_to_be_done": job_to_be_done,
        "literal_request_risk": literal_request_risk,
        "ten_star_options": _normalize_items(
            ten_star_options or [],
            id_prefix="TSO",
            text_key="title",
        ),
        "scope_reduction_options": _normalize_items(
            scope_reduction_options or [],
            id_prefix="SRO",
            text_key="title",
        ),
        "required_questions": list(required_questions or []),
        "assumptions": list(assumptions or []),
    }


def build_scope_decisions(
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a ``SCOPE_DECISIONS.yaml`` artifact payload."""

    normalized: list[dict[str, Any]] = []
    for index, decision in enumerate(decisions or [], start=1):
        item = dict(decision)
        item.setdefault("id", f"SD-{index:03d}")
        item.setdefault("disposition", "deferred")
        item.setdefault("tickets", [])
        item.setdefault("acceptance_criteria", [])
        normalized.append(item)

    return {
        "schema_version": SCOPE_DECISIONS_SCHEMA_VERSION,
        "decisions": normalized,
    }


def validate_strategy_review(review: dict[str, Any]) -> list[str]:
    """Return validation errors for a strategy review artifact."""

    errors: list[str] = []
    if not isinstance(review, dict):
        return ["artifact: must be a mapping"]

    for field in _STRATEGY_REQUIRED_FIELDS:
        if field not in review:
            errors.append(f"{field}: required")

    schema_version = review.get("schema_version")
    if schema_version not in (None, STRATEGY_REVIEW_SCHEMA_VERSION):
        errors.append(
            "schema_version: must be "
            f"{STRATEGY_REVIEW_SCHEMA_VERSION!r} when present"
        )

    for field in _STRATEGY_TEXT_FIELDS:
        value = review.get(field)
        if field in review and not _is_non_empty_string(value):
            errors.append(f"{field}: must be a non-empty string")

    for field in _STRATEGY_LIST_FIELDS:
        value = review.get(field)
        if field in review and not isinstance(value, list):
            errors.append(f"{field}: must be a list")

    for field in ("ten_star_options", "scope_reduction_options"):
        value = review.get(field)
        if isinstance(value, list):
            errors.extend(_validate_disposition_items(value, field))

    return errors


def validate_scope_decisions(scope: dict[str, Any]) -> list[str]:
    """Return validation errors for a scope decisions artifact.

    Accepted decisions must trace to at least one ticket or acceptance
    criterion. Rejected and deferred decisions may remain untraced.
    """

    errors: list[str] = []
    if not isinstance(scope, dict):
        return ["artifact: must be a mapping"]

    schema_version = scope.get("schema_version")
    if schema_version not in (None, SCOPE_DECISIONS_SCHEMA_VERSION):
        errors.append(
            "schema_version: must be "
            f"{SCOPE_DECISIONS_SCHEMA_VERSION!r} when present"
        )

    decisions = scope.get("decisions")
    if not isinstance(decisions, list):
        return errors + ["decisions: must be a list"]

    errors.extend(_validate_disposition_items(decisions, "decisions"))

    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            continue
        if decision.get("disposition") == "accepted" and not _has_trace(decision):
            label = _item_label(decision, index)
            errors.append(
                f"decisions[{label}]: accepted decisions must trace to "
                "tickets or acceptance_criteria"
            )

    return errors


def write_strategy_review(
    review: dict[str, Any],
    root_or_signalos_dir: Path,
) -> Path:
    """Write ``.signalos/product/STRATEGY_REVIEW.yaml``."""

    path = strategy_review_path(root_or_signalos_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_json_compatible_yaml(review), encoding="utf-8")
    return path


def load_strategy_review(root_or_signalos_dir: Path) -> dict[str, Any] | None:
    """Load ``.signalos/product/STRATEGY_REVIEW.yaml`` if present."""

    return _load_mapping(strategy_review_path(root_or_signalos_dir))


def write_scope_decisions(
    scope: dict[str, Any],
    root_or_signalos_dir: Path,
) -> Path:
    """Write ``.signalos/product/SCOPE_DECISIONS.yaml``."""

    path = scope_decisions_path(root_or_signalos_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_json_compatible_yaml(scope), encoding="utf-8")
    return path


def load_scope_decisions(root_or_signalos_dir: Path) -> dict[str, Any] | None:
    """Load ``.signalos/product/SCOPE_DECISIONS.yaml`` if present."""

    return _load_mapping(scope_decisions_path(root_or_signalos_dir))


def strategy_review_path(root_or_signalos_dir: Path) -> Path:
    """Return the strategy review path for a repo root or ``.signalos`` dir."""

    return _product_dir(root_or_signalos_dir) / STRATEGY_REVIEW_FILENAME


def scope_decisions_path(root_or_signalos_dir: Path) -> Path:
    """Return the scope decisions path for a repo root or ``.signalos`` dir."""

    return _product_dir(root_or_signalos_dir) / SCOPE_DECISIONS_FILENAME


def _normalize_items(
    items: list[Any],
    *,
    id_prefix: str,
    text_key: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(items, start=1):
        if isinstance(raw, dict):
            item = dict(raw)
        else:
            item = {text_key: str(raw)}
        item.setdefault("id", f"{id_prefix}-{index:03d}")
        item.setdefault("disposition", "deferred")
        normalized.append(item)
    return normalized


def _validate_disposition_items(
    items: list[Any],
    field: str,
) -> list[str]:
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{field}[{index}]: must be a mapping")
            continue

        label = _item_label(item, index)
        if "disposition" not in item:
            errors.append(f"{field}[{label}].disposition: required")
            continue

        disposition = item.get("disposition")
        if disposition not in VALID_DISPOSITIONS:
            errors.append(
                f"{field}[{label}].disposition: must be one of "
                f"{', '.join(sorted(VALID_DISPOSITIONS))}"
            )
    return errors


def _has_trace(decision: dict[str, Any]) -> bool:
    for key in _TRACE_KEYS:
        if _has_non_empty_value(decision.get(key)):
            return True
    return False


def _has_non_empty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_non_empty_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_non_empty_value(item) for item in value.values())
    return False


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _item_label(item: dict[str, Any], index: int) -> str:
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id.strip():
        return item_id
    return str(index)


def _product_dir(root_or_signalos_dir: Path) -> Path:
    path = Path(root_or_signalos_dir)
    if path.name == "product" and path.parent.name == ".signalos":
        return path
    if path.name == ".signalos":
        return path / "product"
    return path / ".signalos" / "product"


def _dump_json_compatible_yaml(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _load_mapping(path: Path) -> dict[str, Any] | None:
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
