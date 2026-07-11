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
# LiteLLM (v4 Phase 2.1) is the provider-agnostic completion library behind
# the AgentProvider adapter. Pinned <2 to keep the tool-call response shape
# stable.
& $venvPython -m pip install "anthropic>=0.39,<1.0" "openai>=1.30,<2" "google-generativeai>=0.5,<1" "pyyaml>=6.0,<7" "litellm>=1.40,<2"

$entry = Join-Path $root "python\signalos_ipc_server.py"
$pythonPath = Join-Path $root "python"

$dataSpec = "$vendoredCorePath;signalos_lib"
# Bundle package.json at the archive root so the frozen sidecar can report its
# version via _MEIPASS even when the Tauri host didn't inject SIGNALOS_APP_VERSION.
$versionDataSpec = "$(Join-Path $root 'package.json');."

# PyInstaller streams INFO/WARNING to stderr. Under $ErrorActionPreference='Stop'
# (set above), PowerShell 5.1 converts a native command's stderr into a
# terminating NativeCommandError whenever that stderr is captured -- CI, a tool
# host, or any 2>&1/*>&1 -- aborting the build on the very FIRST INFO line even
# though PyInstaller exits 0. Relax the preference around the native call and
# gate on the real exit code, so ordinary INFO output never masquerades as a
# build failure.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
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
  --add-data $versionDataSpec `
  --hidden-import signalos_lib.cli `
  --hidden-import anthropic `
  --hidden-import openai `
  --hidden-import google.generativeai `
  --hidden-import yaml `
  --hidden-import litellm `
  --collect-all litellm `
  --hidden-import tiktoken `
  --hidden-import tiktoken_ext `
  --hidden-import tiktoken_ext.openai_public `
  --collect-all tiktoken `
  --collect-all tiktoken_ext `
  --runtime-hook (Join-Path $PSScriptRoot "pyi-rthook-tiktoken.py") `
  $entry
$piExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($piExit -ne 0) {
  throw "PyInstaller failed with exit code $piExit"
}

$built = Join-Path $outDir $expectedFile
if (-not (Test-Path $built)) {
  throw "Sidecar build failed; expected $built"
}

Write-Host "Built sidecar: $built"
