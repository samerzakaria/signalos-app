#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — Claude Code emitter: W1.1 hook registration.
#
# Emits Claude Code's native hooks descriptor at:
#   <output-dir>/.claude/hooks.json
#
# For each of the 4 W1.1 events (step-started, step-completed,
# step-failed, pre-session-compress) the descriptor names the event and
# points to core/execution/hooks/<event>/ — Claude Code's agent layer
# subscribes to the event and invokes the script. This file does NOT
# re-register the 5 pre-existing events; those remain owned by the
# git/CI pipeline.

# W9-W14 wave commands registered in commands.json and dispatched via this emitter:
#   W9  (brain):        signalos-brain (put/search/list/prune/export/upgrade), signal-learn
#   W10 (cso):          signal-cso
#   W11 (autoplan):     signal-autoplan, signal-context-restore
#   W12 (deploy):       signal-setup-deploy, signal-land-deploy, signal-canary-deploy, signal-benchmark
#   W13 (devex/retro):  signal-devex-plan, signal-devex, signal-retro-global
#   W14 (safety):       signal-careful, signal-freeze, signal-guard, signal-unfreeze
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="${SCRIPT_DIR}/../../_shared/hook-registration-helper.sh"
# shellcheck source=../../_shared/hook-registration-helper.sh
source "$HELPER"

OUTPUT_DIR="${1:-${PWD}}"
mkdir -p "${OUTPUT_DIR}/.claude"
DEST="${OUTPUT_DIR}/.claude/hooks.json"

{
  printf '[\n'
  first=1
  while IFS= read -r event; do
    [[ $first -eq 1 ]] || printf ',\n'
    first=0
    printf '  {"event": "%s", "command": "bash core/execution/hooks/%s/%s.sh"}' \
      "$event" "$event" "$event"
  done < <(signalos_w11_hooks_registered)
  printf '\n]\n'
} > "$DEST"

echo "claude-code/register-hooks.sh: wrote $DEST"
