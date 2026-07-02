# Governed Product Lifecycle — E2E Implementation Plan

Status: **proposed.** Forward-looking plan, not shipped behavior. Every item below is
tagged with its **code-verified starting state** as of this plan's authoring, so effort is
honest about *build* (new system) vs *wire* (activate code that already exists but is not
connected) vs *adopt* (source an existing component instead of building).

Conventions: Foundry enforces, never advises (fail-closed). Human-signed Gates stay
human-signed. Waves / Phases / Gates — no "sprint" vocabulary. The end user is a
**non-technical solo founder**; every surface they touch is plain-language, never internal
codes.

---

## 0. End goal

Foundry today governs software **delivery**: it takes an idea that *someone already decided
was worth building* and drives it through a mechanically-enforced engineering pipeline
(G0–G5: Brief → Design → Build → Validate → Security → Launch → Handoff), with a human
signature on every gate.

The end goal of this plan is to extend Foundry into a governed product **lifecycle** — one
continuous, human-signed journey inside a single surface:

> **raw idea → evidence-based decision to build (or not) → governed build → launch → growth loop**

…for a non-technical solo founder, where:

- **evidence precedes spend** — the idea is researched and challenged before build tokens burn;
- **enforcement precedes generation** — the rules are physically unskippable, not requested by prompts;
- **a human signature precedes every consequence** — every decision is a plain-language gate;
- **the path loops and remembers** — shipped products feed telemetry back, and lessons compound.

Foundry becomes the whole company-in-a-box: the part that decides *whether and how* to
build, the part that builds it under enforced discipline, and the part that launches and
grows it — not just the build engine in the middle.

---

## 1. What already exists (code-verified foundation)

Do not rebuild these — they are real, wired, and tested:

- **The G0–G5 governed gate walk** (`gate_orchestrator.py`): runs a specialist agent per
  gate, pauses for a human verdict, signs via `sign.py` on approve, bounded rework(3)/reject(2),
  waive-advances-but-marks-not-ready. **Caveat (dev-review):** this is real and wired, but the
  "fully enforced" claim holds only after item 0.1 lands — today `_default_sign` can advance a
  gate that produced no artifact. Treat the gate walk as trustworthy *conditional on 0.1*.
- **Mechanical enforcement at the tool boundary** (`agent_loop.py` tool policy): an agent
  *physically cannot* write product code during conversation, before G4, before prior gates
  are signed, or without a matching test existing first. This is the real "test-first is
  enforced" guarantee — fail-closed, at the tool-call layer.
- **Append-only audit trail** (`.signalos/AUDIT_TRAIL.jsonl`) + time-travel replay
  (`audit_replay.py`, `signalos replay`).
- **Secrets vault** — OS-keychain-backed, just-in-time, trace-redacted, revocable.
- **Run-state persistence + crash-resume** (`.signalos/agent-runs/<id>/`).
- **~18 Target Platform stack adapters** (`stacks.py`) spanning web / backend / mobile, plus
  a generic fallback.
- **Dormant-but-real primitives** (built + tested, **no callers today**): `competitor.py`,
  `gtm.py`, `observability.py`, `ux_friction.py`, `share_export.py`, `brownfield.py`.

**The dominant defect pattern found across the repo is not "missing capability" — it is
"capability built and tested but never connected."** Wiring is therefore cheaper than
building, and this plan front-loads it.

---

## 2. Guiding principles for this plan

1. **Integrity before expansion.** Fix anything that *looks* enforced but isn't before
   building new surface on top of it — the whole value proposition is "enforced, not advisory."
2. **Wire before build.** Activate dormant, already-tested code before writing new code.
3. **Adopt before build.** Source commodity engines (model routing, retrieval) rather than
   reinventing them; the defensible value is the governed lifecycle, not the plumbing.
4. **Plain-language at every human surface.** The founder is non-technical; a signature on
   jargon is a failed gate.
