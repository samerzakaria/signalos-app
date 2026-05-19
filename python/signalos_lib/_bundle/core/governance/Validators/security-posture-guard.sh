#!/usr/bin/env bash
# security-posture-guard.sh — Security surfaces declaration validator
# AMD-CORE-011 · Session-start Check 8 · pre-deploy Rule 5
#
# Checks that the product Constitution declares a security_surfaces block.
# SECURITY_POSTURE_UNDECLARED blocks session start and deployment.
#
# Exit 0 = posture declared. Exit 1 = undeclared (or --warn: advisory).
# --warn:      always exit 0; report as warning
# --repo-root: override repo root

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
WARN_MODE=false

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --warn)       WARN_MODE=true; shift ;;
    --repo-root)  REPO_ROOT="$2"; shift 2 ;;
    --help|-h)    echo "Usage: security-posture-guard.sh [--warn] [--repo-root <path>]"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Look for security_surfaces declaration in Constitution / governance docs
SOUL_DOC="${REPO_ROOT}/core/governance/Governance/SOUL-DOCUMENT.md"
CONSTITUTION="${REPO_ROOT}/core/governance/Governance/CONSTITUTION.md"
AMENDMENTS="${REPO_ROOT}/core/governance/Retro/AMENDMENTS.md"

FOUND=false
for doc in "$SOUL_DOC" "$CONSTITUTION" "$AMENDMENTS"; do
  if [ -f "$doc" ] && grep -qi "security_surfaces\|security-surfaces\|security surfaces" "$doc" 2>/dev/null; then
    FOUND=true
    break
  fi
done

if [ "$FOUND" = true ]; then
  echo -e "${GREEN}✓ security-posture-guard: security_surfaces declared.${NC}"
  exit 0
fi

MSG="SECURITY_POSTURE_UNDECLARED: no security_surfaces block found in Constitution."
if [ "$WARN_MODE" = true ]; then
  echo -e "${YELLOW}⚠ security-posture-guard (--warn): $MSG${NC}" >&2
  exit 0
else
  echo -e "${RED}✗ $MSG${NC}" >&2
  echo "  Declare 'security_surfaces:' in SOUL-DOCUMENT.md or CONSTITUTION.md." >&2
  exit 1
fi
