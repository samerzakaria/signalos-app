#!/usr/bin/env bash
# artifact-compiler.sh — YAML/Frontmatter → Rendered Markdown Compiler
#
# Reads SignalOS artifacts with YAML frontmatter and structured templates,
# compiles them into rendered markdown with resolved references, computed
# fields, and cross-linked navigation.
#
# Usage:
#   artifact-compiler.sh compile --input <path> [--output <path>]
#   artifact-compiler.sh compile-all [--repo-root <path>] [--output-dir <path>]
#   artifact-compiler.sh validate --input <path>
#   artifact-compiler.sh index [--repo-root <path>]  (generate MANIFEST.md)
#
# Supported frontmatter fields:
#   wave, scale_track, trust_tier_ceiling, delivery_mode, author, date, status
#
# Exit: 0 = success, 1 = error

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
COMMAND=""
INPUT_PATH=""
OUTPUT_PATH=""
OUTPUT_DIR=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
  cat <<EOF
Usage: artifact-compiler.sh <command> [options]

Commands:
  compile      --input <path>   Compile a single artifact
  compile-all                   Compile all artifacts in repo
  validate     --input <path>   Validate artifact frontmatter
  index                         Generate MANIFEST.md

Options:
  --output <path>       Output file (default: stdout)
  --output-dir <path>   Output directory for compile-all
  --repo-root <path>    Repository root
  --help                Show this help

EOF
  exit "${1:-0}"
}

parse_args() {
  COMMAND="${1:-}"
  shift || true

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --input)      INPUT_PATH="$2"; shift 2 ;;
      --output)     OUTPUT_PATH="$2"; shift 2 ;;
      --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
      --repo-root)  REPO_ROOT="$2"; shift 2 ;;
      --help)       usage 0 ;;
      *)            echo "Error: Unknown argument: $1" >&2; usage 1 ;;
    esac
  done
}

# ─── FRONTMATTER PARSER ────────────────────────────────────────────────────

# Extract YAML frontmatter (between --- delimiters) from a markdown file
extract_frontmatter() {
  local filepath="$1"
  sed -n '/^---$/,/^---$/p' "$filepath" 2>/dev/null | sed '1d;$d'
}

# Extract body (everything after frontmatter)
extract_body() {
  local filepath="$1"
  # If file starts with ---, skip frontmatter
  if head -1 "$filepath" | grep -q "^---$"; then
    sed '1,/^---$/d' "$filepath" | sed '1,/^---$/d'
  else
    cat "$filepath"
  fi
}

# Get a frontmatter field value
get_field() {
  local frontmatter="$1" field="$2"
  echo "$frontmatter" | grep -E "^${field}:" | head -1 | sed "s/^${field}:\s*//" | xargs
}

# ─── TEMPLATE RESOLUTION ──────────────────────────────────────────────────

# Resolve {{variable}} placeholders in content
resolve_templates() {
  local content="$1" frontmatter="$2"
  local result="$content"

  # Replace {{field_name}} with frontmatter values
  while [[ "$result" =~ \{\{([a-zA-Z_]+)\}\} ]]; do
    local field="${BASH_REMATCH[1]}"
    local value
    value=$(get_field "$frontmatter" "$field")
    if [[ -z "$value" ]]; then
      value="{{$field}}"  # Leave unresolved
    fi
    result="${result//\{\{$field\}\}/$value}"
  done

  echo "$result"
}

# ─── CROSS-REFERENCE LINKER ───────────────────────────────────────────────

# Resolve [[ARTIFACT_NAME]] references to relative links
resolve_cross_refs() {
  local content="$1" source_dir="$2"

  # Find all [[...]] references
  echo "$content" | while IFS= read -r line; do
    if [[ "$line" =~ \[\[([^\]]+)\]\] ]]; then
      local ref="${BASH_REMATCH[1]}"
      # Search for the referenced file
      local found
      found=$(find "$REPO_ROOT" -name "$ref" -o -name "${ref}.md" 2>/dev/null | head -1)
      if [[ -n "$found" ]]; then
        local rel_path
        rel_path=$(realpath --relative-to="$source_dir" "$found" 2>/dev/null || echo "$found")
        line="${line//\[\[$ref\]\]/[$ref]($rel_path)}"
      fi
    fi
    echo "$line"
  done
}

