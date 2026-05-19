#!/usr/bin/env bash
# data-protection-guard.sh — Data protection integrity validator
# AMD-CORE-011 · Session-start Check 9
#
# Three checks:
#   1. redact.py sha256 matches AMENDMENTS.md hash anchor (if present)
#   2. No session journal older than SIGNALOS_SESSION_RETENTION_DAYS (default 90)
#   3. PERMANENTLY_T3.md exists with canonical categories
#
# Exit 0 = all checks pass. Exit 1 = failure (--warn: always exit 0).
# --warn:      advisory mode
# --repo-root: override repo root

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
WARN_MODE=false
FAILURES=0

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --warn)       WARN_MODE=true; shift ;;
    --repo-root)  REPO_ROOT="$2"; shift 2 ;;
    --help|-h)    echo "Usage: data-protection-guard.sh [--warn] [--repo-root <path>]"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

_fail() { echo -e "${RED}✗ data-protection-guard: $1${NC}" >&2; FAILURES=$((FAILURES+1)); }
_pass() { echo -e "${GREEN}✓ data-protection-guard: $1${NC}"; }
_warn() { echo -e "${YELLOW}⚠ data-protection-guard: $1${NC}" >&2; }

# ─── Check 1: redact.py sha256 vs AMENDMENTS.md hash anchor ─────────────────
REDACT_PY="${REPO_ROOT}/core/execution/hooks/_lib/redact.py"
AMENDMENTS="${REPO_ROOT}/core/governance/Retro/AMENDMENTS.md"

if [ -f "$REDACT_PY" ] && [ -f "$AMENDMENTS" ]; then
  ACTUAL_HASH=$(sha256sum "$REDACT_PY" 2>/dev/null | awk '{print $1}' || true)
  RECORDED_HASH=$(grep -oE 'redact\.py.*sha256:[a-f0-9]{64}' "$AMENDMENTS" 2>/dev/null | tail -1 | grep -oE '[a-f0-9]{64}' || true)
  if [ -n "$RECORDED_HASH" ]; then
    if [ "$ACTUAL_HASH" = "$RECORDED_HASH" ]; then
      _pass "redact.py hash matches AMENDMENTS.md anchor"
    else
      _fail "redact.py hash mismatch (actual=$ACTUAL_HASH, recorded=$RECORDED_HASH)"
    fi
  else
    _warn "no redact.py sha256 anchor in AMENDMENTS.md — skipping hash check"
  fi
else
  [ -f "$REDACT_PY" ] || _warn "redact.py not found at $REDACT_PY"
fi

# ─── Check 2: No journal older than retention threshold ──────────────────────
RETENTION_DAYS="${SIGNALOS_SESSION_RETENTION_DAYS:-90}"
SESSIONS_DIR="${REPO_ROOT}/.signalos/sessions"
if [ -d "$SESSIONS_DIR" ]; then
  OLD_COUNT=$(find "$SESSIONS_DIR" -maxdepth 2 -name "journal.jsonl" \
    -mtime +"$RETENTION_DAYS" 2>/dev/null | wc -l | tr -d ' ')
  if [ "${OLD_COUNT:-0}" -gt 0 ]; then
    _fail "$OLD_COUNT journal(s) older than ${RETENTION_DAYS}d. Run: signalos session archive --auto"
  else
    _pass "no journals older than ${RETENTION_DAYS}d"
  fi
fi

# ─── Check 3: PERMANENTLY_T3.md exists with canonical categories ─────────────
T3_FILE="${REPO_ROOT}/core/PERMANENTLY_T3.md"
if [ -f "$T3_FILE" ]; then
  if grep -qi "canonical\|permanent\|t3\|trust.tier" "$T3_FILE" 2>/dev/null; then
    _pass "PERMANENTLY_T3.md exists with content"
  else
    _fail "PERMANENTLY_T3.md exists but appears empty or lacks canonical categories"
  fi
else
  _fail "PERMANENTLY_T3.md missing at core/PERMANENTLY_T3.md"
