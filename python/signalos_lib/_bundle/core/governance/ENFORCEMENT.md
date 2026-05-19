<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Enforcement

`Canonical path: core/governance/ENFORCEMENT.md · Authored by: PE + DevOps · Signed at: Gate 0 (product-wide) · Cadence: amended when layers are added/removed`

> The Constitution states the Four Laws. The Gates state when signatures are required. The **Enforcement Chain** states *how the signatures are mechanically verified* — the difference between a process that is aspirational and one that is auditable. SignalOS v1.0 enforces through **five layers**, each independent, each failing closed.

---

## The 5-layer chain

```
                 +---------------------------+
 Layer 1 human   | PO / PE / QA / DevOps     |   typed signatures on artifacts
                 +-------------+-------------+
                               |
                 +-------------v-------------+
 Layer 2 agent   | Role Activation Cards     |   refuses to activate w/o signed prereqs
                 +-------------+-------------+
                               |
                 +-------------v-------------+
 Layer 3 CI      | Validators in /Validators |   static checks on repo state + diffs
                 +-------------+-------------+
                               |
                 +-------------v-------------+
 Layer 4 runtime | Signal Window guardrails  |   metric thresholds + SLO breach triggers
                 +-------------+-------------+
                               |
                 +-------------v-------------+
 Layer 5 retro   | Wave Debrief + Incident   |   post-hoc pattern detection + amendments
                 +---------------------------+
```

**Fail-closed by default (Constitution §1).** Any layer that cannot conclusively pass a check MUST block.

---

## Layer 1 — Human signatures

The signed artifact is the authoritative record. Layers 2-5 reference these signatures; they do not replace them.

| Gate | Artifact | Signers |
|---|---|---|
| Gate 0 | `Governance/SOUL-DOCUMENT.md` | PO |
| Gate 1 | `core/strategy/BELIEF.md` (or BELIEF_LITE) | PO |
| Gate 2 | `core/strategy/EXPECTATION_MAP.md` | PO + Client |
| Gate 3 | `core/execution/DESIGN_NOTE.md` | PO + PE |
| Gate 4 | `core/execution/TRUST_TIER.md` | PE + PO |
| Gate 5 | `core/execution/SIGNAL_LOG.md` verdict + `core/execution/WAVE_DEBRIEF.md` | PO + QA |

---

## Layer 2 — Role Activation Cards

Each agent checks prerequisites before executing any command:

- Does the required signed artifact exist at the expected path?
- Is the signer's name present in the signature block?
- Is the Gate for my task already open?

If any answer is **no**, the agent refuses to activate and emits a blocker message naming the missing artifact. Agents do **not** author missing artifacts on a human's behalf.

---

## Layer 3 — CI Validators

Validators live in `core/governance/Validators/` and run on every PR:

- `gate-signature-guard` — rejects PRs where a Gate's signature line is empty on an artifact the PR touches
- `trust-tier-guard` — rejects PRs where a permanently-T3 surface appears in the diff but the Trust Tier Declaration says otherwise
- `tier-sheet-guard` (daemon mode) — cross-references PR surfaces against `PRODUCT_TIER_SHEET.md`
- `artifact-shape-guard` — rejects artifacts that deviate from their canonical template shape (missing required sections)
- `path-consistency-guard` — rejects if a command file references a path that does not exist in the distro
- `expectation-redline-guard` — rejects if the Expectation Map "Redlines surfaced" section is empty AND the Wave is Wave-scale (a frictionless Expectation Map is a red flag, per Constitution §5)

Validators are authored against SignalOS v1.0 and versioned alongside the Constitution.

---

## Layer 4 — Signal Window runtime guardrails

Once shipped, the Wave's Signal Window (`Governance/signal-logs/wave-{N}-signal-log.md`) runs live metric checks:

- Metric threshold breach triggers early verdict review.
- Operational SLO breach (error rate, latency, cost) triggers PE review — may Kill before the Window closes.
- QA's hourly readings are timestamped; a gap of > 4 h on an active Window triggers a Layer-2 agent alert.

---

## Layer 5 — Retro & incidents

- Every Wave Debrief asks: *"did any Layer 1-4 check fail or get bypassed?"*
- Every Incident file (`Governance/incidents/`) names the layer that should have caught the issue and, if none applies, queues a Constitution amendment.
- Quarterly (daemon mode), PE aggregates which validators fired, which layers caught the most misses, and which never fired — a never-firing validator is either perfect or dead weight.

---

## Bypass handling

The Constitution (§1 fail-hard) prohibits silent bypass. Any explicit override (e.g. emergency hotfix during outage) requires:

1. PE + PO signed bypass memo in `Governance/incidents/` before merge
2. Full post-incident review within 72 h
3. Constitution amendment proposal if the override revealed a structural gap

---

## Amendment history

| Date | What changed | Signers |
|---|---|---|
| 2026-04-16 | Initial 5-layer Enforcement chain, locked with SignalOS v1.0 | PE + DevOps + PO |
