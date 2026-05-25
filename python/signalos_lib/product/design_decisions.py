# signalos_lib/product/design_decisions.py
# Product design decision artifacts for UI delivery.

from __future__ import annotations

__all__ = [
    "SCHEMA_VERSION",
    "VALID_TASTE_DISPOSITIONS",
    "build_design_decisions",
    "design_decisions_path",
    "load_design_decisions",
    "validate_design_decisions",
    "write_design_decisions",
]

from pathlib import Path
from typing import Any
import json


SCHEMA_VERSION = "signalos.design_decisions.v1"
VALID_TASTE_DISPOSITIONS = frozenset({"accepted", "rejected", "deferred"})

_ARTIFACT_FIELDS = (
    "variants",
    "selected_variant",
    "selection_reason",
    "taste_findings",
    "approved_by",
)
_VARIANT_FIELDS = (
    "id",
    "summary",
    "strengths",
    "weaknesses",
    "screenshot",
    "score",
)
_UI_PROFILES = {"react-vite"}
_SELF_APPROVERS = {
    "auto",
    "automated",
    "builder",
    "design builder",
    "recommendation",
    "signalos",
    "signalos builder",
    "system",
}


def build_design_decisions(
    intent: dict[str, Any],
    design_system: dict[str, Any] | None = None,
    *,
    wave: str = "product",
    selected_variant: str | None = None,
    selection_reason: str | None = None,
    taste_findings: list[dict[str, Any]] | None = None,
    approved_by: str = "",
) -> dict[str, Any]:
    """Build a deterministic design decision artifact.

    The builder recommends a selected variant from the generated candidates,
    but it never self-authorizes scope: ``approved_by`` defaults to empty and
    must be supplied by a caller after an external approval gate.
    """
    variants = _build_variants(intent, design_system, wave)
    selected = selected_variant or _top_scoring_variant_id(variants)
    reason = selection_reason or _default_selection_reason(selected, variants, intent)

    return {
        "schema_version": SCHEMA_VERSION,
        "wave": str(wave),
        "product_name": str(intent.get("product_name", "")),
        "variants": variants,
        "selected_variant": selected,
        "selection_reason": reason,
        "taste_findings": list(taste_findings or []),
        "approved_by": str(approved_by),
    }