# ─── COMPUTED FIELDS ──────────────────────────────────────────────────────

# Add computed metadata to the header
compute_header() {
  local filepath="$1" frontmatter="$2"
  local rel_path="${filepath#$REPO_ROOT/}"
  local hash
  hash=$(sha256sum "$filepath" 2>/dev/null | awk '{print $1}' | cut -c1-12)
  local last_modified
  last_modified=$(git log -1 --format="%ai" "$filepath" 2>/dev/null || stat -c %y "$filepath" 2>/dev/null || echo "unknown")
  local word_count
  word_count=$(wc -w < "$filepath" 2>/dev/null || echo 0)

  echo "---"
  echo "# Compiled artifact metadata"
  echo "source: $rel_path"
  echo "hash: $hash"
  echo "last_modified: $last_modified"
  echo "word_count: $word_count"
  if [[ -n "$frontmatter" ]]; then
    echo "$frontmatter"
  fi
  echo "---"
}

# ─── COMPILE: single artifact ──────────────────────────────────────────────

cmd_compile() {
  if [[ -z "$INPUT_PATH" ]]; then
    echo "Error: --input required for compile" >&2
    exit 1
  fi

  if [[ ! -f "$INPUT_PATH" ]]; then
    echo "Error: File not found: $INPUT_PATH" >&2
    exit 1
  fi

  local frontmatter body compiled source_dir
  frontmatter=$(extract_frontmatter "$INPUT_PATH")
  body=$(extract_body "$INPUT_PATH")
  source_dir=$(dirname "$INPUT_PATH")

  # Step 1: Compute header
  local header
  header=$(compute_header "$INPUT_PATH" "$frontmatter")

  # Step 2: Resolve templates
  body=$(resolve_templates "$body" "$frontmatter")

  # Step 3: Resolve cross-references
  body=$(resolve_cross_refs "$body" "$source_dir")

  # Step 4: Assemble
  compiled="${header}

${body}"

  if [[ -n "$OUTPUT_PATH" ]]; then
    echo "$compiled" > "$OUTPUT_PATH"
    echo -e "${GREEN}Compiled: $INPUT_PATH → $OUTPUT_PATH${NC}"
  else
    echo "$compiled"
  fi
}

# ─── COMPILE ALL ───────────────────────────────────────────────────────────

cmd_compile_all() {
  local out_dir="${OUTPUT_DIR:-${REPO_ROOT}/.signalos/compiled}"
  mkdir -p "$out_dir"

  echo -e "${BLUE}Compiling all artifacts...${NC}"

  local count=0
  local errors=0

  # Find all markdown files with frontmatter
  while IFS= read -r filepath; do
    [[ -z "$filepath" ]] && continue

    # Check if file has frontmatter
    if ! head -1 "$filepath" | grep -q "^---$" 2>/dev/null; then
      # Also compile files with SignalOS comment header
      if ! head -1 "$filepath" | grep -q "SignalOS" 2>/dev/null; then
        continue
      fi
    fi

    local rel_path="${filepath#$REPO_ROOT/}"
    local out_path="${out_dir}/${rel_path}"
    mkdir -p "$(dirname "$out_path")"

    INPUT_PATH="$filepath"
    OUTPUT_PATH="$out_path"
    if cmd_compile 2>/dev/null; then
      count=$((count + 1))
    else
      errors=$((errors + 1))
      echo -e "  ${RED}✗ Failed: $rel_path${NC}"
    fi
  done < <(find "$REPO_ROOT" -name "*.md" -not -path "*/.git/*" -not -path "*/.signalos/compiled/*" -not -path "*/node_modules/*" -not -path "*/legacy-proofs/*" 2>/dev/null)

  echo ""
  echo -e "${GREEN}Compiled $count artifact(s) → $out_dir${NC}"
  if [[ $errors -gt 0 ]]; then
    echo -e "${YELLOW}$errors compilation error(s)${NC}"
  fi
}

