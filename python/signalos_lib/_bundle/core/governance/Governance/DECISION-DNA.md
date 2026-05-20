<!-- SignalOS v1.0 — Locked 2026-04-16 -->
<!-- SEED FILE: copy to your product repo at Governance/DECISION-DNA.md. Append-only log of every decision + its reasoning. Template: core/governance/Templates/decision-dna-template.md -->

# Decision DNA Log — {Product Name}

`Canonical path: Governance/DECISION-DNA.md · Append-only · Authored by: PO (PE co-signs technical DECs) · Format: one DEC block per decision, newest at top`

> Every decision the product has made, with its **why**. Append-only. No entry is ever edited or deleted — only superseded by a later DEC that references it. A superseded DEC remains in the log with `Status: Superseded by DEC-{NNNN}`.

---

## How to add a new entry

1. Copy the template block (`core/governance/Templates/decision-dna-template.md`).
2. Assign the next DEC number in sequence.
3. Fill every field — an entry with empty Rationale or Consequences is rejected at PR review.
4. Prepend (newest at top).
5. If this DEC supersedes an earlier one, add a `supersedes: DEC-{NNNN}` line in the new entry's front-matter AND edit the old entry's Status line to `Superseded by DEC-{NNNN}` (this is the only permitted edit to an existing DEC).

---

## DEC-0001 — Adopt SignalOS v1.0

```yaml
date: 2026-04-16
status: Active
author: {PO name}
co_signer: {PE name}
scope: process / governance
supersedes: none
```

**Context**

The product needs a locked operating system for AI-assisted product delivery — gates, artifacts, trust tiers, enforcement — so that a multi-agent squad can ship software without the team having to re-discover process every Wave.

**Decision**

Adopt SignalOS v1.0 as the canonical operating system for this product: Constitution, 6 Gates per Wave (Gate 0 through Gate 5), Trust Tiers, Scale Tracks, Delivery Modes, and the 10-agent roster.

**Rationale**

- Prior ad-hoc process produced drift between client expectation and built reality.
- SignalOS enforces a signed Expectation Map before code — turning silent misalignment into a surfaced, resolvable artifact.
- The 5-layer Enforcement Chain makes the gates auditable, not just aspirational.

**Consequences**

- Gate 0 must be completed before Wave 01 opens.
- Agents activate via Role Activation Cards; ad-hoc prompting is out.
- All future process decisions go through the Constitution amendment path (§13).

**Verification**

- Gate 0 signed Soul Document exists before first Wave.
- Constitution v1.0 present at `Governance/CONSTITUTION.md` with PO signature.

---

## DEC-0002 — {title}

*(Populate as decisions are made. Follow the template. Newest at top.)*

---

---

## AMD-CORE-033 · Deploy JSONL vs. Database

**Date:** 2026-05-01  
**Wave:** W12  
**Author:** PO (signalos-core)  
**Decision:** SignalOS post-deploy lifecycle uses append-only JSONL files for deploy records and benchmarks.

**Why JSONL:**
1. Consistent with the existing Brain, security, and checkpoint stores — no new storage primitive
2. Human-readable and greppable without tooling
3. `land_deploy` rewrites the matching record status in-place (full-file rewrite) — acceptable at Wave scale (< 100 deploy records per product)

**Why not SQLite or PostgreSQL:**
- Adds a runtime dep or a server requirement
- Not justified at < 1,000 records
- JSONL → SQLite migration path is trivial when needed

**Trigger conditions for store migration:**
1. Deploy index exceeds 5,000 records AND query latency > 200 ms
2. Multi-product deploy federation required

**Signed:** signalos-core · 2026-05-01

---

## AMD-CORE-034 · retro_global No Brain Import

**Date:** 2026-05-01  
**Wave:** W13  
**Author:** PO (signalos-core)  
**Decision:** `retro_global()` in `devex.py` reads `.signalos/brain/index.jsonl` directly without importing `signalos_lib.brain`.

