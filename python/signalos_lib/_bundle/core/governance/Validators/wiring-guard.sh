#!/usr/bin/env bash
# wiring-guard.sh — Structural wiring integrity validator
# AMD-CORE-009 · SignalOS Core v2.1
# W7: added C11 (signal-qa gate wiring) and C12 (QUALITY_CHECK.md at Gate 5)
# W10: added C16 (signal-cso security wiring)
# W11: added C17 (velocity primitives wiring)
# W15: added C21 (second-opinion + investigate wiring)
# W16: added C22 (phase debt protocol + open debt scan)
#
# Checks that every command, skill, hook, rule, and emitter is consistently
# registered across all config surfaces. Run in CI before proof scenarios
# and at session-start.
#
# Exit 0 = all checks pass. Exit 1 = one or more failures (exit 0 in --warn mode).
# --quiet:     suppress table, only print failures
# --warn:      report failures as warnings but always exit 0 (advisory mode, W-3)
# --check <ID>: run only the named check (e.g. --check C11). Exits 0/1 per result.
# --repo-root <path>: repo root (default: git rev-parse --show-toplevel)

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
QUIET=false
WARN_MODE=false   # W-3: --warn exits 0 even on failures
CHECK_FILTER=""   # W7: when set via --check, run only this check ID
FAILURES=0
CHECKS_PASSED=0
CHECKS_TOTAL=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet|-q)       QUIET=true; shift ;;
    --warn)           WARN_MODE=true; shift ;;   # W-3: advisory mode
    --repo-root)      REPO_ROOT="$2"; shift 2 ;;
    --check)          CHECK_FILTER="$2"; shift 2 ;;   # W7: run only named check
    --help|-h)
      echo "Usage: wiring-guard.sh [--quiet] [--warn] [--check <ID>] [--repo-root <path>]"
      echo "  Exit 0 = all checks pass. Exit 1 = one or more failures."
      echo "  --warn:       always exit 0; report failures as warnings (advisory mode)."
      echo "  --check <ID>: run only the named check (e.g. C11, C12)."
      exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1 ;;
  esac
done

# Resolved paths
COMMANDS_JSON="${REPO_ROOT}/core/tool-adapters/_shared/commands.json"
SKILLS_JSON="${REPO_ROOT}/core/tool-adapters/_shared/skills.json"
HOOKS_JSON="${REPO_ROOT}/core/tool-adapters/_shared/hooks.json"
CLAUDE_HOOKS_JSON="${REPO_ROOT}/integrations/hooks/claude-hooks.json"
CURSOR_HOOKS_JSON="${REPO_ROOT}/integrations/hooks/cursor-hooks.json"
COMMANDS_DIR="${REPO_ROOT}/core/execution/commands"
SKILLS_DIR="${REPO_ROOT}/core/execution/skills"
HOOKS_DIR="${REPO_ROOT}/core/execution/hooks"
EMITTERS_DIR="${REPO_ROOT}/core/tool-adapters/emitters"
RULES_DIR="${REPO_ROOT}/integrations/rules"
DISPATCHER_SCRIPT="${REPO_ROOT}/core/tool-adapters/dispatcher/session-hook-dispatch.sh"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_fail() {
  local check="$1" msg="$2"
  FAILURES=$((FAILURES + 1))
  echo -e "  ${RED}✗ [${check}] ${msg}${NC}"
}

_pass() {
  local check="$1" msg="$2"
  CHECKS_PASSED=$((CHECKS_PASSED + 1))
  if [[ "$QUIET" == false ]]; then
    echo -e "  ${GREEN}✓ [${check}] ${msg}${NC}"
  fi
}

_section() {
  local title="$1"
  CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
  if [[ "$QUIET" == false ]]; then
    echo ""
    echo -e "${BLUE}── ${title}${NC}"
  fi
}

_require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo -e "${RED}✗ wiring-guard requires jq but it is not installed.${NC}" >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# CHECK 1 — commands registry → disk
# Every source path in commands.json must exist on disk
# ---------------------------------------------------------------------------

