#!/usr/bin/env bash
# git-integration.sh — Git/PR Integration Layer
#
# Provides PR creation, review routing, and merge precondition enforcement
# for GitHub, GitLab, and Bitbucket. Called by Build agents after task completion.
#
# Usage:
#   git-integration.sh create-pr  --wave <id> --branch <name> [--platform <github|gitlab|bitbucket>]
#   git-integration.sh route-review --wave <id> --pr <number> [--platform <...>]
#   git-integration.sh check-merge  --wave <id> --pr <number> [--platform <...>]
#   git-integration.sh merge        --wave <id> --pr <number> [--platform <...>]
#
# Platform auto-detection: reads .git/config for origin URL pattern.
# Falls back to SIGNALOS_GIT_PLATFORM env var, then to 'github'.
#
# Exit: 0 = success, 1 = error, 2 = preconditions not met

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
COMMAND=""
WAVE_ID=""
BRANCH=""
PR_NUMBER=""
PLATFORM=""
BASE_BRANCH="main"
AUDIT_LOG=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
  cat <<EOF
Usage: git-integration.sh <command> [options]

Commands:
  create-pr    --wave <id> --branch <name>  Create a PR from a wave branch
  route-review --wave <id> --pr <number>    Assign reviewers per RACI
  check-merge  --wave <id> --pr <number>    Verify merge preconditions
  merge        --wave <id> --pr <number>    Merge PR (if preconditions met)

Options:
  --platform <github|gitlab|bitbucket>  Git platform (auto-detected from origin)
  --base <branch>       Base branch (default: main)
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
      --wave)      WAVE_ID="$2"; shift 2 ;;
      --branch)    BRANCH="$2"; shift 2 ;;
      --pr)        PR_NUMBER="$2"; shift 2 ;;
      --platform)  PLATFORM="$2"; shift 2 ;;
      --base)      BASE_BRANCH="$2"; shift 2 ;;
      --repo-root) REPO_ROOT="$2"; shift 2 ;;
      --help)      usage 0 ;;
      *)           echo "Error: Unknown argument: $1" >&2; usage 1 ;;
    esac
  done

  AUDIT_LOG="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"
}

# ─── PLATFORM DETECTION ────────────────────────────────────────────────────

detect_platform() {
  # Priority 1: explicit flag
  if [[ -n "$PLATFORM" ]]; then
    echo "$PLATFORM"
    return
  fi

  # Priority 2: env var
  if [[ -n "${SIGNALOS_GIT_PLATFORM:-}" ]]; then
    echo "$SIGNALOS_GIT_PLATFORM"
    return
  fi

  # Priority 3: parse origin URL
  local origin_url
  origin_url=$(git config --get remote.origin.url 2>/dev/null || echo "")

  case "$origin_url" in
    *github.com*) echo "github" ;;
    *gitlab.com*|*gitlab.*) echo "gitlab" ;;
    *bitbucket.org*|*bitbucket.*) echo "bitbucket" ;;
    *) echo "github" ;; # default
  esac
}

audit() {
  local action="$1" detail="$2"
  mkdir -p "$(dirname "$AUDIT_LOG")"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "{\"ts\":\"$ts\",\"actor\":\"git-integration\",\"role\":\"system\",\"action\":\"$action\",\"wave\":\"${WAVE_ID:-unknown}\",\"detail\":\"$detail\"}" >> "$AUDIT_LOG"
}

# ─── REVIEW ROUTING TABLE ──────────────────────────────────────────────────

get_reviewers() {
  local branch="$1"

  # Determine reviewers based on Trust Tier and surface type
  local trust_file="${REPO_ROOT}/core/governance/TRUST_TIER.md"
  if [[ ! -f "$trust_file" ]]; then
    trust_file="${REPO_ROOT}/core/execution/TRUST_TIER.md"
  fi

  # Default: PE reviews everything
  local reviewers="PE"

  # If T3 surfaces touched, require PO + PE + QA
  if [[ -f "$trust_file" ]]; then
    # Check which files the branch touches
    local touched_files
    touched_files=$(git diff --name-only "$BASE_BRANCH"..."$branch" 2>/dev/null || echo "")

    local has_t3=false
    while IFS= read -r file; do
      if grep -q "$file" "$trust_file" 2>/dev/null; then
        local tier
        tier=$(grep "$file" "$trust_file" | grep -oP 'T[123]' | head -1 || echo "")
        if [[ "$tier" == "T3" ]]; then
          has_t3=true
          break
        fi
      fi
    done <<< "$touched_files"

    if [[ "$has_t3" == "true" ]]; then
      reviewers="PO,PE,QA"
    fi
  fi

  echo "$reviewers"
}

# ─── CREATE PR ──────────────────────────────────────────────────────────────

