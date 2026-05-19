#!/usr/bin/env bash
# ownership-guard.sh — Machine-Enforced Agent Ownership Validator
#
# Enforces Preamble Law 4: "Every agent has a named human owner."
# Validates that every agent invocation in the current session/wave
# has a bound human owner. Unowned agent output is non-binding.
#
# Enforcement points:
#   1. Session start  — block if any recent unowned invocations exist
#   2. Pre-commit     — block commits from unowned agent sessions
#   3. Pre-merge      — block PRs with unowned agent work
#   4. On-demand      — audit all agent invocations in audit trail
#
# Owner binding:
#   - SIGNALOS_OWNER env var must be set (full name, not alias)
#   - Or: .signalos/session-owner file in worktree
#   - Or: git config user.name (fallback, with warning)
#
# Exit: 0 = all owned, 1 = unowned detected, 2 = warning only

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
COMMAND=""
WAVE_ID=""
WARN_MODE=false
AUDIT_LOG=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
  cat <<EOF
Usage: ownership-guard.sh <command> [options]

Commands:
  check    Validate current session has a named owner
  audit    Scan audit trail for unowned invocations
  bind     --owner <name>  Bind current session to a named owner

Options:
  --wave <id>       Filter to specific wave
  --repo-root <path> Repository root
  --warn            Warning mode (exit 2 instead of 1)
  --help            Show this help

EOF
  exit "${1:-0}"
}

parse_args() {
  COMMAND="${1:-}"
  shift || true

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --wave)      WAVE_ID="$2"; shift 2 ;;
      --repo-root) REPO_ROOT="$2"; shift 2 ;;
      --warn)      WARN_MODE=true; shift ;;
      --owner)     OWNER_NAME="$2"; shift 2 ;;
      --help)      usage 0 ;;
      *)           echo "Error: Unknown argument: $1" >&2; usage 1 ;;
    esac
  done

  AUDIT_LOG="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"
}

# ─── RESOLVE OWNER ──────────────────────────────────────────────────────────

resolve_owner() {
  local owner=""

  # Priority 1: SIGNALOS_OWNER environment variable
  if [[ -n "${SIGNALOS_OWNER:-}" ]]; then
    owner="$SIGNALOS_OWNER"
    echo "$owner"
    return 0
  fi

  # Priority 2: .signalos/session-owner file
  local owner_file="${REPO_ROOT}/.signalos/session-owner"
  if [[ -f "$owner_file" ]]; then
    owner=$(cat "$owner_file" | xargs)
    if [[ -n "$owner" ]]; then
      echo "$owner"
      return 0
    fi
  fi

  # Priority 3: git config user.name (fallback with warning)
  if git rev-parse --git-dir >/dev/null 2>&1; then
    owner=$(git config user.name 2>/dev/null || echo "")
    if [[ -n "$owner" ]]; then
      echo -e "  ${YELLOW}⚠ Using git config user.name as owner (set SIGNALOS_OWNER for explicit binding)${NC}" >&2
      echo "$owner"
      return 0
    fi
  fi

  # No owner found
  echo ""
  return 1
}

