param(
  [string]$CorePath = "..\SignalOS-Core-v1.0.3",
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

function Resolve-HostTriple {
  $hostLine = rustc -Vv | Select-String "^host:"
  if (-not $hostLine) {
    throw "Could not determine Rust host target triple."
  }
  return ($hostLine.ToString() -split "\s+")[1]
}

$root = Split-Path -Parent $PSScriptRoot
$coreFullPath = Resolve-Path -Path (Join-Path $root $CorePath)
$targetTriple = Resolve-HostTriple
$isWindows = $env:OS -eq "Windows_NT"
$binaryName = "signalos-python-$targetTriple"
$expectedFile = if ($isWindows) { "$binaryName.exe" } else { $binaryName }
$outDir = Join-Path $root "src-tauri\bin"
$venvDir = Join-Path $root ".sidecar-venv"
$workDir = Join-Path $root "src-tauri\target\pyinstaller-build"
$specDir = Join-Path $root "src-tauri\target\pyinstaller-spec"

New-Item -ItemType Directory -Force -Path $outDir | Out-Null

if (-not (Test-Path $venvDir)) {
  & $Python -m venv $venvDir
}

$venvPython = if ($isWindows) {
  Join-Path $venvDir "Scripts\python.exe"
} else {
  Join-Path $venvDir "bin/python"
}

& $venvPython -m pip install --upgrade pip wheel pyinstaller
& $venvPython -m pip install $coreFullPath

$entry = Join-Path $root "python\signalos_ipc_server.py"

& $venvPython -m PyInstaller `
  --onefile `
  --name $binaryName `
  --distpath $outDir `
  --workpath $workDir `
  --specpath $specDir `
  --clean `
  --noconfirm `
  --collect-all signalos_lib `
  $entry

$built = Join-Path $outDir $expectedFile
if (-not (Test-Path $built)) {
  throw "Sidecar build failed; expected $built"
}

Write-Host "Built sidecar: $built"
