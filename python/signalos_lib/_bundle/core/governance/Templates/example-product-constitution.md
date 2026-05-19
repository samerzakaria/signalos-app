<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# SignalOS Constitution

`Version 1.0 — Locked 2026-04-16`

> This is the **supreme law of every Wave** run under SignalOS. It governs how humans and agents collaborate, how trust is assigned, how gates pass, and how the Constitution itself changes. Every agent MUST read and comply before producing any output. Every human MUST sign their named gate before the next phase begins. Violations trigger an immediate protocol reset and a named owner must reconcile.

> **Scope.** This is the **meta-Constitution** — the rules *about the rules*. It governs the delivery process itself. Each product gets its own **product-Constitution** (see `core/governance/Templates/example-product-constitution.md`) which encodes that product's quality bar, tech stack, and security baseline. The meta-Constitution governs how the product-Constitution is written, signed, enforced, and amended.

---

## Preamble — The Four Laws

Every Wave under SignalOS is bound by four laws. These are the minimum bar. A Wave that cannot satisfy all four does not start.

1. **Every Wave carries a signed Belief.** The PO writes what the Wave is meant to prove, the user it serves, and the disproof condition. Unsigned → Wave blocked at Gate 1.
2. **Every agent invocation declares a Trust Tier.** The Plan agent proposes T1 / T2 / T3 per surface; the PO ratifies at Gate 4. Undeclared → **defaults to T3** (PO types the diff).
3. **Every retro produces a Constitution delta.** Either a ratified amendment, or a signed "no change" record. Silence is not permitted.
4. **Every agent has a named human owner.** No agent runs autonomously. Unowned agent output is non-binding and must be re-run.

The Four Laws are enforced as runnable validators (see §7 Enforcement Chain). Violation of any law is a **protocol violation** and blocks the next gate.

---

## §1. The Fail-Hard Default

Every scaling gate in SignalOS **defaults to its strictest form**. Laxity requires an explicit, signed declaration — silence or omission is **not** relaxation.

| Scaling surface | Default if undeclared | Relaxation requires |
|---|---|---|
| Trust Tier per surface | T3 (human types diff) | PE declaration in `core/execution/TRUST_TIER.md` (per-Wave). Tier spec: `executive/Engagement-Model/TRUST_TIERS.md`. |
| Gate 3 Design Approval | Full design brief + client sign | Declared T2/T1 Proceed Tier + PO sign |
| Stage-2 QA review | Full manual review | QA-signed waiver with reason |
| Expectation Map (Gate 2) | PO signature required | No relaxation permitted |
| Belief (Gate 1) | PO signature required | No relaxation permitted |
| Phase-8 Retro | Constitution delta required | No relaxation permitted |

**Rationale.** The single most common failure mode in agentic delivery is *drift via passive skip*: a step is not refused, it is simply forgotten. The fail-hard default makes forgetting visible. A declaration is a record; a silence is not.

---

## §2. Trust Tiers

Trust Tiers are how SignalOS allocates attention. Every **surface** (file, module, endpoint, migration, config) is declared at one of three tiers.

- **T1 — Proceed.** Agent executes without human gating. Suitable for: scaffolding, formatting, docs, non-critical test fixtures.
- **T2 — Propose.** Agent proposes; human reviews before merge. Suitable for: feature code in stable modules, refactors with test coverage, internal APIs.
- **T3 — Suggest.** Agent suggests; human types the diff. Suitable for: migrations, auth, payment, security-sensitive code, irreversible operations, anything touching production data.

### §2.1 Declaration

Trust Tiers are declared in `core/execution/TRUST_TIER.md` at the start of each Wave (template: `core/execution/templates/trust-tier-declaration-template.md`). The Plan agent proposes the declaration; the PO signs at Gate 4 (Trust Tier Declared). *The spec of T1/T2/T3 lives at `executive/Engagement-Model/TRUST_TIERS.md` (plural).*

### §2.2 Default surfaces always T3

Regardless of declaration, the following are **permanently T3** and cannot be relaxed:

- Database migrations (schema, data, and RLS policies)
- Authentication and session handling
- Payment, billing, and financial transactions
- Secret management and key rotation
- Deployment pipeline and infrastructure-as-code
- The Constitution itself and amendments to it

### §2.3 Changing tier mid-Wave

A surface's tier may be raised (toward T3) at any time by any signer. Lowering (toward T1) requires a retro and Constitution delta.

---

## §3. Agent-Output Rules

### §3.1 Ownership

Every agent invocation has exactly one named human owner. The owner is responsible for the agent's output and carries the accountability for any downstream damage.

### §3.2 Traceability

Every agent output must include:

- The ceremony skill(s) it ran under
- The Trust Tier of the surface it touched
- A diff (or proposed diff) — never prose-describing-code
- The agent's own self-review against the product-Constitution

Outputs missing any of the above are non-binding and must be re-run.

### §3.3 No autonomous merges

No agent may merge to a protected branch. Merge is always a human action signed by the PO. This is SoD-critical and cannot be waived.

### §3.4 Inline self-review

Every Build agent output must pass inline self-review (see `core/execution/skills/review/SKILL.md`) before being surfaced to a human. Failed self-review blocks surfacing — the agent re-runs or escalates.

---

## §4. The Six Gates

A Wave passes through six gates in order. No gate may be skipped. Each gate has one named signer, a passing artifact, and a default-hard fallback.

**Gate 0 (prerequisite, not counted in the per-Wave five).** Product-Constitution signed and locked at `core/governance/Governance/CONSTITUTION.md` in the product repo. Signed once at product inception. Every Wave's session-start hook verifies the product-Constitution is present, signed, and hash-consistent — **a missing or tampered product-Constitution blocks Gate 1**.

| # | Gate | Signer | Passing artifact | Default if skipped |
|---|---|---|---|---|
| 1 | Belief signed | PO | `core/strategy/BELIEF.md` — Wave hypothesis, user served, disproof condition, PO signature | Wave blocked |
| 2 | Expectation Map signed | PO | `core/strategy/EXPECTATION_MAP.md` with PO signature | Wave blocked |
| 3 | Design Approval | PO (+ client for T3) | `core/strategy/DESIGN_NOTE.md` at Trust-Tier-appropriate depth | **Defaults to T3 full brief + client sign** |
| 4 | Trust Tier declared | PE | `core/execution/TRUST_TIER.md` (per-Wave) signed by PE, counter-signed by PO | **Defaults to T3 on all surfaces** |
| 5 | Quality Check passed | QA | `core/governance/QUALITY_CHECK.md` with Stage-1 (automated) + Stage-2 (manual) both green | Merge blocked |

### §4.1 Gate sequence is strict

Gates run 1 → 5 within a Wave. A gate may not be attempted before the prior gate is signed. The Orchestrator (session-start hook) enforces the sequence.

### §4.2 Deployment is a separate concern

Deployment sits **outside** the 6-gate sequence. Merge (PE, Gate 5 passed) and deploy (DevOps, post-merge) are segregated by design. DevOps never writes code; PE never presses deploy. This is the SignalOS SoD rule and cannot be waived.

### §4.3 Protocol violati