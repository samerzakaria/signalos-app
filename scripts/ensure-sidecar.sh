#!/usr/bin/env bash
# ensure-sidecar.sh — make sure src-tauri/bin/signalos-python-<triple>(.exe)?
# exists for the current target. Called from CI before any `cargo check`
# / `cargo build` — Tauri's build script refuses to start when the
# externalBin file for the target triple is missing, even if you're only
# linting.
#
# Modes:
#   - production: when the real PyInstaller exe should be bundled, run
#     scripts/bundle-sidecar.sh (POSIX equivalent). Long-running.
#   - lint-only: in CI we just need the file to exist so cargo proceeds.
#     We create a tiny shell stub that does nothing — it's never run
#     because lint targets don't execute the binary.
#
# Usage:
#   bash scripts/ensure-sidecar.sh           # lint-only stub if missing
#   bash scripts/ensure-sidecar.sh --build   # actually build via PyInstaller
#
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="$REPO_ROOT/src-tauri/bin"
mkdir -p "$BIN_DIR"

# ─── Helper: resolve target-triple-suffixed binary name ─────────────────────
# Mirrors what Tauri's build script does. The base name is `signalos-python`
# (from `tauri.conf.json` externalBin). The target triple is appended, and
# on Windows `.exe` is appended. Exported so other scripts can source it.
sidecar_name_for_target() {
  local triple="$1"
  case "$triple" in
    *-windows-msvc|*-windows-gnu) echo "signalos-python-${triple}.exe" ;;
    *)                            echo "signalos-python-${triple}"      ;;
  esac
}

# Compute the current Rust target triple.
detect_target_triple() {
  if command -v rustc >/dev/null 2>&1; then
    rustc -vV | awk '/^host:/ {print $2}'
  else
    # Coarse fallback by uname.
    local os arch
    arch="$(uname -m)"
    case "$arch" in x86_64) arch=x86_64 ;; aarch64|arm64) arch=aarch64 ;; esac
    case "$(uname -s)" in
      Linux*)  os="unknown-linux-gnu" ;;
      Darwin*) os="apple-darwin" ;;
      MINGW*|MSYS*|CYGWIN*) os="pc-windows-msvc" ;;
      *) os="unknown" ;;
    esac
    echo "${arch}-${os}"
  fi
}

TARGET_TRIPLE="$(detect_target_triple)"
SIDECAR_NAME="$(sidecar_name_for_target "$TARGET_TRIPLE")"
SIDECAR_PATH="$BIN_DIR/$SIDECAR_NAME"

echo "[ensure-sidecar] target triple: $TARGET_TRIPLE"
echo "[ensure-sidecar] expected:      $SIDECAR_PATH"

if [[ -f "$SIDECAR_PATH" ]]; then
  echo "[ensure-sidecar] ✓ exists; nothing to do"
  exit 0
fi

MODE="${1:-stub}"

if [[ "$MODE" == "--build" ]]; then
  echo "[ensure-sidecar] building real sidecar via PyInstaller…"
  bash "$REPO_ROOT/scripts/bundle-sidecar.sh"
  if [[ ! -f "$SIDECAR_PATH" ]]; then
    echo "[ensure-sidecar] FAIL: build did not produce $SIDECAR_NAME"
    exit 1
  fi
  exit 0
fi

# Lint/check mode: write a minimal stub. Tauri only checks the file's
# *existence* during the build-script. The stub is never executed.
echo "[ensure-sidecar] stubbing $SIDECAR_NAME (lint-only)…"
case "$TARGET_TRIPLE" in
  *-windows-msvc|*-windows-gnu)
    # Even a stub .exe must look like a binary or Windows tooling balks.
    # `printf` writes 2 bytes that start with MZ (the DOS signature) so
    # `file --mime` won't flag it as plaintext.
    printf 'MZ\n' > "$SIDECAR_PATH"
    ;;
  *)
    # POSIX: a shell script that exits 1 satisfies Tauri's file-presence check.
    cat >"$SIDECAR_PATH" <<'STUB'
#!/usr/bin/env bash
echo "stub sidecar — replace with PyInstaller build (scripts/bundle-sidecar.sh)" >&2
exit 1
STUB
    chmod +x "$SIDECAR_PATH"
    ;;
esac
echo "[ensure-sidecar] ✓ wrote stub at $SIDECAR_PATH"
echo "[ensure-sidecar] NOTE: this stub is for CI lint only — bundle for real before release."
