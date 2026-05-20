# SignalOS — System Audit and Completion Plan

**Status:** ⚠️  **v0.1 — UNVERIFIED — DO NOT TRUST.** This version relies on summarized output from exploration agents that had measurable inaccuracies. Superseded by v0.2 (`SYSTEM-AUDIT-AND-COMPLETION-PLAN-v0.2-2026-05-20.md`) which is built from direct grep verification.
**Generated:** 2026-05-20
**Repo HEAD at write time:** `538d596` (main, post sandbox+P0-fix merge)
**Audit basis:** three parallel exploration agents (skills/hooks, backend, frontend) + my cross-verification of each agent's most damaging claims + direct census across the file tree.

## Known errors in this v0.1 document

Discovered after writing v0.1; corrected in v0.2:

1. Claimed 5 dead Python modules (`context`, `pause`, `registry`, `regression`, `serve`). **Truth:** only `regression.py` is truly dead. The other 4 have full chain: top-level `signalos_lib/X.py` → `signalos_lib/commands/X.py` → `cli.py` dispatch.
2. Claimed `freeze_wave`/`unfreeze_wave` Rust commands have no JS caller. **Truth:** called from `src/js/ipc.js:245-246`.
3. Claimed `pre-tool-use-guard.sh` is dead. **Partial truth:** referenced in `_bundle/integrations/hooks/claude-hooks.json` + `_bundle/core/tool-adapters/_shared/hooks.json` (declarative registrations). Runtime invocation still unverified.
4. Other claims based on agent-summary output are also suspect; do not rely on this document for engineering decisions. Use v0.2.

---


