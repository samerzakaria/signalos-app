#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — Cursor emitter: W1.1 hook registration.
#
# W9-W14 wave commands registered in commands.json and dispatched via this emitter:
#   W9  (brain):        signalos-brain (put/search/list/prune/export/upgrade), signal-learn
#   W10 (cso):          signal-cso
#   W11 (autoplan):     signal-autoplan, signal-context-restore
#   W12 (deploy):       signal-setup-deploy, signal-land-deploy, signal-canary-deploy, signal-benchmark
#   W13 (devex/retro):  signal-devex-plan, signal-devex, signal-retro-global
#   W14 (safety):       signal-careful, signal-freeze, signal-guard, signal-unfreeze
#
# Cursor's rules live under .cursor/rules/*.mdc with front-matter. We
# render a rules file that declares the 4 W1.1 hook events. Cursor's
# agent layer reads these entries on session start and invokes the
# matching script under core/execution/hooks/<event>/<event>.sh when
# the dispatcher emits the event.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="${SCRIPT_DIR}/../../_shared/hook-registration-helper.sh"
# shellcheck source=../../_shared/hook-registration-helper.sh
source "$HELPER"

OUTPUT_DIR="${1:-${PWD}}"
mkdir -p "${OUTPUT_DIR}/.cursor/rules"
DEST="${OUTPUT_DIR}/.cursor/rules/signalos-hooks.mdc"

{
  echo "---"
  echo "description: SignalOS W1.1 hook registrations"
  echo "globs:"
  echo "---"
  echo ""
  echo "# SignalOS W1.1 hooks"
  echo ""
  while IFS= read -r event; do
    echo "- event: $event"
    echo "  command: bash core/execution/hooks/${event}/${event}.sh"
  done < <(signalos_w11_hooks_registered)
} > "$DEST"

echo "cursor/register-hooks.sh: wrote $DEST"
