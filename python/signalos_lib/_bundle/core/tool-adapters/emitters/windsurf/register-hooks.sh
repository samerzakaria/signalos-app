#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — Windsurf emitter: W1.1 hook registration.
#
# W9-W14 wave commands registered in commands.json and dispatched via this emitter:
#   W9  (brain):        signalos-brain (put/search/list/prune/export/upgrade), signal-learn
#   W10 (cso):          signal-cso
#   W11 (autoplan):     signal-autoplan, signal-context-restore
#   W12 (deploy):       signal-setup-deploy, signal-land-deploy, signal-canary-deploy, signal-benchmark
#   W13 (devex/retro):  signal-devex-plan, signal-devex, signal-retro-global
#   W14 (safety):       signal-careful, signal-freeze, signal-guard, signal-unfreeze
#
# Windsurf's config surface is a single .windsurfrules markdown file.
# We append a "## SignalOS W1.1 hooks" section listing the 4 events
# and their scripts. Idempotent via a marker comment so re-running
# the installer does not duplicate the section.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="${SCRIPT_DIR}/../../_shared/hook-registration-helper.sh"
# shellcheck source=../../_shared/hook-registration-helper.sh
source "$HELPER"

OUTPUT_DIR="${1:-${PWD}}"
DEST="${OUTPUT_DIR}/.windsurfrules"
marker='<!-- signalos-w11-hooks -->'

mkdir -p "$OUTPUT_DIR"
touch "$DEST"

if ! grep -q "$marker" "$DEST" 2>/dev/null; then
  {
    echo ""
    echo "$marker"
    echo "## SignalOS W1.1 hooks"
    echo ""
    while IFS= read -r event; do
      echo "- **${event}** -> \`core/execution/hooks/${event}/${event}.sh\`"
    done < <(signalos_w11_hooks_registered)
  } >> "$DEST"
fi

echo "windsurf/register-hooks.sh: updated $DEST"
