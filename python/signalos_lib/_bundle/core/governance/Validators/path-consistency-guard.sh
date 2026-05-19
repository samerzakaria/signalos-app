#!/usr/bin/env bash
# path-consistency-guard.sh
# Validator — Path Consistency Guard
#
# Purpose:
#   Scans all markdown files in the distro for internal path references
#   (markdown links like [text](path)) and verifies they resolve to real files.
#
# Triggers:
#   Runs on any SignalOS distro change (modifications to .md files).
#
# Input:
#   All .md files under SignalOS/ folder; markdown link syntax [text](path).
#
# Rejection rule:
#   Any [text](path) reference that does not resolve = FAIL.
#
# Exit codes:
#   0 = all path references valid
#   1 = broken path reference(s) found (fail-closed)
#   2 = warning-only mode (--warn flag)
#
# Amendment history:
#   2026-04-16 — v1.0 locked, initial implementation.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WARN_MODE=false

usage() {
  cat <<EOF
Usage: path-consistency-guard.sh [OPTIONS]

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

resolve_path() {
  local linkpath="$1"
  local basedir="$2"

  # Strip anchors (#section)
  linkpath="${linkpath%%#*}"

  # Skip external URLs
  [[ "$linkpath" =~ ^https?:// ]] && return 0
  [[ -z "$linkpath" ]] && return 0

  # Make relative to base directory if not absolute
  local fullpath
  if [[ "$linkpath" == /* ]]; then
    fullpath="${REPO_ROOT}${linkpath}"
  else
    fullpath="$(cd "$basedir" 2>/dev/null && pwd)/$linkpath"
  fi

  # Resolve relative path components
  fullpath="$(cd "$(dirname "$fullpath" 2>/dev/null)" 2>/dev/null && pwd)/${linkpath##*/}" || fullpath=""

  if [[ -f "$fullpath" ]] || [[ -d "$fullpath" ]]; then
    return 0
  fi

  return 1
}

main() {
  parse_args "$@"

  local exit_code=0
  local broken_count=0

  # Find all .md files in SignalOS (or repo root if not specified)
  local md_files
  md_files=$(find "${REPO_ROOT}" -name "*.md" -type f 2>/dev/null || echo "")

  while IFS= read -r mdfile; do
    [[ -z "$mdfile" ]] && continue
    [[ ! -f "$mdfile" ]] && continue

    local basedir=$(dirname "$mdfile")

    # Extract markdown links [text](path)
    local links
    links=$(grep -o '\[.*\](.*)[^)]' "$mdfile" 2>/dev/null || echo "")

    while IFS= read -r line; do
      [[ -z "$line" ]] && continue

      # Extract path from [text](path) using sed
      local linkpath
      linkpath=$(echo "$line" | sed -n 's/.*(\([^)]*\)).*/\1/p')

      if [[ -n "$linkpath" ]]; then
        if ! resolve_path "$linkpath" "$basedir"; then
          echo "✗ BROKEN PATH: $mdfile references $(basename "$linkpath") which does not exist" >&2
          broken_count=$((broken_count + 1))
          exit_code=1
        fi
      fi
    done <<< "$links"
  done <<< "$md_files"

  if [[ $broken_count -gt 0 ]]; then
    echo "✗ Path Consistency Guard: $broken_count broken path reference(s)" >&2
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  exit 0
}

main "$@"