cmd_create_pr() {
  if [[ -z "$WAVE_ID" || -z "$BRANCH" ]]; then
    echo "Error: --wave and --branch required for create-pr" >&2
    exit 1
  fi

  local platform
  platform=$(detect_platform)

  echo -e "${BLUE}Creating PR: $BRANCH → $BASE_BRANCH (platform: $platform)${NC}"

  # Build PR title and body
  local title="[Wave $WAVE_ID] $(echo "$BRANCH" | sed 's/wave-[0-9]*\///' | tr '-' ' ')"
  local body="## Wave $WAVE_ID

### Changes
$(git log --oneline "$BASE_BRANCH".."$BRANCH" 2>/dev/null | sed 's/^/- /' || echo "- (commits not available)")

### Trust Tier
$(cat "${REPO_ROOT}/core/governance/TRUST_TIER.md" 2>/dev/null | grep -A2 "^|" | head -5 || echo "See TRUST_TIER.md")

### Reviewers
$(get_reviewers "$BRANCH")

### SignalOS Checklist
- [ ] All validators pass
- [ ] Gate signatures present
- [ ] Evidence pack generated
- [ ] Ownership verified
"

  case "$platform" in
    github)
      if command -v gh &>/dev/null; then
        echo -e "  Using GitHub CLI..."
        gh pr create --title "$title" --body "$body" \
          --base "$BASE_BRANCH" --head "$BRANCH" 2>&1 || {
          echo -e "  ${YELLOW}gh pr create failed. Push branch first: git push -u origin $BRANCH${NC}"
          # Fallback: show the command
          echo ""
          echo "  Manual command:"
          echo "    gh pr create --title \"$title\" --base $BASE_BRANCH --head $BRANCH"
        }
      else
        echo -e "  ${YELLOW}GitHub CLI (gh) not found. Manual PR creation required.${NC}"
        echo ""
        echo "  1. Push: git push -u origin $BRANCH"
        echo "  2. Create PR: $BASE_BRANCH ← $BRANCH"
        echo "  3. Title: $title"
      fi
      ;;
    gitlab)
      if command -v glab &>/dev/null; then
        echo -e "  Using GitLab CLI..."
        glab mr create --title "$title" --description "$body" \
          --target-branch "$BASE_BRANCH" --source-branch "$BRANCH" 2>&1 || true
      else
        echo -e "  ${YELLOW}GitLab CLI (glab) not found.${NC}"
        echo "  Push with MR creation:"
        echo "    git push -u origin $BRANCH -o merge_request.create -o merge_request.title=\"$title\""
      fi
      ;;
    bitbucket)
      echo -e "  ${YELLOW}Bitbucket CLI integration:${NC}"
      echo "  Push: git push -u origin $BRANCH"
      echo "  Then create PR in Bitbucket UI: $BASE_BRANCH ← $BRANCH"
      ;;
  esac

  audit "gate-check" "PR created: $BRANCH → $BASE_BRANCH (platform=$platform)"
  echo -e "\n${GREEN}PR creation initiated for Wave $WAVE_ID${NC}"
}

# ─── ROUTE REVIEW ──────────────────────────────────────────────────────────

cmd_route_review() {
  if [[ -z "$WAVE_ID" || -z "$PR_NUMBER" ]]; then
    echo "Error: --wave and --pr required for route-review" >&2
    exit 1
  fi

  local platform
  platform=$(detect_platform)
  local branch
  branch=$(git branch --show-current 2>/dev/null || echo "unknown")
  local reviewers
  reviewers=$(get_reviewers "$branch")

  echo -e "${BLUE}Routing PR #$PR_NUMBER review to: $reviewers${NC}"

  case "$platform" in
    github)
      if command -v gh &>/dev/null; then
        # Convert role names to GitHub usernames (would need a mapping file in real use)
        echo -e "  ${YELLOW}Note: Map SignalOS roles to GitHub usernames in .signalos/team-mapping.json${NC}"

        local mapping_file="${REPO_ROOT}/.signalos/team-mapping.json"
        if [[ -f "$mapping_file" ]]; then
          local gh_reviewers
          IFS=',' read -ra roles <<< "$reviewers"
          for role in "${roles[@]}"; do
            local gh_user
            gh_user=$(jq -r --arg r "$role" '.[$r] // ""' "$mapping_file" 2>/dev/null)
            if [[ -n "$gh_user" ]]; then
              gh pr edit "$PR_NUMBER" --add-reviewer "$gh_user" 2>/dev/null || true
              echo -e "  ${GREEN}✓ Added reviewer: $gh_user ($role)${NC}"
            fi
          done
        else
          echo "  Create .signalos/team-mapping.json with role→username mappings:"
          echo '  {"PO":"alice","PE":"bob","QA":"carol","DevOps":"dave"}'
        fi
      fi
      ;;
    gitlab)
      echo -e "  Assign reviewers in GitLab: $reviewers"
      ;;
    bitbucket)
      echo -e "  Assign reviewers in Bitbucket: $reviewers"
      ;;
  esac

  audit "gate-check" "review routed: PR #$PR_NUMBER → $reviewers"
}

# ─── CHECK MERGE ───────────────────────────────────────────────────────────

