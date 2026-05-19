#!/usr/bin/env bash
# brain-auto-ingest.sh — Auto-ingest a gate artifact into the Knowledge Brain
# AMD-CORE-030 W9.4
#
# Usage:
#   brain-auto-ingest.sh --source <path> --gate <G0-G5> --wave <NN> [--product-id <id>] [--repo-root <path>]
#
# Called automatically after sign_artifact succeeds.
# Gracefully exits 0 if brain CLI not available or signalos not on PATH.

set -euo pipefail

SOURCE=""
GATE=""
WAVE=""
PRODUCT_ID="core"
REPO_ROOT="${SIGNALOS_REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
ENTRY_TYPE="artifact"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)     SOURCE="$2"; shift 2 ;;
    --gate)       GATE="$2"; shift 2 ;;
    --wave)       WAVE="$2"; shift 2 ;;
    --product-id) PRODUCT_ID="$2"; shift 2 ;;
    --repo-root)  REPO_ROOT="$2"; shift 2 ;;
    --type)       ENTRY_TYPE="$2"; shift 2 ;;
    *) echo "[brain-auto-ingest] unknown arg: $1" >&2; shift ;;
  esac
done

# Graceful skip if signalos not available
if ! command -v signalos >/dev/null 2>&1; then
  echo "[brain-auto-ingest] signalos not on PATH — skipping brain ingest" >&2
  exit 0
fi

# Graceful skip if source file missing
if [[ -z "$SOURCE" ]] || [[ ! -f "$SOURCE" ]]; then
  echo "[brain-auto-ingest] source file not found: ${SOURCE:-<empty>} — skipping" >&2
  exit 0
fi

CONTENT="$(cat "$SOURCE")"
CONTENT_TRIMMED="${CONTENT:0:4000}"   # cap at 4000 chars for index

signalos brain put "$CONTENT_TRIMMED" \
  --source "$SOURCE" \
  --gate "$GATE" \
  --wave "$WAVE" \
  --product-id "$PRODUCT_ID" \
  --type "$ENTRY_TYPE" \
  --repo-root "$REPO_ROOT" \
  >/dev/null 2>&1 && \
  echo "[brain-auto-ingest] ✓ ingested $SOURCE (gate=$GATE wave=$WAVE)" || \
  echo "[brain-auto-ingest] ✗ ingest failed for $SOURCE — continuing" >&2

exit 0
