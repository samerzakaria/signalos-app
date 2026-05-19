#!/usr/bin/env bash
# artifact-shape-guard.sh
# Validator — Artifact Shape Guard
#
# Purpose:
#   Ensures template-governed artifacts (BELIEF, EXPECTATION_MAP, PLAN,
#   design-note, trust-tiers) retain their canonical structure with all
#   required heading sections.
#
# Triggers:
#   Runs when PR modifies any template-governed artifact type.
#
# Input:
#   Modified artifact files; scans for required heading sections.
#
# Rejection rule:
#   Any touched artifact missing a required heading = FAIL.
#   Example: BELIEF.md must have ## Purpose, ## BELIEF, ## SIGNAL, ## KILL RULE
#
# Exit codes:
#   0 = all artifacts have required structure
#   1 = missing required section(s) (fail-closed)
#   2 = warning-only mode (--warn flag)
#
# Amendment history:
#   2026-04-16 — v1.0 locked, initial implementation.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WARN_MODE=false

usage() {
  cat <<EOF
Usage: artifact-shape-guard.sh [OPTIONS]

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

check_artifact_shape() {
  local filepath="$1"
  local filename=$(basename "$filepath")
  local missing=()

  if [[ ! -f "$filepath" ]]; then
    return 0
  fi

  # Define required sections per artifact type
  case "$filename" in
    BELIEF.md)
      for section in "BELIEF" "SIGNAL" "KILL RULE" "BUDGET"; do
        grep -q "^##.*$section" "$filepath" || missing+=("$section")
      done
      ;;
    EXPECTATION_MAP.md)
      for section in "In scope" "Out of scope" "Observable signal" "Non-functional targets"; do
        grep -q "^##.*$section" "$filepath" || missing+=("$section")
      done
      ;;
    PLAN.md)
      for section in "Overview" "Tasks" "Sequencing" "Dependencies"; do
        grep -q "^##.*$section" "$filepath" || missing+=("$section")
      done
      ;;
    design-note.md)
      for section in "Decision" "Rationale" "Alternatives"; do
        grep -q "^##.*$section" "$filepath" || missing+=("$section")
      done
      ;;
    trust-tiers.md)
      for section in "Wave" "Tier assignment"; do
        grep -q "^##.*$section" "$filepath" || missing+=("$section")
      done
      ;;
  esac

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Missing sections in $filepath: ${missing[*]}" >&2
    return 1
  fi

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
  local malformed_count=0

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue

    local filename=$(basename "$file")
    case "$filename" in
      BELIEF.md | EXPECTATION_MAP.md | PLAN.md | design-note.md | trust-tiers.md)
        local filepath="${REPO_ROOT}/${file}"
        if ! check_artifact_shape "$filepath"; then
          malformed_count=$((malformed_count + 1))
          exit_code=1
        fi
        ;;
    esac
  done <<< "$modified_files"

  if [[ $malformed_count -gt 0 ]]; then
    echo "✗ Artifact Shape Guard: $malformed_count artifact(s) with missing sections" >&2
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  exit 0
}

main "$@"
