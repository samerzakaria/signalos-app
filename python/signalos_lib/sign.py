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
    "StrictGateResult",
    "check_gate",
    "sign_artifact",
    "sign_gate",
    "finalize_g5_release",
    "append_audit_event",
    "approve_gate0_as_solo_founder",
    "_compute_hash",
    "_append_audit",
    "verify_audit_chain",
    "_parse_signers",
    "_parse_signatures",
    "check_gate_signed_strict",
    "is_gate_signed_strict",
    "revoke_gate",
    "revoke_gates",
    "clear_gate_revocation",
    "is_gate_revoked",
]

import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .artifacts import (
    GATE_LABELS,
    GATE_MAP,
    expected_gate_artifacts,
    list_gates,
    resolve_gate_artifacts,
)
from .product.release_tree import (
    ReleaseTreeError,
    commit_control_tree,
    commit_release_tree,
    git_control_pathspec,
    git_release_pathspec,
    index_control_tree,
    index_release_tree,
    tree_digest,
    workspace_path,
    workspace_control_tree,
    workspace_release_tree,
)
from .product.run_ids import agent_run_dir, safe_control_path, validate_run_id

VALID_ROLES = ("PO", "PE", "QA", "DevOps")
VALID_VERDICTS = (
    "APPROVED",
    "APPROVED-WITH-CONDITIONS",
    "WAIVED",
    "REQUEST-CHANGES",
    "REJECTED",
)

SOLO_FOUNDER_GATE0_CONSENT = "I approve Gate 0 as sole founder"
_GATE0_APPROVAL_LOCK_TTL_SECONDS = 300.0
_AUDIT_APPEND_LOCK_WAIT_SECONDS = 15.0
_GATE0_BLOCKING_PLACEHOLDER_KINDS = frozenset({
    "double-brace",
    "date-token",
    "link-token",
    "feature-token",
    "fill-token",
    "todo-token",
})

_RELEASE_URL_USERINFO_RE = re.compile(
    r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@"
)
_RELEASE_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|api_key|token|password|client_secret)=)[^&\s]+"
)
_RELEASE_TOKEN_RE = re.compile(
    r"(?i)\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b"
)
_SECRET_ENV_NAME_RE = re.compile(
    r"(?i)(?:^|_)(?:api_?key|token|secret|password|credential)(?:$|_)"
)


def _redact_release_detail(value: object) -> str:
    """Remove credential material before release errors become durable state."""
    text = str(value or "")
    text = _RELEASE_URL_USERINFO_RE.sub(r"\1[REDACTED]@", text)
    text = _RELEASE_QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    text = _RELEASE_TOKEN_RE.sub("[REDACTED]", text)
    for name, secret in os.environ.items():
        if (
            len(secret) >= 8
            and _SECRET_ENV_NAME_RE.search(name)
            and secret in text
        ):
            text = text.replace(secret, "[REDACTED]")
    return text


_AUDIT_LOCK_LOCAL = threading.local()
_GATE0_TRANSACTION_LOCAL = threading.local()
_GATE0_AUTHORITY_CAPABILITY = object()
_RESERVED_AUDIT_ACTIONS = frozenset({"authority:solo-founder-g0-declared"})


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


