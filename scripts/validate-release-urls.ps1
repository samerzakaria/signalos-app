param(
  [switch]$RequireRemote,
  [string]$EvidencePath
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Evidence = New-Object System.Collections.Generic.List[object]
$Failures = New-Object System.Collections.Generic.List[string]

function Add-Evidence {
  param([string]$Check, [string]$Result, [string]$Status = "pass")
  $Evidence.Add([pscustomobject]@{
    check = $Check
    status = $Status
    result = $Result
  }) | Out-Null
  $label = if ($Status -eq "pass") { "PASS" } elseif ($Status -eq "skip") { "SKIP" } else { "FAIL" }
  Write-Host "[$label] $Check - $Result"
  if ($Status -eq "fail") {
    $Failures.Add("$Check - $Result") | Out-Null
  }
}

function Test-LocalJson {
  param([string]$Path)
  $full = Join-Path $Root $Path
  if (-not (Test-Path $full)) {
    Add-Evidence $Path "Missing local file." "fail"
    return
  }
  try {
    $json = Get-Content $full -Raw | ConvertFrom-Json
    if (-not $json.version) { throw "missing version" }
    Add-Evidence $Path "Local JSON is valid; version $($json.version)."
  } catch {
    Add-Evidence $Path "Invalid local JSON: $($_.Exception.Message)" "fail"
  }
}

function Test-RemoteUrl {
  param([string]$Url, [string]$Kind)
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 20
    if ($response.StatusCode -lt 200 -or $response.StatusCode -gt 299) {
      Add-Evidence $Kind "HTTP $($response.StatusCode) from $Url" "fail"
      return
    }
    Add-Evidence $Kind "HTTP $($response.StatusCode) from $Url"
  } catch {
    $status = "skip"
    if ($RequireRemote) { $status = "fail" }
    Add-Evidence $Kind "Remote URL not reachable: $Url ($($_.Exception.Message))" $status
  }
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
    "# SignalOS Release URL Evidence",
    "",
    "Date: $(Get-Date -Format s)",
    "",
    "## Results",
    ""
  )
  foreach ($item in $Evidence) {
    $lines += "- $($item.check): $($item.status) - $($item.result)"
  }
  Set-Content -Path $target -Value ($lines -join "`n") -Encoding UTF8
}

Write-Host "SignalOS release URL validation"

Test-LocalJson "distribution\update-manifest\beta.json"
Test-LocalJson "distribution\update-manifest\latest.json"

$config = Get-Content (Join-Path $Root "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
foreach ($endpoint in @($config.plugins.updater.endpoints)) {
  Test-RemoteUrl $endpoint "Updater endpoint"
}

Test-RemoteUrl "https://samerzakaria.github.io/signalos-app/" "Public docs home"
Test-RemoteUrl "https://samerzakaria.github.io/signalos-app/docs/USER_GUIDE.md" "Public user guide"
Test-RemoteUrl "https://samerzakaria.github.io/signalos-app/docs/RELEASE_OPERATOR_GUIDE.md" "Public release guide"

Write-EvidenceFile

if ($Failures.Count -gt 0) {
  Write-Host ""
  Write-Host "Release URL validation failed:"
  foreach ($failure in $Failures) {
    Write-Host " - $failure"
  }
  exit 1
}

Write-Host ""
Write-Host "Release URL validation completed."
