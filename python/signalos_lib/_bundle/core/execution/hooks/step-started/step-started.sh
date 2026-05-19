#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — step.started hook.
#
# Fired by the dispatcher (core/tool-adapters/dispatcher/, Agent D)
# immediately before a step begins executing. Writes one `step.started`
# event to the session journal via journal-append.sh.
#
# Arg contract (caller MUST pass every required arg):
#   --session-id <id>          required — target session
#   --step-id <id>             required — step identifier (usually a UUID or slug)
#   --actor <name>             required — who runs this step (agent role, emitter name, "human")
#   --intent <text>            required — short description of what the step is trying to do
#   --parent-step-id <id>      optional — if this step was spawned by another
#   --tool <name>              optional — which tool/emitter fired it
#   --ts <iso8601>             optional — override timestamp (defaults to now, UTC, Z-suffix)
#
# Side effects:
#   - Appends { schema_version:1, ts, type:"step.started", session_id, step_id, ... }
#     to .signalos/sessions/<session-id>/journal.jsonl
#   - Updates .signalos/sessions/INDEX.jsonl
#   - Sources _lib/step-pause-check.sh when present (Agent B territory).
#     If the pause check decides to pause, this script still returns 0
#     because the journal write is the definition of "started". The
#     pause gate runs BEFORE step body execution, not before the event.
#
# Exit codes:
#   0 — event written (and pause check, if present, consulted)
#   1 — validation error (missing arg, bad timestamp, missing jq/python3)
#   2 — IO error from journal-append.sh
#   3 — redaction failure inside journal-append.sh

set -euo pipefail

SESSION_ID=""
STEP_ID=""
ACTOR=""
INTENT=""
PARENT_STEP_ID=""
TOOL=""
TS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-id)      SESSION_ID="$2";      shift 2 ;;
    --step-id)         STEP_ID="$2";         shift 2 ;;
    --actor)           ACTOR="$2";           shift 2 ;;
    --intent)          INTENT="$2";          shift 2 ;;
    --parent-step-id)  PARENT_STEP_ID="$2";  shift 2 ;;
    --tool)            TOOL="$2";            shift 2 ;;
    --ts)              TS="$2";              shift 2 ;;
    --help)
      sed -n '1,30p' "$0"
      exit 0
      ;;
    *) echo "step-started.sh: unknown arg: $1" >&2; exit 1 ;;
  esac
done

# --- Required-arg validation --------------------------------------------------
[[ -n "$SESSION_ID" ]] || { echo "step-started.sh: --session-id required" >&2; exit 1; }
[[ -n "$STEP_ID"    ]] || { echo "step-started.sh: --step-id required"    >&2; exit 1; }
[[ -n "$ACTOR"      ]] || { echo "step-started.sh: --actor required"      >&2; exit 1; }
[[ -n "$INTENT"     ]] || { echo "step-started.sh: --intent required"     >&2; exit 1; }

# --- Fail-closed on jq / python3 missing --------------------------------------
if ! command -v jq >/dev/null 2>&1; then
  echo "step-started.sh: jq not on PATH (required)" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "step-started.sh: python3 not on PATH (required)" >&2
  exit 1
fi

# --- Resolve paths ------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LIB_DIR="${HOOKS_ROOT}/_lib"
APPENDER="${LIB_DIR}/journal-append.sh"

if [[ ! -x "$APPENDER" ]]; then
  echo "step-started.sh: journal-append.sh not executable at $APPENDER" >&2
  exit 1
fi

# --- Timestamp --------------------------------------------------------------
if [[ -z "$TS" ]]; then
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi
# Sanity-check the timestamp is ISO-8601 with trailing Z.
if ! [[ "$TS" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$ ]]; then
  echo "step-started.sh: --ts must be ISO-8601 UTC with trailing Z (got: $TS)" >&2
  exit 1
fi

# --- Build JSON payload via jq -nc (no hand-rolled concatenation) -------------
EVENT_JSON="$(jq -nc \
  --argjson schema 1 \
  --arg ts "$TS" \
  --arg type "step.started" \
  --arg sid "$SESSION_ID" \
  --arg step "$STEP_ID" \
  --arg actor "$ACTOR" \
  --arg intent "$INTENT" \
  --arg parent "$PARENT_STEP_ID" \
  --arg tool "$TOOL" \
  '{
    schema_version: $schema,
    ts: $ts,
    type: $type,
    session_id: $sid,
    step_id: $step,
    actor: $actor,
    intent: $intent
  }
  + (if $parent != "" then {parent_step_id: $parent} else {} end)
  + (if $tool   != "" then {tool: $tool}            else {} end)
  ')"

# --- Step-pause check (Agent B territory; fail-open if absent in W1.1 dev) ---
# step-pause-check.sh is env-var driven, not flag-driven. Export the
# required inputs here so an editor emitter or the W1.2 harness can
# fire step-started.sh without the caller having to know the pause
# library's contract. The step-spec is optional — if the caller did
# not export SIGNALOS_PLAN_STEP_JSON, skip the pause check (no pause
# is the default per CONSTITUTION §4).
PAUSE_CHECK="${LIB_DIR}/step-pause-check.sh"
if [[ -f "$PAUSE_CHECK" && -n "${SIGNALOS_PLAN_STEP_JSON:-}" ]]; then
  export SIGNALOS_SESSION_ID="$SESSION_ID"
  export SIGNALOS_STEP_ID="$STEP_ID"
  # shellcheck disable=SC1090
  source "$PAUSE_CHECK" || true
fi

# --- Append the event ---------------------------------------------------------
"$APPENDER" --session-id "$SESSION_ID" --event "$EVENT_JSON"
