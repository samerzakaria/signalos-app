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

from .artifacts import GATE_LABELS, GATE_MAP, list_gates, resolve_gate_artifacts

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
    result: list[ArtifactStatus] = []
    for artifact in resolve_gate_artifacts(root, gate):
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

    gate_entries = resolve_gate_artifacts(root, gate)
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

    signed: list[str] = []
    for artifact in gate_entries:
        p = artifact.path
        if not p.exists():
            continue
        if role not in artifact.required_roles:
            raise ValueError(
                f"role {role!r} is not authorised to sign {artifact.rel_path!r} "
                f"(required: {list(artifact.required_roles)})"
            )
        sign_artifact(p, signer, role, gate, verdict, conditions)
        if audit_log is not None:
            _append_audit(audit_log, signer, role, gate, artifact.rel_path, p, verdict)
        signed.append(artifact.rel_path)

    # M4: after a successful G5 sign, push the local commits to origin
    # so the user actually ships their work. Best-effort — a push
    # failure (network blip, no remote, no OAuth client ID) records a
    # deferred / failed outcome but does NOT undo the gate signature.
    if signed and gate.upper() == "G5":
        try:
            _auto_push_on_g5(root)
        except Exception as exc:
            _record_g5_push_outcome(root, "failed", f"unhandled: {exc}")

    return signed


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

def _record_g5_push_outcome(root: Path, status: str, reason: str = "") -> None:
    """Append a g5-push-result row to AUDIT_TRAIL.jsonl. Silent on failure."""
    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    try:
        trail.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": "g5-push-result",
            "status": status,
            "reason": reason,
        }
        with trail.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


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


def _auto_push_on_g5(root: Path) -> None:
    """Run `git push origin HEAD` after a G5 sign. Best-effort.

    On success: record status=ok.
    On no-remote + no SIGNALOS_GH_CLIENT_ID: record status=deferred with
    a clear reason so the operator knows what to do.
    On remote-doesn't-exist + SIGNALOS_GH_CLIENT_ID set: kick off the
    OAuth device flow to create the repo, set it as origin, retry push.
    On any other failure: record status=failed with stderr excerpt.
    """
    import os

    # No .git -> uninitialized workspace; auto-push isn't meaningful.
    if not (root / ".git").exists():
        _record_g5_push_outcome(root, "deferred", "no-git-dir")
        return

    # Look up origin via the helper in git_remote so tests can monkeypatch
    # a single seam.
    try:
        from .git_remote import ensure_github_remote
    except Exception as exc:
        _record_g5_push_outcome(root, "failed", f"git_remote-import: {exc}")
        return

    remote_url = ensure_github_remote(root)

    def _try_push() -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                ["git", "push", "origin", "HEAD"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"subprocess-error: {exc}"
        if proc.returncode == 0:
            return True, ""
        return False, (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()

    def _attempt_oauth_create_and_push(reason: str) -> None:
        """OAuth path: create the repo on GitHub, set as origin, push."""
        if not os.environ.get("SIGNALOS_GH_CLIENT_ID", "").strip():
            _record_g5_push_outcome(
                root,
                "deferred",
                f"{reason}; SIGNALOS_GH_CLIENT_ID not set "
                f"(see docs/SIGNALOS_GITHUB_OAUTH_SETUP.md)",
            )
            return
        try:
            from .git_remote import create_github_repo_via_oauth
        except Exception as exc:
            _record_g5_push_outcome(root, "failed", f"git_remote-import: {exc}")
            return
        repo_name = root.name or "signalos-workspace"
        try:
            clone_url = create_github_repo_via_oauth(repo_name, private=True)
        except RuntimeError as exc:
            _record_g5_push_outcome(root, "deferred", f"oauth-failed: {exc}")
            return
        except Exception as exc:
            _record_g5_push_outcome(root, "failed", f"oauth-error: {exc}")
            return
        # Wire the new remote and retry push.
        try:
            if remote_url is None:
                subprocess.run(
                    ["git", "remote", "add", "origin", clone_url],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
            else:
                subprocess.run(
                    ["git", "remote", "set-url", "origin", clone_url],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            _record_g5_push_outcome(root, "failed", f"remote-set-error: {exc}")
            return
        ok, msg = _try_push()
        if ok:
            _record_g5_push_outcome(root, "ok", f"created repo + pushed ({clone_url})")
        else:
            _record_g5_push_outcome(root, "failed", f"post-create push failed: {msg[:300]}")

    if remote_url is None:
        # No origin configured — go straight to OAuth (or defer).
        _attempt_oauth_create_and_push("no-origin-remote")
        return

    ok, msg = _try_push()
    if ok:
        _record_g5_push_outcome(root, "ok", f"pushed to {remote_url}")
        return

    if _looks_like_missing_remote_repo(msg):
        _attempt_oauth_create_and_push(f"remote-missing: {msg[:200]}")
        return

    _record_g5_push_outcome(root, "failed", msg[:500])


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
