"""Tests for app-native Trust Tier surface lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signalos_lib.cli import main as cli_main
from signalos_lib.trust_tiers import (
    TrustTierError,
    demote_trust_surface,
    get_trust_surface_by_surface,
    list_trust_surfaces,
    load_trust_surface,
    promote_trust_surface,
    register_trust_surface,
    surface_lookup_cache_key,
    validate_trust_tier,
)


def test_permanently_t3_surface_must_start_at_t3(tmp_path: Path):
    with pytest.raises(TrustTierError, match="permanently-T3 surface must start"):
        register_trust_surface(
            tmp_path,
            surface_id="src/auth",
            tier="T2",
            justification="auth code is sensitive",
            is_permanently_t3=True,
        )


def test_permanently_t3_surface_cannot_be_demoted(tmp_path: Path):
    register_trust_surface(
        tmp_path,
        surface_id="src/auth",
        tier="T3",
        justification="auth code is permanently sensitive",
        is_permanently_t3=True,
    )

    with pytest.raises(TrustTierError, match="cannot demote permanently-T3"):
        demote_trust_surface(
            tmp_path,
            "src/auth",
            target_tier="T2",
            justification="attempted relaxation",
        )

    assert load_trust_surface(tmp_path, "src/auth")["tier"] == "T3"


def test_promote_and_demote_require_correct_direction_and_history(tmp_path: Path):
    register_trust_surface(
        tmp_path,
        surface_id="src/features",
        tier="T1",
        justification="fixture starts low risk",
    )

    with pytest.raises(TrustTierError, match="promote must move upward"):
        promote_trust_surface(
            tmp_path,
            "src/features",
            target_tier="T1",
            justification="same tier",
        )

    promoted = promote_trust_surface(
        tmp_path,
        "src/features",
        target_tier="T2",
        justification="now writes feature code",
    )
    assert promoted["tier"] == "T2"

    with pytest.raises(TrustTierError, match="demote must move downward"):
        demote_trust_surface(
            tmp_path,
            "src/features",
            target_tier="T2",
            justification="same tier",
        )

    demoted = demote_trust_surface(
        tmp_path,
        "src/features",
        target_tier="T1",
        justification="reduced to docs-only fixture",
    )
    assert demoted["tier"] == "T1"
    assert [item["action"] for item in demoted["history"]] == [
        "register",
        "promote",
        "demote",
    ]


def test_validate_blocks_declared_tier_below_touched_surface(tmp_path: Path):
    register_trust_surface(
        tmp_path,
        surface_id="src/auth",
        tier="T3",
        justification="auth code is sensitive",
        is_permanently_t3=True,
    )
    register_trust_surface(
        tmp_path,
        surface_id="tests/**",
        tier="T1",
        justification="tests can be edited at T1",
    )

    failed = validate_trust_tier(
        tmp_path,
        declared_tier="T2",
        touched_paths=["src/auth/login.py", "tests/test_login.py"],
    )
    assert failed["ok"] is False
    assert {blocker["kind"] for blocker in failed["blockers"]} == {"declared-tier-too-low"}
    assert failed["evidence_path"]
    assert Path(failed["evidence_path"]).is_file()

    passed = validate_trust_tier(
        tmp_path,
        declared_tier="T3",
        touched_paths=["src/auth/login.py", "tests/test_login.py"],
        write_evidence=False,
    )
    assert passed["ok"] is True
    assert passed["evidence_path"] is None


def test_validate_blocks_unclassified_paths_unless_allowed(tmp_path: Path):
    blocked = validate_trust_tier(
        tmp_path,
        declared_tier="T2",
        touched_paths=["src/unknown.py"],
        write_evidence=False,
    )
    assert blocked["ok"] is False
    assert blocked["blockers"][0]["kind"] == "surface-unclassified"

    allowed = validate_trust_tier(
        tmp_path,
        declared_tier="T2",
        touched_paths=["src/unknown.py"],
        allow_unclassified=True,
        write_evidence=False,
    )
    assert allowed["ok"] is True


def test_surface_lookup_is_tenant_scoped_and_nullable(tmp_path: Path):
    host = register_trust_surface(
        tmp_path,
        surface_id="src/shared",
        tier="T1",
        justification="host classification",
    )
    tenant = register_trust_surface(
        tmp_path,
        surface_id="src/shared",
        tier="T3",
        justification="tenant classification is sensitive",
        tenant_id="tenant-a",
        is_permanently_t3=True,
    )

    with pytest.raises(FileExistsError, match="already exists for tenant tenant-a"):
        register_trust_surface(
            tmp_path,
            surface_id="src/shared",
            tier="T2",
            justification="duplicate in same tenant",
            tenant_id="tenant-a",
        )

    assert host["tenant_id"] is None
    assert host["id"].startswith("stt_")
    assert tenant["id"].startswith("stt_")
    assert host["id"] != tenant["id"]
    assert host["lookup_cache_key"] == surface_lookup_cache_key("src/shared")
    assert tenant["lookup_cache_key"] == surface_lookup_cache_key(
        "src/shared",
        tenant_id="tenant-a",
    )

    assert get_trust_surface_by_surface(tmp_path, "src/shared")["tier"] == "T1"
    assert get_trust_surface_by_surface(tmp_path, "src/shared", tenant_id="tenant-a")["tier"] == "T3"
    assert get_trust_surface_by_surface(tmp_path, "src/missing", tenant_id="tenant-a") is None
    assert [item["tenant_id"] for item in list_trust_surfaces(tmp_path)] == [None]
    assert [item["tenant_id"] for item in list_trust_surfaces(tmp_path, tenant_id="tenant-a")] == [
        "tenant-a"
    ]
    assert {item["tenant_id"] for item in list_trust_surfaces(tmp_path, all_tenants=True)} == {
        None,
        "tenant-a",
    }

    failed_host = validate_trust_tier(
        tmp_path,
        declared_tier="T1",
        touched_paths=["src/shared/file.py"],
        tenant_id="tenant-a",
        write_evidence=False,
    )
    assert failed_host["tenant_id"] == "tenant-a"
    assert failed_host["ok"] is False
    assert failed_host["blockers"][0]["kind"] == "declared-tier-too-low"


def test_contract_field_limits_match_surface_inputs(tmp_path: Path):
    with pytest.raises(TrustTierError, match="400 characters"):
        register_trust_surface(
            tmp_path,
            surface_id="x" * 401,
            tier="T1",
            justification="surface id is too long",
        )

    with pytest.raises(TrustTierError, match="1000 characters"):
        register_trust_surface(
            tmp_path,
            surface_id="src/limited",
            tier="T1",
            justification="x" * 1001,
        )


def test_trust_tier_cli_lifecycle_and_validation(tmp_path: Path, capsys):
    rc = cli_main(
        [
            "signalos",
            "trust-tier",
            "surface",
            "register",
            "--repo-root",
            str(tmp_path),
            "--surface-id",
            "src/payments",
            "--tier",
            "T3",
            "--justification",
            "payments are permanently sensitive",
            "--permanent",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["surface_id"] == "src/payments"
    assert payload["is_permanently_t3"] is True

    rc = cli_main(
        [
            "signalos",
            "trust-tier",
            "validate",
            "--repo-root",
            str(tmp_path),
            "--declared-tier",
            "T2",
            "--touched",
            "src/payments/checkout.py",
            "--json",
        ]
    )
    assert rc == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["status"] == "FAIL"
    assert failed["blockers"][0]["kind"] == "declared-tier-too-low"

    rc = cli_main(
        [
            "signalos",
            "trust-tier",
            "validate",
            "--repo-root",
            str(tmp_path),
            "--declared-tier",
            "T3",
            "--touched",
            "src/payments/checkout.py",
            "--json",
        ]
    )
    assert rc == 0
    passed = json.loads(capsys.readouterr().out)
    assert passed["status"] == "PASS"

    assert list_trust_surfaces(tmp_path, tier="T3")[0]["surface_id"] == "src/payments"


def test_trust_tier_cli_tenant_scoped_get_by_surface(tmp_path: Path, capsys):
    rc = cli_main(
        [
            "signalos",
            "trust-tier",
            "surface",
            "register",
            "--repo-root",
            str(tmp_path),
            "--tenant-id",
            "tenant-a",
            "--surface-id",
            "src/payments",
            "--tier",
            "T3",
            "--justification",
            "tenant payments are sensitive",
            "--permanent",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tenant_id"] == "tenant-a"

    rc = cli_main(
        [
            "signalos",
            "trust-tier",
            "surface",
            "get-by-surface",
            "--repo-root",
            str(tmp_path),
            "--tenant-id",
            "tenant-a",
            "--surface-id",
            "src/payments",
            "--json",
        ]
    )
    assert rc == 0
    found = json.loads(capsys.readouterr().out)
    assert found["found"] is True
    assert found["surface"]["tenant_id"] == "tenant-a"

    rc = cli_main(
        [
            "signalos",
            "trust-tier",
            "surface",
            "get-by-surface",
            "--repo-root",
            str(tmp_path),
            "--tenant-id",
            "tenant-b",
            "--surface-id",
            "src/payments",
            "--json",
        ]
    )
    assert rc == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing["found"] is False
    assert missing["surface"] is None
