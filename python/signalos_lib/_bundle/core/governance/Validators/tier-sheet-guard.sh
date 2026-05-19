#!/usr/bin/env bash
# tier-sheet-guard.sh
# Validator — Tier Sheet Guard
#
# Purpose:
#   When delivery_mode=daemon in CONSTITUTION.md, ensures every touched file
#   is listed in PRODUCT_TIER_SHEET.md.
#
# Triggers:
#   Runs only when CONSTITUTION.md declares delivery_mode: daemon.
#
# Input:
#   CONSTITUTION.md (to check delivery_mode), PRODUCT_TIER_SHEET.md (roster of
#   all mapped surfaces), git diff touched files.
#
# Rejection rule:
#   PR touches a file not listed in PRODUCT_TIER_SHEET.md = FAIL (if daemon mode).
#
# Exit codes:
#   0 = all touched files are in tier sheet (or not in daemon mode)
#   1 = unmapped surface(s) found (fail-closed)
#   2 = warning-only mode (--warn flag)
#
# Amendment history:
#   2026-04-16 — v1.0 locked, initial implementation.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WARN_MODE=false

usage() {
  cat <<EOF
Usage: tier-sheet-guard.sh [OPTIONS]

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

is_daemon_mode() {
  local constitution="${REPO_ROOT}/CONSTITUTION.md"
  if [[ ! -f "$constitution" ]]; then
    return 1
  fi
  grep -q "delivery_mode:\s*daemon" "$constitution"
}

is_in_tier_sheet() {
  local file="$1"
  local tier_sheet="${REPO_ROOT}/PRODUCT_TIER_SHEET.md"

  if [[ ! -f "$tier_sheet" ]]; then
    return 1
  fi

  grep -q "$(printf '%s\n' "$file" | sed 's/[[\.*^$/]/\\&/g')" "$tier_sheet"
}

main() {
  parse_args "$@"

  # Skip if not in daemon mode
  if ! is_daemon_mode; then
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
  local unmapped_count=0

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue

    # Skip certain file patterns that don't need mapping
    [[ "$file" =~ ^\.git ]] && continue
    [[ "$file" =~ ^\.github ]] && continue

    if ! is_in_tier_sheet "$file"; then
      echo "✗ UNMAPPED: $file not found in PRODUCT_TIER_SHEET.md" >&2
      unmapped_count=$((unmapped_count + 1))
      exit_code=1
    fi
  done <<< "$modified_files"

  if [[ $unmapped_count -gt 0 ]]; then
    echo "✗ Tier Sheet Guard: $unmapped_count unmapped surface(s)" >&2
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  exit 0
}

main "$@"
