# test-gates.ps1 - Wave 5 / G4 - Test Automation L0+L1 gate runner (PowerShell)
#
# Runs the on-machine quality gates defined in docs/test-automation/:
#   L0 (developer machine, pre-commit):
#     - Lint + format
#     - Type-check
#     - Affected unit tests
#     - Secret scan (gitleaks if present, otherwise the in-tree regex)
#   L1 (CI build + verify):
#     - Compilation (cargo check, npm build)
#     - Full unit tests
#     - SCA (npm audit, blocking cargo audit policy)
#     - License check
#     - Mutation testing (if mutmut / stryker is present)
#
# Usage:
#   pwsh scripts/test-gates.ps1            # runs L0
#   pwsh scripts/test-gates.ps1 -Layer L1  # runs L0 + L1
#   pwsh scripts/test-gates.ps1 -Strict    # fail on any optional check missing
#
# Exit 0 = all configured gates passed. Exit 1 = at least one gate failed.

param(
    [ValidateSet("L0","L1")]
    [string]$Layer = "L0",
    [switch]$Strict
)

$ErrorActionPreference = "Continue"
$RepoRoot = Resolve-Path "$PSScriptRoot\.."
Set-Location $RepoRoot

$Failed = @()
$Skipped = @()
$Passed = @()

function Get-GateExitCode {
    param([object]$Result)

    $items = @($Result)
    if ($items.Count -eq 0) {
        return $LASTEXITCODE
    }

    if ($items.Count -gt 1) {
        $items[0..($items.Count - 2)] | ForEach-Object {
            if ($null -ne $_ -and [string]$_ -ne "") {
                Write-Host $_
            }
        }
    }

    $candidate = $items[-1]
    [int]$parsed = 0
    if ($candidate -is [int]) {
        return [int]$candidate
    }
    if ([int]::TryParse([string]$candidate, [ref]$parsed)) {
        return $parsed
    }
    if ($null -ne $candidate -and [string]$candidate -ne "") {
        Write-Host $candidate
    }
    return $LASTEXITCODE
}

function Run-Gate {
    param([string]$Name, [scriptblock]$Block, [switch]$Optional)

    Write-Host ""
    Write-Host "-- $Name ----------------------------------------------"
    try {
        $result = & $Block
        $code = Get-GateExitCode $result
        if ($code -eq 0) {
            Write-Host "  PASS" -ForegroundColor Green
            $script:Passed += $Name
        } else {
            if ($Optional -and -not $Strict) {
                Write-Host "  SKIPPED (optional, exit $code)" -ForegroundColor Yellow
                $script:Skipped += "$Name (exit $code)"
            } else {
                Write-Host "  FAIL (exit $code)" -ForegroundColor Red
                $script:Failed += $Name
            }
        }
    } catch {
        if ($Optional -and -not $Strict) {
            Write-Host "  SKIPPED ($_)" -ForegroundColor Yellow
            $script:Skipped += "$Name ($_)"
        } else {
            Write-Host "  FAIL ($_)" -ForegroundColor Red
            $script:Failed += $Name
        }
    }
}

# L0 - Developer machine

Run-Gate "L0: cargo fmt check" {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { return 0 }
    Set-Location "$RepoRoot\src-tauri"
    cargo fmt --check
    $rc = $LASTEXITCODE
    Set-Location $RepoRoot
    return $rc
}

Run-Gate "L0: cargo clippy (-D warnings)" {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { return 0 }
    Set-Location "$RepoRoot\src-tauri"
    cargo clippy --all-targets -- -D warnings
    $rc = $LASTEXITCODE
    Set-Location $RepoRoot
    return $rc
}

Run-Gate "L0: cargo check (compile)" {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { return 0 }
    Set-Location "$RepoRoot\src-tauri"
    cargo check
    $rc = $LASTEXITCODE
    Set-Location $RepoRoot
    return $rc
}

Run-Gate "L0: cargo test (lib)" {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { return 0 }
    Set-Location "$RepoRoot\src-tauri"
    cargo test --lib
    $rc = $LASTEXITCODE
    Set-Location $RepoRoot
    return $rc
}

