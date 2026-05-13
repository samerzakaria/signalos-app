# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/diagnose.py
# W3.5 — Operator diagnose bundle builder (AMD-CORE-018)
#
# Emits a structured JSON bundle suitable for pasting into a bug report.

from __future__ import annotations

__all__ = [
    "build_diagnose",
    "AUDIT_TRAIL_TAIL",
]

import json
import time
from pathlib import Path
from typing import Optional

# How many audit trail entries to include in the bundle
AUDIT_TRAIL_TAIL: int = 5


def build_diagnose(
    repo_root: Optional[Path] = None,
    wave: Optional[str] = None,
) -> dict:
    """Return a structured diagnose bundle dict.

    Keys:
    - generated_at:  ISO-8601 timestamp
    - repo_root:     absolute path
    - wave:          wave filter (if given)
    - daemon_state:  dict from .signalos/daemon-state.json (or null)
    - audit_trail:   last AUDIT_TRAIL_TAIL entries from AUDIT_TRAIL.jsonl
    - worktrees:     list of worktree state dicts
    - gate_status:   dict gate → {signed: bool, artifact: path}
    - pending_t2:    list of pending T2 pause records
    """
    if repo_root is None:
        repo_root = Path.cwd()
    repo_root = Path(repo_root).resolve()

    import datetime
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    bundle: dict = {
        "generated_at": now,
        "repo_root": str(repo_root),
        "wave": wave,
        "daemon_state": _read_daemon_state(repo_root),
        "audit_trail": _read_audit_trail(repo_root, wave),
        "worktrees": _read_worktrees(repo_root),
        "gate_status": _read_gate_status(repo_root),
        "pending_t2": _read_pending_t2(repo_root),
    }
    return bundle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_daemon_state(repo_root: Path) -> Optional[dict]:
    p = repo_root / ".signalos" / "daemon-state.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "could not parse daemon-state.json"}


def _read_audit_trail(repo_root: Path, wave: Optional[str]) -> list:
    p = repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not p.exists():
        return []
    entries = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if wave and obj.get("wave") != wave:
                continue
            entries.append(obj)
    except OSError:
        return []
    return entries[-AUDIT_TRAIL_TAIL:]


def _read_worktrees(repo_root: Path) -> list:
    wt_dir = repo_root / ".signalos" / "worktrees"
    if not wt_dir.exists():
        return []
    results = []
    for f in sorted(wt_dir.glob("*.json")):
        try:
            results.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            results.append({"file": f.name, "error": "parse error"})
    return results


def _read_gate_status(repo_root: Path) -> dict:
    gates = {}
    for gate in ("G0", "G1", "G2", "G3", "G4", "G5"):
        # Standard gate artifact paths
        candidates = [
            repo_root / "core" / "governance" / f"{gate}-gate.md",
            repo_root / "core" / "governance" / f"Gate-{gate}.md",
            repo_root / f"{gate}-gate.md",
        ]
        artifact = None
        for c in candidates:
            if c.exists():
                artifact = str(c.relative_to(repo_root))
                break
        signed = False
        if artifact:
            try:
                text = (repo_root / artifact).read_text(encoding="utf-8")
                signed = "## Signatures" in text
            except OSError:
                pass
        gates[gate] = {"artifact": artifact, "signed": signed}
    return gates


def _read_pending_t2(repo_root: Path) -> list:
    pauses_dir = repo_root / ".signalos" / "pauses"
    if not pauses_dir.exists():
        return []
    results = []
    for f in sorted(pauses_dir.glob("*.json")):
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
            if obj.get("tier") == "T2" and obj.get("status") in ("pending", "waiting"):
                results.append(obj)
        except Exception:
            pass
    return results
