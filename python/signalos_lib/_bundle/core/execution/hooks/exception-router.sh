#!/usr/bin/env bash
# exception-router.sh — Centralized Exception Routing Engine
#
# Called by any agent or hook when a HARD STOP, HARD REFUSE, or gate block occurs.
# Routes the exception to the correct human based on the exception type,
# logs to AUDIT_TRAIL.jsonl, and writes a structured exception file.
#
# Usage:
#   exception-router.sh --type <type> --source <agent/hook> --surface <path> \
#     --message <description> [--wave <id>] [--repo-root <path>]
#
# Exception types:
#   t3-discovery     — T3 surface discovered mid-build → routes to PE
#   scope-violation  — work exceeds session scope → routes to PO
#   gate-block       — gate artifact missing or unsigned → routes to gate signer
#   test-failure     — test suite failed, blocking merge → routes to QA
#   release-block    — deploy precondition unmet → routes to DevOps
#   sod-violation    — segregation of duties breached → routes to PE + DevOps
#   constitution-violation — Constitution rule breached → routes to PO + PE
#   tier-mismatch    — PR touches surface above declared tier → routes to PE
#   stale-metrics    — Observability data stale or missing → routes to PO
#   unowned-agent    — agent run without named human owner → routes to PO
#
# Exit: 0 (exception logged and routed), 1 (invalid input)

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
EXCEPTION_TYPE=""
SOURCE=""
SURFACE=""
MESSAGE=""
WAVE_ID=""

RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Routing table: exception type → responsible human role(s)
declare -A ROUTE_TABLE=(
  ["t3-discovery"]="PE"
  ["scope-violation"]="PO"
  ["gate-block"]="GATE_SIGNER"
  ["test-failure"]="QA"
  ["release-block"]="DevOps"
  ["sod-violation"]="PE,DevOps"
  ["constitution-violation"]="PO,PE"
  ["tier-mismatch"]="PE"
  ["stale-metrics"]="PO"
  ["unowned-agent"]="PO"
  ["agent-write-blocked"]="PE"
)

# Severity: determines whether the Wave halts or continues with warning
declare -A SEVERITY_TABLE=(
  ["t3-discovery"]="HALT"
  ["scope-violation"]="HALT"
  ["gate-block"]="HALT"
  ["test-failure"]="BLOCK_MERGE"
  ["release-block"]="BLOCK_DEPLOY"
  ["sod-violation"]="HALT"
  ["constitution-violation"]="HALT"
  ["tier-mismatch"]="BLOCK_MERGE"
  ["stale-metrics"]="WARN"
  ["unowned-agent"]="WARN"
  ["agent-write-blocked"]="WARN"
)

usage() {
  cat <<EOF
Usage: exception-router.sh --type <type> --source <agent/hook> --surface <path> \\
         --message <description> [--wave <id>] [--repo-root <path>]

Exception types:
  t3-discovery, scope-violation, gate-block, test-failure, release-block,
  sod-violation, constitution-violation, tier-mismatch, stale-metrics, unowned-agent

EOF
  exit "${1:-0}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --type)       EXCEPTION_TYPE="$2"; shift 2 ;;
      --source)     SOURCE="$2"; shift 2 ;;
      --surface)    SURFACE="$2"; shift 2 ;;
      --message)    MESSAGE="$2"; shift 2 ;;
      --wave)       WAVE_ID="$2"; shift 2 ;;
      --repo-root)  REPO_ROOT="$2"; shift 2 ;;
      --help)       usage 0 ;;
      *)            echo "Error: Unknown argument: $1" >&2; usage 1 ;;
    esac
  done
}

validate_inputs() {
  local errors=0

  if [[ -z "$EXCEPTION_TYPE" ]]; then
    echo "Error: --type is required" >&2
    errors=$((errors + 1))
  elif [[ -z "${ROUTE_TABLE[$EXCEPTION_TYPE]+x}" ]]; then
    echo "Error: Unknown exception type: $EXCEPTION_TYPE" >&2
    echo "Valid types: ${!ROUTE_TABLE[*]}" >&2
    errors=$((errors + 1))
  fi

  if [[ -z "$SOURCE" ]]; then
    echo "Error: --source is required" >&2
    errors=$((errors + 1))
  fi

  if [[ -z "$MESSAGE" ]]; then
    echo "Error: --message is required" >&2
    errors=$((errors + 1))
  fi

  if [[ $errors -gt 0 ]]; then
    exit 1
  fi
}

resolve_gate_signer() {
  # For gate-block exceptions, determine the specific gate signer
  # by checking which gate artifact is missing/unsigned
  local surface_basename
  surface_basename="$(basename "$SURFACE")"
  case "$surface_basename" in
    BELIEF*) echo "PO" ;;
    EXPECTATION*) echo "PO" ;;
    PLAN*) echo "PE" ;;
    DESIGN*) echo "PO" ;;
    TRUST*) echo "PE" ;;
    QUALITY*) echo "QA" ;;
    DEBRIEF*) echo "PO" ;;
    *) echo "PO,PE" ;;
  esac
}