# ─── VALIDATE ──────────────────────────────────────────────────────────────

cmd_validate() {
  if [[ -z "$INPUT_PATH" ]]; then
    echo "Error: --input required for validate" >&2
    exit 1
  fi

  if [[ ! -f "$INPUT_PATH" ]]; then
    echo "Error: File not found: $INPUT_PATH" >&2
    exit 1
  fi

  echo -e "${BLUE}Validating: $INPUT_PATH${NC}"
  local errors=0

  # Check frontmatter presence
  if head -1 "$INPUT_PATH" | grep -q "^---$"; then
    echo -e "  ${GREEN}✓ Has frontmatter${NC}"

    local fm
    fm=$(extract_frontmatter "$INPUT_PATH")

    # Check required fields based on artifact type
    local filename
    filename=$(basename "$INPUT_PATH")

    case "$filename" in
      BELIEF*.md)
        for field in wave scale_track; do
          if [[ -z "$(get_field "$fm" "$field")" ]]; then
            echo -e "  ${RED}✗ Missing required field: $field${NC}"
            errors=$((errors + 1))
          else
            echo -e "  ${GREEN}✓ $field: $(get_field "$fm" "$field")${NC}"
          fi
        done
        ;;
      PLAN*.md)
        for field in wave author; do
          if [[ -z "$(get_field "$fm" "$field")" ]]; then
            echo -e "  ${RED}✗ Missing required field: $field${NC}"
            errors=$((errors + 1))
          fi
        done
        ;;
      TRUST_TIER*.md)
        for field in wave trust_tier_ceiling; do
          if [[ -z "$(get_field "$fm" "$field")" ]]; then
            echo -e "  ${RED}✗ Missing required field: $field${NC}"
            errors=$((errors + 1))
          fi
        done
        ;;
    esac
  else
    # Check for SignalOS comment header
    if head -1 "$INPUT_PATH" | grep -q "SignalOS"; then
      echo -e "  ${GREEN}✓ Has SignalOS header (no YAML frontmatter)${NC}"
    else
      echo -e "  ${YELLOW}⚠ No frontmatter or SignalOS header${NC}"
    fi
  fi

  # Check for unresolved template variables
  local unresolved
  unresolved=$(grep -cE '\{\{[a-zA-Z_]+\}\}' "$INPUT_PATH" 2>/dev/null || echo 0)
  if [[ "$unresolved" -gt 0 ]]; then
    echo -e "  ${YELLOW}⚠ $unresolved unresolved template variable(s)${NC}"
  fi

  # Check for broken cross-references
  local broken_refs=0
  while IFS= read -r ref; do
    [[ -z "$ref" ]] && continue
    local ref_name
    ref_name=$(echo "$ref" | sed 's/.*\[\[//' | sed 's/\]\].*//')
    if ! find "$REPO_ROOT" -name "$ref_name" -o -name "${ref_name}.md" 2>/dev/null | grep -q .; then
      echo -e "  ${YELLOW}⚠ Broken reference: [[$ref_name]]${NC}"
      broken_refs=$((broken_refs + 1))
    fi
  done < <(grep -oE '\[\[[^\]]+\]\]' "$INPUT_PATH" 2>/dev/null || true)

  echo ""
  if [[ $errors -gt 0 ]]; then
    echo -e "${RED}Validation failed: $errors error(s)${NC}"
    exit 1
  else
    echo -e "${GREEN}Validation passed${NC}"
    exit 0
  fi
}

# ─── INDEX: generate MANIFEST.md ──────────────────────────────────────────

