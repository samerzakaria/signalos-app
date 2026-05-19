<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Audit Trail Specification

`Canonical path: .signalos/AUDIT_TRAIL.jsonl · Append-only · Machine-written`

> Every action, handoff, gate signature, exception, and policy decision in SignalOS is recorded in a single append-only JSONL file. This is the governance-grade evidence bundle that supports human oversight, debugging, and compliance.

---

## File location

```
{repo-root}/.signalos/AUDIT_TRAIL.jsonl
```

Created automatically by the first hook or validator that fires. Never edited manually — append-only.

---

## Entry schema

Every line is a self-contained JSON object:

```json
{
  "ts": "2026-04-16T14:30:00Z",
  "actor": "Jane Smith",
  "role": "PO | PE | QA | DevOps | system",
  "action": "sign | exception | worktree-create | worktree-retire | worktree-reconcile | gate-check | hook-fire | validator-run | deploy | rollback | retro | backlog-harvest | daemon-cycle",
  "gate": "Gate 0 | Gate 1 | ... | Gate 5 | Phase-8",
  "artifact": "relative/path/to/artifact.md",
  "hash": "sha256 of artifact at time of action",
  "verdict": "APPROVED | BLOCK | PASS | FAIL | WARN",
  "wave": "01",
  "type": "optional — exception type for exception actions",
  "severity": "optional — HALT | BLOCK_MERGE | BLOCK_DEPLOY | WARN",
  "surface": "optional — file path or branch for surface-related actions",
  "routed_to": "optional — human role for exception routing",
  "message": "optional — human-readable description",
  "detail": "optional — additional structured data",
  "actor_hmac": "optional — HMAC-SHA256 of actor_identity keyed with install.secret (AMD-CORE-025)",
  "actor_identity": "optional — plaintext 'name|role|session_id' string used to compute actor_hmac"
}
```

Required fields: `ts`, `actor`, `role`, `action`. All others are action-specific.

---

## Actor identity and HMAC (AMD-CORE-025)

When `SIGNALOS_ACTOR_IDENTITY` is set (format: `"name|role|session_id"`), `journal-append.sh` computes an HMAC-SHA256 over the identity string keyed with `.signalos/install.secret` and records two additional fields:

| Field | Description |
|---|---|
| `actor_hmac` | HMAC-SHA256 hex digest — keyed proof that the identity was written on an installation that holds `install.secret` |
| `actor_identity` | Plaintext identity string — enables post-hoc HMAC re-computation for verification |

**Fallback behaviour:** when `install.secret` is absent, `journal-append.sh` falls back to a plain SHA256 of the identity string. Entries written in fallback mode are identifiable because the installation has no `install.secret`; `data-protection-guard.sh` Check 5 warns (but does not block) when the secret is absent.

**Verification procedure:**

```bash
# Recompute HMAC for a journal entry and compare to stored actor_hmac
SECRET=$(cat .signalos/install.secret)
IDENTITY=$(jq -r '.actor_identity' <entry>)
EXPECTED=$(python3 -c "
import hmac, hashlib
key = '${SECRET}'.encode()
msg = '${IDENTITY}'.encode()
print(hmac.new(key, msg, hashlib.sha256).hexdigest())
")
STORED=$(jq -r '.actor_hmac' <entry>)
[ "$EXPECTED" = "$STORED" ] && echo "✓ HMAC valid" || echo "✗ HMAC mismatch"
```

`data-protection-guard.sh` Check 5 automates this over the last 10 entries in each session journal.

---

## Writers

| Component | Actions it logs |
|---|---|
| `gate-signature-guard.sh` | `sign` — every successful gate signature validation |
| `exception-router.sh` | `exception` — every routed exception |
| `worktree-manager.sh` | `worktree-create`, `worktree-retire`, `worktree-reconcile` |
| `session-start` hook | `hook-fire` — session initialization result |
| `pre-commit` hook | `hook-fire` — commit validation result |
| `pre-merge` hook | `hook-fire` — merge precondition result |
| `pre-deploy` hook | `hook-fire` — deploy SoD check result |
| `post-retro` hook | `hook-fire` — retro completion, Constitution hash |
| All 9 validators | `validator-run` — per-PR validator results |
| `backlog-harvester.sh` | `backlog-harvest` — items captured |
| `deliver.sh` (daemon) | `daemon-cycle` — polling, gate pauses, completions |
| `qa-evidence-pack.sh` | `validator-run` — evidence bundle creation |
| `git-integration.sh` | `gate-check` — PR creation, review routing |

---

## Readers

- **Humans:** `jq` queries for debugging, compliance audits, incident investigation
- **Observability agent:** reads trail for Wave Debrief evidence
- **Artifact compiler:** reads trail for rendered audit reports

---

## Retention

The audit trail is committed to git alongside product code. It follows the product's retention policy. Never truncate or rotate without a Constitutional amendment.

---

## Example queries

```bash
# All gate signatures for Wave 03
jq 'select(.action=="sign" and .wave=="03")' .signalos/AUDIT_TRAIL.jsonl

# All exceptions (any severity)
jq 'select(.action=="exception")' .signalos/AUDIT_TRAIL.jsonl

# All HALT-severity exceptions
jq 'select(.severity=="HALT")' .signalos/AUDIT_TRAIL.jsonl

# Full timeline for a specific artifact
jq 'select(.artifact=="core/strategy/BELIEF.md")' .signalos/AUDIT_TRAIL.jsonl
```