cmd_check_merge() {
  if [[ -z "$WAVE_ID" || -z "$PR_NUMBER" ]]; then
    echo "Error: --wave and --pr required for check-merge" >&2
    exit 1
  fi

  echo -e "${BLUE}Checking merge preconditions for PR #$PR_NUMBER (Wave $WAVE_ID)${NC}"
  echo ""

  local blocked=0

  # 1. All validators must pass
  local validators_dir="${REPO_ROOT}/core/governance/Validators"
  if [[ -d "$validators_dir" ]]; then
    echo -e "  Running validators..."
    for v in "$validators_dir"/*.sh; do
      [[ -x "$v" ]] || continue
      local vname
      vname=$(basename "$v" .sh)
      if bash "$v" --repo-root "$REPO_ROOT" >/dev/null 2>&1; then
        echo -e "    ${GREEN}✓ $vname${NC}"
      else
        echo -e "    ${RED}✗ $vname${NC}"
        blocked=$((blocked + 1))
      fi
    done
  fi

  # 2. No unresolved HALT exceptions for this wave
  if [[ -f "$AUDIT_LOG" ]]; then
    local halt_count
    halt_count=$(jq -c "select(.action==\"exception\" and .severity==\"HALT\" and .wave==\"$WAVE_ID\")" "$AUDIT_LOG" 2>/dev/null | wc -l || echo 0)
    if [[ "$halt_count" -gt 0 ]]; then
      echo -e "  ${RED}✗ $halt_count unresolved HALT exception(s)${NC}"
      blocked=$((blocked + 1))
    else
      echo -e "  ${GREEN}✓ No HALT exceptions${NC}"
    fi
  fi

  # 3. Evidence pack exists
  local evidence_dir="${REPO_ROOT}/.signalos/evidence/wave-${WAVE_ID}-evidence-pack"
  if [[ -d "$evidence_dir" ]]; then
    echo -e "  ${GREEN}✓ Evidence pack exists${NC}"
  else
    echo -e "  ${YELLOW}⚠ No evidence pack (run qa-evidence-pack.sh generate)${NC}"
  fi

  # 4. Ownership verified
  local ownership_guard="${REPO_ROOT}/core/governance/Validators/ownership-guard.sh"
  if [[ -x "$ownership_guard" ]]; then
    if bash "$ownership_guard" check --repo-root "$REPO_ROOT" --warn >/dev/null 2>&1; then
      echo -e "  ${GREEN}✓ Ownership verified${NC}"
    else
      echo -e "  ${RED}✗ Ownership not verified${NC}"
      blocked=$((blocked + 1))
    fi
  fi

  echo ""
  if [[ $blocked -gt 0 ]]; then
    echo -e "${RED}MERGE BLOCKED: $blocked precondition(s) failed${NC}"
    audit "gate-check" "merge-check BLOCKED: PR #$PR_NUMBER, $blocked failures"
    exit 2
  else
    echo -e "${GREEN}All preconditions met. PR #$PR_NUMBER ready to merge.${NC}"
    audit "gate-check" "merge-check PASS: PR #$PR_NUMBER"
    exit 0
  fi
}

# ─── MERGE ──────────────────────────────────────────────────────────────────

cmd_merge() {
  if [[ -z "$WAVE_ID" || -z "$PR_NUMBER" ]]; then
    echo "Error: --wave and --pr required for merge" >&2
    exit 1
  fi

  # Run precondition check first
  echo -e "${BLUE}Pre-merge check...${NC}"
  if ! cmd_check_merge 2>/dev/null; then
    echo -e "${RED}Cannot merge — preconditions not met.${NC}"
    exit 2
  fi

  local platform
  platform=$(detect_platform)

  echo -e "\n${BLUE}Merging PR #$PR_NUMBER...${NC}"

  case "$platform" in
    github)
      if command -v gh &>/dev/null; then
        gh pr merge "$PR_NUMBER" --squash --delete-branch 2>&1 || {
          echo -e "  ${RED}Merge failed. Check GitHub for details.${NC}"
          exit 1
        }
        echo -e "  ${GREEN}✓ Merged and branch deleted${NC}"
      else
        echo "  Manual: gh pr merge $PR_NUMBER --squash --delete-branch"
      fi
      ;;
    gitlab)
      if command -v glab &>/dev/null; then
        glab mr merge "$PR_NUMBER" --squash --remove-source-branch 2>&1 || true
      else
        echo "  Manual: glab mr merge $PR_NUMBER --squash"
      fi
      ;;
    bitbucket)
      echo "  Manual merge required in Bitbucket UI for PR #$PR_NUMBER"
      ;;
  esac

  audit "gate-check" "PR #$PR_NUMBER merged (platform=$platform)"
}

# ─── MAIN ────────────────────────────────────────────────────────────────────

main() {
  parse_args "$@"

  case "$COMMAND" in
    create-pr)    cmd_create_pr ;;
    route-review) cmd_route_review ;;
    check-merge)  cmd_check_merge ;;
    merge)        cmd_merge ;;
    "")           usage 0 ;;
    *)            echo "Error: Unknown command: $COMMAND" >&2; usage 1 ;;
  esac
}

main "$@"
