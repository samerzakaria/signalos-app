#!/usr/bin/env bash
# gate-signature-guard.sh
# Validator — Gate Signature Guard
#
# Purpose:
#   Enforces that Gate artifacts are signed per SIGNATURE_SPEC.md before promotion.
#   Validates: signer name, role, date format, artifact hash, no DRAFT entries.
#
# Triggers:
#   Runs when PR modifies any signed Gate artifact.
#
# Exit codes:
#   0 = all touched Gate artifacts properly signed
#   1 = one or more Gate artifacts unsigned or invalid (fail-closed)
#   2 = warning-only mode (--warn flag)
#
# Amendment history:
#   2026-04-16 — v1.0 locked, initial implementation.
#   2026-04-17 — upgraded to enforce SIGNATURE_SPEC.md (structured YAML block,
#                artifact hash, DRAFT detection, audit trail append).
#   2026-04-28 — W6.3 (AMD-CORE-027): OIDC block validation added.
#                When oidc_sub_hash is present, verifies it is a 64-char hex
#                string AND that oidc_issuer is also present.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
WARN_MODE=false
AUDIT_LOG=""

# Gate artifacts that must be signed
declare -a GATE_ARTIFACTS=(
  "BELIEF.md"
  "BELIEF_LITE.md"
  "EXPECTATION_MAP.md"
  "ACCEPTANCE_CRITERIA.md"
  "PLAN.md"
  "DESIGN_NOTE.md"
  "TRUST_TIER.md"
  "BUILD_EVIDENCE.md"
  "QUALITY_CHECK.md"
  "DEBRIEF.md"
  "CONSTITUTION.md"
)

# Expected signer role per gate artifact (from RACI table)
declare -A EXPECTED_ROLES=(
  ["BELIEF.md"]="PO"
  ["BELIEF_LITE.md"]="PO"
  ["EXPECTATION_MAP.md"]="PO"
  ["ACCEPTANCE_CRITERIA.md"]="PE"
  ["PLAN.md"]="PE"
  ["DESIGN_NOTE.md"]="PO"
  ["TRUST_TIER.md"]="PE"
  ["BUILD_EVIDENCE.md"]="PE"
  ["QUALITY_CHECK.md"]="QA"
  ["DEBRIEF.md"]="PO"
  ["CONSTITUTION.md"]="PO"
)

usage() {
  cat <<EOF
Usage: gate-signature-guard.sh [OPTIONS]

Options:
  --repo-root <path>  Repository root (default: current directory)
  --warn              Warn-only mode (exit 2 on failure, not 1)
  --help              Show this help message

EOF
  exit "${1:-0}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo-root)
        REPO_ROOT="$2"
        shift 2
        ;;
      --warn)
        WARN_MODE=true
        shift
        ;;
      --help)
        usage 0
        ;;
      *)
        echo "Error: Unknown argument: $1" >&2
        usage 1
        ;;
    esac
  done

  AUDIT_LOG="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"
}

is_gate_artifact() {
  local filename
  filename="$(basename "$1")"
  for artifact in "${GATE_ARTIFACTS[@]}"; do
    if [[ "$filename" == "$artifact" ]]; then
      return 0
    fi
  done
  return 1
}

get_expected_role() {
  local filename
  filename="$(basename "$1")"
  echo "${EXPECTED_ROLES[$filename]:-""}"
}

# Compute SHA-256 of content above the "## Signatures" heading
compute_content_hash() {
  local filepath="$1"
  # Extract everything before "## Signatures" line
  sed '/^## Signatures/,$d' "$filepath" | sha256sum | awk '{print $1}'
}

