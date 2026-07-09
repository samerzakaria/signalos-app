# signalos_lib/product/provision_gates.py
# Gate provisioning: make the prior governance gates (G0-G3) PRESENT-AND-SIGNED
# so a delivery always has one governed build path -- no fail-open (a build
# route that writes ungoverned product) and no dead-end (a build that can never
# start because nothing signs the gates).
#
# The unifying idea: whatever the repo's state (empty greenfield, or an existing
# codebase we adopt), fill any MISSING gate before the build, then run the ONE
# governed path. The critical guardrail -- the thing that keeps this from being
# a silent auto-rubber-stamp -- is that provisioned gates are signed under an
# EXPLICIT provenance tier, NEVER as the founder:
#
#   founder-signed  -- a human reviewed and approved (produced by the real
#                      gate-walk; provision never mints this).
#   reconstructed   -- inferred from EXISTING code and accepted as-built; the
#                      running code IS the decision, and the founder REVIEWS &
#                      CORRECTS the reconstruction rather than approving anew.
#   assumed         -- auto-provisioned to unblock a headless build (CI /
#                      benchmark / no founder present); NOT reviewed. Weakest
#                      tier; founder review pending.
#
# The signer identity carries the tier in plain words, so every signature says
# exactly what it is. Nothing is faked: an assumed gate says "assumed", a
# reconstructed gate says "reconstructed -- correct me". The build only requires
# a gate be SIGNED (any tier); the delivery reports the tier honestly so a
# reviewer sees which gates still need a human.

from __future__ import annotations

__all__ = ["PROVENANCE_SIGNERS", "provision_gates", "governance_tier_summary"]

from pathlib import Path
from typing import Any, Callable, Optional

from .. import sign as _sign
from ..artifacts import resolve_gate_artifacts

# Provenance tier -> signer identity written into the signature block. NEVER the
# founder -- that is the whole point (cf. the benchmark-fixture lesson: sign as
# a clearly-labelled system identity, never as the user).
PROVENANCE_SIGNERS: dict[str, str] = {
    "assumed": "SignalOS (assumed -- auto-provisioned, NOT founder-reviewed)",
    "reconstructed": "SignalOS (reconstructed from existing code -- founder review pending)",
}

# One approved, non-draft signature per artifact satisfies validate_gate; use
# the gate's primary required role. (Manifest roles: G0 PO+PE, G1 PO, G2 PO,
# G3 PO+PE, G4 PE, G5 QA -- the primary is sufficient for the audit-linked
# signed check.)
_PRIMARY_ROLE: dict[str, str] = {
    "G0": "PE", "G1": "PO", "G2": "PO", "G3": "PE", "G4": "PE", "G5": "QA",
}

_PRIOR_GATES = ("G0", "G1", "G2", "G3")

# ContentFn: (gate, ResolvedGateArtifact) -> str | None. Lets a caller supply
# real content (e.g. greenfield's already-generated strategy/design/acceptance,
# or reconstructed-from-code text). None -> honest provenance-marked default.
ContentFn = Callable[[str, Any], Optional[str]]


def _default_content(gate: str, art: Any, tier: str) -> str:
    """Honest, marker-free artifact body. Must contain NO blocking tokens
    (TODO/TBD/FIXME/{{...}}/[DATE]/...) or signing would refuse it, and must be
    non-template so G0's content check passes."""
    if tier == "reconstructed":
        note = ("This artifact was RECONSTRUCTED from the existing codebase and "
                "accepted as built -- the running code embodies these decisions. "
                "The founder should review and CORRECT any place this misreads "
                "the original intent.")
    else:
        note = ("This artifact was AUTO-PROVISIONED to unblock the governed "
                "build in a headless run (no founder present). It has NOT been "
                "reviewed by the founder; treat it as provisional until reviewed.")
    return (
        f"# {art.label}\n\n"
        f"> Provenance: {tier}. {note}\n\n"
        f"This document records the {art.label} for {gate}. It exists so the "
        "governed build has its prerequisite gate present and signed under an "
        "explicit, non-founder provenance tier.\n"
    )


