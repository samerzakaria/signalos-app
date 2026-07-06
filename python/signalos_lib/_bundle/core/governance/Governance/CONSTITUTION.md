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
| Trust Tier per surface | T3 (human types diff) | PE declaration in `core/execution/TRUST_TIER.md` (per-Wave). Tier spec: `executive/Engagement-Model/TRUST_TIERS.md` (plural — the definition of T1/T2/T3). |
| Gate 3 Design Approval | Full design brief + client sign | Declared T2/T1 Trust Tier + PO sign |
| Stage-2 QA review | Full manual review | QA-signed waiver with reason |
| Expectation Map (Gate 2) | PO signature required | No relaxation permitted |
| Belief (Gate 1) | PO signature required | No relaxation permitted |
| Phase-8 Retro | Constitution delta required | No relaxation permitted |
| **Scale Track** (per Wave) | **Wave** (full 6-gate ceremony) | **PO declaration in Belief front-matter** (see §11) |
| **Delivery Mode** (per product) | **fresh-wave** (full per-Wave ceremony) | **PO + PE declaration in product-Constitution + stability criteria** (see §12) |

**Rationale.** The single most common failure mode in agentic delivery is *drift via passive skip*: a step is not refused, it is simply forgotten. The fail-hard default makes forgetting visible. A declaration is a record; a silence is not.

---

## §2. Trust Tiers

Trust Tiers are how SignalOS allocates attention. Every **surface** (file, module, endpoint, migration, config) is declared at one of three tiers.

- **T1 — Proceed.** Agent executes without human gating. Suitable for: scaffolding, formatting, docs, non-critical test fixtures.
- **T2 — Propose.** Agent proposes; human reviews before merge. Suitable for: feature code in stable modules, refactors with test coverage, internal APIs.
- **T3 — Suggest.** Agent suggests; human types the diff. Suitable for: migrations, auth, payment, security-sensitive code, irreversible operations, anything touching production data.

### §2.1 Declaration

Trust Tiers are declared in `core/execution/TRUST_TIER.md` (the per-Wave declaration file; template: `core/execution/templates/trust-tier-declaration-template.md`) at the start of each Wave. The Plan agent proposes the declaration; the PO signs at Gate 4 (Trust Tier Declared). *The spec of what T1/T2/T3 mean lives at `executive/Engagement-Model/TRUST_TIERS.md` (plural) — do not confuse the two.*

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
| 3 | Design Approval | PO (+ client for T3) + PE | `core/strategy/DESIGN_NOTE.md`; `core/execution/PLAN.md`; `core/execution/ACCEPTANCE_CRITERIA.md` | **Defaults to T3 full brief + client sign; no plan or acceptance criteria, no Gate 3 pass** |
| 4 | Trust Tier + Build Evidence | PE | `core/execution/TRUST_TIER.md` (per-Wave) signed by PE, counter-signed by PO; `core/execution/BUILD_EVIDENCE.md` signed by PE after build/test verification | **Defaults to T3 on all surfaces; no build evidence, no Gate 4 pass** |
| 5 | Quality Check passed | QA | `core/governance/QUALITY_CHECK.md` with Stage-1 (automated) + Stage-2 (manual) both green | Merge blocked |

### §4.1 Gate sequence is strict

Gates run 1 → 5 within a Wave. A gate may not be attempted before the prior gate is signed. The Orchestrator (session-start hook) enforces the sequence.

> *Note on the Orchestrator.* "Orchestrator" is a **hat worn by the Product Owner**, not a separate seat or agent. When the session-start hook runs its sequence checks, the PO is the human owner of those checks; the hook is the code they delegate the enforcement to. There is no separate Orchestrator role in the 4-human squad (see `docs/Team-Charters/HUMAN_TEAM_CHARTER.md`).

### §4.2 Deployment is a separate concern

Deployment sits **outside** the 6-gate sequence. Merge (PE, Gate 5 passed) and deploy (DevOps, post-merge) are segregated by design. DevOps never writes code; PE never presses deploy. This is the SignalOS SoD rule and cannot be waived.

### §4.3 Protocol violation

