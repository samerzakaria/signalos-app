#!/usr/bin/env bash
# Cursor tool-adapter emitter
# Reads canonical JSON registries and rendered preamble,
# writes Cursor-native config files (.cursor/rules/*.mdc and .cursorrules)

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

  # Create output directory for rules
  mkdir -p "$output_dir/.cursor/rules"

  # Write preamble to .cursorrules
  cp "$preamble" "$output_dir/.cursorrules"

  # Process each command from JSON
  local count=0
  while IFS= read -r name; do
    if [[ -z "$name" ]]; then
      continue
    fi

    local desc source_path
    desc=$(jq -r --arg name "$name" '.[] | select(.name == $name) | .description // ""' "$commands_json")
    source_path=$(jq -r --arg name "$name" '.[] | select(.name == $name) | .source // ""' "$commands_json")

    local output_file="$output_dir/.cursor/rules/${name}.mdc"

    # Write frontmatter
    {
      echo "---"
      echo "description: $desc"
      echo "globs:"
      echo "---"
      echo ""
    } > "$output_file"

    # Append source file content if available
    if [[ -n "$source_path" && -f "$source_path" ]]; then
      cat "$source_path" >> "$output_file"
    fi

    count=$((count + 1))
  done < <(jq -r '.[].name' "$commands_json")

  write_signalos_guidance_file "$output_dir" "$obligations_json" "$guidance_catalog_json" "$stack"

  echo "Cursor emitter: wrote $count rules and preamble to $output_dir"
}

main "$@"
