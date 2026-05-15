#!/usr/bin/env bash
# test-gates.sh — Wave 5 / G4 — Test Automation L0+L1 gate runner (POSIX)
#
# Mirrors scripts/test-gates.ps1. Runs the on-machine quality gates from
# docs/test-automation/.
#
# Usage:
#   bash scripts/test-gates.sh           # L0
#   bash scripts/test-gates.sh L1        # L0 + L1
#   STRICT=1 bash scripts/test-gates.sh  # fail on missing optional tools

set -u  # do not set -e: we collect failures into a tally

LAYER="${1:-L0}"
STRICT="${STRICT:-0}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PASSED=()
FAILED=()
SKIPPED=()

run_gate() {
    local name="$1"; shift
    local optional="${OPTIONAL:-0}"
    echo
    echo "── $name ──────────────────────────────────────────────"
    if "$@"; then
        echo "  PASS"
        PASSED+=("$name")
    else
        local rc=$?
        if [[ "$optional" == "1" && "$STRICT" != "1" ]]; then
            echo "  SKIPPED (optional, exit $rc)"
            SKIPPED+=("$name (exit $rc)")
        else
            echo "  FAIL (exit $rc)"
            FAILED+=("$name")
        fi
    fi
}

# ─── L0 ──────────────────────────────────────────────────────────────────────

cargo_fmt_check()   { command -v cargo >/dev/null || return 0; (cd src-tauri && cargo fmt --check); }
cargo_clippy()      { command -v cargo >/dev/null || return 0; (cd src-tauri && cargo clippy --all-targets -- -D warnings); }
cargo_check_gate()  { command -v cargo >/dev/null || return 0; (cd src-tauri && cargo check); }
cargo_test_lib()    { command -v cargo >/dev/null || return 0; (cd src-tauri && cargo test --lib); }
python_tests()      {
    command -v python3 >/dev/null || command -v python >/dev/null || return 0
    local py
    py="$(command -v python3 || command -v python)"
    (cd python && \
        if ls test_*.py >/dev/null 2>&1; then "$py" -m unittest discover -p 'test_*.py' -t .; fi)
}
secret_scan()       {
    local bad=0
    while IFS= read -r f; do
        case "$f" in
            *.png|*.jpg|*.jpeg|*.webp|*.ico|*.icns|*.pdf|*.docx|*.pptx|*.xlsx|*.exe|*.dll|*.so|*.dylib) continue ;;
            python/signalos_lib/_bundle/*) continue ;;
            docs/test-automation/*) continue ;;
            # Test fixtures deliberately contain fake-shaped secrets so the
            # redaction layer can be tested against them. Exclude here.
            python/test_*.py) continue ;;
            scripts/validate-installed-runtime.ps1) continue ;;
        esac
        [[ -f "$f" ]] || continue
        if grep -E 'sk-ant-[A-Za-z0-9_\-]{20,}' "$f" >/dev/null 2>&1; then echo "  hit: $f (Anthropic key)"; bad=$((bad+1)); fi
        if grep -E 'sk-(proj-)?[A-Za-z0-9_\-]{30,}' "$f" >/dev/null 2>&1; then echo "  hit: $f (OpenAI-shape key)"; bad=$((bad+1)); fi
        if grep -E 'AKIA[0-9A-Z]{16}' "$f" >/dev/null 2>&1; then echo "  hit: $f (AWS access key)"; bad=$((bad+1)); fi
        if grep -E -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' "$f" >/dev/null 2>&1; then echo "  hit: $f (PEM private key)"; bad=$((bad+1)); fi
    done < <(git ls-files)
    [[ "$bad" == "0" ]]
}

run_gate "L0: cargo fmt check"   cargo_fmt_check
run_gate "L0: cargo clippy"      cargo_clippy
run_gate "L0: cargo check"       cargo_check_gate
run_gate "L0: cargo test lib"    cargo_test_lib
run_gate "L0: python tests"      python_tests
run_gate "L0: secret scan"       secret_scan

# ─── L1 ──────────────────────────────────────────────────────────────────────

if [[ "$LAYER" == "L1" ]]; then
    cargo_test_release() { command -v cargo >/dev/null || return 0; (cd src-tauri && cargo test --release); }
    cargo_audit()        {
        command -v cargo-audit >/dev/null || { echo "  (install: cargo install cargo-audit)"; return 0; }
        (cd src-tauri && cargo audit)
    }
                  run_gate "L1: cargo test --release"  cargo_test_release
    OPTIONAL=1    run_gate "L1: cargo audit"           cargo_audit
fi

# ─── Summary ────────────────────────────────────────────────────────────────

echo
echo "──── Summary ────────────────────────────────────────────────"
echo "Passed:  ${#PASSED[@]}"
for p in "${PASSED[@]}"; do echo "  + $p"; done
if [[ "${#SKIPPED[@]}" -gt 0 ]]; then
    echo "Skipped: ${#SKIPPED[@]}"
    for s in "${SKIPPED[@]}"; do echo "  ~ $s"; done
fi
if [[ "${#FAILED[@]}" -gt 0 ]]; then
    echo "Failed:  ${#FAILED[@]}"
    for f in "${FAILED[@]}"; do echo "  x $f"; done
    exit 1
fi
exit 0