def provision_gates(
    repo_root: Path,
    project_id: str = "default",
    *,
    tier: str = "assumed",
    gates: tuple = _PRIOR_GATES,
    content_fn: Optional[ContentFn] = None,
) -> dict:
    """Author + sign any MISSING prior-gate artifacts under the given provenance
    tier, so the governed build's precondition (signed G0-G3) is satisfied.
    Never touches an artifact that is already present AND signed (an existing
    founder signature is preserved). Returns {gate: {tier, signer, actions}}."""
    if tier not in PROVENANCE_SIGNERS:
        raise ValueError(f"tier must be one of {sorted(PROVENANCE_SIGNERS)}, got {tier!r}")
    signer = PROVENANCE_SIGNERS[tier]
    audit_log = repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    provisioned: dict = {}

    for gate in gates:
        statuses = _sign.check_gate(repo_root, gate, project_id=project_id)
        actions: list = []

        # 1. Author any missing artifact (marker-free provenance content).
        for st in statuses:
            if not st.exists:
                content = None
                if content_fn is not None:
                    try:
                        content = content_fn(gate, st)
                    except Exception:
                        content = None
                if not content:
                    content = _default_content(gate, st, tier)
                try:
                    st.path.parent.mkdir(parents=True, exist_ok=True)
                    st.path.write_text(content, encoding="utf-8")
                    actions.append(f"authored {st.rel_path}")
                except OSError as exc:
                    actions.append(f"author-failed {st.rel_path}: {exc}")

        # 2. Sign the gate under EVERY role its unsigned artifacts require. A
        #    gate can mix roles (e.g. G3: Design Note=PO, Plan/Acceptance=PE) and
        #    sign_gate enforces separation-of-duties -- a PE cannot sign a PO
        #    artifact -- so signing with a single role leaves the others unsigned.
        #    sign_gate is the canonical writer (signature + hash + audit-linked
        #    row), so validate_gate passes. Already-signed artifacts are skipped.
        statuses = _sign.check_gate(repo_root, gate, project_id=project_id)
        roles: set = set()
        for st in statuses:
            if st.exists and not st.has_signatures:
                roles.update(st.required_roles or (_PRIMARY_ROLE.get(gate, "PE"),))
        for role in sorted(roles):
            try:
                # skip_signed=True: sign ONLY the still-unsigned artifacts this
                # role is authorised for. Without it, sign_gate would append a
                # system co-signature onto artifacts a founder already signed in
                # the same gate/role -- polluting the founder's artifact and
                # (because governance_tier_summary weights an assumed signer
                # above a founder one) mislabelling a founder-reviewed gate as
                # 'assumed'. See sign_gate(skip_signed=...).
                _sign.sign_gate(repo_root, gate, signer, role, "APPROVED",
                                audit_log=audit_log, project_id=project_id,
                                skip_signed=True)
                actions.append(f"signed[{role}]")
            except Exception as exc:
                actions.append(f"sign-failed[{role}]: {exc}")

        if actions:
            provisioned[gate] = {"tier": tier, "signer": signer, "actions": actions}

    return provisioned


def governance_tier_summary(repo_root: Path, project_id: str = "default",
                            gates: tuple = _PRIOR_GATES) -> dict:
    """Honest per-gate provenance for the delivery report: which gates are
    founder-signed vs auto-provisioned (assumed/reconstructed). A reviewer sees
    exactly which gates still need a human."""
    out: dict = {}
    for gate in gates:
        try:
            statuses = _sign.check_gate(repo_root, gate, project_id=project_id)
        except Exception:
            out[gate] = "unknown"
            continue
        signers = [s for st in statuses if st.exists for s in (st.signers or [])]
        if not signers:
            out[gate] = "unsigned"
        elif any(str(s).startswith("SignalOS (assumed") for s in signers):
            out[gate] = "assumed"
        elif any(str(s).startswith("SignalOS (reconstructed") for s in signers):
            out[gate] = "reconstructed"
        else:
            out[gate] = "founder-signed"
    return out