def _parse_signatures(path: Path) -> list[dict]:
    """Parse each entry of the ## Signatures block into a structured record.

    Unlike ``_parse_signers`` (which only extracts non-DRAFT signer *names*),
    this returns per-signature fields the strict validator needs to reject
    forgeries: the declared ``role``, ``verdict`` (upper-cased), the declared
    ``artifact_hash``, and whether the entry is a DRAFT placeholder.

    Returns [] when there is no ## Signatures block.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^## Signatures", text, re.MULTILINE)
    if not m:
        return []
    block = text[m.start():]

    def _field(chunk: str, name: str) -> str:
        fm = re.search(rf"(?m)^\s*{name}:\s*(.+)$", chunk)
        return fm.group(1).strip() if fm else ""

    sigs: list[dict] = []
    # Each entry starts with a top-level "- signer:" list item; splitting on
    # that boundary gives one chunk per signature (fields until the next entry).
    for chunk in re.split(r"(?m)^\s*-\s+signer:\s*", block)[1:]:
        first_line = chunk.splitlines()[0].strip() if chunk.strip() else ""
        sigs.append({
            "signer": first_line,
            "role": _field(chunk, "role"),
            "verdict": _field(chunk, "verdict").upper(),
            "artifact_hash": _field(chunk, "artifact_hash"),
            "is_draft": "DRAFT" in chunk.upper(),
        })
    return sigs


# ---------------------------------------------------------------------------
# Durable gate revocation (minimal, fail-closed marker)
# ---------------------------------------------------------------------------
#
# Reopening a signed gate must survive a process restart: the orchestrator's
# in-memory reopen state is invisible to a fresh status board, so a reopened
# gate would keep reading "signed" off its still-present (now stale) signature
# block -- a fail-open. A tiny durable marker closes it: the strict validator
# treats any gate named in `.signalos/gate-revocations.json` as NOT signed
# until it is legitimately re-signed (sign_gate clears the marker on a fresh
# signature). This is the minimal marker the design calls for; a full
# append-only revocation ledger is a later epoch.
#
# The marker lives in the per-project state dir (projects.project_state_dir),
# so a revocation in one project never bleeds into another.

def _gate_revocations_path(root: Path, project_id: str = "default") -> Path:
    from .projects import project_state_dir

    candidate = project_state_dir(root, project_id) / "gate-revocations.json"
    return _path_inside_workspace(root, candidate)


def _load_revocations(root: Path, project_id: str = "default") -> dict:
    try:
        p = _gate_revocations_path(root, project_id)
    except ValueError as exc:
        return {"__invalid__": {"reason": f"unsafe revocation ledger: {exc}"}}
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError:
        return {"__invalid__": {"reason": "revocation ledger unreadable"}}
    except ValueError:
        return {"__invalid__": {"reason": "revocation ledger malformed"}}
    return data if isinstance(data, dict) else {
        "__invalid__": {"reason": "revocation ledger is not an object"},
    }


def _write_revocations(root: Path, data: dict, project_id: str = "default") -> None:
    p = _gate_revocations_path(root, project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def is_gate_revoked(repo_root: Path, gate: str, project_id: str = "default") -> bool:
    """True when *gate* carries a durable revocation marker for *project_id*."""
    data = _load_revocations(Path(repo_root), project_id)
    return "__invalid__" in data or str(gate).strip().upper() in data


def revoke_gate(
    repo_root: Path,
    gate: str,
    project_id: str = "default",
    *,
    reason: str = "",
    actor: str = "",
) -> None:
    """Durably mark *gate* revoked/reopened so every gating surface reads it as
    NOT signed until it is legitimately re-signed.

    This is the authoritative hook the gate-reopen path should call (the
    orchestrator's in-memory reopen alone does not survive a fresh process).
    """
    revoke_gates(
        repo_root, [gate], project_id=project_id, reason=reason, actor=actor,
    )


def revoke_gates(
    repo_root: Path,
    gates: list[str] | tuple[str, ...],
    project_id: str = "default",
    *,
    reason: str = "",
    actor: str = "",
) -> None:
    """Durably revoke a target/cascade set or raise fail-closed.

    The append-only audit reversal is written before the mutable lookup marker.
    If the marker write is interrupted, strict validation still sees the audit
    reversal; if audit append fails, no marker mutation is attempted.
    """
    root = Path(repo_root)
    data = _load_revocations(root, project_id)
    if "__invalid__" in data:
        raise OSError("existing gate revocation ledger is unreadable or malformed")
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gate_ids: list[str] = []
    for raw_gate in gates:
        gate_id = str(raw_gate).strip().upper()
        if gate_id not in GATE_MAP:
            raise ValueError(f"unknown gate revocation target: {raw_gate!r}")
        if gate_id in gate_ids:
            continue
        gate_ids.append(gate_id)
        data[gate_id] = {
            "ts": timestamp,
            "reason": reason,
            "actor": actor,
        }
    if not gate_ids:
        raise ValueError("gate revocation requires at least one gate")
    append_audit_event(
        root / ".signalos" / "AUDIT_TRAIL.jsonl",
        {
            "action": "gate.revoke",
            "kind": "revocation",
            "gate": gate_ids[0] if len(gate_ids) == 1 else None,
            "gates": gate_ids,
            "actor": actor,
            "reason": reason,
            "project_id": project_id,
        },
    )
    _write_revocations(root, data, project_id)


def clear_gate_revocation(
    repo_root: Path, gate: str, project_id: str = "default"
) -> None:
    """Remove the revocation marker for *gate* (called after a fresh re-sign)."""
    root = Path(repo_root)
    gate_id = str(gate).strip().upper()
    data = _load_revocations(root, project_id)
    if "__invalid__" in data:
        raise OSError("gate revocation ledger is unreadable or malformed")
    if gate_id in data:
        del data[gate_id]
        _write_revocations(root, data, project_id)


# ---------------------------------------------------------------------------
# Canonical STRICT gate validator — the single source of truth
# ---------------------------------------------------------------------------
#
# Every gating surface (status board, wave_engine.inspect, build preflight)
# routes its "is this gate signed?" question through here so a forged, rejected,
# hashless, wrong-role, tampered, or revoked signature can never read as signed.
# It converges on the dedicated `commands.validate_gate` strong checks (artifact
# present + signed + not-draft + current-hash-valid + audit-trail-linked with an
# APPROVED verdict and a matching hash) and ADDS the two guarantees that path
# does not yet cover: signer-role authorization and durable revocation.

@dataclass
class StrictGateResult:
    """Verdict of the strict gate validator plus the reasons a gate is unsigned."""
    gate: str
    signed: bool
    reasons: list[str] = field(default_factory=list)


def _gate_authorized_roles(
    root: Path, gate: str, project_id: str = "default"
) -> set[str]:
    """Roles authorized to sign *gate* — the union of every artifact's
    required_roles, exactly the expectation ``sign_gate`` enforces at sign time.
    """
    roles: set[str] = set()
    for artifact in resolve_gate_artifacts(root, gate, project_id=project_id):
        roles.update(artifact.required_roles)
    return roles


def _unauthorized_signature_reasons(
    root: Path, gate: str, project_id: str = "default"
) -> list[str]:
    """Reasons any present artifact lacks an APPROVED, current-hash signature
    from a role authorized for that specific artifact. Empty list == every
    artifact is properly signed by one of its declared roles.

    validate_gate anchors the verdict/hash in the AUDIT ROW; this additionally
    pins them on the IN-FILE signature block so an APPROVED-audit-row paired
    with a REJECTED / hashless / wrong-role in-file block cannot pass.
    """
    from .commands.validate_gate import _APPROVED_VERDICTS

    reasons: list[str] = []
    for artifact in resolve_gate_artifacts(root, gate, project_id=project_id):
        p = artifact.path
        if not p.exists():
            continue  # presence is enforced upstream by validate_gate
        # Authorization is artifact-specific.  Using the union of every role in
        # the gate let a PO signature validate PE-only G0 artifacts as long as
        # PO appeared on a different artifact in the same gate.
        allowed = set(artifact.required_roles)
        current_hash = _compute_hash(p)
        authorized = any(
            (not s["is_draft"])
            and s["verdict"] in _APPROVED_VERDICTS
            and s["role"] in allowed
            and s["artifact_hash"]
            and s["artifact_hash"] == current_hash
            for s in _parse_signatures(p)
        )
        if not authorized:
            reasons.append(
                f"{gate}: {artifact.rel_path} lacks an APPROVED, current-hash "
                f"signature from an authorized role (allowed: {sorted(allowed)})"
            )
    return reasons


def _production_release_evidence_reasons(
    root: Path,
    evidence: dict,
) -> list[str]:
    """Validate the exact production safety receipt used by outcome signing.

    The orchestrator performs the primary readiness decision, but ``sign_gate``
    is the canonical write boundary and must enforce the same shape itself.  A
    persisted ``release_verification.ok`` flag is not a substitute for current
    security, runtime, and (when applicable) executed browser evidence.
    """
    reasons: list[str] = []
    security = evidence.get("security_gate")
    if (
        not isinstance(security, dict)
        or str(security.get("status") or "") != "passed"
    ):
        reasons.append("production security gate did not pass")

    runtime = evidence.get("runtime_proof")
    if not isinstance(runtime, dict):
        return reasons + ["production runtime proof is missing"]
    if runtime.get("status") != "passed" or runtime.get("ok") is not True:
        reasons.append("production runtime proof did not pass")

    persisted_required = runtime.get("ux_required")
    if type(persisted_required) is not bool:
        reasons.append("production UX requirement evidence is missing")
        persisted_required = True

    current_required = True
    try:
        from .product.proof import requires_browser_ux_proof
        from .product.stacks import detect_profile

        current_stack = detect_profile(root)
        current_required = requires_browser_ux_proof(root, current_stack)
        if str(runtime.get("stack") or "") != current_stack:
            reasons.append("production runtime proof stack no longer matches the product")
        if persisted_required is not current_required:
            reasons.append("production UX requirement no longer matches the product")
    except Exception as exc:
        reasons.append(
            "production browser UX requirement could not be determined "
            f"({type(exc).__name__}: {exc})"
        )

    if bool(persisted_required or current_required):
        if runtime.get("ux_status") != "passed":
            reasons.append("production browser UX proof did not pass")
        if runtime.get("ux_executed") is not True:
            reasons.append("production browser UX proof was not executed")
        if runtime.get("ux_schema_version") != "signalos.ux-browser-proof.v1":
            reasons.append("production browser UX proof schema is missing or invalid")
    return list(dict.fromkeys(reasons))


def _g5_release_proof_reasons(root: Path, project_id: str) -> list[str]:
    """Require an ordered, run-bound G4->G5 release checkpoint for strict G5."""
    try:
        runs = safe_control_path(root, ".signalos", "agent-runs")
    except ValueError as exc:
        return [f"G5: governed delivery storage is unsafe ({exc})"]
    if not runs.is_dir():
        return ["G5: no governed delivery release proof exists"]
    try:
        entries = sorted(runs.iterdir(), key=lambda item: item.name, reverse=True)
    except OSError as exc:
        return [f"G5: governed delivery storage cannot be inspected ({exc})"]
    for entry in entries:
        try:
            run_id = validate_run_id(entry.name)
            run_dir = agent_run_dir(root, run_id)
            state_path = safe_control_path(
                root, ".signalos", "agent-runs", run_id, "delivery.json",
            )
            attribution_path = safe_control_path(
                root, ".signalos", "agent-runs", run_id, "g4-attribution.json",
            )
        except ValueError:
            continue
        if state_path.parent != run_dir or attribution_path.parent != run_dir:
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(state, dict):
            continue
        if str(state.get("project_id") or "default") != project_id:
            continue
        if str(state.get("run_id") or "") != run_id:
            continue
        if state.get("current_gate") != "G5" or "G5" not in (state.get("signed") or []):
            continue
        if state.get("status") not in {"complete", "release-pending"}:
            continue
        evidence = state.get("release_evidence")
        if not isinstance(evidence, dict):
            continue
        if state.get("profile") == "production" and _production_release_evidence_reasons(
            root, evidence,
        ):
            continue
        verification = evidence.get("release_verification")
        marker = evidence.get("release_finalization")
        if not isinstance(verification, dict) or not isinstance(marker, dict):
            continue
        digest = str(verification.get("release_digest") or "")
        if verification.get("ok") is not True or not digest:
            continue
        if (
            marker.get("schema_version") != "signalos.release-finalization.v1"
            or marker.get("status") not in {"pending", "succeeded"}
            or marker.get("phase") != "signed"
            or str(marker.get("run_id") or "") != run_id
            or str(marker.get("project_id") or "") != project_id
            or str(marker.get("profile") or "") != str(state.get("profile") or "")
            or str(marker.get("release_digest") or "") != digest
        ):
            continue
        try:
            attribution = json.loads(attribution_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(attribution, dict):
            continue
        release_tree = attribution.get("release_tree")
        if (
            attribution.get("version") != 4
            or attribution.get("phase") != "verified"
            or str(attribution.get("run_id") or "") != run_id
            or str(attribution.get("project_id") or "default") != project_id
            or not isinstance(attribution.get("verification"), dict)
            or attribution["verification"].get("ok") is not True
            or not isinstance(release_tree, dict)
        ):
            continue
        computed = tree_digest(release_tree)
        if computed != digest or attribution.get("release_digest") != digest:
            continue
        try:
            current = tree_digest(workspace_release_tree(root))
        except (OSError, ValueError, ReleaseTreeError):
            continue
        if current != digest:
            continue
        return []
    return [
        "G5: signatures are not linked to a current ordered G4 release proof "
        "and G5 finalization checkpoint"
    ]


def check_gate_signed_strict(
    repo_root: Path,
    gate: str,
    project_id: str = "default",
    *,
    wave: str | int | None = None,
    require_release_proof: bool = True,
) -> StrictGateResult:
    """Return whether *gate* is validly signed, with reasons when it is not.

    Signed only when ALL hold:
      * NON-REVOKED   — no durable revocation marker for the gate;
      * present + signed (non-draft) + VALID CURRENT artifact hash +
        AUDIT-LINKED with an APPROVED verdict and a matching hash — reused
        verbatim from ``commands.validate_gate.validate_gate`` (the same strong
        path the ``signalos validate-gate`` command enforces);
      * AUTHORIZED ROLE — every present artifact carries an APPROVED, current-
        hash in-file signature from a role authorized for the gate.
    """
    root = Path(repo_root)
    gate_id = str(gate).strip().upper()

    # (1) Durable revocation is authoritative — even a still-present, otherwise
    #     valid signature block reads NOT signed once the gate is reopened.
    if is_gate_revoked(root, gate_id, project_id=project_id):
        return StrictGateResult(
            gate_id, False,
            [f"{gate_id}: revoked/reopened (durable revocation marker)"],
        )

    # A prepared G0 approval journal means a process stopped between durable
    # writes.  Never expose that partial transaction as signed; the next
    # approval attempt will restore the recorded snapshots before retrying.
    if gate_id == "G0":
        try:
            transaction = _load_gate0_transaction(root, project_id)
        except Exception as exc:
            return StrictGateResult(
                gate_id,
                False,
                [f"G0: approval recovery journal is invalid: {exc}"],
            )
        active_transaction = getattr(_GATE0_TRANSACTION_LOCAL, "active", None)
        owns_transaction = active_transaction == (
            os.path.normcase(str(root.resolve())),
            project_id,
            transaction.get("transaction_id") if transaction else None,
        )
        if (
            transaction
            and transaction.get("phase") == "prepared"
            and not owns_transaction
        ):
            return StrictGateResult(
                gate_id,
                False,
                ["G0: an interrupted approval transaction requires recovery"],
            )

    # Matching sign rows are not sufficient when the append-only audit chain
    # itself has been edited, reordered, or truncated.  All gate consumers use
    # this strict path, so chain corruption must fail closed here rather than
    # only in the standalone audit command.
    audit_log = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit_violations = verify_audit_chain(audit_log)
    if audit_violations:
        return StrictGateResult(
            gate_id,
            False,
            [f"{gate_id}: audit integrity failure: {item}" for item in audit_violations],
        )

    # (2) Strong checks — reuse the dedicated validator (single source of truth
    #     for present/signed/hash/audit-link). Deferred import breaks the
    #     validate_gate -> sign import cycle. write_evidence=False keeps the
    #     board render side-effect-free.
    try:
        from .commands.validate_gate import validate_gate as _validate_gate
        payload = _validate_gate(
            root, gate_id, wave=wave, write_evidence=False, project_id=project_id,
        )
    except Exception as exc:  # never let a gating surface crash on a check
        return StrictGateResult(
            gate_id, False,
            [f"{gate_id}: strict validation error: {type(exc).__name__}: {exc}"],
        )
    if not payload.get("ok"):
        reasons = [f"{b['id']}: {b['message']}" for b in payload.get("blockers", [])]
        return StrictGateResult(
            gate_id, False, reasons or [f"{gate_id}: gate is not validly signed"],
        )

    # (3) Role authorization — validate_gate does not check the signer's role.
    role_reasons = _unauthorized_signature_reasons(root, gate_id, project_id=project_id)
    if role_reasons:
        return StrictGateResult(gate_id, False, role_reasons)

    # This desktop currently has one authenticated authority mode for G0: the
    # persisted PO's explicit, project-bound sole-founder declaration.  A raw
    # PE role string from a CLI/script is not authentication and must not open
    # the build gate.  A future multi-person mode must introduce its own
    # authenticated authority event rather than silently bypassing this rule.
    if gate_id == "G0":
        if not _matching_gate0_authorities(audit_log, root, project_id):
            return StrictGateResult(
                gate_id,
                False,
                ["G0: signatures lack a valid project-bound PO authority declaration"],
            )
        unresolved = [
            f"{artifact.rel_path}: {finding}"
            for artifact in resolve_gate_artifacts(root, gate_id, project_id=project_id)
            if artifact.path.is_file()
            for finding in _gate0_placeholder_violations(artifact.path)
        ]
        if unresolved:
            return StrictGateResult(
                gate_id,
                False,
                [f"G0: unresolved governance template marker: {item}" for item in unresolved],
            )

    if gate_id == "G5" and require_release_proof:
        proof_reasons = _g5_release_proof_reasons(root, project_id)
        if proof_reasons:
            return StrictGateResult(gate_id, False, proof_reasons)

    return StrictGateResult(gate_id, True, [])


def is_gate_signed_strict(
    repo_root: Path,
    gate: str,
    project_id: str = "default",
    *,
    wave: str | int | None = None,
    require_release_proof: bool = True,
) -> bool:
    """Boolean convenience wrapper over :func:`check_gate_signed_strict`."""
    return check_gate_signed_strict(
        repo_root, gate, project_id=project_id, wave=wave,
        require_release_proof=require_release_proof,
    ).signed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_gate(
    root: Path,
    gate: str,
    project_id: str = "default",
) -> list[ArtifactStatus]:
    """Return signature status of every artifact required for *gate*.

    *project_id* namespaces the artifact paths (§3.2) via
    ``artifacts.resolve_gate_artifacts``; "default" is byte-identical to
    the historical workspace-root layout.
    """
    result: list[ArtifactStatus] = []
    for artifact in resolve_gate_artifacts(root, gate, project_id=project_id):
        p = artifact.path
        status = ArtifactStatus(
            path=p,
            rel_path=artifact.rel_path,
            label=artifact.label,
            required_roles=list(artifact.required_roles),
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


def _governed_outcome_proof_reasons(
    root: Path,
    gate: str,
    *,
    delivery_run_id: str,
    project_id: str,
    signer: str,
) -> list[str]:
    """Validate the durable verifier receipt required to write G4/G5.

    A module-level object is not authority: any direct Python caller can import
    it.  Outcome signing therefore reloads the current run-bound checkpoint and
    independently re-hashes the current release payload at the final write
    boundary.  The orchestrator cannot accidentally bypass this check because
    it calls this same canonical signer.
    """
    gate = gate.upper()
    try:
        run_id = validate_run_id(str(delivery_run_id or "").strip())
    except ValueError:
        return [f"{gate}: a valid governed delivery_run_id is required"]
    try:
        run_dir = agent_run_dir(root, run_id)
        state_path = safe_control_path(
            root, ".signalos", "agent-runs", run_id, "delivery.json",
        )
        proof_path = safe_control_path(
            root, ".signalos", "agent-runs", run_id, "g4-attribution.json",
        )
    except ValueError as exc:
        return [f"{gate}: governed verifier storage is unsafe ({exc})"]
    if state_path.parent != run_dir or proof_path.parent != run_dir:
        return [f"{gate}: governed verifier storage is not run-bound"]
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return [f"{gate}: governed verifier proof is missing or unreadable ({exc})"]
    reasons: list[str] = []
    if not isinstance(state, dict) or not isinstance(proof, dict):
        return [f"{gate}: governed verifier proof is malformed"]
    if str(state.get("run_id") or "") != run_id or state_path.parent.name != run_id:
        reasons.append(f"{gate}: delivery checkpoint run_id mismatch")
    if str(state.get("project_id") or "default") != project_id:
        reasons.append(f"{gate}: delivery checkpoint project mismatch")
    if str(state.get("signer") or "") != str(signer or ""):
        reasons.append(f"{gate}: signer does not match the delivery identity")
    if state.get("current_gate") != gate:
        reasons.append(f"{gate}: delivery is not currently at this gate")
    if state.get("status") not in {"awaiting-verdict", "reopened"}:
        reasons.append(f"{gate}: delivery is not open for an outcome verdict")
    outcome = state.get("last_outcome")
    if not isinstance(outcome, dict) or outcome.get("ok") is not True:
        reasons.append(f"{gate}: gate agent did not produce a reviewable outcome")

    if proof.get("version") != 4:
        reasons.append(f"{gate}: G4 verifier proof version is unsupported")
    if str(proof.get("run_id") or "") != run_id:
        reasons.append(f"{gate}: G4 verifier proof run_id mismatch")
    if str(proof.get("project_id") or "default") != project_id:
        reasons.append(f"{gate}: G4 verifier proof project mismatch")
    verification = proof.get("verification")
    release_tree = proof.get("release_tree")
    post_tree = proof.get("post_tree")
    if (
        proof.get("phase") != "verified"
        or not isinstance(verification, dict)
        or verification.get("ok") is not True
    ):
        reasons.append(f"{gate}: G4 verifier did not approve this build")
    if not isinstance(post_tree, dict) or tree_digest(post_tree) != proof.get("post_digest"):
        reasons.append(f"{gate}: G4 product-source receipt is malformed")
    meaningful = proof.get("meaningful_written_product_source")
    if not isinstance(meaningful, list) or not meaningful:
        reasons.append(f"{gate}: G4 proof contains no meaningful generated product source")
    if not isinstance(release_tree, dict):
        reasons.append(f"{gate}: G4 release tree is missing")
        expected_digest = ""
    else:
        expected_digest = tree_digest(release_tree)
        if expected_digest != str(proof.get("release_digest") or ""):
            reasons.append(f"{gate}: G4 release-tree receipt hash is invalid")
    try:
        current_digest = tree_digest(workspace_release_tree(root))
    except (OSError, ValueError, ReleaseTreeError) as exc:
        reasons.append(f"{gate}: current release payload cannot be read ({exc})")
    else:
        if not expected_digest or current_digest != expected_digest:
            reasons.append(f"{gate}: release payload changed after G4 verification")

    prior = range(4) if gate == "G4" else range(5)
    signed_state = set(state.get("signed") or [])
    for number in prior:
        prior_gate = f"G{number}"
        if prior_gate not in signed_state:
            reasons.append(f"{gate}: {prior_gate} is absent from this delivery state")
            continue
        strict = check_gate_signed_strict(
            root, prior_gate, project_id=project_id,
        )
        if not strict.signed:
            reasons.extend(strict.reasons or [f"{gate}: {prior_gate} is not strictly signed"])

    if gate == "G5":
        evidence = state.get("release_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        release = evidence.get("release_verification")
        marker = evidence.get("release_finalization")
        if not isinstance(release, dict) or release.get("ok") is not True:
            reasons.append("G5: current release verification did not pass")
            release_digest = ""
        else:
            release_digest = str(release.get("release_digest") or "")
        if release_digest != expected_digest:
            reasons.append("G5: release verification is not bound to the G4 tree")
        if not isinstance(marker, dict) or (
            marker.get("schema_version") != "signalos.release-finalization.v1"
            or marker.get("status") != "pending"
            or marker.get("phase") != "verified"
            or str(marker.get("run_id") or "") != run_id
            or str(marker.get("project_id") or "") != project_id
            or str(marker.get("profile") or "") != str(state.get("profile") or "")
            or str(marker.get("release_digest") or "") != expected_digest
        ):
            reasons.append("G5: verified finalization checkpoint is missing or invalid")
        if state.get("waived"):
            reasons.append("G5: delivery contains waived gates")
        if state.get("conditions"):
            reasons.append("G5: delivery contains unresolved approval conditions")
        if state.get("profile") == "production":
            reasons.extend(
                f"G5: {reason}"
                for reason in _production_release_evidence_reasons(root, evidence)
            )
    return list(dict.fromkeys(str(reason) for reason in reasons if reason))


def _gate_complete_enough_to_clear_revocation(
    root: Path, gate: str, project_id: str,
) -> bool:
    """Validate fresh signatures without consulting the revocation marker."""
    from .commands.validate_gate import validate_gate

    try:
        payload = validate_gate(
            root, gate, project_id=project_id, write_evidence=False,
        )
        if payload.get("ok") is not True:
            return False
        if _unauthorized_signature_reasons(root, gate, project_id=project_id):
            return False
        if gate.upper() == "G0" and not _matching_gate0_authorities(
            root / ".signalos" / "AUDIT_TRAIL.jsonl", root, project_id,
        ):
            return False
    except Exception:
        return False
    return True


def sign_gate(
    root: Path,
    gate: str,
    signer: str,
    role: str,
    verdict: str,
    conditions: str = "",
    audit_log: Path | None = None,
    wave: str | None = None,
    oidc_sub_hash: str = "",
    oidc_issuer: str = "",
    project_id: str = "default",
    skip_signed: bool = False,
    finalize_release: bool = True,
    delivery_run_id: str = "",
) -> list[str]:
    """
    Sign every present artifact in *gate*.  Returns rel-paths of signed files.
    Missing artifacts are skipped.

    ``skip_signed`` (default False) additionally skips any artifact that ALREADY
    carries a signature. This is for auto-provisioning: it must fill only the
    UNSIGNED artifacts of a gate and must never append a system co-signature onto
    an artifact a founder already signed (which would both pollute the founder's
    artifact and mislabel the gate's provenance). validate_gate accepts an
    artifact with >=1 non-draft signature, so leaving a co-required role's second
    signature off a system-authored artifact still passes the gate.

    G4/G5 additionally require a durable, current, run-bound verifier receipt.
    Public raw signing cannot manufacture a successful build/release by hashing
    a pre-existing governance artifact or importing a process-local sentinel.

    Raises ValueError if role is not authorised for any artifact in gate.
    This enforces segregation of duties: PO cannot sign G5 (requires QA),
    PE cannot sign G1 (requires PO), etc.

    #17 Edit 3.4: `oidc_sub_hash`/`oidc_issuer` are threaded through so the CLI
    sign path (commands/sign.py) can route through this single role-enforcing
    function while preserving W6.3 OIDC evidence in the signature block.

    §3.2: *project_id* namespaces the artifact paths through the same
    resolver every gate reader uses (projects.project_governance_dir), so a
    signature written here is visible to wave_engine.inspect / status /
    orchestrator gating for the SAME project — and only that project.
    AUDIT_TRAIL stays workspace-global by design.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")
    if gate.upper() in {"G4", "G5"}:
        proof_reasons = _governed_outcome_proof_reasons(
            Path(root), gate,
            delivery_run_id=delivery_run_id,
            project_id=project_id,
            signer=signer,
        )
        if proof_reasons:
            raise ValueError(
                f"outcome gate {gate.upper()} lacks current governed proof: "
                + "; ".join(proof_reasons)
            )

    gate_entries = resolve_gate_artifacts(root, gate, project_id=project_id)
    if not gate_entries:
        raise ValueError(f"unknown gate {gate!r} -- must be one of {list_gates()}")

    all_required: set[str] = set()
    for artifact in gate_entries:
        all_required.update(artifact.required_roles)

    if role not in all_required:
        raise ValueError(
            f"role {role!r} is not authorised to sign gate {gate.upper()} "
            f"(required: {sorted(all_required)})"
        )

    # A gate signature that is not recorded in the workspace audit trail is not
    # verifiable -- and the strict validator (is_gate_signed_strict) treats such
    # a gate as NOT signed. Default the audit log to the workspace-global trail
    # so every sign_gate call is audit-linked by default, matching the CLI and
    # orchestrator signing paths. AUDIT_TRAIL.jsonl is workspace-global (never
    # per-project), so it is anchored at *root* regardless of project_id.
    workspace_root = Path(root)
    if audit_log is None:
        audit_log = workspace_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    audit_log = _path_inside_workspace(workspace_root, Path(audit_log))

    signed: list[str] = []
    for artifact in gate_entries:
        p = artifact.path
        if not p.exists():
            continue
        # SoD for MIXED-role gates: a gate (e.g. G3) can hold artifacts that
        # require different roles (PLAN.md->PE, others->PO). This role signs the
        # artifacts it is authorised for and SKIPS the rest (which the other
        # role signs in its own call) -- matching the docstring ("raises if the
        # role is not authorised for ANY artifact", already enforced by the
        # union check above). Raising here instead broke legitimate multi-role
        # gate signing (a PO signing G3 died on the PE-only PLAN.md).
        if role not in artifact.required_roles:
            continue
        # Auto-provision guard: never co-sign an artifact that already carries a
        # signature (founder or a prior provisioning role). One non-draft
        # signature satisfies validate_gate, so this preserves both the gate's
        # signed-ness and the existing signer's provenance.
        if skip_signed:
            existing_signers, _is_draft, _hash_valid = _parse_signers(p)
            if existing_signers:
                continue
        sign_artifact(
            p,
            signer,
            role,
            gate,
            verdict,
            conditions,
            oidc_sub_hash=oidc_sub_hash,
            oidc_issuer=oidc_issuer,
        )
        if audit_log is not None:
            _append_audit(
                audit_log,
                signer,
                role,
                gate,
                artifact.rel_path,
                p,
                verdict,
                wave=wave,
                project_id=project_id,
            )
        signed.append(artifact.rel_path)

    # A fresh, authorized signature supersedes any prior reopen/revocation: the
    # gate has just been legitimately re-signed, so clear its durable revocation
    # marker (if any) rather than leaving the gate stuck reading NOT signed.
    if signed and _gate_complete_enough_to_clear_revocation(
        Path(root), gate, project_id,
    ):
        clear_gate_revocation(root, gate, project_id=project_id)

    # M4: after a successful G5 sign, push the local commits to origin
    # so the user actually ships their work. Best-effort — a push
    # failure (network blip, no remote, no OAuth client ID) records a
    # deferred / failed outcome but does NOT undo the gate signature.
    if signed and gate.upper() == "G5" and finalize_release:
        try:
            finalize_g5_release(root, project_id=project_id)
        except Exception as exc:
            _record_g5_push_outcome(
                root, "failed", f"unhandled: {exc}", project_id=project_id,
            )

        # Phase 13 hardening: create an integrity seal after a successful
        # G5 sign. Best-effort — a seal failure must never block the gate.
    return signed


def finalize_g5_release(
    root: Path,
    *,
    release_id: str = "",
    release_digest: str = "",
    project_id: str = "default",
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """Finalize an already-signed G5 after terminal delivery state exists.

    The orchestrator suppresses ``sign_gate``'s immediate side effects, writes
    its complete checkpoint and closeout, then calls this once. Seal first; its
    path/hash receipt is persisted beside the exact Git commit/push outcome
    (the local .signalos seal file itself is not committed). Unlike the historical
    fire-and-forget hook, this function returns the honest outcome of every
    stage so the orchestrator can durably record ``succeeded``, ``deferred``, or
    ``failed`` and safely recover a crash that left the release ``pending``.
    """
    cancelled = cancel_check or (lambda: False)
    if cancelled():
        return {
            "status": "cancelled",
            "seal": {"status": "deferred", "reason": "cancelled before seal"},
            "commit": {"status": "deferred", "reason": "cancelled before commit"},
            "push": {"status": "deferred", "reason": "cancelled before push"},
        }
    try:
        seal = _auto_seal_on_g5(root, project_id=project_id)
    except Exception as exc:
        reason = f"unhandled: {exc}"
        _record_g5_seal_outcome(
            root, "failed", reason, project_id=project_id,
        )
        seal = {"status": "failed", "reason": reason}
    if not isinstance(seal, dict):
        seal = {"status": "failed", "reason": "seal hook returned no outcome"}

    # An integrity seal is part of the approved release bundle. Do not commit or
    # push an unsealed tree; return an honest failed outcome for durable retry.
    if seal.get("status") != "ok":
        return {
            "status": "failed",
            "seal": seal,
            "commit": {"status": "deferred", "reason": "seal failed"},
            "push": {"status": "deferred", "reason": "seal failed"},
        }

    if cancelled():
        return {
            "status": "cancelled",
            "seal": seal,
            "commit": {"status": "deferred", "reason": "cancelled before commit"},
            "push": {"status": "deferred", "reason": "cancelled before push"},
        }

    try:
        git = _auto_push_on_g5(
            root, release_id=release_id, release_digest=release_digest,
            project_id=project_id,
            cancel_check=cancelled,
        )
    except Exception as exc:
        reason = f"unhandled: {exc}"
        _record_g5_push_outcome(root, "failed", reason, project_id=project_id)
        git = {
            "commit": {"status": "failed", "reason": reason},
            "push": {"status": "failed", "reason": reason},
        }
    if not isinstance(git, dict):
        git = {
            "commit": {"status": "failed", "reason": "git hook returned no outcome"},
            "push": {"status": "failed", "reason": "git hook returned no outcome"},
        }

    commit = git.get("commit") if isinstance(git.get("commit"), dict) else {}
    push = git.get("push") if isinstance(git.get("push"), dict) else {}
    statuses = {
        str(seal.get("status") or "failed"),
        str(commit.get("status") or "failed"),
        str(push.get("status") or "failed"),
    }
    if "cancelled" in statuses:
        status = "cancelled"
    elif "failed" in statuses:
        status = "failed"
    elif "deferred" in statuses:
        status = "deferred"
    else:
        status = "succeeded"
    return {"status": status, "seal": seal, "commit": commit, "push": push}


def _path_inside_workspace(root: Path, path: Path) -> Path:
    """Return *path* only when it is a non-symlinked workspace-local path.

    Governance writes are authority-bearing.  A `.signalos` or artifact
    symlink must not redirect them outside the selected workspace.
    """
    lexical_workspace = Path(os.path.abspath(str(root)))
    workspace = Path(root).resolve()
    candidate = Path(os.path.abspath(str(path)))
    try:
        relative = candidate.relative_to(lexical_workspace)
    except ValueError:
        # Callers may already hold canonical paths.  Accept that spelling too,
        # while preserving the lexical relative path whenever the selected
        # workspace itself is an alias (notably macOS /var -> /private/var).
        try:
            relative = candidate.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(
                f"governance path escapes the workspace: {path}"
            ) from exc
    try:
        return workspace_path(
            workspace, relative.as_posix(), allow_leaf_symlink=False,
        )
    except ReleaseTreeError as exc:
        raise ValueError(f"unsafe governance path {path}: {exc}") from exc


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, payload: dict) -> None:
    _atomic_write_bytes(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _pid_is_alive(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return True
    if value <= 0:
        return True
    if value == os.getpid():
        return True
    if sys.platform == "win32":
        # CPython maps os.kill(pid, 0) to TerminateProcess on Windows.  A
        # liveness check must therefore use a read-only process handle.
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            still_active = 259
            error_access_denied = 5
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, value
            )
            if not handle:
                return ctypes.get_last_error() == error_access_denied
            try:
                code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == still_active
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _lock_is_reclaimable(path: Path, ttl_seconds: float) -> bool:
    try:
        owner = json.loads(path.read_text(encoding="utf-8"))
        created_at = float(owner.get("created_at", 0.0))
        pid = owner.get("pid")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        try:
            created_at = path.stat().st_mtime
        except OSError:
            return False
        pid = None
    age = max(0.0, time.time() - created_at)
    # Time alone never evicts a provably live owner.  It only bounds recovery
    # of corrupt/ownerless lock files; a dead process can be reclaimed at once.
    return (pid is not None and not _pid_is_alive(pid)) or (
        pid is None and age > ttl_seconds
    )


def _create_owned_lock(path: Path, metadata: dict) -> str | None:
    token = uuid.uuid4().hex
    record = {
        **metadata,
        "owner_token": token,
        "pid": os.getpid(),
        "created_at": time.time(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            return token
        except FileExistsError:
            if attempt == 0 and _lock_is_reclaimable(
                path, _GATE0_APPROVAL_LOCK_TTL_SECONDS
            ):
                try:
                    path.unlink()
                except OSError:
                    return None
                continue
            return None
    return None


def _release_owned_lock(path: Path, token: str | None) -> None:
    if not token:
        return
    try:
        owner = json.loads(path.read_text(encoding="utf-8"))
        if owner.get("owner_token") == token:
            path.unlink()
    except (OSError, json.JSONDecodeError):
        # Never remove a lock when ownership cannot be proven.
        return


def _gate0_approval_lock_path(root: Path) -> Path:
    return _path_inside_workspace(
        root, Path(root).resolve() / ".signalos" / "locks" / "gate0-approval.lock"
    )


def _try_acquire_gate0_approval_lock(
    root: Path, approval_id: str, project_id: str
) -> tuple[Path, str] | None:
    """Atomically acquire the workspace-wide G0 transaction lock."""
    path = _gate0_approval_lock_path(root)
    token = _create_owned_lock(
        path, {"approval_id": approval_id, "project_id": project_id}
    )
    return (path, token) if token else None


def _release_gate0_approval_lock(lock: tuple[Path, str] | None) -> None:
    if lock:
        _release_owned_lock(lock[0], lock[1])


def _audit_append_lock_path(audit_log: Path) -> Path:
    return audit_log.parent / f".{audit_log.name}.append.lock"


@contextmanager
def _exclusive_audit_append(audit_log: Path):
    """Serialize chain-link reads and appends across threads/processes."""
    audit_log = Path(audit_log)
    key = os.path.normcase(str(audit_log.resolve(strict=False)))
    held = getattr(_AUDIT_LOCK_LOCAL, "held", {})
    if key in held:
        held[key]["depth"] += 1
        try:
            yield
        finally:
            held[key]["depth"] -= 1
        return

    lock_path = _audit_append_lock_path(audit_log)
    deadline = time.monotonic() + _AUDIT_APPEND_LOCK_WAIT_SECONDS
    token: str | None = None
    while token is None and time.monotonic() < deadline:
        token = _create_owned_lock(lock_path, {"audit_log": str(audit_log)})
        if token is None:
            time.sleep(0.02)
    if token is None:
        raise TimeoutError(f"timed out waiting for audit append lock: {lock_path}")
    held = dict(held)
    held[key] = {"depth": 1, "path": lock_path, "token": token}
    _AUDIT_LOCK_LOCAL.held = held
    try:
        yield
    finally:
        owner = held.pop(key, None)
        _AUDIT_LOCK_LOCAL.held = held
        if owner:
            _release_owned_lock(owner["path"], owner["token"])


def _gate0_transaction_path(root: Path, project_id: str) -> Path:
    from .projects import project_state_dir

    return _path_inside_workspace(
        root,
        project_state_dir(root, project_id) / "transactions" / "gate0-approval.json",
    )


def _gate0_snapshot_paths(root: Path, project_id: str) -> list[Path]:
    return [
        *(entry.path for entry in resolve_gate_artifacts(root, "G0", project_id=project_id)),
        Path(root).resolve() / ".signalos" / "AUDIT_TRAIL.jsonl",
        _gate_revocations_path(root, project_id),
    ]


def _gate0_snapshot_keys(root: Path, project_id: str) -> set[str]:
    workspace = Path(root).resolve()
    return {
        _path_inside_workspace(workspace, path).relative_to(workspace).as_posix()
        for path in _gate0_snapshot_paths(workspace, project_id)
    }


def _load_gate0_transaction(root: Path, project_id: str) -> dict | None:
    path = _gate0_transaction_path(root, project_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != "signalos.gate0-approval.v1":
        raise ValueError("unsupported Gate 0 approval recovery journal")
    if value.get("project_id") != project_id:
        raise ValueError("Gate 0 approval journal project binding mismatch")
    if os.path.normcase(str(value.get("workspace") or "")) != os.path.normcase(
        str(Path(root).resolve())
    ):
        raise ValueError("Gate 0 approval journal workspace binding mismatch")
    if value.get("phase") not in {"prepared", "committed"}:
        raise ValueError("Gate 0 approval journal has an invalid phase")
    if not isinstance(value.get("snapshots"), dict):
        raise ValueError("Gate 0 approval journal has invalid snapshots")
    if set(value["snapshots"]) != _gate0_snapshot_keys(root, project_id):
        raise ValueError("Gate 0 approval journal snapshot allowlist mismatch")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", str(value.get("approval_id") or "")):
        raise ValueError("Gate 0 approval journal has an invalid approval id")
    if not re.fullmatch(r"[a-f0-9]{32}", str(value.get("transaction_id") or "")):
        raise ValueError("Gate 0 approval journal has an invalid transaction id")
    return value


def _snapshot_file(root: Path, path: Path) -> tuple[str, dict]:
    safe = _path_inside_workspace(root, path)
    rel = safe.relative_to(Path(root).resolve()).as_posix()
    if safe.exists() and not safe.is_file():
        raise ValueError(f"transaction target is not a regular file: {safe}")
    if not safe.exists():
        return rel, {"exists": False, "content_b64": ""}
    return rel, {
        "exists": True,
        "content_b64": base64.b64encode(safe.read_bytes()).decode("ascii"),
    }


def _restore_gate0_transaction(root: Path, transaction: dict) -> None:
    workspace = Path(root).resolve()
    for rel, snapshot in transaction["snapshots"].items():
        if not isinstance(rel, str) or not isinstance(snapshot, dict):
            raise ValueError("Gate 0 approval journal contains an invalid snapshot")
        path = _path_inside_workspace(workspace, workspace / Path(rel))
        if snapshot.get("exists") is True:
            try:
                content = base64.b64decode(snapshot.get("content_b64", ""), validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid transaction snapshot for {rel}") from exc
            _atomic_write_bytes(path, content)
        elif snapshot.get("exists") is False:
            if path.exists():
                if not path.is_file():
                    raise ValueError(f"cannot roll back non-file transaction target: {path}")
                path.unlink()
        else:
            raise ValueError(f"transaction snapshot lacks existence state: {rel}")


def _recover_gate0_transaction(root: Path, project_id: str) -> None:
    path = _gate0_transaction_path(root, project_id)
    transaction = _load_gate0_transaction(root, project_id)
    if not transaction:
        return
    if transaction["phase"] == "prepared":
        _restore_gate0_transaction(root, transaction)
    path.unlink()


def _audit_rows(audit_log: Path) -> list[dict]:
    try:
        lines = audit_log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _authority_row_matches(row: dict, root: Path, project_id: str) -> bool:
    return (
        row.get("action") == "authority:solo-founder-g0-declared"
        and isinstance(row.get("prev_hash"), str)
        and bool(row.get("prev_hash"))
        and isinstance(row.get("entry_hash"), str)
        and row.get("entry_hash") == _audit_entry_hash(row)
        and row.get("role") == "PO"
        and row.get("delegated_role") == "PE"
        and row.get("scope") == "G0 only"
        and row.get("via") in {"button", "chat", "simulation"}
        and row.get("consent") == SOLO_FOUNDER_GATE0_CONSENT
        and row.get("project_id") == project_id
        and os.path.normcase(str(row.get("workspace") or ""))
        == os.path.normcase(str(Path(root).resolve()))
    )


def _matching_gate0_authorities(
    audit_log: Path, root: Path, project_id: str
) -> list[dict]:
    if verify_audit_chain(audit_log):
        return []
    return [
        row for row in _audit_rows(audit_log)
        if _authority_row_matches(row, root, project_id)
    ]


def _gate0_placeholder_violations(path: Path) -> list[str]:
    """Return high-confidence unresolved markers that make G0 unsafe."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"unreadable: {exc}"]
    from .profiles.validation import find_unresolved_placeholders

    return [
        f"line {item.get('line')}: {item.get('token')!r}"
        for item in find_unresolved_placeholders(content)
        if item.get("kind") in _GATE0_BLOCKING_PLACEHOLDER_KINDS
    ]


def approve_gate0_as_solo_founder(
    root: Path,
    *,
    consent: str,
    via: str,
    expected_workspace: str,
    approval_id: str,
    project_id: str = "default",
    expected_project_id: str = "",
) -> dict:
    """Execute the desktop's explicit sole-founder G0 approval transaction.

    The browser never supplies a signer role.  This backend loads the persisted
    workspace identity, requires a PO primary identity, records the explicit
    one-person PE delegation in the tamper-evident audit chain, signs all G0
    artifacts through the PE seat (the only role authorized for every G0
    artifact), and returns success only after canonical strict validation.
    """
    from .projects import validate_project_id

    root = Path(root).resolve()
    project_id = validate_project_id(project_id)
    expected_project_id = validate_project_id(expected_project_id)
    if expected_project_id != project_id:
        raise ValueError("Gate 0 approval belongs to a different project")
    expected = str(expected_workspace or "").strip()
    if not expected:
        raise ValueError("Gate 0 approval must be bound to an expected workspace")
    if os.path.normcase(str(Path(expected).resolve())) != os.path.normcase(str(root)):
        raise ValueError("Gate 0 approval belongs to a different workspace")
    if consent != SOLO_FOUNDER_GATE0_CONSENT:
        raise ValueError(
            f"Gate 0 approval requires the exact consent: {SOLO_FOUNDER_GATE0_CONSENT!r}"
        )
    if via not in {"button", "chat", "simulation"}:
        raise ValueError(
            "Gate 0 approval source must be 'button', 'chat', or 'simulation'"
        )
    approval_id = str(approval_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", approval_id):
        raise ValueError("Gate 0 approval id is missing or invalid")

    from .product.identity import load_identity

    # Validate `.signalos` before any identity/audit/lock access can follow a
    # workspace-controlled symlink.
    _path_inside_workspace(root, root / ".signalos" / "identity.json")
    lock = _try_acquire_gate0_approval_lock(root, approval_id, project_id)
    if lock is None:
        # A racing request may have completed between the first check and lock
        # acquisition.  Return idempotent success only when strict truth agrees.
        raced = check_gate_signed_strict(root, "G0", project_id=project_id)
        audit_log = root / ".signalos" / "AUDIT_TRAIL.jsonl"
        if raced.signed and _matching_gate0_authorities(
            audit_log, root, project_id
        ):
            return {
                "signed": True,
                "already_signed": True,
                "gate": "G0",
                "approval_id": approval_id,
                "reasons": [],
            }
        return {
            "signed": False,
            "gate": "G0",
            "approval_id": approval_id,
            "reason": "another Gate 0 approval is already in progress",
            "reasons": ["approval transaction lock is held"],
        }

    audit_log = _path_inside_workspace(
        root, root / ".signalos" / "AUDIT_TRAIL.jsonl"
    )
    transaction_path = _gate0_transaction_path(root, project_id)
    try:
        with _exclusive_audit_append(audit_log):
            # Recovery and all preflight checks happen after both locks are
            # held, closing the prior check-then-sign mutation window.
            _recover_gate0_transaction(root, project_id)
            identity = load_identity(root)
            actor = str((identity or {}).get("name") or "").strip()
            primary_role = str((identity or {}).get("role") or "").strip().upper()
            if (
                not actor
                or primary_role != "PO"
                or len(actor) > 160
                or any(ord(char) < 32 or ord(char) == 127 for char in actor)
            ):
                raise ValueError(
                    "Sole-founder Gate 0 approval requires a safe persisted PO workspace identity"
                )

            entries = resolve_gate_artifacts(root, "G0", project_id=project_id)
            for entry in entries:
                _path_inside_workspace(root, entry.path)
            missing = [entry.rel_path for entry in entries if not entry.path.is_file()]
            if missing:
                return {
                    "signed": False,
                    "gate": "G0",
                    "approval_id": approval_id,
                    "reason": "required Gate 0 artifacts are missing",
                    "reasons": [f"missing: {item}" for item in missing],
                }
            not_pe_signable = [
                entry.rel_path for entry in entries if "PE" not in entry.required_roles
            ]
            if not_pe_signable:
                return {
                    "signed": False,
                    "gate": "G0",
                    "approval_id": approval_id,
                    "reason": "the sole-founder PE delegation no longer covers every G0 artifact",
                    "reasons": not_pe_signable,
                }
            placeholders: list[str] = []
            for entry in entries:
                placeholders.extend(
                    f"{entry.rel_path}: {finding}"
                    for finding in _gate0_placeholder_violations(entry.path)
                )
            if placeholders:
                return {
                    "signed": False,
                    "gate": "G0",
                    "approval_id": approval_id,
                    "reason": "Gate 0 artifacts still contain unresolved template markers",
                    "reasons": placeholders,
                }

            current = check_gate_signed_strict(root, "G0", project_id=project_id)
            authorities = _matching_gate0_authorities(audit_log, root, project_id)
            same_id_rows = [
                row for row in _audit_rows(audit_log)
                if row.get("action") == "authority:solo-founder-g0-declared"
                and row.get("approval_id") == approval_id
            ]
            if same_id_rows and not all(
                _authority_row_matches(row, root, project_id) for row in same_id_rows
            ):
                raise ValueError("Gate 0 approval id is already bound to another context")
            if current.signed and authorities:
                return {
                    "signed": True,
                    "already_signed": True,
                    "gate": "G0",
                    "approval_id": approval_id,
                    "reasons": [],
                }

            snapshot_paths = _gate0_snapshot_paths(root, project_id)
            snapshots = dict(_snapshot_file(root, path) for path in snapshot_paths)
            transaction = {
                "schema": "signalos.gate0-approval.v1",
                "phase": "prepared",
                "transaction_id": uuid.uuid4().hex,
                "approval_id": approval_id,
                "workspace": str(root),
                "project_id": project_id,
                "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "snapshots": snapshots,
            }
            _atomic_write_json(transaction_path, transaction)
            previous_transaction = getattr(_GATE0_TRANSACTION_LOCAL, "active", None)
            _GATE0_TRANSACTION_LOCAL.active = (
                os.path.normcase(str(root)),
                project_id,
                transaction["transaction_id"],
            )
            try:
                if not any(row.get("approval_id") == approval_id for row in authorities):
                    append_audit_event(
                        audit_log,
                        {
                            "actor": actor,
                            "role": primary_role,
                            "action": "authority:solo-founder-g0-declared",
                            "gate": "Gate 0",
                            "approval_id": approval_id,
                            "delegated_role": "PE",
                            "scope": "G0 only",
                            "via": via,
                            "consent": SOLO_FOUNDER_GATE0_CONSENT,
                            "workspace": str(root),
                            "project_id": project_id,
                        },
                        _capability=_GATE0_AUTHORITY_CAPABILITY,
                    )
                signed_paths: list[str] = []
                if not current.signed:
                    delegated_signer = (
                        f"{actor} (sole founder; primary PO; delegated G0 PE)"
                    )
                    signed_paths = sign_gate(
                        root,
                        "G0",
                        delegated_signer,
                        "PE",
                        "APPROVED",
                        audit_log=audit_log,
                        project_id=project_id,
                    )
                verified = check_gate_signed_strict(root, "G0", project_id=project_id)
                authority_verified = bool(
                    _matching_gate0_authorities(audit_log, root, project_id)
                )
                if not verified.signed or not authority_verified:
                    _restore_gate0_transaction(root, transaction)
                    transaction_path.unlink(missing_ok=True)
                    return {
                        "signed": False,
                        "gate": "G0",
                        "approval_id": approval_id,
                        "reason": "strict Gate 0 verification failed after signing",
                        "reasons": list(verified.reasons)
                        or ["the project-bound authority declaration was not verified"],
                    }
                transaction["phase"] = "committed"
                _atomic_write_json(transaction_path, transaction)
                transaction_path.unlink()
                return {
                    "signed": True,
                    "gate": "G0",
                    "approval_id": approval_id,
                    "signed_paths": signed_paths,
                    "reason": "",
                    "reasons": [],
                }
            except BaseException:
                _restore_gate0_transaction(root, transaction)
                transaction_path.unlink(missing_ok=True)
                raise
            finally:
                _GATE0_TRANSACTION_LOCAL.active = previous_transaction
    finally:
        _release_gate0_approval_lock(lock)


# ---------------------------------------------------------------------------
# M4: auto-push at G5 sign (audit completion plan)
# ---------------------------------------------------------------------------
#
# Once QA signs the release gate (G5), the user has explicitly approved
# the work for shipping. At that point we run `git push origin HEAD`.
# If there's no remote, or the remote points at a not-yet-created repo,
# we kick off the GitHub OAuth device flow to create the repo on
# github.com and set it as origin, then retry the push.
#
# The push step is best-effort:
#   - no .git dir / no git on PATH -> record "deferred"; never raise
#   - no remote + no OAuth client ID -> record "deferred" with reason
#   - push fails for any other reason -> record "failed" with reason
#   - everything works -> record "ok"
#
# Outcome is recorded in .signalos/AUDIT_TRAIL.jsonl with
# action="g5-push-result" and status in {ok, deferred, failed}.

def _record_g5_push_outcome(
    root: Path,
    status: str,
    reason: str = "",
    *,
    project_id: str = "default",
    proof: dict | None = None,
) -> None:
    """Append a g5-push-result row to AUDIT_TRAIL.jsonl. Silent on failure."""
    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    try:
        entry = {
            "action": "g5-push-result",
            "status": status,
            "reason": _redact_release_detail(reason),
            "project_id": project_id,
        }
        if isinstance(proof, dict):
            for key in ("remote", "remote_url_sha256", "ref", "sha", "verified"):
                if key in proof:
                    entry[key] = proof[key]
        append_audit_event(trail, entry)
    except (OSError, ValueError):
        pass


def _record_g5_seal_outcome(
    root: Path,
    status: str,
    reason: str = "",
    wave: str = "",
    sealed: int = 0,
    total: int = 0,
    project_id: str = "default",
) -> None:
    """Append a g5-seal-result row to AUDIT_TRAIL.jsonl. Silent on failure."""
    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    try:
        append_audit_event(trail, {
            "action": "g5-seal-result",
            "status": status,
            "reason": reason,
            "wave": wave,
            "sealed": sealed,
            "total": total,
            "project_id": project_id,
        })
    except OSError:
        pass


def _detect_active_wave(root: Path) -> str:
    """Best-effort detect of the active wave id from .signalos state.

    Falls back to 'unknown' when no signal can be found. The seal filename
    needs a wave id; we never block sign on this.
    """
    candidates = [
        root / ".signalos" / "worktree-state.json",
        root / ".signalos" / "active-wave.json",
    ]
    for cand in candidates:
        if not cand.is_file():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("active_wave", "wave", "current_wave"):
            value = data.get(key) if isinstance(data, dict) else None
            if value:
                return str(value)
    return "unknown"


def _auto_seal_on_g5(root: Path, *, project_id: str = "default") -> dict:
    """Create an integrity seal after a successful G5 sign. Best-effort.

    Records the outcome in AUDIT_TRAIL.jsonl with action=g5-seal-result.
    Never raises — every failure path records and returns.
    """
    try:
        from .commands.seal import create_seal
    except Exception as exc:
        reason = f"seal-import: {exc}"
        _record_g5_seal_outcome(root, "failed", reason, project_id=project_id)
        return {"status": "failed", "reason": reason}

    wave = _detect_active_wave(root)
    try:
        bundle = create_seal(root, wave, project_id=project_id)
    except Exception as exc:
        reason = f"create-error: {exc}"
        _record_g5_seal_outcome(
            root, "failed", reason, wave=wave, project_id=project_id,
        )
        return {"status": "failed", "reason": reason, "wave": wave}

    sealed = sum(1 for e in bundle.get("artifacts", []) if e.get("exists"))
    total = len(bundle.get("artifacts", []))
    from .commands.seal import seal_path
    persisted_path = seal_path(root, wave, project_id=project_id)
    seal_sha256 = hashlib.sha256(persisted_path.read_bytes()).hexdigest()
    _record_g5_seal_outcome(
        root, "ok", f"sealed {sealed}/{total} artifacts",
        wave=wave, sealed=sealed, total=total, project_id=project_id,
    )
    return {
        "status": "ok",
        "reason": f"sealed {sealed}/{total} artifacts",
        "wave": wave,
        "project_id": project_id,
        "sealed": sealed,
        "total": total,
        "path": persisted_path.relative_to(Path(root).resolve()).as_posix(),
        "sha256": seal_sha256,
    }


def _looks_like_missing_remote_repo(stderr: str) -> bool:
    """Heuristic: did `git push` fail because the GitHub repo doesn't exist?

    The two canonical signatures from `git push` against a missing
    GitHub repo are:
      - "remote: Repository not found." / "ERROR: Repository not found."
      - "fatal: repository '<url>' not found" (HTTPS)
      - "fatal: Could not read from remote repository."  (SSH or
        permissions; treated as a strong hint to fall back to OAuth)
    """
    s = (stderr or "").lower()
    return (
        "repository not found" in s
        or "could not read from remote" in s
        or "repository '" in s and "not found" in s
    )


def _record_g5_commit_outcome(
    root: Path,
    status: str,
    reason: str = "",
    *,
    project_id: str = "default",
) -> None:
    """Append a g5-commit-result row to AUDIT_TRAIL.jsonl. Silent on failure.

    Distinct `action` from the push row so callers reading g5-push-result see
    only the push outcome."""
    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    try:
        append_audit_event(trail, {
            "action": "g5-commit-result",
            "status": status,
            "reason": _redact_release_detail(reason),
            "project_id": project_id,
        })
    except OSError:
        pass


def _release_governance_paths(root: Path, project_id: str) -> list[str]:
    """Exact canonical gate artifacts allowed into the release commit."""
    from .projects import project_governance_dir

    resolved_root = Path(root).resolve()
    lexical_base = Path(project_governance_dir(resolved_root, project_id)).absolute()
    try:
        base_rel = lexical_base.relative_to(resolved_root)
    except ValueError as exc:
        raise ReleaseTreeError("project governance base escapes workspace") from exc
    paths: set[str] = set()
    for gate in list_gates():
        for artifact in expected_gate_artifacts(gate):
            # Build this lexically from the canonical namespace and validate
            # every component at the release boundary.  This mirrors the
            # hardened artifact resolver and preserves same-workspace junction
            # evidence instead of normalising it away.
            rel_posix = (base_rel / Path(artifact.rel_path)).as_posix()
            workspace_path(
                resolved_root, rel_posix, allow_leaf_symlink=False,
            )
            paths.add(rel_posix)
    return sorted(paths)


def _git_tree_paths(root: Path, treeish: str) -> set[str]:
    """Return every leaf path in a Git tree, including control-plane paths."""
    try:
        proc = subprocess.run(
            ["git", "ls-tree", "-rz", "--name-only", treeish],
            cwd=str(root), capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseTreeError(f"cannot inspect full Git tree: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace")[:300]
        raise ReleaseTreeError(detail or f"cannot inspect Git tree {treeish}")
    paths: set[str] = set()
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape").replace("\\", "/")
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise ReleaseTreeError(f"unsafe path in Git tree: {rel!r}")
        paths.add(rel)
    return paths


def _release_commit_at_head(
    root: Path, release_id: str, release_digest: str,
    project_id: str = "default",
) -> str:
    """Return HEAD only for an exact, clean, cryptographically bound receipt.

    Message trailers are labels, not proof.  Reuse additionally requires the
    commit payload tree, current payload bytes, and release-scoped Git index to
    all equal the G4 digest.  A forged trailer on an unrelated HEAD therefore
    cannot become the object that is pushed during crash recovery.
    """
    if not release_id or not release_digest:
        return ""
    try:
        proc = subprocess.run(
            ["git", "show", "-s", "--format=%H%x00%B", "HEAD"],
            cwd=str(root), capture_output=True, text=True, check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    try:
        sha, body = proc.stdout.split("\x00", 1)
    except ValueError:
        return ""
    ids = re.findall(r"^SignalOS-Release-ID:\s*(.+?)\s*$", body, re.MULTILINE)
    digests = re.findall(r"^SignalOS-Release-Tree:\s*(.+?)\s*$", body, re.MULTILINE)
    full_trees = re.findall(
        r"^SignalOS-Release-Commit-Tree:\s*(.+?)\s*$", body, re.MULTILINE,
    )
    try:
        tree_proc = subprocess.run(
            ["git", "rev-parse", f"{sha.strip()}^{{tree}}"],
            cwd=str(root), capture_output=True, text=True, check=False, timeout=15,
        )
        full_tree_oid = tree_proc.stdout.strip() if tree_proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""
    if (
        ids != [release_id]
        or digests != [release_digest]
        or not full_tree_oid
        or full_trees != [full_tree_oid]
    ):
        return ""
    try:
        governance_paths = _release_governance_paths(root, project_id)
        if tree_digest(commit_release_tree(root, sha.strip())) != release_digest:
            return ""
        if tree_digest(workspace_release_tree(root)) != release_digest:
            return ""
        if tree_digest(index_release_tree(root)) != release_digest:
            return ""
        committed_release = commit_release_tree(root, sha.strip())
        committed_control = commit_control_tree(
            root, sha.strip(), governance_paths,
        )
        if _git_tree_paths(root, sha.strip()) != (
            set(committed_release) | set(committed_control)
        ):
            return ""
        if committed_control != workspace_control_tree(root, governance_paths):
            return ""
        if committed_control != index_control_tree(root, governance_paths):
            return ""
    except (OSError, ValueError, ReleaseTreeError):
        return ""
    return sha.strip()


def _stage_and_commit_for_release(
    root: Path, *, release_id: str = "", release_digest: str = "",
    project_id: str = "default",
) -> tuple[str, str, str]:
    """Build one exact release commit through an isolated temporary index.

    Only G4-bound payload paths enter the commit. Existing staged user/control-
    plane changes are neither consumed nor reset. The resulting Git tree is
    independently re-hashed before HEAD moves, creating the backend receipt
    that later retries and pushes validate.
    """
    def _run(args: list[str], timeout: int = 60, *, data: bytes | None = None,
             env: dict[str, str] | None = None):
        return subprocess.run(
            args, cwd=str(root), input=data, capture_output=True,
            check=False, timeout=timeout, env=env,
        )

    def _detail(proc: subprocess.CompletedProcess[bytes], fallback: str) -> str:
        raw = proc.stderr or proc.stdout or fallback.encode("utf-8")
        return _redact_release_detail(
            raw.decode("utf-8", "replace").strip()
        )[:300]

    if not release_id or not release_digest:
        return (
            "verification-failed",
            "a governed release id and G4 release-tree digest are required",
            "",
        )
    existing = _release_commit_at_head(
        root, release_id, release_digest, project_id,
    )
    if existing:
        return "already-committed", "idempotent release commit reused", existing
    try:
        approved_tree = workspace_release_tree(root)
        actual_digest = tree_digest(approved_tree)
        governance_paths = _release_governance_paths(root, project_id)
        approved_control = workspace_control_tree(root, governance_paths)
        # Only canonical gate artifacts may drift under core/** after G4.
        # Refuse a late untracked/script payload instead of silently shipping it
        # as "governance" outside the approved product digest.
        current_all_control = workspace_control_tree(root)
        try:
            head_all_control = commit_control_tree(root, "HEAD")
        except ReleaseTreeError:
            head_all_control = {}
        unexpected = sorted(
            rel for rel in set(current_all_control) | set(head_all_control)
            if rel not in set(governance_paths)
            and current_all_control.get(rel) != head_all_control.get(rel)
        )
        if unexpected:
            return (
                "verification-failed",
                "unexpected non-canonical core governance drift: "
                + ", ".join(unexpected[:10]),
                "",
            )
    except (OSError, ValueError, ReleaseTreeError) as exc:
        return "verification-failed", f"cannot read approved release tree: {exc}", ""
    if actual_digest != release_digest:
        return (
            "verification-failed",
            "current release payload does not match the G4 verifier receipt",
            "",
        )

    fd, temp_name = tempfile.mkstemp(prefix="signalos-release-index-")
    os.close(fd)
    temp_index = Path(temp_name)
    try:
        # Git expects a missing path (not a zero-byte file) for a fresh index.
        temp_index.unlink(missing_ok=True)
        isolated_env = dict(os.environ)
        isolated_env["GIT_INDEX_FILE"] = str(temp_index)
        head = _run(["git", "rev-parse", "--verify", "HEAD"], timeout=15)
        parent = head.stdout.decode("ascii", "replace").strip() if head.returncode == 0 else ""
        # Start from an empty index and add the complete approved set.  Seeding
        # from HEAD silently retained tracked `.signalos/**` identity/run state
        # and non-canonical `core/**` files outside both the G4 digest and the
        # selected governance comparison.
        read = _run(["git", "read-tree", "--empty"], env=isolated_env)
        if read.returncode != 0:
            return "add-failed", _detail(read, "git read-tree failed"), ""

        paths = sorted(set(
            git_release_pathspec(root, approved_tree)
            + git_control_pathspec(root, approved_control, governance_paths)
        ))
        governance_path_set = set(governance_paths)

        def populate_index(target_env: dict[str, str] | None) -> tuple[bool, str]:
            """Write exact working bytes to an index without stat-cache trust."""
            for rel in paths:
                try:
                    path = workspace_path(
                        root,
                        rel,
                        allow_leaf_symlink=rel not in governance_path_set,
                    )
                except ReleaseTreeError as exc:
                    return False, str(exc)
                exists = path.exists() or path.is_symlink()
                if not exists:
                    remove = _run(
                        ["git", "update-index", "--force-remove", "--", rel],
                        env=target_env,
                    )
                    if remove.returncode != 0:
                        return False, _detail(remove, f"cannot remove {rel} from index")
                    continue
                try:
                    if path.is_symlink():
                        payload = os.readlink(path).encode(
                            "utf-8", errors="surrogatepass",
                        )
                        mode = "120000"
                    else:
                        payload = path.read_bytes()
                        mode_probe = _run(["git", "ls-files", "-s", "--", rel])
                        mode_fields = (
                            mode_probe.stdout.split(maxsplit=1)
                            if mode_probe.returncode == 0 else []
                        )
                        first = mode_fields[0] if mode_fields else b""
                        mode = (
                            first.decode("ascii")
                            if first in {b"100644", b"100755"}
                            else "100644"
                        )
                except OSError as exc:
                    return False, f"cannot read {rel}: {exc}"
                blob = _run(["git", "hash-object", "-w", "--stdin"], data=payload)
                if blob.returncode != 0:
                    return False, _detail(blob, f"cannot hash {rel}")
                oid = blob.stdout.decode("ascii", "replace").strip()
                update = _run([
                    "git", "update-index", "--add", "--cacheinfo",
                    f"{mode},{oid},{rel}",
                ], env=target_env)
                if update.returncode != 0:
                    return False, _detail(update, f"cannot stage {rel}")
            return True, ""

        populated, populate_reason = populate_index(isolated_env)
        if not populated:
            return "add-failed", populate_reason, ""
        write = _run(["git", "write-tree"], env=isolated_env)
        if write.returncode != 0:
            return "commit-failed", _detail(write, "git write-tree failed"), ""
        tree_oid = write.stdout.decode("ascii", "replace").strip()
        try:
            staged_digest = tree_digest(commit_release_tree(root, tree_oid))
            staged_control = commit_control_tree(root, tree_oid, governance_paths)
        except (OSError, ValueError, ReleaseTreeError) as exc:
            return "verification-failed", f"cannot verify staged release tree: {exc}", ""
        if staged_digest != release_digest:
            return (
                "verification-failed",
                "isolated Git release tree does not match the G4 verifier receipt "
                f"(expected {release_digest}, got {staged_digest}; "
                f"approved paths={sorted(approved_tree)}, "
                f"staged paths={sorted(commit_release_tree(root, tree_oid))})",
                "",
            )
        if staged_control != approved_control:
            return (
                "verification-failed",
                "isolated Git governance tree does not match current signed evidence",
                "",
            )
        expected_paths = set(approved_tree) | set(approved_control)
        staged_paths = _git_tree_paths(root, tree_oid)
        if staged_paths != expected_paths:
            extras = sorted(staged_paths - expected_paths)
            missing = sorted(expected_paths - staged_paths)
            return (
                "verification-failed",
                "isolated Git tree contains paths outside the exact approved set "
                f"(extra={extras[:10]}, missing={missing[:10]})",
                "",
            )

        msg = ("chore(release): ship G5-approved product\n\n"
               "Auto-committed by SignalOS Foundry at G5 sign so the release push "
               "carries the exact verified product bytes and signed gate evidence."
               f"\n\nSignalOS-Release-ID: {release_id}"
               f"\nSignalOS-Release-Tree: {release_digest}"
               f"\nSignalOS-Release-Commit-Tree: {tree_oid}\n")

        identity_env = dict(os.environ)
        identity_env.setdefault("GIT_AUTHOR_NAME", "SignalOS Foundry")
        identity_env.setdefault("GIT_AUTHOR_EMAIL", "foundry@signalos.local")
        identity_env.setdefault("GIT_COMMITTER_NAME", "SignalOS Foundry")
        identity_env.setdefault("GIT_COMMITTER_EMAIL", "foundry@signalos.local")
        commit_args = ["git", "commit-tree", tree_oid]
        if parent:
            commit_args.extend(["-p", parent])
        commit = _run(commit_args, data=msg.encode("utf-8"), env=identity_env)
        if commit.returncode != 0:
            return "commit-failed", _detail(commit, "git commit-tree failed"), ""
        sha = commit.stdout.decode("ascii", "replace").strip()
        try:
            committed_digest = tree_digest(commit_release_tree(root, sha))
            committed_control = commit_control_tree(root, sha, governance_paths)
        except (OSError, ValueError, ReleaseTreeError) as exc:
            return "verification-failed", f"cannot verify release commit: {exc}", ""
        if committed_digest != release_digest:
            return "verification-failed", "release commit tree digest mismatch", ""
        if committed_control != approved_control:
            return "verification-failed", "release governance tree mismatch", ""
        if _git_tree_paths(root, sha) != (set(approved_tree) | set(approved_control)):
            return "verification-failed", "release commit contains unapproved paths", ""

        update_args = ["git", "update-ref", "HEAD", sha]
        if parent:
            update_args.append(parent)
        update = _run(update_args)
        if update.returncode != 0:
            return "commit-failed", _detail(update, "git update-ref failed"), ""

        # Reconcile only payload entries in the caller's real index. This leaves
        # any staged SignalOS governance work untouched and makes safe retries
        # prove a clean release-scoped index against the new HEAD.
        synced, sync_reason = populate_index(None)
        if not synced:
            return "commit-failed", f"release index sync failed: {sync_reason}", sha
        receipt = _release_commit_at_head(
            root, release_id, release_digest, project_id,
        )
        if receipt != sha:
            return "verification-failed", "release backend receipt validation failed", sha
        return "committed", "", sha
    except (OSError, subprocess.SubprocessError, ReleaseTreeError) as exc:
        return "commit-failed", _redact_release_detail(f"subprocess-error: {exc}"), ""
    finally:
        try:
            temp_index.unlink(missing_ok=True)
        except OSError:
            pass


def _auto_push_on_g5(
    root: Path, *, release_id: str = "", release_digest: str = "",
    project_id: str = "default",
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """Commit the generated product, then run `git push origin HEAD` after a G5
    sign. Best-effort.

    A G5 sign means the founder approved the work for shipping, so the built
    product must actually be committed BEFORE the push -- previously this ran a
    bare `git push origin HEAD` with no `git add`/`git commit`, shipping zero
    product bytes whenever the walk had not already committed them.

    On success: record status=ok.
    On no-remote + no SIGNALOS_GH_CLIENT_ID: record status=deferred with
    a clear reason so the operator knows what to do.
    On remote-doesn't-exist + SIGNALOS_GH_CLIENT_ID set: kick off the
    OAuth device flow to create the repo, set it as origin, retry push.
    On any other failure: record status=failed with stderr excerpt.
    """
    import os

    cancelled = cancel_check or (lambda: False)
    if cancelled():
        reason = "cancelled before release commit"
        return {
            "commit": {"status": "cancelled", "reason": reason},
            "push": {"status": "deferred", "reason": reason},
        }

    # No .git -> uninitialized workspace; auto-push isn't meaningful.
    if not (root / ".git").exists():
        reason = "no-git-dir"
        _record_g5_push_outcome(root, "deferred", reason, project_id=project_id)
        return {
            "commit": {"status": "deferred", "reason": reason},
            "push": {"status": "deferred", "reason": reason},
        }

    # Ship real bytes: stage + commit the generated product BEFORE pushing.
    commit_outcome, commit_detail, commit_sha = _stage_and_commit_for_release(
        root, release_id=release_id, release_digest=release_digest,
        project_id=project_id,
    )
    commit_detail = _redact_release_detail(commit_detail)
    if commit_outcome != "already-committed":
        _record_g5_commit_outcome(
            root, commit_outcome, commit_detail, project_id=project_id,
        )
    commit = {
        "status": commit_outcome,
        "reason": commit_detail,
        "sha": commit_sha,
    }
    # Shipping after a failed add/commit can push an old HEAD containing none of
    # the approved product bytes. Fail closed until the release tree is either
    # committed now or was already clean/committed.
    if commit_outcome not in {"committed", "already-committed"}:
        reason = f"release {commit_outcome}: {commit_detail}".rstrip(": ")
        _record_g5_push_outcome(root, "failed", reason, project_id=project_id)
        return {
            "commit": commit,
            "push": {"status": "failed", "reason": reason},
        }
    receipt = _release_commit_at_head(
        root, release_id, release_digest, project_id,
    )
    if not commit_sha or receipt != commit_sha:
        reason = "release backend receipt no longer matches HEAD/current payload"
        _record_g5_push_outcome(root, "failed", reason, project_id=project_id)
        return {
            "commit": commit,
            "push": {"status": "failed", "reason": reason},
        }
    if cancelled():
        reason = "cancelled after release commit and before push"
        _record_g5_push_outcome(root, "deferred", reason, project_id=project_id)
        return {
            "commit": commit,
            "push": {"status": "cancelled", "reason": reason},
        }

    # Look up origin via the helper in git_remote so tests can monkeypatch
    # a single seam.
    try:
        from .git_remote import ensure_github_remote
    except Exception as exc:
        reason = _redact_release_detail(f"git_remote-import: {exc}")
        _record_g5_push_outcome(root, "failed", reason, project_id=project_id)
        return {"commit": commit, "push": {"status": "failed", "reason": reason}}

    try:
        remote_url = ensure_github_remote(root)
    except Exception as exc:
        reason = _redact_release_detail(f"remote-lookup: {exc}")
        _record_g5_push_outcome(root, "failed", reason, project_id=project_id)
        return {"commit": commit, "push": {"status": "failed", "reason": reason}}

    def _try_push(remote_label: str) -> tuple[dict, str]:
        if cancelled():
            reason = "cancelled before push"
            return {"status": "cancelled", "reason": reason}, reason
        try:
            branch = subprocess.run(
                ["git", "symbolic-ref", "--quiet", "HEAD"],
                cwd=str(root), capture_output=True, text=True, check=False,
                timeout=15,
            )
            destination = branch.stdout.strip() if branch.returncode == 0 else ""
            if not destination.startswith("refs/heads/"):
                reason = "detached HEAD has no safe remote branch destination"
                return {"status": "failed", "reason": reason}, reason
            proc = subprocess.run(
                ["git", "push", "origin", f"{commit_sha}:{destination}"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            reason = _redact_release_detail(f"subprocess-error: {exc}")
            return {"status": "failed", "reason": reason}, reason
        if proc.returncode != 0:
            reason = _redact_release_detail(
                (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
            )
            return {"status": "failed", "reason": reason}, reason

        # A zero exit from `git push` is not enough evidence for a release
        # receipt. Read the exact destination ref back from origin and bind the
        # durable outcome to the commit we just proved locally.
        try:
            remote = subprocess.run(
                ["git", "ls-remote", "--exit-code", "origin", destination],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            reason = _redact_release_detail(f"remote verification failed: {exc}")
            return {"status": "failed", "reason": reason}, reason
        remote_rows = []
        if remote.returncode == 0:
            for line in (remote.stdout or "").splitlines():
                fields = line.split()
                if len(fields) == 2 and fields[1] == destination:
                    remote_rows.append(fields[0])
        if remote_rows != [commit_sha]:
            detail = _redact_release_detail(
                (remote.stderr or remote.stdout or "remote ref missing").strip()
            )
            reason = (
                "remote verification did not return the pushed commit "
                f"for {destination}: {detail[:250]}"
            )
            return {"status": "failed", "reason": reason}, reason
        proof = {
            "status": "ok",
            "reason": "pushed and read back from origin",
            "remote": "origin",
            "remote_url_sha256": hashlib.sha256(
                str(remote_label or "origin").encode("utf-8")
            ).hexdigest(),
            "ref": destination,
            "sha": commit_sha,
            "verified": True,
        }
        return proof, ""

    def _attempt_oauth_create_and_push(reason: str) -> dict:
        """OAuth path: create the repo on GitHub, set as origin, push."""
        if not os.environ.get("SIGNALOS_GH_CLIENT_ID", "").strip():
            detail = (
                f"{reason}; SIGNALOS_GH_CLIENT_ID not set "
                f"(see docs/SIGNALOS_GITHUB_OAUTH_SETUP.md)"
            )
            _record_g5_push_outcome(
                root,
                "deferred",
                detail,
                project_id=project_id,
            )
            return {"status": "deferred", "reason": detail}
        try:
            from .git_remote import create_github_repo_via_oauth
        except Exception as exc:
            detail = _redact_release_detail(f"git_remote-import: {exc}")
            _record_g5_push_outcome(
                root, "failed", detail, project_id=project_id,
            )
            return {"status": "failed", "reason": detail}
        repo_name = root.name or "signalos-workspace"
        try:
            clone_url = create_github_repo_via_oauth(repo_name, private=True)
        except RuntimeError as exc:
            detail = _redact_release_detail(f"oauth-failed: {exc}")
            _record_g5_push_outcome(
                root, "deferred", detail, project_id=project_id,
            )
            return {"status": "deferred", "reason": detail}
        except Exception as exc:
            detail = _redact_release_detail(f"oauth-error: {exc}")
            _record_g5_push_outcome(
                root, "failed", detail, project_id=project_id,
            )
            return {"status": "failed", "reason": detail}
        # Wire the new remote and retry push.
        try:
            if remote_url is None:
                configured = subprocess.run(
                    ["git", "remote", "add", "origin", clone_url],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
            else:
                configured = subprocess.run(
                    ["git", "remote", "set-url", "origin", clone_url],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
            if configured.returncode != 0:
                detail = (
                    configured.stderr or configured.stdout
                    or f"exit {configured.returncode}"
                ).strip()
                raise RuntimeError(detail)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            detail = _redact_release_detail(f"remote-set-error: {exc}")
            _record_g5_push_outcome(
                root, "failed", detail, project_id=project_id,
            )
            return {"status": "failed", "reason": detail}
        push, msg = _try_push(clone_url)
        if push.get("status") == "ok":
            push = {**push, "reason": "created repository, pushed, and read back from origin"}
            _record_g5_push_outcome(
                root, "ok", str(push["reason"]), project_id=project_id, proof=push,
            )
            return push
        if push.get("status") == "cancelled":
            return push
        detail = f"post-create push failed: {msg[:300]}"
        _record_g5_push_outcome(root, "failed", detail, project_id=project_id)
        return {"status": "failed", "reason": detail}

    if remote_url is None:
        # No origin configured — go straight to OAuth (or defer).
        push = _attempt_oauth_create_and_push("no-origin-remote")
        return {"commit": commit, "push": push}

    push, msg = _try_push(remote_url)
    if push.get("status") == "ok":
        _record_g5_push_outcome(
            root, "ok", str(push.get("reason") or ""),
            project_id=project_id, proof=push,
        )
        return {"commit": commit, "push": push}
    if push.get("status") == "cancelled":
        return {"commit": commit, "push": push}

    if _looks_like_missing_remote_repo(msg):
        push = _attempt_oauth_create_and_push(f"remote-missing: {msg[:200]}")
        return {"commit": commit, "push": push}

    reason = msg[:500]
    _record_g5_push_outcome(root, "failed", reason, project_id=project_id)
    return {"commit": commit, "push": {"status": "failed", "reason": reason}}


def append_audit_event(
    audit_log: Path,
    entry: dict,
    *,
    _capability: object | None = None,
) -> dict:
    """Append one generic event to the same tamper-evident audit chain as signs."""
    if not isinstance(entry, dict) or not str(entry.get("action") or "").strip():
        raise ValueError("audit event requires a non-empty action")
    if (
        str(entry.get("action")) in _RESERVED_AUDIT_ACTIONS
        and _capability is not _GATE0_AUTHORITY_CAPABILITY
    ):
        raise ValueError(
            "reserved Gate 0 authority events can only be minted by the explicit approval transaction"
        )
    audit_log = Path(audit_log)
    # Every canonical backend audit writer converges here.  Protect the shared
    # authority trail even when the caller is not sign_gate (release, reopen,
    # cancellation, and Gate 0 transactions all append directly).
    if audit_log.name == "AUDIT_TRAIL.jsonl" and audit_log.parent.name == ".signalos":
        audit_log = _path_inside_workspace(audit_log.parent.parent, audit_log)
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_audit_append(audit_log):
        row = {
            **entry,
            # The backend owns event time and chain fields; callers cannot forge
            # either by placing them in the supplied payload.
            "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        row.pop("prev_hash", None)
        row.pop("entry_hash", None)
        row["prev_hash"] = _audit_prev_link(audit_log)
        row["entry_hash"] = _audit_entry_hash(row)
        with audit_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return row


def _append_audit(
    audit_log: Path,
    signer: str,
    role: str,
    gate: str,
    rel_path: str,
    artifact_path: Path,
    verdict: str,
    wave: str | None = None,
    project_id: str = "default",
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
        # AUDIT_TRAIL.jsonl is workspace-global.  Bind every new sign row to
        # its virtual project so canonical artifact paths and identical bytes
        # cannot be replayed across project namespaces.
        "project_id": project_id,
    }
    normalized_wave = _normalize_wave(wave)
    if normalized_wave:
        row["wave"] = normalized_wave
    # Tamper-evidence (Wave 0.2): forward-link this row to the prior row's
    # hash, then hash this row. Editing/inserting/deleting any chained row
    # breaks the chain and is caught by verify_audit_chain().
    append_audit_event(audit_log, row)


def _audit_entry_hash(row: dict) -> str:
    """Deterministic SHA-256 of an audit row, excluding its own entry_hash."""
    payload = {k: v for k, v in row.items() if k != "entry_hash"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _audit_prev_link(audit_log: Path) -> str:
    """Chain link for the next entry: the last row's entry_hash if it has one,
    else a hash of the last raw line (so un-chained rows are still committed to),
    else GENESIS for an empty/absent log."""
    try:
        lines = [l for l in audit_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return "GENESIS"
    if not lines:
        return "GENESIS"
    last = lines[-1]
    try:
        parsed = json.loads(last)
        if isinstance(parsed, dict) and parsed.get("entry_hash"):
            return str(parsed["entry_hash"])
    except json.JSONDecodeError:
        pass
    return hashlib.sha256(last.encode("utf-8")).hexdigest()


def verify_audit_chain(audit_log: Path) -> list[str]:
    """Verify the tamper-evident hash chain of an AUDIT_TRAIL.jsonl file.

    Returns a list of human-readable violation strings; an empty list means the
    chain is intact. Rows without an ``entry_hash`` (written by appenders that do
    not yet chain) are treated as un-chained boundaries: they are not hash-checked
    themselves, but a following chained row commits to their exact bytes, so an
    insertion or deletion across them is still detected.
    """
    try:
        raw_lines = [l for l in audit_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []
    violations: list[str] = []
    prev_link = "GENESIS"
    for idx, line in enumerate(raw_lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            violations.append(f"line {idx}: not valid JSON")
            prev_link = hashlib.sha256(line.encode("utf-8")).hexdigest()
            continue
        entry_hash = row.get("entry_hash") if isinstance(row, dict) else None
        if entry_hash:
            if _audit_entry_hash(row) != entry_hash:
                violations.append(f"line {idx}: entry_hash mismatch (row edited in place)")
            if row.get("prev_hash") != prev_link:
                violations.append(f"line {idx}: prev_hash breaks the chain (insertion/deletion/reorder)")
            prev_link = str(entry_hash)
        else:
            prev_link = hashlib.sha256(line.encode("utf-8")).hexdigest()
    return violations


def _normalize_wave(value: str | int | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().upper()
    if not raw:
        return None
    raw = raw.removeprefix("W").strip()
    if raw.isdigit():
        return f"{int(raw):02d}"
    return raw
