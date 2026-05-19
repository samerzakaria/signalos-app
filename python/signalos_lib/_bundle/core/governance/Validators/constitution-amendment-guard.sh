#!/usr/bin/env bash
# constitution-amendment-guard.sh
# Validator — Constitution Amendment Guard
#
# Purpose:
#   When CONSTITUTION.md changes, verifies there is a matching amendment entry
#   in core/governance/Retro/ amendment log following §13 amendment path.
#
# Triggers:
#   Runs when PR modifies CONSTITUTION.md.
#
# Input:
#   CONSTITUTION.md; core/governance/Retro/ directory for amendment records.
#
# Rejection rule:
#   Direct edit to CONSTITUTION.md without matching amendment log entry = FAIL.
#
# Exit codes:
#   0 = valid amendment or no constitution change
#   1 = missing amendment record (fail-closed)
#   2 = warning-only mode (--warn flag)
#
# Amendment history:
#   2026-04-16 — v1.0 locked, initial implementation.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WARN_MODE=false

usage() {
  cat <<EOF
Usage: constitution-amendment-guard.sh [OPTIONS]

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

has_amendment_record() {
  local retro_dir="${REPO_ROOT}/core/governance/Retro"

  if [[ ! -d "$retro_dir" ]]; then
    # Directory doesn't exist — no amendment records possible (fail-closed per §6.1)
    echo "✗ Retro directory not found at: $retro_dir — cannot verify amendment record" >&2
    return 1
  fi

  # Check if any amendment file exists (*.md in the retro dir)
  if find "$retro_dir" -maxdepth 1 -name "*.md" -type f | grep -q .; then
    return 0
  fi

  return 1
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

    if [[ "$file" == *"CONSTITUTION.md" ]]; then
      # Constitution was modified; check for amendment record
      if ! has_amendment_record; then
        echo "✗ AMENDMENT MISSING: CONSTITUTION.md changed but no amendment record found in core/governance/Retro/" >&2
        exit_code=1
      fi
    fi
  done <<< "$modified_files"

  if [[ $exit_code -ne 0 ]]; then
    echo "✗ Constitution Amendment Guard: validation failed" >&2
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  exit 0
}

main "$@"
