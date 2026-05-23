param(
  [string]$Root = (Join-Path $PSScriptRoot ".."),
  [switch]$Json,
  [switch]$RequireInstallers
)

$ErrorActionPreference = "Stop"
$ResolvedRoot = Resolve-Path $Root
$ReleaseDir = Join-Path $ResolvedRoot "src-tauri\target\release"
$IsWindowsHost = $env:OS -eq "Windows_NT"
$ExeSuffix = if ($IsWindowsHost) { ".exe" } else { "" }
$ReleaseExe = Join-Path $ReleaseDir "signalos-desktop$ExeSuffix"
$SidecarExe = Join-Path $ReleaseDir "signalos-python$ExeSuffix"
$Checks = New-Object System.Collections.Generic.List[object]

function Add-Check {
  param(
    [string]$Name,
    [string]$Path,
    [bool]$Required = $true
  )
  $exists = Test-Path -LiteralPath $Path
  $bytes = 0
  if ($exists) {
    $bytes = (Get-Item -LiteralPath $Path).Length
  }
  $Checks.Add([pscustomobject]@{
    name = $Name
    path = $Path
    required = $Required
    exists = $exists
    bytes = $bytes
  }) | Out-Null
}

Add-Check "release executable" $ReleaseExe $true
Add-Check "bundled sidecar" $SidecarExe $true

$NsisInstaller = Get-ChildItem -Path (Join-Path $ReleaseDir "bundle\nsis") -Filter "*_x64-setup.exe" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$MsiInstaller = Get-ChildItem -Path (Join-Path $ReleaseDir "bundle\msi") -Filter "*_x64_en-US.msi" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Add-Check "NSIS installer" $(if ($NsisInstaller) { $NsisInstaller.FullName } else { Join-Path $ReleaseDir "bundle\nsis\<missing>_x64-setup.exe" }) $RequireInstallers.IsPresent
Add-Check "MSI installer" $(if ($MsiInstaller) { $MsiInstaller.FullName } else { Join-Path $ReleaseDir "bundle\msi\<missing>_x64_en-US.msi" }) $RequireInstallers.IsPresent

$MissingRequired = @($Checks | Where-Object { $_.required -and -not $_.exists })
$Status = if ($MissingRequired.Count -eq 0) { "READY_FOR_SMOKE" } else { "BLOCKED" }
$SmokeCommand = "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke-installed-build.ps1"
$Result = [pscustomobject]@{
  schema_version = "signalos.installed_app_preflight.v1"
  status = $Status
  root = "$ResolvedRoot"
  installed_app_passed = $false
  checks = $Checks.ToArray()
  manual_launch_command = "& `"$ReleaseExe`""
  smoke_command = $SmokeCommand
  note = "This preflight only checks artifact presence. It does not install, launch, verify sidecar startup, or claim installed-app success. Run npm run tauri build, then the smoke command for installed-app proof."
}

if ($Json) {
  $Result | ConvertTo-Json -Depth 6
} else {
  Write-Host "SignalOS installed-artifact preflight: $Status"
  foreach ($check in $Checks) {
    $label = if ($check.exists) { "FOUND" } else { "MISSING" }
    $required = if ($check.required) { "required" } else { "optional" }
    Write-Host "[$label] $($check.name) ($required): $($check.path)"
  }
  Write-Host ""
  Write-Host "Manual launch command: $($Result.manual_launch_command)"
  Write-Host "Installed-app smoke command: $SmokeCommand"
  Write-Host $Result.note
}

if ($Status -ne "READY_FOR_SMOKE") {
  exit 1
}
