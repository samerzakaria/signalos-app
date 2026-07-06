#!/usr/bin/env bash
# backlog-harvester.sh — Automated Backlog Intake Harvester
#
# Scans codebase and artifacts for TODO, FIXME, DEFER, HACK, and structured
# "Backlog:" markers. Captures each as a raw backlog item in BACKLOG.yaml.
# Deduplicates against existing items. Runs post-retro or on-demand.
#
# Usage:
#   backlog-harvester.sh scan   [--repo-root <path>] [--wave <id>]
#   backlog-harvester.sh report [--repo-root <path>]
#   backlog-harvester.sh clean  [--repo-root <path>]  (remove items whose source line is gone)
#
# Sources scanned:
#   1. Code comments: TODO, FIXME, HACK, DEFER, XXX
#   2. Markdown markers: "Backlog:", "Future:", "Deferred:"
#   3. DEBRIEF.md action items (lines starting with "- [ ]")
#   4. Exception files (.signalos/exceptions/) — unresolved items
#   5. Retro outputs — improvement items
#
# Exit: 0 = success, 1 = error

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WAVE_ID=""
COMMAND=""
BACKLOG_FILE=""
AUDIT_LOG=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Directories to skip during scan
SKIP_DIRS=(".git" "node_modules" ".signalos/worktrees" "vendor" "dist" "build" "__pycache__" ".venv")

usage() {
  cat <<EOF
Usage: backlog-harvester.sh <command> [options]

Commands:
  scan    Scan codebase and capture new backlog items
  report  Show summary of current backlog
  clean   Remove items whose source location no longer exists

Options:
  --repo-root <path>  Repository root (default: current directory)
  --wave <id>         Current wave (for tagging captured items)
  --help              Show this help

EOF
  exit "${1:-0}"
}

parse_args() {
  COMMAND="${1:-}"
  shift || true

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo-root) REPO_ROOT="$2"; shift 2 ;;
      --wave)      WAVE_ID="$2"; shift 2 ;;
      --help)      usage 0 ;;
      *)           echo "Error: Unknown argument: $1" >&2; usage 1 ;;
    esac
  done

  BACKLOG_FILE="${REPO_ROOT}/core/governance/Governance/BACKLOG.yaml"
  AUDIT_LOG="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"

  # Fallback if primary location doesn't exist
  if [[ ! -f "$BACKLOG_FILE" ]]; then
    BACKLOG_FILE="${REPO_ROOT}/core/strategy/Templates/backlog-schema.yaml"
  fi
}

build_find_excludes() {
  local excludes=""
  for d in "${SKIP_DIRS[@]}"; do
    excludes="$excludes -path '*/$d' -prune -o"
  done
  echo "$excludes"
}

# Generate a deterministic ID from file path + line number
make_item_id() {
  local file="$1" line="$2"
  echo "harvest-$(echo "${file}:${line}" | sha256sum | cut -c1-8)"
}

# Check if an item ID already exists in BACKLOG.yaml
item_exists() {
  local item_id="$1"
  if [[ -f "$BACKLOG_FILE" ]]; then
    grep -q "id: \"$item_id\"" "$BACKLOG_FILE" 2>/dev/null
  else
    return 1
  fi
}

# Append a raw backlog item to BACKLOG.yaml
append_item() {
  local item_id="$1" title="$2" source_file="$3" source_line="$4" marker="$5"
  local ts
  ts=$(date -u +%Y-%m-%d)

  # Escape title for YAML
  title=$(echo "$title" | sed 's/"/\\"/g' | head -c 200)

  cat >> "$BACKLOG_FILE" <<ITEMEOF

  - id: "$item_id"
    title: "$title"
    belief: ""
    status: raw
    bet_score: 0
    blast_radius: "Contained"
    wave: ${WAVE_ID:-0}
    created: "$ts"
    source_file: "$source_file"
    source_line: $source_line
    marker: "$marker"
    acceptance_criteria: []
    notes: "Auto-captured by backlog-harvester"
ITEMEOF
}

# ─── SCAN: code comments ──────────────────────────────────────────────────

