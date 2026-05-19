#!/usr/bin/env bash
# worktree-manager.sh — Build ×N Worktree Supervisor
#
# Creates, monitors, reconciles, and retires git worktrees for parallel Build agents.
# Reads tasks from PLAN.md, creates one worktree per parallelizable task,
# tracks status, and logs handoffs.
#
# Usage:
#   worktree-manager.sh <command> [options]
#
# Commands:
#   create   --wave <id> --plan <path>  Create worktrees from PLAN.md tasks
#   status   --wave <id>                Show status of all worktrees for a Wave
#   reconcile --wave <id>               Check for drift, conflicts, stale branches
#   retire   --wave <id>                Remove merged worktrees, archive handoffs
#   list                                List all active worktrees
#
# AMD-CORE-008 fixes applied:
#   1. Parser: HTML comment task format <!-- task: id=X tier=T parallel=true -->
#   2. Journal routing: audit_log() uses journal-append.sh instead of direct echo
#   3. git merge-tree: uses --write-tree --no-messages; git version check
#   4. Completion signal: step_id field in state JSON

set -euo pipefail

# SCRIPT_DIR resolves to <SignalOS-source>/core/execution/build — used to
# locate companion helpers that ship with this script. REPO_ROOT, in
# contrast, is the repo being managed (overridable via --repo-root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
WT_STATE_HELPER="${SCRIPT_DIR}/../../../cli/signalos_lib/_worktree_state.py"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
COMMAND=""
WAVE_ID=""
PLAN_PATH=""
SESSION_ID=""         # optional, passed via --session-id
WORKTREE_BASE=""      # deferred — set in resolve_paths()
HANDOFFS_FILE=""      # deferred — set in resolve_paths()
STATE_FILE=""         # deferred — set in resolve_paths()
AUDIT_LOG=""          # deferred — set in resolve_paths()
MAX_CONCURRENT=5

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
  cat <<EOF
Usage: worktree-manager.sh <command> [options]

Commands:
  create   --wave <id> --plan <path>  Create worktrees from PLAN.md
  status   --wave <id>                Show worktree status
  reconcile --wave <id>               Check drift and conflicts
  retire   --wave <id>                Remove merged worktrees
  list                                List all active worktrees

Options:
  --max-concurrent <n>  Max parallel worktrees (default: 5)
  --repo-root <path>    Repository root
  --session-id <id>     Session id for journal routing (default: auto)
  --help                Show this help

EOF
  exit "${1:-0}"
}

parse_args() {
  COMMAND="${1:-}"
  # Handle --help / -h as first argument (before command dispatch)
  if [[ "$COMMAND" == "--help" || "$COMMAND" == "-h" ]]; then
    usage 0
  fi
  shift || true

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --wave)           WAVE_ID="$2"; shift 2 ;;
      --plan)           PLAN_PATH="$2"; shift 2 ;;
      --max-concurrent) MAX_CONCURRENT="$2"; shift 2 ;;
      --repo-root)      REPO_ROOT="$2"; shift 2 ;;
      --session-id)     SESSION_ID="$2"; shift 2 ;;
      --help|-h)        usage 0 ;;
      *)                echo "Error: Unknown argument: $1" >&2; usage 1 ;;
    esac
  done
}

resolve_paths() {
  # Must run AFTER parse_args so --repo-root is honoured.
  WORKTREE_BASE="${REPO_ROOT}/.signalos/worktrees"
  HANDOFFS_FILE="${REPO_ROOT}/core/governance/Worktree-sync/HANDOFFS.md"
  STATE_FILE="${REPO_ROOT}/.signalos/worktree-state.json"
  AUDIT_LOG="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"
  # Default SESSION_ID if not provided
  if [[ -z "$SESSION_ID" ]]; then
    SESSION_ID="worktree-$(date +%Y%m%dT%H%M%S)"
  fi
}

init_state() {
  mkdir -p "$(dirname "$STATE_FILE")"
  mkdir -p "$WORKTREE_BASE"
  if [[ ! -f "$STATE_FILE" ]]; then
    echo '{"worktrees":[]}' > "$STATE_FILE"
  fi
}

