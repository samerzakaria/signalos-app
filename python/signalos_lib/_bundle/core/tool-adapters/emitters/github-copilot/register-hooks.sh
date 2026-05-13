#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — GitHub Copilot emitter: W1.1 hook registration.
#
# GitHub Copilot's surface is .github/copilot-instructions.md plus
# .github/copilot-chat-agents.json. Neither file has a first-class
# hook schema, so we emit a sidecar:
#     .github/copilot-hooks.json
# and append a "SignalOS W1.1 hooks" section to copilot-instructions.md
# so the chat agent sees the wiring in its system prompt.

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
mkdir -p "${OUTPUT_DIR}/.github"
SIDECAR="${OUTPUT_DIR}/.github/copilot-hooks.json"
INSTRUCTIONS="${OUTPUT_DIR}/.github/copilot-instructions.md"

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
} > "$SIDECAR"

# Append (idempotent) the section to copilot-instructions.md.
marker='<!-- signalos-w11-hooks -->'
if [[ ! -f "$INSTRUCTIONS" ]] || ! grep -q "$marker" "$INSTRUCTIONS" 2>/dev/null; then
  {
    echo ""
    echo "$marker"
    echo "## SignalOS W1.1 hooks"
    echo ""
    while IFS= read -r event; do
      echo "- **${event}** — \`core/execution/hooks/${event}/${event}.sh\`"
    done < <(signalos_w11_hooks_registered)
  } >> "$INSTRUCTIONS"
fi

echo "github-copilot/register-hooks.sh: wrote $SIDECAR and updated $INSTRUCTIONS"
