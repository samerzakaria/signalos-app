"""Shared SignalOS gate artifact definitions and path resolvers.

This module is the Python source of truth for gate artifact paths.  Existing
callers can keep using ``signalos_lib.sign.GATE_MAP``; that value is now derived
from the definitions here instead of being maintained separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

__all__ = [
    "GATE_ARTIFACTS",
    "GATE_LABELS",
    "GATE_MAP",
    "GateArtifact",
    "ResolvedGateArtifact",
    "expected_gate_artifacts",
    "get_gate_label",
    "gate_artifact_map",
    "list_gates",
    "resolve_gate_artifacts",
    "resolve_workspace_path",
]


@dataclass(frozen=True)
class GateArtifact:
    """One required artifact for a governance gate."""

    gate: str
    rel_path: str
    required_roles: tuple[str, ...]
    label: str


@dataclass(frozen=True)
class ResolvedGateArtifact:
    """A gate artifact resolved under a specific workspace root."""

    gate: str
    rel_path: str
    required_roles: tuple[str, ...]
    label: str
    path: Path


GATE_LABELS: dict[str, str] = {
    "G0": "Gate 0",
    "G1": "Gate 1",
    "G2": "Gate 2",
    "G3": "Gate 3",
    "G4": "Gate 4",
    "G5": "Gate 5",
}


GATE_ARTIFACTS: dict[str, tuple[GateArtifact, ...]] = {
    "G0": (
        GateArtifact("G0", "core/governance/Governance/SOUL-DOCUMENT.md", ("PO", "PE"), "Soul Document"),
        GateArtifact("G0", "core/governance/Governance/CONSTITUTION.md", ("PO", "PE"), "Constitution"),
        GateArtifact("G0", "core/governance/Governance/SURFACE_INVENTORY.md", ("PE",), "Surface Inventory"),
        GateArtifact("G0", "core/governance/Governance/PERMANENTLY_T3.md", ("PE",), "Permanently T3"),
    ),
    "G1": (
        GateArtifact("G1", "core/strategy/BELIEF.md", ("PO",), "Belief"),
        GateArtifact("G1", "core/execution/ROLE_ACTIVATION_CARD.md", ("PO",), "Role Activation Card"),
    ),
    "G2": (
        GateArtifact("G2", "core/strategy/EXPECTATION_MAP.md", ("PO",), "Expectation Map"),
    ),
    "G3": (
        GateArtifact("G3", "core/strategy/DESIGN_NOTE.md", ("PO",), "Design Note"),
        GateArtifact("G3", "core/execution/PLAN.md", ("PE",), "Plan"),
        GateArtifact("G3", "core/execution/ACCEPTANCE_CRITERIA.md", ("PE",), "Acceptance Criteria"),
    ),
    "G4": (
        GateArtifact("G4", "core/execution/TRUST_TIER.md", ("PE", "PO"), "Trust Tier"),
    ),
    "G5": (
        GateArtifact("G5", "core/governance/QUALITY_CHECK.md", ("QA",), "Quality Check"),
    ),
}


def gate_artifact_map() -> dict[str, list[tuple[str, list[str], str]]]:
    """Return a compatibility copy shaped like the historical ``GATE_MAP``."""

    return {
        gate: [
            (artifact.rel_path, list(artifact.required_roles), artifact.label)
            for artifact in artifacts
        ]
        for gate, artifacts in GATE_ARTIFACTS.items()
    }


GATE_MAP: dict[str, list[tuple[str, list[str], str]]] = gate_artifact_map()


def list_gates() -> list[str]:
    """Return known gate IDs in canonical order."""

    return list(GATE_ARTIFACTS.keys())


def get_gate_label(gate: str) -> str:
    """Return the display label for *gate*, falling back to the normalized ID."""

    normalized = gate.upper()
    return GATE_LABELS.get(normalized, normalized)


def expected_gate_artifacts(gate: str | None = None) -> list[GateArtifact]:
    """Return expected artifact specs for one gate or all gates."""

    if gate is None:
        return [artifact for artifacts in GATE_ARTIFACTS.values() for artifact in artifacts]
    return list(GATE_ARTIFACTS.get(gate.upper(), ()))


def resolve_workspace_path(repo_root: Path, rel_path: str) -> Path:
    """Resolve *rel_path* under *repo_root* and reject path escape attempts."""

    relative = _relative_path(rel_path)
    root = Path(repo_root).expanduser().resolve(strict=False)
    candidate = (root / relative).resolve(strict=False)
    if not _is_relative_to(candidate, root):
        raise ValueError(f"path escapes workspace root: {rel_path!r}")
    return candidate


def resolve_gate_artifacts(repo_root: Path, gate: str | None = None) -> list[ResolvedGateArtifact]:
    """Resolve expected gate artifacts under *repo_root* with escape checks."""

    resolved: list[ResolvedGateArtifact] = []
    for artifact in expected_gate_artifacts(gate):
        resolved.append(
            ResolvedGateArtifact(
                gate=artifact.gate,
                rel_path=artifact.rel_path,
                required_roles=artifact.required_roles,
                label=artifact.label,
                path=resolve_workspace_path(repo_root, artifact.rel_path),
            )
        )
    return resolved


def _relative_path(rel_path: str) -> Path:
    text = str(rel_path).strip()
    if not text:
        raise ValueError("artifact path must not be empty")
    if "\x00" in text:
        raise ValueError("artifact path must not contain NUL bytes")
    if "\\" in text:
        raise ValueError(f"artifact path must use POSIX separators: {rel_path!r}")
    if len(text) >= 2 and text[1] == ":":
        raise ValueError(f"artifact path must be relative: {rel_path!r}")

    path = PurePosixPath(text)
    if path.is_absolute():
        raise ValueError(f"artifact path must be relative: {rel_path!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"artifact path contains unsafe segment: {rel_path!r}")
    return Path(*path.parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
