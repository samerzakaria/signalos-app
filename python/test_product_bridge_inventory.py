"""Tests for Product Delivery Bridge asset inventory (Phase P0).

Validates that python/signalos_lib/product/bridge/assets.json:
  - Conforms to the inventory schema
  - Covers every public sidecar CLI command
  - Covers every product-relevant IPC route
  - Has no unknown bridge_status values
  - Has valid kind and bridge_phase on every entry
  - Covers all minimum required categories
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
ASSETS_PATH = ROOT / "signalos_lib" / "product" / "bridge" / "assets.json"

VALID_KINDS = {
    "cli-command",
    "ipc-route",
    "tauri-permission",
    "profile-manifest",
    "validator",
    "orchestrator",
    "prompt",
    "preview-runtime",
    "deploy-command",
    "handoff-surface",
    "proof-test",
    "runner",
    "signing",
    "wave-engine",
    "audit",
}

VALID_BRIDGE_STATUSES = {"used", "required", "optional", "deprecated", "blocked"}

VALID_BRIDGE_PHASES = {
    "bootstrap",
    "intent",
    "scaffold",
    "generation",
    "validation",
    "proof",
    "deploy",
    "handoff",
    "governance",
}

REQUIRED_CATEGORIES = {
    "cli-command",
    "ipc-route",
    "tauri-permission",
    "profile-manifest",
    "validator",
    "orchestrator",
    "runner",
    "signing",
    "wave-engine",
    "proof-test",
}

# ── CLI commands registered in cli.py ──────────────────────────────────────────
# Every subparser name from _build_parser() that is a public sidecar command.
EXPECTED_CLI_COMMANDS = {
    "session",
    "pause",
    "harness",
    "install",
    "verify",
    "list",
    "uninstall",
    "publish",
    "context",
    "orchestrate",
    "status",
    "sign",
    "plan",
    "intent",
    "daemon",
    "worktree",
    "health",
    "diagnose",
    "validate",
    "verify-product",
    "release-readiness",
    "hooks",
    "recover",
    "completion",
    "serve",
    "tenant",
    "campaign",
    "data",
    "search",
    "info",
    "pre-design",
    "design",
    "design-review",
    "design-html",
    "brain",
    "signal-learn",
    "signal-cso",
    "signal-autoplan",
    "signal-context-restore",
    "signal-setup-deploy",
    "signal-land-deploy",
    "signal-canary-deploy",
    "signal-benchmark",
    "signal-devex-plan",
    "signal-devex",
    "signal-retro-global",
    "signal-post-retro",
    "signal-careful",
    "signal-freeze",
    "signal-guard",
    "signal-unfreeze",
    "signal-second-opinion",
    "signal-second-opinion-record",
    "signal-investigate",
    "session-preamble",
    "init",
    "signal-qa",
    "qa",
    "signal-qa-only",
}

# ── IPC routes from main.rs generate_handler![] ───────────────────────────────
EXPECTED_IPC_ROUTES = {
    "set_workspace",
    "clear_workspace",
    "get_workspace",
    "get_workspace_status",
    "validate_workspace_write",
    "get_project_artifacts",
    "open_workspace_path",
    "write_workspace_export",
    "write_workspace_files",
    "preview_workspace_files",
    "read_workspace_file",
    "list_workspace_dir",
    "upsert_workspace_secret",
    "list_workspace_secrets",
    "reveal_workspace_secret",
    "delete_workspace_secret",
    "apply_workspace_env_diff",
    "set_identity",
    "get_identity",
    "check_role_for_gate",
    "get_git_status",
    "start_workspace_watch",
    "check_for_updates",
    "run_signal_command",
    "get_sidecar_status",
    "restart_python_sidecar",
    "get_wave_state",
    "get_gate_status",
    "sign_gate",
    "get_brain_entries",
    "add_brain_entry",
    "get_audit_trail",
    "get_cost_summary",
    "store_api_key",
    "delete_api_key",
    "has_api_key",
    "list_providers",
    "get_active_provider",
    "set_active_provider",
    "set_provider_model",
    "set_provider_pricing",
    "get_cost_state",
    "record_token_usage",
    "reset_session_cost",
    "set_monthly_budget",
    "fetch_provider_models",
    "test_provider_connection",
    "send_provider_message",
    "send_provider_message_stream",
    "probe_node",
    "start_preview",
    "stop_preview",
    "list_previews",
    "get_preview",
    "get_enforcement_state",
    "build_precheck",
    "override_rule",
    "set_rule_mode",
    "freeze_wave",
    "unfreeze_wave",
    "list_test_debt",
    "add_test_debt",
    "resolve_test_debt",
    "check_mutation_threshold",
    "check_test_first",
    "read_mutation_score",
}


@pytest.fixture(scope="module")
def inventory():
    """Load and return the parsed assets.json."""
    assert ASSETS_PATH.exists(), f"assets.json not found at {ASSETS_PATH}"
    data = json.loads(ASSETS_PATH.read_text(encoding="utf-8"))
    assert "assets" in data, "assets.json must contain an 'assets' key"
    return data


@pytest.fixture(scope="module")
def assets(inventory):
    return inventory["assets"]


# ── Schema validation ──────────────────────────────────────────────────────────

def test_assets_json_loads(inventory):
    """assets.json is valid JSON with required top-level keys."""
    assert "version" in inventory
    assert isinstance(inventory["assets"], list)
    assert len(inventory["assets"]) > 0


def test_every_entry_has_required_fields(assets):
    """Every asset entry has all required schema fields."""
    required_fields = {"id", "kind", "path", "bridge_status", "bridge_phase", "proof", "notes"}
    for entry in assets:
        missing = required_fields - set(entry.keys())
        assert not missing, f"Asset {entry.get('id', '???')} missing fields: {missing}"


def test_no_duplicate_ids(assets):
    """No two assets share the same id."""
    ids = [a["id"] for a in assets]
    dupes = [i for i in ids if ids.count(i) > 1]
    assert not dupes, f"Duplicate asset IDs: {set(dupes)}"


# ── Value validation ──────────────────────────────────────────────────────────

def test_no_unknown_bridge_status(assets):
    """No entry has bridge_status outside the allowed set."""
    for entry in assets:
        assert entry["bridge_status"] in VALID_BRIDGE_STATUSES, (
            f"Asset {entry['id']} has invalid bridge_status: {entry['bridge_status']}"
        )


def test_every_entry_has_valid_kind(assets):
    """Every entry's kind is in the allowed set."""
    for entry in assets:
        assert entry["kind"] in VALID_KINDS, (
            f"Asset {entry['id']} has invalid kind: {entry['kind']}"
        )


