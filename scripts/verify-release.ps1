param(
  [switch]$BuildInstaller,
  [switch]$SmokeInstalledBuild,
  [switch]$InstallNsisSmoke,
  [switch]$InstalledRuntimeSmoke,
  [switch]$LiveProviderValidation,
  [switch]$RequireCloudProviderKeys,
  [switch]$ValidateRemoteReleaseUrls,
  [switch]$RequireRemoteReleaseUrls
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Failures = New-Object System.Collections.Generic.List[string]

function Invoke-Check {
  param(
    [string]$Name,
    [scriptblock]$Body
  )
  try {
    & $Body
    Write-Host "[PASS] $Name"
  } catch {
    Write-Host "[FAIL] $Name - $($_.Exception.Message)"
    $Failures.Add("$Name - $($_.Exception.Message)")
  }
}

function Invoke-Step {
  param(
    [string]$Name,
    [string]$WorkingDirectory,
    [string]$Executable,
    [string[]]$StepArguments = @()
  )
  Write-Host "[RUN ] $Name"
  $resolvedExecutable = $Executable
  if ($Executable -eq "npm") {
    $npmCmd = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if ($npmCmd) { $resolvedExecutable = $npmCmd.Source }
  }
  Push-Location $WorkingDirectory
  try {
    if ($StepArguments.Count -gt 0) {
      & $resolvedExecutable @StepArguments
    } else {
      & $resolvedExecutable
    }
    if ($LASTEXITCODE -ne 0) {
      throw "$Executable exited with code $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
}

function Get-ReleaseManifestNameForVersion {
  param([string]$Version)
  if ($Version -like "0.*" -or $Version -match "-[A-Za-z]") {
    return "beta.json"
  }
  return "latest.json"
}

function Get-HostTriple {
  $hostLine = rustc -Vv | Select-String "^host:"
  if (-not $hostLine) {
    throw "Could not read rustc host triple."
  }
  return ($hostLine.ToString() -split "\s+")[1]
}

Invoke-Check "Reference review exists" {
  $path = Join-Path $Root "docs\SIGNALOS_INSTALLED_APP_REFERENCE_REVIEW.md"
  if (-not (Test-Path $path)) { throw "Missing $path" }
}

Invoke-Check "Public docs are present and app-side missing is none" {
  $required = @(
    "docs\SIGNALOS_INSTALLED_APP_REFERENCE_REVIEW.md",
    "docs\USER_GUIDE.md",
    "docs\RELEASE_OPERATOR_GUIDE.md",
    "docs\PROVIDER_VALIDATION_GUIDE.md",
    "docs\CLEAN_MACHINE_VALIDATION.md"
  )
  foreach ($rel in $required) {
    $path = Join-Path $Root $rel
    if (-not (Test-Path $path)) { throw "Missing $rel" }
  }
  $reference = Get-Content (Join-Path $Root "docs\SIGNALOS_INSTALLED_APP_REFERENCE_REVIEW.md") -Raw
  if ($reference -notmatch "## What Is Still Missing In App Code\s+None\.") {
    throw "Reference review must state that app-side missing items are none."
  }
}

Invoke-Check "Bundled sidecar exists for host target" {
  $triple = Get-HostTriple
  $name = if ($env:OS -eq "Windows_NT") { "signalos-python-$triple.exe" } else { "signalos-python-$triple" }
  $path = Join-Path $Root "src-tauri\bin\$name"
  if (-not (Test-Path $path)) { throw "Missing sidecar $path. Run scripts\bundle-sidecar.ps1 first." }
  if ((Get-Item $path).Length -lt 1000000) { throw "Sidecar binary is unexpectedly small: $path" }
}

Invoke-Check "Tauri config points at bundled sidecar and update manifests" {
  $configPath = Join-Path $Root "src-tauri\tauri.conf.json"
  $config = Get-Content $configPath -Raw | ConvertFrom-Json
  if ($config.bundle.externalBin -notcontains "bin/signalos-python") {
    throw "bundle.externalBin must include bin/signalos-python"
  }
  $endpoints = @($config.plugins.updater.endpoints)
  if (-not ($endpoints | Where-Object { $_ -like "*update-manifest/beta.json" })) {
    throw "Missing beta update manifest endpoint."
  }
  if (-not ($endpoints | Where-Object { $_ -like "*update-manifest/latest.json" })) {
    throw "Missing latest update manifest endpoint."
  }
}

Invoke-Check "Desktop package versions are in sync" {
  $packagePath = Join-Path $Root "package.json"
  $lockPath = Join-Path $Root "package-lock.json"
  $tauriPath = Join-Path $Root "src-tauri\tauri.conf.json"
  $cargoPath = Join-Path $Root "src-tauri\Cargo.toml"

  $package = Get-Content $packagePath -Raw | ConvertFrom-Json
  $tauri = Get-Content $tauriPath -Raw | ConvertFrom-Json
  $cargoText = Get-Content $cargoPath -Raw
  $lockVersionsJson = & node "-e" "const fs=require('fs'); const lock=JSON.parse(fs.readFileSync(process.argv[1], 'utf8')); console.log(JSON.stringify({root: lock.version || '', package: (lock.packages && lock.packages[''] && lock.packages[''].version) || ''}));" $lockPath
  if ($LASTEXITCODE -ne 0) { throw "Could not parse package-lock.json with node." }
  $lockVersions = $lockVersionsJson | ConvertFrom-Json

  $expected = [string]$package.version
  if (-not $expected) { throw "package.json missing version." }
  if ([string]$lockVersions.root -ne $expected) { throw "package-lock.json root version '$($lockVersions.root)' must match package.json '$expected'." }
  if ([string]$lockVersions.package -ne $expected) { throw "package-lock.json package version '$($lockVersions.package)' must match package.json '$expected'." }
  if ([string]$tauri.version -ne $expected) { throw "src-tauri\tauri.conf.json version '$($tauri.version)' must match package.json '$expected'." }
  if ($cargoText -notmatch '(?m)^version\s*=\s*"([^"]+)"') { throw "src-tauri\Cargo.toml missing package version." }
  if ($Matches[1] -ne $expected) { throw "src-tauri\Cargo.toml version '$($Matches[1])' must match package.json '$expected'." }

  $manifestName = Get-ReleaseManifestNameForVersion $expected
  $manifestPath = Join-Path $Root "distribution\update-manifest\$manifestName"
  $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
  if ([string]$manifest.version -ne $expected) {
    throw "$manifestName update manifest version '$($manifest.version)' must match package.json '$expected'."
  }
  foreach ($platform in @("darwin-aarch64", "darwin-x86_64", "windows-x86_64", "linux-x86_64")) {
    $entry = $manifest.platforms.PSObject.Properties[$platform].Value
    if ($entry.url -notlike "*v$expected/*") {
      throw "$manifestName $platform URL must point at release tag v$expected."
    }
  }
}

Invoke-Check "Update manifests are structurally valid" {
  foreach ($name in @("beta.json", "latest.json")) {
    $path = Join-Path $Root "distribution\update-manifest\$name"
    if (-not (Test-Path $path)) { throw "Missing $path" }
    $manifest = Get-Content $path -Raw | ConvertFrom-Json
    if (-not $manifest.version) { throw "$name missing version" }
    foreach ($platform in @("darwin-aarch64", "darwin-x86_64", "windows-x86_64", "linux-x86_64")) {
      $entry = $manifest.platforms.PSObject.Properties[$platform].Value
      if (-not $entry.url) { throw "$name missing URL for $platform" }
    }
  }
}

Invoke-Check "Release workflows expose non-signing proof gates" {
  $releasePath = Join-Path $Root ".github\workflows\release.yml"
  $pagesPath = Join-Path $Root ".github\workflows\pages.yml"
  if (-not (Test-Path $releasePath)) { throw "Missing release workflow" }
  if (-not (Test-Path $pagesPath)) { throw "Missing Pages workflow" }
  $release = Get-Content $releasePath -Raw
  if ($release -notmatch "Verify Linux package artifacts") {
    throw "Release workflow must verify Linux package artifacts."
  }
  if ($release -notmatch "github\.event\.inputs\.version") {
    throw "Release workflow must honor manual dispatch version input."
  }
  if ($release -notmatch 'gh release create "\$TAG"' -or $release -notmatch 'gh release upload "\$TAG"') {
    throw "Release uploads must use the resolved release tag."
  }
  $pages = Get-Content $pagesPath -Raw
  if ($pages -notmatch "deploy-pages") {
    throw "Pages workflow must deploy public docs."
  }
}

Invoke-Step -Name "Frontend build" -WorkingDirectory $Root -Executable "npm" -StepArguments @("run", "build")
Invoke-Step -Name "Frontend tests" -WorkingDirectory $Root -Executable "npm" -StepArguments @("run", "test", "--", "--run")
Invoke-Step -Name "Python safety tests" -WorkingDirectory $Root -Executable "python" -StepArguments @("-m", "pytest", "python")
Invoke-Step -Name "Rust compile check" -WorkingDirectory (Join-Path $Root "src-tauri") -Executable "cargo" -StepArguments @("check")
Invoke-Step -Name "Rust tests" -WorkingDirectory (Join-Path $Root "src-tauri") -Executable "cargo" -StepArguments @("test")

if ($BuildInstaller) {
  Invoke-Step -Name "Build Tauri installer bundle" -WorkingDirectory $Root -Executable "cargo" -StepArguments @("tauri", "build")
  Invoke-Check "Installer artifact exists" {
    $bundle = Join-Path $Root "src-tauri\target\release\bundle"
    if (-not (Test-Path $bundle)) { throw "Missing bundle directory $bundle" }
    $artifacts = Get-ChildItem -Path $bundle -Recurse -File |
      Where-Object { $_.Extension -in @(".exe", ".msi", ".dmg", ".AppImage", ".deb") }
    if (-not $artifacts) { throw "No installer artifacts found under $bundle" }
  }
}

if ($SmokeInstalledBuild) {
  $smokeArgs = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts\smoke-installed-build.ps1"
  )
  if ($InstallNsisSmoke) { $smokeArgs += "-InstallNsis" }
  Invoke-Step -Name "Unsigned installed-build smoke" -WorkingDirectory $Root -Executable "powershell" -StepArguments $smokeArgs
}

if ($InstalledRuntimeSmoke) {
  Invoke-Step -Name "Installer-only runtime smoke" -WorkingDirectory $Root -Executable "powershell" -StepArguments @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts\validate-installed-runtime.ps1"
  )
}

if ($LiveProviderValidation) {
  $providerArgs = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts\validate-live-providers.ps1"
  )
  if ($RequireCloudProviderKeys) { $providerArgs += "-RequireCloudKeys" }
  Invoke-Step -Name "Live provider validation" -WorkingDirectory $Root -Executable "powershell" -StepArguments $providerArgs
}

if ($ValidateRemoteReleaseUrls) {
  $urlArgs = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts\validate-release-urls.ps1"
  )
  if ($RequireRemoteReleaseUrls) { $urlArgs += "-RequireRemote" }
  Invoke-Step -Name "Release URL validation" -WorkingDirectory $Root -Executable "powershell" -StepArguments $urlArgs
}

if ($Failures.Count -gt 0) {
  Write-Host ""
  Write-Host "Release readiness failed:"
  foreach ($failure in $Failures) {
    Write-Host " - $failure"
  }
  exit 1
}

Write-Host ""
Write-Host "Release readiness checks passed."
