#!/usr/bin/env bash
# build-internal.sh — Internal Testing Build path (NOT signed) — POSIX port
# Mirrors scripts/build-internal.ps1 for macOS / Linux operators.
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

STRICT=0
SKIP_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --strict)     STRICT=1     ;;
    --skip-build) SKIP_BUILD=1 ;;
  esac
done

# Name comes from git config — testers see who built it.
# Email defaults to project noreply so personal email never ships in
# the attestation file. Override with SIGNALOS_BUILDER_EMAIL when you
# want a specific address (e.g., a per-tester distribution alias).
BUILDER_NAME="$(git config user.name)"
if [[ -z "$BUILDER_NAME" ]]; then
  echo "git config user.name must be set to attest a build." >&2
  exit 1
fi
BUILDER_EMAIL="${SIGNALOS_BUILDER_EMAIL:-noreply@signalos.app}"
echo "── Builder identity ─────────────────────────────────────────"
echo "  Name:    $BUILDER_NAME"
echo "  Email:   $BUILDER_EMAIL"

COMMIT="$(git rev-parse HEAD)"
SHORT_COMMIT="$(git rev-parse --short HEAD)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ -z "$(git status --porcelain)" ]]; then IS_CLEAN=true; else IS_CLEAN=false; fi
echo "── Source state ─────────────────────────────────────────────"
echo "  Commit:  $COMMIT"
echo "  Branch:  $BRANCH"
echo "  Clean:   $IS_CLEAN"
if [[ "$IS_CLEAN" == "false" && "$STRICT" == "1" ]]; then
  echo "Working tree is dirty. Commit or stash before attesting (or drop --strict)." >&2
  exit 1
fi

VERSION="$(awk -F'"' '/"version":/ {print $4; exit}' src-tauri/tauri.conf.json)"
echo "  Version: $VERSION"

# 2. Build
if [[ "$SKIP_BUILD" == "0" ]]; then
  echo "── Building installer (unsigned) ────────────────────────────"
  bash "$REPO_ROOT/scripts/ensure-sidecar.sh" --build
  case "$(uname -s)" in
    Darwin*) cargo tauri build --bundles dmg ;;
    Linux*)  cargo tauri build --bundles deb,appimage ;;
    *)       cargo tauri build ;;
  esac
else
  echo "── Skipping build (using existing artifacts) ────────────────"
fi

# 3. Discover artifacts + compute SHA-256
BUNDLE_DIR="src-tauri/target/release/bundle"
declare -a ARTIFACTS=()
sha_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
  elif command -v shasum >/dev/null 2>&1;    then shasum -a 256 "$1" | awk '{print $1}';
  else echo ""; fi
}
for pattern in "nsis/*.exe" "msi/*.msi" "deb/*.deb" "appimage/*.AppImage" "dmg/*.dmg"; do
  for f in $BUNDLE_DIR/$pattern; do
    [[ -e "$f" ]] || continue
    SIZE="$(wc -c <"$f" | tr -d ' ')"
    HASH="$(sha_of "$f")"
    REL="${f#$REPO_ROOT/}"
    REL="${REL#./}"
    ARTIFACTS+=("$REL|$SIZE|$HASH")
    echo "  + $REL ($SIZE bytes)"
    echo "    sha256: $HASH"
  done
done
if [[ "${#ARTIFACTS[@]}" == "0" ]]; then
  echo "No installer artifacts found under $BUNDLE_DIR." >&2
  exit 1
fi

# 4. Write attestation JSON
mkdir -p distribution/internal
ATTEST="distribution/internal/attestation-${SHORT_COMMIT}.json"
TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# Build the artifacts JSON array
ART_JSON=""
for entry in "${ARTIFACTS[@]}"; do
  IFS='|' read -r p s h <<< "$entry"
  if [[ -n "$ART_JSON" ]]; then ART_JSON="$ART_JSON,"; fi
  ART_JSON="$ART_JSON
    {\"path\":\"$p\",\"size\":$s,\"sha256\":\"$h\"}"
done

cat >"$ATTEST" <<EOF
{
  "schema": "signalos.attestation.v1",
  "release_type": "internal-testing-unsigned",
  "product": "SignalOS",
  "version": "$VERSION",
  "builder": {
    "name": "$BUILDER_NAME",
    "email": "$BUILDER_EMAIL"
  },
  "built_at": "$TS",
  "git": {
    "commit": "$COMMIT",
    "branch": "$BRANCH",
    "clean": $IS_CLEAN
  },
  "artifacts": [$ART_JSON
  ],
  "distribution_notes": [
    "Unsigned installer. SmartScreen will warn 'Unknown publisher' on Windows.",
    "macOS users must right-click -> Open the first launch (Gatekeeper bypass).",
    "Linux AppImage runs directly with chmod +x.",
    "DO NOT publish to the public landing page. Distribute only to named internal testers.",
    "When signing certs become available, run scripts/build-signed.ps1 and replace these artifacts."
  ]
}
EOF

echo
echo "── Attestation written ─────────────────────────────────────"
echo "  $ATTEST"
echo

# 5. Audit log
AUDIT=".signalos/AUDIT_TRAIL.jsonl"
mkdir -p .signalos
echo "{\"ts\":\"$TS\",\"action\":\"build:internal-attest\",\"actor\":\"$BUILDER_NAME <$BUILDER_EMAIL>\",\"detail\":\"version=$VERSION commit=$SHORT_COMMIT artifacts=${#ARTIFACTS[@]}\"}" >> "$AUDIT"
echo "  Audit entry appended to $AUDIT"

# 6. Hand-off summary
echo
echo "═══════════════════════════════════════════════════════════════"
echo "  Internal-testing build is ready."
echo "  Attested by: $BUILDER_NAME <$BUILDER_EMAIL>"
echo "  Not code-signed. Not notarized. Not for public release."
echo
echo "  Distribute to internal testers:"
for entry in "${ARTIFACTS[@]}"; do
  echo "    ${entry%%|*}"
done
echo
echo "  Tell testers:"
echo "  - On Windows, SmartScreen says 'Unknown publisher' -> 'More info' -> 'Run anyway'."
echo "  - On macOS, right-click the .dmg's app -> 'Open' on first launch."
echo "  - Report bugs to: $BUILDER_EMAIL"
echo
echo "  When ready for signed public beta: see docs/RELEASE_GATES_RUNBOOK.md"
echo "═══════════════════════════════════════════════════════════════"
