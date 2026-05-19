#!/usr/bin/env bash
# client-signal-verbatim-guard.sh
# Validator — Client Signal Verbatim Guard
#
# Purpose:
#   Ensures new SIG (signal) entries in CLIENT-SIGNAL-LOG.md include a
#   blockquote (> ) of the client's verbatim input AND a Sentiment: field.
#
# Triggers:
#   Runs when PR modifies CLIENT-SIGNAL-LOG.md.
#
# Input:
#   CLIENT-SIGNAL-LOG.md file; scans new entries for blockquote + Sentiment field.
#
# Rejection rule:
#   New SIG entry missing blockquote OR missing Sentiment: field = FAIL.
#
# Exit codes:
#   0 = all new entries have blockquote and sentiment
#   1 = missing blockquote or sentiment (fail-closed)
#   2 = warning-only mode (--warn flag)
#
# Amendment history:
#   2026-04-16 — v1.0 locked, initial implementation.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WARN_MODE=false

usage() {
  cat <<EOF
Usage: client-signal-verbatim-guard.sh [OPTIONS]

Options:
  --repo-root <path>  Repository root (default: current directory)
  --warn              Warn-only mode (exit 2 on failure, not 1)
  --help              Show this help message

EOF
  exit "${1:-0}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo-root)
        REPO_ROOT="$2"
        shift 2
        ;;
      --warn)
        WARN_MODE=true
        shift
        ;;
      --help)
        usage 0
        ;;
      *)
        echo "Error: Unknown argument: $1" >&2
        usage 1
        ;;
    esac
  done
}

check_signal_entries() {
  local signal_log="$1"

  if [[ ! -f "$signal_log" ]]; then
    return 0
  fi

  # Get git diff for new additions only
  local new_lines
  if git rev-parse --git-dir >/dev/null 2>&1; then
    new_lines=$(git diff "$signal_log" 2>/dev/null | grep "^+" | grep -v "^+++" || echo "")
  else
    return 0
  fi

  local in_sig_entry=0
  local current_sig_line=""
  local has_blockquote=0
  local has_sentiment=0
  local violations=0

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue

    # Remove leading + from diff
    line="${line:1}"

    # Detect new SIG entry start (### SIG-)
    if [[ "$line" =~ \#\#\#\ SIG- ]]; then
      # Check previous entry before starting new one
      if [[ -n "$current_sig_line" ]] && ([[ $has_blockquote -eq 0 ]] || [[ $has_sentiment -eq 0 ]]); then
        echo "✗ INCOMPLETE SIG ENTRY: $current_sig_line missing blockquote or sentiment" >&2
        violations=$((violations + 1))
      fi

      # Reset for new entry
      current_sig_line="$line"
      has_blockquote=0
      has_sentiment=0
      in_sig_entry=1
      continue
    fi

    # Track blockquote and sentiment in active SIG entry
    if [[ $in_sig_entry -eq 1 ]]; then
      if [[ "$line" =~ ^[[:space:]]*'>'[[:space:]] ]]; then
        has_blockquote=1
      fi
      if [[ "$line" =~ Sentiment: ]]; then
        has_sentiment=1
      fi
    fi
  done <<< "$new_lines"

  # Check last entry
  if [[ -n "$current_sig_line" ]] && ([[ $has_blockquote -eq 0 ]] || [[ $has_sentiment -eq 0 ]]); then
    echo "✗ INCOMPLETE SIG ENTRY: $current_sig_line missing blockquote or sentiment" >&2
    violations=$((violations + 1))
  fi

  [[ $violations -eq 0 ]]
}

main() {
  parse_args "$@"

  # Get modified files
  local modified_files
  if git rev-parse --git-dir >/dev/null 2>&1; then
    modified_files=$(git diff --cached --name-only 2>/dev/null || git diff --name-only 2>/dev/null || echo "")
  else
    echo "Warning: Not a git repository, skipping validation" >&2
    exit 0
  fi

  local exit_code=0

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue

    if [[ "$file" == *"CLIENT-SIGNAL-LOG.md" ]]; then
      local signal_log="${REPO_ROOT}/${file}"

      if ! check_signal_entries "$signal_log"; then
        exit_code=1
      fi
    fi
  done <<< "$modified_files"

  if [[ $exit_code -ne 0 ]]; then
    echo "✗ Client Signal Verbatim Guard: validation failed" >&2
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  exit 0
}

main "$@"
