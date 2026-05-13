#!/usr/bin/env bash
# Antigravity tool-adapter emitter
# Reads canonical JSON registries and rendered preamble,
# writes Antigravity-native config files (.antigravity/rules.md and .antigravity/commands/*.md)

set -euo pipefail

parse_args() {
  local commands_json=""
  local skills_json=""
  local hooks_json=""
  local preamble=""
  local output_dir=""

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

  if [[ ! -f "$commands_json" ]]; then
    echo "Error: commands JSON file not found: $commands_json" >&2
    return 1
  fi

  if [[ ! -f "$preamble" ]]; then
    echo "Error: preamble file not found: $preamble" >&2
    return 1
  fi

  # Create output directories
  mkdir -p "$output_dir/.antigravity/commands"

  # Write preamble to rules.md
  cp "$preamble" "$output_dir/.antigravity/rules.md"

  # Process each command from JSON
  local count=0
  while IFS= read -r name; do
    if [[ -z "$name" ]]; then
      continue
    fi

    local desc source_path
    desc=$(jq -r --arg name "$name" '.[] | select(.name == $name) | .description // ""' "$commands_json")
    source_path=$(jq -r --arg name "$name" '.[] | select(.name == $name) | .source // ""' "$commands_json")

    local output_file="$output_dir/.antigravity/commands/${name}.md"

    # Write command file with description and source content
    {
      echo "# $name"
      echo ""
      echo "**Description:** $desc"
      echo ""
    } > "$output_file"

    # Append source file content if available
    if [[ -n "$source_path" && -f "$source_path" ]]; then
      {
        echo "## Implementation"
        echo ""
        cat "$source_path"
      } >> "$output_file"
    fi

    count=$((count + 1))
  done < <(jq -r '.[].name' "$commands_json")

  echo "Antigravity emitter: wrote $count commands and preamble to $output_dir"
}

main "$@"