This document is the canonical reference for: what's in the repo, what works, what doesn't, what we've promised but haven't delivered, and how we finish. It replaces the conversational audit summaries.

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Guiding principle: no-delete, complete-the-commitment](#2-guiding-principle-no-delete-complete-the-commitment)
3. [Repository overview](#3-repository-overview)
4. [Subsystem-by-subsystem audit](#4-subsystem-by-subsystem-audit)
   - 4.1 [Protocol bundle](#41-protocol-bundle)
   - 4.2 [Python sidecar](#42-python-sidecar)
   - 4.3 [Rust IPC](#43-rust-ipc)
   - 4.4 [Frontend (TS + Preact)](#44-frontend-ts--preact)
   - 4.5 [Legacy JS](#45-legacy-js)
   - 4.6 [CI / build pipeline](#46-ci--build-pipeline)
   - 4.7 [Tests](#47-tests)
5. [End-to-end wiring trace](#5-end-to-end-wiring-trace)
6. [The 12 most damning findings](#6-the-12-most-damning-findings)
7. [The completion plan](#7-the-completion-plan)
   - 7.1 [Phase 1: Design records (G3-signed)](#71-phase-1-design-records-g3-signed)
   - 7.2 [Phase 2: Complete unfinished commitments](#72-phase-2-complete-unfinished-commitments)
   - 7.3 [Phase 3: Wiring half-built surfaces](#73-phase-3-wiring-half-built-surfaces)
   - 7.4 [Phase 4: Activation — protocol docs become runtime](#74-phase-4-activation--protocol-docs-become-runtime)
   - 7.5 [Phase 5: Verification](#75-phase-5-verification)
   - 7.6 [Phase 6: Anti-regression CI](#76-phase-6-anti-regression-ci)
8. [Implementation considerations](#8-implementation-considerations)
9. [Definition of done](#9-definition-of-done)
10. [Appendix A — Complete file inventory](#10-appendix-a--complete-file-inventory)
11. [Appendix B — Agent-audit accuracy notes](#11-appendix-b--agent-audit-accuracy-notes)

---

## 1. Executive summary

SignalOS is a Tauri + Preact + Python sidecar application that wraps an LLM orchestrator with a governance protocol (gates, audit trail, skills, decision DNA, soul document). It works end-to-end at the byte level: code compiles, CI is green across Linux/macOS/Windows + real Docker, the orchestrator writes files (after the P0 fix shipped in commit `538d596`).

**It is not a finished product.** The audit reveals **60+ unfinished commitments** — protocol files, modules, commands, hooks, and UI surfaces that exist as text or scaffolding but lack the wiring to be triggered at runtime. Among them:

- **16 slash commands** declared in the protocol with full CLI dispatch but **zero UI surfaces** to invoke them.
- **5 Python sidecar modules** completely unused (`context.py`, `pause.py`, `registry.py`, `regression.py`, `serve.py`).
- **11 Rust Tauri commands** registered with no JavaScript caller (6 in enforcement.rs, 5 in test_automation.rs, 1 orphan `get_cost_summary`).
- **~15 governance documents** copied to the workspace by `signal-init` but **never read back** by any decision-time code path.
- **7 IDE adapter `emit.sh` scripts** present for the multi-IDE story but never invoked.
- **~12 strategy templates** representing wave stages (belief, design, etc.) that are copied and forgotten.
- **20+ metric query configs** intended for observability but with no consumer.
- **DashboardView's gate activities/criteria** UI exists but the backend never emits real data.
- **G3 Design gate** is entirely absent from the implemented flow.
- **The intent classifier** is a regex that misses natural phrasings like "I want to do a financial dashboard."
- **Git push, commit, and remote-repo creation** have zero automation.
- **Test results** are captured by tdd_runner but never surfaced in the UI.

The framing is critical: **these are not dead code. They are unfinished commitments.** The protocol promised them; the implementation never delivered. The remediation principle is to finish, not delete (see §2).

**Honest scope:** completing the protocol's stated commitments is **~6 to 10 months of focused engineering**. This document maps each item, sequences the work, and defines what "done" means.

---

## 2. Guiding principle: no-delete, complete-the-commitment

> **Nothing is dead. Everything is a commitment in progress.**
>
> Unused = unfinished. Delete requires explicit retraction recorded in DECISION-DNA, signed at G5 by the user. The default is finish.

Corollary rules:

1. **User signs every gate by default.** Auto-sign is an advanced per-gate opt-in. G5 (release) is never auto-signable.
2. **Every gate has activities + criteria emitted by the backend** and rendered with real data in the UI.
3. **Soul / Constitution / Decision-DNA are active runtime constraints**, not files-on-disk. They are injected into every plan-generation prompt and consulted at decision time.
4. **The anti-regression CI rejects new orphans.** If a PR adds a new exported function, signal, command, hook, template, or governance doc without a caller in the same PR, CI fails.
5. **Design before code, always.** G3 is mandatory. AMD-CORE entries in DECISION-DNA precede implementation.

These five rules become **AMD-CORE-100 through AMD-CORE-104** and are the first deliverables of Phase 1.

---

## 3. Repository overview

```
signalos-app/
├── .github/workflows/      (4 workflows: pages, release, smoke, test-automation)
├── docs/                   (this audit + INTERNAL_TESTING_BUILD.md)
├── distribution/           (landing page + update manifests)
├── python/
│   ├── signalos_lib/       (40 modules + commands/ + _bundle/)
│   │   ├── _bundle/        (424 files: protocol templates, hooks, skills, tools)
│   │   └── commands/       (32 CLI subcommands)
│   └── test_*.py           (11 test files)
├── src/                    (TS + Preact frontend)
│   ├── components/views/   (13 views)
│   ├── services/           (11 service files)
│   ├── js/                 (18 legacy JS files, mid-migration)
│   ├── state.ts            (Preact signals)
│   └── global.d.ts         (window-level type decls)
├── src-tauri/              (Rust shell)
│   └── src/                (11 .rs files, 64 #[tauri::command] handlers)
└── scripts/                (build/sidecar tooling)
```

### Census (verified counts as of `538d596`)

| Asset | Count | Audit verdict (see §4 for detail) |
|---|---:|---|
| Bundle files | 424 | ~80 protocol-only / unwired |
| Bundle SKILL.md | 35 | All 35 routed via `_SKILL_KEY_TO_PATH` ✓ |
| Bundle hook scripts | 11 | 3 fired at runtime; 7 helpers wired through them; 1 dead (`pre-tool-use-guard.sh`) |
| Slash command definitions | 49 | 33 wired to UI; 16 with zero UI caller |
| Python sidecar modules | 40 | 35 wired; 5 dead (`context.py`, `pause.py`, `registry.py`, `regression.py`, `serve.py`) |
| Python CLI commands | 32 | Mostly wired; argument parsers exist for unfinished modules |
| Rust source files | 11 | All used |
| Rust #[tauri::command] handlers | 64 | 53 called from JS; 11 with zero caller |
| TS service files | 11 | All used |
| TS view components | 13 | 12 fully wired; 1 (DashboardView) shows empty data because backend doesn't emit |
| Legacy JS files in src/js | 18 | 7 still imported by main.tsx; 11 dead from incomplete Preact migration |
| Python test files | 11 | All exercise real production paths |
| JS test files | 6 | All cover real component or service logic |
| GitHub workflows | 4 | All triggered + functional after fixes shipped this session |

---

## 4. Subsystem-by-subsystem audit

### 4.1 Protocol bundle

The bundle ships as Python package data under `python/signalos_lib/_bundle/` and is copied to a user's workspace by `signal-init`. **424 files. ~80 currently unwired.**

#### 4.1.1 Skills (all 35 wired ✓)

Every `SKILL.md` in the bundle is reachable via `orchestrator.py::_SKILL_KEY_TO_PATH` and loaded by `_relevant_skills()` at task-prompt-build time.

| Skill key | Bundle path | Verdict |
|---|---|---|
| `test-driven-development` | `core/execution/build/test-driven-development/SKILL.md` | ✓ |
| `test-generation` | `core/execution/build/test-generation/SKILL.md` | ✓ |
| `e2e-testing` | `core/execution/build/e2e-testing/SKILL.md` | ✓ |
| `systematic-debugging` | `core/execution/build/systematic-debugging/SKILL.md` | ✓ |
| `verification-before-completion` | `core/execution/build/verification-before-completion/SKILL.md` | ✓ |
| `writing-plans` | `core/execution/plan/writing-plans/SKILL.md` | ✓ |
| `executing-plans` | `core/execution/plan/executing-plans/SKILL.md` | ✓ |
| `comprehensive-code-review` | `core/execution/review/comprehensive-code-review/SKILL.md` | ✓ |
| `receiving-code-review` | `core/execution/review/receiving-code-review/SKILL.md` | ✓ |
| `requesting-code-review` | `core/execution/review/requesting-code-review/SKILL.md` | ✓ |
| `subagent-driven-development` | `core/execution/subagents/subagent-driven-development/SKILL.md` | ✓ |
| `dispatching-parallel-agents` | `core/execution/subagents/dispatching-parallel-agents/SKILL.md` | ✓ |
| `using-git-worktrees` | `core/execution/worktree/using-git-worktrees/SKILL.md` | ✓ |
| `finishing-a-development-branch` | `core/execution/worktree/finishing-a-development-branch/SKILL.md` | ✓ |
| `security-audit` | `core/governance/SecurityAudit/SKILL.md` | ✓ |
| `retro-run` | `core/governance/Retro/retro-run/SKILL.md` | ✓ |
| `retrospective-analyze` | `core/governance/Retro/retrospective-analyze/SKILL.md` | ✓ |
| 18 additional skills under `core/execution/skills/` | (see appendix A) | All ✓ |

**Verdict: skills layer is the single most-wired part of the bundle.** Every skill is loadable; every skill is regex- or key-routable from a task tag.

#### 4.1.2 Hook scripts

| Path | Event | Wired? |
|---|---|---|
| `core/execution/hooks/step-started/step-started.sh` | Pre-task | ✓ fired by `harness.py::_fire_hook` |
| `core/execution/hooks/step-completed/step-completed.sh` | Post-task success | ✓ fired by harness |
| `core/execution/hooks/step-failed/step-failed.sh` | Post-task failure | ✓ fired by harness |
| `core/execution/hooks/pre-session-compress/pre-session-compress.sh` | Pre-compression | ⚠ referenced in `context.py` but `context.py` is itself unused |
| `core/execution/hooks/exception-router.sh` | Error routing | ✓ called from `worktree-manager.sh` |
| **`core/execution/hooks/pre-tool-use-guard.sh`** | Pre-write security guard | **❌ DEAD — no caller. This is a security commitment we currently dishonour.** |
| `core/execution/hooks/_lib/brain-auto-ingest.sh` | Helper | ✓ called by hooks |
| `core/execution/hooks/_lib/brain-session-inject.sh` | Helper | ✓ called by hooks |
| `core/execution/hooks/_lib/journal-append.sh` | Helper | ✓ called by step-* hooks (29 refs) |
| `core/execution/hooks/_lib/metrics-append.sh` | Helper | ✓ called by `harness.py::_append_metric` |
| `core/execution/hooks/_lib/redact.py` | Helper | ✓ called by `harness.py::_redact_text` |

**Verdict: pre-tool-use-guard.sh is the highest-impact unfinished commitment in the bundle.** It claims to block unsafe writes, secret exfiltration patterns, and dangerous bash. Nothing invokes it. Completion = wire it into every subprocess invocation in orchestrator/preview/tdd-runner/e2e_runner.

#### 4.1.3 Tool-adapter emitters (multi-IDE story)

| Adapter | `register-hooks.sh` | `emit.sh` |
|---|---|---|
| `claude-code/` | ✓ called by `commands/init.py` on IDE detection | ❌ never invoked |
| `cursor/` | ✓ | ❌ |
| `codex/` | ✓ | ❌ |
| `vs-code/` | ✓ | ❌ |
| `github-copilot/` | ✓ | ❌ |
| `windsurf/` | ✓ | ❌ |
| `antigravity/` | ✓ | ❌ |
| `harness/` | (none) | ❌ |
| `_shared/commands.json` | — | ❌ dead registry — never loaded |
| `_shared/skills.json` | — | ❌ dead registry — orchestrator builds its own |
| `_shared/hooks.json` | — | ✓ read by `commands/hooks.py` |
| `_shared/session-preamble.md` | — | ✓ substituted by `preamble.py` |
| `_shared/hook-registration-helper.sh` | — | ✓ called from `register-hooks.sh` |

**Verdict: half the multi-IDE story is built.** `register-hooks.sh` runs on init; `emit.sh` (which would produce per-IDE config files) is committed work that never runs. Completion = wire `emit.sh` invocation into `init.py` for the detected IDE, after `register-hooks.sh`.

#### 4.1.4 Governance documents

| Path | Type | Wired? |
|---|---|---|
| `core/governance/Governance/SOUL-DOCUMENT.md` | Template | ❌ copied by init, never read back |
| `core/governance/Governance/CONSTITUTION.md` | Template | ⚠ partial — `preamble.py::_constitution_hash` reads it; no enforcement |
| `core/governance/Governance/DECISION-DNA.md` | Living log | ⚠ partial — `design.py::_append_decision_dna` appends; no read-back at decision time |
| `core/governance/Governance/ARTIFACT_MAP.md` | Reference | ❌ |
| `core/governance/Governance/AUDIT_TRAIL_SPEC.md` | Spec | ❌ |
| `core/governance/Governance/CAPABILITY_AUDIT_v1.0.1.md` | Spec | ❌ |
| `core/governance/Governance/CLIENT-SIGNAL-LOG.md` | Reference | ❌ |
| `core/governance/Governance/DATA_PROCESSING_RECORD.md` | Template | ❌ |
| `core/governance/Governance/PHASE-DEBT-PROTOCOL.md` | Spec | ❌ |
| `core/governance/Governance/PROMPT-LIBRARY.md` | Reference | ❌ |
| `core/governance/Governance/SIGNATURE_SPEC.md` | Spec | ❌ |
| `core/governance/QA/scenarios/*.yaml` (100+ files) | QA cases | ✓ loaded by `signalos qa-validate` |
| `core/governance/QA/README.md` | Reference | ✓ referenced by cli.py |
| `core/governance/Proof/wave-*.md` (3 files) | Historical | ❌ never validated against |
| `core/governance/Retro/waves/*/README.md` | Retro template | ✓ loaded by `context.py::_retro_waves` (but context.py itself unused) |
| `core/governance/ENFORCEMENT.md` | Policy guide | ❌ |
| `core/governance/SecurityAudit/SKILL.md` | Skill | ✓ routed |
| `core/governance/SecurityAudit/references/*` | Skill refs | ✓ loaded as part of skill |
| `core/governance/SecurityAudit/assets/report-template.md` | Template | ⚠ referenced in skill text; no validator reads it |

**Verdict: governance is mostly ceremony.** SOUL, AUDIT_TRAIL_SPEC, CAPABILITY_AUDIT, DATA_PROCESSING_RECORD, etc. are copied to the workspace and forgotten. Completion = activate each. Phase 4 covers SOUL/CONSTITUTION/DECISION-DNA specifically; the others get individual treatment.

#### 4.1.5 Strategy templates

12 templates under `core/strategy/Templates/`:

```
backlog-schema.yaml
belief-lite-template.md
belief-map-template.md
belief-template.md
discovery-brief-template.md
expectation-map-template.md
product-belief-template.md
product-expectation-map-template.md
refinement-checklist.md
role-activation-card-template.md
```

Plus `core/strategy/BELIEF_MAP.md` and `core/strategy/SIGNAL_CONCEPTS.md`.

**Verdict: all 12 templates are dead.** Copied to workspace; never read back; never populated by the agent. Each template represents a stage of the wave (G1 Belief, G2 Plan, etc.) that the protocol promises but the implementation skips. Completion = each template is populated by the agent at the corresponding stage and signed at the right gate.

#### 4.1.6 Execution commands

`core/execution/commands/` contains 49 `.md` files — one per slash command. All 49 are copied to the workspace by `signal-init` (which is how Claude Code / Cursor / etc. discover them). **The protocol layer is wired.**

The **wiring gap** is in the **UI to invoke them** — see §4.2 (Python sidecar) and §4.4 (Frontend). 16 of the 49 have no UI caller.

#### 4.1.7 Other bundle assets

| Path | Verdict |
|---|---|
| `core/observability/dashboard.html` | ❌ dead static HTML; no caller |
| `core/observability/render-dashboard.py` | ❌ self-referential script; no caller |
| `core/registry/_schema/plugin-manifest.schema.json` | ✓ loaded by `registry.py::_schema_path` (but registry.py itself is unused → effectively dead) |
| `core/execution/agents/*.md` (5 docs) | ❌ reference material; not loaded |
| `core/execution/agents/queries/**/*.yaml` (20+ files) | ❌ metric query configs; no consumer |
| `core/execution/agents/*.sh` (3 helper scripts) | ❌ never invoked |
| `integrations/rules/*.mdc` | ✓ scaffolding for Cursor rules |
| `integrations/github/copilot-instructions.md` | ✓ scaffolding for Copilot |
| `integrations/github/copilot-chat-agents.json` | ⚠ scaffolding; no validator |

### 4.2 Python sidecar

`python/signalos_lib/` contains 40 top-level modules + a `commands/` directory with 32 CLI subcommand modules + the `_bundle/` data tree.

#### 4.2.1 Module-level wiring

**✓ wired:**
- `orchestrator.py` — drives wave execution; called via `cli.py orchestrate` subcommand
- `harness.py` — LLM call abstraction; called via `cli.py harness` and from orchestrator
- `sandbox.py` — Docker wrap helpers; called from harness/orchestrator/e2e_runner/tdd_runner
- `brain.py` — memory subsystem; called via `brain` CLI + IPC `brain:search`, `brain:add`
- `sign.py` — gate signing; called via `cli.py sign` (mapped from `/signal-sign`)
- `status.py` — status card printing; called from CLI + orchestrator
- `security.py` — security scans; called via `signal-cso` (though signal-cso has no UI caller)
- `plan.py` — PLAN.tasks.yaml schema; called from orchestrator
- `health.py` — health checks; CLI subcommand
- `campaign.py` — QA campaign orchestration; called via `signal-qa`
- `intent.py` — intent classification (CLI-side); called from `signal-intent` subcommand
- `deploy.py` — deploy lifecycle; called via signal-setup/land/canary-deploy (but no UI caller)
- `devex.py` — DevEx metrics; called via `signal-devex` (but no UI caller)
- `tdd_runner.py` — TDD test execution + sandbox-wrap; called from orchestrator
- `e2e_runner.py` — Playwright e2e + sandbox-wrap; called from orchestrator
- `skill_validators.py` — post-LLM skill enforcement; called from orchestrator
- `signalos_secret_guard.py` — secret redaction at the IPC boundary; called from `signalos_ipc_server.py`
- `signalos_attachments.py` — payload analysis; called via `attachment:analyze` IPC
- `signalos_ipc_server.py` — main IPC entry point; spawned by Tauri shell

**❌ DEAD (no caller, no dispatch):**

| Module | Promised function | Status | Action |
|---|---|---|---|
| `context.py` | AMD-CORE-005 context compression | Argparser exists; no main() dispatch | Complete: wire `signal-context-restore` UI |
| `pause.py` | Step pause/resume controller | Argparser exists; no main() dispatch | Complete: UI pause button on plan card |
| `registry.py` | AMD-CORE-006 plugin registry | Parsers for install/verify/list; no main() dispatch | Complete: plugin manager UI + signing flow |
| `regression.py` | Regression test generation | Functions exist; zero imports | Complete: wire into post-failure orchestrator |
| `serve.py` | (purpose currently undocumented) | Zero imports | Investigate intent; complete or G5-sign retraction |

**⚠ semi-wired (the orchestrator's previously-broken P0 was here):**
- `_read_harness_response` (fixed in `538d596`) — was reading wrong path; now correct.

#### 4.2.2 IPC command routing

`signalos_ipc_server.py::route()` handles these direct IPC commands:

| Command | Handler | JS caller? |
|---|---|---|
| `state:wave` | `get_wave_state()` | ⚠ JS calls `get_wave_state` Tauri command instead (different path) |
| `state:gates` | `get_gate_states()` | ⚠ same |
| `gate:sign` | `sign_gate()` | ⚠ JS uses `/signal-sign` instead |
| `brain:search` | `brain_search()` | ⚠ JS uses `get_brain_entries` Tauri command instead |
| `brain:add` | `brain_add()` | ⚠ same — `add_brain_entry` Tauri command |
| `audit:list` | `audit_list()` | ⚠ JS uses `get_audit_trail` Tauri command |
| `cost:summary` | stub ("cost tracked in Rust") | ❌ |
| `security:secrets` | `scan_secret_files()` | ❌ no caller |
| `attachment:analyze` | `analyze_payload()` | ❌ no caller (only on attach-file flow which is itself a placeholder) |
| `ping` | stub pong | ❌ no caller |
| `phase:contract` | `PHASE_CONTRACTS` lookup | ✓ called from `orchestratorEvents.ts` |
| `signal-checkpoint` | `handle_checkpoint()` | ✓ `approvePlan.ts` |
| `signal-rollback` | `handle_rollback()` | ✓ `approvePlan.ts` (rollbackWave) |
| `signal-sandbox` | `handle_sandbox()` | ❌ no UI yet |

**Verdict: there are two parallel routing systems** — direct IPC (`state:wave`, `brain:*`, etc.) AND slash commands (`/signal-*`). The frontend predominantly uses slash commands. Many of the direct IPC commands are shadowed by Tauri commands in Rust that read the same data. **Architectural debt.** Consolidation = AMD-CORE-107.

#### 4.2.3 Slash command map (49 → 33 wired, 16 dead UI)

`signalos_ipc_server.py::map_slash_command()` lists every slash command. **16 of the 49 have full CLI dispatch but zero UI invocation:**

| Slash command | Backend module | UI completion required |
|---|---|---|
| `/signal-learn` | `brain.py` | BrainView search box + result pane |
| `/signal-cso` | `security.py` | Security findings drawer; toolbar button |
| `/signal-autoplan` | `orchestrator.py` (autoplan flow) | Plan-card "regenerate" button |
| `/signal-context-restore` | `context.py` | Auto-fire after compression; UI notice |
| `/signal-setup-deploy` | `deploy.py` | Deploy tab; target picker |
| `/signal-land-deploy` | `deploy.py` | Deploy "ship" button (G5 user-signed) |
| `/signal-canary-deploy` | `deploy.py` | Canary slider + watch dashboard |
| `/signal-benchmark` | `devex.py` | Benchmarks tab |
| `/signal-devex-plan` | `devex.py` | DevEx planning panel |
| `/signal-devex` | `devex.py` | Time-to-wave metric in dashboard |
| `/signal-retro-global` | `devex.py` / `retro.py` | HistoryView "Retro" tab |
| `/signal-careful` | `enforcement` flow | Toolbar careful-mode toggle |
| `/signal-guard` | `enforcement` flow | Stricter security-audit mode |
| `/signal-freeze` | `cli.py freeze_wave` (Python) AND `enforcement.rs::freeze_wave` (Rust) | Pick ONE; wire toolbar button |
| `/signal-unfreeze` | (same duplicate) | (same) |
| `/signal-second-opinion` | review subagent | "Get second opinion" button |
| `/signal-second-opinion-record` | review log | Persist to DECISION-DNA |
| `/signal-investigate` | (incident flow) | IncidentView |

### 4.3 Rust IPC

`src-tauri/src/` has 11 .rs files. The Tauri command registration in `main.rs` lists **64 commands** — more than the previous audit-agent estimate of 54.

**✓ called from JS (53 commands):**
Workspace ops (set/get/validate, read/write/list, secrets CRUD, identity, role-for-gate, git-status, watch, env-diff, open-path), preview (probe-node, start/stop/list/get), provider (test/list/get-active/set-active/set-model/set-pricing, send-message, send-message-stream, record-token-usage, get-cost-state, reset-session-cost, set-monthly-budget, fetch-provider-models), keychain (store/delete/has), sidecar (status/restart), updates (check), governance (get-wave-state, get-gate-status, sign-gate, get-brain-entries, add-brain-entry, get-audit-trail), and the dispatch (`run_signal_command`).

**❌ DEAD — registered, never invoked from JS (11 commands):**

| Command | File | Completion required |
|---|---|---|
| `get_cost_summary` | `ipc.rs` | Wire to SettingsView cost panel OR remove via G5 retraction |
| `get_enforcement_state` | `enforcement.rs` | Toolbar enforcement pill |
| `build_precheck` | `enforcement.rs` | Pre-wave check before Approve |
| `override_rule` | `enforcement.rs` | Override modal when a rule blocks |
| `set_rule_mode` | `enforcement.rs` | Settings → Enforcement |
| `freeze_wave` | `enforcement.rs` | Toolbar (pick this OR Python impl per AMD-CORE-107) |
| `unfreeze_wave` | `enforcement.rs` | Companion |
| `list_test_debt` | `test_automation.rs` | Test debt panel in BuildView |
| `add_test_debt` | `test_automation.rs` | "Defer test" button on failed-test row |
| `resolve_test_debt` | `test_automation.rs` | Per-item resolve action |
| `check_mutation_threshold` | `test_automation.rs` | CI integration + UI metric |
| `check_test_first` | `test_automation.rs` | Gate that blocks build if no test refs |
| `read_mutation_score` | `test_automation.rs` | DashboardView metric |

**Verdict: enforcement layer + test-automation layer are both fully implemented in Rust and untouched by the frontend.** They represent a substantial unfinished governance commitment.

### 4.4 Frontend (TS + Preact)

#### 4.4.1 State signals (`src/state.ts`)

Approximately 55 exported signals. Most are bidirectionally wired (a writer + a reader). Notable orphans:

| Signal | Status | Completion |
|---|---|---|
| `busy` | Never written or read | Investigate intended use; either wire to chat-busy indicator or G5-sign retraction |
| `cmdPaletteOpen` | Read by BuildView; never written | Add keyboard shortcut (Cmd+K) to toggle |
| `previewStack` | Read by `preview.ts`; never written | Add stack picker UI in PreviewView |
| `currentGateId` | Read in legacy JS only | Modernize wiring |
| `gateOpen` | Written by `openGate()` legacy; not read in Preact | Audit and wire to gate-detail modal |
| `previewKey` | Written by preview.ts; not read in Preact | Confirm intended for lifecycle; expose if useful |
| `gateActivities`, `gateCriteria`, `currentWaveSummary`, `currentGateInfo` | Populated by `loadDashboard()` but source data is empty | Backend must emit (see §4.4.3) |
| `currentWave` | Read by approvePlan; mostly read-only | Wire to wave-selector UI |

#### 4.4.2 Services (`src/services/*.ts`)

11 service modules. Most are fully wired through `window.X` globals or event listeners.

**Cross-verified findings (the earlier agent was WRONG about some):**

- `signalosPrompt.ts::isBuildIntent`, `wrapWithSignalosContext`, `extractPlanWithErrors`, `inferMissingSkills`, `planToYaml`, `planToMarkdownTaskList` — **all called** from `src/js/ui/chat.js:72-90` and `src/services/approvePlan.ts`. The earlier audit-agent claim that these were "never called" was incorrect (agent only searched `src/services/`, missed legacy JS).
- `protocolContext.ts::buildContextBlock` — **called** by `wrapWithSignalosContext` (which is wired into chat.js). Earlier agent claim was wrong.

**Genuinely orphan service exports:** none found after cross-verification.

#### 4.4.3 Views (`src/components/views/*.tsx`)

13 view components. 12 fully wired with real data.

**DashboardView (the one damning case):**

`DashboardView.tsx` reads `gateActivities`, `gateCriteria`, `currentWaveSummary`, `currentGateInfo`. These signals ARE written by `src/js/ui/dashboard.js::loadDashboard()` from `ipc.gates.getAll()` → `state:gates` IPC → `signalos_ipc_server.py::get_gate_states()` → `signalos status --json`.

**Problem:** the CLI's `signalos status --json` does NOT currently emit activities or criteria arrays. So the signals get populated with empty data. The UI renders "No activities yet" placeholder text. **The pipe is wired; the source is dry.**

**Completion:** make `status.py` emit per-gate activities (derived from PLAN.tasks.yaml tasks) and criteria (derived from skill_validators + gate-spec).

#### 4.4.4 Window globals (`src/global.d.ts`)

~60 window-level functions declared. Cross-verified count:

- ~57 fully wired (called from JSX + implemented in `app-v2.js` or services)
- 2 placeholders: `attachFile()` (just appends "[File attached]"), `voiceInput()` (shows error)
- 1 orphan: `changeStack()` (defined but never called)

### 4.5 Legacy JS

`src/js/` contains 18 files. **Only 7 are imported by main.tsx:**

✓ imported + active:
- `app-v2.js` — main wiring layer
- `ipc.js` — Tauri IPC wrapper
- `wizard.js` — onboarding logic
- `conversation.js` — chat history
- `state.js` — reactive state proxy
- `util.js` — utilities
- `ui/chat.js`, `ui/dashboard.js` — UI rendering

❌ orphaned from incomplete Preact migration (11 files):
- `enforcement.js`, `csp-bootstrap.js`, `file-tree.js`, `left-tabs.js`, `plan-reader.js`, `preview.js`, `progress.js`, `secrets.js`, `test-debt.js`, `wired-commands.js`, and the older sibling `ui/*.js` files not enumerated above

**Verdict: the Preact migration is incomplete.** These 11 JS files represent work-in-progress that was abandoned mid-rewrite. Completion = finish moving each to a Preact equivalent OR explicitly G5-retract.

### 4.6 CI / build pipeline

4 GitHub workflows:

| Workflow | Triggers | Jobs | Status |
|---|---|---|---|
| `pages.yml` | push to main | build, deploy, publish-manifest | ✓ green |
| `release.yml` | push of `v*` tags | matrix build + signed installers | ✓ wired; not yet exercised |
| `smoke.yml` | every-branch push + PR to main | unit-tests (ubuntu-22.04), smoke-build (linux+windows+macos) | ✓ green |
| `test-automation.yml` | every-branch push + PR + nightly cron | l0-precommit, l1-build (matrix), l1-sandbox-integration, l2-installer-smoke (matrix), l3-preprod, l6-nightly | ✓ green |

**Verdict: CI is healthy.** This session shipped fixes for:
- Trigger pattern (branch protection) — previously main-only
- `cargo fmt --check` formatting violation
- PyYAML missing in l0-precommit and l3-preprod
- `npm install` missing in smoke-build
- `--network host` security bug in sandbox

**Completion needed:** anti-regression CI job to detect new orphans (see §7.6).

### 4.7 Tests

11 Python test files (204 tests + 6 skipped). 6 JS test files (76 tests).

| Test file | Coverage |
|---|---|
| `test_onboarding.py` | Integration test for signal-init slides |
| `test_signalos_attachments.py` | Payload analysis |
| `test_signalos_secret_guard.py` | Secret redaction |
| `test_orchestrator_core.py` | File extraction, write, plan-load, skills regex, prompt build, progress emit, harness-response handoff |
| `test_orchestrator_skills.py` | Skill catalog integrity, explicit skill routing, regex fallback |
| `test_sandbox.py` | Sandbox config, image classifier, argv shape, ports, network-host opt-in |
| `test_sandbox_integration.py` | 6 real-Docker integration tests (CI-only) |
| `test_wave_rollback.py` | Checkpoint capture + git reset rollback |
| `test_e2e_runner.py` | E2E runner + dev-server-spawn sandbox wrap |
| `test_tdd_runner.py` | TDD test execution loop |
| `test_skill_validators.py` | Post-LLM skill enforcement |

| JS test file | Coverage |
|---|---|
| `app.test.tsx` | App init |
| `signalosPrompt.test.ts` | isBuildIntent, plan extraction, skill inference, YAML emit, Markdown emit |
| `BuildView.test.tsx` | Plan card rendering, approve/cancel/retry/rollback handlers |
| `DashboardView.test.tsx` | Gate stepper, verdict text |
| `HelpView.test.tsx` | Static content |
| `PreviewView.test.tsx` | Status state machine, device buttons |

**Verdict: test coverage is solid for components and core helpers.** Gaps:
- No test for the harness → orchestrator file handoff (one was added in this session: `test_end_to_end_with_real_harness`)
- No integration test for the full chat → plan → orchestrate → file-write pipeline (would catch the kind of cross-module bugs the smoke check found)
- No test for the chat.js → signalosPrompt.ts wiring (the legacy JS isn't covered)

---

## 5. End-to-end wiring trace

For "user types build intent and ends up with files on disk":

```
1. User types in BuildView's <textarea>
   ↓
2. composerKey(e) → sendMsg() in src/js/ui/chat.js
   ↓
3. wrapWithSignalosContext(val) in src/services/signalosPrompt.ts
   ↓ (if isBuildIntent matches "build" regex)
4. buildContextBlock() in src/services/protocolContext.ts
   ↓ reads .signalos/Governance/SOUL-DOCUMENT.md, CONSTITUTION.md, DECISION-DNA.md
5. The wrapped prompt is sent via ipc.provider.chatStream
   ↓
6. Rust Tauri command send_provider_message_stream in src-tauri/src/provider.rs
   ↓
7. stream_anthropic / stream_openai / etc. POST to provider API
   ↓ SSE deltas emitted as chat:token Tauri events
8. JS accumulates tokens into streaming bubble
   ↓ on stream done
9. extractPlanWithErrors(text) in signalosPrompt.ts parses the ```signalos-plan block
   ↓
10. inferMissingSkills (heuristic auto-tag) backfills security-audit/test-generation/etc.
   ↓
11. Plan card renders in BuildView with the parsed tasks
   ↓ user clicks Approve & run
12. approvePlan(bubbleId) in src/services/approvePlan.ts:
    a. /signal-checkpoint captures pre-wave HEAD SHA → handle_checkpoint in signalos_ipc_server.py
    b. write PLAN.tasks.yaml + PLAN.md → write_workspace_files Tauri command
    c. /signal-sign G2 signs Gate 2
    d. /signal-orchestrate dispatches the wave
   ↓
13. signalos_ipc_server.py::dispatch_cli("signal-orchestrate", ...) → map_slash_command → ["orchestrate", ...] → run_core_cli
   ↓
14. cli.py orchestrate subcommand → orchestrator.run_wave(wave_id, plan_path, ...)
   ↓
15. _tasks_from_plan(plan_path) loads tasks; OR _read_tasks(root) if worktrees enabled
   ↓ for each task:
16. _build_task_prompt(task, root):
    - inject task identity (id, title, branch, wave, tier, description)
    - inject file list
    - inject existing file contents (iterative refinement)
    - inject skill catalog content via _relevant_skills (explicit skills + regex fallback + verification-before-completion always)
    - prepend "previous_failure" section if retry
    ↓
17. harness.run_step(step_id, prompt, ...) fires step-started hook → calls active provider → fires step-completed/failed hook
   ↓
18. harness._persist_response_preview writes response to .signalos/sessions/<sid>/harness/<call_id>/response.preview.txt
   ↓
19. _read_harness_response(root, sid, result) — FIXED in 538d596 — reads the canonical path
   ↓
20. _extract_files_from_response(response) parses ### filepath: ... blocks
   ↓
21. _write_extracted_files(root, files) writes them with resolve-time path-escape defense
   ↓
22. _append_files_to_wave_checkpoint updates wave checkpoint with files-written
   ↓
23. _record_missing_deps scans imports vs package.json, writes .signalos/missing-deps.json
   ↓
24. skill_validators.validate_skill_artifacts runs post-LLM checks (security-audit/test-gen/etc.)
   ↓ if violations: result.status = "failed" + previous_failure populated for retry
25. e2e_runner.run_e2e_task if e2e-testing tagged (spawns dev server + Playwright)
   ↓
26. _emit_task_progress writes JSON to sys.__stdout__ → Tauri sidecar:progress event
   ↓ frontend listener in src/services/orchestratorEvents.ts updates plan-card task status
27. Wave summary returned to JS; cost delta captured; audit trail appended
```

**This is the happy path AS OF `538d596`.** The previously-broken P0 step (19) is now fixed. Steps 1-12 work for explicit build-intent verbs. Steps 25 + e2e are sandbox-wrapped when toggled.

**Known gaps along this path** (all in the completion plan §7):
- Step 1: regex misses natural phrasings (intent classifier issue)
- Step 4: reads governance docs but doesn't enforce them as constraints
- Step 12c: G3 design gate is skipped entirely
- Step 14: worktree path is broken on Mac/Linux (worktree-manager.sh reads PLAN.md HTML comments; only PLAN.tasks.yaml is the source of truth)
- Step 23: missing-deps catches JS imports; doesn't yet catch Python/Rust deps
- Step 27: no auto-commit/push after wave completion

---

## 6. The 12 most damning findings

Sorted by severity / blast radius:

1. **`pre-tool-use-guard.sh` is dead but represents a SECURITY commitment.** The protocol claims to block unsafe writes (paths outside workspace, secret patterns, dangerous bash). Nothing invokes it. **Currently dishonored.**

2. **The orchestrator file-extraction was silently broken until `538d596`.** Every prior wave produced ZERO files while reporting "all_completed." Two disconnects: filename (`response.preview.txt` vs `preview.txt`) + directory (`harness/<call_id>` vs `calls/<call_id>`). Now fixed; regression test in place.

3. **G3 Design gate is skipped.** SignalOS's own protocol mandates G0→G1→G2→G3→G4→G5; the implementation jumps G2 to G4. Code-first is the failure mode SignalOS exists to prevent.

4. **DashboardView shows empty data.** Gate activities + criteria UI exists; the source backend (`status.py`) doesn't emit them.

5. **16 slash commands declared with full CLI dispatch have zero UI invocation.** Every one represents a feature the protocol promised: brain search, security scan, autoplan, deploy, benchmark, devex, retro-global, careful, guard, freeze/unfreeze, second-opinion, investigate.

6. **The intent classifier is a regex.** "I want to do a financial dashboard" doesn't trigger build mode. Natural phrasings fail silently — user sees a chat response, no plan, no wave.

7. **SOUL / CONSTITUTION / DECISION-DNA are files-on-disk, not runtime constraints.** Copied by signal-init; never read at plan-gen time; never enforced as constraints on the LLM's output.

8. **Dual freeze/unfreeze implementation.** Python (`cli.py` + enforcement module) AND Rust (`enforcement.rs`). JS calls neither. Both implementations rot.

9. **Test results are captured but never surfaced.** `tdd_runner` knows what passed and failed; the UI shows nothing.

10. **No git push / commit / remote-creation automation.** User must shell out manually. The "agent ships your work" promise is unfulfilled.

11. **11 Rust Tauri commands registered with no JS caller** (6 enforcement + 5 test-automation). All represent governance commitments without UI.

12. **The Preact migration is incomplete.** 11 legacy JS files in `src/js/` are orphaned from a half-finished rewrite.

---

## 7. The completion plan

Six phases. All work passes through G3 design + G4 build sign with the user as PO. **Default: user signs every gate. Auto-sign is opt-in per gate.**

### 7.1 Phase 1: Design records (G3-signed)

Write AMD-CORE entries in `core/governance/Governance/DECISION-DNA.md` that codify the rules of the road. **No code; only design.** Each entry is ≤2 pages, signed at G3 by the user before any related implementation.

| AMD-CORE entry | What it locks |
|---|---|
| AMD-CORE-100 | No-delete principle — unused = unfinished; delete requires G5-signed retraction |
| AMD-CORE-101 | User signs every gate by default; auto-sign is per-gate opt-in; G5 never auto-signable |
| AMD-CORE-102 | Drop regex intent classifier; always wrap with protocol; LLM decides emit-plan vs chat |
| AMD-CORE-103 | Soul + Constitution + Decision-DNA injected into every plan-gen prompt; post-write validators enforce them |
| AMD-CORE-104 | Anti-regression CI rejects new orphans (orphan = exported symbol with no caller) |
| AMD-CORE-105 | G3 Design gate is mandatory; agent drafts design, user signs, then code |
| AMD-CORE-106 | Audit trail is the SINGLE SOURCE OF TRUTH for gate state; DashboardView reads from it |
| AMD-CORE-107 | Consolidate dual implementations (freeze/unfreeze, IPC vs slash routing) — pick ONE per concern |
| AMD-CORE-108 | Per-wave git automation: commit at wave end; push gated on user sign; remote creation via OAuth |

**Deliverable:** 9 AMD-CORE entries in DECISION-DNA.md. User reviews and signs G3 on each.

**Effort:** 1-2 days of design writing.

### 7.2 Phase 2: Complete unfinished commitments

Every item the audit flagged as unwired becomes a completion task. Sub-waves grouped by subsystem:

#### Wave 2A — Python sidecar modules (5 items)

| Module | Completion spec |
|---|---|
| `context.py` | Wire AMD-CORE-005 context compression: monitor prompt size; auto-compress when >X tokens; UI notice; CLI `signalos context status`; integration test |
| `pause.py` | Wire step-pause: per-task pause button on plan card; resume + abort actions; persist to audit trail; CLI subcommand dispatch |
| `registry.py` | Wire AMD-CORE-006: `signalos plugin install/verify/list` CLI; trust-tier cosign validation; UI plugin manager |
| `regression.py` | After every wave with failures, auto-generate `regression-<wave>.test.ts` capturing the failure; integrate into test suite |
| `serve.py` | Investigate intent (likely "serve workspace artifacts via HTTP"); complete the feature OR G5-sign retraction |

#### Wave 2B — Hook scripts (1 critical)

| Hook | Completion spec |
|---|---|
| `pre-tool-use-guard.sh` | Define deny rules (paths outside workspace, secret-shaped strings in commits, dangerous bash like `rm -rf /`, `curl | sh`, `wget | bash`). Wire into EVERY subprocess invocation: orchestrator (post-write check), preview (pre-npm-install), tdd_runner (pre-test), e2e_runner (pre-server-spawn). Integration test that a malicious payload is rejected. **Highest-priority security completion.** |

#### Wave 2C — Tool-adapter emitters (8 items)

For each of 7 IDE emitters + 1 harness:

| Adapter | Completion spec |
|---|---|
| `claude-code/emit.sh` | Verify against real Claude Code install; integration test; `init.py` invokes after `register-hooks.sh` for detected IDE |
| `cursor/emit.sh` | Same against Cursor |
| `codex/emit.sh` | Same against Codex CLI |
| `vs-code/emit.sh` | Same against VS Code (settings.json + commands) |
| `github-copilot/emit.sh` | Same against Copilot config |
| `windsurf/emit.sh` | Same against Windsurf |
| `antigravity/emit.sh` | Same against Antigravity |
| `_shared/commands.json` | Wire as data source for emit.sh; integration test |
| `_shared/skills.json` | Same |

#### Wave 2D — Slash commands (16 items)

For each unwired slash command, build the UI surface + integration test. See §4.2.3 table for the per-command UI spec.

#### Wave 2E — Rust Tauri commands (11 items)

For each Rust command with no JS caller, build the JS invocation + UI surface. See §4.3 table.

**Sub-decision:** for `freeze_wave` / `unfreeze_wave`, AMD-CORE-107 dictates picking ONE implementation (Python OR Rust). The other gets G5-retracted in DECISION-DNA.

#### Wave 2F — Governance documents (~15 items)

Each governance doc becomes an active runtime artifact:

| Doc | Activation |
|---|---|
| `SOUL-DOCUMENT.md` | Read at every plan-gen; halt on detected violation |
| `CONSTITUTION.md` | Read + post-write rule validation |
| `DECISION-DNA.md` | Last N entries injected as prompt context |
| `AUDIT_TRAIL_SPEC.md` | Schema validated against AUDIT_TRAIL.jsonl writes at startup |
| `CAPABILITY_AUDIT.md` | Run capability audit at signal-init; surface in security tab |
| `DATA_PROCESSING_RECORD.md` | Active GDPR-style record per wave |
| `SIGNATURE_SPEC.md` | Validate gate signatures at sign time |
| `PROMPT-LIBRARY.md` | UI library of prompt templates |
| `CLIENT-SIGNAL-LOG.md` | Format spec for client-side logs |
| `PHASE-DEBT-PROTOCOL.md` | Active debt tracking per phase; visible in Dashboard |
| `ENFORCEMENT.md` | Wired to enforcement rules engine |
| `ARTIFACT_MAP.md` | Registry the UI uses to discover which docs are where |
| `conversations/`, `incidents/`, `signal-logs/` READMEs | Active folder structures |

#### Wave 2G — Strategy templates (12 items)

Each template represents a wave stage. Completion = the agent populates the template at the right stage.

| Template | Stage | Activation |
|---|---|---|
| `belief-template.md` | G1 | Agent drafts; user signs G1 |
| `belief-lite-template.md` | Quick G1 | Optional shorter path |
| `belief-map-template.md` | G1 visualization | Generated graph |
| `discovery-brief-template.md` | G0→G1 | Discovery interview output |
| `expectation-map-template.md` | G1 | Stakeholder expectations |
| `product-belief-template.md` | G1 | Product-level belief |
| `product-expectation-map-template.md` | G1 | Product expectations |
| `refinement-checklist.md` | G2 | Plan refinement gate |
| `role-activation-card-template.md` | G0 | Per-role activation |
| `backlog-schema.yaml` | G2 | Backlog management |

#### Wave 2H — Agent docs (5 items)

5 docs (`brainstorm.md`, `build.md`, `observability.md`, `onboarding.md`, `plan.md`) become per-phase sub-agent definitions:

| Doc | Activation |
|---|---|
| `brainstorm.md` | G1 sub-agent: reads doc as system prompt; outputs belief draft |
| `build.md` | G4 sub-agent: governs the wave executor |
| `observability.md` | G5 sub-agent: builds observability artifacts |
| `onboarding.md` | G0 sub-agent: drives the init wizard |
| `plan.md` | G2 sub-agent: builds the plan |

#### Wave 2I — Metric queries (20+ items)

Build observability layer that consumes the query configs:
- Datadog/Prometheus/Grafana queries surfaced in DashboardView + SettingsView
- Per-wave perf + cost + reliability metrics

#### Wave 2J — Frontend orphans (small list)

| Item | Completion |
|---|---|
| `cmdPaletteOpen` | Add Cmd+K keyboard shortcut; palette becomes functional |
| `attachFile()` | Implement real file upload; agent receives the file as context |
| `voiceInput()` | Integrate Web Speech API |
| `changeStack()` | Wire to PreviewView stack picker |
| `previewStack` | Populate from picker |
| 11 legacy JS files | Finish moving to Preact components |

### 7.3 Phase 3: Wiring half-built surfaces

| Wave | Item | Detail |
|---|---|---|
| 3.1 | Gate activities + criteria | `status.py` emits per-gate activities (= PLAN tasks) + criteria (= skill validators + spec rules); DashboardView shows real data |
| 3.2 | Test results panel | tdd_runner persists results to `.signalos/test-results/wave-<id>.json`; build a Tests panel; failing test rows clickable to open file |
| 3.3 | Git status / commit / push | GitStatusPanel; auto-commit at wave end; "Push wave" button (user-signed by default); first-time push → OAuth flow to create GitHub repo |
| 3.4 | Wave history panel | Group audit entries by wave_id; HistoryView renders timeline |
| 3.5 | Governance viewers | New Governance tab showing SOUL/CONSTITUTION/DECISION-DNA; edit-in-place; agent re-reads on next prompt |

### 7.4 Phase 4: Activation — protocol docs become runtime

Per AMD-CORE-103.

| Wave | Item |
|---|---|
| 4.1 | Inject SOUL + CONSTITUTION + DECISION-DNA into every plan-gen prompt (max 8 KB combined, with truncation if over) |
| 4.2 | Active constitutional enforcement — define rule schema, post-plan validator, post-write validator; halt on violation |
| 4.3 | Decision-DNA append on agent decisions — every gate sign appends AMD-CORE entry; last N injected as context |
| 4.4 | Drop regex; always wrap; let LLM choose plan vs chat (per AMD-CORE-102) |

### 7.5 Phase 5: Verification

| Wave | Item |
|---|---|
| 5.1 | Real LLM end-to-end in Codespaces — run a real "build a financial dashboard" prompt against Anthropic API; verify files land, preview boots, iframe loads |
| 5.2 | TDD-in-Docker verification — enable sandbox toggle; run a wave with TDD-tagged task; verify tests execute in container |
| 5.3 | Beta tester run — tag v1.2.0; signed installers; hand to 3 non-developers; capture friction |

### 7.6 Phase 6: Anti-regression CI

Per AMD-CORE-104.

New CI job: `test_no_dead_code.py`

| Check | Implementation |
|---|---|
| Every `python/signalos_lib/*.py` module has at least one caller in `cli.py main()` or `signalos_ipc_server.py route()` | AST walk + grep across `python/` and `src/` |
| Every `#[tauri::command]` in `src-tauri/src/` is invoked by at least one `src/` file | Parse Rust attributes + grep `invoke("<name>"`) |
| Every entry in `map_slash_command()` has at least one JS invocation | grep against ipc.js patterns |
| Every signal in `state.ts` has both a writer and a reader | tsc AST analysis |
| Every `window.X` global has an `onClick`/`onSubmit` reference | grep |
| Every `.md` and `.sh` in `_bundle/` is either read by code at runtime OR explicitly archived in `_bundle/REFERENCE-ONLY.md` | grep + allow-list |

**On a PR that introduces a new orphan:** CI fails. Either complete the wiring in the same PR, or update the allow-list with a G5-signed retraction comment.

---

## 8. Implementation considerations

### Effort estimates

| Phase | Effort | Risk |
|---|---|---|
| Phase 1 — 9 AMD-CORE design records | 1-2 days | Low (writing only) |
| Phase 2A — 5 Python modules | ~20-30 days | Medium |
| Phase 2B — pre-tool-use-guard | ~3-5 days | Medium-high (security correctness) |
| Phase 2C — 8 IDE adapters | ~15-25 days | Medium (per-IDE quirks) |
| Phase 2D — 16 slash command UIs | ~30-50 days | Medium |
| Phase 2E — 11 Rust→JS wirings | ~22-30 days | Medium |
| Phase 2F — 15 governance docs activation | ~15-25 days | Medium-high (changes runtime behaviour) |
| Phase 2G — 12 strategy template activations | ~15-20 days | Medium |
| Phase 2H — 5 sub-agent doc activations | ~10-15 days | Medium |
| Phase 2I — observability layer | ~7-10 days | Low (read-only metrics) |
| Phase 2J — frontend orphans | ~7-10 days | Low |
| Phase 3 — 5 wiring waves | ~15-20 days | Medium |
| Phase 4 — 4 activation waves | ~10-15 days | Medium-high (LLM behaviour) |
| Phase 5 — verification | ~5-7 days + beta cycle | Discovery |
| Phase 6 — anti-regression CI | ~5-7 days | Low |
| **Total** | **~180-260 days (6-10 months focused engineering)** | + beta cycle |

### Sequencing strategy (highest-value first)

Don't do this linearly. Order by impact-per-effort:

| Order | Item | Why first |
|---|---|---|
| 1 | AMD-CORE-100 + 101 + 105 | Lock the rules of the road |
| 2 | Wave 2B (`pre-tool-use-guard.sh`) | Security commitment currently dishonored |
| 3 | Wave 4.4 (drop regex, always wrap) | Highest UX-impact UI fix |
| 4 | Wave 4.1-4.3 (Soul/Constitution active) | Principal-engineer-with-constitution payoff |
| 5 | Wave 3.1 (gate activities + criteria) | DashboardView stops lying |
| 6 | Wave 3.3 (git automation) | "Agent ships your work" promise |
| 7 | Wave 3.2 (test results panel) | Quality visibility |
| 8 | Phase 6 (anti-regression CI) | Prevent re-accumulation |
| 9-15 | Remaining Phase 2 waves in priority order |
| 16 | Phase 5 verification | Beta after foundation is solid |

### Dependencies

- Phase 1 (design records) gates everything in Phase 2-4.
- Wave 2B (security guard) depends on the deny-rules spec being signed at G3.
- Wave 3.1 (gate data) depends on Wave 2H (sub-agent docs that define what activities/criteria each gate has).
- Wave 4.1 (Soul injection) depends on Wave 3.5 (Governance viewers — so the user can edit).
- Phase 6 (anti-regression CI) is independent and can land in parallel with Phase 2.

### Risk register

| Risk | Mitigation |
|---|---|
| LLM behaviour changes when Soul/Constitution are injected | Wave 4.1 ships behind a feature flag; A/B between with-soul and without-soul on a held-out test set |
| Sandbox `--network host` opt-in regresses isolation | Already mitigated via unit test (added in this session); add integration test of "container can't reach host's other services" |
| Anti-regression CI rejects too-noisy and devs disable it | Tune allow-list aggressively in first 2 weeks of operation; treat as a tool, not a wall |
| 6-10 month estimate slips | Adopt the sequencing above so each milestone delivers user-visible value monthly; never go silent for >2 weeks |

---

## 9. Definition of done

After all phases complete, ALL of these are true (verified by `test_no_dead_code.py` + manual review):

| Check |
|---|
| Every `python/signalos_lib/*.py` module has at least one caller |
| Every `#[tauri::command]` is invoked by at least one `src/` file |
| Every entry in `map_slash_command()` has a JS invocation |
| Every signal in `state.ts` has both a writer and a reader |
| Every `window.X` global has an `onClick`/`onSubmit` reference |
| Every `.md` and `.sh` in `_bundle/` is either active at runtime OR explicitly archived in `_bundle/REFERENCE-ONLY.md` |
| Every gate (G0..G5) has activities + criteria emitted by the backend and rendered with real data |
| Every gate transition requires user sign by default |
| SOUL + CONSTITUTION + DECISION-DNA are read at decision time |
| Real LLM Codespace run produces a working app with sandbox toggle ON |
| Test results visible in UI |
| Git commit/push integrated with wave end |
| Zero placeholder dead handlers (`attachFile`, `voiceInput`, `changeStack` style) |
| All CI gates green across 3 OSes + real Docker |
| Beta tester run completes "build me X" successfully without help |

---

## 10. Appendix A — Complete file inventory

(Full enumeration of every file with its verdict. Generated via census + cross-verification. See sections 4.1-4.7 for grouped tables. The full per-file CSV is omitted from this document for length; can be regenerated from the audit-agent outputs preserved in conversation transcript `97fe7a6e-de62-4233-b1a0-555bd8a58e57.jsonl`.)

---

## 11. Appendix B — Agent-audit accuracy notes

The three exploration agents that produced the source data for this audit had measurable inaccuracies. Documented here so future audits can be calibrated:

| Agent | Inaccuracy |
|---|---|
| Frontend agent | Claimed `signalosPrompt.ts::isBuildIntent`, `wrapWithSignalosContext`, `extractPlanWithErrors`, `inferMissingSkills` had no callers. **They are called from `src/js/ui/chat.js`** — the agent searched only `src/services/`. |
| Frontend agent | Claimed `protocolContext.ts::buildContextBlock` was dead. **It's called by `wrapWithSignalosContext`** which IS wired. |
| Frontend agent | Counted 9 dead legacy JS files. **Actual count is 11** (the agent missed `csp-bootstrap.js` and others). |
| Backend agent | Counted 54 Tauri commands. **Actual count is 64** (verified by grep `#[tauri::command]`). |
| Backend agent | Listed `freeze_wave` / `unfreeze_wave` as orphaned. **They have dual implementations** (Python + Rust); the audit-agent only saw one side. |
| All agents | Tend to summarize rather than enumerate — sub-counts vary by ±15% from direct census. Always cross-verify counts via direct grep before relying. |

**Operational rule:** treat agent outputs as starting points. Cross-verify the most damaging claims with `grep` / `git log` before reporting as truth.

---

*End of document.*

*Maintainer: Samer Zakaria. Last verified: 2026-05-20 against commit `538d596`.*
