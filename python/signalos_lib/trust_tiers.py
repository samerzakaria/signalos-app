"""App-native Trust Tier surface registry and validator.

SignalOS.NET records each surface's tier and refuses illegal movement, most
importantly demotion of permanently-T3 surfaces. This module keeps the same
behavior in portable JSON files for the app runtime.
"""

from __future__ import annotations

__all__ = [
    "TIERS",
    "SCHEMA_VERSION",
    "VALIDATION_SCHEMA_VERSION",
    "TrustTierError",
    "surface_lookup_cache_key",
    "register_trust_surface",
    "get_trust_surface_by_surface",
    "load_trust_surface",
    "list_trust_surfaces",
    "promote_trust_surface",
    "demote_trust_surface",
    "validate_trust_tier",
]

import fnmatch
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TIERS = ("T1", "T2", "T3")
SCHEMA_VERSION = "signalos.trust_tier_surface.v1"
VALIDATION_SCHEMA_VERSION = "signalos.validate_trust_tier.v1"

_TIER_ORDER = {"T1": 1, "T2": 2, "T3": 3}
_SURFACES_REL = Path(".signalos") / "trust-tiers" / "surfaces"
_EVIDENCE_REL = Path(".signalos") / "evidence" / "trust-tiers" / "validate-trust-tier.json"


class TrustTierError(RuntimeError):
    """Raised when a trust-tier operation violates the lifecycle."""


