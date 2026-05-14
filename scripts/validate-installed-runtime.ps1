param(
  [switch]$UseReleaseTree,
  [switch]$KeepArtifacts,
  [string]$EvidencePath,
  [int]$TimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$ReleaseDir = Join-Path $Root "src-tauri\target\release"
$ReleaseSidecar = Join-Path $ReleaseDir "signalos-python.exe"
$NsisInstaller = Get-ChildItem -Path (Join-Path $Root "src-tauri\target\release\bundle\nsis") -Filter "*_x64-setup.exe" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$RunRoot = Join-Path $env:TEMP ("signalos-installed-runtime-" + [guid]::NewGuid().ToString("N"))
$InstallDir = Join-Path $RunRoot "install"
$ProjectDir = Join-Path $RunRoot "next-project"
$Evidence = New-Object System.Collections.Generic.List[object]

function Add-Evidence {
  param([string]$Step, [string]$Result)
  $Evidence.Add([pscustomobject]@{
    step = $Step
    result = $Result
  }) | Out-Null
  Write-Host "[PASS] $Step - $Result"
}

function Assert-True {
  param([bool]$Condition, [string]$Message)
  if (-not $Condition) { throw $Message }
}

function Assert-File {
  param([string]$Path, [string]$Name)
  Assert-True (Test-Path $Path) "Missing $Name at $Path"
}

function Assert-NoRunningSignalOS {
  $processes = Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -in @("SignalOS", "signalos-desktop", "signalos-python") }
  if ($processes) {
    $names = ($processes | ForEach-Object { "$($_.ProcessName) ($($_.Id))" }) -join ", "
    throw "SignalOS is already running: $names. Close SignalOS and retry."
  }
}

function Read-JsonLine {
  param([System.Diagnostics.Process]$Process, [string]$Label)
  $task = $Process.StandardOutput.ReadLineAsync()
  if (-not $task.Wait($TimeoutSeconds * 1000)) {
    throw "Timed out waiting for sidecar response: $Label"
  }
  $line = $task.Result
  if ([string]::IsNullOrWhiteSpace($line)) {
    throw "Sidecar returned an empty response for $Label"
  }
  try {
    return $line | ConvertFrom-Json
  } catch {
    throw "Sidecar returned non-JSON for ${Label}: $line"
  }
}

function Invoke-SidecarRequest {
  param(
    [System.Diagnostics.Process]$Process,
    [string]$Id,
    [string]$Command,
    [string[]]$RequestArgs = @()
  )
  $payload = @{
    id = $Id
    command = $Command
    args = @($RequestArgs)
    cwd = $ProjectDir
  } | ConvertTo-Json -Compress -Depth 8
  $Process.StandardInput.WriteLine($payload)
  $Process.StandardInput.Flush()
  return Read-JsonLine $Process $Id
}

function Assert-OkResponse {
  param($Response, [string]$Label)
  if (-not $Response.ok) {
    $message = $Response.error
    if (-not $message) { $message = "unknown error" }
    throw "$Label failed: $message"
  }
}

function Start-Sidecar {
  param([string]$SidecarPath)
  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $SidecarPath
  $psi.WorkingDirectory = $ProjectDir
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  [void]$psi.EnvironmentVariables.Remove("PYTHONPATH")
  [void]$psi.EnvironmentVariables.Remove("PYTHONHOME")
  $signalKeys = @($psi.EnvironmentVariables.Keys | Where-Object { $_ -like "SIGNALOS_*" })
  foreach ($key in $signalKeys) {
    [void]$psi.EnvironmentVariables.Remove($key)
  }
  return [System.Diagnostics.Process]::Start($psi)
}

function Write-EvidenceFile {
  if (-not $EvidencePath) { return }
  $target = if ([System.IO.Path]::IsPathRooted($EvidencePath)) {
    $EvidencePath
  } else {
    Join-Path $Root $EvidencePath
  }
  $dir = Split-Path -Parent $target
  if ($dir) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $lines = @(
    "# SignalOS Installed Runtime Evidence",
    "",
    "Date: $(Get-Date -Format s)",
    "",
    "Runtime source: $script:RuntimeSource",
    "",
    "Project: $ProjectDir",
    "",
    "## Passed Checks",
    ""
  )
  foreach ($item in $Evidence) {
    $lines += "- $($item.step): $($item.result)"
  }
  Set-Content -Path $target -Value ($lines -join "`n") -Encoding UTF8
}

Write-Host "SignalOS installed-runtime validation"
Assert-NoRunningSignalOS
Assert-File $ReleaseSidecar "release sidecar"
New-Item -ItemType Directory -Path $RunRoot -Force | Out-Null
New-Item -ItemType Directory -Path $ProjectDir -Force | Out-Null

$RootPath = (Resolve-Path $Root).Path
$ProjectPath = (Resolve-Path $ProjectDir).Path
Assert-True (-not $ProjectPath.StartsWith($RootPath, [StringComparison]::OrdinalIgnoreCase)) "Runtime project must be outside the signalos-app repo."