# ─── AMD-CORE-008 Fix 2: Journal routing via journal-append.sh ───────────────
audit_log() {
  local action="$1" detail="$2"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # Map action to SignalOS event type
  local event_type
  case "$action" in
    worktree-create)     event_type="worktree.created" ;;
    worktree-reconcile)  event_type="worktree.reconciled" ;;
    worktree-retire)     event_type="worktree.retired" ;;
    worktree-drift)      event_type="worktree.drifted" ;;
    *)                   event_type="worktree.${action}" ;;
  esac

  local journal_append="${REPO_ROOT}/core/execution/hooks/_lib/journal-append.sh"
  if [[ -f "$journal_append" ]]; then
    local payload
    payload=$(printf '{"ts":"%s","type":"%s","actor":"worktree-manager","session_id":"%s","wave":"%s","detail":"%s"}' \
      "$ts" "$event_type" "$SESSION_ID" "$WAVE_ID" "$detail")
    bash "$journal_append" \
      --session-id "$SESSION_ID" \
      --event "$payload" 2>/dev/null || true
  else
    # Fallback: direct write to AUDIT_LOG (original behaviour)
    mkdir -p "$(dirname "$AUDIT_LOG")"
    printf '{"ts":"%s","actor":"worktree-manager","role":"system","action":"%s","wave":"%s","detail":"%s"}\n' \
      "$ts" "$action" "$WAVE_ID" "$detail" >> "$AUDIT_LOG"
  fi
}

# ─── CREATE ──────────────────────────────────────────────────────────────────

# AMD-CORE-008 Fix 1: Parser supports HTML comment task format as primary pattern
extract_tasks_from_plan() {
  local plan="$1"

  # PRIMARY: HTML comment format: <!-- task: id=<id> tier=<T1|T2|T3> parallel=true -->
  local comment_tasks
  comment_tasks=$(grep -oE '<!--\s*task:\s*id=[^ >]+[^>]*-->' "$plan" 2>/dev/null || true)
  if [[ -n "$comment_tasks" ]]; then
    echo "$comment_tasks" | while IFS= read -r comment; do
      # Extract id field
      local task_id
      task_id=$(echo "$comment" | grep -oE 'id=[^ >]+' | sed 's/id=//' | head -1)
      [[ -z "$task_id" ]] && continue
      # Extract tier field
      local tier
      tier=$(echo "$comment" | grep -oE 'tier=(T[123])' | sed 's/tier=//' | head -1 || echo "T1")
      echo "${task_id} ${tier}"
    done | head -n "$MAX_CONCURRENT"
    return
  fi

  # FALLBACK 1: markdown checkbox list — "- [ ] **Task N**"
  local checkbox_tasks
  checkbox_tasks=$(grep -E '^\s*-\s*\[.\]\s*\*\*Task' "$plan" 2>/dev/null || true)
  if [[ -n "$checkbox_tasks" ]]; then
    echo "$checkbox_tasks" | \
      sed 's/.*\*\*Task \([0-9]*\)[^*]*\*\*:\s*/\1 /' | \
      head -n "$MAX_CONCURRENT"
    return
  fi

  # FALLBACK 2: markdown table format
  grep -E '^\|\s*[0-9]+' "$plan" 2>/dev/null | \
    awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $2); gsub(/^[ \t]+|[ \t]+$/, "", $3); print $2, $3}' | \
    head -n "$MAX_CONCURRENT" || true
}

cmd_create() {
  if [[ -z "$WAVE_ID" || -z "$PLAN_PATH" ]]; then
    echo "Error: --wave and --plan are required for create" >&2
    exit 1
  fi

  if [[ ! -f "$PLAN_PATH" ]]; then
    echo "Error: PLAN.md not found at $PLAN_PATH" >&2
    exit 1
  fi

  init_state
  echo -e "${BLUE}Creating worktrees for Wave $WAVE_ID from $PLAN_PATH${NC}"

  local task_count=0
  local main_branch
  main_branch=$(git rev-parse --abbrev-ref HEAD)

  while IFS= read -r task_line; do
    [[ -z "$task_line" ]] && continue
    local task_num task_desc
    task_num=$(echo "$task_line" | awk '{print $1}')
    task_desc=$(echo "$task_line" | cut -d' ' -f2- | tr ' ' '-' | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]//g' | head -c 40)

    local branch_name="wave-${WAVE_ID}/task-${task_num}-${task_desc}"
    local worktree_path="${WORKTREE_BASE}/${branch_name//\//-}"

    # AMD-CORE-008 Fix 4: step_id field (same as branch name by default)
    local step_id="${branch_name}"

    # Check if worktree already exists
    if git worktree list --porcelain | grep -q "$worktree_path"; then
      echo -e "  ${YELLOW}⚠ Worktree already exists: $branch_name${NC}"
      continue
    fi

    # Check concurrent limit
    local active_count
    active_count=$(git worktree list --porcelain | grep -c "^worktree " || echo 0)
    if [[ $active_count -ge $((MAX_CONCURRENT + 1)) ]]; then
      # AMD-CORE-012 T4: overflow queue — record pending task in worktree-state.json
      # instead of silently dropping it.
      echo -e "  ${YELLOW}⚠ Max concurrent worktrees ($MAX_CONCURRENT) reached. Task ${step_id} queued.${NC}"
      local ts_q
      ts_q=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      python3 "$WT_STATE_HELPER" append-pending \
        "$STATE_FILE" "$task_num" "$step_id" "$ts_q" || true
      continue
    fi

    # Create branch and worktree
    git branch "$branch_name" "$main_branch" 2>/dev/null || true
    git worktree add "$worktree_path" "$branch_name" 2>/dev/null

    # Update state via safe helper — values passed as CLI args, never interpolated
    # into Python source code (B-6: code injection prevention)
    python3 "$WT_STATE_HELPER" append-worktree \
      "$STATE_FILE" "$WAVE_ID" "$task_num" "$step_id" "$branch_name" \
      "$worktree_path" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" || true

    echo -e "  ${GREEN}✓ Created: $branch_name → $worktree_path${NC}"
    task_count=$((task_count + 1))
    audit_log "worktree-create" "branch=$branch_name task=$task_num step_id=$step_id"

  done < <(extract_tasks_from_plan "$PLAN_PATH")

  echo ""
  echo -e "${GREEN}Created $task_count worktree(s) for Wave $WAVE_ID${NC}"
}