Any attempt to skip or backfill a gate is a **protocol violation**. The session-start hook aborts the next phase with:

> `PROTOCOL VIOLATION: Gate [N] ([Name]) not signed. Wave [ID] blocked. Named owner [Role] must reconcile.`

Reconciliation requires either signing the gate or formally cancelling the Wave (retro + Constitution delta).

---

## §5. Phase-Gate Rules *(adapted from Agency §5)*

The six gates above are reinforced by these execution-level rules. Every rule is enforced by a validator in the Constitution-as-code library.

1. **Explicit approval is mandatory.** Every gate requires an explicit human signature, typed into the gate artifact. "Approved in Slack" is not a signature.
2. **No phase skipping.** The Orchestrator enforces gate order. A skip attempt triggers a protocol violation.
3. **Artifact-at-path.** Every phase produces an artifact at a specific path. If the artifact is not at the path, the phase is not complete.
4. **Handoff message.** Each phase ends with a handoff note in `Worktree-sync/HANDOFFS.md` naming the next phase's owner and the artifact path.
5. **Worktree-Sync is automatic.** After every gate signature, the Worktree-Sync agent runs without human invocation. This is the mechanism by which parallel Waves stay coherent. *(Renamed from Agency's Worktree-Sync. The mechanism is the same; the pattern is preserved.)*
6. **Agent invocation is structured.** All agent invocations use the SignalOS command protocol (see `core/execution/commands/`). Free-form natural-language agent instructions are a protocol violation.
7. **Code only via Build.** No human or agent writes production code outside a Build agent invocation. This preserves traceability. (The PE types T3 diffs, but does so *through* a Build invocation with tier=T3.)
8. **Phase-8 is mandatory (Closure Gate).** After every production ship, run reconcile → retro → retrospective-analyze → Constitution-delta. No exceptions. A Wave is not closed until Phase-8 completes. *(Phase-8 is the Closure Gate — the formal end-of-Wave ceremony. It subsumes Signal's original "Closure Gate" concept. The post-retro hook enforces that Phase-8 cannot be skipped: no Constitution delta → next Wave blocked at Gate 1.)*

### §5.1 Wave branching

Every Wave has its own `wave-[NN]-[short-name]` branch. Created at Wave start, merged via PR at Wave close. No direct commits to `main`.

### §5.2 Worktree discipline

Parallel Waves run in separate git worktrees. Each worktree has its own PE (per `executive/Engagement-Model/ENGAGEMENT_MODEL.md`). The Worktree-Sync agent is the sole cross-worktree communicator.

---

## §6. Amendment Mechanism

The Constitution changes only through retros. There is no other path.

### §6.1 Every retro produces a delta

Phase-8 of every Wave produces a Constitution delta. The delta is either:

- A **ratified amendment** — a concrete change to this document, signed by PO + PE, recorded in `core/governance/Retro/AMENDMENTS.md`.
- A **signed "no change" record** — an explicit note that this Wave surfaced no constitutional lesson, signed by PO + PE.

Silence is not permitted. A retro without a delta blocks the next Wave's Gate 1.

### §6.2 Delta scope

A delta may:

- Add, remove, or modify a rule in §1–§5 or §7
- Add, remove, or modify a gate
- Add, remove, or modify a Trust Tier surface classification
- Update the example product-Constitution template

A delta may **not**:

- Remove any of the Four Laws (preamble)
- Remove the fail-hard default (§1)
- Remove the permanently-T3 surfaces (§2.2)
- Waive SoD between merge and deploy (§4.2)

These are the immutable core. Changing them requires a new Constitution version, not a delta.

### §6.3 Retro ownership

The PO owns the retro. The PE, QA, and DevOps each contribute a signed section. The Observability agent contributes a post-deploy metrics section. The PO drafts the delta; PO + PE sign it.

---

## §7. Enforcement Chain

SignalOS enforces the Constitution through five layers. Each layer catches what the layer above it missed. No single layer is sufficient.

**Layer 1 — Constitution-as-code.** The rules in this document are encoded as runnable validators in `core/governance/Validators/`. Each rule has a validator; each validator is referenced from at least one hook.

**Layer 2 — Hooks.** Git hooks invoke validators at the right moment:

- `pre-commit` — surface Trust Tier check, self-review freshness, protocol-violation scan
- `pre-merge` — Gate 5 signature check, Worktree-Sync completion
- `pre-deploy` — SoD check (merge signer ≠ deploy runner)
- `post-retro` — Constitution delta presence check
- `session-start` — gate-sequence validity, unowned-agent check

**Layer 3 — Ceremony skills inside phase agents.** Each phase agent runs the ceremony skills that produce the phase's gate artifact. Belief-writing runs inside Brainstorm; Trust-Tier-declaration runs inside Plan; Bet-Score and Kickoff run at Wave-start; Expectation-Map runs inside Plan; Retro-as-amendment runs inside Phase-8.

**Layer 4 — Independent audit agents.** Review (Stage-1), Security (scan), and Observability (post-deploy) run independently of the phase agents. They report to the PO and QA, not to the phase agent they audit.

**Layer 5 — Human gate signatures.** The final enforcement is human. PO signs Belief and Expectation Map. PE declares Trust Tier and signs the merge. QA signs Stage-2. DevOps signs the deploy. No signature → no passage.

### §7.1 The chain is ordered

Earlier layers catch cheaper failures faster. By the time a breach reaches Layer 5, it has survived four automated checks — the signer's job is judgment, not error-catching.

### §7.2 Validator canon

The full list of validators lives at `core/governance/ENFORCEMENT.md`. That document is the definitive map of which hook runs which validator, which skill belongs to which agent, and which human signs which gate. If `ENFORCEMENT.md` and this Constitution disagree, **this Constitution wins** and the map is corrected at the next retro.

---

## §8. Roles and SoD

`Updated 2026-05-01 (AMD-CORE-038): one-man show model — sole human seat is PO.`

SignalOS operates on a **one-man show model**: one human seat (PO). All execution is agent-driven. SoD in one-man show mode is a known concession: the PO merges and deploys as one person. This concession is recorded in the product-Constitution at onboarding.

| Role | Gates owned | Cannot delegate |
|---|---|---|
| **PO** (sole human) | All gates (G0–G5) | Gate signatures; T3 diff authoring |

The full charter lives at `docs/Team-Charters/HUMAN_TEAM_CHARTER.md`. The agent roster lives at `docs/Team-Charters/AGENTIC_TEAM_CHARTER.md` — **ten agent sub-roles**: two Discovery (Onboarding, Brainstorm) and eight Delivery (Plan, Build, Test, Review, Worktree-Sync, Security, Release, Observability). Every agent sub-role is owned by the PO; unowned agent output is non-binding (Law 4, Preamble).

---

## §9. Relation to product-Constitutions

Each product under SignalOS maintains its own product-Constitution in its product repo. The product-Constitution encodes:

- Quality bar (coverage, linting, complexity)
- Tech stack rules
- Security baseline
- Architecture standards
- DevOps standards
- Documentation standards

See `core/governance/Templates/example-product-constitution.md` for a reference instance.

**This meta-Constitution governs** *how* the product-Constitution is written, signed at Gate 1, enforced by validators, and amended at Phase-8. It does **not** prescribe the product-Constitution's content — that is the product's choice.

---

## §10. Preservation

### §10.1 Canonical location

This file at `core/governance/Governance/CONSTITUTION.md` is the canonical SignalOS meta-Constitution. All other copies (decks, handouts, derived outputs) are representations. If a derived output disagrees with this file, this file wins.

### §10.2 Versioning

Major version bumps (1.0 → 2.0) are reserved for changes to the immutable core (§6.2). Minor version bumps (1.0 → 1.1) track ratified deltas. The version and lock date on line 3 of this document are updated at every amendment.

### §10.3 Tamper-evidence

The `post-retro` hook computes a hash of this document after every amendment and records it in `core/governance/Retro/AMENDMENTS.md`. Any out-of-band edit to this file without a corresponding amendment record is a protocol violation and blocks the next Gate 1.

---

## §11. Scale Tracks

SignalOS supports three Scale Tracks per Wave: **Quick**, **Wave** (default), and **Campaign**. The Scale Track is a per-Wave declaration that scales the *form* each gate takes without waiving any gate.

### §11.1 Declaration

Scale Track is declared by the PO at the top of the Belief artifact as:

```
scale_track: quick | wave | campaign
```

Undeclared → **Wave** (fail-hard default). See §1.

### §11.2 Ceiling rules

- **Quick** — Trust Tier ceiling is **T2**. Permanently-T3 surfaces (§2.2) **cannot** be touched in a Quick track. If a Quick Wave discovers a permanently-T3 surface, the Wave halts and re-declares as **Wave** with a full Trust Tier declaration.
- **Wave** — All Trust Tiers permitted. Full 6-gate ceremony.
- **Campaign** — All Trust Tiers permitted. Requires a co-signed `CAMPAIGN_CONSTITUTION.md` (PO + PE) at `core/governance/Governance/CAMPAIGN_CONSTITUTION.md` for the Campaign's duration.

### §11.3 Ceremony form matrix

Each gate takes a different form per track. The full matrix lives in `core/execution/SCALE_TRACKS.md` and is authoritative. Silence or ambiguity in `SCALE_TRACKS.md` resolves to the strictest form (Wave).

### §11.4 Scale Track is not gate-waiving

Declaring Quick does not skip any gate — it scales the form. Every gate still has a signed artifact; a Belief-lite, a PR-embedded acceptance line, and a QA-signed Stage-2 waiver are still artifacts.

---

## §12. Delivery Modes

SignalOS supports two product-level Delivery Modes: **fresh-wave** (default) and **daemon**. A third, **onboarding**, is a one-time pre-Wave state every product passes through exactly once before either mode takes effect.

### §12.1 Declaration

Delivery Mode is declared at the top of the product-Constitution:

```
delivery_mode: fresh-wave | daemon
```

Undeclared → **fresh-wave** (fail-hard default). See §1.

**Onboarding as pre-Wave state.** Before a product-Constitution exists to declare a Delivery Mode, the product runs `/signal-onboard` once (Team Charter §1 — Onboarding agent). Onboarding emits the first Soul Document, product-Constitution draft, Surface Inventory, permanently-T3 list, and seed Belief. After Gate 0 closes on the Soul Document and the product-Constitution is signed, the product is ready for `/signal-init` and enters `fresh-wave` (or may declare `daemon` later, per §12.2). Onboarding is not re-runnable except on material restructure (acquisition, monolith split) and only by PO decision logged in `governance/DECISION-DNA.md`.

### §12.2 Daemon-mode entry criteria

A product may declare daemon mode only when **all** of:

1. At least 3 consecutive Waves produced "Keep" on the same Belief with no material amendments.
2. The T3 surface list has been stable for 3 Waves.
3. The backlog is queue-like (refined items with acceptance lines, not exploratory bets).
4. PO + PE commit to a quarterly product retro (90-day cadence).
5. Client signs off if the engagement model requires it.

Full spec: `core/execution/DELIVERY_MODES.md`.

### §12.3 Daemon-mode gate redistribution

In daemon mode, gates redistribute between **product-level** (signed once, re-affirmed quarterly) and **item-level** (signed per item drawn from queue). Gate 1 and Gate 2 become product-level; Gate 3, Gate 4, Gate 5 stay per-item. The quarterly product retro is mandatory.

### §12.4 Daemon-mode exit

A product **must** exit daemon mode when any of:

1. A Campaign-scale track is declared.
2. The product Belief amends materially at a quarterly retro.
3. The T3 surface list expands.
4. A production incident's root cause is "ceremony was performed but judgment was not."
5. The client opts out.
6. The quarterly retro is overdue (forces automatic exit).

### §12.5 Scale Track × Delivery Mode

Scale Track and Delivery Mode are orthogonal, with one constraint: **Campaign is not allowed in daemon mode**. A Campaign declaration forces exit to fresh-wave for the Campaign's duration.

---

**Signed at lock:**

- PO: _________________________ Date: 2026-04-16

*This section is the product-Constitution signature block pattern. The SignalOS meta-Constitution itself is signed at release by the SignalOS authors.*

---

## §12b. Security Surfaces

security_surfaces:
  - webview (Tauri WebView2 — user-facing UI, CSP-governed)
  - ipc (Tauri IPC bridge — all Rust commands)
  - sidecar (Python SignalOS Core — LLM harness, file writes)
  - keychain (OS credential storage — API keys)
  - filesystem (workspace reads/writes — sandbox-bounded)
  - network (LLM API calls — redacted before transmission)

---

## §13. Glossary

Operational vocabulary introduced or clarified by amendments. Entries are additive and do not override the preceding sections; when a section and a glossary entry disagree, the section wins. New entries land in Wave-scoped amendments and are cross-referenced from `core/governance/Retro/AMENDMENTS.md`.

**headless harness** — The execution path that runs a PLAN step without an attached editor. Introduced in W1.2 by AMD-CORE-004 as the 8th tool-adapter emitter. The harness shells directly into the Anthropic Messages API via the pinned `anthropic>=0.39,<1.0` Python SDK and emits the same four W1.1 journal events (`step.started`, `step.completed`, `step.failed`, `pre-session-compress`) an editor emitter would — byte-identical shape. The harness is an operational lever, **not a Gate**; gate count remains five plus Gate 0 (§4). T3 surfaces (§2) refuse the harness exactly as they refuse every other emitter — the `pause: true` gate in the step-spec is honoured, and T3 hard-stops are non-negotiable. Canonical command doc: `core/execution/commands/harness-call.md`. Canonical skill doc: `core/execution/skills/headless-execution/SKILL.md`.

**8th emitter** — Shorthand for the harness tool-adapter emitter that sits alongside the seven editor emitters (claude-code, cursor, codex, vs-code, windsurf, github-copilot, antigravity). The 8th emitter's source lives at `core/tool-adapters/emitters/harness/emit.sh`. It accepts the same `--commands-json` / `--skills-json` / `--hooks-json` / `--preamble` / `--output-dir` contract as the editor emitters and writes a self-contained `.signalos/harness/` tree the CLI harness reads at run time. The dispatcher flag `--headless` on `core/tool-adapters/dispatcher/session-hook-dispatch.sh` forces emitter selection to the 8th without changing `detect_tool`; it sets `SIGNALOS_TOOL=harness` and reuses the pre-existing tool-override path.

**harness:call** — The operator verb for invoking a single headless step. Implemented as the `signalos harness call` CLI subcommand (plus `signalos harness status` and `signalos harness abort`). Mutually-exclusive `--prompt` / `--prompt-file` carry the step body; `--model` defaults to `claude-sonnet-4-5`; `--session-id` attaches to an existing session or, if omitted, creates a `harness-session-YYYYMMDDTHHMMSSZ-<hex6>` session. Exit codes: `0` completed, `1` user error (no `step.failed` event), `2` execution error (`step.failed` emitted once the session/step resolve), `3` reserved for the future T3 policy-refusal path. `SIGNALOS_HARNESS_TEST=1` short-circuits the Messages API to a deterministic canned response so proof scenarios can exercise the event-emission path without an API key. A harness call that leaves no journal or metrics trail is a bug, not a feature.

**rule-based compression** — The W1.3 context-window compressor at `cli/signalos_lib/context.py`, introduced by AMD-CORE-005 and invoked via `signalos context compress <input>`. "Rule-based" means *no LLM call and no new runtime dependency*: compression is a fixed pipeline of regex + turn-index + blob-size heuristics applied against a session transcript. Four layers run over the transcript tail-first: VERBATIM preserves the last two turns in full, SUMMARY caps the next eight turns at 400 characters each with a deterministic sentence-selector, HEADLINE collapses anything beyond that to a ≤120-character topic line, and DISCARD drops redacted payloads and individual blobs of 8 KB or more. The decision vector is recorded alongside the compressed transcript so a downstream reader can recover provenance per-turn. The compressor is an operational lever, **not a Gate**; it does not change the gate count in §4, and it is never invoked implicitly by Core — a human or a plugin must run `signalos context compress` explicitly. Canonical skill doc: `core/execution/skills/compress-context/SKILL.md`. Canonical command doc: `core/execution/commands/context-expand.md`.

**disk-truth unchanged invariant** — The T3 rule governing compression: the *in-context projection* of a session may change, but the *on-disk record* never does. Formally: no code path inside `cli/signalos_lib/context.py`, `core/execution/hooks/pre-session-compress/pre-session-compress.sh`, or any plugin that advertises a `compress-context` capability may open for write, rename, or unlink any of `.signalos/sessions/*/journal.jsonl`, `.signalos/sessions/*/metrics.jsonl`, or `.signalos/AUDIT_TRAIL.jsonl`. The hook refuses with exit code 1 if any of these paths is passed as a compression input; the library raises `DiskTruthRefused`. The invariant is enforced twice — once at the hook boundary, once at the library boundary — on purpose: a plugin that side-steps the hook still hits the library check. Enforcement is proved by `proof/scenarios/38_compression_disk_truth_refused.sh`. This invariant is a strict extension of AMD-CORE-001's append-only journal rule; it does not override any existing §F clause.

**plugin registry** — The W1.3 first-party plugin distribution surface at `cli/signalos_lib/registry.py`, introduced by AMD-CORE-006. Operator verbs: `signalos install <tarball.tgz>`, `signalos verify <tarball.tgz>`, `signalos list`, `signalos uninstall <name>`, `signalos publish <dir> --out <tarball.tgz> --key <cosign-ref>`. Plugins install under `core/registry/plugins/<sanitized-name>/<version>/` and register a row in `core/registry/INSTALLED.jsonl`. The manifest contract is `core/registry/_schema/plugin-manifest.schema.json` (JSON Schema draft-07); `plugin.name` must match `^(@signalos/[a-z0-9-]+|community/[a-z0-9-]+)$` — the `@signalos/*` namespace is reserved for the core team, `community/*` is open, everything else is refused at install time. The registry is an operational surface, **not a Gate**; installing a plugin does not change the gate count in §4, and a plugin cannot lower the Trust Tier of any surface outside its own `core/registry/plugins/...` subtree. Canonical skill doc: `core/execution/skills/plugin-registry/SKILL.md`. Canonical layout doc: `core/registry/README.md`.

**cosign-signed tarball** — The signature contract for a SignalOS plugin, introduced by AMD-CORE-006. A release candidate `.tgz` is signed with `cosign sign-blob` by the publisher's key, and the resulting `.sig` travels alongside the tarball. The manifest's `signature` block records `{ "algo": "cosign", "ref": "sha256:<hex>" }`; `signalos install` re-derives the tarball SHA-256 at install time and refuses the install if the recorded `ref` does not match. `signalos verify` runs the same check without writing to the registry. `SIGNALOS_REGISTRY_TEST=1` short-circuits the external cosign binary to a deterministic mock (the signature file must contain the literal `MOCK-COSIGN-SIG`) so proof scenarios can exercise the install and publish paths without a cosign toolchain. An install with no valid signature is refused at exit code 3 unless the operator explicitly passes `--allow-unsigned`, in which case the resulting `AUDIT_TRAIL.jsonl` row carries `unsigned: true` alongside the standard T3 record.

**T3-by-default for plugins** — The Trust Tier rule governing the plugin registry: **every plugin surface installed via `signalos install` is recorded as T3 in the AUDIT_TRAIL regardless of the manifest's declared `trust_tier_default`**. The manifest may advertise a lower tier as a hint to the product-Constitution author ("this plugin's developers consider their surfaces safe at T1"), but Core itself always writes the AUDIT row with `trust_tier: "T3"`. A product-Constitution author who wants to relax a specific plugin surface to T1 or T2 must do so by an explicit §F declaration in the product-Constitution with PO + PE co-sign — the plugin's own manifest cannot shortcut that declaration. Enforcement is proved by `proof/scenarios/43_registry_t3_default.sh`, which installs a manifest that declares `trust_tier_default: "T1"` and asserts the AUDIT row records `"trust_tier": "T3"`. This invariant is a strict extension of §2's fail-hard default; it does not override any existing §2 clause.

**LLM provider abstraction** — The W2.1 extensibility surface at `cli/signalos_lib/harness.py`, introduced by AMD-CORE-007. The `LLMProvider` Protocol defines a single `call(prompt, model) -> (text, tokens_in, tokens_out)` method that every concrete backend must implement. Five built-in providers ship with Core: `AnthropicProvider` (default, wraps the `anthropic` SDK), `OpenAIProvider` (lazy-imports `openai`), `GeminiProvider` (lazy-imports `google.generativeai`), `OllamaProvider` (stdlib `urllib.request`, calls the local Ollama server), `TestProvider` (canned deterministic response, no network). Provider selection: `SIGNALOS_HARNESS_TEST=1` always selects `TestProvider`; otherwise `SIGNALOS_LLM_PROVIDER` env var or the `--provider` CLI flag selects the backend (default `anthropic`). This abstraction is an operational lever, **not a Gate**; it does not affect the gate count in §4 and does not change the journal or metrics contract. All T3 surfaces, pause semantics, and hook firing are provider-agnostic. `anthropic` remains the sole *required* runtime Python dep; all other providers are opt-in and raise a `RuntimeError` with an install hint if their backing package is missing.

**parallel wave orchestrator** — The W2.1 concurrent execution surface at `cli/signalos_lib/orchestrator.py`, introduced by AMD-CORE-008 and invoked via `signalos orchestrate --wave <id> --plan <path>`. The orchestrator: (1) calls `worktree-manager.sh create` to fan out one git worktree per task, (2) dispatches `run_step()` calls concurrently using `ThreadPoolExecutor` (default cap: 5), (3) prints the Wave status card after each task state change, (4) calls `worktree-manager.sh reconcile` + `retire` after all tasks finish. T2 paused tasks are logged and the orchestrator continues with other tasks; a "Pending T2 resumes needed" list is printed at the end. The orchestrator is an operational lever, **not a Gate**. Task parallelism does not bypass Trust Tier or pause semantics — each task's `run_step()` call fires the same four W1.1 journal events as a sequential harness call. Canonical command doc: `core/execution/commands/signalos-orchestrate.md`. Canonical skill doc: `core/execution/skills/parallel-orchestration/SKILL.md`.

**wiring guard** — The W2.1 structural integrity validator at `core/governance/Validators/wiring-guard.sh`, introduced by AMD-CORE-009. Seven checks verify that every command, skill, hook, rule, and emitter is consistently registered across all config surfaces: (1) commands registry → disk, (2) commands disk → registry, (3) commands ↔ rules, (4) skills registry → disk, (5) skills disk → registry, (6) hooks registry ↔ disk, (7) emitters ↔ dispatcher. The guard exits 0 if all checks pass; exits 1 if any check fails. It runs at `session-start` (before the Summary section) with `--quiet` and in CI (`.github/workflows/core-proof.yml`) before the proof scenario suite. A wiring gap detected at session-start is a hard `ERRORS++` — the session is blocked until the gap is resolved, in conformance with the §1 fail-hard default. `jq` is the only non-stdlib dependency (already required by CI from W1.1).

**Wave status card** — The W2.1 observability display at `cli/signalos_lib/status.py`, introduced by AMD-CORE-008 and invoked via `signalos status`. The card reads all state from local disk (no LLM call, no network, stdlib only) and renders: Wave ID + delivery phase, first line of the problem statement from `BELIEF.md`, scale track + delivery mode, gate status G0–G5 (✓/○), active tasks with trust tier and status (⟳/⏸/✓/✗), and next blocking action for the appropriate role. Phase detection: highest open gate determines phase name (ONBOARDING → BELIEF → PLANNING → DESIGN → BUILD → REVIEW → DONE). Next action logic: paused task → `PE → signalos pause resume <step-id>`; all done and G5 open → `QA → sign QUALITY_CHECK.md`. The status card exit code is always 0 — it is advisory, never blocking.
