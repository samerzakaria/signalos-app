param(
  [switch]$InstallNsis,
  [switch]$CloseRunning,
  [int]$LaunchTimeoutSeconds = 25
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$ReleaseDir = Join-Path $Root "src-tauri\target\release"
$ReleaseExe = Join-Path $ReleaseDir "signalos-desktop.exe"
$SidecarExe = Join-Path $ReleaseDir "signalos-python.exe"
$NsisInstaller = Get-ChildItem -Path (Join-Path $Root "src-tauri\target\release\bundle\nsis") -Filter "*_x64-setup.exe" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$MsiInstaller = Get-ChildItem -Path (Join-Path $Root "src-tauri\target\release\bundle\msi") -Filter "*_x64_en-US.msi" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$SmokeRoot = Join-Path $env:TEMP "signalos-smoke"

function Assert-File {
  param([string]$Path, [string]$Name)
  if (-not (Test-Path $Path)) {
    throw "Missing $Name at $Path"
  }
  if ((Get-Item $Path).Length -lt 1000000) {
    throw "$Name is unexpectedly small: $Path"
  }
}

function Stop-SignalOSProcesses {
  param([switch]$AllowClose)

  $processes = Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -in @("SignalOS", "signalos-desktop", "signalos-python") }
  if (-not $processes) { return }

  if (-not $AllowClose) {
    $names = ($processes | ForEach-Object { "$($_.ProcessName) ($($_.Id))" }) -join ", "
    throw "SignalOS is already running: $names. Close SignalOS and retry."
  }

  $processes | ForEach-Object {
    $name = $_.ProcessName
    $id = $_.Id
    try {
      if ($_.MainWindowHandle -ne 0) {
        [void]$_.CloseMainWindow()
        if (-not $_.WaitForExit(5000)) { $_.Kill() }
      } else {
        $_.Kill()
      }
    } catch {
      $stillRunning = Get-Process -Id $id -ErrorAction SilentlyContinue
      if ($stillRunning) {
        throw "Could not stop running process $name ($id). Close SignalOS and retry."
      }
    }
  }
}

function Test-AppLaunch {
  param([string]$ExePath, [string]$Name)

  Write-Host "[RUN ] Launch smoke: $Name"
  $process = Start-Process -FilePath $ExePath -PassThru -WindowStyle Hidden
  $deadline = (Get-Date).AddSeconds($LaunchTimeoutSeconds)
  $hasWindow = $false
  $hasSidecar = $false

  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $process.Refresh()
    if ($process.HasExited) {
      throw "$Name exited early with code $($process.ExitCode)"
    }
    if ($process.MainWindowHandle -ne 0) {
      $hasWindow = $true
    }
    $sidecar = Get-Process -Name "signalos-python" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($sidecar) {
      $hasSidecar = $true
    }
    if ($hasWindow -and $hasSidecar) { break }
  }

  if (-not $hasWindow) {
    throw "$Name did not create a main window within $LaunchTimeoutSeconds seconds"
  }

  if (-not $hasSidecar) {
    throw "$Name launched but did not start the bundled SignalOS engine"
  }

  [void]$process.CloseMainWindow()
  if (-not $process.WaitForExit(8000)) {
    $process.Kill()
  }
  Stop-SignalOSProcesses -AllowClose
  Write-Host "[PASS] Launch smoke: $Name"
}

function Test-MsiExtraction {
  Write-Host "[RUN ] MSI administrative extraction"
  $target = Join-Path $SmokeRoot "msi-extract"
  if (Test-Path $target) { Remove-Item -LiteralPath $target -Recurse -Force }
  New-Item -ItemType Directory -Path $target -Force | Out-Null

  $args = @("/a", "`"$($MsiInstaller.FullName)`"", "/qn", "TARGETDIR=`"$target`"")
  $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $args -Wait -PassThru
  if ($process.ExitCode -ne 0) {
    throw "MSI extraction failed with code $($process.ExitCode)"
  }

  $extractedExe = Get-ChildItem -Path $target -Recurse -Filter "*.exe" |
    Where-Object { $_.Name -match "SignalOS|signalos|desktop" } |
    Select-Object -First 1
  if (-not $extractedExe) {
    throw "MSI extraction did not produce an app executable"
  }
  Write-Host "[PASS] MSI administrative extraction"
}

function Test-NsisInstall {
  Write-Host "[RUN ] NSIS silent install smoke"
  $target = Join-Path $SmokeRoot "nsis-install"
  if (Test-Path $target) { Remove-Item -LiteralPath $target -Recurse -Force }
  New-Item -ItemType Directory -Path $target -Force | Out-Null

  $args = @("/S", "/D=$target")
  $process = Start-Process -FilePath $NsisInstaller.FullName -ArgumentList $args -Wait -PassThru
  if ($process.ExitCode -ne 0) {
    throw "NSIS silent install failed with code $($process.ExitCode). Close SignalOS if it is running, then retry."
  }

  $installedExe = Get-ChildItem -Path $target -Recurse -Filter "*.exe" |
    Where-Object { $_.Name -match "SignalOS|signalos|desktop" -and $_.Name -notmatch "uninst|uninstall" } |
    Sort-Object Length -Descending |
    Select-Object -First 1
  if (-not $installedExe) {
    throw "NSIS install did not produce an app executable in $target"
  }

  Test-AppLaunch $installedExe.FullName "NSIS installed app"

  $uninstaller = Get-ChildItem -Path $target -Recurse -Filter "*uninst*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($uninstaller) {
    $uninstall = Start-Process -FilePath $uninstaller.FullName -ArgumentList @("/S") -Wait -PassThru
    if ($uninstall.ExitCode -ne 0) {
      throw "NSIS silent uninstall failed with code $($uninstall.ExitCode)"
    }
  }

  if (Test-Path $target) {
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
  }
  Write-Host "[PASS] NSIS silent install smoke"
}

Write-Host "SignalOS unsigned installed-build smoke"
Assert-File $ReleaseExe "release app executable"
Assert-File $SidecarExe "release sidecar executable"
if (-not $NsisInstaller) { throw "Missing NSIS installer under src-tauri\target\release\bundle\nsis" }
if (-not $MsiInstaller) { throw "Missing MSI installer under src-tauri\target\release\bundle\msi" }
Assert-File $NsisInstaller.FullName "NSIS installer"
Assert-File $MsiInstaller.FullName "MSI installer"

Stop-SignalOSProcesses -AllowClose:$CloseRunning
if (Test-Path $SmokeRoot) { Remove-Item -LiteralPath $SmokeRoot -Recurse -Force }
New-Item -ItemType Directory -Path $SmokeRoot -Force | Out-Null

Test-AppLaunch $ReleaseExe "release executable"
Test-MsiExtraction
if ($InstallNsis) {
  Test-NsisInstall
}

Write-Host ""
Write-Host "Unsigned installed-build smoke passed."
