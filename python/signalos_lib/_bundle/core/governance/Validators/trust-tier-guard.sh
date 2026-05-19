#!/usr/bin/env bash
# trust-tier-guard.sh
# Validator — Trust Tier Guard
#
# Purpose:
#   Ensures PR commit message declares a Trust tier (T1/T2/T3) and that all
#   touched surfaces match the declared tier or are lower.
#
# Triggers:
#   Runs on every PR that touches any surface.
#
# Input:
#   Git commit message (grep for "Trust tier: T[123]" pattern);
#   CONSTITUTION.md for T3 surface list; PRODUCT_TIER_SHEET.md if present.
#
# Rejection rule:
#   - No "Trust tier: T[123]" in commit message = FAIL
#   - PR touches a T3 surface without T3 declaration = FAIL
#
# Exit codes:
#   0 = all surfaces match declared trust tier
#   1 = tier mismatch or missing declaration (fail-closed)
#   2 = warning-only mode (--warn flag)
#
# Amendment history:
#   2026-04-16 — v1.0 locked, initial implementation.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WARN_MODE=false
DECLARED_TIER=""

usage() {
  cat <<EOF
Usage: trust-tier-guard.sh [OPTIONS]

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

get_declared_tier() {
  # Extract tier from HEAD commit message
  local msg
  if git rev-parse --git-dir >/dev/null 2>&1; then
    msg=$(git log -1 --pretty=%B 2>/dev/null || echo "")
  else
    return 1
  fi

  if [[ "$msg" =~ Trust\ tier:\ (T[123]) ]]; then
    echo "${BASH_REMATCH[1]}"
  fi
}

is_t3_surface() {
  local file="$1"
  local constitution="${REPO_ROOT}/CONSTITUTION.md"

  if [[ ! -f "$constitution" ]]; then
    return 1
  fi

  # Simple heuristic: check if file path is mentioned after "§T3 surface list" section
  local in_t3_section=0
  while IFS= read -r line; do
    if [[ "$line" =~ "§T3 surface list" ]] || [[ "$line" =~ "T3 surface" ]]; then
      in_t3_section=1
      continue
    fi
    if [[ $in_t3_section -eq 1 ]] && [[ "$line" =~ ^## ]]; then
      break
    fi
    if [[ $in_t3_section -eq 1 ]] && [[ "$line" =~ "$file" ]]; then
      return 0
    fi
  done < "$constitution"

  return 1
}

main() {
  parse_args "$@"

  DECLARED_TIER=$(get_declared_tier)

  if [[ -z "$DECLARED_TIER" ]]; then
    echo "✗ Trust Tier Guard: No 'Trust tier: T[123]' found in commit message" >&2
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
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
  local violations=0

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue

    # Check if T3 surface but declared tier is not T3
    if is_t3_surface "$file" && [[ "$DECLARED_TIER" != "T3" ]]; then
      echo "✗ TIER MISMATCH: $file is T3 but declared tier is $DECLARED_TIER" >&2
      violations=$((violations + 1))
      exit_code=1
    fi
  done <<< "$modified_files"

  if [[ $violations -gt 0 ]]; then
    echo "✗ Trust Tier Guard: $violations tier violation(s)" >&2
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  exit 0
}

main "$@"
