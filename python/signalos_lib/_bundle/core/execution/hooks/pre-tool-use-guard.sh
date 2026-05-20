#!/usr/bin/env bash
# pre-tool-use-guard.sh — Agent write guard (AMD-CORE-011)
#
# Fires on PreToolUse Write|Edit. Two checks:
#   1. T3 surface guard — block if target file path is a permanently-T3 surface
#      and the agent trust tier does not permit writes.
#   2. Secret scan — pipe write content through redact.py --scan-diff; block
#      if any secret pattern fires.
#
# Exit 0 = write permitted. Exit 2 = write blocked (audit trail written).
# Environment variables read:
#   CLAUDE_TOOL_INPUT_FILE_PATH or CURSOR_TOOL_INPUT_FILE_PATH — target file
#   CLAUDE_TOOL_INPUT_CONTENT    or CURSOR_TOOL_INPUT_CONTENT  — write content
#   SIGNALOS_PLUGIN_ROOT or CLAUDE_PLUGIN_ROOT — repo root override

set -euo pipefail

REPO_ROOT="${SIGNALOS_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}}"
REDACT_PY="${REPO_ROOT}/core/execution/hooks/_lib/redact.py"
TRUST_TIER="${REPO_ROOT}/core/execution/TRUST_TIER.md"
PERMANENTLY_T3="${REPO_ROOT}/core/PERMANENTLY_T3.md"
AUDIT_TRAIL="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"

# Pick a working Python interpreter. On Windows Git Bash, `python3` may resolve
# to a Microsoft Store stub that errors out; fall back to `python`. With
# `set -e` + `pipefail`, an unusable python3 inside the secret-scan pipe would
# cause a false-positive block.
if python3 --version >/dev/null 2>&1; then
  PYTHON=python3
elif python --version >/dev/null 2>&1; then
  PYTHON=python
else
  PYTHON=python3  # let the failure surface clearly downstream
fi

RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

BLOCKED=0
BLOCK_REASON=""

TARGET_FILE="${CLAUDE_TOOL_INPUT_FILE_PATH:-${CURSOR_TOOL_INPUT_FILE_PATH:-}}"
WRITE_CONTENT="${CLAUDE_TOOL_INPUT_CONTENT:-${CURSOR_TOOL_INPUT_CONTENT:-}}"

# ─── Check 1: T3 surface guard ───────────────────────────────────────────────
if [ -n "$TARGET_FILE" ] && [ -f "$PERMANENTLY_T3" ]; then
  # Normalise to relative path for comparison
  REL_TARGET="${TARGET_FILE#"$REPO_ROOT/"}"
  # Read T3 path prefixes from PERMANENTLY_T3.md (lines starting with - or *)
  while IFS= read -r line; do
    t3_path=$(echo "$line" | grep -oE '[a-zA-Z0-9/_.-]+\.(py|sh|json|md|jsonl)' | head -1 || true)
    [[ -z "$t3_path" ]] && continue
    if [[ "$REL_TARGET" == "$t3_path"* ]]; then
      echo -e "${RED}✗ BLOCKED: Agent write to T3 surface: $REL_TARGET${NC}" >&2
      BLOCK_REASON="T3 surface write blocked: $REL_TARGET"
      BLOCKED=1
      break
    fi
  done < "$PERMANENTLY_T3"
fi

# ─── Check 2: Secret scan on write content ───────────────────────────────────
if [ -n "$WRITE_CONTENT" ] && [ -f "$REDACT_PY" ]; then
  # Format as a minimal unified diff (+line per content line)
  DIFF_INPUT=$(echo "$WRITE_CONTENT" | sed 's/^/+/')
  if echo "$DIFF_INPUT" | "$PYTHON" "$REDACT_PY" --scan-diff 2>&1; then
    : # clean
  else
    echo -e "${RED}✗ BLOCKED: Write content contains a secret pattern.${NC}" >&2
    BLOCK_REASON="${BLOCK_REASON:+$BLOCK_REASON; }secret pattern in write content"
    BLOCKED=1
  fi
fi

# ─── Audit trail on block ─────────────────────────────────────────────────────
if [ "$BLOCKED" -eq 1 ]; then
  mkdir -p "$(dirname "$AUDIT_TRAIL")"
  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"event":"agent-write-blocked","timestamp":"%s","target":"%s","reason":"%s"}\n' \
    "$TS" "${TARGET_FILE:-unknown}" "${BLOCK_REASON}" >> "$AUDIT_TRAIL" 2>/dev/null || true

  # Route through exception-router if available
  ROUTER="${REPO_ROOT}/core/execution/hooks/exception-router.sh"
  if [ -x "$ROUTER" ]; then
    bash "$ROUTER" --type agent-write-blocked \
      --context "target=${TARGET_FILE:-unknown};reason=${BLOCK_REASON}" 2>/dev/null || true
  fi

  exit 2
fi

exit 0