# Validate owner name (not placeholder, not empty, not "TBD")
validate_owner_name() {
  local name="$1"

  if [[ -z "$name" ]]; then
    return 1
  fi

  # Reject placeholders
  local lower
  lower=$(echo "$name" | tr '[:upper:]' '[:lower:]')
  case "$lower" in
    tbd|none|unknown|""|system|bot|agent|ai|auto)
      return 1
      ;;
  esac

  # Must contain at least one space (first + last name) or be a known alias
  if [[ "$name" != *" "* ]] && [[ ${#name} -lt 3 ]]; then
    return 1
  fi

  return 0
}

# ─── CHECK: validate current session ───────────────────────────────────────

cmd_check() {
  echo -e "${BLUE}Ownership Guard — checking current session${NC}"
  echo ""

  local owner
  owner=$(resolve_owner 2>&1) || true

  # Separate stderr messages from the resolved name
  local resolved_name
  resolved_name=$(resolve_owner 2>/dev/null) || resolved_name=""

  if [[ -z "$resolved_name" ]]; then
    echo -e "  ${RED}✗ NO OWNER BOUND${NC}"
    echo ""
    echo "  Preamble Law 4: Every agent has a named human owner."
    echo "  Unowned agent output is non-binding."
    echo ""
    echo "  To bind an owner:"
    echo "    export SIGNALOS_OWNER=\"Jane Smith\""
    echo "    # or: ownership-guard.sh bind --owner \"Jane Smith\""
    echo "    # or: echo \"Jane Smith\" > .signalos/session-owner"

    # Route exception
    local exc_router="${REPO_ROOT}/core/execution/hooks/exception-router.sh"
    if [[ -x "$exc_router" ]]; then
      bash "$exc_router" --type "unowned-agent" --source "ownership-guard" \
        --surface "session" --wave "${WAVE_ID:-unknown}" \
        --message "Agent session has no named human owner" \
        --repo-root "$REPO_ROOT" 2>/dev/null || true
    fi

    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  if ! validate_owner_name "$resolved_name"; then
    echo -e "  ${RED}✗ INVALID OWNER: '$resolved_name'${NC}"
    echo "  Owner must be a real human name (not TBD, system, agent, etc.)"

    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  echo -e "  ${GREEN}✓ Session owner: $resolved_name${NC}"

  # Write to audit log
  mkdir -p "$(dirname "$AUDIT_LOG")"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "{\"ts\":\"$ts\",\"actor\":\"ownership-guard\",\"role\":\"system\",\"action\":\"ownership-check\",\"wave\":\"${WAVE_ID:-unknown}\",\"detail\":\"owner=$resolved_name\"}" >> "$AUDIT_LOG"

  exit 0
}

# ─── AUDIT: scan trail for unowned invocations ────────────────────────────

cmd_audit() {
  echo -e "${BLUE}Ownership Guard — auditing trail for unowned invocations${NC}"
  echo ""

  if [[ ! -f "$AUDIT_LOG" ]]; then
    echo "  No audit trail found."
    exit 0
  fi

  local filter=".actor"
  if [[ -n "$WAVE_ID" ]]; then
    filter="select(.wave==\"$WAVE_ID\") | .actor"
  fi

  # Find entries where actor is system/empty/TBD
  local unowned_count=0
  local total_count=0

  while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue
    total_count=$((total_count + 1))

    local actor role
    actor=$(echo "$entry" | jq -r '.actor // ""' 2>/dev/null)
    role=$(echo "$entry" | jq -r '.role // ""' 2>/dev/null)

    # System actors are OK (hooks, validators)
    if [[ "$role" == "system" ]]; then
      continue
    fi

    if ! validate_owner_name "$actor"; then
      unowned_count=$((unowned_count + 1))
      local action ts
      action=$(echo "$entry" | jq -r '.action // "?"' 2>/dev/null)
      ts=$(echo "$entry" | jq -r '.ts // "?"' 2>/dev/null)
      echo -e "  ${RED}✗ Unowned: $ts — $action (actor: '$actor')${NC}"
    fi
  done < <(if [[ -n "$WAVE_ID" ]]; then
    jq -c "select(.wave==\"$WAVE_ID\")" "$AUDIT_LOG" 2>/dev/null
  else
    cat "$AUDIT_LOG"
  fi)

  echo ""
  echo "  Scanned $total_count entries: $unowned_count unowned"

  if [[ $unowned_count -gt 0 ]]; then
    echo -e "  ${YELLOW}⚠ Unowned agent output is non-binding per Preamble Law 4.${NC}"
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  else
    echo -e "  ${GREEN}✓ All entries have valid owners.${NC}"
    exit 0
  fi
}

# ─── BIND: set owner for current session ───────────────────────────────────

cmd_bind() {
  local name="${OWNER_NAME:-}"

  if [[ -z "$name" ]]; then
    echo "Error: --owner <name> is required for bind" >&2
    exit 1
  fi

  if ! validate_owner_name "$name"; then
    echo -e "${RED}Error: Invalid owner name: '$name'${NC}" >&2
    echo "  Must be a real human name (not TBD, system, agent, etc.)" >&2
    exit 1
  fi

  mkdir -p "${REPO_ROOT}/.signalos"
  echo "$name" > "${REPO_ROOT}/.signalos/session-owner"

  echo -e "${GREEN}✓ Session owner bound: $name${NC}"
  echo "  File: ${REPO_ROOT}/.signalos/session-owner"
  echo "  Alternatively: export SIGNALOS_OWNER=\"$name\""

  # Audit the binding
  mkdir -p "$(dirname "$AUDIT_LOG")"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "{\"ts\":\"$ts\",\"actor\":\"$name\",\"role\":\"system\",\"action\":\"ownership-bind\",\"wave\":\"${WAVE_ID:-unknown}\",\"detail\":\"owner bound via ownership-guard\"}" >> "$AUDIT_LOG"
}

# ─── MAIN ────────────────────────────────────────────────────────────────────

main() {
  OWNER_NAME=""
  parse_args "$@"

  case "$COMMAND" in
    check) cmd_check ;;
    audit) cmd_audit ;;
    bind)  cmd_bind ;;
    "")    usage 0 ;;
    *)     echo "Error: Unknown command: $COMMAND" >&2; usage 1 ;;
  esac
}

main "$@"
