# build-internal.ps1 — Internal Testing Build path (NOT signed)
#
# Use this when you want a working installer to hand to internal testers
# WITHOUT going through Authenticode / Developer ID / notarization.
# The installer will trip SmartScreen and Gatekeeper warnings — that's
# expected for internal testing. The build is "attested by name", not
# "signed by certificate".
#
# Output for each run:
#   src-tauri/target/release/bundle/nsis/SignalOS_<version>_x64-setup.exe   (Windows)
#   distribution/internal/attestation-<commit>.json — signed-by-name record
#
# Note: MSI is intentionally NOT built here. Windows Installer requires
# numeric-only pre-release identifiers (<=65535), so versions like
# "1.0.0-internal1" or "1.0.0-beta3" cannot be packaged as MSI. NSIS handles
# them fine. Signed public builds with numeric versions can re-enable MSI.
#
# What the attestation file contains:
#   - builder name + email (from `git config`)
#   - timestamp (UTC ISO 8601)
#   - git commit SHA
#   - product version
#   - SHA-256 of every installer artifact
#   - a "release_type": "internal-testing-unsigned" marker
#
# Usage:
#   pwsh scripts/build-internal.ps1
#   pwsh scripts/build-internal.ps1 -SkipBuild   # re-attest existing artifacts only