def validate_design_decisions(
    decisions: dict[str, Any] | None,
    *,
    profile: str = "generic",
    intent: dict[str, Any] | None = None,
    design_system: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a design decisions artifact.

    UI products must have at least one variant, a selected variant, and the
    selected variant must reference one of the variant IDs. Taste findings must
    explicitly declare disposition as accepted, rejected, or deferred.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    is_ui = _is_ui_product(profile, intent, design_system)

    if decisions is None:
        if is_ui:
            blockers.append("UI product requires DESIGN_DECISIONS.yaml")
        return {
            "valid": not blockers,
            "ui_product": is_ui,
            "blockers": blockers,
            "warnings": warnings,
        }

    if not isinstance(decisions, dict):
        blockers.append("design decisions artifact must be a mapping")
        return {
            "valid": False,
            "ui_product": is_ui,
            "blockers": blockers,
            "warnings": warnings,
        }

    for field in _ARTIFACT_FIELDS:
        if field not in decisions:
            blockers.append(f"missing required field: {field}")

    variants_raw = decisions.get("variants", [])
    variant_ids: set[str] = set()
    if not isinstance(variants_raw, list):
        blockers.append("variants must be a list")
        variants: list[Any] = []
    else:
        variants = variants_raw

    if is_ui and not variants:
        blockers.append("UI product design decisions must include variants")

    for index, variant in enumerate(variants):
        prefix = f"variants[{index}]"
        if not isinstance(variant, dict):
            blockers.append(f"{prefix} must be a mapping")
            continue
        for field in _VARIANT_FIELDS:
            if field not in variant:
                blockers.append(f"{prefix} missing required field: {field}")

        variant_id = str(variant.get("id", "")).strip()
        if not variant_id:
            blockers.append(f"{prefix}.id must be non-empty")
        else:
            if variant_id in variant_ids:
                blockers.append(f"duplicate variant id: {variant_id}")
            variant_ids.add(variant_id)

        if not isinstance(variant.get("strengths", []), list):
            blockers.append(f"{prefix}.strengths must be a list")
        if not isinstance(variant.get("weaknesses", []), list):
            blockers.append(f"{prefix}.weaknesses must be a list")

        score = variant.get("score")
        if score is not None and not isinstance(score, (int, float)):
            blockers.append(f"{prefix}.score must be numeric")

    selected = str(decisions.get("selected_variant") or "").strip()
    if is_ui and not selected:
        blockers.append("UI product design decisions must include selected_variant")
    if selected and selected not in variant_ids:
        blockers.append(f"selected_variant not found in variants: {selected}")

    taste_findings = decisions.get("taste_findings", [])
    if not isinstance(taste_findings, list):
        blockers.append("taste_findings must be a list")
    else:
        for index, finding in enumerate(taste_findings):
            prefix = f"taste_findings[{index}]"
            if not isinstance(finding, dict):
                blockers.append(f"{prefix} must be a mapping")
                continue
            disposition = str(finding.get("disposition", "")).strip()
            if disposition not in VALID_TASTE_DISPOSITIONS:
                allowed = ", ".join(sorted(VALID_TASTE_DISPOSITIONS))
                blockers.append(f"{prefix}.disposition must be one of: {allowed}")

    approved_by = str(decisions.get("approved_by") or "").strip()
    if not approved_by:
        warnings.append(
            "approved_by is empty; recommendation is not delivery authorization"
        )
    elif approved_by.lower() in _SELF_APPROVERS:
        blockers.append(
            "approved_by must identify an external approver; recommendations cannot self-authorize scope"
        )

    return {
        "valid": not blockers,
        "ui_product": is_ui,
        "blockers": blockers,
        "warnings": warnings,
    }


def design_decisions_path(signalos_dir: Path, wave: str) -> Path:
    """Return the canonical path for a wave design decisions artifact."""
    return signalos_dir / "designs" / str(wave) / "DESIGN_DECISIONS.yaml"


def write_design_decisions(
    decisions: dict[str, Any],
    signalos_dir: Path,
    wave: str | None = None,
) -> Path:
    """Write ``.signalos/designs/<wave>/DESIGN_DECISIONS.yaml``."""
    artifact_wave = str(wave or decisions.get("wave") or "").strip()
    if not artifact_wave:
        raise ValueError("wave is required to write DESIGN_DECISIONS.yaml")

    path = design_decisions_path(signalos_dir, artifact_wave)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(decisions, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_design_decisions(signalos_dir: Path, wave: str) -> dict[str, Any] | None:
    """Load ``.signalos/designs/<wave>/DESIGN_DECISIONS.yaml`` if present."""
    path = design_decisions_path(signalos_dir, wave)
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


def _build_variants(
    intent: dict[str, Any],
    design_system: dict[str, Any] | None,
    wave: str,
) -> list[dict[str, Any]]:
    surfaces = {str(s).lower() for s in intent.get("ux_surfaces", [])}
    product_type = str(intent.get("product_type", "custom")).lower()
    entities = [str(e) for e in intent.get("entities", []) if str(e)]
    workflows = [str(w) for w in intent.get("primary_workflows", []) if str(w)]
    ui_name = _ui_library_name(design_system)

    if product_type == "financial-dashboard" or surfaces.intersection({"chart", "dashboard", "gauge"}):
        candidates = _dashboard_variants(entities, workflows, ui_name)
    elif surfaces.intersection({"form", "table", "calendar"}) or len(entities) >= 4:
        candidates = _entity_variants(entities, workflows, ui_name, surfaces)
    elif surfaces.intersection({"kanban", "list"}):
        candidates = _workflow_variants(entities, workflows, ui_name)
    else:
        candidates = _general_variants(entities, workflows, ui_name)

    return [
        {
            "id": candidate["id"],
            "summary": candidate["summary"],
            "strengths": candidate["strengths"],
            "weaknesses": candidate["weaknesses"],
            "screenshot": f".signalos/designs/{wave}/screenshots/{candidate['id']}.png",
            "score": candidate["score"],
        }
        for candidate in candidates[: max(3, len(candidates))]
    ]


def _dashboard_variants(
    entities: list[str],
    workflows: list[str],
    ui_name: str,
) -> list[dict[str, Any]]:
    subject = _subject(entities, workflows, "business metrics")
    return [
        _variant(
            "variant-01-metric-command",
            f"Metric-first command center for monitoring {subject}.",
            [
                "Makes status and trends visible above the fold",
                f"Fits {ui_name or 'the selected UI system'} dashboard primitives",
                "Supports fast scan and exception triage",
            ],
            [
                "Can feel dense if the product has few metrics",
                "Requires careful empty and loading states for charts",
            ],
            8.7,
        ),
        _variant(
            "variant-02-analyst-workbench",
            f"Exploratory workspace with filters, tables, and chart detail for {subject}.",
            [
                "Good for users comparing segments or periods",
                "Keeps drill-down context close to charts",
                "Scales to richer data workflows",
            ],
            [
                "Higher implementation cost than a summary dashboard",
                "Less suited to casual first-time users",
            ],
            8.2,
        ),
        _variant(
            "variant-03-executive-summary",
            f"Concise summary view focused on decisions and anomalies in {subject}.",
            [
                "Strong narrative hierarchy for stakeholders",
                "Reduces cognitive load on mobile",
                "Easy to pair with scheduled reports",
            ],
            [
                "Less efficient for detailed operational work",
                "May hide data relationships unless drilldowns are strong",
            ],
            7.8,
        ),
    ]


def _entity_variants(
    entities: list[str],
    workflows: list[str],
    ui_name: str,
    surfaces: set[str],
) -> list[dict[str, Any]]:
    subject = _subject(entities, workflows, "records")
    calendar_note = " with calendar context" if "calendar" in surfaces else ""
    return [
        _variant(
            "variant-01-record-workbench",
            f"Table and detail workbench for managing {subject}{calendar_note}.",
            [
                "Efficient for repeated review and edit workflows",
                f"Leverages {ui_name or 'rich UI'} controls for forms and tables",
                "Keeps list context while inspecting one record",
            ],
            [
                "Can overwhelm low-volume products",
                "Needs strong responsive behavior for narrow screens",
            ],
            8.6,
        ),
        _variant(
            "variant-02-guided-flow",
            f"Step-by-step guided flow for creating and updating {subject}.",
            [
                "Reduces mistakes in complex forms",
                "Creates clear validation checkpoints",
                "Works well for infrequent or high-stakes tasks",
            ],
            [
                "Slower for expert users",
                "Can add unnecessary ceremony to simple edits",
            ],
            8.1,
        ),
        _variant(
            "variant-03-review-queue",
            f"Queue-based review surface for pending work around {subject}.",
            [
                "Highlights the next action instead of the whole database",
                "Good fit for approval or triage workflows",
                "Supports deferred decisions cleanly",
            ],
            [
                "Requires clear queue rules",
                "Less discoverable for broad browsing",
            ],
            7.9,
        ),
    ]


def _workflow_variants(
    entities: list[str],
    workflows: list[str],
    ui_name: str,
) -> list[dict[str, Any]]:
    subject = _subject(entities, workflows, "work items")
    return [
        _variant(
            "variant-01-board-first",
            f"Board-first workflow view for moving {subject} through statuses.",
            [
                "Makes state changes direct and visible",
                "Supports lightweight team planning",
                f"Pairs well with {ui_name or 'component'} cards and menus",
            ],
            [
                "Poor fit for very large datasets",
                "Needs keyboard and screen-reader alternatives",
            ],
            8.5,
        ),
        _variant(
            "variant-02-list-detail",
            f"List-detail layout for fast scanning and editing of {subject}.",
            [
                "Predictable for CRUD-heavy work",
                "Better density than a board on desktop",
                "Straightforward responsive fallback",
            ],
            [
                "Less visually expressive than board-first",
                "Status progression is less prominent",
            ],
            8.0,
        ),
        _variant(
            "variant-03-planning-timeline",
            f"Timeline planning view that emphasizes sequence and deadlines for {subject}.",
            [
                "Strong for schedule-driven workflows",
                "Surfaces bottlenecks and sequencing",
                "Useful for roadmap or calendar adjacencies",
            ],
            [
                "Overbuilt if deadlines are not central",
                "More complex to implement accessibly",
            ],
            7.6,
        ),
    ]


def _general_variants(
    entities: list[str],
    workflows: list[str],
    ui_name: str,
) -> list[dict[str, Any]]:
    subject = _subject(entities, workflows, "the primary workflow")
    return [
        _variant(
            "variant-01-workflow-dashboard",
            f"Overview dashboard that anchors navigation around {subject}.",
            [
                "Gives users a clear home base",
                "Leaves room for future product surfaces",
                f"Works with {ui_name or 'the selected UI library'} without custom primitives",
            ],
            [
                "May be generic without strong domain content",
                "Dashboard metrics must be meaningful to avoid filler",
            ],
            8.3,
        ),
        _variant(
            "variant-02-focused-task",
            f"Focused single-task surface optimized for completing {subject}.",
            [
                "Low cognitive load",
                "Fast to implement and validate",
                "Clear mobile behavior",
            ],
            [
                "Limited support for multi-step operations",
                "Can under-serve administrative users",
            ],
            7.9,
        ),
        _variant(
            "variant-03-command-console",
            f"Command console for searching, filtering, and acting on {subject}.",
            [
                "Efficient for repeat users",
                "Centralizes common actions",
                "Scales well as the product grows",
            ],
            [
                "Requires thoughtful empty states and shortcuts",
                "Less obvious for first-time users",
            ],
            7.7,
        ),
    ]


def _variant(
    variant_id: str,
    summary: str,
    strengths: list[str],
    weaknesses: list[str],
    score: float,
) -> dict[str, Any]:
    return {
        "id": variant_id,
        "summary": summary,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "score": score,
    }


def _top_scoring_variant_id(variants: list[dict[str, Any]]) -> str:
    if not variants:
        return ""
    top = max(variants, key=lambda v: float(v.get("score", 0.0)))
    return str(top.get("id", ""))


def _default_selection_reason(
    selected_variant: str,
    variants: list[dict[str, Any]],
    intent: dict[str, Any],
) -> str:
    selected = next((v for v in variants if v.get("id") == selected_variant), None)
    if selected is None:
        return (
            "Selected variant must be reviewed by an external approver before "
            "it can authorize delivery scope."
        )

    product_name = str(intent.get("product_name", "the product") or "the product")
    return (
        f"Recommended for {product_name} because it has the strongest deterministic "
        f"fit score ({selected.get('score')}). This recommendation does not "
        "authorize scope without explicit approval."
    )


def _is_ui_product(
    profile: str,
    intent: dict[str, Any] | None,
    design_system: dict[str, Any] | None,
) -> bool:
    if profile in _UI_PROFILES:
        return True
    if intent:
        if intent.get("ux_surfaces"):
            return True
        product_type = str(intent.get("product_type", "")).lower()
        if product_type in {"financial-dashboard", "task-management"}:
            return True
    return bool(_ui_library_name(design_system))


def _ui_library_name(design_system: dict[str, Any] | None) -> str:
    if not isinstance(design_system, dict):
        return ""
    ui_library = design_system.get("ui_library", {})
    if isinstance(ui_library, dict):
        return str(ui_library.get("name", "") or "")
    return ""


def _subject(
    entities: list[str],
    workflows: list[str],
    fallback: str,
) -> str:
    if entities:
        return ", ".join(entities[:3])
    if workflows:
        return workflows[0]
    return fallback


def _load_yaml_if_available(text: str) -> Any:
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return None

    try:
        return yaml.safe_load(text)
    except Exception:
        return None
