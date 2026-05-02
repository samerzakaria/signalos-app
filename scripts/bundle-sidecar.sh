#!/usr/bin/env bash
# bundle-sidecar.sh
#
# Compiles the SignalOS Core Python CLI into a standalone binary
# using PyInstaller and copies it into the Tauri sidecar location.
#
# Run this before `cargo tauri build` to ensure the sidecar is bundled.
# The output binary is platform-specific and gitignored.
#
# Usage:
#   ./scripts/bundle-sidecar.sh [path-to-signalos-core]
#
# Example:
#   ./scripts/bundle-sidecar.sh ../SignalOS-Core-v1.0.3
#
# Requirements:
#   - Python 3.11+
#   - pip install pyinstaller
#   - SignalOS Core installed or available at the given path

set -euo pipefail

CORE_PATH="${1:-../SignalOS-Core-v1.0.3}"
SIDECAR_DIR="src-tauri/bin"
SIDECAR_NAME="signalos-python"

echo "▶ SignalOS sidecar bundler"
echo "  Core path : $CORE_PATH"
echo "  Output    : $SIDECAR_DIR/$SIDECAR_NAME"
echo ""

# Validate core path
if [[ ! -d "$CORE_PATH" ]]; then
  echo "✗ Core path not found: $CORE_PATH"
  echo "  Usage: ./scripts/bundle-sidecar.sh <path-to-signalos-core>"
  exit 1
fi

# Check PyInstaller
if ! command -v pyinstaller &>/dev/null; then
  echo "✗ PyInstaller not found. Install it:"
  echo "  pip install pyinstaller --break-system-packages"
  exit 1
fi

# Create output directory
mkdir -p "$SIDECAR_DIR"

# Create the IPC server entry point if it doesn't exist in Core
IPC_ENTRY="$CORE_PATH/signalos_ipc_server.py"
if [[ ! -f "$IPC_ENTRY" ]]; then
  echo "▶ Creating IPC server entry point..."
  cat > "$IPC_ENTRY" << 'PYTHON'
#!/usr/bin/env python3
"""
signalos_ipc_server.py — stdin/stdout JSON IPC bridge for the desktop app.

Reads SidecarRequest JSON objects from stdin (one per line),
dispatches to the appropriate signalos command, and writes
SidecarResponse JSON objects to stdout.
"""

import sys
import json
import os

def handle(req: dict) -> dict:
    req_id  = req.get("id", "unknown")
    command = req.get("command", "")
    args    = req.get("args", [])
    cwd     = req.get("cwd")

    if cwd:
        os.chdir(cwd)

    try:
        # Route to the appropriate signalos module
        if command.startswith("signal-") or command.startswith("/signal-"):
            from signalos.cli import dispatch
            output = dispatch(command.lstrip("/"), args)
            return {"id": req_id, "ok": True, "output": output}

        elif command == "state:wave":
            from signalos.state import get_wave_state
            return {"id": req_id, "ok": True, "data": get_wave_state()}

        elif command == "state:gates":
            from signalos.state import get_gate_states
            return {"id": req_id, "ok": True, "data": get_gate_states()}

        elif command == "gate:sign":
            gate_id, signer = int(args[0]), args[1]
            from signalos.governance import sign_gate
            result = sign_gate(gate_id, signer)
            return {"id": req_id, "ok": True, "data": result}

        elif command == "brain:search":
            from signalos.brain import search
            query = args[0] if args else ""
            return {"id": req_id, "ok": True, "data": search(query)}

        elif command == "brain:add":
            entry_type, text = args[0], args[1]
            from signalos.brain import add_entry
            result = add_entry(text, entry_type)
            return {"id": req_id, "ok": True, "data": result}

        elif command == "audit:list":
            limit = int(args[0]) if args else 50
            from signalos.audit import list_entries
            return {"id": req_id, "ok": True, "data": list_entries(limit)}

        elif command == "cost:summary":
            # Cost is tracked Rust-side; return empty stub
            return {"id": req_id, "ok": True, "data": {"note": "tracked in app"}}

        else:
            return {"id": req_id, "ok": False, "error": f"Unknown command: {command}"}

    except Exception as e:
        return {"id": req_id, "ok": False, "error": str(e)}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req  = json.loads(line)
            resp = handle(req)
        except json.JSONDecodeError as e:
            resp = {"id": "parse-error", "ok": False, "error": str(e)}

        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
PYTHON
  echo "  ✓ Created $IPC_ENTRY"
fi

# Run PyInstaller
echo "▶ Running PyInstaller..."
pyinstaller \
  --onefile \
  --name "$SIDECAR_NAME" \
  --distpath "$SIDECAR_DIR" \
  --workpath "/tmp/pyinstaller-build" \
  --specpath "/tmp/pyinstaller-spec" \
  --clean \
  --noconfirm \
  "$IPC_ENTRY"

echo ""
echo "✓ Sidecar built: $SIDECAR_DIR/$SIDECAR_NAME"
echo ""
echo "Next step:"
echo "  cargo tauri build"
