#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — step.completed hook.
#
# Fired by the dispatcher (core/tool-adapters/dispatcher/, Agent D) after
# a step finishes successfully. Writes one `step.completed` event to the
# session journal via journal-append.sh.
#
# Arg contract:
#   --session-id <id>          required
#   --step-id <id>             required
#   --outcome <text>           required — short outcome summary ("ok", "no-op", "retried-2x", ...)
#   --duration-ms <int>        optional — caller-measured wall time; preserved verbatim.
#                              Caller (dispatcher) is the authoritative source for
#                              this value; this hook does not synthesize it.
#   --tokens-in <int>          optional — model input tokens for this step
#   --tokens-out <int>         optional — model output tokens for this step
#   --cost-usd <float>         optional — USD cost attributed to this step
#   --tool <name>              optional — which tool/emitter fired it
#   --ts <iso8601>             optional — override timestamp (defaults to now, UTC, Z-suffix)
#
# Side effects:
#   - Appends { schema_version:1, ts, type:"step.completed", session_id, step_id, ... }
#     to .signalos/sessions/<session-id>/journal.jsonl
#   - Updates .signalos/sessions/INDEX.jsonl
#
# Exit codes:
#   0 — event written
#   1 — validation error
#   2 — IO error from journal-append.sh
#   3 — redaction failure inside journal-append.sh

set -euo pipefail

SESSION_ID=""
STEP_ID=""
OUTCOME=""
DURATION_MS=""
TOKENS_IN=""
TOKENS_OUT=""
COST_USD=""
TOOL=""
TS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-id)   SESSION_ID="$2";   shift 2 ;;
    --step-id)      STEP_ID="$2";      shift 2 ;;
    --outcome)      OUTCOME="$2";      shift 2 ;;
    --duration-ms)  DURATION_MS="$2";  shift 2 ;;
    --tokens-in)    TOKENS_IN="$2";    shift 2 ;;
    --tokens-out)   TOKENS_OUT="$2";   shift 2 ;;
    --cost-usd)     COST_USD="$2";     shift 2 ;;
    --tool)         TOOL="$2";         shift 2 ;;
    --ts)           TS="$2";           shift 2 ;;
    --help)
      sed -n '1,30p' "$0"
      exit 0
      ;;
    *) echo "step-completed.sh: unknown arg: $1" >&2; exit 1 ;;
  esac
done

[[ -n "$SESSION_ID" ]] || { echo "step-completed.sh: --session-id required" >&2; exit 1; }
[[ -n "$STEP_ID"    ]] || { echo "step-completed.sh: --step-id required"    >&2; exit 1; }
[[ -n "$OUTCOME"    ]] || { echo "step-completed.sh: --outcome required"    >&2; exit 1; }

# Numeric sanity on optional numerics.
if [[ -n "$DURATION_MS" ]] && ! [[ "$DURATION_MS" =~ ^[0-9]+$ ]]; then
  echo "step-completed.sh: --duration-ms must be a non-negative integer (got: $DURATION_MS)" >&2
  exit 1
fi
if [[ -n "$TOKENS_IN" ]] && ! [[ "$TOKENS_IN" =~ ^[0-9]+$ ]]; then
  echo "step-completed.sh: --tokens-in must be a non-negative integer (got: $TOKENS_IN)" >&2
  exit 1
fi
if [[ -n "$TOKENS_OUT" ]] && ! [[ "$TOKENS_OUT" =~ ^[0-9]+$ ]]; then
  echo "step-completed.sh: --tokens-out must be a non-negative integer (got: $TOKENS_OUT)" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "step-completed.sh: jq not on PATH (required)" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "step-completed.sh: python3 not on PATH (required)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LIB_DIR="${HOOKS_ROOT}/_lib"
APPENDER="${LIB_DIR}/journal-append.sh"

if [[ ! -x "$APPENDER" ]]; then
  echo "step-completed.sh: journal-append.sh not executable at $APPENDER" >&2
  exit 1
fi

if [[ -z "$TS" ]]; then
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi
if ! [[ "$TS" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$ ]]; then
  echo "step-completed.sh: --ts must be ISO-8601 UTC with trailing Z (got: $TS)" >&2
  exit 1
fi

# duration_ms / tokens / cost are added only when the caller supplied them.
# This keeps the event compact and preserves caller-provided values verbatim.
EVENT_JSON="$(jq -nc \
  --argjson schema 1 \
  --arg ts "$TS" \
  --arg type "step.completed" \
  --arg sid "$SESSION_ID" \
  --arg step "$STEP_ID" \
  --arg outcome "$OUTCOME" \
  --arg dur "$DURATION_MS" \
  --arg tin "$TOKENS_IN" \
  --arg tout "$TOKENS_OUT" \
  --arg cost "$COST_USD" \
  --arg tool "$TOOL" \
  '{
    schema_version: $schema,
    ts: $ts,
    type: $type,
    session_id: $sid,
    step_id: $step,
    outcome: $outcome
  }
  + (if $dur  != "" then {duration_ms: ($dur  | tonumber)} else {} end)
  + (if $tin  != "" then {tokens_in:   ($tin  | tonumber)} else {} end)
  + (if $tout != "" then {tokens_out:  ($tout | tonumber)} else {} end)
  + (if $cost != "" then {cost_usd:    ($cost | tonumber)} else {} end)
  + (if $tool != "" then {tool: $tool} else {} end)
  ')"

"$APPENDER" --session-id "$SESSION_ID" --event "$EVENT_JSON"
