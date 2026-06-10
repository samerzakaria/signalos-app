#!/usr/bin/env bash
# Dev-container provisioning for SignalOS / Foundry.
#
# Installs the Linux system libraries Tauri 2 needs (WebKitGTK et al.), the
# tauri-cli, and the JS + Python dependencies, so a contributor gets all three
# toolchains (Rust, Python 3.11, Node 20) ready from a single container build
# instead of hand-assembling them. Node/Python/Rust themselves come from the
# devcontainer features; this script wires up everything on top.
set -euo pipefail

echo "── Installing Tauri system dependencies ─────────────────────"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  libwebkit2gtk-4.1-dev \
  build-essential \
  curl wget file \
  libxdo-dev \
  libssl-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev \
  patchelf

echo "── Installing tauri-cli ─────────────────────────────────────"
if ! command -v cargo-tauri >/dev/null 2>&1; then
  cargo install tauri-cli --locked
fi

echo "── Installing JS dependencies ───────────────────────────────"
npm ci || npm install

echo "── Installing Python sidecar dependencies ───────────────────"
python3 -m pip install --upgrade pip
# Mirror the runtime deps the sidecar bundler pins (see scripts/bundle-sidecar.sh).
python3 -m pip install \
  "anthropic>=0.39,<1.0" "openai>=1.30,<2" "google-generativeai>=0.5,<1" \
  "pyyaml>=6.0,<7" "litellm>=1.40,<2" pytest

echo "── Dev container ready ──────────────────────────────────────"
echo "  npm run dev          # Vite dev server"
echo "  npm test             # Vitest"
echo "  python -m pytest     # sidecar tests"
echo "  npm run tauri dev    # full app (needs a display)"
