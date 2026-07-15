"""
health.py — System health check (W3.5 · AMD-CORE-018).

Checks: git availability, Python version, jq availability,
wiring-guard.sh, and daemon heartbeat staleness.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .git_process import run_git

HEARTBEAT_STALE_SECS: int = 300  # 5 minutes


class HealthStatus:
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"

    _ORDER = [OK, DEGRADED, DOWN]

    @staticmethod
    def worst(a: str, b: str) -> str:
        order = HealthStatus._ORDER
        ia = order.index(a) if a in order else 0
        ib = order.index(b) if b in order else 0
        return order[max(ia, ib)]


@dataclass
class HealthItem:
    name: str
    status: str
    detail: str = ""

    def is_ok(self) -> bool:
        return self.status == HealthStatus.OK


@dataclass
class HealthReport:
    items: List[HealthItem] = field(default_factory=list)

    @property
    def overall(self) -> str:
        result = HealthStatus.OK
        for item in self.items:
            result = HealthStatus.worst(result, item.status)
        return result

    @property
    def exit_code(self) -> int:
        o = self.overall
        if o == HealthStatus.DOWN:
            return 2
        if o == HealthStatus.DEGRADED:
            return 1
        return 0


def _check_git() -> HealthItem:
    if shutil.which("git") is None:
        return HealthItem("git", HealthStatus.DOWN, "git not found on PATH")
    try:
        out = run_git(
            ["--version"],
            cwd=Path.cwd(),
            runner=subprocess.run,
            capture_output=True,
            text=True,
            timeout=5,
        )
        ver = out.stdout.strip()
        return HealthItem("git", HealthStatus.OK, ver)
    except Exception as exc:
        return HealthItem("git", HealthStatus.DOWN, str(exc))


def _check_python() -> HealthItem:
    vi = sys.version_info
    ver_str = f"{vi[0]}.{vi[1]}.{vi[2]}"
    if vi >= (3, 11):
        return HealthItem("python", HealthStatus.OK, f"Python {ver_str}")
    return HealthItem(
        "python", HealthStatus.DEGRADED,
        f"Python {ver_str} < 3.11 (some features may not work)"
    )


def _check_jq() -> HealthItem:
    if shutil.which("jq") is None:
        return HealthItem("jq", HealthStatus.DEGRADED, "jq not found on PATH — shell scripts may fail")
    try:
        out = subprocess.run(
            ["jq", "--version"], capture_output=True, text=True, timeout=5
        )
        return HealthItem("jq", HealthStatus.OK, out.stdout.strip())
    except Exception as exc:
        return HealthItem("jq", HealthStatus.DEGRADED, f"jq error: {exc}")


def _check_wiring_guard(repo_root: Path) -> HealthItem:
    guard = repo_root / "core" / "governance" / "Validators" / "wiring-guard.sh"
    if not guard.exists():
        return HealthItem("wiring-guard", HealthStatus.DOWN, f"wiring-guard.sh not found at {guard}")
    try:
        result = subprocess.run(
            ["bash", str(guard)], capture_output=True, text=True,
            cwd=str(repo_root), timeout=30
        )
        if result.returncode == 0:
            return HealthItem("wiring-guard", HealthStatus.OK, "all checks passed")
        details = (result.stdout + result.stderr).strip()
        return HealthItem("wiring-guard", HealthStatus.DOWN, f"failed: {details[:120]}")
    except Exception as exc:
        return HealthItem("wiring-guard", HealthStatus.DEGRADED, f"could not run: {exc}")


def _check_daemon_heartbeat(repo_root: Path) -> HealthItem:
    hb_path = repo_root / ".signalos" / "daemon-heartbeat"
    try:
        st = hb_path.stat()
    except FileNotFoundError:
        return HealthItem(
            "daemon-heartbeat", HealthStatus.DEGRADED,
            "heartbeat file absent — daemon not running"
        )
    except OSError as exc:
        return HealthItem("daemon-heartbeat", HealthStatus.DEGRADED, f"cannot stat: {exc}")
    try:
        age = time.time() - st.st_mtime
        if age <= HEARTBEAT_STALE_SECS:
            return HealthItem(
                "daemon-heartbeat", HealthStatus.OK,
                f"last beat {int(age)}s ago"
            )
        return HealthItem(
            "daemon-heartbeat", HealthStatus.DEGRADED,
            f"stale: last beat {int(age)}s ago (threshold {HEARTBEAT_STALE_SECS}s)"
        )
    except OSError as exc:
        return HealthItem("daemon-heartbeat", HealthStatus.DEGRADED, f"cannot read mtime: {exc}")


def run_health(repo_root: Optional[Path] = None) -> HealthReport:
    """Run all health checks and return a HealthReport."""
    if repo_root is None:
        repo_root = Path.cwd()
    repo_root = Path(repo_root)
    items = [
        _check_git(),
        _check_python(),
        _check_jq(),
        _check_wiring_guard(repo_root),
        _check_daemon_heartbeat(repo_root),
    ]
    return HealthReport(items=items)
