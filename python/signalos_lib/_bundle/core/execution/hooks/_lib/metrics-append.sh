#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — shared metrics sidecar append helper.
#
# Usage:
#   metrics-append.sh --session-id <id> --metric '<json-one-liner>'
#
# Writes one JSONL line to:
#   .signalos/sessions/<session-id>/metrics.jsonl
#
# Guarantees:
#   - Valid JSON per line (constructed via jq -nc by the caller; this script
#     validates, enforces a strict field allowlist, and appends).
#   - Concurrent-write safety via flock(1) — mirrors journal-append.sh.
#   - Redaction filter applied before every write (shared redact.py --filter;
#     NEVER forked).
#   - NO INDEX.jsonl update — metrics are session-local only.
#   - Field allowlist enforced via jq to prevent any prompt/response body
#     from slipping onto disk. Any unknown field aborts the write.
#
# Required fields on every metric row:
#   ts             ISO-8601 UTC timestamp (string)
#   schema_version must be integer 1
#   session_id     string
#   step_id        string
#   (hook OR tool) at least one of these two string fields
#   duration_ms    non-negative integer
#
# Optional allowed fields:
#   tokens_in      non-negative integer
#   tokens_out     non-negative integer
#   cost_usd       non-negative number
#   wave_id        string
#   phase          string
#   actor          string
#   subagent_count non-negative integer
#
# Any other field name — including anything that looks like prompt/response
# body (prompt, response, body, content, text, message, input, output) —
# causes an immediate exit 1 schema violation.
#
# Exit codes:
#   0 — appended
#   1 — validation error (missing arg, invalid JSON, schema violation)
#   2 — IO error (cannot write)
#   3 — redaction filter failed

set -euo pipefail

SESSION_ID=""
METRIC_JSON=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-id) SESSION_ID="$2"; shift 2 ;;
    --metric)     METRIC_JSON="$2"; shift 2 ;;
    --help)
      echo "Usage: metrics-append.sh --session-id <id> --metric '<json>'"
      exit 0
      ;;
    *) echo "metrics-append.sh: unknown arg: $1" >&2; exit 1 ;;
  esac
done

[[ -n "$SESSION_ID"  ]] || { echo "metrics-append.sh: --session-id required" >&2; exit 1; }
[[ -n "$METRIC_JSON" ]] || { echo "metrics-append.sh: --metric required" >&2; exit 1; }

# Resolve repo root and metrics path.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SESSION_DIR="${REPO_ROOT}/.signalos/sessions/${SESSION_ID}"
METRICS_PATH="${SESSION_DIR}/metrics.jsonl"
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$SESSION_DIR" || { echo "metrics-append.sh: cannot mkdir $SESSION_DIR" >&2; exit 2; }

# jq required.
if ! command -v jq >/dev/null 2>&1; then
  echo "metrics-append.sh: jq not on PATH (required)" >&2
  exit 1
fi

# Validate JSON.
if ! echo "$METRIC_JSON" | jq -e . >/dev/null 2>&1; then
  echo "metrics-append.sh: --metric is not valid JSON" >&2
  exit 1
fi

# Must be a JSON object.
if ! echo "$METRIC_JSON" | jq -e 'type == "object"' >/dev/null 2>&1; then
  echo "metrics-append.sh: --metric must be a JSON object" >&2
  exit 1
fi

# Field allowlist — any unknown key rejects the row. Required and optional
# keys are the ONLY legal fields. This is the read-side contract the
# dashboard renderer assumes.
ALLOWED_FIELDS='["ts","schema_version","session_id","step_id","hook","tool","duration_ms","tokens_in","tokens_out","cost_usd","wave_id","phase","actor","subagent_count"]'

UNKNOWN="$(echo "$METRIC_JSON" | jq -r --argjson allow "$ALLOWED_FIELDS" \
  '[keys[] | select(. as $k | $allow | index($k) | not)] | .[]' 2>/dev/null || true)"
if [[ -n "${UNKNOWN:-}" ]]; then
  echo "metrics-append.sh: schema violation — unknown field(s): $(echo "$UNKNOWN" | tr '\n' ',' | sed 's/,$//')" >&2
  echo "metrics-append.sh: metrics sidecar rejects anything outside the allowlist; see header comment" >&2
  exit 1
fi

# Required fields.
REQUIRED_CHECK="$(echo "$METRIC_JSON" | jq -r '
  [
    (if has("ts") and (.ts | type) == "string" then "ok" else "missing_or_bad:ts" end),
    (if has("schema_version") and (.schema_version == 1) then "ok" else "missing_or_bad:schema_version" end),
    (if has("session_id") and (.session_id | type) == "string" then "ok" else "missing_or_bad:session_id" end),
    (if has("step_id") and (.step_id | type) == "string" then "ok" else "missing_or_bad:step_id" end),
    (if (has("hook") and (.hook | type) == "string") or (has("tool") and (.tool | type) == "string") then "ok" else "missing_or_bad:hook_or_tool" end),
    (if has("duration_ms") and (.duration_ms | type) == "number" and (.duration_ms >= 0) then "ok" else "missing_or_bad:duration_ms" end)
  ] | map(select(. != "ok")) | join(",")
')"
if [[ -n "$REQUIRED_CHECK" ]]; then
  echo "metrics-append.sh: schema violation — required field(s): $REQUIRED_CHECK" >&2
  exit 1
fi

# Optional-field type checks — if present they must be well-typed.
OPTIONAL_CHECK="$(echo "$METRIC_JSON" | jq -r '
  [
    (if has("tokens_in")      and ((.tokens_in      | type) != "number" or .tokens_in      < 0) then "bad:tokens_in"      else "ok" end),
    (if has("tokens_out")     and ((.tokens_out     | type) != "number" or .tokens_out     < 0) then "bad:tokens_out"     else "ok" end),
    (if has("cost_usd")       and ((.cost_usd       | type) != "number" or .cost_usd       < 0) then "bad:cost_usd"       else "ok" end),
    (if has("subagent_count") and ((.subagent_count | type) != "number" or .subagent_count < 0) then "bad:subagent_count" else "ok" end)
  ] | map(select(. != "ok")) | join(",")
')"
if [[ -n "$OPTIONAL_CHECK" ]]; then
  echo "metrics-append.sh: schema violation — optional field(s): $OPTIONAL_CHECK" >&2
  exit 1
fi

# Apply the SAME redaction filter used by journal (never forked).
REDACTED="$(echo "$METRIC_JSON" | python3 "${LIB_DIR}/redact.py" --filter)" \
  || { echo "metrics-append.sh: redaction filter failed" >&2; exit 3; }

# Append with flock for concurrent-write safety.
(
  flock -x 9
  printf '%s\n' "$REDACTED" >> "$METRICS_PATH"
) 9>> "${METRICS_PATH}.lock"
