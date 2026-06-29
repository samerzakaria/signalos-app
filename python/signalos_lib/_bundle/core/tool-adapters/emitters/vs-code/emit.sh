#!/usr/bin/env bash
# VS Code tool-adapter emitter
# Reads canonical JSON registries and rendered preamble,
# writes VS Code-native config files (.github/copilot-instructions.md and .vscode/settings.json)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../../_shared/guidance-emitter.sh"

parse_args() {
  local commands_json=""
  local skills_json=""
  local hooks_json=""
  local preamble=""
  local output_dir=""
  local obligations_json=""
  local guidance_catalog_json=""
  local stack="any"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --commands-json)
        commands_json="$2"
        shift 2
        ;;
      --skills-json)
        skills_json="$2"
        shift 2
        ;;
      --hooks-json)
        hooks_json="$2"
        shift 2
        ;;
      --preamble)
        preamble="$2"
        shift 2
        ;;
      --output-dir)
        output_dir="$2"
        shift 2
        ;;
      --obligations-json)
        obligations_json="$2"
        shift 2
        ;;
      --guidance-catalog-json)
        guidance_catalog_json="$2"
        shift 2
        ;;
      --stack)
        stack="$2"
        shift 2
        ;;
      *)
        echo "Unknown argument: $1" >&2
        return 1
        ;;
    esac
  done

  if [[ -z "$commands_json" || -z "$preamble" || -z "$output_dir" ]]; then
    echo "Usage: emit.sh --commands-json <path> --skills-json <path> --hooks-json <path> --preamble <path> --output-dir <path>" >&2
    return 1
  fi

  echo "$commands_json"
  echo "$skills_json"
  echo "$hooks_json"
  echo "$preamble"
  echo "$output_dir"
  echo "$obligations_json"
  echo "$guidance_catalog_json"
  echo "$stack"
}

main() {
  if ! command -v jq &> /dev/null; then
    echo "Error: jq is required but not found" >&2
    return 1
  fi

  local args
  if ! args=$(parse_args "$@"); then
    return 1
  fi

  local commands_json=$(echo "$args" | sed -n '1p')
  local skills_json=$(echo "$args" | sed -n '2p')
  local hooks_json=$(echo "$args" | sed -n '3p')
  local preamble=$(echo "$args" | sed -n '4p')
  local output_dir=$(echo "$args" | sed -n '5p')
  local obligations_json=$(echo "$args" | sed -n '6p')
  local guidance_catalog_json=$(echo "$args" | sed -n '7p')
  local stack=$(echo "$args" | sed -n '8p')

  if [[ ! -f "$commands_json" ]]; then
    echo "Error: commands JSON file not found: $commands_json" >&2
    return 1
  fi

  if [[ ! -f "$preamble" ]]; then
    echo "Error: preamble file not found: $preamble" >&2
    return 1
  fi

  # Create output directories
  mkdir -p "$output_dir/.github"
  mkdir -p "$output_dir/.vscode"

  # Write preamble to copilot-instructions.md
  cp "$preamble" "$output_dir/.github/copilot-instructions.md"

  # Write minimal settings.json that references copilot instructions
  {
    echo "{"
    echo "  \"github.copilot.codeCompletions.enabled\": true"
    echo "}"
  } > "$output_dir/.vscode/settings.json"

  write_signalos_guidance_file "$output_dir" "$obligations_json" "$guidance_catalog_json" "$stack"

  echo "VS Code emitter: wrote copilot instructions and settings to $output_dir"
}

main "$@"
