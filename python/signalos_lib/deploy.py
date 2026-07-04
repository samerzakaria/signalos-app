"""cli/signalos_lib/deploy.py — SignalOS Post-Deploy Lifecycle (AMD-CORE-033) / W12."""
from __future__ import annotations

__all__ = [
    "DEPLOY_INDEX_RELATIVE",
    "BENCHMARK_INDEX_RELATIVE",
    "DeployRecord",
    "BenchmarkRecord",
    "DeployHookError",
    "setup_deploy",
    "land_deploy",
    "run_pre_deploy_hook",
    "canary_deploy_check",
    "record_benchmark",
    "deploy_list",
    "benchmark_list",
    "check_deploy_wired",
    "_next_id",
    "_append_record",
    "_iso_now",
]

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEPLOY_INDEX_RELATIVE = ".signalos/deploy/index.jsonl"
BENCHMARK_INDEX_RELATIVE = ".signalos/deploy/benchmarks.jsonl"
PRE_DEPLOY_HOOK_RELATIVE = "core/execution/hooks/pre-deploy"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DeployRecord:
    id: str        # e.g. "deploy-001"
    stage: str     # e.g. "staging", "production"
    wave: str
    ts: str        # ISO-8601
    note: str
    status: str    # "setup" | "landed"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class BenchmarkRecord:
    id: str           # e.g. "bench-001"
    url: str
    wave: str
    ts: str
    lcp_ms: float     # Largest Contentful Paint ms
    inp_ms: float     # Interaction to Next Paint ms
    cls_score: float  # Cumulative Layout Shift score
    ttfb_ms: float    # Time to First Byte ms
    weight_kb: float  # Page weight KB

    def as_dict(self) -> dict:
        return asdict(self)


class DeployHookError(RuntimeError):
    """Raised when a deployment lifecycle hook blocks a deploy transition."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return current UTC time as an ISO-8601 string."""
    t = time.gmtime()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", t)


def _next_id(index_path: Path, prefix: str) -> str:
    """Read JSONL at *index_path*, find the highest N in ``{prefix}-NNN`` IDs, return next.

    Defensive: OSError → "{prefix}-001", JSONDecodeError / blank / ValueError → skip line.
    Never raises.
    """
    highest = 0
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return f"{prefix}-001"

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        entry_id = entry.get("id", "")
        if not entry_id.startswith(f"{prefix}-"):
            continue
        suffix = entry_id[len(prefix) + 1:]
        try:
            n = int(suffix)
        except ValueError:
            continue
        if n > highest:
            highest = n

    return f"{prefix}-{highest + 1:03d}"


