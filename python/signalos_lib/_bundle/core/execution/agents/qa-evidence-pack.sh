#!/usr/bin/env bash
# qa-evidence-pack.sh — QA Evidence Packet Generator
#
# Collects test results, coverage reports, Stage-1 review verdicts,
# Trust Tier declarations, diff stats, and exception logs into a
# structured evidence bundle that Gate 5 requires before signing.
#
# Usage:
#   qa-evidence-pack.sh generate --wave <id> [--repo-root <path>]
#   qa-evidence-pack.sh verify  --wave <id> [--repo-root <path>]
#   qa-evidence-pack.sh list    [--repo-root <path>]
#
# Output: .signalos/evidence/wave-{N}-evidence-pack/
#   ├── SUMMARY.md          (human-readable overview)
#   ├── test-results.json   (aggregated test suite outcomes)
#   ├── coverage.json       (line/branch coverage per module)
#   ├── review-verdicts.json(Stage-1 automated review outcomes)
#   ├── trust-tier.json     (declared tier + surfaces)
#   ├── diff-stats.json     (files changed, lines added/removed)
#   ├── exceptions.json     (any exceptions routed during wave)
#   ├── signal-window.json  (metric readings from Observability)
#   └── audit-extract.jsonl (filtered audit entries for this wave)
#
# Exit: 0 = pack generated, 1 = error, 2 = pack incomplete (missing required sections)

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WAVE_ID=""
COMMAND=""
AUDIT_LOG=""
EVIDENCE_DIR=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
  cat <<EOF
Usage: qa-evidence-pack.sh <command> [options]

Commands:
  generate  --wave <id>  Collect evidence and build pack
  verify    --wave <id>  Check if pack is complete for Gate 5
  list                   List all existing evidence packs

Options:
  --repo-root <path>   Repository root (default: current directory)
  --help               Show this help

EOF
  exit "${1:-0}"
}

parse_args() {
  COMMAND="${1:-}"
  shift || true

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --wave)      WAVE_ID="$2"; shift 2 ;;
      --repo-root) REPO_ROOT="$2"; shift 2 ;;
      --help)      usage 0 ;;
      *)           echo "Error: Unknown argument: $1" >&2; usage 1 ;;
    esac
  done

  AUDIT_LOG="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"
  EVIDENCE_DIR="${REPO_ROOT}/.signalos/evidence/wave-${WAVE_ID}-evidence-pack"
}

# ─── COLLECT: test results ──────────────────────────────────────────────────

