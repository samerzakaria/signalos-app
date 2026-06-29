#!/usr/bin/env bash
# GitHub Copilot tool-adapter emitter
# Reads canonical JSON registries and rendered preamble,
# writes GitHub Copilot-native config files:
#   - .github/copilot-instructions.md (system prompt)
#   - .github/copilot-chat-agents.json (custom agents/skills)
#   - .copilot/ directory (optional workspace-level config)
#
# GitHub Copilot respects:
#   1. .github/copilot-instructions.md — always injected into Copilot Chat context
#   2. .github/copilot-chat-agents.json — custom slash commands for Copilot Chat
#   3. Workspace settings in VS Code for completion tuning

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
      --commands-json) commands_json="$2"; shift 2 ;;
      --skills-json)   skills_json="$2"; shift 2 ;;
      --hooks-json)    hooks_json="$2"; shift 2 ;;
      --preamble)      preamble="$2"; shift 2 ;;
      --output-dir)    output_dir="$2"; shift 2 ;;
      --obligations-json) obligations_json="$2"; shift 2 ;;
      --guidance-catalog-json) guidance_catalog_json="$2"; shift 2 ;;
      --stack) stack="$2"; shift 2 ;;
      *)               echo "Unknown argument: $1" >&2; return 1 ;;
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

  # ─── 1. Write copilot-instructions.md ────────────────────────────────────
  # This is the primary system prompt injection point for GitHub Copilot.
  # Copilot reads this file and includes it in every Chat context.
  {
    cat "$preamble"
    echo ""
    echo "---"
    echo ""
    echo "## SignalOS Commands"
    echo ""
    echo "The following commands are available in this SignalOS product:"
    echo ""
    jq -r '.[] | "- **\(.name)** — \(.description)"' "$commands_json" 2>/dev/null || true
    echo ""

    if [[ -n "$skills_json" && -f "$skills_json" ]]; then
      echo "## SignalOS Skills"
      echo ""
      jq -r '.[] | "- **\(.name)** — \(.description)"' "$skills_json" 2>/dev/null || true
      echo ""
    fi

    if [[ -n "$hooks_json" && -f "$hooks_json" ]]; then
      echo "## Active Hooks"
      echo ""
      jq -r '.[] | "- **\(.name)** (\(.trigger)) — validators: \(.validators | join(", "))"' "$hooks_json" 2>/dev/null || true
      echo ""
    fi
  } > "$output_dir/.github/copilot-instructions.md"

  # ─── 2. Write copilot-chat-agents.json ───────────────────────────────────
  # Maps SignalOS commands to Copilot Chat slash commands.
  # Format: array of { name, description, prompt } objects.
  {
    echo "["
    local first=true
    jq -c '.[]' "$commands_json" 2>/dev/null | while IFS= read -r cmd; do
      local name desc source
      name=$(echo "$cmd" | jq -r '.name')
      desc=$(echo "$cmd" | jq -r '.description')
      source=$(echo "$cmd" | jq -r '.source // ""')

      if [[ "$first" != "true" ]]; then
        echo ","
      fi
      first=false

      # Build the prompt from command source if available
      local prompt="Execute the SignalOS command: $name. $desc"
      if [[ -n "$source" ]]; then
        local source_path="${output_dir}/${source}"
        if [[ -f "$source_path" ]]; then
          # Include the command's markdown prompt content
          prompt=$(cat "$source_path" | head -100 | jq -Rs '.')
          # Re-wrap as raw string
          echo "  {"
          echo "    \"name\": \"$name\","
          echo "    \"description\": \"$desc\","
          echo "    \"prompt\": $prompt"
          echo -n "  }"
          continue
        fi
      fi

      echo "  {"
      echo "    \"name\": \"$name\","
      echo "    \"description\": \"$desc\","
      echo "    \"prompt\": \"$prompt\""
      echo -n "  }"
    done
    echo ""
    echo "]"
  } > "$output_dir/.github/copilot-chat-agents.json"

  write_signalos_guidance_file "$output_dir" "$obligations_json" "$guidance_catalog_json" "$stack"

  echo "GitHub Copilot emitter: wrote instructions and chat agents to $output_dir/.github/"
}

main "$@"