5. **Every new rule is mechanical.** A rule whose violation is merely improbable rather than
   impossible does not count as enforcement, and must ship with a test that proves the
   violation is *blocked*, not merely that the checker exists.

---

## Wave 0 — Enforcement integrity & honest wiring

**Why:** Several mechanisms *present as* enforcement but don't enforce — a mislabeled toggle
surface, existence-only "wired" checks, schema files nothing loads, an audit ledger nothing
protects, and dormant features that read as "done." For a product whose entire promise is
"mechanically enforced, never advisory," these are the highest-priority fixes: they cost
little and they close the gap between what the product *claims* and what it *does*.

**End state:** every enforcement surface either genuinely enforces or is honestly relabeled;
the audit trail is tamper-evident; and the already-built dormant primitives are activated.

| # | Item | State | Action | Effort |
|---|---|---|---|---|
| 0.1 | Gate can advance with no artifact produced (`_default_sign` signs nothing, raises nothing, advances anyway) | Candidate fail-open | Verify against UI/`wave_engine`; if real, refuse verdict until the gate's artifact exists | S / 1–2d |
| 0.2 | Audit ledger (`AUDIT_TRAIL.jsonl`) is convention-only append-only (a flat file nothing stops from being edited) | Weak | Hash-chain each entry to its predecessor so tampering becomes detectable | S / 2–3d |
| 0.3 | Runtime rule toggles imply control over 12 rules; only 2 are read; UI reads the wrong field and shows "no rules loaded" | Cosmetic (real enforcement is elsewhere, in the tool policy) | Wire the toggles to the real checks or delete the dead ones; fix the UI field so state is truthful | S / ~1wk |
| 0.4 | ~9 `check_*_wired` functions assert a file exists, not that anything calls it | Misleading | Upgrade to verify an actual call site / dispatch entry | S / 3–5d |
| 0.5 | 3 schema files defined but never loaded; hand-written validators duplicate them and can silently drift | Duplicate-risk | Load the schema for real validation, or delete it | S / 2–3d |
| 0.6 | Gate documents are tamper-checked but not content-checked — placeholder boilerplate signs fine | Gap (placeholder detector already exists, unused) | Point the existing placeholder scanner at gate docs before signing | S / 1–2d |
| 0.7 | Activate dormant primitives (`competitor.py`, `gtm.py`, `observability.py`, `ux_friction.py`, `brownfield.py`) into their intended call sites | Built + tested, no callers | Wire each into its pipeline stage; add a behavioral test that proves it runs, not that it exists | M / ~1wk |

**Gate 0:** the enforcement surface is honest — no toggle, schema, "wired" check, or ledger
claims a guarantee it does not deliver; dormant code is active and exercised by a real test.

---

## Wave 1 — Engineering power

**Why:** These upgrade the build engine the founder already relies on today — faster,
safer, cheaper, more honest — independent of everything downstream. They are no-regret:
worth doing whether or not the lifecycle front/back layers are ever built.

**End state:** builds run in governed parallel; spend cannot run away silently; the right
model is used for the right task; reviews are genuinely independent; platform honesty and
plan structure meet a professional bar.

