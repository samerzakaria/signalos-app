# SignalOS Core v2.1 — Wave status card (AMD-CORE-008).
#
# Renders a structured ASCII status card for the current Wave, showing:
#   - Wave ID and current delivery phase
#   - First line of the problem statement from BELIEF.md
#   - Scale track and delivery mode
#   - Gate status (G0–G5)
#   - Active tasks from .signalos/worktree-state.json
#   - Next blocking action
#
# Public API:
#   get_wave_status(repo_root: Path) -> dict
#   render_status_card(status: dict) -> str
#   print_status_card(repo_root: Path | None = None) -> None


from __future__ import annotations

__all__ = ["get_wave_status", "print_status_card", "watch_status", "_format_elapsed"]  # W-2/W3.2

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from signalos_lib.ide import detect_ide

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT_MARKER = ".signalos"
CARD_WIDTH = 62  # inner width (between the ║ chars)

# Phase names keyed by the highest open gate
_PHASE_NAMES = {
    0: "ONBOARDING",
    1: "BELIEF",
    2: "PLANNING",
    3: "DESIGN",
    4: "BUILD",
    5: "REVIEW",
    6: "DONE",
}


# ---------------------------------------------------------------------------
# Repo root helper
# ---------------------------------------------------------------------------

def _repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / REPO_ROOT_MARKER).is_dir():
            return cand
    return p  # fallback: return cwd (status card still renders partial data)


# ---------------------------------------------------------------------------
# Gate detection helpers
# ---------------------------------------------------------------------------