# ─── STATUS ──────────────────────────────────────────────────────────────────

cmd_status() {
  if [[ -z "$WAVE_ID" ]]; then
    echo "Error: --wave is required for status" >&2
    exit 1
  fi

  init_state
  echo -e "${BLUE}Worktree status for Wave $WAVE_ID${NC}"
  echo ""

  local found=0
  while IFS= read -r wt_line; do
    local wt_path
    wt_path=$(echo "$wt_line" | awk '{print $1}')
    local wt_branch
    wt_branch=$(echo "$wt_line" | sed 's/.*\[//' | sed 's/\]//')

    if [[ "$wt_branch" == *"wave-${WAVE_ID}"* ]]; then
      found=$((found + 1))

      # Read step_id from state JSON if available (B-6: use safe helper)
      local step_id=""
      if [[ -f "$STATE_FILE" ]]; then
        step_id=$(python3 "$WT_STATE_HELPER" read-step-id \
          "$STATE_FILE" "$wt_branch" 2>/dev/null || true)
      fi

      # Check branch status
      local ahead behind
      ahead=$(git rev-list --count "main..${wt_branch}" 2>/dev/null || echo "?")
      behind=$(git rev-list --count "${wt_branch}..main" 2>/dev/null || echo "?")

      local last_commit
      last_commit=$(git log -1 --format="%h %s" "$wt_branch" 2>/dev/null || echo "no commits")

      local status_icon
      if [[ "$behind" != "0" && "$behind" != "?" ]]; then
        status_icon="${YELLOW}⚠ DRIFT${NC}"
      else
        status_icon="${GREEN}✓ OK${NC}"
      fi

      echo -e "  ${status_icon}  ${wt_branch}"
      echo "        Path:    $wt_path"
      if [[ -n "$step_id" ]]; then
        echo "        Step ID: $step_id"
      fi
      echo "        Ahead:   $ahead  Behind: $behind"
      echo "        Last:    $last_commit"
      echo ""
    fi
  done < <(git worktree list 2>/dev/null)

  if [[ $found -eq 0 ]]; then
    echo "  No worktrees found for Wave $WAVE_ID"
  fi
}

# ─── RECONCILE ───────────────────────────────────────────────────────────────

