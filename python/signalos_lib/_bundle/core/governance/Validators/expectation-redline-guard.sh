#!/usr/bin/env bash
# expectation-redline-guard.sh
# Validator — Expectation Redline Guard
#
# Purpose:
#   Ensures EXPECTATION_MAP.md has populated ## Redlines section OR a
#   PO-note-zero-redlines: line when the Wave is in Wave track (scale_track=wave).
#
# Triggers:
#   Runs when PR modifies EXPECTATION_MAP.md and Wave track is active.
#
# Input:
#   EXPECTATION_MAP.md file; checks for Redlines section or PO-note-zero-redlines line.
#
# Rejection rule:
#   EXPECTATION_MAP.md in Wave track with neither populated Redlines nor
#   PO-note-zero-redlines = FAIL.
#
# Exit codes:
#   0 = redlines validated or not in Wave track
#   1 = missing redlines documentation (fail-closed)
#   2 = warning-only mode (--warn flag)
#
# Amendment history:
#   2026-04-16 — v1.0 locked, initial implementation.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WARN_MODE=false

usage() {
  cat <<EOF
Usage: expectation-redline-guard.sh [OPTIONS]

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

is_wave_track() {
  local constitution="${REPO_ROOT}/CONSTITUTION.md"
  if [[ ! -f "$constitution" ]]; then
    return 1
  fi
  grep -q "scale_track:\s*wave" "$constitution"
}

has_populated_redlines() {
  local expmap="$1"
  if [[ ! -f "$expmap" ]]; then
    return 1
  fi

  # Check for ## Redlines section followed by non-empty content
  local in_redlines=0
  local has_content=0

  while IFS= read -r line; do
    if [[ "$line" =~ ^##\ Redlines ]]; then
      in_redlines=1
      continue
    fi

    if [[ $in_redlines -eq 1 ]]; then
      # Stop at next heading
      if [[ "$line" =~ ^## ]] && [[ ! "$line" =~ ^##\ Redlines ]]; then
        break
      fi

      # Check for non-empty, non-heading content
      if [[ -n "$line" && ! "$line" =~ ^# ]]; then
        has_content=1
      fi
    fi
  done < "$expmap"

  [[ $has_content -eq 1 ]]
}

has_zero_redlines_note() {
  local expmap="$1"
  if [[ ! -f "$expmap" ]]; then
    return 1
  fi

  grep -q "PO-note-zero-redlines:" "$expmap"
}

main() {
  parse_args "$@"

  # Skip if not Wave track
  if ! is_wave_track; then
    exit 0
  fi

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

    if [[ "$file" == *"EXPECTATION_MAP.md" ]]; then
      local expmap="${REPO_ROOT}/${file}"

      if ! has_populated_redlines "$expmap" && ! has_zero_redlines_note "$expmap"; then
        echo "✗ REDLINE MISSING: $file must have ## Redlines section OR PO-note-zero-redlines: line" >&2
        exit_code=1
      fi
    fi
  done <<< "$modified_files"

  if [[ $exit_code -ne 0 ]]; then
    echo "✗ Expectation Redline Guard: validation failed" >&2
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  exit 0
}

main "$@"