check_commands_registry_to_disk() {
  _section "CHECK 1: commands registry → disk"
  local check_id="C1"
  if [[ ! -f "$COMMANDS_JSON" ]]; then
    _fail "$check_id" "commands.json not found at $COMMANDS_JSON"
    return
  fi
  local sources
  sources=$(jq -r '.[].source' "$COMMANDS_JSON" 2>/dev/null)
  local missing=0
  while IFS= read -r src; do
    [[ -z "$src" ]] && continue
    local full="${REPO_ROOT}/${src}"
    if [[ -f "$full" ]]; then
      _pass "$check_id" "exists: $src"
    else
      _fail "$check_id" "MISSING: $src (registered in commands.json)"
      missing=$((missing + 1))
    fi
  done <<< "$sources"
  if [[ $missing -eq 0 ]]; then
    _pass "$check_id" "all command sources exist"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 2 — commands disk → registry
# Every .md in core/execution/commands/ must be in commands.json
# ---------------------------------------------------------------------------

check_commands_disk_to_registry() {
  _section "CHECK 2: commands disk → registry"
  local check_id="C2"
  if [[ ! -d "$COMMANDS_DIR" ]]; then
    _fail "$check_id" "commands dir not found: $COMMANDS_DIR"
    return
  fi
  if [[ ! -f "$COMMANDS_JSON" ]]; then
    _fail "$check_id" "commands.json not found"
    return
  fi
  local registered_sources
  registered_sources=$(jq -r '.[].source' "$COMMANDS_JSON" 2>/dev/null)
  local unregistered=0
  while IFS= read -r md_file; do
    [[ -z "$md_file" ]] && continue
    local rel_path
    rel_path="${md_file#"${REPO_ROOT}/"}"
    if echo "$registered_sources" | grep -qF "$rel_path"; then
      _pass "$check_id" "registered: $rel_path"
    else
      _fail "$check_id" "UNREGISTERED on disk: $rel_path (not in commands.json)"
      unregistered=$((unregistered + 1))
    fi
  done < <(find "$COMMANDS_DIR" -name "*.md" -maxdepth 1 2>/dev/null)
  if [[ $unregistered -eq 0 ]]; then
    _pass "$check_id" "all command docs registered"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 3 — commands ↔ rules
# Each command name in commands.json → integrations/rules/<name>.mdc must exist
# Each .mdc (except signalos-preamble.mdc) → command must exist in registry
# ---------------------------------------------------------------------------

check_commands_rules() {
  _section "CHECK 3: commands ↔ rules"
  local check_id="C3"
  if [[ ! -f "$COMMANDS_JSON" ]]; then
    _fail "$check_id" "commands.json not found"
    return
  fi
  if [[ ! -d "$RULES_DIR" ]]; then
    _fail "$check_id" "rules dir not found: $RULES_DIR"
    return
  fi

  # commands.json → rules
  local names
  names=$(jq -r '.[].name' "$COMMANDS_JSON" 2>/dev/null)
  local missing_rules=0
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    local rule_file="${RULES_DIR}/${name}.mdc"
    if [[ -f "$rule_file" ]]; then
      _pass "$check_id" "rule exists: ${name}.mdc"
    else
      _fail "$check_id" "MISSING rule: ${name}.mdc (command '$name' has no .mdc)"
      missing_rules=$((missing_rules + 1))
    fi
  done <<< "$names"

  # rules → commands.json
  local unregistered=0
  while IFS= read -r mdc_file; do
    [[ -z "$mdc_file" ]] && continue
    local mdc_name
    mdc_name=$(basename "$mdc_file" .mdc)
    # Skip preamble — it's a meta-rule, not a command
    [[ "$mdc_name" == "signalos-preamble" ]] && continue
    if echo "$names" | grep -qxF "$mdc_name"; then
      _pass "$check_id" "command exists for rule: ${mdc_name}.mdc"
    else
      _fail "$check_id" "ORPHAN rule: ${mdc_name}.mdc (no command in registry)"
      unregistered=$((unregistered + 1))
    fi
  done < <(find "$RULES_DIR" -name "*.mdc" -maxdepth 1 2>/dev/null)

  if [[ $missing_rules -eq 0 && $unregistered -eq 0 ]]; then
    _pass "$check_id" "commands and rules are in sync"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 4 — skills registry → disk
# Every source path in skills.json must exist on disk
# ---------------------------------------------------------------------------

check_skills_registry_to_disk() {
  _section "CHECK 4: skills registry → disk"
  local check_id="C4"
  if [[ ! -f "$SKILLS_JSON" ]]; then
    _fail "$check_id" "skills.json not found at $SKILLS_JSON"
    return
  fi
  local sources
  sources=$(jq -r '.[].source' "$SKILLS_JSON" 2>/dev/null)
  local missing=0
  while IFS= read -r src; do
    [[ -z "$src" ]] && continue
    local full="${REPO_ROOT}/${src}"
    if [[ -f "$full" ]]; then
      _pass "$check_id" "exists: $src"
    else
      _fail "$check_id" "MISSING: $src (registered in skills.json)"
      missing=$((missing + 1))
    fi
  done <<< "$sources"
  if [[ $missing -eq 0 ]]; then
    _pass "$check_id" "all skill sources exist"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 5 — skills disk → registry
# Each SKILL.md directly in core/execution/skills/*/ → must be in skills.json
# ---------------------------------------------------------------------------

check_skills_disk_to_registry() {
  _section "CHECK 5: skills disk → registry"
  local check_id="C5"
  if [[ ! -d "$SKILLS_DIR" ]]; then
    _fail "$check_id" "skills dir not found: $SKILLS_DIR"
    return
  fi
  if [[ ! -f "$SKILLS_JSON" ]]; then
    _fail "$check_id" "skills.json not found"
    return
  fi
  local registered_sources
  registered_sources=$(jq -r '.[].source' "$SKILLS_JSON" 2>/dev/null)
  local unregistered=0
  # Only look one level deep: core/execution/skills/*/SKILL.md
  while IFS= read -r skill_file; do
    [[ -z "$skill_file" ]] && continue
    local rel_path
    rel_path="${skill_file#"${REPO_ROOT}/"}"
    if echo "$registered_sources" | grep -qF "$rel_path"; then
      _pass "$check_id" "registered: $rel_path"
    else
      _fail "$check_id" "UNREGISTERED skill: $rel_path (not in skills.json)"
      unregistered=$((unregistered + 1))
    fi
  done < <(find "$SKILLS_DIR" -name "SKILL.md" -maxdepth 2 2>/dev/null)
  if [[ $unregistered -eq 0 ]]; then
    _pass "$check_id" "all skill docs registered"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 6 — hooks registry ↔ disk
# hooks.json sources must exist; each event dir → must be in hooks.json;
# each command in claude-hooks.json and cursor-hooks.json must exist
# ---------------------------------------------------------------------------

check_hooks() {
  _section "CHECK 6: hooks registry ↔ disk"
  local check_id="C6"

  # hooks.json sources must exist
  if [[ -f "$HOOKS_JSON" ]]; then
    local hook_sources
    hook_sources=$(jq -r '.[].source' "$HOOKS_JSON" 2>/dev/null || true)
    while IFS= read -r src; do
      [[ -z "$src" ]] && continue
      local full="${REPO_ROOT}/${src}"
      if [[ -d "$full" || -f "$full" ]]; then
        _pass "$check_id" "hook source exists: $src"
      else
        _fail "$check_id" "MISSING hook source: $src (in hooks.json)"
      fi
    done <<< "$hook_sources"
  else
    _fail "$check_id" "hooks.json not found at $HOOKS_JSON"
  fi

  # Each event dir in core/execution/hooks/ (excl _lib) → must be in hooks.json
  if [[ -d "$HOOKS_DIR" && -f "$HOOKS_JSON" ]]; then
    local registered_events
    registered_events=$(jq -r '.[].event' "$HOOKS_JSON" 2>/dev/null || true)
    while IFS= read -r hook_dir; do
      [[ -z "$hook_dir" ]] && continue
      local dir_name
      dir_name=$(basename "$hook_dir")
      [[ "$dir_name" == "_lib" ]] && continue
      [[ "$dir_name" == "exception-router.sh" ]] && continue
      # Only check subdirectories, not files
      [[ ! -d "$hook_dir" ]] && continue
      if echo "$registered_events" | grep -qxF "$dir_name"; then
        _pass "$check_id" "hook event registered: $dir_name"
      else
        _fail "$check_id" "UNREGISTERED hook event dir: $dir_name (not in hooks.json)"
      fi
    done < <(find "$HOOKS_DIR" -maxdepth 1 -mindepth 1 2>/dev/null)
  fi

  # claude-hooks.json commands must exist
  if [[ -f "$CLAUDE_HOOKS_JSON" ]]; then
    local claude_commands
    claude_commands=$(jq -r '.. | objects | .command? // empty' "$CLAUDE_HOOKS_JSON" 2>/dev/null || true)
    while IFS= read -r cmd; do
      [[ -z "$cmd" ]] && continue
      # Resolve ${CLAUDE_PLUGIN_ROOT} placeholder to REPO_ROOT for checking
      local resolved="${cmd/\$\{CLAUDE_PLUGIN_ROOT\}/$REPO_ROOT}"
      if [[ -f "$resolved" || -d "$resolved" ]]; then
        _pass "$check_id" "claude hook command exists: $cmd"
      else
        _fail "$check_id" "MISSING claude hook command: $cmd"
      fi
    done <<< "$claude_commands"
  fi

  # cursor-hooks.json commands must exist
  if [[ -f "$CURSOR_HOOKS_JSON" ]]; then
    local cursor_commands
    cursor_commands=$(jq -r '.. | objects | .command? // empty' "$CURSOR_HOOKS_JSON" 2>/dev/null || true)
    while IFS= read -r cmd; do
      [[ -z "$cmd" ]] && continue
      # Cursor uses ${CURSOR_PLUGIN_ROOT} rather than ${CLAUDE_PLUGIN_ROOT}
      local resolved="${cmd/\$\{CURSOR_PLUGIN_ROOT\}/$REPO_ROOT}"
      resolved="${resolved/\$\{CLAUDE_PLUGIN_ROOT\}/$REPO_ROOT}"
      if [[ -f "$resolved" || -d "$resolved" ]]; then
        _pass "$check_id" "cursor hook command exists: $cmd"
      else
        _fail "$check_id" "MISSING cursor hook command: $cmd"
      fi
    done <<< "$cursor_commands"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 7 — emitters ↔ dispatcher
# Each dir in core/tool-adapters/emitters/ must have emit.sh;
# each emitter referenced in session-hook-dispatch.sh must have dir
# ---------------------------------------------------------------------------

check_emitters() {
  _section "CHECK 7: emitters ↔ dispatcher"
  local check_id="C7"

  if [[ ! -d "$EMITTERS_DIR" ]]; then
    _fail "$check_id" "emitters dir not found: $EMITTERS_DIR"
    return
  fi

  # Each emitter dir must have emit.sh
  local missing_emit=0
  while IFS= read -r emitter_dir; do
    [[ -z "$emitter_dir" ]] && continue
    [[ ! -d "$emitter_dir" ]] && continue
    local emitter_name
    emitter_name=$(basename "$emitter_dir")
    local emit_script="${emitter_dir}/emit.sh"
    if [[ -f "$emit_script" ]]; then
      _pass "$check_id" "emit.sh exists: $emitter_name"
    else
      _fail "$check_id" "MISSING emit.sh in emitter: $emitter_name"
      missing_emit=$((missing_emit + 1))
    fi
  done < <(find "$EMITTERS_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null)

  # Each emitter referenced in session-hook-dispatch.sh must have a dir
  if [[ -f "$DISPATCHER_SCRIPT" ]]; then
    # Extract emitter names from the dispatcher (tool detection block)
    local referenced_emitters
    referenced_emitters=$(grep -oE '(emitters/[a-z0-9-]+)' "$DISPATCHER_SCRIPT" 2>/dev/null | \
      sed 's|emitters/||' | sort -u || true)
    local missing_dir=0
    while IFS= read -r ename; do
      [[ -z "$ename" ]] && continue
      local edir="${EMITTERS_DIR}/${ename}"
      if [[ -d "$edir" ]]; then
        _pass "$check_id" "emitter dir exists for dispatcher reference: $ename"
      else
        _fail "$check_id" "MISSING emitter dir: $ename (referenced in dispatcher)"
        missing_dir=$((missing_dir + 1))
      fi
    done <<< "$referenced_emitters"
    if [[ $missing_dir -eq 0 && -n "$referenced_emitters" ]]; then
      _pass "$check_id" "all dispatcher emitter references have dirs"
    fi
  else
    _fail "$check_id" "dispatcher script not found: $DISPATCHER_SCRIPT"
  fi
}


# ---------------------------------------------------------------------------
# Check 10: every product namespace has Constitution + Soul Document (W4.2)
# ---------------------------------------------------------------------------

check_product_namespaces() {
  _section "CHECK 10: product namespaces — Constitution + Soul Document"
  local check_id="C10"
  local products_dir="${REPO_ROOT}/.signalos/products"

  if [[ ! -d "$products_dir" ]]; then
    _pass "$check_id" "no product namespaces registered (single-product repo)"
    return
  fi

  local found=0
  local repo_gov="${REPO_ROOT}/core/governance/Governance"

  while IFS= read -r product_dir; do
    [[ -z "$product_dir" ]] && continue
    [[ ! -d "$product_dir" ]] && continue
    local pid
    pid=$(basename "$product_dir")
    found=$((found + 1))

    # Constitution: product-level OR repo-level
    if [[ -f "${product_dir}/CONSTITUTION.md" ]] || [[ -f "${repo_gov}/CONSTITUTION.md" ]]; then
      _pass "$check_id" "constitution found for product: $pid"
    else
      _fail "$check_id" "MISSING constitution for product: $pid (checked ${product_dir}/CONSTITUTION.md and ${repo_gov}/CONSTITUTION.md)"
    fi

    # Soul Document: product-level OR repo-level
    if [[ -f "${product_dir}/SOUL-DOCUMENT.md" ]] || [[ -f "${repo_gov}/SOUL-DOCUMENT.md" ]]; then
      _pass "$check_id" "soul document found for product: $pid"
    else
      _fail "$check_id" "MISSING soul document for product: $pid (checked ${product_dir}/SOUL-DOCUMENT.md and ${repo_gov}/SOUL-DOCUMENT.md)"
    fi
  done < <(find "$products_dir" -maxdepth 1 -mindepth 1 -type d 2>/dev/null)

  if [[ $found -eq 0 ]]; then
    _pass "$check_id" "no product subdirs found in .signalos/products/"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 11 — signal-qa gate wiring (W7)
# Verifies that all W7 QA components are in place before /signal-qa can gate:
#   (a) signal-qa.md command exists
#   (b) signal-qa-only.md command exists
#   (c) quality-check-template.md template exists
#   (d) cli/signalos_lib/browser.py (SBrowser) exists
#   (e) cli/signalos_lib/qa_runner.py exists
#   (f) QA scenarios directory exists
# ---------------------------------------------------------------------------

check_signal_qa_gate_wiring() {
  _section "CHECK 11: signal-qa gate wiring (W7)"
  local check_id="C11"

  local required_files=(
    "core/execution/commands/signal-qa.md"
    "core/execution/commands/signal-qa-only.md"
    "core/governance/Templates/quality-check-template.md"
    "cli/signalos_lib/browser.py"
    "cli/signalos_lib/qa_runner.py"
  )

  local all_ok=true
  for rel in "${required_files[@]}"; do
    local full="${REPO_ROOT}/${rel}"
    if [[ -f "$full" ]]; then
      _pass "$check_id" "exists: $rel"
    else
      _fail "$check_id" "MISSING: $rel (required for /signal-qa gate wiring)"
      all_ok=false
    fi
  done

  local scenarios_dir="${REPO_ROOT}/core/governance/QA/scenarios"
  if [[ -d "$scenarios_dir" ]]; then
    local scenario_count
    scenario_count=$(find "$scenarios_dir" -name "*.yaml" -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$scenario_count" -gt 0 ]]; then
      _pass "$check_id" "QA scenarios dir exists with $scenario_count scenario(s)"
    else
      _pass "$check_id" "QA scenarios dir exists (0 scenarios — add *.yaml before running /signal-qa)"
    fi
  else
    _fail "$check_id" "MISSING: core/governance/QA/scenarios/ — create dir and add *.yaml scenarios"
    all_ok=false
  fi

  if [[ "$all_ok" == true ]]; then
    _pass "$check_id" "all signal-qa gate wiring components present"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 12 — QUALITY_CHECK.md present and signed at Gate 5 (W7)
# ---------------------------------------------------------------------------

check_quality_check_gate5() {
  _section "CHECK 12: QUALITY_CHECK.md at Gate 5 (W7)"
  local check_id="C12"

  local qc_path="${REPO_ROOT}/core/governance/QUALITY_CHECK.md"
  local template_path="${REPO_ROOT}/core/governance/Templates/quality-check-template.md"

  if [[ -f "$template_path" ]]; then
    _pass "$check_id" "quality-check-template.md exists"
  else
    _fail "$check_id" "MISSING: core/governance/Templates/quality-check-template.md"
  fi

  if [[ ! -f "$qc_path" ]]; then
    if [[ "$WARN_MODE" == true ]]; then
      echo -e "  ${YELLOW}⚠ [${check_id}] QUALITY_CHECK.md not found — run /signal-qa to generate it (advisory)${NC}"
    else
      _fail "$check_id" "MISSING: core/governance/QUALITY_CHECK.md — run /signal-qa to generate it"
    fi
    return
  fi

  _pass "$check_id" "QUALITY_CHECK.md exists"

  local sig_line
  sig_line=$(grep -m1 "Signed (QA):" "$qc_path" 2>/dev/null || true)
  if [[ -z "$sig_line" ]]; then
    if [[ "$WARN_MODE" == true ]]; then
      echo -e "  ${YELLOW}⚠ [${check_id}] QUALITY_CHECK.md has no 'Signed (QA):' line (advisory)${NC}"
    else
      _fail "$check_id" "QUALITY_CHECK.md has no 'Signed (QA):' line — may be corrupted or truncated"
    fi
    return
  fi

  local sig_value
  sig_value=$(echo "$sig_line" | sed 's/.*Signed (QA):[[:space:]]*//' | sed 's/[[:space:]]*\*Date:.*$//' | tr -d '_[:space:]')

  if [[ -n "$sig_value" ]]; then
    _pass "$check_id" "QUALITY_CHECK.md is QA-signed — Gate 5 entry clear"
  else
    if [[ "$WARN_MODE" == true ]]; then
      echo -e "  ${YELLOW}⚠ [${check_id}] QUALITY_CHECK.md exists but QA signature is blank — sign before merge${NC}"
    else
      _fail "$check_id" "QUALITY_CHECK.md exists but QA signature is blank — Gate 5 blocked until QA signs"
    fi
  fi
}

# ---------------------------------------------------------------------------
# Main — run all checks + print summary
# ---------------------------------------------------------------------------

_run_check() {
  local id="$1" fn="$2"
  if [[ -z "$CHECK_FILTER" || "$CHECK_FILTER" == "$id" ]]; then
    "$fn"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 16 — signal-cso security wiring (W10)
# ---------------------------------------------------------------------------

check_signal_cso_wired() {
  _section "CHECK 16: signal-cso security wiring (W10)"
  local check_id="C16"
  local required_files=(
    "core/execution/commands/signal-cso.md"
    "cli/signalos_lib/security.py"
    "integrations/rules/signal-cso.mdc"
  )
  local all_ok=true
  for rel in "${required_files[@]}"; do
    local full="${REPO_ROOT}/${rel}"
    if [[ -f "$full" ]]; then
      _pass "$check_id" "exists: $rel"
    else
      _fail "$check_id" "MISSING: $rel (required for signal-cso wiring)"
      all_ok=false
    fi
  done
  if [[ "$all_ok" == true ]]; then
    _pass "$check_id" "all signal-cso wiring components present"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 17 — velocity primitives wiring (W11)
# ---------------------------------------------------------------------------

check_velocity_wired() {
  _section "CHECK 17: velocity primitives wiring (W11)"
  local check_id="C17"
  local required_files=(
    "core/execution/commands/signal-autoplan.md"
    "core/execution/commands/signal-context-restore.md"
    "cli/signalos_lib/velocity.py"
  )
  local all_ok=true
  for rel in "${required_files[@]}"; do
    local full="${REPO_ROOT}/${rel}"
    if [[ -f "$full" ]]; then
      _pass "$check_id" "exists: $rel"
    else
      _fail "$check_id" "MISSING: $rel (required for velocity primitives wiring)"
      all_ok=false
    fi
  done
  if [[ "$all_ok" == true ]]; then
    _pass "$check_id" "all velocity primitives wiring components present"
  fi
}

check_brain_hook_wired() {
  local idx="$REPO_ROOT/.signalos/brain/index.jsonl"
  local hook="$REPO_ROOT/core/execution/hooks/_lib/brain-auto-ingest.sh"
  if [[ ! -f "$idx" ]]; then
    _pass "C15" "brain index not yet created — no check needed"
    return
  fi
  if [[ -f "$hook" ]]; then
    _pass "C15" "brain-auto-ingest.sh present"
  else
    _warn "C15" "brain index exists but core/execution/hooks/_lib/brain-auto-ingest.sh is missing"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 18 — post-deploy lifecycle wiring (W12)
# ---------------------------------------------------------------------------

check_deploy_wired() {
  _section "CHECK 18: post-deploy lifecycle wiring (W12)"
  local check_id="C18"
  local required_files=(
    "core/execution/commands/signal-setup-deploy.md"
    "core/execution/commands/signal-land-deploy.md"
    "core/execution/commands/signal-canary-deploy.md"
    "core/execution/commands/signal-benchmark.md"
    "cli/signalos_lib/deploy.py"
    "integrations/rules/signal-setup-deploy.mdc"
    "integrations/rules/signal-land-deploy.mdc"
    "integrations/rules/signal-canary-deploy.mdc"
    "integrations/rules/signal-benchmark.mdc"
  )
  local all_ok=true
  for rel in "${required_files[@]}"; do
    local full="${REPO_ROOT}/${rel}"
    if [[ -f "$full" ]]; then
      _pass "$check_id" "exists: $rel"
    else
      _fail "$check_id" "MISSING: $rel (required for post-deploy lifecycle wiring)"
      all_ok=false
    fi
  done
  if [[ "$all_ok" == true ]]; then
    _pass "$check_id" "all post-deploy lifecycle wiring components present"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 19 — DevEx + global retro wiring (W13)
# ---------------------------------------------------------------------------

check_devex_wired() {
  _section "CHECK 19: DevEx + global retro wiring (W13)"
  local check_id="C19"
  local required_files=(
    "core/execution/commands/signal-devex-plan.md"
    "core/execution/commands/signal-devex.md"
    "core/execution/commands/signal-retro-global.md"
    "cli/signalos_lib/devex.py"
    "integrations/rules/signal-devex-plan.mdc"
    "integrations/rules/signal-devex.mdc"
    "integrations/rules/signal-retro-global.mdc"
  )
  local all_ok=true
  for rel in "${required_files[@]}"; do
    local full="${REPO_ROOT}/${rel}"
    if [[ -f "$full" ]]; then
      _pass "$check_id" "exists: $rel"
    else
      _fail "$check_id" "MISSING: $rel (required for devex wiring)"
      all_ok=false
    fi
  done
  if [[ "$all_ok" == true ]]; then
    _pass "$check_id" "all devex wiring components present"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 20 — safety gates wiring (W14)
# ---------------------------------------------------------------------------

check_safety_wired() {
  _section "CHECK 20: safety gates wiring (W14)"
  local check_id="C20"
  local required_files=(
    "core/execution/commands/signal-careful.md"
    "core/execution/commands/signal-freeze.md"
    "core/execution/commands/signal-guard.md"
    "core/execution/commands/signal-unfreeze.md"
    "cli/signalos_lib/safety.py"
    "integrations/rules/signal-careful.mdc"
    "integrations/rules/signal-freeze.mdc"
    "integrations/rules/signal-guard.mdc"
    "integrations/rules/signal-unfreeze.mdc"
  )
  local all_ok=true
  for rel in "${required_files[@]}"; do
    local full="${REPO_ROOT}/${rel}"
    if [[ -f "$full" ]]; then
      _pass "$check_id" "exists: $rel"
    else
      _fail "$check_id" "MISSING: $rel (required for safety gates wiring)"
      all_ok=false
    fi
  done
  if [[ "$all_ok" == true ]]; then
    _pass "$check_id" "all safety gates wiring components present"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 21 — second-opinion + investigate wiring (W15)
# ---------------------------------------------------------------------------

check_second_opinion_wired() {
  _section "CHECK 21: second-opinion + investigate wiring (W15)"
  local check_id="C21"
  local required_files=(
    "core/execution/commands/signal-second-opinion.md"
    "core/execution/commands/signal-investigate.md"
    "cli/signalos_lib/second_opinion.py"
    "cli/signalos_lib/investigate.py"
    "integrations/rules/signal-second-opinion.mdc"
    "integrations/rules/signal-investigate.mdc"
  )
  local all_ok=true
  for rel in "${required_files[@]}"; do
    local full="${REPO_ROOT}/${rel}"
    if [[ -f "$full" ]]; then
      _pass "$check_id" "exists: $rel"
    else
      _fail "$check_id" "MISSING: $rel (required for second-opinion + investigate wiring)"
      all_ok=false
    fi
  done
  if [[ "$all_ok" == true ]]; then
    _pass "$check_id" "all second-opinion + investigate wiring components present"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 22 — phase debt protocol + open debt scan (W16)
# ---------------------------------------------------------------------------

check_phase_debt_protocol() {
  _section "CHECK 22: phase debt protocol + open debt scan (W16)"
  local check_id="C22"
  local protocol_file="${REPO_ROOT}/core/governance/Governance/PHASE-DEBT-PROTOCOL.md"
  local dna_file="${REPO_ROOT}/core/governance/Governance/DECISION-DNA.md"
  local plan_file="${REPO_ROOT}/core/execution/PLAN.tasks.yaml"

  # 1. Protocol file must exist
  if [[ ! -f "$protocol_file" ]]; then
    _fail "$check_id" "PHASE-DEBT-PROTOCOL.md missing at core/governance/Governance/PHASE-DEBT-PROTOCOL.md"
    return
  fi
  _pass "$check_id" "PHASE-DEBT-PROTOCOL.md present"

  # 2. If no DECISION-DNA yet, nothing to scan
  if [[ ! -f "$dna_file" ]]; then
    _pass "$check_id" "DECISION-DNA.md not yet present — no open debt to scan"
    return
  fi

  # 3. Per the PHASE-DEBT-PROTOCOL, build only fails when debt is *overdue*
  #    (current_wave - debt_wave >= 2) AND not closed. Scope `closed: true`
  #    matches to within each PHASE-DEBT block (block ends at the next
  #    `event:` line) so unrelated `closed: true` markers in other decision
  #    types don't offset the count.
  local current_wave overdue_waves overdue_count
  current_wave=""
  if [[ -f "$plan_file" ]]; then
    current_wave=$(awk '/^wave:/ { sub(/^wave:[[:space:]]*/, ""); gsub(/["[:space:]]/, ""); print; exit }' "$plan_file" 2>/dev/null || true)
  fi

  overdue_waves=$(awk -v cur="$current_wave" '
    function wave_num(s,   t) { t = s; sub(/^[Ww]/, "", t); return t + 0 }
    /^event:[[:space:]]*PHASE-DEBT/ {
      flush()
      in_debt = 1
      debt_wave = ""
      closed = 0
      next
    }
    in_debt && /^event:/ { flush(); in_debt = 0 }
    in_debt && /^wave:[[:space:]]*[Ww]?[0-9]+/ {
      sub(/^wave:[[:space:]]*/, "")
      gsub(/["[:space:]]/, "")
      debt_wave = $0
    }
    in_debt && /^closed:[[:space:]]*true/ { closed = 1 }
    function flush() {
      if (in_debt && closed == 0 && cur != "" && debt_wave != "") {
        if (wave_num(cur) - wave_num(debt_wave) >= 2) print debt_wave
      }
    }
    END { flush() }
  ' "$dna_file" 2>/dev/null || true)

  if [[ -z "$overdue_waves" ]]; then
    _pass "$check_id" "no overdue phase-debt entries (deadline: 2 waves after debt was recorded)"
  else
    overdue_count=$(printf '%s\n' "$overdue_waves" | grep -c .)
    _fail "$check_id" "$overdue_count overdue phase-debt entr(ies) (debt waves: $(printf '%s ' $overdue_waves)) — close them or escalate per PHASE-DEBT-PROTOCOL.md"
  fi
}

main() {
  _require_jq

  if [[ "$QUIET" == false ]]; then
    echo -e "${BLUE}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║  SignalOS · Wiring Guard (AMD-CORE-009+W7+W8+W9+W10+W11+W12+W13+W14+W15+W16) ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════╝${NC}"
    echo "  Repo root: $REPO_ROOT"
    [[ -n "$CHECK_FILTER" ]] && echo "  Filter: --check $CHECK_FILTER"
  fi

  _run_check "C1"  check_commands_registry_to_disk
  _run_check "C2"  check_commands_disk_to_registry
  _run_check "C3"  check_commands_rules
  _run_check "C4"  check_skills_registry_to_disk
  _run_check "C5"  check_skills_disk_to_registry
  _run_check "C6"  check_hooks
  _run_check "C7"  check_emitters
  _run_check "C10" check_product_namespaces
  _run_check "C11" check_signal_qa_gate_wiring
  _run_check "C12" check_quality_check_gate5
  _run_check "C13" check_po_brief_signed
  _run_check "C14" check_design_review_before_note
  _run_check "C15" check_brain_hook_wired
  _run_check "C16" check_signal_cso_wired
  _run_check "C17" check_velocity_wired
  _run_check "C18" check_deploy_wired
  _run_check "C19" check_devex_wired
  _run_check "C20" check_safety_wired
  _run_check "C21" check_second_opinion_wired
  _run_check "C22" check_phase_debt_protocol
  _run_check "C23" check_intent_to_cli_subparsers

  echo ""
  if [[ $FAILURES -gt 0 ]]; then
    if [[ "$WARN_MODE" == true ]]; then
      echo -e "${YELLOW}⚠ Wiring Guard (--warn): $FAILURES check(s) failed (advisory — not blocking).${NC}"
      exit 0
    else
      echo -e "${RED}✗ Wiring Guard: $FAILURES check(s) FAILED. Fix before proceeding.${NC}"
      exit 1
    fi
  else
    echo -e "${GREEN}✓ Wiring Guard: all checks passed (${CHECKS_PASSED} items verified).${NC}"
    exit 0
  fi
}

# ---------------------------------------------------------------------------
# CHECK 13 — PO_BRIEF.md signed before DESIGN_NOTE (W8)
# ---------------------------------------------------------------------------

check_po_brief_signed() {
  _section "CHECK 13: PO_BRIEF.md signed before DESIGN_NOTE (W8)"
  local check_id="C13"

  local design_note="${REPO_ROOT}/core/strategy/DESIGN_NOTE.md"
  local po_brief="${REPO_ROOT}/core/strategy/PO_BRIEF.md"

  if [[ ! -f "$design_note" ]]; then
    _pass "$check_id" "DESIGN_NOTE.md not yet present — no gate check needed"
    return
  fi

  if [[ ! -f "$po_brief" ]]; then
    _fail "$check_id" "DESIGN_NOTE.md exists but PO_BRIEF.md is missing (core/strategy/PO_BRIEF.md)"
    return
  fi

  if grep -q "^signer:" "$po_brief" 2>/dev/null && ! grep "^signer:" "$po_brief" | grep -qi "DRAFT"; then
    _pass "$check_id" "PO_BRIEF.md is signed — DESIGN_NOTE gate satisfied"
  else
    _fail "$check_id" "PO_BRIEF.md exists but has no valid signature (run: signalos sign G3)"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 14 — design-review run before DESIGN_NOTE signed (W8)
# ---------------------------------------------------------------------------

check_design_review_before_note() {
  _section "CHECK 14: design-review run before DESIGN_NOTE signed (W8)"
  local check_id="C14"

  local design_note="${REPO_ROOT}/core/strategy/DESIGN_NOTE.md"
  local reviews_dir="${REPO_ROOT}/.signalos/design/reviews"

  if [[ ! -f "$design_note" ]]; then
    _pass "$check_id" "DESIGN_NOTE.md not yet present — no review check needed"
    return
  fi

  if ! grep -q "^signer:" "$design_note" 2>/dev/null; then
    _pass "$check_id" "DESIGN_NOTE.md unsigned — review check deferred"
    return
  fi

  if find "$reviews_dir" -name "*-review.json" 2>/dev/null | grep -q .; then
    _pass "$check_id" "design review file found — C14 satisfied"
  else
    _fail "$check_id" "DESIGN_NOTE.md is signed but no design review found — run /signal-design-review"
  fi
}

# ---------------------------------------------------------------------------
# CHECK 23 — every `signalos <cmd>` registered in intent.py must have a
# matching subparser registered in cli.py. Catches the failure mode where
# a new command is wired to an intent route but the dispatcher never
# learns about it (W7 signal-qa shipped that way for one cycle).
# ---------------------------------------------------------------------------

check_intent_to_cli_subparsers() {
  _section "CHECK 23: intent.py → cli.py subparser registration"
  local check_id="C23"
  local intent="${REPO_ROOT}/cli/signalos_lib/intent.py"
  local cli="${REPO_ROOT}/cli/signalos_lib/cli.py"

  if [[ ! -f "$intent" ]]; then
    _pass "$check_id" "intent.py not present — skipped"
    return
  fi
  if [[ ! -f "$cli" ]]; then
    _fail "$check_id" "cli.py not found — cannot verify subparser registration"
    return
  fi

  # Extract `"command": "signalos <cmd>"` lines from intent.py and pull
  # the first token after `signalos `. Quote-handling is forgiving so
  # both single- and double-quoted forms work.
  local intent_cmds
  intent_cmds="$(python3 -c "
import re, sys
with open(r'''${intent}''', 'r', encoding='utf-8') as fh:
    src = fh.read()
seen = []
for m in re.finditer(r'''[\"']command[\"']\s*:\s*[\"']signalos\s+([a-zA-Z0-9_-]+)''', src):
    name = m.group(1)
    if name not in seen:
        seen.append(name)
print('\n'.join(seen))
" 2>/dev/null || true)"

  local missing=0
  while IFS= read -r cmd; do
    cmd="${cmd%$'\r'}"   # strip trailing CR (Windows-emitted by python print)
    [[ -z "$cmd" ]] && continue
    # Match either   sub.add_parser("<cmd>"   or   sub.add_parser('<cmd>'
    if grep -qE "add_parser\(['\"]${cmd}['\"]" "$cli"; then
      _pass "$check_id" "intent → cli subparser: ${cmd}"
    else
      _fail "$check_id" "intent route 'signalos ${cmd}' has no add_parser(\"${cmd}\") in cli.py"
      missing=$((missing + 1))
    fi
  done <<< "$intent_cmds"

  if [[ $missing -eq 0 ]]; then
    _pass "$check_id" "all intent routes have matching subparsers"
  fi
}

main
