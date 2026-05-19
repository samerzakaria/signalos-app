#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — shared journal-append helper.
#
# Usage:
#   journal-append.sh --session-id <id> --event '<json-one-liner>'
#
# Writes one JSONL line to:
#   .signalos/sessions/<session-id>/journal.jsonl
#
# Guarantees:
#   - Valid JSON per line (constructed via jq -nc by the caller; this script
#     merely validates and appends).
#   - Concurrent-write safety via flock(1).
#   - Redaction filter applied before every write.
#   - Index file .signalos/sessions/INDEX.jsonl is updated atomically.
#   - Perf target: < 5 ms per append on SSD (see proof/scenarios/18).
#
# Exit codes:
#   0 — appended
#   1 — validation error (missing arg, invalid JSON)
#   2 — IO error (cannot write)
#   3 — redaction filter failed

set -euo pipefail

SESSION_ID=""
EVENT_JSON=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-id) SESSION_ID="$2"; shift 2 ;;
    --event)      EVENT_JSON="$2";  shift 2 ;;
    --help)
      echo "Usage: journal-append.sh --session-id <id> --event '<json>'"
      exit 0
      ;;
    *) echo "journal-append.sh: unknown arg: $1" >&2; exit 1 ;;
  esac
done

[[ -n "$SESSION_ID" ]] || { echo "journal-append.sh: --session-id required" >&2; exit 1; }
[[ -n "$EVENT_JSON"  ]] || { echo "journal-append.sh: --event required" >&2; exit 1; }

# Resolve repo root and journal path.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SESSION_DIR="${REPO_ROOT}/.signalos/sessions/${SESSION_ID}"
JOURNAL_PATH="${SESSION_DIR}/journal.jsonl"
INDEX_PATH="${REPO_ROOT}/.signalos/sessions/INDEX.jsonl"
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$SESSION_DIR" || { echo "journal-append.sh: cannot mkdir $SESSION_DIR" >&2; exit 2; }

# Validate JSON (jq required).
if ! command -v jq >/dev/null 2>&1; then
  echo "journal-append.sh: jq not on PATH (required)" >&2
  exit 1
fi
if ! echo "$EVENT_JSON" | jq -e . >/dev/null 2>&1; then
  echo "journal-append.sh: --event is not valid JSON" >&2
  exit 1
fi

# B8: Inject trace_id (session-scoped UUID4) + span_id (step-scoped UUID4).
# trace_id is stable for the lifetime of a session; span_id is unique per append.
# If SIGNALOS_TRACE_ID is set (by session-start or orchestrator), reuse it;
# otherwise generate a new one and export it for subsequent calls in this shell.
if [[ -z "${SIGNALOS_TRACE_ID:-}" ]]; then
  SIGNALOS_TRACE_ID="$(python3 -c 'import uuid; print(str(uuid.uuid4()))')"
  export SIGNALOS_TRACE_ID
fi
_SPAN_ID="$(python3 -c 'import uuid; print(str(uuid.uuid4()))')"

# B4: Actor identity — AMD-CORE-025. SIGNALOS_ACTOR_IDENTITY format: "name|role|session_id".
# HMAC-SHA256 keyed with .signalos/install.secret (AMD-CORE-025).
# Falls back to plain SHA256 when install.secret is absent (no key available).
# actor_identity is stored in plaintext alongside actor_hmac to enable verification.
_SECRET_FILE="${REPO_ROOT}/.signalos/install.secret"
_ACTOR_HMAC=""
_ACTOR_IDENTITY_PLAIN=""
if [[ -n "${SIGNALOS_ACTOR_IDENTITY:-}" ]]; then
  _ACTOR_IDENTITY_PLAIN="${SIGNALOS_ACTOR_IDENTITY}"
  if [[ -f "$_SECRET_FILE" ]] && [[ -r "$_SECRET_FILE" ]]; then
    # HMAC-SHA256: key = hex string from install.secret, msg = identity string
    _ACTOR_HMAC="$(python3 -c "
import hmac, hashlib, sys
key = open(sys.argv[1]).read().strip().encode()
msg = sys.argv[2].encode()
print(hmac.new(key, msg, hashlib.sha256).hexdigest())
" "$_SECRET_FILE" "${SIGNALOS_ACTOR_IDENTITY}")"
  else
    # install.secret absent — fall back to plain SHA256 (no HMAC possible)
    _ACTOR_HMAC="$(printf '%s' "${SIGNALOS_ACTOR_IDENTITY}" | sha256sum | awk '{print $1}')"
  fi
fi

# Augment EVENT_JSON with trace_id, span_id, actor_hmac, actor_identity before redaction.
EVENT_JSON="$(echo "$EVENT_JSON" | jq -c \
  --arg trace_id        "$SIGNALOS_TRACE_ID" \
  --arg span_id         "$_SPAN_ID" \
  --arg actor_hmac      "${_ACTOR_HMAC}" \
  --arg actor_identity  "${_ACTOR_IDENTITY_PLAIN}" \
  '. + {trace_id: $trace_id, span_id: $span_id} +
   (if $actor_hmac != "" then {actor_hmac: $actor_hmac, actor_identity: $actor_identity} else {} end)'
)"

# Apply redaction filter.
REDACTED="$(echo "$EVENT_JSON" | python3 "${LIB_DIR}/redact.py" --filter)" \
  || { echo "journal-append.sh: redaction filter failed" >&2; exit 3; }

# Append with flock for concurrent-write safety.
(
  flock -x 9
  printf '%s\n' "$REDACTED" >> "$JOURNAL_PATH"
) 9>> "${JOURNAL_PATH}.lock"

# Index update: one row per session, upserted atomically via rename.
if [[ ! -f "$INDEX_PATH" ]]; then
  : > "$INDEX_PATH"
fi

TMP_INDEX="${INDEX_PATH}.tmp.$$"
LAST_EVENT="$(printf '%s' "$REDACTED" | jq -r '.type // empty' 2>/dev/null || true)"
LAST_TS="$(printf '%s' "$REDACTED" | jq -r '.ts   // empty' 2>/dev/null || true)"
UPDATED_AT="${LAST_TS:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

(
  flock -x 8
  grep -v "\"session_id\":\"${SESSION_ID}\"" "$INDEX_PATH" > "$TMP_INDEX" 2>/dev/null || true
  jq -nc \
    --arg sid "$SESSION_ID" \
    --arg path "$JOURNAL_PATH" \
    --arg last "${LAST_EVENT:-unknown}" \
    --arg ts "$UPDATED_AT" \
    '{session_id:$sid, journal:$path, last_event:$last, updated_at:$ts}' \
    >> "$TMP_INDEX"
  mv "$TMP_INDEX" "$INDEX_PATH"
) 8>> "${INDEX_PATH}.lock"
