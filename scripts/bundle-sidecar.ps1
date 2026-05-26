param(
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
$vendoredCorePath = Join-Path $root "python\signalos_lib"
if (-not (Test-Path $vendoredCorePath)) {
  throw "Vendored signalos_lib is missing at $vendoredCorePath"
}

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
& $venvPython -m pip install "anthropic>=0.39,<1.0" "pyyaml>=6.0,<7"

$entry = Join-Path $root "python\signalos_ipc_server.py"
$pythonPath = Join-Path $root "python"

# Exclude _bundle/ from the PyInstaller binary. The governance library
# (425 files, 2.9MB) is read-only text that the sidecar accesses lazily
# at agent-dispatch time. Packing it into the onefile binary adds cold-
# start extraction penalty (~90s on Windows CI). Instead, the _bundle/
# dir is shipped alongside the binary as a Tauri resource.
$bundleExclude = Join-Path $vendoredCorePath "_bundle"
$dataSpec = "$vendoredCorePath;signalos_lib"

& $venvPython -m PyInstaller `
  --onefile `
  --name $binaryName `
  --distpath $outDir `
  --workpath $workDir `
  --specpath $specDir `
  --clean `
  --noconfirm `
  --paths $pythonPath `
  --add-data $dataSpec `
  --exclude-module signalos_lib._bundle `
  --hidden-import signalos_lib.cli `
  --hidden-import anthropic `
  --hidden-import yaml `
  $entry

# Copy _bundle/ alongside the binary for runtime access
$bundleOut = Join-Path $outDir "_bundle"
if (Test-Path $bundleOut) { Remove-Item -LiteralPath $bundleOut -Recurse -Force }
Copy-Item -Path $bundleExclude -Destination $bundleOut -Recurse

$built = Join-Path $outDir $expectedFile
if (-not (Test-Path $built)) {
  throw "Sidecar build failed; expected $built"
}

Write-Host "Built sidecar: $built"
