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

