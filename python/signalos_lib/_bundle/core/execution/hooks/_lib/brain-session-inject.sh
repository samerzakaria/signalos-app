#!/usr/bin/env bash
# brain-session-inject.sh — Prepend top brain entries to the session preamble
# AMD-CORE-030 W9.5
#
# Usage:
#   brain-session-inject.sh [--query <terms>] [--top N] [--preamble <path>] [--repo-root <path>]
#
# Called from session-start after the gate sequence check.
# Gracefully skips if no brain index exists or signalos not on PATH.

set -euo pipefail

QUERY=""
TOP=5
PREAMBLE_PATH="${SIGNALOS_PREAMBLE_PATH:-.signalos-session-preamble.md}"
REPO_ROOT="${SIGNALOS_REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --query)    QUERY="$2"; shift 2 ;;
    --top)      TOP="$2"; shift 2 ;;
    --preamble) PREAMBLE_PATH="$2"; shift 2 ;;
    --repo-root) REPO_ROOT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

INDEX="$REPO_ROOT/.signalos/brain/index.jsonl"

# Graceful skip if no index
if [[ ! -f "$INDEX" ]]; then
  exit 0
fi

# Graceful skip if signalos not on PATH
if ! command -v signalos >/dev/null 2>&1; then
  exit 0
fi

# Fetch top entries
if [[ -n "$QUERY" ]]; then
  BRAIN_CONTEXT="$(signalos brain search "$QUERY" --top "$TOP" --repo-root "$REPO_ROOT" 2>/dev/null || true)"
else
  BRAIN_CONTEXT="$(signalos brain list --repo-root "$REPO_ROOT" 2>/dev/null | head -"$TOP" || true)"
fi

if [[ -z "$BRAIN_CONTEXT" ]]; then
  exit 0
fi

# Prepend to preamble
BLOCK="## Brain Context (top ${TOP} knowledge entries)

${BRAIN_CONTEXT}

---
"

# Prepend: write block first, then existing preamble
EXISTING=""
if [[ -f "$PREAMBLE_PATH" ]]; then
  EXISTING="$(cat "$PREAMBLE_PATH")"
fi

printf '%s\n%s' "$BLOCK" "$EXISTING" > "$PREAMBLE_PATH"
echo "[brain-session-inject] ✓ injected top-${TOP} brain entries into $PREAMBLE_PATH"
exit 0
