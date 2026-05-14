param(
  [string]$Platform = "windows"
)

$ErrorActionPreference = "Stop"

$pass = 0
$fail = 0

function Add-ManualCheck {
  param([string]$Description)

  Write-Host ""
  Write-Host "MANUAL: $Description"
  $answer = Read-Host "Did it pass? [y/N]"
  if ($answer -match "^[Yy]") {
    $script:pass++
  } else {
    $script:fail++
    Write-Host "Recorded as FAIL"
  }
}

function Add-Section {
  param([string]$Title)

  Write-Host ""
  Write-Host "== $Title =="
}

Write-Host ""
Write-Host "SignalOS Installed-App Checklist - $Platform"
Write-Host "Use this on a clean machine or clean VM. The SignalOS app repo must not be present."
Write-Host "Unsigned internal builds may show OS trust warnings. Signed release builds must not."

$nsis = Get-ChildItem -Path "src-tauri\target\release\bundle\nsis" -Filter "*.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$msi = Get-ChildItem -Path "src-tauri\target\release\bundle\msi" -Filter "*.msi" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1

if ($nsis) {
  Write-Host "Latest NSIS: $($nsis.FullName)"
}
if ($msi) {
  Write-Host "Latest MSI:  $($msi.FullName)"
}
if (-not $nsis -and -not $msi) {
  Write-Host "No local Windows installer artifact found. Build one with scripts\verify-release.ps1 -BuildInstaller -SmokeInstalledBuild -InstallNsisSmoke."
}

Write-Host "Before this manual checklist, run:"
Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -InstalledRuntimeSmoke"

Add-Section "1. Installer"
Add-ManualCheck "Install SignalOS from the generated installer only"
Add-ManualCheck "Confirm the SignalOS source repo is not present on the test machine"
Add-ManualCheck "Launch SignalOS from the installed app shortcut/menu"
Add-ManualCheck "Confirm the app opens without requiring a terminal"
Add-ManualCheck "Confirm the main workspace scrolls inside the app window"

Add-Section "2. First Project"
Add-ManualCheck "Choose a new writable project folder"
Add-ManualCheck "Confirm the project name and path appear in the app"
Add-ManualCheck "Open Settings and confirm the project path is visible"
Add-ManualCheck "Restart the app and confirm the project is remembered"

Add-Section "3. AI Setup"
Add-ManualCheck "Select an AI provider"
Add-ManualCheck "Paste an API key once, or select Ollama with a local running model"
Add-ManualCheck "Fetch models from the provider"
Add-ManualCheck "Select a fetched model"
Add-ManualCheck "Switch to Other model and confirm manual model entry works"
Add-ManualCheck "Save and test AI connection"
Add-ManualCheck "Confirm the raw API key is not displayed after save"
Add-ManualCheck "Delete or replace the saved key from Settings"

Add-Section "4. Chat And Commands"
Add-ManualCheck "Send a plain chat message and receive a provider response"
Add-ManualCheck "Run /signal-status and see a command result"
Add-ManualCheck "Run /signal-init and see progress while it runs"
Add-ManualCheck "Confirm setup/status results remain visible after navigation"
Add-ManualCheck "Confirm command catalog labels ready, advanced, and preview commands"

Add-Section "5. Project Artifacts"
Add-ManualCheck "Confirm .signalos/ exists in the selected project after setup"
Add-ManualCheck "Confirm core/strategy/PLAN.md exists or is preserved"
Add-ManualCheck "Confirm project artifacts are listed in the app"
Add-ManualCheck "Open a listed artifact/path from the app"
Add-ManualCheck "Confirm the app explains the next action after setup/status"

Add-Section "6. Dashboard, Guide, Notes, And History"
Add-ManualCheck "Open Dashboard and confirm project, AI, engine, next action, gates, and files are visible"
Add-ManualCheck "Confirm the first-project checklist shows project, AI, setup, status, first note, and first gate action"
Add-ManualCheck "Open Guide and confirm phases behave like tabs"
Add-ManualCheck "Apply a project template from Guide"
Add-ManualCheck "Add a Note/Brain entry"
Add-ManualCheck "Search for the Note/Brain entry"
Add-ManualCheck "Open History and confirm recent activity is visible"
Add-ManualCheck "Export a team handoff report from Dashboard or History"

Add-Section "7. Settings And Safety"
Add-ManualCheck "Confirm Settings is operational, not read-only"
Add-ManualCheck "Confirm secret file summaries show names/metadata without raw secret values"
Add-ManualCheck "Confirm budget controls can save monthly budget and reset session"
Add-ManualCheck "Confirm engine ping works"
Add-ManualCheck "Confirm engine restart works"
Add-ManualCheck "Confirm redacted diagnostics can be copied"
Add-ManualCheck "Export a redacted issue report"

Add-Section "8. Gate Signing"
Add-ManualCheck "Open gate signing UI"
Add-ManualCheck "Enter a signer name"
Add-ManualCheck "Run a gate signing action"
Add-ManualCheck "Confirm the signing command result is visible"

Add-Section "9. Persistence"
Add-ManualCheck "Close and reopen SignalOS"
Add-ManualCheck "Confirm project selection persisted"
Add-ManualCheck "Confirm provider/model selection persisted"
Add-ManualCheck "Confirm API key is still saved but hidden"
Add-ManualCheck "Confirm chat/command transcript persisted for the project"

Add-Section "10. Install Lifecycle"
Add-ManualCheck "Install a newer build over the existing build and confirm state is preserved"
Add-ManualCheck "Uninstall SignalOS through the normal OS path"
Add-ManualCheck "Confirm the app is removed cleanly"

Add-Section "11. Signing And Updates"
Add-ManualCheck "Signed build avoids scary OS trust warnings (mark FAIL for unsigned public release)"
Add-ManualCheck "Switch update channel between beta and stable"
Add-ManualCheck "Check for Updates returns a meaningful result"
Add-ManualCheck "Older signed build updates to newer signed build"
Add-ManualCheck "Signed update manifests contain valid signatures"

Write-Host ""
Write-Host "Results: $pass passed, $fail failed"

if ($fail -gt 0) {
  Write-Host "INSTALLER TEST FAILED"
  exit 1
}

Write-Host "INSTALLER TEST PASSED"
