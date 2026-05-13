#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — VS Code emitter: W1.1 hook registration.
#
# VS Code has no first-class "hooks" concept; its nearest analogue is
# declarative entries inside .vscode/settings.json. We emit a sidecar
# JSON at .vscode/signalos-hooks.json so the installer or a user's own
# task runner can consume it. We also write a stanza into
# .vscode/settings.json under the "signalos.hooks" key so extensions
# that subscribe to workspace settings can enumerate the wiring.
#
# DECLARATIVE ONLY — no Node build step, no package.json, no extension
# activation. The hook target strings are POSIX paths the user or CI
# runs via bash.

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
mkdir -p "${OUTPUT_DIR}/.vscode"
SIDECAR="${OUTPUT_DIR}/.vscode/signalos-hooks.json"
SETTINGS="${OUTPUT_DIR}/.vscode/settings.json"

# Sidecar descriptor — authoritative list of the 4 events.
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

# Best-effort merge into settings.json. If the file does not yet exist
# or cannot be merged safely, write a minimal settings that just holds
# the signalos.hooks stanza. No Node; jq is the only tool used.
if command -v jq >/dev/null 2>&1 && [[ -f "$SETTINGS" ]]; then
  tmp="$(mktemp)"
  jq --slurpfile hooks "$SIDECAR" '. + {"signalos.hooks": $hooks[0]}' "$SETTINGS" > "$tmp"
  mv "$tmp" "$SETTINGS"
else
  {
    echo "{"
    echo "  \"github.copilot.codeCompletions.enabled\": true,"
    echo "  \"signalos.hooks\": $(cat "$SIDECAR")"
    echo "}"
  } > "$SETTINGS"
fi

echo "vs-code/register-hooks.sh: wrote $SIDECAR and updated $SETTINGS"