cmd_reconcile() {
  if [[ -z "$WAVE_ID" ]]; then
    echo "Error: --wave is required for reconcile" >&2
    exit 1
  fi

  init_state
  echo -e "${BLUE}Reconciling worktrees for Wave $WAVE_ID${NC}"

  # AMD-CORE-008 Fix 3: git version check for merge-tree --write-tree support
  local git_version_str
  git_version_str=$(git --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1 || echo "0.0")
  local git_major git_minor
  git_major=$(echo "$git_version_str" | cut -d. -f1)
  git_minor=$(echo "$git_version_str" | cut -d. -f2)
  local can_use_write_tree=true
  if [[ "$git_major" -lt 2 ]] || { [[ "$git_major" -eq 2 ]] && [[ "$git_minor" -lt 38 ]]; }; then
    echo -e "  ${YELLOW}⚠ git < 2.38 detected — skipping conflict check (merge-tree --write-tree not available)${NC}"
    can_use_write_tree=false
  fi

  local issues=0
  while IFS= read -r wt_line; do
    local wt_branch
    wt_branch=$(echo "$wt_line" | sed 's/.*\[//' | sed 's/\]//')

    if [[ "$wt_branch" == *"wave-${WAVE_ID}"* ]]; then
      # Check drift from main
      local behind
      behind=$(git rev-list --count "${wt_branch}..main" 2>/dev/null || echo 0)

      if [[ "$behind" -gt 10 ]]; then
        echo -e "  ${RED}✗ LARGE DRIFT: $wt_branch is $behind commits behind main${NC}"
        echo "    → PE must rebase manually (>10 commits = unsafe for auto-merge)"
        issues=$((issues + 1))

        # Route exception — honour severity exit code
        local exc_rc=0
        bash "${REPO_ROOT}/core/execution/hooks/exception-router.sh" \
          --type "tier-mismatch" --source "worktree-manager" \
          --surface "$wt_branch" --wave "$WAVE_ID" \
          --message "Worktree $wt_branch is $behind commits behind main. PE must rebase." \
          --repo-root "$REPO_ROOT" 2>/dev/null || exc_rc=$?

        if [[ $exc_rc -eq 1 ]]; then
          echo -e "  ${RED}HALT: Exception router halted the wave. PE must resolve before continuing.${NC}"
          audit_log "worktree-reconcile" "HALTED: $wt_branch drift=$behind exception_exit=$exc_rc"
          exit 1
        elif [[ $exc_rc -eq 2 ]]; then
          echo -e "  ${RED}BLOCK_MERGE: Merge blocked for $wt_branch until drift is resolved.${NC}"
          audit_log "worktree-reconcile" "BLOCKED: $wt_branch drift=$behind exception_exit=$exc_rc"
        fi
        audit_log "worktree-drift" "branch=$wt_branch drift=$behind"

      elif [[ "$behind" -gt 0 ]]; then
        echo -e "  ${YELLOW}⚠ MINOR DRIFT: $wt_branch is $behind commits behind main${NC}"
        echo "    → Consider rebasing before PR"
      else
        echo -e "  ${GREEN}✓ $wt_branch is up to date${NC}"
      fi

      # AMD-CORE-008 Fix 3: conflict check via --write-tree --no-messages
      if [[ "$can_use_write_tree" == true ]]; then
        if ! git merge-tree --write-tree --no-messages "$wt_branch" main >/dev/null 2>&1; then
          echo -e "  ${RED}✗ CONFLICT: $wt_branch has merge conflicts with main${NC}"
          issues=$((issues + 1))
        fi
      fi
    fi
  done < <(git worktree list 2>/dev/null)

  if [[ $issues -gt 0 ]]; then
    echo ""
    echo -e "${RED}$issues issue(s) found. PE must resolve before merge.${NC}"
    audit_log "worktree-reconcile" "issues=$issues"
  else
    echo ""
    echo -e "${GREEN}All worktrees clean.${NC}"
    audit_log "worktree-reconcile" "clean"
  fi
}

# ─── RETIRE ──────────────────────────────────────────────────────────────────

cmd_retire() {
  if [[ -z "$WAVE_ID" ]]; then
    echo "Error: --wave is required for retire" >&2
    exit 1
  fi

  init_state
  echo -e "${BLUE}Retiring merged worktrees for Wave $WAVE_ID${NC}"

  local retired=0
  while IFS= read -r wt_line; do
    local wt_path wt_branch
    wt_path=$(echo "$wt_line" | awk '{print $1}')
    wt_branch=$(echo "$wt_line" | sed 's/.*\[//' | sed 's/\]//')

    if [[ "$wt_branch" == *"wave-${WAVE_ID}"* ]]; then
      # Check if branch is merged to main
      if git branch --merged main 2>/dev/null | grep -q "$wt_branch"; then
        echo -e "  ${GREEN}✓ Retiring: $wt_branch (merged)${NC}"
        git worktree remove "$wt_path" --force 2>/dev/null || true
        git branch -d "$wt_branch" 2>/dev/null || true
        retired=$((retired + 1))
        audit_log "worktree-retire" "branch=$wt_branch"
      else
        echo -e "  ${YELLOW}⚠ Keeping: $wt_branch (not yet merged)${NC}"
      fi
    fi
  done < <(git worktree list 2>/dev/null)

  echo ""
  echo -e "Retired $retired worktree(s)"
}

# ─── LIST ────────────────────────────────────────────────────────────────────

cmd_list() {
  echo -e "${BLUE}All active worktrees${NC}"
  echo ""
  git worktree list 2>/dev/null || echo "Not a git repository"
}

# ─── MAIN ────────────────────────────────────────────────────────────────────

main() {
  parse_args "$@"
  resolve_paths

  case "$COMMAND" in
    create)    cmd_create ;;
    status)    cmd_status ;;
    reconcile) cmd_reconcile ;;
    retire)    cmd_retire ;;
    list)      cmd_list ;;
    "")        usage 0 ;;
    *)         echo "Error: Unknown command: $COMMAND" >&2; usage 1 ;;
  esac
}

main "$@"
