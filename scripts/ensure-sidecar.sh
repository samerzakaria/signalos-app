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

MODE="${1:-stub}"

# ─── Freshness helpers (Claim 1b) ───────────────────────────────────────────
# "Exists" is NOT sufficient. A committed-but-stale binary (the shipped 0.0.9
# sidecar reports "Unknown command: agent:deliver") would otherwise be accepted
# forever. A fresh sidecar answers the `capabilities` handshake with a command
# list that includes agent:deliver; a stale one does not.

# The lint stub written below is a few bytes; a real PyInstaller onefile is tens
# of MB. Anything under 100 KB is treated as a stub, not a real binary, and the
# freshness probe is skipped for it (stubs are never executed).
sidecar_looks_like_stub() {
  local size
  size="$(wc -c < "$1" 2>/dev/null | tr -d '[:space:]')"
  [[ -z "$size" ]] && size=0
  [[ "$size" -lt 102400 ]]
}

# Probe the binary's capability handshake. Returns 0 only when agent:deliver is
# reported. The sidecar exits on stdin EOF, so one piped line is enough.
sidecar_reports_agent_deliver() {
  local out probe='{"id":"__ensure_probe__","command":"capabilities","args":[]}'
  if command -v timeout >/dev/null 2>&1; then
    out="$(printf '%s\n' "$probe" | timeout 60 "$1" 2>/dev/null)" || true
  else
    out="$(printf '%s\n' "$probe" | "$1" 2>/dev/null)" || true
  fi
  case "$out" in
    *'"agent:deliver"'*) return 0 ;;
    *)                    return 1 ;;
  esac
}

# Best-effort source version for the loud "rebuild required" message (no jq/node
# dependency).
source_version() {
  sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    "$REPO_ROOT/package.json" 2>/dev/null | head -1
}

rebuild_sidecar() {
  echo "[ensure-sidecar] building real sidecar via PyInstaller…"
  bash "$REPO_ROOT/scripts/bundle-sidecar.sh"
  if [[ ! -f "$SIDECAR_PATH" ]]; then
    echo "[ensure-sidecar] FAIL: build did not produce $SIDECAR_NAME"
    exit 1
  fi
  if ! sidecar_reports_agent_deliver "$SIDECAR_PATH"; then
    echo "[ensure-sidecar] FAIL: rebuilt sidecar still does not report agent:deliver."
    exit 1
  fi
  echo "[ensure-sidecar] ✓ rebuilt and verified fresh (agent:deliver present)."
  exit 0
}

print_stale_message() {
  echo "[ensure-sidecar] ✗ STALE sidecar: it does not report agent:deliver."
  echo "[ensure-sidecar]   on-disk binary : $SIDECAR_PATH"
  local sv; sv="$(source_version)"
  [[ -n "$sv" ]] && echo "[ensure-sidecar]   source version : $sv (package.json)"
  echo "[ensure-sidecar]   The bundled engine is too old — rebuild required:"
  echo "[ensure-sidecar]     bash scripts/ensure-sidecar.sh --build   (or scripts/bundle-sidecar.sh)"
}

if [[ -f "$SIDECAR_PATH" ]]; then
  if sidecar_looks_like_stub "$SIDECAR_PATH"; then
    if [[ "$MODE" == "--build" ]]; then
      echo "[ensure-sidecar] existing file is a lint stub; building the real sidecar…"
      rebuild_sidecar
    fi
    if [[ "$MODE" == "--check" ]]; then
      echo "[ensure-sidecar] ✗ existing file is a lint stub, not a real sidecar."
      exit 1
    fi
    echo "[ensure-sidecar] existing file looks like a lint stub (<100KB); skipping freshness probe."
    exit 0
  fi
  if sidecar_reports_agent_deliver "$SIDECAR_PATH"; then
    echo "[ensure-sidecar] ✓ real sidecar reports agent:deliver; fresh — nothing to do."
    exit 0
  fi
  # A real but stale binary: rebuild when asked, otherwise fail loudly so the
  # staleness cannot be silently accepted.
  print_stale_message
  if [[ "$MODE" == "--build" ]]; then
    rebuild_sidecar
  fi
  exit 1
fi

# ─── Missing binary ─────────────────────────────────────────────────────────
if [[ "$MODE" == "--build" ]]; then
  rebuild_sidecar
fi

if [[ "$MODE" == "--check" ]]; then
  echo "[ensure-sidecar] ✗ missing sidecar: $SIDECAR_PATH (run with --build)."
  exit 1
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