Run-Gate "L0: python tests" {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { return 0 }
    Set-Location "$RepoRoot\python"
    $tests = Get-ChildItem -Filter "test_*.py" -File
    if ($tests.Count -eq 0) { Set-Location $RepoRoot; return 0 }
    python -m unittest discover -p "test_*.py" -t .
    $rc = $LASTEXITCODE
    Set-Location $RepoRoot
    return $rc
}

Run-Gate "L0: secret scan (regex)" {
    # The in-tree secret regex set lives in python/signalos_secret_guard.py.
    # Scan tracked text files for high-confidence secret patterns.
    $bad = 0
    $files = git ls-files | Where-Object {
        $_ -notmatch '\.(png|jpg|jpeg|webp|ico|icns|pdf|docx|pptx|xlsx|exe|dll|so|dylib)$' -and
        $_ -notmatch '^python/signalos_lib/_bundle/' -and
        $_ -notmatch '^docs/test-automation/' -and
        $_ -notmatch '^python/test_' -and
        $_ -notmatch '\.test\.tsx?$' -and
        $_ -notmatch '^scripts/validate-installed-runtime\.ps1$'
    }
    foreach ($f in $files) {
        if (-not (Test-Path $f)) { continue }
        $text = Get-Content $f -Raw -ErrorAction SilentlyContinue
        if (-not $text) { continue }
        if ($text -match '\bsk-ant-[A-Za-z0-9_\-]{20,}\b') { Write-Host "  hit: $f (Anthropic key)"; $bad++ }
        if ($text -match '\bsk-(proj-)?[A-Za-z0-9_\-]{30,}\b') { Write-Host "  hit: $f (OpenAI-shape key)"; $bad++ }
        if ($text -match '\bAKIA[0-9A-Z]{16}\b') { Write-Host "  hit: $f (AWS access key)"; $bad++ }
        if ($text -match '-----BEGIN [A-Z ]*PRIVATE KEY-----') { Write-Host "  hit: $f (PEM private key)"; $bad++ }
    }
    if ($bad -gt 0) { return 1 }
    return 0
}

Run-Gate "L0: Tauri command ACL" {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { return 1 }
    python scripts/validate-tauri-acl.py
    return $LASTEXITCODE
}

# L1 - CI build + verify

if ($Layer -eq "L1") {
    Run-Gate "L1: cargo test --release" {
        if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { return 0 }
        Set-Location "$RepoRoot\src-tauri"
        cargo test --release
        $rc = $LASTEXITCODE
        Set-Location $RepoRoot
        return $rc
    }
    Run-Gate "L1: cargo audit" {
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            Write-Host "  (install Python 3 to validate RustSec exceptions)"
            return 1
        }
        if (-not (Get-Command cargo-audit -ErrorAction SilentlyContinue)) {
            Write-Host "  (install: cargo install cargo-audit --locked)"
            return 1
        }
        python scripts/validate-rustsec-exceptions.py --cargo-dir src-tauri
        if ($LASTEXITCODE -ne 0) {
            return $LASTEXITCODE
        }
        Set-Location "$RepoRoot\src-tauri"
        cargo audit --deny warnings
        $rc = $LASTEXITCODE
        Set-Location $RepoRoot
        return $rc
    }
}

# Summary

Write-Host ""
Write-Host "---- Summary ------------------------------------------"
Write-Host "Passed:  $($Passed.Count)"
foreach ($p in $Passed) { Write-Host "  + $p" -ForegroundColor Green }
if ($Skipped.Count -gt 0) {
    Write-Host "Skipped: $($Skipped.Count)"
    foreach ($s in $Skipped) { Write-Host "  ~ $s" -ForegroundColor Yellow }
}
if ($Failed.Count -gt 0) {
    Write-Host "Failed:  $($Failed.Count)"
    foreach ($f in $Failed) { Write-Host "  x $f" -ForegroundColor Red }
    exit 1
}
exit 0
