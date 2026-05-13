# SignalOS Core — Multi-repo campaign orchestration (AMD-CORE-023, W5.2).
#
# Coordinates work across multiple repos under a single named Campaign.
# A Campaign Constitution (CAMPAIGN.json) lives at a shared path; a
# BELIEF_MAP.md is written after each orchestration pass.
#
# Public API:
#   CampaignError
#   CAMPAIGN_FILE, BELIEF_MAP_FILE, CAMPAIGN_SCHEMA_VERSION
#   init_campaign(name, repos, campaign_root=None) -> dict
#   load_campaign(campaign_root=None) -> dict
#   campaign_status(campaign, *, repo_status_fn=None) -> dict
#   campaign_orchestrate(campaign, wave, plan, ...) -> dict
#   update_belief_map(campaign, aggregate, campaign_root=None) -> str
#
# Stdlib only (json, pathlib, concurrent.futures, subprocess).
# All external calls are injectable for 100%-coverage testing.

from __future__ import annotations

__all__ = [
    "CampaignError",
    "CAMPAIGN_FILE",
    "BELIEF_MAP_FILE",
    "CAMPAIGN_SCHEMA_VERSION",
    "init_campaign",
    "load_campaign",
    "campaign_status",
    "campaign_orchestrate",
    "update_belief_map",
]

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from signalos_lib.status import get_wave_status as _default_repo_status_fn

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CAMPAIGN_SCHEMA_VERSION = "1.0"
CAMPAIGN_FILE = "CAMPAIGN.json"
BELIEF_MAP_FILE = "BELIEF_MAP.md"
_REPO_MARKER = ".signalos"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CampaignError(Exception):
    """Raised for campaign configuration or orchestration errors."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _subprocess_orchestrate(repo_path: str, wave: str, plan: str) -> int:
    """Default orchestrate_fn: invoke ``signalos orchestrate`` in a subprocess."""
    cli = Path(__file__).resolve().parent.parent.parent / "signalos"
    result = subprocess.run(
        [sys.executable, str(cli), "orchestrate",
         "--wave", wave, "--plan", plan, "--repo-root", repo_path],
        capture_output=True,
    )
    return result.returncode


# ---------------------------------------------------------------------------
# init_campaign
# ---------------------------------------------------------------------------

def init_campaign(
    name: str,
    repos: list[str],
    campaign_root: "Path | str | None" = None,
) -> "dict[str, Any]":
    """Create ``CAMPAIGN.json`` at *campaign_root* (defaults to ``Path.cwd()``).

    Parameters
    ----------
    name:
        Human-readable campaign name (non-empty).
    repos:
        List of paths to per-repo roots. Each must contain ``.signalos/``.
    campaign_root:
        Directory where ``CAMPAIGN.json`` is written.

    Returns
    -------
    dict
        The manifest that was written to disk.

    Raises
    ------
    CampaignError
        If *name* is empty, *repos* is empty, or a repo lacks ``.signalos``.
    """
    if not name or not name.strip():
        raise CampaignError("Campaign name must not be empty.")
    if not repos:
        raise CampaignError("At least one repo path is required.")

    root = Path(campaign_root) if campaign_root is not None else Path.cwd()

    resolved: list[str] = []
    for rp in repos:
        p = Path(rp)
        if not (p / _REPO_MARKER).exists():
            raise CampaignError(
                f"Repo path does not contain .signalos directory: {rp!r}"
            )
        resolved.append(str(p.resolve()))

    manifest: dict[str, Any] = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "name": name.strip(),
        "repos": resolved,
        "created_at": _now_iso(),
    }
    (root / CAMPAIGN_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# load_campaign
# ---------------------------------------------------------------------------

def load_campaign(campaign_root: "Path | str | None" = None) -> "dict[str, Any]":
    """Load ``CAMPAIGN.json`` from *campaign_root* (defaults to ``Path.cwd()``).

    Raises
    ------
    CampaignError
        If the file is missing or contains invalid JSON.
    """
    root = Path(campaign_root) if campaign_root is not None else Path.cwd()
    fp = root / CAMPAIGN_FILE
    if not fp.exists():
        raise CampaignError(f"No CAMPAIGN.json found at {root}")
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CampaignError(f"Invalid CAMPAIGN.json: {exc}") from exc


# ---------------------------------------------------------------------------
# campaign_status
# ---------------------------------------------------------------------------

def campaign_status(
    campaign: "dict[str, Any]",
    *,
    repo_status_fn: "Callable[[Path], dict[str, Any]] | None" = None,
) -> "dict[str, Any]":
    """Aggregate wave status across all repos in *campaign*.

    Parameters
    ----------
    campaign:
        Campaign manifest (as returned by :func:`load_campaign`).
    repo_status_fn:
        ``(repo_root: Path) -> dict`` returning a wave-status dict.
        Defaults to :func:`signalos_lib.status.get_wave_status`.

    Returns
    -------
    dict
        ``{"name": str, "repos": [{"path": str, "status": dict|None, "error": str|None}]}``
    """
    fn = repo_status_fn if repo_status_fn is not None else _default_repo_status_fn
    results: list[dict[str, Any]] = []
    for rp in campaign.get("repos", []):
        try:
            st = fn(Path(rp))
            results.append({"path": rp, "status": st, "error": None})
        except Exception as exc:  # noqa: BLE001
            results.append({"path": rp, "status": None, "error": str(exc)})
    return {"name": campaign.get("name", ""), "repos": results}


# ---------------------------------------------------------------------------
# campaign_orchestrate
# ---------------------------------------------------------------------------

def campaign_orchestrate(
    campaign: "dict[str, Any]",
    wave: str,
    plan: str,
    *,
    max_concurrent: int = 4,
    orchestrate_fn: "Callable[[str, str, str], int] | None" = None,
    campaign_root: "Path | str | None" = None,
    belief_map_fn: "Callable[..., str] | None" = None,
) -> "dict[str, Any]":
    """Fan out orchestration to every repo in *campaign*, then fan in results.

    Parameters
    ----------
    campaign:
        Campaign manifest.
    wave:
        Wave ID string (e.g. ``"W5.2"``).
    plan:
        Path to the plan file passed to each repo's orchestrator.
    max_concurrent:
        Maximum simultaneous orchestrations.
    orchestrate_fn:
        ``(repo_path, wave, plan) -> returncode``.
        Defaults to :func:`_subprocess_orchestrate`.
    campaign_root:
        Where to write ``BELIEF_MAP.md``. Defaults to ``Path.cwd()``.
    belief_map_fn:
        Injectable override for :func:`update_belief_map`.

    Returns
    -------
    dict
        ``{"wave": str, "plan": str, "repos": [{"path": str, "returncode": int, "error": str|None}]}``
    """
    orch_fn = orchestrate_fn if orchestrate_fn is not None else _subprocess_orchestrate
    bmap_fn = belief_map_fn if belief_map_fn is not None else update_belief_map

    repos = campaign.get("repos", [])
    repo_results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {pool.submit(orch_fn, rp, wave, plan): rp for rp in repos}
        for fut in as_completed(futures):
            rp = futures[fut]
            try:
                rc = fut.result()
                repo_results.append({"path": rp, "returncode": rc, "error": None})
            except Exception as exc:  # noqa: BLE001
                repo_results.append({"path": rp, "returncode": -1, "error": str(exc)})

    aggregate: dict[str, Any] = {"wave": wave, "plan": plan, "repos": repo_results}
    root = Path(campaign_root) if campaign_root is not None else Path.cwd()
    bmap_fn(campaign, aggregate, root)
    return aggregate


# ---------------------------------------------------------------------------
# update_belief_map
# ---------------------------------------------------------------------------

def update_belief_map(
    campaign: "dict[str, Any]",
    aggregate: "dict[str, Any]",
    campaign_root: "Path | str | None" = None,
) -> str:
    """Write (or overwrite) ``BELIEF_MAP.md`` at *campaign_root* (defaults to cwd).

    Returns the rendered Markdown string.
    """
    root = Path(campaign_root) if campaign_root is not None else Path.cwd()
    name = campaign.get("name", "unnamed")
    wave = aggregate.get("wave", "—")
    plan = aggregate.get("plan", "—")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows: list[str] = []
    for entry in aggregate.get("repos", []):
        path = entry.get("path", "")
        rc = entry.get("returncode", -1)
        err = entry.get("error") or ""
        status_str = "✓ ok" if rc == 0 else f"✗ rc={rc}"
        if err:
            status_str += f" ({err[:60]})"
        rows.append(f"| `{path}` | {status_str} |")

    table = "\n".join(rows) if rows else "| (no repos) | — |"
    md = (
        f"# Campaign Belief Map: {name}\n\n"
        f"**Wave:** {wave}  \n"
        f"**Plan:** {plan}  \n"
        f"**Updated:** {now}\n\n"
        f"| Repo | Orchestrate result |\n"
        f"|------|--------------------|\n"
        f"{table}\n"
    )
    (root / BELIEF_MAP_FILE).write_text(md, encoding="utf-8")
    return md