def _is_non_template(path: Path) -> bool:
    """Return True if the file exists and has some non-template content."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    # Template markers found in SignalOS scaffold files
    template_markers = [
        "<!-- Template",
        "# Template",
        "YYYY-MM-DD",
        "{product-name}",
        "<!-- PLACEHOLDER",
    ]
    # Non-template: file exists and doesn't look like a blank scaffold
    filled_lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("<!--") and not ln.strip().startswith("#")
    ]
    if len(filled_lines) < 3:
        return False
    for marker in template_markers:
        if marker in text:
            return len(filled_lines) > 10  # large enough to not be all-template
    return True


def _detect_gates(root: Path) -> dict[str, bool]:
    """Return dict with G0..G5 as bool (True = gate passed)."""
    g = {}
    g["G0"] = _is_non_template(root / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md")
    g["G1"] = (
        (root / "core" / "strategy" / "BELIEF.md").is_file()
        or (root / "core" / "strategy" / "BELIEF_LITE.md").is_file()
    )
    g["G2"] = (root / "core" / "strategy" / "EXPECTATION_MAP.md").is_file()
    g["G3"] = (root / "core" / "strategy" / "DESIGN_NOTE.md").is_file()
    g["G4"] = (root / "core" / "execution" / "TRUST_TIER.md").is_file()
    g["G5"] = (root / "core" / "governance" / "QUALITY_CHECK.md").is_file()
    return g


def _detect_phase(gates: dict[str, bool]) -> str:
    """Return phase name based on highest passed gate."""
    if gates.get("G5"):
        return "REVIEW"
    if gates.get("G4"):
        return "BUILD"
    if gates.get("G3"):
        return "DESIGN"
    if gates.get("G2"):
        return "PLANNING"
    if gates.get("G1"):
        return "BELIEF"
    if gates.get("G0"):
        return "ONBOARDING"
    return "ONBOARDING"


# ---------------------------------------------------------------------------
# Belief text extraction
# ---------------------------------------------------------------------------

def _read_belief_line(root: Path) -> str:
    """Read first non-empty line after '## Problem' in BELIEF.md."""
    for belief_path in [
        root / "core" / "strategy" / "BELIEF.md",
        root / "core" / "strategy" / "BELIEF_LITE.md",
    ]:
        if not belief_path.is_file():
            continue
        text = belief_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        in_problem = False
        for line in lines:
            stripped = line.strip()
            if re.match(r"^#{1,3}\s+Problem", stripped) or stripped == "**Problem**":
                in_problem = True
                continue
            if in_problem and stripped and not stripped.startswith("#"):
                # Strip markdown formatting
                clean = re.sub(r"\*{1,2}|_{1,2}|`", "", stripped)
                clean = re.sub(r"^\s*[-*>]+\s*", "", clean)
                return clean[:50]
    return "No belief statement found"


def _read_scale_track(root: Path) -> str:
    """Read scale_track from BELIEF.md front-matter."""
    for belief_path in [
        root / "core" / "strategy" / "BELIEF_LITE.md",
        root / "core" / "strategy" / "BELIEF.md",
    ]:
        if not belief_path.is_file():
            continue
        for line in belief_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^scale_track:\s*(\S+)", line.strip())
            if m:
                return m.group(1)
    return "wave"


def _read_delivery_mode(root: Path) -> str:
    """Read delivery_mode from SOUL-DOCUMENT.md or CONSTITUTION.md."""
    for path in [
        root / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md",
        root / "core" / "governance" / "Governance" / "CONSTITUTION.md",
    ]:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^delivery_mode:\s*(\S+)", line.strip())
            if m:
                return m.group(1)
    return "fresh-wave"


# ---------------------------------------------------------------------------
# Task data from worktree-state.json
# ---------------------------------------------------------------------------

def _read_tasks(root: Path, product_id: str | None = None) -> list[dict[str, Any]]:
    """Read task list from worktree-state.json.

    When *product_id* is provided the product-scoped path is used:
      .signalos/products/<id>/worktree-state.json
    Otherwise falls back to the repo-level:
      .signalos/worktree-state.json
    """
    if product_id:
        state_file = root / REPO_ROOT_MARKER / "products" / product_id / "worktree-state.json"
    else:
        state_file = root / REPO_ROOT_MARKER / "worktree-state.json"
    if not state_file.is_file():
        return []
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data.get("worktrees", [])
    except Exception:
        return []


def _task_icon(status: str) -> str:
    """Return display icon for a task status."""
    icons = {
        "active": "⟳",
        "running": "⟳",
        "paused": "⏸",
        "completed": "✓",
        "failed": "✗",
        "merged": "✓",
    }
    return icons.get(status.lower() if status else "", "○")


def _infer_tier(task: dict[str, Any]) -> str:
    """Infer task trust tier from branch name or task dict."""
    # Try explicit tier field first
    if "tier" in task:
        return str(task["tier"])
    # Try to infer from branch name patterns
    branch = task.get("branch", "")
    if "t3" in branch.lower():
        return "T3"
    if "t2" in branch.lower():
        return "T2"
    return "T1"


# ---------------------------------------------------------------------------
# Next action
# ---------------------------------------------------------------------------

def _next_action(gates: dict[str, bool], tasks: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (role, command) for the next blocking action."""
    # Check for paused tasks
    for task in tasks:
        status = task.get("status", "")
        if status in {"paused"}:
            step_id = task.get("step_id") or task.get("branch", "unknown")
            return "PE", f"signalos pause resume {step_id}"
    # Check for failed tasks
    for task in tasks:
        if task.get("status", "") == "failed":
            step_id = task.get("step_id") or task.get("branch", "unknown")
            return "PE", f"signalos harness status {step_id}"
    # All tasks done and G5 open
    all_done = all(
        t.get("status", "") in {"completed", "merged"} for t in tasks
    ) if tasks else False
    if all_done and not gates.get("G5"):
        return "QA", "sign QUALITY_CHECK.md"
    # G0 not passed
    if not gates.get("G0"):
        return "PO", "signalos signal-onboard"
    # G1 not passed
    if not gates.get("G1"):
        return "PO", "signalos signal-pre-wave"
    return "—", "No blocking action"


# ---------------------------------------------------------------------------
# State aggregation
# ---------------------------------------------------------------------------

def _detect_wave_id(root: Path, tasks: list[dict[str, Any]]) -> str:
    """Try to determine current wave ID."""
    # From tasks
    for t in tasks:
        wave = t.get("wave", "")
        if wave:
            return str(wave)
    # From worktree-state.json top-level field
    state_file = root / REPO_ROOT_MARKER / "worktree-state.json"
    if state_file.is_file():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            w = data.get("wave_id") or data.get("wave")
            if w:
                return str(w)
        except Exception:
            pass
    return "—"


def get_wave_status(
    repo_root: Path,
    product_id: str | None = None,
) -> dict[str, Any]:
    """Read all state from disk and return a status dict.

    When *product_id* is provided tasks are read from the product-scoped
    worktree-state.json (.signalos/products/<id>/worktree-state.json).
    Gate and belief state are always repo-level.
    """
    gates = _detect_gates(repo_root)
    phase = _detect_phase(gates)
    belief_line = _read_belief_line(repo_root)
    scale_track = _read_scale_track(repo_root)
    delivery_mode = _read_delivery_mode(repo_root)
    tasks = _read_tasks(repo_root, product_id=product_id)
    wave_id = _detect_wave_id(repo_root, tasks)
    role, action_cmd = _next_action(gates, tasks)
    return {
        "wave_id": wave_id,
        "phase": phase,
        "belief_line": belief_line,
        "scale_track": scale_track,
        "delivery_mode": delivery_mode,
        "gates": gates,
        "tasks": tasks,
        "next_action": {"role": role, "command": action_cmd},
        "repo_root": str(repo_root),
        "product_id": product_id,
        "ide": detect_ide(),
    }