def register_trust_surface(
    repo_root: Path | str,
    *,
    surface_id: str,
    tier: str,
    justification: str,
    is_permanently_t3: bool = False,
    tenant_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create or replace a trust-tier surface declaration."""
    root = Path(repo_root)
    normalized_surface = _normalize_surface_id(surface_id)
    normalized_tier = _normalize_tier(tier)
    normalized_tenant = _normalize_tenant_id(tenant_id)
    normalized_justification = _require_text(justification, "justification", max_length=1000)
    if is_permanently_t3 and normalized_tier != "T3":
        raise TrustTierError("permanently-T3 surface must start at Tier=T3")
    path = _surface_path(root, normalized_surface, normalized_tenant)
    if path.exists() and not force:
        raise FileExistsError(
            f"trust-tier surface already exists for tenant {_tenant_token(normalized_tenant)}: {normalized_surface}"
        )
    now = _now_iso()
    surface = {
        "schema_version": SCHEMA_VERSION,
        "id": _surface_uid(normalized_tenant, normalized_surface),
        "tenant_id": normalized_tenant,
        "surface_id": normalized_surface,
        "tier": normalized_tier,
        "justification": normalized_justification,
        "is_permanently_t3": bool(is_permanently_t3),
        "lookup_cache_key": surface_lookup_cache_key(normalized_surface, tenant_id=normalized_tenant),
        "created_at": now,
        "updated_at": now,
        "history": [
            {
                "action": "register",
                "from": None,
                "to": normalized_tier,
                "justification": normalized_justification,
                "at": now,
            }
        ],
    }
    _write_surface(root, surface)
    return surface


def get_trust_surface_by_surface(
    repo_root: Path | str,
    surface_id: str,
    *,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """Return a surface declaration by surface id, or None when not declared."""
    try:
        return load_trust_surface(repo_root, surface_id, tenant_id=tenant_id)
    except FileNotFoundError:
        return None


def load_trust_surface(
    repo_root: Path | str,
    surface_id: str,
    *,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Load a trust-tier surface by id."""
    root = Path(repo_root)
    normalized_surface = _normalize_surface_id(surface_id)
    normalized_tenant = _normalize_tenant_id(tenant_id)
    path = _surface_path(root, normalized_surface, normalized_tenant)
    if not path.is_file():
        raise FileNotFoundError(
            f"trust-tier surface not found for tenant {_tenant_token(normalized_tenant)}: {normalized_surface}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TrustTierError("trust-tier surface file must contain a JSON object")
    _validate_surface(data)
    return data


def list_trust_surfaces(
    repo_root: Path | str,
    *,
    tier: str | None = None,
    tenant_id: str | None = None,
    all_tenants: bool = False,
) -> list[dict[str, Any]]:
    """List persisted trust-tier surfaces."""
    root = Path(repo_root)
    wanted_tier = _normalize_tier(tier) if tier else None
    wanted_tenant = _normalize_tenant_id(tenant_id)
    directory = root / _SURFACES_REL
    if not directory.is_dir():
        return []
    surfaces: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            _validate_surface(data)
        except TrustTierError:
            continue
        if not all_tenants and _normalize_tenant_id(data.get("tenant_id")) != wanted_tenant:
            continue
        if wanted_tier and data["tier"] != wanted_tier:
            continue
        surfaces.append(data)
    return surfaces


def promote_trust_surface(
    repo_root: Path | str,
    surface_id: str,
    *,
    target_tier: str,
    justification: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Promote a surface upward, e.g. T1 -> T2 or T2 -> T3."""
    surface = load_trust_surface(repo_root, surface_id, tenant_id=tenant_id)
    target = _normalize_tier(target_tier)
    current = surface["tier"]
    if _TIER_ORDER[target] <= _TIER_ORDER[current]:
        raise TrustTierError("promote must move upward")
    return _transition_surface(repo_root, surface, action="promote", target=target, justification=justification)


def demote_trust_surface(
    repo_root: Path | str,
    surface_id: str,
    *,
    target_tier: str,
    justification: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Demote a surface downward unless it is permanently T3."""
    surface = load_trust_surface(repo_root, surface_id, tenant_id=tenant_id)
    if bool(surface.get("is_permanently_t3")):
        raise TrustTierError(f"cannot demote permanently-T3 surface {surface['surface_id']}")
    target = _normalize_tier(target_tier)
    current = surface["tier"]
    if _TIER_ORDER[target] >= _TIER_ORDER[current]:
        raise TrustTierError("demote must move downward")
    return _transition_surface(repo_root, surface, action="demote", target=target, justification=justification)


def validate_trust_tier(
    repo_root: Path | str,
    *,
    declared_tier: str,
    touched_paths: list[str],
    tenant_id: str | None = None,
    allow_unclassified: bool = False,
    write_evidence: bool = True,
) -> dict[str, Any]:
    """Validate touched paths against registered surface tiers."""
    root = Path(repo_root)
    declared = _normalize_tier(declared_tier)
    normalized_tenant = _normalize_tenant_id(tenant_id)
    surfaces = list_trust_surfaces(root, tenant_id=normalized_tenant)
    blockers: list[dict[str, Any]] = []
    touched: list[dict[str, Any]] = []

    for raw_path in touched_paths:
        rel = _normalize_surface_id(raw_path)
        matches = _matching_surfaces(rel, surfaces)
        if not matches:
            touched.append({"path": rel, "required_tier": None, "matched_surfaces": []})
            if not allow_unclassified:
                blockers.append(
                    {
                        "kind": "surface-unclassified",
                        "path": rel,
                        "message": (
                            f"{rel} has no registered trust-tier surface "
                            f"for tenant {_tenant_token(normalized_tenant)}"
                        ),
                        "fix_command": (
                            "signalos trust-tier surface register"
                            f"{_tenant_cli_arg(normalized_tenant)} --surface-id <path>"
                            " --tier T2 --justification <reason>"
                        ),
                    }
                )
            continue
        required = max(matches, key=lambda item: _TIER_ORDER[item["tier"]])
        required_tier = required["tier"]
        touched.append(
            {
                "path": rel,
                "required_tier": required_tier,
                "matched_surfaces": [
                    {
                        "surface_id": item["surface_id"],
                        "tier": item["tier"],
                        "is_permanently_t3": item["is_permanently_t3"],
                    }
                    for item in matches
                ],
            }
        )
        if _TIER_ORDER[declared] < _TIER_ORDER[required_tier]:
            blockers.append(
                {
                    "kind": "declared-tier-too-low",
                    "path": rel,
                    "message": f"{rel} requires {required_tier} but declared tier is {declared}",
                    "fix_command": f"redeclare the session as {required_tier} or avoid the surface",
                }
            )

    payload: dict[str, Any] = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "repo_root": str(root.resolve()),
        "tenant_id": normalized_tenant,
        "declared_tier": declared,
        "ok": not blockers,
        "status": "PASS" if not blockers else "FAIL",
        "allow_unclassified": bool(allow_unclassified),
        "surface_count": len(surfaces),
        "touched": touched,
        "blockers": blockers,
        "generated_at": _now_iso(),
    }
    if write_evidence:
        payload["evidence_path"] = _write_validation(root, payload)
    else:
        payload["evidence_path"] = None
    return payload


def surface_lookup_cache_key(surface_id: str, *, tenant_id: str | None = None) -> str:
    """Return the technology-neutral lookup key used for GetBySurface parity."""
    normalized_surface = _normalize_surface_id(surface_id)
    normalized_tenant = _normalize_tenant_id(tenant_id)
    return f"trusttiers:surface:{_tenant_token(normalized_tenant)}:{normalized_surface}"


def _transition_surface(
    repo_root: Path | str,
    surface: dict[str, Any],
    *,
    action: str,
    target: str,
    justification: str,
) -> dict[str, Any]:
    reason = _require_text(justification, "justification", max_length=1000)
    now = _now_iso()
    before = surface["tier"]
    surface["tier"] = target
    surface["justification"] = reason
    surface["updated_at"] = now
    history = surface.setdefault("history", [])
    if not isinstance(history, list):
        history = []
        surface["history"] = history
    history.append(
        {
            "action": action,
            "from": before,
            "to": target,
            "justification": reason,
            "at": now,
        }
    )
    _write_surface(Path(repo_root), surface)
    return surface


def _matching_surfaces(path: str, surfaces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for surface in surfaces:
        surface_id = surface["surface_id"]
        if _surface_matches(path, surface_id):
            matches.append(surface)
    return matches


def _surface_matches(path: str, surface_id: str) -> bool:
    if any(ch in surface_id for ch in "*?[]"):
        return fnmatch.fnmatch(path, surface_id)
    normalized_surface = surface_id.rstrip("/")
    return path == normalized_surface or path.startswith(f"{normalized_surface}/")


def _validate_surface(surface: dict[str, Any]) -> None:
    if surface.get("schema_version") != SCHEMA_VERSION:
        raise TrustTierError("unsupported trust-tier surface schema")
    surface_id = _normalize_surface_id(str(surface.get("surface_id", "")))
    tenant_id = _normalize_tenant_id(surface.get("tenant_id"))
    tier = _normalize_tier(str(surface.get("tier", "")))
    _require_text(str(surface.get("justification", "")), "justification", max_length=1000)
    if bool(surface.get("is_permanently_t3")) and tier != "T3":
        raise TrustTierError("permanently-T3 surface must be Tier=T3")
    declared_id = surface.get("id")
    if declared_id is not None and declared_id != _surface_uid(tenant_id, surface_id):
        raise TrustTierError("trust-tier surface id does not match tenant/surface")
    declared_cache_key = surface.get("lookup_cache_key")
    if declared_cache_key is not None and declared_cache_key != surface_lookup_cache_key(
        surface_id,
        tenant_id=tenant_id,
    ):
        raise TrustTierError("trust-tier lookup cache key does not match tenant/surface")


def _surface_path(root: Path, surface_id: str, tenant_id: str | None) -> Path:
    digest = hashlib.sha256(f"{_tenant_token(tenant_id)}\0{surface_id}".encode("utf-8")).hexdigest()[:24]
    return root / _SURFACES_REL / f"{digest}.json"


def _write_surface(root: Path, surface: dict[str, Any]) -> None:
    path = _surface_path(root, surface["surface_id"], _normalize_tenant_id(surface.get("tenant_id")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(surface, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_validation(root: Path, payload: dict[str, Any]) -> str:
    path = root / _EVIDENCE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)


def _normalize_tier(tier: str | None) -> str:
    normalized = str(tier or "").strip().upper()
    if normalized not in _TIER_ORDER:
        raise TrustTierError("tier must be T1, T2, or T3")
    return normalized


def _normalize_surface_id(surface_id: str) -> str:
    text = str(surface_id or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text:
        raise TrustTierError("surface_id is required")
    if len(text) > 400:
        raise TrustTierError("surface_id must be 400 characters or fewer")
    if text.startswith("/") or ".." in Path(text).parts:
        raise TrustTierError("surface_id must be a safe relative path or surface identifier")
    return text


def _normalize_tenant_id(tenant_id: Any) -> str | None:
    if tenant_id is None:
        return None
    text = str(tenant_id).strip()
    if not text:
        return None
    if len(text) > 200:
        raise TrustTierError("tenant_id must be 200 characters or fewer")
    if any(ord(ch) < 32 for ch in text):
        raise TrustTierError("tenant_id must not contain control characters")
    return text


def _tenant_token(tenant_id: str | None) -> str:
    # Single source for both the lookup-key segment and the human-facing
    # label: the host tenant has no id, so it resolves to the literal "host".
    return tenant_id if tenant_id is not None else "host"


def _tenant_cli_arg(tenant_id: str | None) -> str:
    return f" --tenant-id {tenant_id}" if tenant_id is not None else ""


def _surface_uid(tenant_id: str | None, surface_id: str) -> str:
    digest = hashlib.sha256(f"{_tenant_token(tenant_id)}\0{surface_id}".encode("utf-8")).hexdigest()
    return f"stt_{digest[:32]}"


def _require_text(value: str, field: str, *, max_length: int | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        raise TrustTierError(f"{field} is required")
    if max_length is not None and len(text) > max_length:
        raise TrustTierError(f"{field} must be {max_length} characters or fewer")
    return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
