#!/usr/bin/env bash
# update-manifest.sh - update a SignalOS update manifest locally.
#
# Usage:
#   ./scripts/update-manifest.sh <version> [stable|beta]
#
# Examples:
#   ./scripts/update-manifest.sh 1.0.1
#   ./scripts/update-manifest.sh 1.0.2-beta.1 beta
#
# The script writes distribution/update-manifest/latest.json for stable
# or distribution/update-manifest/beta.json for beta.
# Signatures are left empty. CI fills them after build artifacts are available.

set -euo pipefail

VERSION="${1:?Usage: $0 <version> [stable|beta]}"
CHANNEL="${2:-stable}"
DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
REPO="samerzakaria/signalos-app"

NOTES=$(awk "/^## \[$VERSION\]/{found=1; next} found && /^## /{exit} found{print}" CHANGELOG.md 2>/dev/null | head -5 | tr '\n' ' ' || true)
NOTES="${NOTES:-SignalOS v${VERSION}}"

if [[ "$CHANNEL" == "beta" ]]; then
  OUT="distribution/update-manifest/beta.json"
else
  OUT="distribution/update-manifest/latest.json"
fi

cat > "$OUT" << EOF
{
  "version": "$VERSION",
  "notes": "$NOTES",
  "pub_date": "$DATE",
  "platforms": {
    "darwin-aarch64": {
      "url": "https://github.com/$REPO/releases/download/v$VERSION/SignalOS_${VERSION}_aarch64.dmg",
      "signature": ""
    },
    "darwin-x86_64": {
      "url": "https://github.com/$REPO/releases/download/v$VERSION/SignalOS_${VERSION}_x64.dmg",
      "signature": ""
    },
    "windows-x86_64": {
      "url": "https://github.com/$REPO/releases/download/v$VERSION/SignalOS_${VERSION}_x64-setup.exe",
      "signature": ""
    },
    "linux-x86_64": {
      "url": "https://github.com/$REPO/releases/download/v$VERSION/signalos-app_${VERSION}_amd64.AppImage",
      "signature": ""
    }
  }
}
EOF

echo "[OK] Wrote $OUT (v$VERSION, channel: $CHANNEL)"
echo "Signatures are empty. CI will populate them from .sig release assets."
