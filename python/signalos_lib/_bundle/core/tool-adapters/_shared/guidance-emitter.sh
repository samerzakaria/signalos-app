#!/usr/bin/env bash
# Shared helper for SignalOS tool-adapter emitters.

write_signalos_guidance_file() {
  local output_dir="$1"
  local obligations_json="$2"
  local guidance_catalog_json="$3"
  local stack="${4:-any}"

  if [[ -z "$obligations_json" || ! -f "$obligations_json" ]]; then
    return 0
  fi
  if [[ -z "$guidance_catalog_json" || ! -f "$guidance_catalog_json" ]]; then
    return 0
  fi
  if ! command -v jq >/dev/null 2>&1; then
    return 0
  fi

  local guidance_dir="$output_dir/.signalos"
  mkdir -p "$guidance_dir"
  {
    echo "# SignalOS Guidance"
    echo ""
    echo "Stack: $stack"
    echo ""
    echo "Catalog: \`$guidance_catalog_json\`"
    echo "Obligations: \`$obligations_json\`"
    echo ""
    echo "## Active Guidance"
    echo ""
    jq -r '.[] | select(.active == true) | "- **\(.id)** (\(.stack // "any")) - \(.path)"' "$guidance_catalog_json" 2>/dev/null || true
    echo ""
    echo "## Obligation Rules"
    echo ""
    jq -r '.[] | "- **\(.rule_id)** [\(.mode // "autoload")] - \(.title)"' "$obligations_json" 2>/dev/null || true
  } > "$guidance_dir/GUIDANCE.md"
}
