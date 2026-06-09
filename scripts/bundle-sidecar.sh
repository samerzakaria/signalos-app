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
# LiteLLM (v4 Phase 2.1) is the provider-agnostic completion library behind
# the AgentProvider adapter. Pinned <2 to keep the tool-call response shape
# stable. litellm pulls openai as a transitive dep but we pin it explicitly.
"$VENV_PYTHON" -m pip install "anthropic>=0.39,<1.0" "openai>=1.30,<2" "google-generativeai>=0.5,<1" "pyyaml>=6.0,<7" "litellm>=1.40,<2"

IPC_ENTRY="$ROOT_DIR/python/signalos_ipc_server.py"
if [[ ! -f "$IPC_ENTRY" ]]; then
  echo "Missing IPC entry: $IPC_ENTRY"
  exit 1
fi

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
  --hidden-import signalos_lib.cli \
  --hidden-import anthropic \
  --hidden-import openai \
  --hidden-import google.generativeai \
  --hidden-import yaml \
  --hidden-import litellm \
  --collect-all litellm \
  --hidden-import tiktoken \
  --hidden-import tiktoken_ext \
  --hidden-import tiktoken_ext.openai_public \
  --collect-all tiktoken \
  --collect-all tiktoken_ext \
  --runtime-hook "$ROOT_DIR/scripts/pyi-rthook-tiktoken.py" \
  "$IPC_ENTRY"

if [[ ! -f "$SIDECAR_DIR/$SIDECAR_NAME" ]]; then
  echo "Sidecar build failed; expected $SIDECAR_DIR/$SIDECAR_NAME"
  exit 1
fi

echo "Built sidecar: $SIDECAR_DIR/$SIDECAR_NAME"
