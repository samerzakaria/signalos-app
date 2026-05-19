<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Worktree Handoffs

`Canonical path: core/governance/Worktree-sync/HANDOFFS.md · Append-only · Authored by: any agent at handoff · Format: one HAND block per handoff, newest at top`

> Every time work crosses an agent boundary — Backend → Frontend, PO → PE, Engineer → QA, Wave N close → Wave N+1 open — a Handoff entry is logged here. The Handoff is the **receiving agent's cold-start package**: what's done, what's next, where the artifacts live, what state the branch is in, what the receiver should re-verify before continuing.

---

## Why this file exists

Multi-agent squads fail at handoffs more than at any other boundary. The git log shows what was committed; it does not show what the sender **thought they finished** vs what was actually finished. This log closes that gap — and gives the receiving agent one file to read before picking up.

A missing Handoff entry is a Gate 4 blocker: PE will not open a PR for review if the upstream handoff is undocumented.

---

## How to add a new entry

1. Assign the next HAND number in sequence.
2. Fill every field — ambiguous handoffs are rejected at Gate 4.
3. Prepend (newest at top).
4. Notify the receiving agent via their activation channel (Role Activation Card).

---

## HAND-0001 — Build → Review, metrics-adapter.sh OTLP rewrite

```yaml
date: 2026-04-17-09:30
wave: Wave 01
sender: PE (Build agent)
receiver: QA (Review agent)
branch: wave-01-metrics-adapter
last_commit: a1b2c3d
```

**What is done**

- `core/execution/agents/metrics-adapter.sh` rewritten from 257-line stub to 1039-line production adapter with OTLP-first 3-layer architecture.
- `core/execution/agents/metrics-config.example.yaml` created with two-mode config, precedence rules documented.
- All 5 commands operational: read, push, check, poll, list-backends.
- Mode precedence rules implemented and documented in script header.
- Transport failure policy enforced: audit_write() called before every exit 3.
- `--help` flag handled before command parsing (no "Unknown command" error).
- `list-backends` tested: returns backend list with availability status.

**What is next (the receiver's job)**

- Run all validators against the updated adapter code.
- Verify exit codes: 0=success, 1=error, 2=stale, 3=backend unavailable.
- Confirm AUDIT_TRAIL.jsonl entries are written on every code path.
- Verify metrics-config.example.yaml parses correctly with yq/python.
- Generate evidence pack for Wave 01.

**Where the artifacts live**

| Artifact | Path |
|---|---|
| Belief | `core/strategy/BELIEF.md` |
| Expectation Map | `core/strategy/EXPECTATION_MAP.md` |
| Design Note | `core/execution/DESIGN_NOTE.md` |
| Trust Tier | `core/execution/TRUST_TIER.md` |
| Signal Log (in-progress) | `Governance/signal-logs/wave-01-signal-log.md` |

**What the receiver should re-verify**

- [x] `bash metrics-adapter.sh --help` exits 0, no error output
- [x] `bash metrics-adapter.sh list-backends` exits 0, lists 5 backends
- [ ] `bash metrics-adapter.sh check --config metrics-config.example.yaml` validates config
- [ ] Trust Tier of touched surfaces unchanged since last activation
- [ ] No unsigned artifact in required-signed list

**Known unknowns**

- OTLP push has not been tested against a live OpenTelemetry Collector (no collector in CI yet).
- Direct backend auth tokens are placeholder env vars — real values depend on team config.

---

## HAND-0002 — Review → Release, post-audit fixes applied

```yaml
date: 2026-04-17-14:00
wave: Wave 01
sender: QA (Review agent)
receiver: PE (Release agent)
branch: wave-01-metrics-adapter
last_commit: d4e5f6g
```

**What is done**

- Capability audit v1.0.1 run against actual codebase (honest, 3 parallel auditors).
- constitution-amendment-guard.sh fixed: missing Retro dir now returns 1 (was 0).
- session-hook-dispatch.sh fixed: no-tool-detected path now exits 1 (was fallback file).
- pre-merge hook fixed: PE_MERGE_SIGNER now required (was warn-only), agent identity blocked.
- Aggregate score: 45/50 (90%) — "Agentic and Working" with remediation items in progress.

**What is next (the receiver's job)**

- Verify remaining audit findings are addressed (Cat 7 QA enforcement, Cat 10 recovery gaps).
- Update CAPABILITY_AUDIT_v1.0.1.md with final scores.
- Rebuild release zip.

**Where the artifacts live**

| Artifact | Path |
|---|---|
| Capability Audit | `core/governance/Governance/CAPABILITY_AUDIT_v1.0.1.md` |
| Constitution | `core/governance/Governance/CONSTITUTION.md` |
| Validators | `core/governance/Validators/*.sh` |
| Hooks | `core/execution/hooks/*` |

**What the receiver should re-verify**

- [ ] `bash constitution-amendment-guard.sh --repo-root .` exits 0 in valid repo
- [ ] pre-merge hook blocks when PE_MERGE_SIGNER is unset
- [ ] pre-merge hook blocks when PE_MERGE_SIGNER matches agent pattern
- [ ] No unsigned artifact in required-signed list

**Known unknowns**

- Cat 7 (QA evidence) enforcement gaps still open — QA Activation Card validator not yet built.
- Cat 10 (Recovery) checkpoint field still not populated in deliver.sh.

---

## Template — {sender → receiver, short tag}

```yaml
date: YYYY-MM-DD-HH:MM
wave: Wave {N}
sender: {role}
receiver: {role}
branch: {branch name}
last_commit: {SHA}
```

**What is done**

- {Concrete, verifiable.}

**What is next (the receiver's job)**

- {Concrete, scoped.}

**Where the artifacts live**

| Artifact | Path |
|---|---|
| Belief | `core/strategy/BELIEF.md` |
| Expectation Map | `core/strategy/EXPECTATION_MAP.md` |
| Design Note | `core/execution/DESIGN_NOTE.md` |
| Trust Tier | `core/execution/TRUST_TIER.md` |
| Signal Log (in-progress) | `Governance/signal-logs/wave-{N}-signal-log.md` |

**What the receiver should re-verify**

- [ ] `pnpm test` green on last commit
- [ ] Trust Tier of touched surfaces unchanged since last activation
- [ ] No unsigned artifact in required-signed list

**Known unknowns**

- {…}