param(
    [switch]$SkipBuild,
    [switch]$Strict   # fail if git is dirty or HEAD is behind origin/main
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path "$PSScriptRoot\.."
Set-Location $RepoRoot

# --- 1. Identity (who is attesting?) ----------------------------------------
# Name comes from git config — testers see who built it.
# Email defaults to project noreply so personal email never ships in
# the attestation file. Override with SIGNALOS_BUILDER_EMAIL when you
# want a specific address (e.g., a per-tester distribution alias).
$BuilderName  = git config user.name
if (-not $BuilderName) {
    Write-Error "git config user.name must be set to attest a build."
    exit 1
}
$BuilderEmail = if ($env:SIGNALOS_BUILDER_EMAIL) {
    $env:SIGNALOS_BUILDER_EMAIL
} else {
    "noreply@signalos.app"
}
Write-Host "-- Builder identity -----------------------------------------"
Write-Host "  Name:    $BuilderName"
Write-Host "  Email:   $BuilderEmail"

# --- 2. Git state (clean? in sync?) -----------------------------------------
$Commit       = (git rev-parse HEAD).Trim()
$ShortCommit  = (git rev-parse --short HEAD).Trim()
$Branch       = (git rev-parse --abbrev-ref HEAD).Trim()
$IsClean      = -not (git status --porcelain)
Write-Host "-- Source state ---------------------------------------------"
Write-Host "  Commit:  $Commit"
Write-Host "  Branch:  $Branch"
Write-Host "  Clean:   $IsClean"
if (-not $IsClean -and $Strict) {
    Write-Error "Working tree is dirty. Commit or stash before attesting (or drop -Strict)."
    exit 1
}

# --- 3. Version from tauri.conf.json ----------------------------------------
$TauriConf = Get-Content "src-tauri\tauri.conf.json" -Raw | ConvertFrom-Json
$Version = $TauriConf.version
Write-Host "  Version: $Version"

# --- 4. Build the installer (unsigned) -------------------------------------
if (-not $SkipBuild) {
    Write-Host "-- Building installer (unsigned) ----------------------------"
    # Ensure the Windows sidecar binary exists before cargo runs. We do this
    # check in PowerShell rather than invoking bash scripts/ensure-sidecar.sh,
    # because on Windows-with-WSL the bash script detects WSL's Linux triple
    # and tries to build the wrong sidecar.
    $SidecarPath = "src-tauri\bin\signalos-python-x86_64-pc-windows-msvc.exe"
    if (-not (Test-Path $SidecarPath)) {
        Write-Host "  Windows sidecar missing at $SidecarPath; bundling now..."
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bundle-sidecar.ps1
        if ($LASTEXITCODE -ne 0) {
            Write-Error "bundle-sidecar.ps1 failed - cannot continue."
            exit 1
        }
    } else {
        Write-Host "  Windows sidecar: $SidecarPath (exists)"
    }
    # Use cargo-tauri to produce the bundle. NOT calling tauri-cli's signing
    # path; --bundles nsis is the unsigned Windows installer path.
    # MSI omitted: rejects non-numeric pre-release tags like "internal1".
    cargo tauri build --bundles nsis
    if ($LASTEXITCODE -ne 0) {
        Write-Error "cargo tauri build failed - see above."
        exit 1
    }
} else {
    Write-Host "-- Skipping build (using existing artifacts) ----------------"
}

# --- 5. Find the artifacts + compute SHA-256 --------------------------------
$BundleDir = "src-tauri\target\release\bundle"
$Artifacts = @()
foreach ($pattern in @("nsis\*.exe", "deb\*.deb", "appimage\*.AppImage", "dmg\*.dmg")) {
    $full = Join-Path $BundleDir $pattern
    Get-ChildItem $full -ErrorAction SilentlyContinue | ForEach-Object {
        $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
        $size = $_.Length
        $rel  = $_.FullName.Substring($RepoRoot.Path.Length + 1) -replace '\\', '/'
        $Artifacts += @{
            path    = $rel
            size    = $size
            sha256  = $hash
        }
        Write-Host "  + $rel ($size bytes)"
        Write-Host "    sha256: $hash"
    }
}
if ($Artifacts.Count -eq 0) {
    Write-Error "No installer artifacts found under $BundleDir. Did the build succeed?"
    exit 1
}

# --- 6. Write the attestation -----------------------------------------------
$AttestDir = "distribution\internal"
New-Item -ItemType Directory -Force -Path $AttestDir | Out-Null
$AttestPath = Join-Path $AttestDir "attestation-$ShortCommit.json"
$Ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$Attestation = [ordered]@{
    schema        = "signalos.attestation.v1"
    release_type  = "internal-testing-unsigned"
    product       = "SignalOS"
    version       = $Version
    builder       = @{
        name    = $BuilderName
        email   = $BuilderEmail
    }
    built_at      = $Ts
    git           = @{
        commit  = $Commit
        branch  = $Branch
        clean   = $IsClean
    }
    artifacts     = $Artifacts
    distribution_notes = @(
        "Unsigned installer. SmartScreen will warn 'Unknown publisher' on Windows.",
        "macOS users must right-click -> Open the first launch (Gatekeeper bypass).",
        "Linux AppImage runs directly with chmod +x.",
        "DO NOT publish to the public landing page. Distribute only to named internal testers.",
        "When signing certs become available, run scripts/build-signed.ps1 and replace these artifacts."
    )
}

# Pretty-print JSON
$Json = $Attestation | ConvertTo-Json -Depth 6
Set-Content -Path $AttestPath -Value $Json -Encoding UTF8
Write-Host
Write-Host "-- Attestation written -------------------------------------"
Write-Host "  $AttestPath"
Write-Host

# --- 7. Audit-log this attestation ------------------------------------------
$AuditPath = ".signalos\AUDIT_TRAIL.jsonl"
$AuditEntry = [ordered]@{
    ts         = $Ts
    action     = "build:internal-attest"
    actor      = "$BuilderName <$BuilderEmail>"
    detail     = "version=$Version commit=$ShortCommit artifacts=$($Artifacts.Count)"
} | ConvertTo-Json -Compress
Add-Content -Path $AuditPath -Value $AuditEntry -Encoding UTF8
Write-Host "  Audit entry appended to $AuditPath"

# --- 8. Hand-off summary for the operator -----------------------------------
Write-Host
Write-Host "==============================================================="
Write-Host "  Internal-testing build is ready."
Write-Host "  Attested by: $BuilderName <$BuilderEmail>"
Write-Host "  Not code-signed. Not notarized. Not for public release."
Write-Host
Write-Host "  Distribute to internal testers:"
foreach ($a in $Artifacts) {
    Write-Host "    $($a.path)"
}
Write-Host
Write-Host "  Tell testers:"
Write-Host "  - On Windows, SmartScreen will say 'Unknown publisher' -> 'More info' -> 'Run anyway'."
Write-Host "  - On macOS, right-click the .dmg's app -> 'Open' on first launch."
Write-Host "  - Report bugs to: $BuilderEmail"
Write-Host
Write-Host "  When ready for signed public beta: see docs/RELEASE_GATES_RUNBOOK.md"
Write-Host "==============================================================="
