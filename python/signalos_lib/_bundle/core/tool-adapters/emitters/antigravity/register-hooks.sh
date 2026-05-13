#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — Antigravity emitter: W1.1 hook registration.
#
# Antigravity's config surface is .antigravity/rules.md (preamble) plus
# .antigravity/commands/*.md (one file per command). We render a
# dedicated .antigravity/hooks.md that lists the 4 W1.1 events and
# their scripts — the Antigravity agent reads every file under
# .antigravity/ at session start.

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
mkdir -p "${OUTPUT_DIR}/.antigravity"
DEST="${OUTPUT_DIR}/.antigravity/hooks.md"

{
  echo "# SignalOS W1.1 hooks"
  echo ""
  echo "| Event | Script |"
  echo "|---|---|"
  while IFS= read -r event; do
    echo "| ${event} | core/execution/hooks/${event}/${event}.sh |"
  done < <(signalos_w11_hooks_registered)
} > "$DEST"

echo "antigravity/register-hooks.sh: wrote $DEST"
