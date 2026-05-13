# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/sign.py
# W3.1 — Guided gate signing CLI (AMD-CORE-014)
# Provides: GATE_MAP, ArtifactStatus, check_gate, sign_artifact, sign_gate

from __future__ import annotations

__all__ = [
    "GATE_MAP",
    "GATE_LABELS",
    "VALID_ROLES",
    "VALID_VERDICTS",
    "ArtifactStatus",
    "check_gate",
    "sign_artifact",
    "sign_gate",
    "_compute_hash",
    "_append_audit",
    "_parse_signers",
]

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Gate -> artifact map
# ---------------------------------------------------------------------------

GATE_MAP: dict[str, list[tuple[str, list[str], str]]] = {
    "G0": [
        ("core/governance/Governance/SOUL-DOCUMENT.md",   ["PO", "PE"], "Soul Document"),
        ("core/governance/Governance/CONSTITUTION.md",    ["PO", "PE"], "Constitution"),
        ("core/governance/Governance/SURFACE_INVENTORY.md", ["PE"],     "Surface Inventory"),
        ("core/governance/Governance/PERMANENTLY_T3.md",  ["PE"],       "Permanently T3"),
    ],
    "G1": [
        ("core/strategy/BELIEF.md",              ["PO"], "Belief"),
        ("core/execution/ROLE_ACTIVATION_CARD.md", ["PO"], "Role Activation Card"),
    ],
    "G2": [
        ("core/strategy/EXPECTATION_MAP.md", ["PO"], "Expectation Map"),
    ],
    "G3": [
        ("core/strategy/DESIGN_NOTE.md",          ["PO"], "Design Note"),
        ("core/execution/PLAN.md",                ["PE"], "Plan"),
        ("core/execution/ACCEPTANCE_CRITERIA.md", ["PE"], "Acceptance Criteria"),
    ],
    "G4": [
        ("core/execution/TRUST_TIER.md", ["PE", "PO"], "Trust Tier"),
    ],
    "G5": [
        ("core/governance/QUALITY_CHECK.md", ["QA"], "Quality Check"),
    ],
}

GATE_LABELS: dict[str, str] = {
    "G0": "Gate 0",
    "G1": "Gate 1",
    "G2": "Gate 2",
    "G3": "Gate 3",
    "G4": "Gate 4",
    "G5": "Gate 5",
}

VALID_ROLES = ("PO", "PE", "QA", "DevOps")
VALID_VERDICTS = ("APPROVED", "APPROVED-WITH-CONDITIONS", "WAIVED")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ArtifactStatus:
    """Snapshot of one gate artifact's signature state."""
    path: Path
    rel_path: str
    label: str
    required_roles: list[str]
    exists: bool
    has_signatures: bool = False
    is_draft: bool = False
    hash_valid: bool | None = None
    signers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _compute_hash(path: Path) -> str:
    """Return sha256 hex of artifact content above the ## Signatures heading."""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^## Signatures", text, re.MULTILINE)
    content = text[: m.start()] if m else text
    return hashlib.sha256(content.rstrip().encode("utf-8")).hexdigest()