# ---------------------------------------------------------------------------
# W3.2 — time_in_state helpers (AMD-CORE-015)
# ---------------------------------------------------------------------------

def _format_elapsed(seconds: float) -> str:
    """Return human-readable elapsed time: '4m 32s', '1h 20m', '< 1s'."""
    if seconds < 1:
        return "< 1s"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _task_elapsed(task: dict) -> str:
    """Return elapsed-time string for a task, or '' if not available."""
    import time as _time
    started = task.get("started_at") or task.get("start_time") or task.get("created_at")
    if not started:
        return ""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        elapsed = _time.time() - dt.timestamp()
        return _format_elapsed(elapsed)
    except Exception:
        return ""

# ---------------------------------------------------------------------------
# Card renderer
# ---------------------------------------------------------------------------

def _pad(text: str, width: int) -> str:
    """Left-justify text padded to `width` chars (truncate if longer)."""
    if len(text) > width:
        text = text[:width - 1] + "…"
    return text.ljust(width)


def render_status_card(status: dict[str, Any]) -> str:
    """Render the ASCII status card from a status dict.

    Uses box-drawing characters ╔ ╠ ╚ ║ ═.
    """
    W = CARD_WIDTH
    sep_top    = "╔" + "═" * W + "╗"
    sep_mid    = "╠" + "═" * W + "╣"
    sep_bot    = "╚" + "═" * W + "╝"

    def row(text: str = "") -> str:
        return "║" + _pad("  " + text, W) + "║"

    lines = []
    lines.append(sep_top)

    # ── Header ──────────────────────────────────────────────────────────────
    wave_id = status.get("wave_id", "—")
    phase   = status.get("phase", "—")
    header  = f"SignalOS · Wave {wave_id} · {phase}"
    lines.append(row(header))
    lines.append(sep_mid)

    # ── Belief / Track ───────────────────────────────────────────────────────
    belief_line  = status.get("belief_line", "—")
    scale_track  = status.get("scale_track", "wave")
    delivery_mode = status.get("delivery_mode", "fresh-wave")
    lines.append(row(f"Belief  {belief_line[:50]}"))
    lines.append(row(f"Track   {scale_track} · Mode: {delivery_mode}"))
    lines.append(sep_mid)

    # ── Gates ────────────────────────────────────────────────────────────────
    lines.append(row("GATES"))
    gates = status.get("gates", {})
    gate_names = {
        "G0": "Onboarding",
        "G1": "Belief",
        "G2": "Planning",
        "G3": "Design",
        "G4": "Build",
        "G5": "Review",
    }
    # Two columns of 3 gates each
    gate_keys = list(gate_names.keys())
    gate_row1 = "  ".join(
        f"{'✓' if gates.get(k) else '○'} {k} {gate_names[k]:<10}"
        for k in gate_keys[:3]
    )
    gate_row2 = "  ".join(
        f"{'✓' if gates.get(k) else '○'} {k} {gate_names[k]:<10}"
        for k in gate_keys[3:]
    )
    lines.append(row(gate_row1))
    lines.append(row(gate_row2))
    lines.append(sep_mid)

    # ── Tasks ────────────────────────────────────────────────────────────────
    # Header
    task_hdr = f"{'TASKS':<34}{'TIER':<8}STATUS"
    lines.append(row(task_hdr))
    tasks = status.get("tasks", [])
    if not tasks:
        lines.append(row("No active tasks"))
    else:
        for task in tasks[:8]:  # cap at 8 rows to keep card bounded
            icon   = _task_icon(task.get("status", ""))
            name   = task.get("task") or task.get("branch", "?")
            if len(str(name)) > 26:
                name = str(name)[:25] + "…"
            tier   = _infer_tier(task)
            tstatus = (task.get("status") or "active").upper()
            elapsed = _task_elapsed(task)
            time_tag = f" ({elapsed})" if elapsed else ""
            task_line = f"{icon}  {str(name):<28}{tier:<6}{tstatus}{time_tag}"
            lines.append(row(task_line))
    lines.append(sep_mid)

    # ── Next action ──────────────────────────────────────────────────────────
    lines.append(row("NEXT ACTION"))
    na = status.get("next_action", {})
    role = na.get("role", "—")
    cmd  = na.get("command", "—")
    lines.append(row(f"{role} → {cmd}"))
    lines.append(sep_bot)

    return "\n".join(lines)



