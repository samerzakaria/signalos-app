# SignalOS App

The SignalOS desktop application — a standalone AI governance runtime for non-technical founders.

No IDE required. Chat-first interface. Ships as a native app on macOS, Windows, and Linux.

## What it is

SignalOS App wraps the [SignalOS Core](https://github.com/signalos/signalos-core) governance engine in a native desktop UI. You download it, open it, and it mentors you through every wave — from Discovery to Debrief — without touching a terminal.

- **Chat interface** — talk to SignalOS in plain language
- **Gate signing** — one-click G0–G5 with full audit trail
- **Live dashboard** — wave health, phase debt, belief confidence
- **Brain browser** — search every belief and decision ever made
- **BYOK** — bring your own API key (Anthropic, OpenAI, Gemini, or Ollama)
- **Cost meter** — see exactly what each session costs

## Architecture

```
signalos-app/
├── src/                  # Frontend (HTML/CSS/JS — rendered in OS webview)
│   └── index.html        # Full app shell
├── src-tauri/            # Rust backend (Tauri 2)
│   ├── src/
│   │   ├── main.rs       # App entry point
│   │   ├── ipc.rs        # IPC command handlers
│   │   ├── sidecar.rs    # Python subprocess manager
│   │   └── keychain.rs   # OS keychain (macOS / Windows / Linux)
│   ├── Cargo.toml
│   └── tauri.conf.json   # Signing-ready config
├── distribution/
│   ├── landing/          # Download landing page
│   └── update-manifest/  # Auto-update JSON manifests
├── SIGNING.md            # Step-by-step signing checklist
└── README.md
```

**Tauri 2** (Rust) — native window, IPC, OS keychain, auto-updater  
**Python sidecar** — the existing SignalOS Core CLI spawned as a subprocess  
**OS webview** — WKWebView (macOS), WebView2 (Windows), WebKitGTK (Linux)

## Development

### Prerequisites

- [Rust](https://rustup.rs) stable toolchain
- [Node.js](https://nodejs.org) 18+
- Python 3.11+ with SignalOS Core installed
- Tauri CLI: `cargo install tauri-cli`

### Run in dev mode

```bash
# Install frontend deps (none currently — vanilla JS)
# Start the Tauri dev server
cargo tauri dev
```

### Build for release

```bash
cargo tauri build
```

Unsigned binaries are produced by default. See [SIGNING.md](SIGNING.md) to enable code signing.

## Signing

The app is **signing-ready** — all configuration placeholders are in `tauri.conf.json`. See [SIGNING.md](SIGNING.md) for the exact accounts, certificates, and CI secrets needed to enable signed builds for macOS and Windows.

## Distribution

Releases are hosted on GitHub Releases. The auto-updater checks `https://cdn.signalos.io/updates/{{target}}/{{arch}}/latest.json` on every launch.

The landing page lives in `distribution/landing/index.html`.

## License

Proprietary — © 2026 SignalOS