**Why direct JSONL read:**
1. Avoids a circular import risk if brain ever imports devex
2. `devex.py` stays stdlib-only — no dependency on the optional `anthropic` SDK that brain's embeddings upgrade pulls in
3. The BM25 scoring in `brain.py` is not needed for retro_global (simple keyword scan suffices)

**Trade-off accepted:** If the brain JSONL schema changes, retro_global must be updated separately. This is acceptable because schema changes to brain JSONL are versioned and documented in DECISION-DNA.

**Signed:** signalos-core · 2026-05-01

---

## AMD-CORE-035 · Directory Freeze MD5 Hash Key

**Date:** 2026-05-01  
**Wave:** W14  
**Author:** PO (signalos-core)  
**Decision:** Directory freeze records are keyed by `hashlib.md5(target.encode()).hexdigest()[:8]` — an 8-char MD5 prefix of the target path string.

**Why MD5 prefix:**
1. Provides a stable, collision-resistant filename for the freeze JSON (`freeze/<hash>.json`)
2. MD5 is not used for security (freeze is not a crypto primitive) — only for path-to-filename mapping
3. 8 hex chars gives 4 billion possible values — sufficient for a single product's directory set
4. stdlib-only: `hashlib.md5` requires no additional import

**Why not SHA256:** Overkill for a filename key; MD5 is faster and the 8-char truncation makes filenames readable in `ls` output.

**Collision policy:** If two directories produce the same 8-char prefix (probability ~1 in 4B), the second freeze fails with a descriptive error. Acceptable at product scale.

**Signed:** signalos-core · 2026-05-01

## Superseded / archived

*Kept here for grep-ability when newer DECs reference them. Never removed.*

*(None yet.)*

---

## AMD-CORE-030 · Brain v2 Migration Path

**Date:** 2026-05-01  
**Wave:** W9  
**Author:** PO (signalos-core)  
**Decision:** SignalOS Brain v1 uses pure-Python BM25 + JSONL storage. v2 migration to PGLite (SQLite-in-WASM) is deferred.

**Trigger conditions for v2:**
1. Brain index exceeds 10,000 entries AND query latency > 500 ms on a standard dev machine
2. Multi-product brain federation required (cross-product-id queries)
3. Real-time streaming search needed (e.g., live session injection < 100 ms)

**v2 architecture on record:**
- PGLite (SQLite via WASM) as embedded store — no server, no separate process
- `brain upgrade --pglite` command migrates JSONL → PGLite in-place
- `brain search` prefers PGLite index; falls back to BM25 JSONL if PGLite absent
- `brain export` remains JSONL-based (portable, human-readable)
- v1 JSONL index is retained as cold backup after migration

**Why deferred:** v1 BM25 covers all current use cases with zero runtime deps. PGLite adds a WASM binary (~3 MB) and breaks the stdlib-only invariant. Not justified at < 10k entries.

**Signed:** signalos-core · 2026-05-01


---

### 2026-05-01 — AMD-CORE-037 doctrine wiring closure (W16)

**Wave:** W16  
**Artifact:** `cli/signalos_lib/preamble.py`, `integrations/rules/signal-discovery.mdc`, `integrations/rules/signal-pre-wave.mdc` (framework-self-wave), `integrations/rules/signal-init.mdc` (step 5), `integrations/rules/signal-onboard.mdc` (greenfield clause removed), `core/execution/hooks/session-start`  
**Author:** PO + PE  

**Decision:** Close five validated runtime-wiring gaps from the W15-end audit as a single doctrine-coherence wave. Choose **doctrine + thin code** over more framework features — the framework's promise to fire correctly end-to-end has higher leverage than any next surface.

**Rationale:** The reframe in the audit ("existence is not the right test — does the chain fire correctly at runtime?") exposed five places where files existed and were referenced, but the system still broke when an adopter actually ran it: `{{...}}` literals reached agents unsubstituted (C1); greenfield deadlocked between signal-onboard and signal-init (C4); the stakeholder-interview skill had no entry point so its prerequisite was unsatisfiable (H1); register-hooks.sh was a silent manual step responsible for the most common "installed but inert" deployment (H3); Core's own waves silently skipped Gate 2 with no doctrine to acknowledge the case (M2). Each fires on every adopter session or every Core-self wave; closure had to come before any next surface.

