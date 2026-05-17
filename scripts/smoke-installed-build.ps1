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
  # Enable WebView2 remote debugging so we can drive the UI after launch.
  # Origin allow-list is required for the WebSocket handshake from a non-browser client.
  $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=9223 --remote-allow-origins=*"
  try {
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

    Test-FrontendInteractivity -Name $Name

    [void]$process.CloseMainWindow()
    if (-not $process.WaitForExit(8000)) {
      $process.Kill()
    }
  } finally {
    Remove-Item Env:\WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS -ErrorAction SilentlyContinue
  }
  Stop-SignalOSProcesses -AllowClose
  Write-Host "[PASS] Launch smoke: $Name"
}

# Drives the running webview through the DevTools protocol to verify that
# inline onclick handlers actually fire — the v1.1.1 regression class that
# the bare launch test cannot catch (window appears, nothing clicks).
function Test-FrontendInteractivity {
  param([string]$Name)

  Write-Host "[RUN ] Frontend interactivity: $Name"

  # Poll the DevTools HTTP endpoint for the page URL.
  $pageDeadline = (Get-Date).AddSeconds(10)
  $page = $null
  while ((Get-Date) -lt $pageDeadline) {
    try {
      $resp = Invoke-WebRequest -Uri "http://localhost:9223/json" -UseBasicParsing -TimeoutSec 2
      $pages = ConvertFrom-Json $resp.Content
      $page = $pages | Where-Object { $_.type -eq "page" -and $_.url -like "http://tauri.localhost/*" } | Select-Object -First 1
      if ($page) { break }
    } catch { }
    Start-Sleep -Milliseconds 500
  }
  if (-not $page) {
    throw "$Name did not expose a DevTools page on port 9223 (WebView2 may not be debuggable)"
  }

  # System.Net.WebSockets.ClientWebSocket lives in the GAC on any
  # PowerShell 5.1+ runner — no explicit Add-Type needed.
  $ws = New-Object System.Net.WebSockets.ClientWebSocket
  $cts = New-Object System.Threading.CancellationTokenSource
  $cts.CancelAfter(15000)
  $wsUri = [Uri]$page.webSocketDebuggerUrl
  $ws.ConnectAsync($wsUri, $cts.Token).Wait()

  $script:msgId = 0
  function Invoke-CDP {
    param([string]$Method, [hashtable]$Params = @{})
    $script:msgId++
    $payload = @{ id = $script:msgId; method = $Method; params = $Params } | ConvertTo-Json -Compress -Depth 10
    $buf = [System.Text.Encoding]::UTF8.GetBytes($payload)
    $seg = [System.ArraySegment[byte]]::new($buf)
    $ws.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cts.Token).Wait()
    while ($true) {
      $rxBuf = New-Object byte[] 65536
      $rxSeg = [System.ArraySegment[byte]]::new($rxBuf)
      $sb = New-Object System.Text.StringBuilder
      do {
        $rx = $ws.ReceiveAsync($rxSeg, $cts.Token)
        $rx.Wait()
        [void]$sb.Append([System.Text.Encoding]::UTF8.GetString($rxBuf, 0, $rx.Result.Count))
      } while (-not $rx.Result.EndOfMessage)
      $obj = ConvertFrom-Json $sb.ToString()
      if ($obj.id -eq $script:msgId) { return $obj }
    }
  }
  function Invoke-Eval {
    param([string]$Expr)
    $r = Invoke-CDP -Method "Runtime.evaluate" -Params @{ expression = $Expr; returnByValue = $true }
    return $r.result.result.value
  }

  try {
    # The bootstrap removes inline onclick attrs — if it ran, the Begin
    # button has no onclick attribute but window.nextStep is still bound.
    $hasNext = Invoke-Eval "typeof window.nextStep === 'function'"
    if (-not $hasNext) {
      throw "${Name}: window.nextStep is not defined — app-v2.js failed to load"
    }

    # Verify the bootstrap actually neutralised inline attributes (regression
    # canary: if Tauri changes CSP behaviour, this check trips).
    $stillInline = Invoke-Eval "document.querySelector('button.btn.btn-primary[onclick]') !== null"
    if ($stillInline) {
      throw "${Name}: inline onclick attributes are still present — csp-bootstrap did not run"
    }

    # Simulate the user click on Step 1's Begin button and confirm Step 2 activates.
    $clicked = Invoke-Eval @"
(()=>{ const b=document.querySelector('.ob-step[data-step="1"] button.btn-primary'); if(!b) return 'no-button'; b.click(); return document.querySelector('.ob-step[data-step="2"]').classList.contains('active') ? 'advanced' : 'stuck'; })()
"@
    if ($clicked -ne "advanced") {
      throw "${Name}: Begin button click did not advance onboarding (got '$clicked')"
    }

    # Confirm IPC is reachable — connect-src must allow http://ipc.localhost.
    $ipcOk = Invoke-Eval "Object.keys(window.__TAURI__ || {}).length > 0"
    if (-not $ipcOk) {
      throw "${Name}: window.__TAURI__ bridge is missing"
    }

    # Tauri 2 renamed getCurrent() to getCurrentWindow(); _doExit() depends
    # on the new name. Also verifies the capability ACL grants the close
    # permission — without core:window:allow-close, calling close()
    # returns "Command plugin:window|close not allowed by ACL" at runtime.
    $hasClose = Invoke-Eval "typeof window.__TAURI__.window?.getCurrentWindow?.()?.close === 'function'"
    if (-not $hasClose) {
      throw "${Name}: Tauri 2 window.getCurrentWindow().close API is missing — Close button will leave a dead window"
    }
    # Probe the capability ACL. Tauri 2 returns
    # "Command plugin:window|<name> not allowed by ACL" if the permission
    # is missing. We call minimize+unminimize so the window state ends up
    # where it started; if either ACL is missing the smoke fails.
    $aclProbe = Invoke-Eval @"
(async () => {
  try {
    const w = window.__TAURI__.window.getCurrentWindow();
    await w.minimize();
    await w.unminimize();
    return 'ok';
  } catch (e) { return String(e); }
})()
"@
    if ($aclProbe -ne "ok") {
      throw "${Name}: window plugin ACL probe failed — close/minimize/maximize will silently fail (got: $aclProbe)"
    }

    Write-Host "[PASS] Frontend interactivity: $Name"
  } finally {
    try { $ws.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, "done", $cts.Token).Wait() } catch { }
    $ws.Dispose()
    $cts.Dispose()
  }
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
