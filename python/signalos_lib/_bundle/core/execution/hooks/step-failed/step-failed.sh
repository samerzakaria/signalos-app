#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — step.failed hook.
#
# Fired by the dispatcher when a step exits non-zero or raises. Writes one
# `step.failed` event to the session journal via journal-append.sh.
#
# Arg contract:
#   --session-id <id>   required
#   --step-id <id>      required
#   --reason <text>     required — HARD ERROR if empty. Describes the failure.
#   --exit-code <int>   required — MUST be non-zero; exit 1 if it is 0 (contradiction).
#   --tool <name>       optional
#   --ts <iso8601>      optional — defaults to now, UTC, Z-suffix
#
# Side effects:
#   - Appends { schema_version:1, ts, type:"step.failed", session_id, step_id,
#               reason, exit_code, ... } to the session journal.
#   - Updates .signalos/sessions/INDEX.jsonl.
#
# Exit codes:
#   0 — event written
#   1 — validation error (missing/empty reason, zero exit-code, bad timestamp, etc.)
#   2 — IO error from journal-append.sh
#   3 — redaction failure inside journal-append.sh

set -euo pipefail

SESSION_ID=""
STEP_ID=""
REASON=""
EXIT_CODE=""
TOOL=""
TS=""

# Track whether --reason was explicitly passed (even if empty) so empty-value
# is an obvious hard error rather than "forgot to pass it".
REASON_PASSED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-id) SESSION_ID="$2"; shift 2 ;;
    --step-id)    STEP_ID="$2";    shift 2 ;;
    --reason)     REASON="$2"; REASON_PASSED=1; shift 2 ;;
    --exit-code)  EXIT_CODE="$2";  shift 2 ;;
    --tool)       TOOL="$2";       shift 2 ;;
    --ts)         TS="$2";         shift 2 ;;
    --help)
      sed -n '1,28p' "$0"
      exit 0
      ;;
    *) echo "step-failed.sh: unknown arg: $1" >&2; exit 1 ;;
  esac
done

[[ -n "$SESSION_ID" ]] || { echo "step-failed.sh: --session-id required" >&2; exit 1; }
[[ -n "$STEP_ID"    ]] || { echo "step-failed.sh: --step-id required"    >&2; exit 1; }

# Empty --reason is a hard error. Passing --reason with an empty string is
# also a hard error: we refuse to log a failure without saying why.
if [[ $REASON_PASSED -eq 0 ]]; then
  echo "step-failed.sh: --reason required" >&2
  exit 1
fi
if [[ -z "${REASON//[[:space:]]/}" ]]; then
  echo "step-failed.sh: --reason must not be empty or whitespace-only" >&2
  exit 1
fi

[[ -n "$EXIT_CODE" ]] || { echo "step-failed.sh: --exit-code required" >&2; exit 1; }
if ! [[ "$EXIT_CODE" =~ ^-?[0-9]+$ ]]; then
  echo "step-failed.sh: --exit-code must be an integer (got: $EXIT_CODE)" >&2
  exit 1
fi
if [[ "$EXIT_CODE" == "0" ]]; then
  echo "step-failed.sh: --exit-code must be non-zero for a failure event" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "step-failed.sh: jq not on PATH (required)" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "step-failed.sh: python3 not on PATH (required)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LIB_DIR="${HOOKS_ROOT}/_lib"
APPENDER="${LIB_DIR}/journal-append.sh"

if [[ ! -x "$APPENDER" ]]; then
  echo "step-failed.sh: journal-append.sh not executable at $APPENDER" >&2
  exit 1
fi

if [[ -z "$TS" ]]; then
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi
if ! [[ "$TS" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$ ]]; then
  echo "step-failed.sh: --ts must be ISO-8601 UTC with trailing Z (got: $TS)" >&2
  exit 1
fi

EVENT_JSON="$(jq -nc \
  --argjson schema 1 \
  --arg ts "$TS" \
  --arg type "step.failed" \
  --arg sid "$SESSION_ID" \
  --arg step "$STEP_ID" \
  --arg reason "$REASON" \
  --arg ec "$EXIT_CODE" \
  --arg tool "$TOOL" \
  '{
    schema_version: $schema,
    ts: $ts,
    type: $type,
    session_id: $sid,
    step_id: $step,
    reason: $reason,
    exit_code: ($ec | tonumber)
  }
  + (if $tool != "" then {tool: $tool} else {} end)
  ')"

"$APPENDER" --session-id "$SESSION_ID" --event "$EVENT_JSON"
