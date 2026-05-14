#!/usr/bin/env bash
# SignalOS installed-app manual checklist.
#
# This script records a human clean-machine test. It does not fake installer,
# signing, update, or provider proof. Run it after building an installer and
# testing on the target platform.
#
# Usage:
#   ./scripts/test-installer.sh [windows|macos|linux]

set -euo pipefail

PLATFORM="${1:-$(uname -s | tr '[:upper:]' '[:lower:]')}"
PASS=0
FAIL=0

manual_check() {
  local desc="$1"
  echo ""
  echo "MANUAL: $desc"
  printf "Did it pass? [y/N]: "
  read -r ans
  if [[ "$ans" =~ ^[Yy] ]]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "Recorded as FAIL"
  fi
}

section() {
  echo ""
  echo "== $1 =="
}

echo ""
echo "SignalOS Installed-App Checklist - $PLATFORM"
echo "Use this on a clean machine or clean VM. The SignalOS app repo must not be present."
echo "Unsigned internal builds may show OS trust warnings. Signed release builds must not."
echo "Before this manual checklist, run the installer-only runtime smoke on the build machine."

section "1. Installer"
manual_check "Install SignalOS from the generated installer only"
manual_check "Confirm the SignalOS source repo is not present on the test machine"
manual_check "Launch SignalOS from the installed app shortcut/menu"
manual_check "Confirm the app opens without requiring a terminal"
manual_check "Confirm the main workspace scrolls inside the app window"

section "2. First Project"
manual_check "Choose a new writable project folder"
manual_check "Confirm the project name and path appear in the app"
manual_check "Open Settings and confirm the project path is visible"
manual_check "Restart the app and confirm the project is remembered"

section "3. AI Setup"
manual_check "Select an AI provider"
manual_check "Paste an API key once, or select Ollama with a local running model"
manual_check "Fetch models from the provider"
manual_check "Select a fetched model"
manual_check "Switch to Other model and confirm manual model entry works"
manual_check "Save and test AI connection"
manual_check "Confirm the raw API key is not displayed after save"
manual_check "Delete or replace the saved key from Settings"

section "4. Chat And Commands"
manual_check "Send a plain chat message and receive a provider response"
manual_check "Run /signal-status and see a command result"
manual_check "Run /signal-init and see progress while it runs"
manual_check "Confirm setup/status results remain visible after navigation"
manual_check "Confirm command catalog labels ready, advanced, and preview commands"

section "5. Project Artifacts"
manual_check "Confirm .signalos/ exists in the selected project after setup"
manual_check "Confirm core/strategy/PLAN.md exists or is preserved"
manual_check "Confirm project artifacts are listed in the app"
manual_check "Open a listed artifact/path from the app"
manual_check "Confirm the app explains the next action after setup/status"

section "6. Dashboard, Guide, Notes, And History"
manual_check "Open Dashboard and confirm project, AI, engine, next action, gates, and files are visible"
manual_check "Confirm the first-project checklist shows project, AI, setup, status, first note, and first gate action"
manual_check "Open Guide and confirm phases behave like tabs"
manual_check "Apply a project template from Guide"
manual_check "Add a Note/Brain entry"
manual_check "Search for the Note/Brain entry"
manual_check "Open History and confirm recent activity is visible"
manual_check "Export a team handoff report from Dashboard or History"

section "7. Settings And Safety"
manual_check "Confirm Settings is operational, not read-only"
manual_check "Confirm secret file summaries show names/metadata without raw secret values"
manual_check "Confirm budget controls can save monthly budget and reset session"
manual_check "Confirm engine ping works"
manual_check "Confirm engine restart works"
manual_check "Confirm redacted diagnostics can be copied"
manual_check "Export a redacted issue report"

section "8. Gate Signing"
manual_check "Open gate signing UI"
manual_check "Enter a signer name"
manual_check "Run a gate signing action"
manual_check "Confirm the signing command result is visible"

section "9. Persistence"
manual_check "Close and reopen SignalOS"
manual_check "Confirm project selection persisted"
manual_check "Confirm provider/model selection persisted"
manual_check "Confirm API key is still saved but hidden"
manual_check "Confirm chat/command transcript persisted for the project"

section "10. Install Lifecycle"
manual_check "Install a newer build over the existing build and confirm state is preserved"
manual_check "Uninstall SignalOS through the normal OS path"
manual_check "Confirm the app is removed cleanly"

section "11. Signing And Updates"
manual_check "Signed build avoids scary OS trust warnings (mark FAIL for unsigned public release)"
manual_check "Switch update channel between beta and stable"
manual_check "Check for Updates returns a meaningful result"
manual_check "Older signed build updates to newer signed build"
manual_check "Signed update manifests contain valid signatures"

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [[ $FAIL -gt 0 ]]; then
  echo "INSTALLER TEST FAILED"
  exit 1
fi

echo "INSTALLER TEST PASSED"
