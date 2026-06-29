"""Behavioral concept-parity checks for app-side SignalOS.NET surfaces.

These tests do not assert that the app has copied SignalOS.NET or reached full
feature parity, and they no longer grep app source for symbol names (weak
evidence: it proves a string exists, not that the behavior is wired). Instead
they IMPORT the real app modules and exercise the invariants that matter:

* trust-tier promotion moves toward T3 and refuses wrong-direction movement;
  permanently-T3 surfaces cannot be demoted;
* observability ``evaluate_listening_window`` returns blockers for a stale /
  threshold-violating window and a draft (never authoritative) verdict;
* worktree snapshots persist and can be queried by id / branch / commit and the
  latest is deterministic;
* the parity CLI commands are actually registered in the parser.

A single light-touch existence check confirms the adjacent SignalOS.NET concept
files and their app-side counterparts both exist, so the parity map itself does
not silently rot. Focused per-module behavior lives in the dedicated test files
(test_trust_tiers.py, test_worktree_sync.py, and the observability tests).
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

import pytest

from signalos_lib.cli import _build_parser
from signalos_lib.product.observability import (
    create_listening_window,
    evaluate_listening_window,
    open_listening_window,
    record_window_reading,
)
from signalos_lib.product.stacks import list_adapters
from signalos_lib.trust_tiers import (
    TrustTierError,
    demote_trust_surface,
    get_trust_surface_by_surface,
    promote_trust_surface,
    register_trust_surface,
    validate_trust_tier,
)
from signalos_lib.worktree_sync import (
    latest_worktree_snapshot,
    list_worktree_snapshots,
    take_worktree_snapshot,
)


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SIGNALOS_NET = APP_ROOT.parent / "SignalOS.NET"
SIGNALOS_NET = Path(os.environ.get("SIGNALOS_NET_REPO", DEFAULT_SIGNALOS_NET))

_GIT = shutil.which("git")
_requires_git = pytest.mark.skipif(_GIT is None, reason="git not available")


def _require_signalos_net() -> Path:
    if not SIGNALOS_NET.is_dir():
        pytest.skip(
            "SignalOS.NET repo not available; set SIGNALOS_NET_REPO to run parity scan"
        )
    return SIGNALOS_NET


def _app_commands() -> set[str]:
    parser = _build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


# ---------------------------------------------------------------------------
# Parity map: the concept files exist on both sides (cheap structural guard).
# ---------------------------------------------------------------------------


def test_signalos_net_core_concepts_have_app_side_artifacts() -> None:
    net = _require_signalos_net()
    concept_pairs = {
        "constitutions": (
            net / "src" / "SignalOS.NET.Constitutions.Domain" / "Constitution.cs",
            APP_ROOT / "python" / "signalos_lib" / "commands" / "constitution.py",
        ),
        "waves": (
            net / "src" / "SignalOS.NET.Waves.Domain" / "Wave.cs",
            APP_ROOT / "python" / "signalos_lib" / "wave_engine.py",
        ),
        "hooks": (
            net / "src" / "SignalOS.NET.Hooks.Domain" / "HookExecution.cs",
            APP_ROOT / "python" / "signalos_lib" / "commands" / "hooks.py",
        ),
        "observability": (
            net / "src" / "SignalOS.NET.Observability.Domain" / "ListeningWindow.cs",
            APP_ROOT / "python" / "signalos_lib" / "product" / "observability.py",
        ),
        "trust_tiers": (
            net / "src" / "SignalOS.NET.TrustTiers.Domain" / "SurfaceTrustTier.cs",
            APP_ROOT / "python" / "signalos_lib" / "trust_tiers.py",
        ),
        "worktree_sync": (
            net / "src" / "SignalOS.NET.WorktreeSync.Domain" / "WorktreeSnapshot.cs",
            APP_ROOT / "python" / "signalos_lib" / "worktree_sync.py",
        ),
        "audit_trail": (
            net / "src" / "SignalOS.NET.AuditTrail.Domain" / "AuditEventTaxonomy.cs",
            APP_ROOT / "python" / "signalos_lib" / "audit_replay.py",
        ),
        "product_delivery": (
            net / "src" / "SignalOS.NET.Cli" / "Commands" / "DeliverCommand.cs",
            APP_ROOT / "python" / "signalos_lib" / "product" / "delivery.py",
        ),
    }
    missing = [
        concept
        for concept, (net_path, app_path) in concept_pairs.items()
        if not net_path.is_file() or not app_path.is_file()
    ]
    assert missing == []


# ---------------------------------------------------------------------------
# Parser registry: parity commands are actually wired into the CLI.
# ---------------------------------------------------------------------------


def test_parity_cli_commands_are_registered_in_the_parser() -> None:
    commands = _app_commands()
    expected = {
        "deliver",
        "deliver-intent",
        "deliver-design",
        "deliver-design-preview",
        "validate-gate",
        "validate-wave-status",
        "validate-traceability",
        "validate-prd-traceability",
        "detect-bypass",
        "validate-guidance-obligations",
        "defer",
        "feature-gate",
        "trace",
        "bundle",
        "handoff",
        "test",
        "verify",
        "integrity-witness",
        "cost",
        "product",
        "observe",
        "worktree-snapshot",
        "validate-worktree-sync",
        "trust-tier",
        "release-readiness",
        "release-proof",
        "ship",
    }
    missing = sorted(expected - commands)
    assert missing == [], f"parity commands missing from parser: {missing}"


def test_delivery_cli_parses_technology_independent_capability_controls() -> None:
    deliver = _build_parser().parse_args(
        [
            "deliver",
            "--prompt",
            "Build an API",
            "--technology",
            "node",
            "--frontend",
            "none",
            "--database",
            "postgresql",
            "--cache",
            "redis",
            "--agent",
            "none",
            "--yes",
        ]
    )
    assert deliver.technologies == ["node"]
    assert deliver.frontend == "none"
    assert deliver.database == "postgresql"
    assert deliver.cache == "redis"


def test_product_stack_adapters_are_not_dotnet_locked() -> None:
    app_adapter_ids = {adapter["id"] for adapter in list_adapters()}
    # A representative, non-.NET-locked spread must be present.
    assert {
        "react-vite",
        "nextjs-app",
        "vue-vite",
        "angular",
        "flutter-app",
        "expo-react-native",
        "node-api",
        "nestjs-api",
        "fastapi-api",
        "django-api",
        "dotnet-minimal-api",
        "go-api",
        "spring-boot-api",
        "existing-repo",
        "generic",
    }.issubset(app_adapter_ids)
    # The .NET adapter is one option among many, not the whole registry.
    assert len(app_adapter_ids) > 5


# ---------------------------------------------------------------------------
# Trust-tier lifecycle behavior (promotion toward T3, refusal of wrong moves).
# ---------------------------------------------------------------------------


def test_trust_tier_promotion_moves_toward_t3_and_refuses_wrong_direction(tmp_path: Path) -> None:
    register_trust_surface(
        tmp_path,
        surface_id="src/api",
        tier="T1",
        justification="fixture starts low risk",
    )

    promoted = promote_trust_surface(
        tmp_path,
        "src/api",
        target_tier="T3",
        justification="now touches sensitive surface",
    )
    assert promoted["tier"] == "T3"
    assert get_trust_surface_by_surface(tmp_path, "src/api")["tier"] == "T3"

    # Wrong-direction promotion (already at the ceiling) is refused, not a no-op.
    with pytest.raises(TrustTierError, match="promote must move upward"):
        promote_trust_surface(
            tmp_path,
            "src/api",
            target_tier="T2",
            justification="illegal downward promote",
        )


def test_trust_tier_permanent_t3_surface_cannot_be_demoted(tmp_path: Path) -> None:
    register_trust_surface(
        tmp_path,
        surface_id="src/payments",
        tier="T3",
        justification="payments are permanently sensitive",
        is_permanently_t3=True,
    )

    with pytest.raises(TrustTierError, match="cannot demote permanently-T3"):
        demote_trust_surface(
            tmp_path,
            "src/payments",
            target_tier="T2",
            justification="attempted relaxation",
        )

    # Refusal must be enforcing: the persisted tier is unchanged.
    assert get_trust_surface_by_surface(tmp_path, "src/payments")["tier"] == "T3"


def test_trust_tier_validation_blocks_under_declared_session(tmp_path: Path) -> None:
    register_trust_surface(
        tmp_path,
        surface_id="src/payments",
        tier="T3",
        justification="payments are sensitive",
        is_permanently_t3=True,
    )

    blocked = validate_trust_tier(
        tmp_path,
        declared_tier="T2",
        touched_paths=["src/payments/checkout.py"],
        write_evidence=False,
    )
    assert blocked["ok"] is False
    assert {b["kind"] for b in blocked["blockers"]} == {"declared-tier-too-low"}

    allowed = validate_trust_tier(
        tmp_path,
        declared_tier="T3",
        touched_paths=["src/payments/checkout.py"],
        write_evidence=False,
    )
    assert allowed["ok"] is True
    assert allowed["blockers"] == []


# ---------------------------------------------------------------------------
# Observability listening-window behavior: stale / threshold-violating windows
# produce blockers and a draft-only verdict.
# ---------------------------------------------------------------------------


def test_evaluate_listening_window_blocks_stale_threshold_violating_window(tmp_path: Path) -> None:
    create_listening_window(
        tmp_path,
        wave=1,
        belief_id="belief-checkout-faster",
        opens_at="2026-01-01T00:00:00Z",
        closes_at="2026-01-02T00:00:00Z",
        expected_outcome="p95 latency drops below 200ms",
        metric_name="p95_latency_ms",
        threshold=200,
        direction="down",
        minimum_cohort=100,
    )
    open_listening_window(tmp_path, 1, now="2026-01-01T00:05:00Z")
    # A reading that violates the threshold (300 > 200, direction=down) and an
    # undersized cohort, recorded long before the evaluation `now` (stale).
    record_window_reading(
        tmp_path,
        1,
        value=300,
        cohort=10,
        ts="2026-01-01T01:00:00Z",
    )

    result = evaluate_listening_window(
        tmp_path,
        1,
        now="2026-01-01T18:00:00Z",
        stale_after_hours=4.0,
        write_evidence=False,
    )

    assert result["ok"] is False
    assert result["status"] == "FAIL"
    kinds = {b["kind"] for b in result["blockers"]}
    assert "stale-primary-reading" in kinds
    assert "sub-threshold-cohort" in kinds
    # The metric did not clear its threshold, and the verdict is draft-only --
    # observability proposes, it never decides authoritatively.
    assert result["metric"]["threshold_met"] is False
    assert result["proposed_verdict"] == "ITERATE"
    assert result["draft_only"] is True
    assert result["decision_owner"] == "PO"


def test_evaluate_listening_window_keeps_a_clean_threshold_meeting_window(tmp_path: Path) -> None:
    create_listening_window(
        tmp_path,
        wave=2,
        belief_id="belief-conversion-up",
        opens_at="2026-01-01T00:00:00Z",
        closes_at="2026-01-02T00:00:00Z",
        expected_outcome="conversion rises above 5%",
        metric_name="conversion_pct",
        threshold=5,
        direction="up",
        minimum_cohort=10,
    )
    open_listening_window(tmp_path, 2, now="2026-01-01T00:05:00Z")
    record_window_reading(
        tmp_path,
        2,
        value=7.5,
        cohort=250,
        ts="2026-01-01T11:30:00Z",
    )

    result = evaluate_listening_window(
        tmp_path,
        2,
        now="2026-01-01T12:00:00Z",
        stale_after_hours=4.0,
        write_evidence=False,
    )

    assert result["ok"] is True
    assert result["blockers"] == []
    assert result["metric"]["threshold_met"] is True
    assert result["proposed_verdict"] == "KEEP"
    # Even a passing window stays a draft -- it must not claim final authority.
    assert result["draft_only"] is True


# ---------------------------------------------------------------------------
# WorktreeSync snapshot behavior: persist, query by branch/commit, latest.
# ---------------------------------------------------------------------------


@_requires_git
def test_worktree_snapshot_query_by_branch_commit_and_latest(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)

    first = take_worktree_snapshot(repo)
    second = take_worktree_snapshot(repo)

    by_branch = list_worktree_snapshots(repo, branch=first["branch"])
    by_commit = list_worktree_snapshots(repo, commit_sha=first["commit_sha"])
    assert {row["id"] for row in by_branch} == {first["id"], second["id"]}
    assert {row["id"] for row in by_commit} == {first["id"], second["id"]}

    # Filtering by a non-existent branch yields nothing (real filtering, not a
    # pass-through).
    assert list_worktree_snapshots(repo, branch="does-not-exist") == []

    # Latest is deterministic even for same-second snapshots (seq tiebreaker).
    latest = latest_worktree_snapshot(repo)
    assert latest is not None
    assert latest["id"] == second["id"]
    assert second["seq"] == first["seq"] + 1


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(
        repo,
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "user.name=SignalOS Test",
        "commit",
        "-m",
        "initial",
    )
    return repo


def _git(repo: Path, *args: str) -> str:
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=20,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return (proc.stdout or "").strip()
