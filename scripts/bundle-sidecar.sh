#!/usr/bin/env bash
# Build the Python SignalOS sidecar binary for the current Rust host target.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENDORED_CORE_PATH="$ROOT_DIR/python/signalos_lib"
SIDECAR_DIR="$ROOT_DIR/src-tauri/bin"
TARGET_TRIPLE="$(rustc -Vv | awk '/^host:/ {print $2}')"
SIDECAR_NAME="signalos-python-${TARGET_TRIPLE}"
VENV_DIR=".sidecar-venv"
WORK_DIR="$ROOT_DIR/src-tauri/target/pyinstaller-build"
SPEC_DIR="$ROOT_DIR/src-tauri/target/pyinstaller-spec"

echo "SignalOS sidecar bundler"
echo "  Core path : $VENDORED_CORE_PATH"
echo "  Output    : $SIDECAR_DIR/$SIDECAR_NAME"

if [[ ! -d "$VENDORED_CORE_PATH" ]]; then
  echo "Vendored signalos_lib is missing: $VENDORED_CORE_PATH"
  exit 1
fi

mkdir -p "$SIDECAR_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip wheel pyinstaller
"$VENV_PYTHON" -m pip install "anthropic>=0.39,<1.0" "pyyaml>=6.0,<7"

IPC_ENTRY="$ROOT_DIR/python/signalos_ipc_server.py"
if [[ ! -f "$IPC_ENTRY" ]]; then
  echo "Missing IPC entry: $IPC_ENTRY"
  exit 1
fi

# Exclude _bundle/ from the binary — it's shipped alongside as a resource.
# Packing 425 files (2.9MB) into the onefile binary adds cold-start penalty.
DATA_SPEC="$VENDORED_CORE_PATH:signalos_lib"

"$VENV_PYTHON" -m PyInstaller \
  --onefile \
  --name "$SIDECAR_NAME" \
  --distpath "$SIDECAR_DIR" \
  --workpath "$WORK_DIR" \
  --specpath "$SPEC_DIR" \
  --clean \
  --noconfirm \
  --paths "$ROOT_DIR/python" \
  --add-data "$DATA_SPEC" \
  --exclude-module signalos_lib._bundle \
  --hidden-import signalos_lib.cli \
  --hidden-import anthropic \
  --hidden-import yaml \
  "$IPC_ENTRY"

# Copy _bundle/ alongside the binary for runtime access
BUNDLE_OUT="$SIDECAR_DIR/_bundle"
rm -rf "$BUNDLE_OUT"
cp -r "$VENDORED_CORE_PATH/_bundle" "$BUNDLE_OUT"

if [[ ! -f "$SIDECAR_DIR/$SIDECAR_NAME" ]]; then
  echo "Sidecar build failed; expected $SIDECAR_DIR/$SIDECAR_NAME"
  exit 1
fi

echo "Built sidecar: $SIDECAR_DIR/$SIDECAR_NAME"