def test_every_entry_has_valid_bridge_phase(assets):
    """Every entry's bridge_phase is in the allowed set."""
    for entry in assets:
        assert entry["bridge_phase"] in VALID_BRIDGE_PHASES, (
            f"Asset {entry['id']} has invalid bridge_phase: {entry['bridge_phase']}"
        )


# ── Coverage assertions ───────────────────────────────────────────────────────

def test_all_cli_commands_present(assets):
    """Every public sidecar CLI command appears in the inventory."""
    cli_ids = {a["id"] for a in assets if a["kind"] == "cli-command"}
    # Build a lookup: for each expected CLI command name, check there's a
    # cli-command asset whose id contains that name (possibly prefixed with "cli-").
    for cmd_name in EXPECTED_CLI_COMMANDS:
        normalised = cmd_name.replace("-", "-")
        candidate_id = f"cli-{normalised}"
        # Check either exact match or that id starts with "cli-" and matches
        found = any(
            a["id"] == candidate_id
            or a["id"] == f"cli-{cmd_name}"
            or a["id"].startswith("cli-") and cmd_name in a["id"]
            for a in assets
            if a["kind"] == "cli-command"
        )
        assert found, (
            f"CLI command '{cmd_name}' not found in inventory. "
            f"Expected an asset with kind=cli-command matching '{cmd_name}'. "
            f"Existing CLI IDs: {sorted(cli_ids)}"
        )


def test_all_ipc_routes_present(assets):
    """Every product-relevant IPC route appears in the inventory."""
    # Collect all IPC-route asset ids, normalise to underscore form
    ipc_ids = set()
    for a in assets:
        if a["kind"] == "ipc-route":
            # normalise: "ipc-set-workspace" -> "set_workspace"
            norm = a["id"].replace("ipc-", "", 1).replace("-", "_")
            ipc_ids.add(norm)

    for route_name in EXPECTED_IPC_ROUTES:
        assert route_name in ipc_ids, (
            f"IPC route '{route_name}' not found in inventory. "
            f"Existing normalised IPC IDs: {sorted(ipc_ids)}"
        )


def test_minimum_categories_covered(assets):
    """The inventory covers all minimum required kind categories."""
    present_kinds = {a["kind"] for a in assets}
    missing = REQUIRED_CATEGORIES - present_kinds
    assert not missing, f"Missing required categories: {missing}"


def test_every_entry_has_nonempty_notes(assets):
    """Every entry has a non-empty notes field."""
    for entry in assets:
        assert entry["notes"].strip(), f"Asset {entry['id']} has empty notes"


def test_every_entry_has_nonempty_path(assets):
    """Every entry has a non-empty path field."""
    for entry in assets:
        assert entry["path"].strip(), f"Asset {entry['id']} has empty path"


def test_every_entry_has_nonempty_proof(assets):
    """Every entry has a non-empty proof field."""
    for entry in assets:
        assert entry["proof"].strip(), f"Asset {entry['id']} has empty proof"
