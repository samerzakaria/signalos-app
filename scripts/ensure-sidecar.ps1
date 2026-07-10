<#
.SYNOPSIS
  Windows counterpart of scripts/ensure-sidecar.sh — make sure
  src-tauri/bin/signalos-python-<triple>.exe exists AND is fresh for the
  current target before `cargo check` / `cargo build`.

.DESCRIPTION
  "Exists" is NOT sufficient (Claim 1b). A committed-but-stale sidecar (the
  shipped 0.0.9 binary answers "Unknown command: agent:deliver") must be
  caught. A fresh sidecar answers the `capabilities` handshake with a command
  list that includes agent:deliver; a stale one does not.

  Modes (mirrors the .sh variant's --build / --check / lint-stub behavior):
    (default)   Lint: stub the file if missing so cargo proceeds. If a real but
                stale binary is present, fail loudly (exit 1).
    -Build      Build the real sidecar via bundle-sidecar.ps1 when missing or
                stale, then verify it reports agent:deliver.
    -Check      Verify freshness only (no build). Exit 1 if missing/stub/stale.

.EXAMPLE
  pwsh scripts/ensure-sidecar.ps1            # lint-only stub if missing
  pwsh scripts/ensure-sidecar.ps1 -Build     # build/refresh the real sidecar
  pwsh scripts/ensure-sidecar.ps1 -Check     # freshness gate (release preflight)
#>
param(
  [switch]$Build,
  [switch]$Check
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$binDir = Join-Path $root "src-tauri\bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

function Resolve-HostTriple {
  $hostLine = rustc -Vv | Select-String "^host:"
  if (-not $hostLine) {
    throw "Could not determine Rust host target triple (is rustc on PATH?)."
  }
  return ($hostLine.ToString() -split "\s+")[1]
}

$targetTriple = Resolve-HostTriple
$isWindows = ($env:OS -eq "Windows_NT") -or ($targetTriple -match "windows")
$sidecarName = if ($isWindows) { "signalos-python-$targetTriple.exe" } else { "signalos-python-$targetTriple" }
$sidecarPath = Join-Path $binDir $sidecarName

Write-Host "[ensure-sidecar] target triple: $targetTriple"
Write-Host "[ensure-sidecar] expected:      $sidecarPath"

# ─── Freshness helpers ──────────────────────────────────────────────────────

# A lint stub is a few bytes; a real PyInstaller onefile is tens of MB. Anything
# under 100 KB is treated as a stub (never executed), so the freshness probe is
# skipped for it.
function Test-SidecarLooksLikeStub {
  param([string]$Path)
  try { return ((Get-Item -LiteralPath $Path).Length -lt 102400) }
  catch { return $true }
}

# Probe the binary's capability handshake. Returns $true only when agent:deliver
# is reported. The sidecar exits on stdin EOF, so one piped line is enough.
function Test-SidecarReportsAgentDeliver {
  param([string]$Path)
  try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Path
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    if (-not $proc) { return $false }
    $proc.StandardInput.WriteLine('{"id":"__ensure_probe__","command":"capabilities","args":[]}')
    $proc.StandardInput.Close()
    $out = $proc.StandardOutput.ReadToEnd()
    if (-not $proc.WaitForExit(60000)) {
      try { $proc.Kill() } catch {}
    }
    return ($out -match "agent:deliver")
  } catch {
    return $false
  }
}

function Get-SourceVersion {
  try {
    return (Get-Content (Join-Path $root "package.json") -Raw | ConvertFrom-Json).version
  } catch {
    return ""
  }
}

function Invoke-SidecarRebuild {
  Write-Host "[ensure-sidecar] building real sidecar via PyInstaller..."
  & (Join-Path $PSScriptRoot "bundle-sidecar.ps1")
  if (-not (Test-Path -LiteralPath $sidecarPath)) {
    Write-Host "[ensure-sidecar] FAIL: build did not produce $sidecarName"
    exit 1
  }
  if (-not (Test-SidecarReportsAgentDeliver $sidecarPath)) {
    Write-Host "[ensure-sidecar] FAIL: rebuilt sidecar still does not report agent:deliver."
    exit 1
  }
  Write-Host "[ensure-sidecar] OK: rebuilt and verified fresh (agent:deliver present)."
  exit 0
}

function Write-StaleMessage {
  Write-Host "[ensure-sidecar] STALE sidecar: it does not report agent:deliver."
  Write-Host "[ensure-sidecar]   on-disk binary : $sidecarPath"
  $sv = Get-SourceVersion
  if ($sv) { Write-Host "[ensure-sidecar]   source version : $sv (package.json)" }
  Write-Host "[ensure-sidecar]   The bundled engine is too old -- rebuild required:"
  Write-Host "[ensure-sidecar]     pwsh scripts/ensure-sidecar.ps1 -Build   (or scripts/bundle-sidecar.ps1)"
}

# ─── Existing binary ────────────────────────────────────────────────────────
if (Test-Path -LiteralPath $sidecarPath) {
  if (Test-SidecarLooksLikeStub $sidecarPath) {
    if ($Build) {
      Write-Host "[ensure-sidecar] existing file is a lint stub; building the real sidecar..."
      Invoke-SidecarRebuild
    }
    if ($Check) {
      Write-Host "[ensure-sidecar] existing file is a lint stub, not a real sidecar."
      exit 1
    }
    Write-Host "[ensure-sidecar] existing file looks like a lint stub (<100KB); skipping freshness probe."
    exit 0
  }
  if (Test-SidecarReportsAgentDeliver $sidecarPath) {
    Write-Host "[ensure-sidecar] OK: real sidecar reports agent:deliver; fresh -- nothing to do."
    exit 0
  }
  # A real but stale binary: rebuild when asked, otherwise fail loudly.
  Write-StaleMessage
  if ($Build) {
    Invoke-SidecarRebuild
  }
  exit 1
}

# ─── Missing binary ─────────────────────────────────────────────────────────
if ($Build) {
  Invoke-SidecarRebuild
}

if ($Check) {
  Write-Host "[ensure-sidecar] missing sidecar: $sidecarPath (run with -Build)."
  exit 1
}

# Lint mode: write a minimal stub. Tauri only checks the file's *existence*
# during the build-script; the stub is never executed.
Write-Host "[ensure-sidecar] stubbing $sidecarName (lint-only)..."
# A stub .exe must start with the MZ DOS signature so Windows tooling does not
# balk at a plaintext file wearing an .exe name.
[System.IO.File]::WriteAllBytes($sidecarPath, [byte[]](0x4D, 0x5A, 0x0A))
Write-Host "[ensure-sidecar] OK: wrote stub at $sidecarPath"
Write-Host "[ensure-sidecar] NOTE: this stub is for CI lint only -- bundle for real before release."
