# SignalOS Core v1.1 — Step-pause controller (opt-in, per-step).
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
#
# Design intent:
#   - pause is OPT-IN: a PLAN step-spec sets `pause: true` to request a halt
#     between Gate 3 and Gate 4. Unlike babysitter's "breakpoint by default,
#     /yolo to skip", Core's default is NO pause.
#   - the `step-started` hook sources core/execution/hooks/_lib/step-pause-check.sh
#     which reads the pause flag. If set, it writes a pending-pause file
#     (.signalos/sessions/<sid>/pauses/<step-id>.json) and exits 2. The
#     step is unblocked when `.<step-id>.resume` appears next to it.
#   - T3 steps REFUSE to pause — the shell hook emits step.aborted with
#     cause "t3-refuses-pause" and this module declines to write a .resume
#     marker on top of a .abort marker.
#
# Disk-truth contract:
#   - Journal writes go through core/execution/hooks/_lib/journal-append.sh
#     (never written directly from Python).
#   - Marker files (.json pending, .resume, .abort) are tiny JSON blobs
#     written atomically via `os.replace(tmp, final)`.


from __future__ import annotations

__all__ = ["PauseController", "pause_step", "resume_step", "is_paused"]  # W-2: explicit public API

import getpass
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .session import REPO_ROOT_MARKER, repo_root, sessions_dir


_HOOK_LIB_REL = Path("core/execution/hooks/_lib")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_root(root: Path | None) -> Path:
    return root if root is not None else repo_root()


def pauses_dir(session_id: str, root: Path | None = None) -> Path:
    return sessions_dir(_resolve_root(root)) / session_id / "pauses"


def _atomic_write_json(target: Path, payload: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}.{int(time.time()*1000)}")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, target)


def _find_journal_append() -> Path:
    """Locate journal-append.sh.

    Resolution order (the helper lives inside the Core install, not in the
    adopter's working tree):
      1. $SIGNALOS_HOOK_LIB / journal-append.sh (explicit override)
      2. climb up from this file until we hit .../core/execution/hooks/_lib/
    """
    override = os.environ.get("SIGNALOS_HOOK_LIB")
    if override:
        cand = Path(override) / "journal-append.sh"
        if cand.exists():
            return cand
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "core" / "execution" / "hooks" / "_lib" / "journal-append.sh"
        if cand.exists():
            return cand
    raise RuntimeError("journal-append.sh not found (install layout broken)")


def _append_event(session_id: str, event: dict, root: Path) -> None:
    """Append via the shared journal-append.sh helper (never write directly).

    `root` is the adopter's repo (where `.signalos/` lives); the helper
    respects that via the `SIGNALOS_REPO_ROOT` env var below.
    """
    script = _find_journal_append()
    payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
    # journal-append.sh resolves repo root via `git rev-parse --show-toplevel
    # || pwd`; run it with cwd=root so the fallback targets our adopter tree.
    subprocess.run(
        ["bash", str(script), "--session-id", session_id, "--event", payload],
        check=True,
        cwd=str(root),
    )


def _find_step(step_id: str, session_id: str | None, root: Path) -> tuple[str, Path]:
    """Locate the session directory holding the pending pause for step_id.

    Returns (session_id, pauses_dir_path). Raises FileNotFoundError if absent.
    """
    if session_id is not None:
        pdir = pauses_dir(session_id, root)
        if (pdir / f"{step_id}.json").exists():
            return session_id, pdir
        raise FileNotFoundError(f"no pending pause for step {step_id} in session {session_id}")

    # Scan every session for a pending pause with this step id.
    sroot = sessions_dir(root)
    if sroot.exists():
        for sess in sorted(p for p in sroot.iterdir() if p.is_dir()):
            cand = sess / "pauses" / f"{step_id}.json"
            if cand.exists():
                return sess.name, sess / "pauses"
    raise FileNotFoundError(f"no pending pause for step {step_id}")


def list_paused(root: Path | None = None) -> list[dict]:
    """Return every pending pause (pause file exists, no .resume/.abort sibling)."""
    root = _resolve_root(root)
    sroot = sessions_dir(root)
    out: list[dict] = []
    if not sroot.exists():
        return out
    for sess in sorted(p for p in sroot.iterdir() if p.is_dir()):
        pdir = sess / "pauses"
        if not pdir.is_dir():
            continue
        for pfile in sorted(pdir.glob("*.json")):
            step_id = pfile.stem
            if (pdir / f"{step_id}.resume").exists():
                continue
            if (pdir / f"{step_id}.abort").exists():
                continue
            try:
                rec = json.loads(pfile.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                rec = {}
            rec.setdefault("session_id", sess.name)
            rec.setdefault("step_id", step_id)
            out.append(rec)
    return out


def _resolve_marker(
    step_id: str,
    rationale: str,
    marker_suffix: str,
    event_type: str,
    event_extra: dict | None,
    session_id: str | None,
    root: Path | None,
) -> dict:
    if not rationale or not rationale.strip():
        raise ValueError("rationale is required (empty rationale is refused)")

    root = _resolve_root(root)
    sid, pdir = _find_step(step_id, session_id, root)

    # If the step was aborted (including T3 hard-stop), refuse to resume it.
    if marker_suffix == ".resume" and (pdir / f"{step_id}.abort").exists():
        raise PermissionError(
            f"step {step_id} was aborted; cannot resume an aborted step"
        )

    marker = pdir / f"{step_id}{marker_suffix}"
    payload = {
        "resolved_at" if marker_suffix != ".resume" else "resumed_at": _now_iso(),
        "rationale": rationale.strip(),
        "user": os.environ.get("SIGNALOS_USER") or getpass.getuser(),
        "step_id": step_id,
        "session_id": sid,
    }
    # Keep both keys readable regardless of marker kind.
    if marker_suffix == ".resume":
        payload["resumed_at"] = payload.pop("resolved_at", _now_iso())
    else:
        payload["aborted_at"] = payload.pop("resolved_at", _now_iso())
    _atomic_write_json(marker, payload)

    event: dict = {
        "schema_version": 1,
        "ts": _now_iso(),
        "type": event_type,
        "step_id": step_id,
        "rationale": rationale.strip(),
    }
    if event_extra:
        event.update(event_extra)
    _append_event(sid, event, root)

    return {"session_id": sid, "step_id": step_id, "marker": str(marker), "event": event}


def resume(
    step_id: str,
    rationale: str,
    session_id: str | None = None,
    root: Path | None = None,
) -> dict:
    """Write a `.resume` marker and append `step.resumed` to the journal."""
    return _resolve_marker(
        step_id=step_id,
        rationale=rationale,
        marker_suffix=".resume",
        event_type="step.resumed",
        event_extra=None,
        session_id=session_id,
        root=root,
    )


def abort(
    step_id: str,
    rationale: str,
    session_id: str | None = None,
    root: Path | None = None,
) -> dict:
    """Write an `.abort` marker and append `step.aborted {cause:"manual-abort"}`."""
    return _resolve_marker(
        step_id=step_id,
        rationale=rationale,
        marker_suffix=".abort",
        event_type="step.aborted",
        event_extra={"cause": "manual-abort"},
        session_id=session_id,
        root=root,
    )
