#!/usr/bin/env bash
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — pre-session-compress guard hook.
#
# Purpose: enforce the disk-truth invariant from TRUST_TIER.md and the
# Babysitter briefing §3-Core — "Disk audit trail is NEVER compressed;
# only the in-context projection changes." This hook refuses to run if
# any caller tries to pass disk-truth files (session journals, metrics
# streams, AUDIT_TRAIL) as compression inputs.
#
# The actual compression engine is Agent C / W1.3 territory; this guard
# lives in front of it.
#
# Arg contract (caller passes each file to check as a positional arg):
#   pre-session-compress.sh <path> [<path>...]
#
# Forbidden patterns (repo-root-relative or absolute):
#   - .signalos/sessions/*/journal.jsonl
#   - .signalos/sessions/*/metrics.jsonl
#   - .signalos/AUDIT_TRAIL.jsonl
#
# Exit codes:
#   0 — none of the inputs are disk-truth; compression may proceed
#   1 — a disk-truth file was passed; compression REFUSED

set -euo pipefail

if [[ $# -eq 0 ]]; then
  # No inputs = nothing to guard against. Don't fail the pipeline.
  exit 0
fi

# Resolve repo root so relative paths are compared against the same base.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# Build canonical forbidden prefixes and filenames.
SESSIONS_DIR="${REPO_ROOT}/.signalos/sessions"
AUDIT_TRAIL="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"

fail=0
for raw in "$@"; do
  # Normalize: absolutize relative paths against REPO_ROOT.
  if [[ "$raw" = /* ]]; then
    p="$raw"
  else
    p="${REPO_ROOT}/${raw}"
  fi

  # Canonicalize without requiring the file to exist (Python fallback when
  # readlink -f is unavailable; BSD readlink doesn't support -f).
  canon="$(python3 -c 'import os,sys; print(os.path.normpath(sys.argv[1]))' "$p")"

  # Rule A: AUDIT_TRAIL.jsonl exact match (the single-file audit log).
  if [[ "$canon" == "$AUDIT_TRAIL" ]]; then
    echo "pre-session-compress.sh: REFUSED — AUDIT_TRAIL.jsonl is disk-truth and must never be compressed" >&2
    echo "  path: $canon" >&2
    fail=1
    continue
  fi

  # Rule B: any per-session journal.jsonl or metrics.jsonl inside sessions/.
  # Pattern: <repo>/.signalos/sessions/<session-id>/{journal,metrics}.jsonl
  if [[ "$canon" == "${SESSIONS_DIR}/"*"/journal.jsonl" ]]; then
    echo "pre-session-compress.sh: REFUSED — per-session journal.jsonl is disk-truth and must never be compressed" >&2
    echo "  path: $canon" >&2
    fail=1
    continue
  fi
  if [[ "$canon" == "${SESSIONS_DIR}/"*"/metrics.jsonl" ]]; then
    echo "pre-session-compress.sh: REFUSED — per-session metrics.jsonl is disk-truth and must never be compressed" >&2
    echo "  path: $canon" >&2
    fail=1
    continue
  fi
done

exit "$fail"