def _parse_signers(path: Path) -> tuple[list[str], bool, bool | None]:
    """
    Parse the ## Signatures block of *path*.

    Returns:
        signers   -- list of non-DRAFT signer names
        is_draft  -- True if any DRAFT token found in the block
        hash_valid -- None if no artifact_hash; True/False if declared
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^## Signatures", text, re.MULTILINE)
    if not m:
        return [], False, None

    sig_block = text[m.start():]
    is_draft = bool(re.search(r"DRAFT", sig_block, re.IGNORECASE))

    signers: list[str] = []
    for sm in re.finditer(r"signer:\s*(.+)", sig_block):
        name = sm.group(1).strip()
        if name and "DRAFT" not in name.upper():
            signers.append(name)

    hash_match = re.search(r"artifact_hash:\s*([a-f0-9]{64})", sig_block)
    if hash_match:
        declared = hash_match.group(1)
        computed = _compute_hash(path)
        hash_valid: bool | None = declared == computed
    else:
        hash_valid = None

    return signers, is_draft, hash_valid


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_gate(root: Path, gate: str) -> list[ArtifactStatus]:
    """Return signature status of every artifact required for *gate*."""
    entries = GATE_MAP.get(gate.upper(), [])
    result: list[ArtifactStatus] = []
    for rel, roles, label in entries:
        p = root / rel
        status = ArtifactStatus(
            path=p,
            rel_path=rel,
            label=label,
            required_roles=list(roles),
            exists=p.exists(),
        )
        if status.exists:
            signers, is_draft, hash_valid = _parse_signers(p)
            status.signers = signers
            status.has_signatures = len(signers) > 0
            status.is_draft = is_draft
            status.hash_valid = hash_valid
        result.append(status)
    return result


def sign_artifact(
    path: Path,
    signer: str,
    role: str,
    gate: str,
    verdict: str,
    conditions: str = "",
    oidc_sub_hash: str = "",
    oidc_issuer: str = "",
) -> None:
    """
    Append a YAML signature entry to *path*.

    If the artifact already has a ## Signatures / ```yaml block, the new
    entry is inserted inside the existing YAML list (co-sign).  Otherwise
    a complete ## Signatures section is appended.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"verdict must be one of {VALID_VERDICTS}, got {verdict!r}")

    artifact_hash = _compute_hash(path)
    today = date.today().isoformat()
    gate_label = GATE_LABELS.get(gate.upper(), gate)

    cond_line = f'\n  conditions: "{conditions}"' if (verdict == "APPROVED-WITH-CONDITIONS" and conditions) else ""
    oidc_lines = (
        f"\n  oidc_sub_hash: {oidc_sub_hash}\n  oidc_issuer: {oidc_issuer}"
        if oidc_sub_hash
        else ""
    )

    new_entry = (
        f"- signer: {signer}\n"
        f"  role: {role}\n"
        f"  date: {today}\n"
        f"  gate: {gate_label}\n"
        f"  artifact_hash: {artifact_hash}\n"
        f"  verdict: {verdict}"
        f"{cond_line}"
        f"{oidc_lines}\n"
    )

    text = path.read_text(encoding="utf-8", errors="replace")

    sig_m = re.search(r"^## Signatures", text, re.MULTILINE)
    if sig_m:
        yaml_start = text.find("```yaml", sig_m.start())
        if yaml_start != -1:
            yaml_end = text.find("```", yaml_start + 7)
            if yaml_end != -1:
                updated = text[:yaml_end] + new_entry + text[yaml_end:]
                path.write_text(updated, encoding="utf-8")
                _call_brain_ingest(path, gate)
                return

    full_block = (
        "\n\n## Signatures\n\n"
        "```yaml\n"
        + new_entry
        + "```\n"
    )
    path.write_text(text.rstrip() + full_block, encoding="utf-8")
    _call_brain_ingest(path, gate)


def _call_brain_ingest(path: Path, gate: str) -> None:
    """Fire-and-forget: call brain-auto-ingest.sh after a signature is written.

    Walks up from the artifact path looking for a `.git` or `cli/` marker
    (max 10 hops) to locate repo root, then invokes the brain-auto-ingest
    hook if present. Swallows every error so the signing flow never
    blocks on the brain.
    """
    try:
        # Find repo root by walking up from artifact path
        root = path.resolve().parent
        for _ in range(10):
            if (root / ".git").exists() or (root / "cli").is_dir():
                break
            root = root.parent
        hook = root / "core" / "execution" / "hooks" / "_lib" / "brain-auto-ingest.sh"
        if not hook.exists():
            return
        subprocess.run(
            ["bash", str(hook), "--source", str(path), "--gate", gate, "--repo-root", str(root)],
            check=False, capture_output=True, timeout=10,
        )
    except Exception:
        pass  # never block the signing flow


def sign_gate(
    root: Path,
    gate: str,
    signer: str,
    role: str,
    verdict: str,
    conditions: str = "",
    audit_log: Path | None = None,
) -> list[str]:
    """
    Sign every present artifact in *gate*.  Returns rel-paths of signed files.
    Missing artifacts are skipped.

    Raises ValueError if role is not authorised for any artifact in gate.
    This enforces segregation of duties: PO cannot sign G5 (requires QA),
    PE cannot sign G1 (requires PO), etc.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")

    gate_entries = GATE_MAP.get(gate.upper(), [])
    if not gate_entries:
        raise ValueError(f"unknown gate {gate!r} -- must be one of {list(GATE_MAP)}")

    all_required: set[str] = set()
    for _rel, req_roles, _label in gate_entries:
        all_required.update(req_roles)

    if role not in all_required:
        raise ValueError(
            f"role {role!r} is not authorised to sign gate {gate.upper()} "
            f"(required: {sorted(all_required)})"
        )

    signed: list[str] = []
    for rel, req_roles, _label in gate_entries:
        p = root / rel
        if not p.exists():
            continue
        if role not in req_roles:
            raise ValueError(
                f"role {role!r} is not authorised to sign {rel!r} "
                f"(required: {req_roles})"
            )
        sign_artifact(p, signer, role, gate, verdict, conditions)
        if audit_log is not None:
            _append_audit(audit_log, signer, role, gate, rel, p, verdict)
        signed.append(rel)
    return signed


def _append_audit(
    audit_log: Path,
    signer: str,
    role: str,
    gate: str,
    rel_path: str,
    artifact_path: Path,
    verdict: str,
) -> None:
    """Append one row to AUDIT_TRAIL.jsonl after a successful signature."""
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    h = _compute_hash(artifact_path)
    gate_label = GATE_LABELS.get(gate.upper(), gate)
    row = {
        "ts": ts,
        "actor": signer,
        "role": role,
        "action": "sign",
        "gate": gate_label,
        "artifact": rel_path,
        "hash": h,
        "verdict": verdict,
    }
    with audit_log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