write_exception_file() {
  local exception_dir="${REPO_ROOT}/.signalos/exceptions"
  mkdir -p "$exception_dir"

  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local exception_id
  exception_id="EXC-$(date -u +%Y%m%d%H%M%S)-$$"
  local route="${ROUTE_TABLE[$EXCEPTION_TYPE]}"

  # Resolve GATE_SIGNER to actual role
  if [[ "$route" == "GATE_SIGNER" ]]; then
    route=$(resolve_gate_signer)
  fi

  local severity="${SEVERITY_TABLE[$EXCEPTION_TYPE]}"

  local exception_file="${exception_dir}/${exception_id}.md"
  cat > "$exception_file" <<EXCEOF
# Exception — ${exception_id}

- **Type:** ${EXCEPTION_TYPE}
- **Severity:** ${severity}
- **Source:** ${SOURCE}
- **Surface:** ${SURFACE:-"N/A"}
- **Wave:** ${WAVE_ID:-"unknown"}
- **Timestamp:** ${ts}
- **Routed to:** ${route}
- **Status:** OPEN

## Description

${MESSAGE}

## Required resolution

$(case "$severity" in
  HALT) echo "**WAVE HALTED.** The named human(s) above must resolve this exception before any further agent work proceeds. Run the originating command again after resolution." ;;
  BLOCK_MERGE) echo "**MERGE BLOCKED.** The PR cannot be merged until this exception is resolved. The named human(s) must review and either fix the issue or re-tier the work." ;;
  BLOCK_DEPLOY) echo "**DEPLOY BLOCKED.** The release cannot proceed until this exception is resolved. DevOps must verify preconditions." ;;
  WARN) echo "**WARNING.** Work may continue, but the named human(s) should address this before the next gate." ;;
esac)

## Resolution log

| Date | Action | By | Notes |
|---|---|---|---|
| | | | |

EXCEOF

  echo "$exception_file"
}

append_audit_entry() {
  local audit_log="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"
  mkdir -p "$(dirname "$audit_log")"

  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local route="${ROUTE_TABLE[$EXCEPTION_TYPE]}"
  if [[ "$route" == "GATE_SIGNER" ]]; then
    route=$(resolve_gate_signer)
  fi
  local severity="${SEVERITY_TABLE[$EXCEPTION_TYPE]}"

  echo "{\"ts\":\"$ts\",\"actor\":\"$SOURCE\",\"role\":\"system\",\"action\":\"exception\",\"type\":\"$EXCEPTION_TYPE\",\"severity\":\"$severity\",\"surface\":\"$SURFACE\",\"wave\":\"$WAVE_ID\",\"routed_to\":\"$route\",\"message\":\"$MESSAGE\"}" >> "$audit_log"
}

main() {
  parse_args "$@"
  validate_inputs

  local route="${ROUTE_TABLE[$EXCEPTION_TYPE]}"
  if [[ "$route" == "GATE_SIGNER" ]]; then
    route=$(resolve_gate_signer)
  fi
  local severity="${SEVERITY_TABLE[$EXCEPTION_TYPE]}"

  # Write exception file
  local exception_file
  exception_file=$(write_exception_file)

  # Append to audit trail
  append_audit_entry

  # Print routing notification
  echo ""
  case "$severity" in
    HALT)
      echo -e "${RED}EXCEPTION [${EXCEPTION_TYPE}] — WAVE HALTED${NC}"
      ;;
    BLOCK_MERGE)
      echo -e "${RED}EXCEPTION [${EXCEPTION_TYPE}] — MERGE BLOCKED${NC}"
      ;;
    BLOCK_DEPLOY)
      echo -e "${RED}EXCEPTION [${EXCEPTION_TYPE}] — DEPLOY BLOCKED${NC}"
      ;;
    WARN)
      echo -e "${YELLOW}EXCEPTION [${EXCEPTION_TYPE}] — WARNING${NC}"
      ;;
  esac

  echo -e "  Source:  ${SOURCE}"
  echo -e "  Surface: ${SURFACE:-"N/A"}"
  echo -e "  Wave:    ${WAVE_ID:-"unknown"}"
  echo -e "  Routed:  ${BLUE}${route}${NC}"
  echo -e "  File:    ${exception_file}"
  echo ""
  echo "  ${MESSAGE}"
  echo ""

  # Exit code reflects severity — callers MUST honour this
  case "$severity" in
    HALT)         exit 1 ;;   # wave halted — caller must stop
    BLOCK_MERGE)  exit 2 ;;   # merge blocked — caller must not merge
    BLOCK_DEPLOY) exit 3 ;;   # deploy blocked — caller must not deploy
    WARN)         exit 0 ;;   # advisory only — caller may continue
    *)            exit 1 ;;   # unknown severity — fail-closed
  esac
}

main "$@"