$SidecarPath = $ReleaseSidecar
$script:RuntimeSource = "release tree"

if (-not $UseReleaseTree) {
  if (-not $NsisInstaller) {
    throw "Missing NSIS installer under src-tauri\target\release\bundle\nsis. Run verify-release.ps1 -BuildInstaller first."
  }
  New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
  $install = Start-Process -FilePath $NsisInstaller.FullName -ArgumentList @("/S", "/D=$InstallDir") -Wait -PassThru
  if ($install.ExitCode -ne 0) {
    throw "NSIS install failed with code $($install.ExitCode). Close SignalOS if it is running, then retry."
  }
  $installedSidecar = Get-ChildItem -Path $InstallDir -Recurse -Filter "signalos-python.exe" | Select-Object -First 1
  $installedApp = Get-ChildItem -Path $InstallDir -Recurse -Filter "signalos-desktop.exe" | Select-Object -First 1
  if (-not $installedSidecar) { throw "Installed package did not include signalos-python.exe." }
  if (-not $installedApp) { throw "Installed package did not include signalos-desktop.exe." }
  $SidecarPath = $installedSidecar.FullName
  $script:RuntimeSource = "NSIS installed package"
  Add-Evidence "NSIS installer-only install" "Installed to a temp folder outside the repo."
}

$sidecar = $null
try {
  $sidecar = Start-Sidecar $SidecarPath
  $init = Read-JsonLine $sidecar "startup"
  Assert-OkResponse $init "sidecar startup"
  Add-Evidence "Bundled engine startup" "JSON IPC startup response received."

  $ping = Invoke-SidecarRequest $sidecar "ping" "ping"
  Assert-OkResponse $ping "ping"
  Assert-True ([bool]$ping.data.pong) "Ping response did not include pong=true."
  Add-Evidence "Engine ping" "Sidecar responded with pong."

  $setup = Invoke-SidecarRequest $sidecar "signal-init" "/signal-init"
  Assert-OkResponse $setup "/signal-init"
  Assert-File (Join-Path $ProjectDir ".signalos") "runtime state folder"
  Assert-File (Join-Path $ProjectDir "core\strategy\PLAN.md") "project plan"
  Assert-File (Join-Path $ProjectDir "core\execution\commands") "command library"
  Add-Evidence "Project setup" "/signal-init created runtime state, plan, and command library."

  Set-Content -Path (Join-Path $ProjectDir ".env") -Value "OPENAI_API_KEY=sk-test-secret-runtime-validation" -Encoding UTF8
  $secrets = Invoke-SidecarRequest $sidecar "secrets" "security:secrets"
  Assert-OkResponse $secrets "secret scan"
  $secretJson = $secrets | ConvertTo-Json -Depth 12
  Assert-True ($secretJson -match "OPENAI_API_KEY") "Secret scan did not report the variable name."
  Assert-True ($secretJson -notmatch "sk-test-secret-runtime-validation") "Secret scan leaked the fake secret value."
  Add-Evidence "Secret redaction" "Variable names are reported and values stay hidden."

  $status = Invoke-SidecarRequest $sidecar "signal-status" "/signal-status"
  Assert-OkResponse $status "/signal-status"
  Assert-True (($status.output | Out-String) -match "NEXT ACTION") "Status output did not include a next action."
  Add-Evidence "Project status" "/signal-status returned a next action."

  $brainAdd = Invoke-SidecarRequest $sidecar "brain-add" "brain:add" @("note", "Installed runtime validation note")
  Assert-OkResponse $brainAdd "brain add"
  $brainSearch = Invoke-SidecarRequest $sidecar "brain-search" "brain:search" @("runtime validation")
  Assert-OkResponse $brainSearch "brain search"
  Assert-True (($brainSearch | ConvertTo-Json -Depth 12) -match "Installed runtime validation note") "Brain search did not return the saved note."
  Add-Evidence "Notes and Brain" "Note was saved and found through the bundled engine."

  $gates = Invoke-SidecarRequest $sidecar "gates" "state:gates"
  Assert-OkResponse $gates "gate status"
  Assert-True (@($gates.data).Count -eq 6) "Gate status did not return six gates."
  Add-Evidence "Gate status" "Six governance gates returned through the bundled engine."
} finally {
  if ($sidecar -and -not $sidecar.HasExited) {
    $sidecar.Kill()
  }
  if (-not $UseReleaseTree) {
    $uninstaller = Get-ChildItem -Path $InstallDir -Recurse -Filter "*uninst*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($uninstaller) {
      $uninstall = Start-Process -FilePath $uninstaller.FullName -ArgumentList @("/S") -Wait -PassThru
      if ($uninstall.ExitCode -eq 0) {
        Add-Evidence "NSIS uninstall" "Silent uninstall completed."
      }
    }
  }
  Write-EvidenceFile
  if (-not $KeepArtifacts -and (Test-Path $RunRoot)) {
    Remove-Item -LiteralPath $RunRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}

Write-Host ""
Write-Host "Installed-runtime validation passed."