cmd_index() {
  local manifest="${REPO_ROOT}/MANIFEST.md"
  echo -e "${BLUE}Generating MANIFEST.md...${NC}"

  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  {
    echo "<!-- SignalOS v1.0 — Auto-generated by artifact-compiler.sh -->"
    echo ""
    echo "# SignalOS v1.0 — File Manifest"
    echo ""
    echo "Generated: $ts"
    echo ""
    echo "---"
    echo ""

    # Count by pillar
    local strategy_count execution_count governance_count tool_count other_count
    strategy_count=$(find "$REPO_ROOT/core/strategy" -name "*.md" -not -path "*/.git/*" 2>/dev/null | wc -l || echo 0)
    execution_count=$(find "$REPO_ROOT/core/execution" -type f -not -path "*/.git/*" 2>/dev/null | wc -l || echo 0)
    governance_count=$(find "$REPO_ROOT/core/governance" -type f -not -path "*/.git/*" 2>/dev/null | wc -l || echo 0)
    tool_count=$(find "$REPO_ROOT/core/tool-adapters" -type f -not -path "*/.git/*" 2>/dev/null | wc -l || echo 0)

    echo "## Summary"
    echo ""
    echo "| Pillar | Files |"
    echo "|---|---|"
    echo "| Strategy | $strategy_count |"
    echo "| Execution | $execution_count |"
    echo "| Governance | $governance_count |"
    echo "| Tool-Adapters | $tool_count |"
    echo ""
    echo "---"
    echo ""

    # List by pillar
    for pillar in "core/strategy" "core/execution" "core/governance" "core/tool-adapters"; do
      local pillar_dir="${REPO_ROOT}/$pillar"
      if [[ ! -d "$pillar_dir" ]]; then continue; fi

      echo "## $pillar"
      echo ""
      echo "| File | Type | Size |"
      echo "|---|---|---|"

      find "$pillar_dir" -type f -not -path "*/.git/*" -not -path "*/legacy-proofs/*" 2>/dev/null | sort | while IFS= read -r filepath; do
        local rel_path="${filepath#$REPO_ROOT/}"
        local ext="${filepath##*.}"
        local size
        size=$(wc -c < "$filepath" 2>/dev/null || echo 0)
        local size_human
        if [[ $size -gt 1024 ]]; then
          size_human="$((size / 1024))K"
        else
          size_human="${size}B"
        fi
        echo "| $rel_path | .$ext | $size_human |"
      done

      echo ""
    done

    # Root files
    echo "## Root"
    echo ""
    echo "| File | Type | Size |"
    echo "|---|---|---|"
    for f in "$REPO_ROOT"/*.md "$REPO_ROOT"/*.sh "$REPO_ROOT"/*.yaml "$REPO_ROOT"/*.json; do
      if [[ -f "$f" ]]; then
        local rel_path="${f#$REPO_ROOT/}"
        local ext="${f##*.}"
        local size
        size=$(wc -c < "$f" 2>/dev/null || echo 0)
        echo "| $rel_path | .$ext | ${size}B |"
      fi
    done

  } > "$manifest"

  local total
  total=$(find "$REPO_ROOT" -type f -not -path "*/.git/*" -not -path "*/.signalos/*" -not -path "*/legacy-proofs/*" -not -path "*/node_modules/*" 2>/dev/null | wc -l || echo 0)
  echo -e "${GREEN}MANIFEST.md generated: $total files indexed${NC}"
}

# ─── MAIN ────────────────────────────────────────────────────────────────────

main() {
  parse_args "$@"

  case "$COMMAND" in
    compile)     cmd_compile ;;
    compile-all) cmd_compile_all ;;
    validate)    cmd_validate ;;
    index)       cmd_index ;;
    "")          usage 0 ;;
    *)           echo "Error: Unknown command: $COMMAND" >&2; usage 1 ;;
  esac
}

main "$@"