fi

# ─── Check 4: AUDIT_TRAIL.jsonl hash-chain integrity (AMD-CORE-013 B3) ──────
check_audit_chain() {
  local trail="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"
  if [[ ! -f "$trail" ]]; then
    echo -e "  ${YELLOW}⚠ AUDIT_TRAIL.jsonl not found — skipping chain check${NC}"
    return 0
  fi
  local prev_hash=""
  local line_num=0
  local chain_ok=true
  while IFS= read -r line; do
    line_num=$((line_num + 1))
    [[ -z "$line" ]] && continue
    # From row 2 onwards, verify prev_hash matches sha256 of previous row
    if [[ $line_num -gt 1 && -n "$prev_hash" ]]; then
      local declared_prev
      declared_prev="$(echo "$line" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('prev_hash',''))" 2>/dev/null || echo "")"
      if [[ -n "$declared_prev" && "$declared_prev" != "$prev_hash" ]]; then
        echo -e "${RED}✗ AUDIT_TRAIL.jsonl chain broken at line ${line_num}${NC}"
        echo "  Expected prev_hash: $prev_hash"
        echo "  Declared prev_hash: $declared_prev"
        FAILURES=$((FAILURES + 1))
        chain_ok=false
        break
      fi
    fi
    prev_hash="$(printf '%s' "$line" | sha256sum | awk '{print $1}')"
  done < "$trail"
  if [[ "$chain_ok" == "true" ]]; then
    echo -e "${GREEN}  ✓ Check 4: AUDIT_TRAIL.jsonl hash-chain intact (${line_num} rows)${NC}"
  fi
}
check_audit_chain

# ─── Check 5: actor_hmac consistency on recent journal entries (AMD-CORE-025) ─
SECRET_FILE="${REPO_ROOT}/.signalos/install.secret"
if [[ -f "$SECRET_FILE" ]] && [[ -r "$SECRET_FILE" ]]; then
  HMAC_FAIL=0
  HMAC_CHECK=0
  if [ -d "$SESSIONS_DIR" ]; then
    while IFS= read -r entry_file; do
      [[ -f "$entry_file" ]] || continue
      while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        actor_hmac="$(echo "$line" | jq -r '.actor_hmac // empty' 2>/dev/null || true)"
        actor_identity="$(echo "$line" | jq -r '.actor_identity // empty' 2>/dev/null || true)"
        [[ -z "$actor_hmac" || -z "$actor_identity" ]] && continue
        expected="$(python3 -c "
import hmac, hashlib, sys
key = open(sys.argv[1]).read().strip().encode()
msg = sys.argv[2].encode()
print(hmac.new(key, msg, hashlib.sha256).hexdigest())
" "$SECRET_FILE" "$actor_identity" 2>/dev/null || true)"
        HMAC_CHECK=$((HMAC_CHECK + 1))
        if [[ "$expected" != "$actor_hmac" ]]; then
          HMAC_FAIL=$((HMAC_FAIL + 1))
          _fail "actor_hmac mismatch for identity '${actor_identity}' in ${entry_file}"
        fi
      done < <(tail -10 "$entry_file" 2>/dev/null)
    done < <(find "$SESSIONS_DIR" -maxdepth 2 -name "journal.jsonl" 2>/dev/null | head -10)
  fi
  if [[ $HMAC_FAIL -eq 0 && $HMAC_CHECK -gt 0 ]]; then
    _pass "actor_hmac consistent on ${HMAC_CHECK} sampled journal entr(ies)"
  elif [[ $HMAC_CHECK -eq 0 ]]; then
    _warn "no actor_hmac entries found to verify — skipping HMAC consistency check"
  fi
else
  _warn "install.secret absent — Check 5 (HMAC consistency) skipped"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
if [ "$FAILURES" -gt 0 ]; then
  if [ "$WARN_MODE" = true ]; then
    _warn "$FAILURES check(s) failed (advisory mode — not blocking)"
    exit 0
  else
    exit 1
  fi
fi
exit 0