scan_code_comments() {
  echo -e "  ${BLUE}Scanning code comments (TODO/FIXME/HACK/DEFER/XXX)...${NC}"
  local count=0

  # Build grep pattern
  local pattern='(TODO|FIXME|HACK|DEFER|XXX)\s*[:(-]?\s*'

  # Find source files, skip binary and excluded dirs
  while IFS=: read -r file line_num content; do
    [[ -z "$file" ]] && continue

    local rel_path="${file#$REPO_ROOT/}"
    local marker
    marker=$(echo "$content" | grep -oP '(TODO|FIXME|HACK|DEFER|XXX)' | head -1)
    local description
    description=$(echo "$content" | sed -E "s/.*${marker}\s*[:(-]?\s*//" | sed 's/\s*\*\/\s*$//' | sed 's/\s*#\s*$//' | sed 's/\s*\/\/\s*$//' | xargs)

    if [[ -z "$description" || ${#description} -lt 5 ]]; then
      continue
    fi

    local item_id
    item_id=$(make_item_id "$rel_path" "$line_num")

    if ! item_exists "$item_id"; then
      append_item "$item_id" "[$marker] $description" "$rel_path" "$line_num" "$marker"
      count=$((count + 1))
    fi
  done < <(grep -rnE "$pattern" "$REPO_ROOT" \
    --include='*.sh' --include='*.py' --include='*.js' --include='*.ts' \
    --include='*.go' --include='*.java' --include='*.rb' --include='*.rs' \
    --include='*.yaml' --include='*.yml' --include='*.json' \
    --exclude-dir='.git' --exclude-dir='node_modules' --exclude-dir='vendor' \
    --exclude-dir='.signalos' --exclude-dir='Archive' \
    2>/dev/null || true)

  echo -e "    ${GREEN}$count new item(s) from code comments${NC}"
  return $count
}

# ─── SCAN: markdown markers ───────────────────────────────────────────────

scan_markdown_markers() {
  echo -e "  ${BLUE}Scanning markdown markers (Backlog:/Future:/Deferred:)...${NC}"
  local count=0

  while IFS=: read -r file line_num content; do
    [[ -z "$file" ]] && continue

    local rel_path="${file#$REPO_ROOT/}"
    local marker
    marker=$(echo "$content" | grep -oiP '(Backlog|Future|Deferred)' | head -1)
    local description
    description=$(echo "$content" | sed -E "s/.*${marker}\s*[:]\s*//" | xargs)

    if [[ -z "$description" || ${#description} -lt 5 ]]; then
      continue
    fi

    local item_id
    item_id=$(make_item_id "$rel_path" "$line_num")

    if ! item_exists "$item_id"; then
      append_item "$item_id" "$description" "$rel_path" "$line_num" "$marker"
      count=$((count + 1))
    fi
  done < <(grep -rnEi '(Backlog|Future|Deferred)\s*:' "$REPO_ROOT" \
    --include='*.md' \
    --exclude-dir='.git' --exclude-dir='node_modules' --exclude-dir='Archive' \
    --exclude-dir='.signalos' \
    2>/dev/null || true)

  echo -e "    ${GREEN}$count new item(s) from markdown markers${NC}"
  return $count
}

# ─── SCAN: DEBRIEF action items ───────────────────────────────────────────

scan_debrief_actions() {
  echo -e "  ${BLUE}Scanning RETROSPECTIVE.md action items...${NC}"
  local count=0

  for debrief in "${REPO_ROOT}"/core/governance/Governance/RETROSPECTIVE.md; do
    [[ -f "$debrief" ]] || continue

    local rel_path="${debrief#$REPO_ROOT/}"
    local line_num=0

    while IFS= read -r line; do
      line_num=$((line_num + 1))
      # Unchecked action items: "- [ ] something"
      if echo "$line" | grep -qE '^\s*-\s*\[\s*\]'; then
        local description
        description=$(echo "$line" | sed 's/^\s*-\s*\[\s*\]\s*//' | xargs)

        if [[ ${#description} -lt 5 ]]; then
          continue
        fi

        local item_id
        item_id=$(make_item_id "$rel_path" "$line_num")

        if ! item_exists "$item_id"; then
          append_item "$item_id" "[DEBRIEF] $description" "$rel_path" "$line_num" "DEBRIEF"
          count=$((count + 1))
        fi
      fi
    done < "$debrief"
  done

  echo -e "    ${GREEN}$count new item(s) from DEBRIEF action items${NC}"
  return $count
}

# ─── SCAN: exception files ────────────────────────────────────────────────

scan_exceptions() {
  echo -e "  ${BLUE}Scanning unresolved exceptions...${NC}"
  local count=0
  local exc_dir="${REPO_ROOT}/.signalos/exceptions"

  if [[ ! -d "$exc_dir" ]]; then
    echo -e "    ${GREEN}No exception directory found${NC}"
    return 0
  fi

  for exc_file in "$exc_dir"/EXC-*.md; do
    [[ -f "$exc_file" ]] || continue

    # Check if still OPEN
    if ! grep -q "Status: OPEN" "$exc_file" 2>/dev/null; then
      continue
    fi

    local rel_path="${exc_file#$REPO_ROOT/}"
    local exc_type
    exc_type=$(grep -oP 'Type:\*\*\s*\K.*' "$exc_file" 2>/dev/null || echo "unknown")
    local exc_msg
    exc_msg=$(sed -n '/^## Description/,/^## /p' "$exc_file" 2>/dev/null | grep -v '^##' | head -3 | xargs)

    if [[ -z "$exc_msg" || ${#exc_msg} -lt 5 ]]; then
      exc_msg="Unresolved exception: $exc_type"
    fi

    local item_id
    item_id=$(make_item_id "$rel_path" "1")

    if ! item_exists "$item_id"; then
      append_item "$item_id" "[EXCEPTION] $exc_msg" "$rel_path" "1" "EXCEPTION"
      count=$((count + 1))
    fi
  done

  echo -e "    ${GREEN}$count new item(s) from exceptions${NC}"
  return $count
}

# ─── SCAN: main command ──────────────────────────────────────────────────

cmd_scan() {
  echo -e "${BLUE}Backlog Harvester — scanning $REPO_ROOT${NC}"
  echo ""

  # Ensure backlog file exists
  if [[ ! -f "$BACKLOG_FILE" ]]; then
    mkdir -p "$(dirname "$BACKLOG_FILE")"
    cat > "$BACKLOG_FILE" <<'INITEOF'
# BACKLOG.yaml — Two-Speed Backlog
# Status lifecycle: raw → refined → in-progress → done | deferred | cancelled
# Only 'refined' items can be picked up for building.

backlog:
INITEOF
  fi

  local total=0
  local c=0

  scan_code_comments || true; c=$?; total=$((total + c))
  scan_markdown_markers || true; c=$?; total=$((total + c))
  scan_debrief_actions || true; c=$?; total=$((total + c))
  scan_exceptions || true; c=$?; total=$((total + c))

  echo ""
  echo -e "${GREEN}Harvest complete: $total new item(s) captured as 'raw'${NC}"
  echo "  Backlog: $BACKLOG_FILE"

  # Audit
  mkdir -p "$(dirname "$AUDIT_LOG")"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "{\"ts\":\"$ts\",\"actor\":\"backlog-harvester\",\"role\":\"system\",\"action\":\"backlog-harvest\",\"wave\":\"${WAVE_ID:-unknown}\",\"detail\":\"$total new items captured\"}" >> "$AUDIT_LOG"
}

# ─── REPORT ──────────────────────────────────────────────────────────────

cmd_report() {
  echo -e "${BLUE}Backlog Report${NC}"
  echo ""

  if [[ ! -f "$BACKLOG_FILE" ]]; then
    echo "  No backlog file found."
    exit 0
  fi

  # Count by status using grep (portable, no YAML parser required)
  local raw refined in_progress done deferred cancelled
  raw=$(grep -c 'status: raw' "$BACKLOG_FILE" 2>/dev/null || echo 0)
  refined=$(grep -c 'status: refined' "$BACKLOG_FILE" 2>/dev/null || echo 0)
  in_progress=$(grep -c 'status: in-progress' "$BACKLOG_FILE" 2>/dev/null || echo 0)
  done=$(grep -c 'status: done' "$BACKLOG_FILE" 2>/dev/null || echo 0)
  deferred=$(grep -c 'status: deferred' "$BACKLOG_FILE" 2>/dev/null || echo 0)
  cancelled=$(grep -c 'status: cancelled' "$BACKLOG_FILE" 2>/dev/null || echo 0)

  local total=$((raw + refined + in_progress + done + deferred + cancelled))

  echo "  Total items:   $total"
  echo "  Raw:           $raw"
  echo "  Refined:       $refined"
  echo "  In progress:   $in_progress"
  echo "  Done:          $done"
  echo "  Deferred:      $deferred"
  echo "  Cancelled:     $cancelled"
  echo ""

  # Count by marker
  echo "  By source:"
  for marker in TODO FIXME HACK DEFER XXX DEBRIEF EXCEPTION Backlog Future Deferred; do
    local mc
    mc=$(grep -c "marker: \"$marker\"" "$BACKLOG_FILE" 2>/dev/null || echo 0)
    if [[ "$mc" -gt 0 ]]; then
      echo "    $marker: $mc"
    fi
  done
}

# ─── CLEAN ───────────────────────────────────────────────────────────────

cmd_clean() {
  echo -e "${BLUE}Cleaning stale backlog items...${NC}"

  if [[ ! -f "$BACKLOG_FILE" ]]; then
    echo "  No backlog file found."
    exit 0
  fi

  local removed=0

  # Extract source_file + source_line pairs, check if the source still has the marker
  python3 -c "
import yaml, sys, os

backlog_path = '$BACKLOG_FILE'
repo_root = '$REPO_ROOT'

with open(backlog_path) as f:
    data = yaml.safe_load(f) or {}

items = data.get('backlog', []) or []
keep = []
removed = 0

for item in items:
    if not item:
        continue
    src = item.get('source_file', '')
    if not src or item.get('status') in ('done', 'cancelled'):
        keep.append(item)
        continue

    full_path = os.path.join(repo_root, src)
    if not os.path.exists(full_path):
        print(f'  Removed: {item.get(\"id\", \"?\")} — source file gone: {src}')
        removed += 1
        continue

    keep.append(item)

data['backlog'] = keep

with open(backlog_path, 'w') as f:
    yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

print(f'\nCleaned {removed} stale item(s)')
" 2>/dev/null || echo "  Python/YAML not available. Clean requires python3 + PyYAML."
}

# ─── MAIN ────────────────────────────────────────────────────────────────────

main() {
  parse_args "$@"

  case "$COMMAND" in
    scan)   cmd_scan ;;
    report) cmd_report ;;
    clean)  cmd_clean ;;
    "")     usage 0 ;;
    *)      echo "Error: Unknown command: $COMMAND" >&2; usage 1 ;;
  esac
}

main "$@"