---

## AMD-CORE-110 · Enforcement universality

**Date:** 2026-05-20
**Wave:** W17 (signalos-app M1)
**Author:** PO (signalos-core)
**Decision:** Every output channel SignalOS produces — chat replies, file writes, subprocess execution, gate transitions, design artifacts, deploy actions — must pass through the same enforcement framework (gates + validators + hooks) as user-written code. Enforcement is not opt-in per channel; it IS the framework.

**Why:**
1. If SignalOS produces any output through any path that bypasses its own gates, validators, or hooks, SignalOS is not an operating system — it is a CLI wrapper.
2. An OS enforces its kernel guarantees on every syscall. Same standard applies here: the protocol is the kernel; the gates are the syscall boundary.
3. Without this principle, "unused = unfinished" (AMD-CORE-100) becomes incoherent — orphan checks only catch "code with no caller," not "output channel that bypasses the framework," which is the larger failure mode.

**Operational consequence:**
- Buttons that look like commands are an anti-pattern; they advertise an à la carte model the framework doesn't offer.
- Per-skill validators (`skill_validators.py`) must extend to cover all output channels, not just file writes.
- The `pre-tool-use-guard.sh` must run on every orchestrator file write — currently a gap closed in this same wave.

**Verification:**
- Anti-regression CI (`test_no_dead_code.py`) gates: every output-producing code path is mapped to a validator or gate.
- Audit trail records every system-initiated trigger so reviewers can confirm enforcement fired.

**Trade-off accepted:** Some flexibility is lost — a developer cannot quickly add a button that bypasses gates. This is the point.

**Signed:** signalos-core · 2026-05-20

---

## AMD-CORE-111 · Enforcement ≠ always block (override-with-audit extension)

**Date:** 2026-05-20
**Wave:** W17 (signalos-app M1)
**Author:** PO (signalos-core)
**Decision:** Enforcement is "block by default + allow override-with-logged-violation for authorized roles." The audit trail is the integrity layer; the gate is the recommendation.

**Role policy:**
- **Solo-owner** (proven sole stakeholder of the product): can skip gate signs; each skip is recorded as a `violation:gate-skip` entry in `AUDIT_TRAIL.jsonl`.
- **Operator**: can override per-gate via the Toolbar Override button; logged as `override:gate-override`.
- **Service-account / CI**: override requires valid OIDC token + matching role policy; logged as `override:oidc`.
- **Anonymous / unauthenticated**: cannot skip or override; gate sign is required.

**Semantic distinction:**
- **Override** = "I am authorized to make this call" (logged as override).
- **Violation** = "I am skipping a protection I'm supposed to honor, and accept that this is recorded as such" (logged as violation).

Both leave audit-trail evidence; the difference matters for downstream review. Override evidence is normal operational record; violation evidence stacks up and signals process drift.

**Why:**
1. Pure "always block" is unworkable for solo owners — they cannot peer-review themselves.
2. Pure "allow anything" defeats the protocol — there is no integrity.
3. Block-with-recorded-override-or-violation preserves both productivity and auditability.

**Implication for validators:** every `_validate_*` skill validator that returns a violation must emit an audit-trail entry naming the violation kind, the role at the time, and the artifact context.

**Verification:**
- Role test: a solo-owner who skips G3 sign produces an `AUDIT_TRAIL.jsonl` entry with `action="violation:gate-skip"`, `role="solo-owner"`, and `gate="G3"`.
- An anonymous user who attempts to skip the same gate is refused with no audit entry (because no skip occurred).

**Trade-off accepted:** A user who silently accumulates violations may produce shippable software that no peer ever reviewed. The audit trail makes this visible; the policy is that visibility is sufficient — the framework does not refuse to ship on N violations. That's a future tightening if needed.

**Signed:** signalos-core · 2026-05-20