| # | Item | State | Action | Effort |
|---|---|---|---|---|
| 1.1 | Live parallel multi-agent executor (claim / heartbeat / worktree-isolated branches / merge queue) | Missing (design validated, not built) | Build the supervisor/worker executor over a `TaskStore` abstraction | M / 2–3wk |
| 1.2 | Budget hard-stop + 90% auto-pause | Missing (cost is report-only) | Wire a spend check into the admission layer; at 90% warn, at 100% pause all agents via the existing pause/resume state | S / 2–4d (after 1.1's admission layer lands) |
| 1.3 | Model router: task-class → model, with auto-fallback, per-task cost caps, and outcome-adaptive selection | Missing (one model per session) | **Adopt** an existing router (offline capability priors) and feed it an *online* adaptation signal derived from the audit trail's observed outcomes — validated as 2026 best practice (offline-init + online-update routers); no separate performance-tracking system | M / ~1wk |
| 1.4 | Cross-vendor critique routing | Partial (independent second-opinion exists, not vendor-diverse) | Policy: route critique to a different model vendor than the artifact's author when one is configured | S / 3–5d |
| 1.5 | Target-platform maturity tiers (A / B / C) declared before the founder commits | Missing | Label each stack adapter with a maturity tier; surface it at intake | S / 1–2d |
| 1.6 | Plan structure: Feature → Epic → Story hierarchy, release grouping, value score, provenance field, end-to-end traceability view | Partial — core `plan.py` already has typed task fields + dependencies; what's missing is the **founder-facing product-plan layer** (hierarchy, releases, value, provenance, traceability view) | Extend the existing typed plan into the founder-facing product plan — not a schema from zero | M / ~2wk |
| 1.7 | Bidirectional external tracker sync (invisible to the founder) | Missing | New headless integration behind the single-surface principle | M / 1–2wk |
| 1.8 | Plain-words gate briefs: every gate presents exactly four fields — what you are signing / what changes after / the one risk / the question worth asking — authored by a *different* agent (a critic) than the one that produced the artifact, with one-tap escalation to an advisory session | Missing (today's brief is single-field, self-authored) | Build the four-field brief + critic-authoring path across all gates | M / ~1–2wk |
| 1.9 | Completeness-rubric inversion pass: before any gate, a critic runs an inversion check — "what does this artifact silently assume but never provide?" — against a standard omission checklist (identity & accounts, money & billing, permissions & isolation, onboarding & empty states, data lifecycle, operations & failure states). An artifact that assumes what nothing provides is incomplete regardless of how well-structured it is | Missing (0.6 only catches placeholder text; this catches silent omissions) | Build the critic inversion pass + omission checklist, run before every gate | M / ~1wk |
| 1.10 | Failure-state incident cards: every failure surfaces as a plain-words card (what failed, why, cost so far, recovery options), never a stack trace or silent stall — each with a named detection rule and a recovery path. Ship the generic card component + the four cards that apply to today's pipeline: gate deadlock (repeated rejections), integration outage / rate limit, credential revoked/expired, deploy failure/partial release. The remaining two (thin research, low-confidence expert) ship alongside their features in Waves 2 and 5 | Partial (budget-exhaustion path exists via 1.2; the rest are ad-hoc) | Build the incident-card framework + the four present-day cards | M / ~1–1.5wk |
| 1.11 | Founder policy controls in plain language (gate mode strict/standard/fast-lane, research depth, budgets, standards profile, allowed deploy targets) | Partial (rule-mode config exists, not plain-language) | Extend the existing rule-mode config into a plain-language policy surface. **A visual workflow-graph editor is deliberately rejected** — the founder edits *policy*, never the invariant *structure*; handing a non-technical user the power to rewire gates would let them break the governance that is the product's value. (Optional internal item: make the gate order genuinely declarative + invariant-validated instead of hardcoded, so a graph that violates an invariant is refused execution — engine-side, never founder-facing.) | S–M / 3–5d |

**Why 1.8–1.11 are here and not later:** the end user is a non-technical founder who signs
gates *today*. A signature on jargon (1.8), on an artifact with a silent hole (1.9), a
failure that surfaces as a stack trace (1.10), or a policy surface only an engineer can read
(1.11) are all failed gates on the product that already ships. These harden the
human-signature and failure surfaces on today's product, so they earn their place in the
no-regret wave rather than waiting on the lifecycle layers.

**Gate 1:** a real product delivered by the parallel governed fleet, inside budget, with
task-appropriate outcome-adaptive model routing, a structured traceable plan, a plain-words
critic-authored brief on every human signature, a completeness pass that catches silent
omissions, plain-words incident cards on every failure, and a founder-readable policy surface.

---

## Wave 2 — The decision front-door (evidence before spend)

**Why:** This is the missing *front half* of the company. Today Foundry starts at "translate
an idea into engineering requirements" and assumes the "should we build this at all?"
decision was made elsewhere, by hand. This wave adds the pre-build validation lane that
researches the idea, challenges it, and drives it to an evidence-based, human-signed Go /
No-Go — so build tokens only ever burn on a decision the founder actually signed. A No-Go or
a reshape is a *successful* outcome.

This is the largest greenfield tentpole (there is **zero** existing code for the opportunity
lane — the design doc that describes it has no implementation). It gates the rest of the
business layer.

**End state:** the founder can hand Foundry one sentence, get back cited research, a
challenged/reshaped idea, and a signed decision — all before any build begins — and that
signed decision seeds the existing G0 Brief.

| # | Item | State | Action | Effort |
|---|---|---|---|---|
| 2.1 | Idea intake + auto-research **before** asking the founder anything; evidence from a configurable catalog of public sources; every market claim cited or flagged, never asserted | Greenfield | Build intake + a 3-tier evidence resolver | H / 3–4wk |
| 2.2 | Micro-budget cap (~$1) on qualification, separate from the build budget; nothing proceeds until it completes and is signed | Greenfield | Build the capped qualification pass | (part of 2.1) |
| 2.3 | Founder is asked only founder-private questions (intent, unfair advantage, constraints); "I don't know" triggers ranked, evidence-backed candidate answers, never a stall | Greenfield | Build the discovery flow | (part of 2.1) |
| 2.4 | Verdict: Qualified / Qualified-with-reshape / Not-qualified; a reshape is an evidence-cited alternative that outputs an early **Belief-shaped scope draft** — the re-framed *falsifiable hypothesis* with evidence, scope boundaries, and a signal window (not just a yes/no, and **not an Epic** — Epics come later at plan time 1.6; the Belief is what G1 signs and what telemetry resolves in 3.2, closing the loop) | Greenfield | Build the verdict + reshape→Belief artifact | M / ~1wk |
| 2.5 | Commercial analysis stations (business case, competitive edge, pitch) | Partial dormant (`competitor.py` real, unwired) | Wire `competitor.py`; build the remaining stations | M / ~2wk |
| 2.6 | Go/No-Go decision: scores configurable criteria from approved artifacts only, each traceable to source; produces a recommendation; the founder's signed decision is authoritative | Greenfield | Build the scoring room (reuses `sign.py` for the signature) | M / 1–2wk |
| 2.7 | Pre-G0 decision gate that hands the signed Go + evidence + platform tier + scope draft into the existing G0 Brief; the strategy output seeds the G0 governing documents | Buildable (reuses gate/sign infra) | Build the handoff-in bridge | S / ~1wk |

**Gate 2:** a founder completes idea → signed Go/No-Go with cited evidence; a Go flows
directly into the existing engineering lane as a seeded, signed Brief.

---

## Wave 3 — Continuity & the loop-back bridge

**Why:** A lifecycle is not a one-shot pipeline — it loops and it remembers. This wave
connects the build engine's *outputs* back into the decision/growth layer, and makes the
founder's journey continuous across sessions and devices. Without it, the front-door of
Wave 2 and the growth loop of Wave 4 are islands.

**End state:** a shipped product's real-world signals automatically update the hypothesis
that justified building it; closeout is a structured artifact a downstream layer can consume;
in-flight advisory decisions write back into the plan; and a returning founder resumes exactly
where they left off.

| # | Item | State | Action | Effort |
|---|---|---|---|---|
| 3.1 | Structured, machine-readable closeout artifact | **Already exists** — `closeout.py` writes `CLOSEOUT.json` + `.md` with `check_closeout_honesty` | Extend the existing CLOSEOUT.json schema for downstream consumption and **wire consumers** (belief-resolution, portfolio, memory); do not rebuild | S / 2–3d |
| 3.2 | Wire post-launch telemetry to auto-resolve the build's core hypothesis (Keep / Refute / Iterate) | Half-built (`observability.py` already tags the belief id) | Connect the signal stream to the hypothesis state. **Gate (dev-review):** telemetry may only resolve a hypothesis whose **success metrics were signed earlier** — bind resolution to the already-signed Expectation Map / Belief thresholds; never auto-resolve against an unsigned target | S–M / 3–5d (after 0.2) |
| 3.3 | Advisory-session decisions write back into the gate/plan state, not just into side minutes | Missing | Build the write-back path | M / ~1wk (after 0.2) |
| 3.4 | A launch surface (e.g. landing page) re-enters the same enforced G0–G5 loop as a second mini-build | Reuses existing pipeline | Wire the recursive build path | S / 1–2d |
| 3.5 | Growth-generated stories re-enter the *same* product's plan for the next cycle | Missing | Build the re-entry path into the plan | M / 3–5d |
| 3.6 | Identity continuity across the whole journey (one durable record from idea → build → grow), resume across sessions/devices | Partial (local identity + run-resume exist) | Extend identity to span the journey | S–M / 3–5d |

**Gate 3:** a product that ships produces a structured closeout, its hypothesis resolves from
live telemetry, and a returning founder lands back in their in-progress journey.

---

## Wave 4 — Launch & grow

**Why:** The *back half* of the company — turning a built product into a launched, growing
one. Two of the three pieces already have real, tested backends sitting dormant, so this wave
is more wiring than building.

**End state:** a Go'd, built product can be launched (brand, landing, launch kit) and then
grown (telemetry ingested, feedback clustered into themes that propose the next work) —
inside the same governed surface.

| # | Item | State | Action | Effort |
|---|---|---|---|---|
| 4.1 | Launch studio: brand kit (name vs live trademark/domain, logo/palette), landing generation, launch-kit drafts, legal pack (terms/privacy against applicable regimes, flagged for licensed counsel) | Partial dormant (`gtm.py` generates launch copy, unwired) | Wire `gtm.py`; build brand/landing/legal | H / 3–4wk |
| 4.2 | Grow: ingest live product telemetry (signups/activation/revenue/churn/uptime) via an SDK in the generated app + a founder-facing view | Dormant backend (`observability.py` real; no SDK, no view) | Add the emitter SDK + view; wire ingestion | S–M / 1–2wk |
| 4.3 | Cluster feedback into evidence-weighted themes that propose stories / promote parked ideas / convene an advisory session | Greenfield | Build the clustering + proposal logic. **Depends on the Idea Ledger, now pulled forward to Wave 3 (5.1) — dependency resolved** | M / 1–2wk |

**Gate 4:** a product launches through the enforced loop and its live telemetry + feedback
seed the next version's work.

---

## Wave 5 — Memory, learning & portfolio

**Why:** These are the compounding-advantage layers — what makes the *second* product start
smarter than the first. They are the least time-critical (nothing upstream depends on them)
but the highest long-term moat, because they compound with every product the founder runs.

**End state:** every idea is captured and tracked; failures become distilled lessons that
harden into enforced standards; everything is queryable in natural language; and multiple
products can be compared and killed/doubled-down on as signed decisions.

| # | Item | State | Action | Effort |
|---|---|---|---|---|
| 5.1 | Idea ledger: ideas are captured with provenance; lifecycle states (exploring / parked / in-roadmap / rejected); auto-promotes when new evidence arrives | Greenfield | **Sequencing fix (dev-review): pull this forward to Wave 3** — Wave 2 reshape/parking and Wave 4 feedback-clustering (4.3) both depend on it, so it cannot sit after them. Define **capture boundaries, consent, provenance, and deletion** — not "every idea voiced anywhere," which creates privacy/noise problems | M / 1–2wk |
| 5.2 | Advisory board & on-demand expert composition: convene a multi-agent session with current context; compose a freshly-researched, cited expert from a natural-language description; regulated domains carry a licensed-review flag; sessions auto-produce signed minutes | Greenfield | Build the session + composition engine | M–H / 2–3wk |
| 5.3 | Playbook: auto-capture failure signals (repeated red loops, gate rejections, reversed decisions, deploy incidents); distill root-cause lessons; inject them into agent context as knowledge; graduate recurring/high-severity lessons into hardened standards via a signed gate; strict per-founder isolation | Partial (static lesson catalog exists) | Extend the existing catalog with capture + graduation | M / ~2wk |
| 5.4 | Company memory: every artifact/gate/decision/minute/idea/trace queryable in natural language, with cited sources | **Not greenfield** — `brain.py` is a real persistent memory with pure-Python BM25 + optional embeddings upgrade | **Extend Brain** into lifecycle/company memory (index the full artifact/gate/decision set; add citation-returning query); do not build from zero | M / ~1–2wk |
| 5.5 | Portfolio: manage multiple runs with comparable scores/status/economics; kill and double-down are signed gates | Greenfield | Build the cross-run view + decision gates | M / 1–2wk |

**Gate 5:** a founder runs a second product that measurably starts smarter — retrieved
lessons, captured ideas, and portfolio comparison are live.

---

## 3. Sequencing & dependencies

- **Wave 0 is mandatory-first** — never build new surface on enforcement that isn't honest.
- **Waves 0 and 1 are no-regret** and independent of the business layers; they improve the
  product that ships today. With multiple agents working in parallel, ~3 weeks wall-clock,
  bounded by the parallel executor (1.1); nothing else should touch the admission layer while
  it lands, and the budget hard-stop (1.2) tails it.
- **Wave 2 gates Waves 3–5** — the front-door decision must exist before the loop-back,
  launch, grow, and portfolio layers mean anything. Its intake/evidence engine (2.1) is the
  single true greenfield tentpole (~3–4wk) and internally parallelizes (intake, evidence
  resolver, discovery, verdict as separate agents).
- **Waves 3 and 4 depend on a real shipped product** (they consume closeout + telemetry), so
  they follow at least one full Wave-2→engineering→ship cycle.
- **Wave 5 is last** by value-timing, not difficulty — it compounds across products, so it
  needs more than one product to be worth its cost.

Rough order-of-magnitude with multi-agent parallelization: **Waves 0–1 ≈ 3–4 weeks; Waves 2–5 ≈
3 months.** (Wave 1 grew with 1.8–1.11 and the executor's widened scope, so it now trails ~3–4
weeks, not 3.) Estimates on Waves 2–5 carry lower confidence than 0–1 because they are largely
greenfield; the wire/adopt items (2.5, 3.1, 3.2, 4.1-copy, 5.3, 5.4) are the most likely to come
in under estimate because the hard part already exists.

**Estimate assumptions & excluded complexity (dev-review):** these numbers hold *only* under
these boundaries, and are indefensible without them:
- **one experienced builder + multi-agent assist**, not a team; sequential dependencies
  (Wave 2 gates 3–5; a real shipped product gates 3–4) are real and uncompressible by adding agents;
- **excluded from the estimate:** live-data source reliability and rate-limit handling (2.1),
  provider price-config coverage (2.2), per-stack SDK breadth (4.2), trademark/domain/legal
  live-lookup integrations (4.1), and any cloud/multi-tenant/account infrastructure (excluded
  by scope, see §4);
- **greenfield items (2.1, 2.4, 2.6, 5.1, 5.2, 5.5) carry ±50% variance**; wire/adopt items
  (3.1, 5.4, 2.5, 4.1-copy) carry ±20%;
- the numbers are **capacity estimates, not calendar commitments** — Waves are scope
  boundaries, not dates.

## 3a. Revision log — dev-team review (accepted corrections)

A dev-team review challenged 24 points; the disputed code-claims were verified against source
and the review was correct on every factual one. Corrections adopted (rows patched inline
above where marked "dev-review"; the rest recorded here to keep the plan honest without
bloating each row):

- **0.2 (audit hash-chain):** the ledger has multiple appenders (Rust, Python `sign.py`,
  shell). Hash-chaining requires **centralizing the append path or upgrading every writer** —
  scope crosses three languages, not a single-file change.
- **0.3 (enforcement toggles):** wiring toggles to real checks must **not** make a core
  invariant disableable. Toggles may only expose **status** or a **governed, signed override** —
  never an off-switch for hard enforcement (policy layer vs invariant layer).
- **0.5 (schemas):** count is 3, but some are *intentionally* mirrored by hand-written
  validators (`registry.py` says so in its docstring). Reframe as **"load, formally mirror, or
  delete"** — not a blanket "load or delete."
- **0.7 (dormant primitives):** split effort **per primitive** (competitor / gtm /
  observability / ux_friction / brownfield are unrelated surfaces); keep them mandatory in
  Wave 0 but don't compress to a single ~1-week line.
- **1.1 (executor):** name all required components explicitly — **claim, lease, heartbeat,
  retry+backoff, dead-letter, merge-queue, worktree isolation**. Existing parallel
  orchestration is not this; effort widened to **2–4 weeks**.
- **1.2 (budget hard-stop):** must cover **today's provider calls**, not only the future
  executor's admission layer — runaway spend is a present risk.
- **1.5 (maturity tiers):** adapter count is **19** (16 concrete + 3 meta). Rename the maturity
  labels (e.g. **Proven / Supported / Experimental**) to avoid colliding with the A/B/C roadmap
  layers.
- **1.7 (tracker sync): KEPT (founder decision).** Rationale of record: Foundry's plan stays
  the single source of truth; the mirror is **optional, off by default, and one-way-authoritative**
  (Foundry → tracker). It serves founders who (a) already run their work in an external tracker
  and won't double-enter, (b) need to show progress to non-Foundry collaborators / contractors /
  investors in a tool those people already use, or (c) will grow past solo and want zero migration
  later. The single-surface principle holds because **the founder still never opens the tracker** —
  Foundry operates it headlessly.
- **1.8 (critic briefs):** "different agent" is not independence. Require **critic provenance** —
  author agent + model + vendor, reviewer agent + model + vendor, artifact source — and enforce
  vendor separation (ties to 1.4).
- **Wave 2 intro:** "zero existing code" applies to the **opportunity lane only**; commercial
  primitives (competitor / gtm / observability) are reused, not rebuilt.
- **2.1 (evidence resolver):** specify the **source catalog, citation rules, freshness rules,
  and failure behavior** — this is a live-data system; the 3–4-week estimate is optimistic until
  those are pinned.
- **2.2 (micro-budget):** a dollar cap is meaningless when a model's price is unknown
  (`cost.py` never guesses a cost). Require **fail-closed when price is unknown**, or an approved
  price config as a precondition.
- **2.6 (Go/No-Go):** add **"insufficient evidence"** as a distinct *signed* outcome, so weak
  evidence cannot masquerade as a low-confidence Go/No-Go.
- **3.6 (identity):** scope is **local-only continuity** for now; **cross-device identity is
  explicitly deferred** (it implies account/sync/key-management, excluded with cloud/billing).
- **4.1 (launch studio):** split the scope — **GTM copy** (wire `gtm.py`, cheap) vs
  **brand/domain/trademark checks** (live lookups) vs **legal pack** (drafting + counsel flag).
  These are three different sizes, not one.
- **4.2 (grow SDK):** name **which stack adapters** receive the emitter SDK first, and define the
  **event schema + privacy/consent contract** — SDK-across-all-stacks is broader than backend
  wiring.
- **5.2 (expert composition):** a licensed-review flag is not sufficient for regulated domains.
  Add **hard limits: advisory-only output, mandatory citations, and a professional-review
  requirement** where the domain demands it.

## 3b. B-layer confidence & decisions (search-grounded, 2026 practice)

Each B item was checked against how the field actually builds these in 2026, not reasoned from
first principles. Confidence is honest; most of B is commodity/CRUD/extend-existing, and the
genuine engineering differentiators are narrow.

| Item | Confidence | Grounded decision |
|---|---|---|
| **B1** evidence resolver | Med-High build / **Med** on strict bar | Adopt a deep-research pattern (STORM/GPT-Researcher class; citation accuracy tops out ~78–94%). **Build a citation-faithfulness verifier** — the "cited or flagged, never asserted" bar needs it. Source catalog = free structured sources; timestamp every claim, flag stale. |
| **B2** micro-budget | **High** | Enforce mid-session at the **gateway layer** (shared with A-1.2), **fail-closed when price is unknown**. Validated as the only reliable pattern. |
| **B3** private Q's + discovery | Medium | Ship simple in v1: ask + flag "I don't know" + research candidates. **Defer** the elaborate candidate-ranking mechanism. |
| **B4** verdict + reshape | Med-High | Reshape → **Belief-shaped scope draft** (not Epic; see 2.4). Verdict taxonomy (Proceed/Pivot/Kill) is standard. |
| **B5** commercial stations | Med-High | Assemble from prior art (open-source reference: VettIQ); wire dormant `competitor.py` + `gtm.py`. Don't adopt wholesale. |
| **B6** Go/No-Go scoring | **High** | Weighted 1–5×weight rubric (mature framework); add "insufficient evidence" as a signed outcome. |
| **B7** Launch Studio | Medium (**legal, not technical**) | GTM copy autonomous. **Brand/trademark/legal must be advisory-only + counsel-flagged** — AI logos aren't trademarkable, clearance is the founder's legal duty, EU AI Act binding. Legal necessity, not polish. |
| **B8** Grow SDK/telemetry | Med-High | `observability.py` backend exists; **SDK-per-stack breadth is the effort**, not the risk. |
| **B9** Feedback clustering | **High** | Commodity — adopt embedding-based theme clustering + drift detection. |
| **B10** Idea Ledger | **High** | CRUD + lifecycle state machine; define capture boundaries/consent/deletion. |
| **B11** Advisory / expert composition | Medium (**legal, not technical**) | **Advisory-only, cited, mandatory professional review is legally required** (courts sanction unverified AI citations; EU AI Act high-risk). Multi-agent dissent must be genuine. |
| **B12** Playbook | Med-High | Adopt a proven learning pattern (Reflexion / ExpeL / GUARDRAILS.md); **graduate lessons → enforcement via signed gate** is the novel governance layer on top. |
| **B13** Company Memory | **High** | **Extend Brain** (BM25 + embeddings), don't build from zero. |
| **B14** Portfolio | **High** | CRUD + reuse B6's scoring rubric + existing gate infra. |

**Bottom line:** the only genuine *engineering-differentiator* investments in B are **B1 (citation
verifier)** and **B4 (reshape→Belief)**. B7 and B11 are "medium" for **legal/compliance** reasons,
not technical difficulty — they force an advisory-with-mandatory-human-review posture. Everything
else (B2, B5, B6, B8, B9, B10, B12, B13, B14) is adopt, extend, or CRUD — low technical risk.

## 4. What this plan deliberately does not include

- **Billing / paid tiers** — out of scope for now by decision; identity continuity (3.6) is
  included, monetization is not.
- **A portable "take the governance and leave" export** — deliberately excluded; the enforced
  runtime is the value, and it only enforces inside Foundry.
- **Multi-seat / enterprise team features** (RBAC, SSO) — the user is a single non-technical
  founder; the *product they build* may target enterprise, but Foundry itself stays
  single-user here.
