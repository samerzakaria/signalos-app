#!/usr/bin/env bash
# test-installer.sh — SignalOS installer test checklist
#
# Run this manually after each release build to verify install / upgrade / uninstall
# paths work correctly on the target platform.
#
# Usage:
#   ./scripts/test-installer.sh [macos|windows|linux]
#
# Exit codes: 0 = all passed, 1 = one or more checks failed
#
# Each check prints PASS / FAIL and records the result.
# At the end, a summary is printed and the script exits non-zero if anything failed.

set -euo pipefail

PLATFORM="${1:-$(uname -s | tr '[:upper:]' '[:lower:]')}"
PASS=0; FAIL=0

check() {
  local desc="$1"
  local result="$2"   # "pass" or "fail"
  if [[ "$result" == "pass" ]]; then
    echo "  ✓ $desc"
    PASS=$((PASS+1))
  else
    echo "  ✗ $desc"
    FAIL=$((FAIL+1))
  fi
}

manual_check() {
  local desc="$1"
  echo ""
  echo "  ▷ MANUAL: $desc"
  echo -n "    Did it pass? [y/N]: "
  read -r ans
  if [[ "$ans" =~ ^[Yy] ]]; then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1))
    echo "    (recorded as FAIL)"
  fi
}

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  SignalOS Installer Test — $PLATFORM"
echo "═══════════════════════════════════════════════════════"

# ── 1. FRESH INSTALL ─────────────────────────────────────────────────────────
echo ""
echo "1. Fresh Install"

manual_check "Download installer from GitHub Releases for your platform"
manual_check "Installer opens without OS security warning (signed / notarized)"
manual_check "App installs to expected location (Applications/ on macOS, Program Files on Windows)"
manual_check "App launches successfully after install"
manual_check "Welcome / onboarding screen appears (no prior config)"
manual_check "Workspace picker opens and accepts a valid project folder"
manual_check "API key entry works and 'has_api_key' returns true after save"
manual_check "First chat message sends and receives a response"

# ── 2. WINDOW STATE ───────────────────────────────────────────────────────────
echo ""
echo "2. Window State"

manual_check "Resize window → quit → reopen → window remembers size (within ~20px)"
manual_check "Move window → quit → reopen → window remembers position"

# ── 3. NATIVE MENU ───────────────────────────────────────────────────────────
echo ""
echo "3. Native Menu"

manual_check "File → Open Workspace… opens workspace picker"
manual_check "View → Chat (Cmd/Ctrl+1) navigates to chat panel"
manual_check "View → Dashboard (Cmd/Ctrl+2) navigates to dashboard"
manual_check "View → Brain (Cmd/Ctrl+3) navigates to brain panel"
manual_check "Help → Check for Updates… shows either banner or 'up to date' toast"
manual_check "Help → SignalOS Docs opens browser at docs.signalos.io"

# ── 4. AUTO-UPDATE ────────────────────────────────────────────────────────────
echo ""
echo "4. Auto-Update"

manual_check "Install v1.0.0 (older build if available)"
manual_check "Launch app → update banner appears after ~2s with correct version"
manual_check "Dismiss button hides the banner without crashing"

# ── 5. UPGRADE PATH ──────────────────────────────────────────────────────────
echo ""
echo "5. Upgrade"

manual_check "Install new version over existing install (same workspace, same API key)"
manual_check "API key is preserved after upgrade (OS keychain not wiped)"
manual_check "Workspace path is preserved after upgrade"
manual_check "Brain entries are preserved after upgrade"

# ── 6. UNINSTALL ─────────────────────────────────────────────────────────────
echo ""
echo "6. Uninstall"

if [[ "$PLATFORM" == "macos" ]]; then
  manual_check "Drag app to Trash — app removed from Applications/"
  manual_check "~/Library/Application Support/io.signalos.app/ can be manually removed for clean uninstall"
elif [[ "$PLATFORM" == "windows" ]]; then
  manual_check "Add/Remove Programs → uninstall — app removed cleanly"
  manual_check "%APPDATA%\\io.signalos.app\\ can be manually removed for clean uninstall"
else
  manual_check "AppImage can be deleted — no system-level install artifacts left"
fi

# ── 7. PERF CHECKS ───────────────────────────────────────────────────────────
echo ""
echo "7. Performance"

manual_check "Cold start (first launch after install) < 5 seconds to interactive"
manual_check "Warm start (relaunch) < 3 seconds to interactive"
manual_check "Chat response arrives in < 3 seconds for a simple /signal-status query"

# ── SUMMARY ───────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Results: $PASS passed, $FAIL failed"
echo "═══════════════════════════════════════════════════════"

if [[ $FAIL -gt 0 ]]; then
  echo "  INSTALLER TEST FAILED — $FAIL check(s) need attention"
  exit 1
else
  echo "  All checks passed ✓"
  exit 0
fi
