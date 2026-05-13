#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — Shared hook-registration helper used by all 7
# editor emitters (+ the 8th headless emitter in W1.2).
#
# Purpose: every emitter's settings.json / config.json / equivalent has
# the same 4 W1.1 hooks to register:
#   - step-started           -> core/execution/hooks/step-started
#   - step-completed         -> core/execution/hooks/step-completed
#   - step-failed            -> core/execution/hooks/step-failed
#   - pre-session-compress   -> core/execution/hooks/pre-session-compress
#
# This helper centralises the naming + paths so each emitter's patch is
# ~10 lines instead of 50.
#
# Usage from an emitter patch script:
#     source "$(git rev-parse --show-toplevel)/core/tool-adapters/_shared/hook-registration-helper.sh"
#     signalos_w11_hooks_registered | while read -r event; do
#         # emitter-specific wiring of each event
#     done
#
# The helper exits non-zero if any expected hook script is missing from
# disk, so a misconfigured repo never ships.

set -euo pipefail

# List the 4 new hook events introduced in W1.1.
signalos_w11_hooks_registered() {
  printf '%s\n' \
    "step-started" \
    "step-completed" \
    "step-failed" \
    "pre-session-compress"
}

# Resolve the on-disk script path for a given hook event. Exits non-zero
# if the script is not executable.
signalos_hook_path() {
  local event="$1"
  local root
  root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  local path="${root}/core/execution/hooks/${event}"
  if [[ ! -x "$path" ]]; then
    echo "hook-registration-helper.sh: missing or non-executable hook: $path" >&2
    return 1
  fi
  printf '%s\n' "$path"
}

# Self-check: enumerate every hook this helper claims to own and verify
# the script exists. Called by scenario 08_hook_dispatch (extended) and
# by each emitter's install-test scenario.
signalos_hook_selfcheck() {
  local event path
  while read -r event; do
    path="$(signalos_hook_path "$event")" || return 1
    echo "ok: $event -> $path"
  done < <(signalos_w11_hooks_registered)
}

# If sourced, export nothing extra. If executed directly with --selfcheck,
# run the selfcheck and exit.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-}" in
    --selfcheck) signalos_hook_selfcheck ;;
    --list)      signalos_w11_hooks_registered ;;
    *)
      echo "Usage: hook-registration-helper.sh [--selfcheck|--list]" >&2
      exit 1 ;;
  esac
fi