# Validate the structured signature block per SIGNATURE_SPEC.md
validate_signature_block() {
  local filepath="$1"
  local filename
  filename="$(basename "$filepath")"
  local errors=0

  # Check if file exists
  if [[ ! -f "$filepath" ]]; then
    echo "  ✗ File not found: $filepath" >&2
    return 1
  fi

  # Check for ## Signatures section
  if ! grep -q "^## Signatures" "$filepath"; then
    # Fall back to legacy check: "Signed:" or "Signed by:" line
    if grep -qE "^Signed( by)?:" "$filepath" 2>/dev/null; then
      echo "  ⚠ Legacy signature format detected in $filename (migrate to SIGNATURE_SPEC.md format)" >&2
      return 0  # Accept legacy format for backward compatibility
    fi
    echo "  ✗ No ## Signatures section found in $filename" >&2
    return 1
  fi

  # Extract the signature YAML block (everything after ## Signatures)
  local sig_block
  sig_block=$(sed -n '/^## Signatures/,$ p' "$filepath" | tail -n +2)

  # Check for DRAFT entries
  if echo "$sig_block" | grep -qi "DRAFT"; then
    echo "  ✗ DRAFT signature found in $filename — human must replace DRAFT with their name" >&2
    errors=$((errors + 1))
  fi

  # Check for at least one signer: entry with non-empty name
  local signer_count
  signer_count=$(echo "$sig_block" | grep -cE '^\s*-?\s*signer:\s*.+' 2>/dev/null || echo 0)
  if [[ "$signer_count" -eq 0 ]]; then
    echo "  ✗ No signer entry found in $filename" >&2
    errors=$((errors + 1))
  fi

  # Check for role: field
  local role_count
  role_count=$(echo "$sig_block" | grep -cE '^\s*role:\s*(PO|PE|QA|DevOps)' 2>/dev/null || echo 0)
  if [[ "$role_count" -eq 0 ]]; then
    echo "  ✗ No valid role entry found in $filename (must be PO|PE|QA|DevOps)" >&2
    errors=$((errors + 1))
  fi

  # Check expected role matches RACI
  local expected_role
  expected_role=$(get_expected_role "$filepath")
  if [[ -n "$expected_role" ]]; then
    if ! echo "$sig_block" | grep -qE "role:\s*$expected_role"; then
      echo "  ✗ Expected role $expected_role for $filename (RACI table) but not found in signatures" >&2
      errors=$((errors + 1))
    fi
  fi

  # Check for date: field in ISO 8601 format
  if ! echo "$sig_block" | grep -qE '^\s*date:\s*[0-9]{4}-[0-9]{2}-[0-9]{2}'; then
    echo "  ✗ No valid ISO 8601 date found in $filename signatures" >&2
    errors=$((errors + 1))
  fi

  # Check artifact_hash if present (warn if missing, don't fail — migration period)
  if echo "$sig_block" | grep -qE '^\s*artifact_hash:'; then
    local declared_hash
    declared_hash=$(echo "$sig_block" | grep -E '^\s*artifact_hash:' | head -1 | awk '{print $2}')
    local computed_hash
    computed_hash=$(compute_content_hash "$filepath")
    if [[ -n "$declared_hash" && "$declared_hash" != "$computed_hash" ]]; then
      echo "  ✗ artifact_hash mismatch in $filename (declared: ${declared_hash:0:12}... computed: ${computed_hash:0:12}...)" >&2
      echo "    Content may have been modified after signing" >&2
      errors=$((errors + 1))
    fi
  else
    echo "  ⚠ No artifact_hash in $filename signatures (recommended by SIGNATURE_SPEC.md)" >&2
  fi

  # W6.3: OIDC block validation — when oidc_sub_hash is present, enforce
  # a valid 64-char hex digest and a matching oidc_issuer field.
  if echo "$sig_block" | grep -qE '^\s*oidc_sub_hash:'; then
    local oidc_hash
    oidc_hash=$(echo "$sig_block" | grep -E '^\s*oidc_sub_hash:' | head -1 | awk '{print $2}')
    if ! echo "$oidc_hash" | grep -qE '^[a-f0-9]{64}$'; then
      echo "  ✗ oidc_sub_hash in $filename is not a valid SHA-256 hex digest (got: ${oidc_hash:0:16}...)" >&2
      errors=$((errors + 1))
    fi
    if ! echo "$sig_block" | grep -qE '^\s*oidc_issuer:\s*.+'; then
      echo "  ✗ oidc_sub_hash present in $filename but oidc_issuer is missing" >&2
      errors=$((errors + 1))
    fi
  fi

  # Append to audit trail on success
  if [[ $errors -eq 0 ]]; then
    append_audit_entry "$filepath" "$sig_block"
  fi

  return $errors
}

append_audit_entry() {
  local filepath="$1"
  local sig_block="$2"

  # Only append if audit log directory exists
  if [[ -n "$AUDIT_LOG" ]]; then
    mkdir -p "$(dirname "$AUDIT_LOG")"
    local signer role date_val gate verdict
    signer=$(echo "$sig_block" | grep -E '^\s*-?\s*signer:' | head -1 | sed 's/.*signer:\s*//' | xargs)
    role=$(echo "$sig_block" | grep -E '^\s*role:' | head -1 | awk '{print $2}')
    date_val=$(echo "$sig_block" | grep -E '^\s*date:' | head -1 | awk '{print $2}')
    gate=$(echo "$sig_block" | grep -E '^\s*gate:' | head -1 | sed 's/.*gate:\s*//' | xargs)
    verdict=$(echo "$sig_block" | grep -E '^\s*verdict:' | head -1 | awk '{print $2}')
    local hash
    hash=$(compute_content_hash "$filepath")
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    local rel_path="${filepath#$REPO_ROOT/}"

    echo "{\"ts\":\"$ts\",\"actor\":\"$signer\",\"role\":\"$role\",\"action\":\"sign\",\"gate\":\"$gate\",\"artifact\":\"$rel_path\",\"hash\":\"$hash\",\"verdict\":\"$verdict\",\"date\":\"$date_val\"}" >> "$AUDIT_LOG"
  fi
}

main() {
  parse_args "$@"

  # Get list of modified files in the current commit/PR
  local modified_files
  if git rev-parse --git-dir >/dev/null 2>&1; then
    modified_files=$(git diff --cached --name-only 2>/dev/null || git diff --name-only 2>/dev/null || echo "")
  else
    echo "Warning: Not a git repository, skipping validation" >&2
    exit 0
  fi

  local exit_code=0
  local unsigned_count=0
  local checked_count=0

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue

    if is_gate_artifact "$file"; then
      local filepath="${REPO_ROOT}/${file}"
      checked_count=$((checked_count + 1))
      if ! validate_signature_block "$filepath"; then
        unsigned_count=$((unsigned_count + 1))
        exit_code=1
      fi
    fi
  done <<< "$modified_files"

  if [[ $checked_count -eq 0 ]]; then
    echo "✓ Gate Signature Guard: no gate artifacts in diff" >&2
    exit 0
  fi

  if [[ $unsigned_count -gt 0 ]]; then
    echo "✗ Gate Signature Guard: $unsigned_count of $checked_count artifact(s) failed validation" >&2
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  fi

  echo "✓ Gate Signature Guard: $checked_count artifact(s) validated" >&2
  exit 0
}

main "$@"
