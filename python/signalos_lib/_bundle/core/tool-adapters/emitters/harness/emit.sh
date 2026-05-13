#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.2 — Harness tool-adapter emitter (AMD-CORE-004).
#
# The 8th emitter. Mirrors the contract of the seven editor emitters
# (claude-code, cursor, codex, vs-code, windsurf, github-copilot,
# antigravity) but targets the headless harness rather than an IDE.
# Read the canonical JSON registries + rendered preamble and write a
# self-contained .signalos/harness/ area the CLI harness reads at run
# time.
#
# Inputs (same flags as the editor emitters; session-hook-dispatch.sh
# invokes every emitter with this shape):
#   --commands-json <path>
#   --skills-json   <path>
#   --hooks-json    <path>
#   --preamble      <path>
#   --output-dir    <path>
#
# Outputs written under $output_dir:
#   HARNESS.md                                  rendered preamble
#   .signalos/harness/commands.json             verbatim copy of the registry
#   .signalos/harness/skills.json               verbatim copy of the registry
#   .signalos/harness/hooks.json                verbatim copy of the registry
#   .signalos/harness/commands/<name>.md        one file per command with
#                                               `---\ndescription: <d>\n---`
#                                               frontmatter + the source
#                                               command-doc body.
#   .signalos/harness/MANIFEST.txt              summary line counts.
#
# Exit codes:
#   0 — wrote every file
#   1 — usage / dep error (bad args, jq missing, commands_json missing)

set -euo pipefail

parse_args() {
  local commands_json=""
  local skills_json=""
  local hooks_json=""
  local preamble=""
  local output_dir=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --commands-json) commands_json="$2"; shift 2 ;;
      --skills-json)   skills_json="$2";   shift 2 ;;
      --hooks-json)    hooks_json="$2";    shift 2 ;;
      --preamble)      preamble="$2";      shift 2 ;;
      --output-dir)    output_dir="$2";    shift 2 ;;
      *)
        echo "harness emitter: unknown argument: $1" >&2
        return 1
        ;;
    esac
  done

  if [[ -z "$commands_json" || -z "$preamble" || -z "$output_dir" ]]; then
    echo "Usage: emit.sh --commands-json <path> --skills-json <path> \\" >&2
    echo "               --hooks-json <path> --preamble <path> --output-dir <path>" >&2
    return 1
  fi

  echo "$commands_json"
  echo "$skills_json"
  echo "$hooks_json"
  echo "$preamble"
  echo "$output_dir"
}

main() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "harness emitter: jq is required but not found on PATH" >&2
    return 1
  fi

  local args
  if ! args=$(parse_args "$@"); then
    return 1
  fi

  local commands_json skills_json hooks_json preamble output_dir
  commands_json=$(echo "$args" | sed -n '1p')
  skills_json=$(echo "$args" | sed -n '2p')
  hooks_json=$(echo "$args" | sed -n '3p')
  preamble=$(echo "$args" | sed -n '4p')
  output_dir=$(echo "$args" | sed -n '5p')

  if [[ ! -f "$commands_json" ]]; then
    echo "harness emitter: commands JSON not found: $commands_json" >&2
    return 1
  fi
  if [[ ! -f "$preamble" ]]; then
    echo "harness emitter: preamble file not found: $preamble" >&2
    return 1
  fi

  local harness_dir="${output_dir}/.signalos/harness"
  local cmd_dir="${harness_dir}/commands"
  mkdir -p "$cmd_dir"

  # 1) Preamble — parallel of CLAUDE.md / .cursor/rules. The harness
  #    reads HARNESS.md as its system-prompt-equivalent for any call.
  cp "$preamble" "${output_dir}/HARNESS.md"

  # 2) Registry copies — so the harness never reads the canonical paths
  #    directly and tracks its own view of the world.
  cp "$commands_json" "${harness_dir}/commands.json"
  [[ -f "$skills_json" ]] && cp "$skills_json" "${harness_dir}/skills.json" || true
  [[ -f "$hooks_json"  ]] && cp "$hooks_json"  "${harness_dir}/hooks.json"  || true

  # 3) One markdown file per command, with frontmatter (same shape as
  #    claude-code/.claude/commands/*.md).
  local count=0
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    local desc source_path
    desc=$(jq -r --arg name "$name" '.[] | select(.name == $name) | .description // ""' "$commands_json")
    source_path=$(jq -r --arg name "$name" '.[] | select(.name == $name) | .source // ""' "$commands_json")

    local out_file="${cmd_dir}/${name}.md"
    {
      echo "---"
      echo "description: ${desc}"
      echo "---"
      echo ""
    } > "$out_file"

    if [[ -n "$source_path" && -f "$source_path" ]]; then
      cat "$source_path" >> "$out_file"
    fi

    count=$((count + 1))
  done < <(jq -r '.[].name' "$commands_json")

  # 4) Flat manifest — proof scenarios + humans both check this.
  local skills_count=0 hooks_count=0
  if [[ -f "$skills_json" ]]; then
    skills_count=$(jq 'length' "$skills_json" 2>/dev/null || echo 0)
  fi
  if [[ -f "$hooks_json" ]]; then
    # hooks.json is an object (event -> entries), so count the keys.
    hooks_count=$(jq 'keys | length' "$hooks_json" 2>/dev/null || echo 0)
  fi
  {
    echo "emitter: harness"
    echo "generated_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "commands: ${count}"
    echo "skills: ${skills_count}"
    echo "hooks: ${hooks_count}"
  } > "${harness_dir}/MANIFEST.txt"

  echo "harness emitter: wrote ${count} commands, ${skills_count} skills, ${hooks_count} hook events, and preamble to ${output_dir}"
}

main "$@"