def _append_record(index_path: Path, record: dict) -> None:
    """Create parent dirs if needed, then append *record* as a JSON line."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# setup_deploy
# ---------------------------------------------------------------------------

def setup_deploy(
    repo_root: Path,
    wave: str,
    stage: str,
    note: str = "",
) -> DeployRecord:
    """Create and save a DeployRecord with status='setup'."""
    index_path = repo_root / DEPLOY_INDEX_RELATIVE
    record_id = _next_id(index_path, "deploy")
    record = DeployRecord(
        id=record_id,
        stage=stage,
        wave=wave,
        ts=_iso_now(),
        note=note,
        status="setup",
    )
    _append_record(index_path, record.as_dict())
    return record


# ---------------------------------------------------------------------------
# land_deploy
# ---------------------------------------------------------------------------

def land_deploy(
    repo_root: Path,
    deploy_id: str,
    *,
    enforce_pre_deploy: bool = False,
    deploy_signer: str | None = None,
) -> "DeployRecord | None":
    """Read deploy index, find record by id, rewrite file with status='landed'.

    Returns the updated DeployRecord, or None if not found.

    When *enforce_pre_deploy* is true, the installed pre-deploy hook must pass
    before the deploy index is mutated. A hook failure raises DeployHookError
    and leaves the record at its prior status.
    """
    index_path = repo_root / DEPLOY_INDEX_RELATIVE
    try:
        raw = index_path.read_text(encoding="utf-8")
    except OSError:
        return None

    lines = raw.splitlines()
    updated_record: "DeployRecord | None" = None
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            new_lines.append(line)
            continue
        if entry.get("id") == deploy_id:
            entry["status"] = "landed"
            updated_record = DeployRecord(
                id=entry["id"],
                stage=entry.get("stage", ""),
                wave=entry.get("wave", ""),
                ts=entry.get("ts", ""),
                note=entry.get("note", ""),
                status="landed",
            )
            new_lines.append(json.dumps(entry))
        else:
            new_lines.append(line)

    if updated_record is None:
        return None

    if enforce_pre_deploy:
        run_pre_deploy_hook(repo_root, deploy_signer=deploy_signer)

    index_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated_record


def run_pre_deploy_hook(
    repo_root: Path,
    *,
    deploy_signer: str | None = None,
    timeout_s: int = 60,
) -> None:
    """Run the installed pre-deploy hook for the deploy lifecycle.

    The hook is part of the initialized SignalOS workspace under
    ``core/execution/hooks/pre-deploy``. It enforces deployment SoD using the
    DevOps signer and the merge signer recorded by pre-merge. Missing, failing,
    or non-runnable hooks block the live deploy transition instead of being
    skipped.
    """
    repo_root = Path(repo_root)
    hook = repo_root / PRE_DEPLOY_HOOK_RELATIVE
    if not hook.is_file():
        raise DeployHookError(f"pre-deploy hook is not installed at {PRE_DEPLOY_HOOK_RELATIVE}")

    signer = (
        (deploy_signer or "").strip()
        or os.environ.get("DEVOPS_DEPLOY_SIGNER", "").strip()
        or os.environ.get("SIGNALOS_DEVOPS_DEPLOY_SIGNER", "").strip()
    )
    env = os.environ.copy()
    if signer:
        env["DEVOPS_DEPLOY_SIGNER"] = signer

    bash = shutil.which("bash")
    if not bash:
        raise DeployHookError("pre-deploy hook requires bash, but bash was not found")

    proc = subprocess.run(
        [bash, str(hook)],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    if proc.returncode != 0:
        output = "\n".join(
            part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip()
        )
        detail = output or f"pre-deploy exited with {proc.returncode}"
        raise DeployHookError(detail)


# ---------------------------------------------------------------------------
# canary_deploy_check
# ---------------------------------------------------------------------------

def canary_deploy_check(repo_root: Path, wave: str) -> dict:
    """Read deploy index, count records with matching wave.

    Returns ``{"wave": wave, "found": bool, "count": int}``.
    Defensive: OSError → count=0, JSONDecodeError/blank → skip.
    """
    index_path = repo_root / DEPLOY_INDEX_RELATIVE
    count = 0
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"wave": wave, "found": False, "count": 0}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if entry.get("wave") == wave:
            count += 1

    return {"wave": wave, "found": count > 0, "count": count}


# ---------------------------------------------------------------------------
# record_benchmark
# ---------------------------------------------------------------------------

def record_benchmark(
    repo_root: Path,
    url: str,
    wave: str,
    lcp_ms: float,
    inp_ms: float,
    cls_score: float,
    ttfb_ms: float,
    weight_kb: float,
) -> BenchmarkRecord:
    """Create and save a BenchmarkRecord."""
    index_path = repo_root / BENCHMARK_INDEX_RELATIVE
    record_id = _next_id(index_path, "bench")
    record = BenchmarkRecord(
        id=record_id,
        url=url,
        wave=wave,
        ts=_iso_now(),
        lcp_ms=lcp_ms,
        inp_ms=inp_ms,
        cls_score=cls_score,
        ttfb_ms=ttfb_ms,
        weight_kb=weight_kb,
    )
    _append_record(index_path, record.as_dict())
    return record


# ---------------------------------------------------------------------------
# deploy_list
# ---------------------------------------------------------------------------

def deploy_list(repo_root: Path, wave: "str | None" = None) -> "list[DeployRecord]":
    """Read deploy JSONL, optionally filter by wave.

    Defensive: OSError → [], JSONDecodeError/blank/KeyError/ValueError → skip line.
    """
    index_path = repo_root / DEPLOY_INDEX_RELATIVE
    results: list[DeployRecord] = []
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
            record = DeployRecord(
                id=entry["id"],
                stage=entry["stage"],
                wave=entry["wave"],
                ts=entry["ts"],
                note=entry["note"],
                status=entry["status"],
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if wave is not None and record.wave != wave:
            continue
        results.append(record)

    return results


# ---------------------------------------------------------------------------
# benchmark_list
# ---------------------------------------------------------------------------

def benchmark_list(repo_root: Path, wave: "str | None" = None) -> "list[BenchmarkRecord]":
    """Read benchmark JSONL, optionally filter by wave.

    Defensive: OSError → [], JSONDecodeError/blank/KeyError/ValueError → skip line.
    """
    index_path = repo_root / BENCHMARK_INDEX_RELATIVE
    results: list[BenchmarkRecord] = []
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
            record = BenchmarkRecord(
                id=entry["id"],
                url=entry["url"],
                wave=entry["wave"],
                ts=entry["ts"],
                lcp_ms=entry["lcp_ms"],
                inp_ms=entry["inp_ms"],
                cls_score=entry["cls_score"],
                ttfb_ms=entry["ttfb_ms"],
                weight_kb=entry["weight_kb"],
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if wave is not None and record.wave != wave:
            continue
        results.append(record)

    return results


# ---------------------------------------------------------------------------
# check_deploy_wired
# ---------------------------------------------------------------------------

def check_deploy_wired(repo_root: Path) -> "tuple[bool, str]":
    """C18: check that deploy lib and key command specs exist.

    Returns ``(True, "deploy wired")`` or ``(False, "missing: ...")``.
    """
    required = [
        "cli/signalos_lib/deploy.py",
        "core/execution/commands/signal-setup-deploy.md",
        "core/execution/commands/signal-benchmark.md",
    ]
    missing = [r for r in required if not (repo_root / r).exists()]
    if missing:
        return (False, "missing: " + ", ".join(missing))
    return (True, "deploy wired")
