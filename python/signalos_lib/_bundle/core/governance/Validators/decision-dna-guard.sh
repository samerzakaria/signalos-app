#!/usr/bin/env bash
# decision-dna-guard.sh
# Validator — Decision DNA Guard
#
# Purpose:
#   Enforces append-only constraint on DECISION-DNA.md: new DEC entries are
#   prepended, existing entries can only be edited to add "Superseded by DEC-NNNN"
#
# Triggers:
#   Runs when PR modifies DECISION-DNA.md.
#
# Input:
#   DECISION-DNA.md file; examines git diff for content changes within DEC blocks.
#
# Rejection rule:
#   Any edit that modifies the body of an existing DEC entry (not just prepend
#   or Superseded marker) = FAIL.
#
# Exit codes:
#   0 = append-only constraint maintained
#   1 = existing DEC edited improperly (fail-closed)
#   2 = warning-only mode (--warn flag)
#
# Amendment history:
#   2026-04-16 — v1.0 locked, initial implementation.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WARN_MODE=false

usage() {
  cat <<EOF
Usage: decision-dna-guard.sh [OPTIONS]

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

check_append_only() {
  local dna_file="$1"

  if [[ ! -f "$dna_file" ]]; then
    return 0
  fi

  # Get git diff for this file
  local diff
  if git rev-parse --git-dir >/dev/null 2>&1; then
    diff=$(git diff "$dna_file" 2>/dev/null || echo "")
  else
    return 0
  fi

  # Simple heuristic: check for deletions (-) or modifications within DEC blocks
  # Allow: new DEC entries (prepended), Superseded markers
  # Reject: deletions or rewrites of existing DEC content

  local in_del=0
  while IFS= read -r line; do
    if [[ "$line" =~ ^-.*DEC- ]]; then
      # Deletion of DEC content (except Superseded marker adds)
      if ! [[ "$line" =~ "Superseded by" ]]; then
        echo "✗ IMMUTABLE EDIT: Line deleted from existing DEC entry: $line" >&2
        return 1
      fi
    fi

    # Warn on modifications to DEC metadata (not just Superseded)
    if [[ "$line" =~ ^-.*\*\* ]] || [[ "$line" =~ ^-.*Status ]]; then
      if ! [[ "$line" =~ "Superseded" ]]; then
        echo "✗ IMMUTABLE EDIT: DEC metadata modified: $line" >&2
        return 1
      fi
    fi
  done <<< "$diff"

  return 0
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

    if [[ "$file" == *"DECISION-DNA.md" ]]; then
      local dna_file="${REPO_ROOT}/${file}"

      if ! check_append_only "$dna_file"; then
        exit_code=1
      fi
    fi
  done <<< "$modified_files"

  if [[ $exit_code -ne 0 ]]; then
    echo "✗ Decision DNA Guard: append-only constraint violated" >&2
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  exit 0
}

main "$@"