collect_test_results() {
  echo -e "  ${BLUE}Collecting test results...${NC}"
  local out="${EVIDENCE_DIR}/test-results.json"

  # Look for common test output locations
  local results='{"collected_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","sources":[],"suites":[]}'

  # Jest / Vitest JSON output
  for f in "${REPO_ROOT}"/test-results.json "${REPO_ROOT}"/coverage/test-results.json "${REPO_ROOT}"/.signalos/test-output/*.json; do
    if [[ -f "$f" ]]; then
      results=$(echo "$results" | jq --arg src "$f" '.sources += [$src]')
      local suite_data
      suite_data=$(jq '{name: (.testResults[0].testFilePath // "unknown"), passed: .numPassedTests, failed: .numFailedTests, total: .numTotalTests}' "$f" 2>/dev/null || echo '{}')
      if [[ "$suite_data" != "{}" ]]; then
        results=$(echo "$results" | jq --argjson s "$suite_data" '.suites += [$s]')
      fi
    fi
  done

  # pytest XML output
  for f in "${REPO_ROOT}"/pytest-results.xml "${REPO_ROOT}"/.signalos/test-output/*.xml; do
    if [[ -f "$f" ]]; then
      results=$(echo "$results" | jq --arg src "$f" '.sources += [$src]')
      # Extract summary from XML
      local tests errors failures
      tests=$(grep -oP 'tests="\K[0-9]+' "$f" 2>/dev/null | head -1 || echo "0")
      errors=$(grep -oP 'errors="\K[0-9]+' "$f" 2>/dev/null | head -1 || echo "0")
      failures=$(grep -oP 'failures="\K[0-9]+' "$f" 2>/dev/null | head -1 || echo "0")
      results=$(echo "$results" | jq --arg t "$tests" --arg e "$errors" --arg f "$failures" \
        '.suites += [{"name":"pytest","total":($t|tonumber),"failed":(($e|tonumber)+($f|tonumber)),"passed":(($t|tonumber)-(($e|tonumber)+($f|tonumber)))}]')
    fi
  done

  # If no automated results found, create placeholder
  if echo "$results" | jq -e '.sources | length == 0' >/dev/null 2>&1; then
    results=$(echo "$results" | jq '.note = "No automated test output found. Teams must configure test runners to write results to .signalos/test-output/"')
  fi

  echo "$results" | jq '.' > "$out"
  echo -e "    ${GREEN}Done${NC} → test-results.json"
}

# ─── COLLECT: coverage reports ──────────────────────────────────────────────

collect_coverage() {
  echo -e "  ${BLUE}Collecting coverage reports...${NC}"
  local out="${EVIDENCE_DIR}/coverage.json"

  local coverage='{"collected_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","sources":[],"summary":{}}'

  # Istanbul / c8 coverage
  for f in "${REPO_ROOT}"/coverage/coverage-summary.json "${REPO_ROOT}"/coverage/coverage-final.json; do
    if [[ -f "$f" ]]; then
      coverage=$(echo "$coverage" | jq --arg src "$f" '.sources += [$src]')
      local totals
      totals=$(jq '.total // {}' "$f" 2>/dev/null || echo '{}')
      if [[ "$totals" != "{}" ]]; then
        coverage=$(echo "$coverage" | jq --argjson t "$totals" '.summary = $t')
      fi
    fi
  done

  # Python coverage.py JSON
  for f in "${REPO_ROOT}"/coverage.json "${REPO_ROOT}"/htmlcov/status.json; do
    if [[ -f "$f" ]]; then
      coverage=$(echo "$coverage" | jq --arg src "$f" '.sources += [$src]')
    fi
  done

  if echo "$coverage" | jq -e '.sources | length == 0' >/dev/null 2>&1; then
    coverage=$(echo "$coverage" | jq '.note = "No coverage data found. Teams must configure coverage output."')
  fi

  echo "$coverage" | jq '.' > "$out"
  echo -e "    ${GREEN}Done${NC} → coverage.json"
}

# ─── COLLECT: Stage-1 review verdicts ───────────────────────────────────────

collect_review_verdicts() {
  echo -e "  ${BLUE}Collecting Stage-1 review verdicts...${NC}"
  local out="${EVIDENCE_DIR}/review-verdicts.json"

  local verdicts='{"collected_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","verdicts":[]}'

  # Extract from audit trail: validator-run actions
  if [[ -f "$AUDIT_LOG" ]]; then
    local validator_entries
    validator_entries=$(jq -c "select(.action==\"validator-run\" and .wave==\"$WAVE_ID\")" "$AUDIT_LOG" 2>/dev/null || echo "")
    if [[ -n "$validator_entries" ]]; then
      verdicts=$(echo "$verdicts" | jq --argjson v "$(echo "$validator_entries" | jq -sc '.')" '.verdicts = $v')
    fi
  fi

  # Also check for review agent output files
  for f in "${REPO_ROOT}"/.signalos/reviews/wave-"${WAVE_ID}"*.json; do
    if [[ -f "$f" ]]; then
      verdicts=$(echo "$verdicts" | jq --arg src "$f" '.review_files += [$src]')
    fi
  done

  echo "$verdicts" | jq '.' > "$out"
  echo -e "    ${GREEN}Done${NC} → review-verdicts.json"
}

# ─── COLLECT: Trust Tier declaration ────────────────────────────────────────

collect_trust_tier() {
  echo -e "  ${BLUE}Collecting Trust Tier declaration...${NC}"
  local out="${EVIDENCE_DIR}/trust-tier.json"

  local tier='{"collected_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","wave":"'"$WAVE_ID"'","surfaces":[]}'

  # Look for TRUST_TIER.md in the current Wave
  local trust_file=""
  for f in "${REPO_ROOT}"/core/governance/TRUST_TIER.md \
           "${REPO_ROOT}"/core/governance/trust-tiers/wave-"${WAVE_ID}"-trust.md; do
    if [[ -f "$f" ]]; then
      trust_file="$f"
      break
    fi
  done

  if [[ -n "$trust_file" ]]; then
    tier=$(echo "$tier" | jq --arg src "$trust_file" '.source = $src')
    # Extract surface classifications from markdown table
    while IFS= read -r line; do
      local surface tier_val
      surface=$(echo "$line" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}')
      tier_val=$(echo "$line" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $3); print $3}')
      if [[ -n "$surface" && -n "$tier_val" ]]; then
        tier=$(echo "$tier" | jq --arg s "$surface" --arg t "$tier_val" '.surfaces += [{"surface":$s,"tier":$t}]')
      fi
    done < <(grep -E '^\|[^|]+\|[^|]*T[123]' "$trust_file" 2>/dev/null || true)
  else
    tier=$(echo "$tier" | jq '.note = "No TRUST_TIER.md found for this wave"')
  fi

  echo "$tier" | jq '.' > "$out"
  echo -e "    ${GREEN}Done${NC} → trust-tier.json"
}

# ─── COLLECT: diff stats ───────────────────────────────────────────────────

collect_diff_stats() {
  echo -e "  ${BLUE}Collecting diff stats...${NC}"
  local out="${EVIDENCE_DIR}/diff-stats.json"

  local stats='{"collected_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","wave":"'"$WAVE_ID"'"}'

  if git rev-parse --git-dir >/dev/null 2>&1; then
    # Diff against main for wave branch
    local wave_branch="wave-${WAVE_ID}"
    local base_branch="main"

    # Try to find the wave branch
    if git rev-parse "$wave_branch" >/dev/null 2>&1; then
      local files_changed lines_added lines_removed
      files_changed=$(git diff --stat "$base_branch".."$wave_branch" 2>/dev/null | tail -1 | grep -oP '\d+ file' | grep -oP '\d+' || echo "0")
      lines_added=$(git diff --numstat "$base_branch".."$wave_branch" 2>/dev/null | awk '{s+=$1} END {print s+0}' || echo "0")
      lines_removed=$(git diff --numstat "$base_branch".."$wave_branch" 2>/dev/null | awk '{s+=$2} END {print s+0}' || echo "0")

      stats=$(echo "$stats" | jq --arg fc "$files_changed" --arg la "$lines_added" --arg lr "$lines_removed" \
        '.branch = "'"$wave_branch"'" | .files_changed = ($fc|tonumber) | .lines_added = ($la|tonumber) | .lines_removed = ($lr|tonumber)')

      # Per-file breakdown
      local file_stats
      file_stats=$(git diff --numstat "$base_branch".."$wave_branch" 2>/dev/null | \
        awk '{print "{\"file\":\""$3"\",\"added\":"$1",\"removed\":"$2"}"}' | jq -sc '.' 2>/dev/null || echo "[]")
      stats=$(echo "$stats" | jq --argjson fs "$file_stats" '.files = $fs')
    else
      stats=$(echo "$stats" | jq '.note = "Wave branch not found. Stats from working tree."')
      # Fallback: uncommitted changes
      local wt_changed
      wt_changed=$(git diff --numstat 2>/dev/null | awk '{print "{\"file\":\""$3"\",\"added\":"$1",\"removed\":"$2"}"}' | jq -sc '.' 2>/dev/null || echo "[]")
      stats=$(echo "$stats" | jq --argjson fs "$wt_changed" '.files = $fs')
    fi
  else
    stats=$(echo "$stats" | jq '.note = "Not a git repository"')
  fi

  echo "$stats" | jq '.' > "$out"
  echo -e "    ${GREEN}Done${NC} → diff-stats.json"
}

# ─── COLLECT: exceptions ───────────────────────────────────────────────────

collect_exceptions() {
  echo -e "  ${BLUE}Collecting exception log...${NC}"
  local out="${EVIDENCE_DIR}/exceptions.json"

  local exceptions='{"collected_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","wave":"'"$WAVE_ID"'","exceptions":[]}'

  # Extract from audit trail
  if [[ -f "$AUDIT_LOG" ]]; then
    local exc_entries
    exc_entries=$(jq -c "select(.action==\"exception\" and .wave==\"$WAVE_ID\")" "$AUDIT_LOG" 2>/dev/null || echo "")
    if [[ -n "$exc_entries" ]]; then
      exceptions=$(echo "$exceptions" | jq --argjson e "$(echo "$exc_entries" | jq -sc '.')" '.exceptions = $e')
    fi
  fi

  # Also check exception files directory
  local exc_dir="${REPO_ROOT}/.signalos/exceptions"
  if [[ -d "$exc_dir" ]]; then
    local exc_files
    exc_files=$(find "$exc_dir" -name "EXC-*.md" -type f 2>/dev/null | wc -l || echo 0)
    exceptions=$(echo "$exceptions" | jq --arg c "$exc_files" '.exception_file_count = ($c|tonumber)')
  fi

  echo "$exceptions" | jq '.' > "$out"
  echo -e "    ${GREEN}Done${NC} → exceptions.json"
}

# ─── COLLECT: signal window readings ────────────────────────────────────────

collect_signal_window() {
  echo -e "  ${BLUE}Collecting signal window readings...${NC}"
  local out="${EVIDENCE_DIR}/signal-window.json"

  local signals='{"collected_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","wave":"'"$WAVE_ID"'","readings":[]}'

  # Look for signal log files
  for f in "${REPO_ROOT}"/.signalos/signal-log.md \
           "${REPO_ROOT}"/core/governance/signal-logs/wave-"${WAVE_ID}".md; do
    if [[ -f "$f" ]]; then
      signals=$(echo "$signals" | jq --arg src "$f" '.source = $src')
      # Parse markdown table rows into JSON
      while IFS= read -r line; do
        local ts metric value threshold direction status
        ts=$(echo "$line" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}')
        metric=$(echo "$line" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $3); print $3}')
        value=$(echo "$line" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $4); print $4}')
        status=$(echo "$line" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $7); print $7}')
        if [[ -n "$ts" && -n "$metric" ]]; then
          signals=$(echo "$signals" | jq --arg ts "$ts" --arg m "$metric" --arg v "$value" --arg s "$status" \
            '.readings += [{"ts":$ts,"metric":$m,"value":$v,"status":$s}]')
        fi
      done < <(grep -E '^\| [0-9]{4}' "$f" 2>/dev/null || true)
      break
    fi
  done

  if echo "$signals" | jq -e '.readings | length == 0' >/dev/null 2>&1; then
    signals=$(echo "$signals" | jq '.note = "No signal window data found. Observability agent must run metrics-adapter.sh poll first."')
  fi

  echo "$signals" | jq '.' > "$out"
  echo -e "    ${GREEN}Done${NC} → signal-window.json"
}

# ─── COLLECT: audit trail extract ──────────────────────────────────────────

collect_audit_extract() {
  echo -e "  ${BLUE}Extracting audit trail for Wave $WAVE_ID...${NC}"
  local out="${EVIDENCE_DIR}/audit-extract.jsonl"

  if [[ -f "$AUDIT_LOG" ]]; then
    jq -c "select(.wave==\"$WAVE_ID\")" "$AUDIT_LOG" > "$out" 2>/dev/null || touch "$out"
    local count
    count=$(wc -l < "$out" 2>/dev/null || echo 0)
    echo -e "    ${GREEN}Done${NC} → audit-extract.jsonl ($count entries)"
  else
    touch "$out"
    echo -e "    ${YELLOW}No audit trail found${NC}"
  fi
}

# ─── GENERATE SUMMARY ──────────────────────────────────────────────────────

generate_summary() {
  echo -e "  ${BLUE}Generating SUMMARY.md...${NC}"
  local out="${EVIDENCE_DIR}/SUMMARY.md"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  local test_status="unknown" coverage_status="unknown" tier_status="unknown"
  local exc_count=0 reading_count=0

  # Parse collected data for summary
  if [[ -f "${EVIDENCE_DIR}/test-results.json" ]]; then
    local suites_total suites_failed
    suites_total=$(jq '.suites | length' "${EVIDENCE_DIR}/test-results.json" 2>/dev/null || echo 0)
    suites_failed=$(jq '[.suites[] | select(.failed > 0)] | length' "${EVIDENCE_DIR}/test-results.json" 2>/dev/null || echo 0)
    if [[ "$suites_total" -eq 0 ]]; then
      test_status="no data"
    elif [[ "$suites_failed" -gt 0 ]]; then
      test_status="FAILING ($suites_failed of $suites_total suites)"
    else
      test_status="PASSING ($suites_total suites)"
    fi
  fi

  if [[ -f "${EVIDENCE_DIR}/exceptions.json" ]]; then
    exc_count=$(jq '.exceptions | length' "${EVIDENCE_DIR}/exceptions.json" 2>/dev/null || echo 0)
  fi

  if [[ -f "${EVIDENCE_DIR}/signal-window.json" ]]; then
    reading_count=$(jq '.readings | length' "${EVIDENCE_DIR}/signal-window.json" 2>/dev/null || echo 0)
  fi

  cat > "$out" <<SUMMARYEOF
# QA Evidence Pack — Wave $WAVE_ID

Generated: $ts

---

## Status overview

| Section | Status |
|---|---|
| Test results | $test_status |
| Coverage | $coverage_status |
| Stage-1 verdicts | see review-verdicts.json |
| Trust Tier | $tier_status |
| Exceptions | $exc_count routed |
| Signal Window | $reading_count readings |

---

## Contents

| File | Description |
|---|---|
| test-results.json | Aggregated test suite outcomes |
| coverage.json | Line/branch coverage per module |
| review-verdicts.json | Stage-1 automated review outcomes |
| trust-tier.json | Declared tier and surface classifications |
| diff-stats.json | Files changed, lines added/removed |
| exceptions.json | Exceptions routed during this Wave |
| signal-window.json | Metric readings from Observability |
| audit-extract.jsonl | Filtered audit entries for Wave $WAVE_ID |

---

## Gate 5 checklist

- [ ] All declared test packs green
- [ ] Coverage floor met on touched modules
- [ ] Stage-2 items reviewed or waived with signed reason
- [ ] Signal Window threshold instrumented and flowing
- [ ] No unresolved HALT or BLOCK exceptions
- [ ] Evidence pack reviewed by QA

---

## QA sign-off

Signed (QA): __________  Date: __________

SUMMARYEOF

  echo -e "    ${GREEN}Done${NC} → SUMMARY.md"
}

# ─── GENERATE: main command ────────────────────────────────────────────────

cmd_generate() {
  if [[ -z "$WAVE_ID" ]]; then
    echo "Error: --wave is required for generate" >&2
    exit 1
  fi

  mkdir -p "$EVIDENCE_DIR"
  echo -e "${BLUE}Generating evidence pack for Wave $WAVE_ID${NC}"
  echo -e "  Output: $EVIDENCE_DIR"
  echo ""

  collect_test_results
  collect_coverage
  collect_review_verdicts
  collect_trust_tier
  collect_diff_stats
  collect_exceptions
  collect_signal_window
  collect_audit_extract
  generate_summary

  echo ""
  echo -e "${GREEN}Evidence pack generated: $EVIDENCE_DIR${NC}"

  # Audit the generation
  mkdir -p "$(dirname "$AUDIT_LOG")"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "{\"ts\":\"$ts\",\"actor\":\"qa-evidence-pack\",\"role\":\"system\",\"action\":\"validator-run\",\"wave\":\"$WAVE_ID\",\"detail\":\"evidence pack generated at $EVIDENCE_DIR\"}" >> "$AUDIT_LOG"
}

# ─── VERIFY: check pack completeness ──────────────────────────────────────

cmd_verify() {
  if [[ -z "$WAVE_ID" ]]; then
    echo "Error: --wave is required for verify" >&2
    exit 1
  fi

  if [[ ! -d "$EVIDENCE_DIR" ]]; then
    echo -e "${RED}Evidence pack not found: $EVIDENCE_DIR${NC}"
    echo "Run: qa-evidence-pack.sh generate --wave $WAVE_ID"
    exit 1
  fi

  echo -e "${BLUE}Verifying evidence pack for Wave $WAVE_ID${NC}"
  local required_files=("SUMMARY.md" "test-results.json" "coverage.json" "review-verdicts.json" "trust-tier.json" "diff-stats.json" "exceptions.json" "signal-window.json" "audit-extract.jsonl")
  local missing=0 present=0

  for f in "${required_files[@]}"; do
    if [[ -f "${EVIDENCE_DIR}/$f" ]]; then
      echo -e "  ${GREEN}✓${NC} $f"
      present=$((present + 1))
    else
      echo -e "  ${RED}✗${NC} $f — MISSING"
      missing=$((missing + 1))
    fi
  done

  echo ""

  # Check for data completeness (not just file presence)
  local warnings=0

  # QA Activation Card must exist and be signed
  local qa_card=""
  for f in "${REPO_ROOT}/core/governance/QA_ACTIVATION_CARD.md" \
           "${REPO_ROOT}/core/governance/qa-activation-cards/wave-${WAVE_ID}-card.md"; do
    if [[ -f "$f" ]]; then
      qa_card="$f"
      break
    fi
  done

  if [[ -z "$qa_card" ]]; then
    echo -e "  ${RED}✗ QA Activation Card not found — Gate 5 blocked${NC}"
    echo -e "    Expected: core/governance/QA_ACTIVATION_CARD.md"
    missing=$((missing + 1))
  else
    echo -e "  ${GREEN}✓${NC} QA Activation Card found: $(basename "$qa_card")"
    # Check for signature
    if ! grep -qiE '(Signed|QA[[:space:]]+signature).*:.*\S+' "$qa_card" 2>/dev/null; then
      echo -e "  ${RED}✗ QA Activation Card is unsigned — Gate 5 blocked${NC}"
      missing=$((missing + 1))
    fi
  fi

  # Test results should have at least one suite
  if jq -e '.suites | length == 0' "${EVIDENCE_DIR}/test-results.json" >/dev/null 2>&1; then
    echo -e "  ${YELLOW}⚠ test-results.json has no test suite data${NC}"
    warnings=$((warnings + 1))
  fi

  # Coverage floor check — if QA Activation Card declares a floor, verify it
  if [[ -n "$qa_card" ]]; then
    local declared_floor
    declared_floor=$(grep -oP 'coverage[_ ]floor[:\s]+\K[0-9]+' "$qa_card" 2>/dev/null | head -1 || echo "")
    if [[ -n "$declared_floor" && -f "${EVIDENCE_DIR}/coverage.json" ]]; then
      local actual_pct
      actual_pct=$(jq '.summary.lines.pct // .summary.line_rate // -1' "${EVIDENCE_DIR}/coverage.json" 2>/dev/null || echo "-1")
      if [[ "$actual_pct" != "-1" ]]; then
        if ! command -v bc &>/dev/null; then
          echo -e "  ${YELLOW}⚠ 'bc' not installed — cannot verify coverage floor (${actual_pct}% vs ${declared_floor}%)${NC}"
          warnings=$((warnings + 1))
        else
          local floor_met
          floor_met=$(echo "$actual_pct >= $declared_floor" | bc -l 2>/dev/null || echo "1")
          if [[ "$floor_met" == "0" ]]; then
            echo -e "  ${RED}✗ Coverage ${actual_pct}% below declared floor ${declared_floor}% — Gate 5 blocked${NC}"
            missing=$((missing + 1))
          else
            echo -e "  ${GREEN}✓${NC} Coverage ${actual_pct}% meets floor ${declared_floor}%"
          fi
        fi
      fi
    fi
  fi

  # Signal window: missing data is a blocker if Card declares it required
  if [[ -f "${EVIDENCE_DIR}/signal-window.json" ]]; then
    local reading_count
    reading_count=$(jq '.readings | length' "${EVIDENCE_DIR}/signal-window.json" 2>/dev/null || echo 0)
    if [[ "$reading_count" -eq 0 ]]; then
      # Check if QA Card requires signal window
      if [[ -n "$qa_card" ]] && grep -qi "signal.*window.*required\|signal.*threshold.*instrumented" "$qa_card" 2>/dev/null; then
        echo -e "  ${RED}✗ Signal Window data required by QA Card but no readings found — Gate 5 blocked${NC}"
        missing=$((missing + 1))
      else
        echo -e "  ${YELLOW}⚠ No signal window readings (not required by QA Card)${NC}"
        warnings=$((warnings + 1))
      fi
    fi
  fi

  # Exceptions: check for unresolved HALTs
  local halt_count
  halt_count=$(jq '[.exceptions[] | select(.severity=="HALT")] | length' "${EVIDENCE_DIR}/exceptions.json" 2>/dev/null || echo 0)
  if [[ "$halt_count" -gt 0 ]]; then
    echo -e "  ${RED}✗ $halt_count unresolved HALT exception(s) — Gate 5 cannot proceed${NC}"
    missing=$((missing + 1))
  fi

  echo ""
  if [[ $missing -gt 0 ]]; then
    echo -e "${RED}Pack INCOMPLETE: $missing issue(s). Gate 5 blocked.${NC}"
    exit 2
  elif [[ $warnings -gt 0 ]]; then
    echo -e "${YELLOW}Pack present with $warnings warning(s). QA should review before signing.${NC}"
    exit 0
  else
    echo -e "${GREEN}Pack COMPLETE: $present files verified. Ready for Gate 5 sign-off.${NC}"
    exit 0
  fi
}

# ─── LIST: show all evidence packs ────────────────────────────────────────

cmd_list() {
  local base="${REPO_ROOT}/.signalos/evidence"
  echo -e "${BLUE}Evidence packs${NC}"
  echo ""

  if [[ ! -d "$base" ]]; then
    echo "  No evidence packs found."
    exit 0
  fi

  for d in "$base"/wave-*-evidence-pack; do
    if [[ -d "$d" ]]; then
      local wave_num
      wave_num=$(basename "$d" | sed 's/wave-//' | sed 's/-evidence-pack//')
      local file_count
      file_count=$(find "$d" -type f | wc -l)
      local generated=""
      if [[ -f "$d/SUMMARY.md" ]]; then
        generated=$(grep "^Generated:" "$d/SUMMARY.md" 2>/dev/null | head -1 | sed 's/Generated: //')
      fi
      echo -e "  Wave $wave_num — $file_count files — $generated"
    fi
  done
}

# ─── MAIN ────────────────────────────────────────────────────────────────────

main() {
  parse_args "$@"

  case "$COMMAND" in
    generate) cmd_generate ;;
    verify)   cmd_verify ;;
    list)     cmd_list ;;
    "")       usage 0 ;;
    *)        echo "Error: Unknown command: $COMMAND" >&2; usage 1 ;;
  esac
}

main "$@"
