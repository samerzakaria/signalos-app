#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — Step-pause check (hook library, sourced by step-started.sh).
#
# Contract (Agent A wires this in via step-started):
#   Inputs (env):
#     SIGNALOS_SESSION_ID       — required. The active session id.
#     SIGNALOS_STEP_ID          — required. Step identifier (e.g. phase-3a.build-4).
#     SIGNALOS_PLAN_STEP_JSON   — required. The step-spec as a JSON one-liner
#                                 (what the PLAN declares for this step).
#                                 Recognised fields used here:
#                                   .pause  (bool) — opt-in flag (default false)
#                                   .tier   (string) — "T1"|"T2"|"T3"
#     SIGNALOS_REPO_ROOT        — optional. Overrides auto-detected root (tests).
#
#   Side-effects:
#     On fresh pause:
#       - Writes .signalos/sessions/<sid>/pauses/<step-id>.json atomically
#         (mv from tmp) with the pending-pause record.
#       - Emits `step.paused` journal event via journal-append.sh.
#     On T3 refusal:
#       - Emits `step.aborted {cause:"t3-refuses-pause"}` via journal-append.sh.
#     On resume marker present:
#       - Fast-path, no writes, no journal events (the resume CLI already
#         emitted `step.resumed`).
#
#   Exit codes:
#     0 — no pause requested OR pause already resolved by a .resume marker.
#         Caller continues normally.
#     2 — pause is active; step is blocked until `signalos pause resume <id>`.
#         Caller (step-started.sh) must exit non-zero to halt the tool.
#     3 — pause was requested on a T3 step; Core REFUSES to pause T3 surfaces
#         (Constitution §C.3). Step is aborted. Caller must halt the tool.
#     1 — internal error (missing env, bad JSON, jq missing).
#
#   Why this is a Core invariant (not babysitter's model):
#     - Pause is OPT-IN per step-spec — default is NO pause.
#     - There is no global `/yolo`-style bypass — the only way to skip pause
#       is to omit `pause: true` from the PLAN step-spec.
#     - T3 steps CANNOT pause — a pause on a T3 surface HARD-STOPs the step
#       rather than giving the operator a rescue lever.

set -euo pipefail

: "${SIGNALOS_SESSION_ID:?step-pause-check.sh: SIGNALOS_SESSION_ID required}"
: "${SIGNALOS_STEP_ID:?step-pause-check.sh: SIGNALOS_STEP_ID required}"
: "${SIGNALOS_PLAN_STEP_JSON:?step-pause-check.sh: SIGNALOS_PLAN_STEP_JSON required}"

if ! command -v jq >/dev/null 2>&1; then
  echo "step-pause-check.sh: jq not on PATH (required)" >&2
  exit 1
fi

# Validate the spec JSON early.
if ! echo "$SIGNALOS_PLAN_STEP_JSON" | jq -e . >/dev/null 2>&1; then
  echo "step-pause-check.sh: SIGNALOS_PLAN_STEP_JSON is not valid JSON" >&2
  exit 1
fi

PAUSE_REQUESTED="$(printf '%s' "$SIGNALOS_PLAN_STEP_JSON" | jq -r '.pause // false')"

# Fast-path: no pause requested.
if [[ "$PAUSE_REQUESTED" != "true" ]]; then
  exit 0
fi

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SIGNALOS_REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
PAUSES_DIR="${REPO_ROOT}/.signalos/sessions/${SIGNALOS_SESSION_ID}/pauses"
PAUSE_FILE="${PAUSES_DIR}/${SIGNALOS_STEP_ID}.json"
RESUME_FILE="${PAUSES_DIR}/${SIGNALOS_STEP_ID}.resume"
ABORT_FILE="${PAUSES_DIR}/${SIGNALOS_STEP_ID}.abort"

TIER="$(printf '%s' "$SIGNALOS_PLAN_STEP_JSON" | jq -r '.tier // ""')"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# T3 hard-stop: pause is incompatible with permanently-T3 refusal semantics.
if [[ "$TIER" == "T3" ]]; then
  EVENT="$(jq -nc \
    --arg ts "$TS" \
    --arg step "$SIGNALOS_STEP_ID" \
    --arg cause "t3-refuses-pause" \
    '{schema_version:1, ts:$ts, type:"step.aborted", step_id:$step, cause:$cause}')"
  ( cd "$REPO_ROOT" && "${LIB_DIR}/journal-append.sh" \
      --session-id "$SIGNALOS_SESSION_ID" \
      --event "$EVENT" ) >&2 || true
  echo "step-pause-check.sh: T3 step refuses pause (step=${SIGNALOS_STEP_ID})" >&2
  exit 3
fi

# If an abort marker already exists, refuse to re-pause (step was terminated).
if [[ -f "$ABORT_FILE" ]]; then
  echo "step-pause-check.sh: step ${SIGNALOS_STEP_ID} was aborted; refusing to re-pause" >&2
  exit 3
fi

# If a resume marker already exists, we are past the pause — fast-path.
if [[ -f "$RESUME_FILE" ]]; then
  exit 0
fi

# Fresh pause: write the pending-pause file atomically and emit step.paused.
mkdir -p "$PAUSES_DIR"
if [[ ! -f "$PAUSE_FILE" ]]; then
  TMP="${PAUSE_FILE}.tmp.$$"
  jq -nc \
    --arg ts "$TS" \
    --arg sid "$SIGNALOS_SESSION_ID" \
    --arg step "$SIGNALOS_STEP_ID" \
    --arg tier "${TIER:-unknown}" \
    '{paused_at:$ts, session_id:$sid, step_id:$step, tier:$tier, status:"pending"}' \
    > "$TMP"
  mv "$TMP" "$PAUSE_FILE"

  EVENT="$(jq -nc \
    --arg ts "$TS" \
    --arg step "$SIGNALOS_STEP_ID" \
    --arg tier "${TIER:-unknown}" \
    '{schema_version:1, ts:$ts, type:"step.paused", step_id:$step, tier:$tier}')"
  ( cd "$REPO_ROOT" && "${LIB_DIR}/journal-append.sh" \
      --session-id "$SIGNALOS_SESSION_ID" \
      --event "$EVENT" ) >&2 || true
fi

echo "step-pause-check.sh: step ${SIGNALOS_STEP_ID} PAUSED — awaiting \`signalos pause resume\`" >&2
exit 2