# ---------------------------------------------------------------------------
# W3.2 — watch mode (AMD-CORE-015 T1)
# ---------------------------------------------------------------------------

def _clear_screen() -> None:
    """ANSI clear + cursor-home."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _inotifywait_available() -> bool:
    """Return True if inotifywait is present on PATH."""
    import shutil
    return shutil.which("inotifywait") is not None


def _watch_with_inotify(watch_dir: str, timeout: float = 5.0) -> bool:
    """
    Block until inotifywait fires an event in *watch_dir*, or *timeout* elapses.
    Returns True if an event was detected, False on timeout.
    """
    import subprocess as _sp
    try:
        result = _sp.run(
            ["inotifywait", "-r", "-q", "--timeout", str(int(timeout)),
             "-e", "modify,create,moved_to", watch_dir],
            capture_output=True, timeout=timeout + 2,
        )
        return result.returncode == 0
    except Exception:
        return False


def watch_status(
    repo_root: Path | None = None,
    interval: float = 2.0,
    clear: bool = True,
) -> None:
    """
    Continuously refresh the status card on any journal/daemon-events change.

    Strategy:
      1. Try inotifywait on .signalos/ (Linux inotify).
      2. Fall back to polling every *interval* seconds.

    Ctrl-C exits cleanly.
    """
    import time as _time

    root = repo_root or _repo_root()
    watch_dir = str(root / REPO_ROOT_MARKER)
    use_inotify = _inotifywait_available()

    if use_inotify:
        sys.stderr.write(f"  watch: inotifywait on {watch_dir}\n")
    else:
        sys.stderr.write(
            f"  watch: polling every {interval}s (install inotify-tools for event-driven mode)\n"
        )

    def _render() -> None:
        if clear:
            _clear_screen()
        print_status_card(root)

    try:
        _render()
        while True:
            if use_inotify:
                _watch_with_inotify(watch_dir, timeout=interval)
            else:
                _time.sleep(interval)
            _render()
    except KeyboardInterrupt:
        sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Convenience wrapper

# ---------------------------------------------------------------------------
# W4.2 — multi-product aggregated dashboard (AMD-CORE-020)
# ---------------------------------------------------------------------------

def render_multi_product_dashboard(records: list[dict[str, Any]]) -> str:
    """Render an ASCII dashboard summarising all registered products.

    Each record is a dict from tenant.product_status().
    """
    W = CARD_WIDTH
    sep_top = "╔" + "═" * W + "╗"
    sep_mid = "╠" + "═" * W + "╣"
    sep_bot = "╚" + "═" * W + "╝"

    def row(text: str = "") -> str:
        return "║" + _pad("  " + text, W) + "║"

    lines = [sep_top]
    lines.append(row("SignalOS · Multi-Product Dashboard"))
    lines.append(sep_mid)

    if not records:
        lines.append(row("No product namespaces registered."))
        lines.append(row("  signalos tenant init <id>  to create one."))
    else:
        hdr = f"{'PRODUCT':<20} {'C':<3} {'S':<3} {'SESS':>5} {'TASKS':>5} {'VALID':<6}"
        lines.append(row(hdr))
        lines.append(row("─" * (W - 2)))
        for r in records:
            c = "✓" if r["constitution"] else "✗"
            s = "✓" if r["soul_document"] else "✗"
            v = "✓" if r["valid"] else "✗"
            data_row = (
                f"{r['product_id']:<20} {c:<3} {s:<3} "
                f"{r['session_count']:>5} {r['active_tasks']:>5} {v:<6}"
            )
            lines.append(row(data_row))
        lines.append(row())
        lines.append(row("C=Constitution  S=Soul-Document  SESS=sessions  VALID=both ok"))

    lines.append(sep_bot)
    return "\n".join(lines)


# ---------------------------------------------------------------------------

def print_status_card(
    repo_root: Path | None = None,
    product_id: str | None = None,
) -> None:
    """Print the status card for the given repo root (or cwd).

    When *product_id* is provided tasks are scoped to that product
    namespace. When None and products exist the multi-product dashboard
    is printed instead of the single-product card.
    """
    root = repo_root or _repo_root()

    if product_id is None:
        # Auto-detect: show multi-product dashboard when products are registered
        try:
            from signalos_lib.tenant import list_products, multi_product_summary
            products = list_products(root)
        except Exception:
            products = []
        if products:
            records = multi_product_summary(root)
            sys.stdout.write(render_multi_product_dashboard(records) + "\n")
            return

    status = get_wave_status(root, product_id=product_id)
    card = render_status_card(status)
    sys.stdout.write(card + "\n")
