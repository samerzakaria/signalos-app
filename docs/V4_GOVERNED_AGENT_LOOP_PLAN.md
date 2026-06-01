# SignalOS v4 — The Governed Agent Loop

## Goal

Build SignalOS v4 — the governed agent loop. Replace the current dumb chat relay and fake terminal with a real provider-agnostic agent runtime powered by LiteLLM behind a capability-detecting adapter contract. The agent uses tool calling (file ops, shell, search) natively from any provider. SignalOS's entire governance layer (6 gates, 12 validators, 13 runtime rules, 287 governance files, trust tiers, security scanning, audit trail, 5-verdict gate review, bounded rework, acceptance criteria, honest closeout) wraps every agent action. The agent works between gates and pauses at every gate for user review. The user talks naturally in chat — no separate Deliver page, no wizard, no fake terminal. One conversation surface. Any AI provider. The user is the client. The agents are the team. SignalOS is the software house.

Every v3 module is either actively used, intentionally marked optional with justification, or de-scoped with proof. No forced integration. No fake usage.

---

## Architecture Invariants

These are non-negotiable rules that every component must satisfy:

### INV-1: No silent skips
Every skip must be explicit, justified, and classified as either `not-applicable` (the check genuinely does not apply) or `blocking` (the check applies but cannot run). A `not-applicable` skip cannot satisfy a mandatory proof. A `blocking` skip prevents delivery closure.

### INV-2: Hard delivery completion invariant
Every successful delivery MUST end with: a real product repo/workspace, executed tests with evidence, runtime proof (dev server health check), UX proof (page renders), and an evidence-based closeout. No delivery can claim "ready" without all of these.

### INV-3: Gate signatures are API-only
Gate approval MUST call the real `sign.py:sign_artifact()` API. Agents MUST NEVER forge `signed_by` fields, write gate signature frontmatter directly, or bypass the signing ceremony. The signing API is the only path to a signed gate.

### INV-4: No silent failures
LLM call failures, tool execution failures, governance denials, and validation failures MUST be surfaced to the user in the chat. No `except: pass`. No empty fallback pretending to be a real result.

### INV-5: Persisted agent run state
Every agent run has a unique run ID. The event log, tool-call ledger, conversation history, and gate state are persisted to `.signalos/agent-runs/<run-id>/`. If the sidecar crashes or restarts, the run can be resumed from the last checkpoint. Tool calls are idempotent (re-executing a write that already succeeded is a no-op).

### INV-6: CI independence from live providers
CI tests MUST use deterministic fake providers (`TestProvider`). Live provider tests (T01-T04) are optional smoke tests gated behind env vars, never blocking CI.

---

## Architecture

```
User chat (single surface)
    |
    v
Provider Adapter (capability detection)
    |--- Detects: streaming? tool-calls? json-schema? context-length? auth-status?
    |--- Routes to LiteLLM with correct model prefix
    |--- Falls back gracefully (no tool calls --> text-only mode)
    |
    v
Agent Loop (the governed runtime)
    |--- Send to LLM via adapter
    |--- LLM responds / requests tool calls
    |--- GOVERNANCE CHECK on every tool call:
    |    |--- Typed path allowlist (not glob, explicit list per trust tier)
    |    |--- Command policy (allowlist + denylist + timeout + cancellation)
    |    |--- Secret redaction on command output
    |    |--- No direct governance file edits (.signalos/, gate frontmatter)
    |    |--- Audit ledger entry for every tool call (allowed or denied)
    |--- Execute allowed tool calls
    |--- GATE CHECK after each round:
    |    |--- Query wave_engine.inspect()
    |    |--- If gate boundary reached --> pause, emit GatePause event
    |--- Loop until LLM done or gate reached
    |
    v
Gate checkpoint --> user reviews --> 5 verdicts:
    |--- APPROVE --> call sign.py:sign_artifact(), advance
    |--- APPROVE-WITH-CONDITIONS --> sign with conditions logged, advance
    |--- REQUEST-CHANGES --> bounded rework (max 3), agent re-works
    |--- REJECT --> bounded rejection (max 2), agent restarts from scratch
    |--- WAIVE --> skip with documented justification, audit logged
    |
    v
Continue or rework (persisted run state survives restart)
```

### What exists (v3)

**Python sidecar (governance layer):**
- `product/llm_provider.py` -- provider detection (11 providers)
- `product/intent.py` -- LLM intent extraction + refinement
- `product/design.py` -- LLM design selection
- `product/questions.py` -- LLM question generation
- `product/assumptions.py` -- LLM assumption recording
- `product/acceptance.py` -- acceptance matrix
- `product/generation.py` -- generation packet + validation
- `product/agent_packets.py` -- scoped execution boundaries
- `product/agent_dispatch.py` -- sends work to LLM
- `product/gate_review.py` -- 5 verdicts (approve, request-changes, reject, waive, conditions)
- `product/security_gate.py` -- injection scan, PII, GDPR, canary
- `product/validation.py` -- build/test/lint
- `product/proof.py` -- runtime + UX proof
- `product/deploy.py` -- deploy decision
- `product/closeout.py` -- honest evidence summary
- `product/lifecycle.py` -- delivery state machine
- `product/repair_loop.py` -- bounded rework
- `product/design_preview.py` -- LLM-generated interactive prototype
- `harness.py` -- AnthropicProvider, OpenAIProvider, GeminiProvider, OllamaProvider
- `wave_engine.py` -- gate state machine
- `orchestrator.py` -- dispatches agents per gate (G0-G5 all wired)
- `sign.py` -- multi-party gate signing
- `validate_cmd.py` -- 12 Layer 1 validators (incl security-posture-guard)
- `security.py` -- OWASP/STRIDE, canary tokens
- `data_privacy.py` -- GDPR export/purge
- `_bundle/` -- 287 governance files (constitution, agent contracts, standards, rules)

**Rust backend (Tauri):**
- `enforcement.rs` -- 13 runtime rules
- `keychain.rs` -- OS credential storage (11 providers)
- `sidecar.rs` -- Python sidecar spawn with env key injection
- `ipc.rs` -- all IPC commands (file ops, secrets, workspace, gates, etc.)
- `governance.rs` -- audit trail, gate state

**Frontend (Preact):**
- `BuildView.tsx` -- chat + command palette + phase strip
- `PreviewView.tsx` -- live iframe preview
- `DashboardView.tsx` -- velocity, burndown, gates
- `TerminalView.tsx` -- fake terminal (to be replaced)
- `DeliverView.tsx` -- wizard (to be merged into chat)
- `WorkspaceSwitcher.tsx` -- multi-workspace dropdown
- `TestDebtPanel.tsx` -- test debt in sidebar
- `ProgressDetail.tsx` -- phase/substep progress
- `ChatBubbleSystem.tsx` -- scope-drift, gate review UI
- `Toolbar.tsx` -- top navigation
- `Sidebar.tsx` -- projects, files, gov tabs

---

## v3 Module Disposition

Each v3 module is classified as ACTIVE (used directly), OPTIONAL (available but not required), or DE-SCOPED (not used, with justification).

| v3 Module | Disposition | Justification |
|-----------|------------|---------------|
| `intent.py` | ACTIVE | Gate G0 agent uses for intent extraction |
| `design.py` | ACTIVE | Gate G1-G3 agent uses for design decisions |
| `questions.py` | ACTIVE | Agent loop uses for clarifying questions |
| `assumptions.py` | ACTIVE | Agent loop records assumptions with LLM |
| `acceptance.py` | ACTIVE | Gate G2 agent builds acceptance matrix |
| `generation.py` | ACTIVE | Validation function checks agent output against packet specs |
| `agent_packets.py` | ACTIVE | Scoped execution boundaries for tool calls |
| `agent_dispatch.py` | DE-SCOPED | Replaced by agent_loop.py. The dispatch module was a single LLM call; the loop is iterative with tool use. Code may be deleted after parity proven. |
| `gate_review.py` | ACTIVE | All 5 verdicts used by GateReviewCard.tsx |
| `security_gate.py` | ACTIVE | Injection scan on every write_file tool call |
| `validation.py` | ACTIVE | Build/test/lint after code generation |
| `proof.py` | ACTIVE | Runtime + UX proof at G5 |
| `deploy.py` | ACTIVE | Deploy decision at G5 |
| `closeout.py` | ACTIVE | Evidence-based summary at delivery end |
| `lifecycle.py` | ACTIVE | Delivery state machine tracks phases |
| `repair_loop.py` | ACTIVE | Bounded rework on validation failure |
| `design_preview.py` | ACTIVE | LLM-generated preview shown in chat iframe |
| `harness.py` | ACTIVE (modified) | Provider protocol kept, implementations replaced by LiteLLM adapter |
| `wave_engine.py` | ACTIVE | Gate state machine, inspect(), scope-drift |
| `orchestrator.py` | ACTIVE | Pre-write guard, audit trail, auto-commit |
| `sign.py` | ACTIVE | Gate signing API (INV-3: the ONLY signing path) |
| `validate_cmd.py` | ACTIVE | 12 Layer 1 validators |
| `security.py` | ACTIVE | OWASP/STRIDE, canary tokens |
| `data_privacy.py` | ACTIVE | GDPR export/purge |
| `_bundle/` | ACTIVE | Gate agent system prompts, constitution, standards |
| `enforcement.rs` | ACTIVE | 13 runtime rules checked on every tool call |
| `keychain.rs` | ACTIVE | OS credential storage for all providers |
| `sidecar.rs` | ACTIVE (modified) | Event routing for agent loop events |
| `governance.rs` | ACTIVE | Audit trail, gate state persistence |
| `DeliverView.tsx` | OPTIONAL (feature-flagged) | Hidden behind feature flag until chat parity proven. Deleted only after T33-T37 pass. |
| `TerminalView.tsx` | OPTIONAL (feature-flagged) | Hidden behind feature flag until governed shell parity proven. Deleted only after T10 passes. |

---

## Implementation Phases

### Phase 1: UI/UX Revamp

The chat must look and feel as powerful as Claude Code's interface BEFORE we wire the agent loop.

DeliverView and TerminalView are NOT deleted. They are hidden behind a feature flag (`v4_agent_loop`). They remain accessible via the flag until the new chat proves parity.

| Step | What | Files | Delivers |
|------|------|-------|----------|
| 1.1 | Feature flag: `v4_agent_loop`. When on: hide Deliver + Terminal tabs. When off: everything works as v3. | Toolbar.tsx, app.tsx, state.ts | Safe rollback |
| 1.2 | Chat redesign: full-width, streaming text, markdown rendering, code blocks with syntax highlighting, copy button | BuildView.tsx, styles.css | Professional chat |
| 1.3 | Tool call bubbles: compact cards showing file read/write/edit, command execution, search -- with spinner then checkmark | ToolCallBubble.tsx (new) | User sees agent working |
| 1.4 | File diff bubbles: inline green/red diffs for edits, collapsible full-file view | FileDiffBubble.tsx (new) | User sees what changed |
| 1.5 | Gate review cards: ALL 5 verdict buttons (Approve, Approve with Conditions, Request Changes, Reject, Waive). Each verdict has policy-backed behavior. Feedback text field for changes/reject/waive justification. | GateReviewCard.tsx (new) | User reviews at gates with full verdict model |
| 1.6 | Design preview inline: iframe in chat bubble for G3 design review, "pop out to Preview" button | ChatPreviewBubble.tsx (new) | User sees design in conversation |
| 1.7 | Command input: unified input bar -- type a message OR a command. Auto-detect `/signal-*` vs natural language | chat.js, BuildView.tsx | One input, everything |
| 1.8 | Sidebar cleanup: Projects + Files + Gov (with TestDebtPanel). No clutter | Sidebar.tsx | Clean workspace view |
| 1.9 | Streaming indicator: typing animation while agent responds, tool-use indicator while agent works | state.ts, BuildView.tsx | User knows agent is working |
| 1.10 | Mobile-responsive: chat and sidebar work on narrow screens | styles.css | Works everywhere |

### Phase 2: LiteLLM + Provider Adapter + Agent Loop

| Step | What | Files | Delivers |
|------|------|-------|----------|
| 2.1 | Install LiteLLM in sidecar bundle, add to requirements | bundle-sidecar.ps1, bundle-sidecar.sh | Any-provider library |
| 2.2 | Provider adapter contract: `ProviderAdapter` protocol with capability detection (streaming, tool_calls, json_schema, context_length, model_list, auth_errors). LiteLLM sits behind this adapter. | provider_adapter.py (new) | Capability-aware routing |
| 2.3 | Replace harness providers with adapter. Keep LLMProvider Protocol for backward compat. TestProvider unchanged. | harness.py | Unified provider with capability detection |
| 2.4 | Agent loop core -- `agent_loop.py`: message --> adapter --> tool calls --> governance --> execute --> loop. Persisted run state (INV-5). | agent_loop.py (new) | The runtime |
| 2.5 | Tool definitions with strict execution rules: typed path allowlists (not globs), command policy (allowlist + denylist), timeouts (default 30s read, 120s command), cancellation support, secret redaction on stdout/stderr, no direct governance file edits | agent_loop.py | Governed tool execution |
| 2.6 | Audit ledger: every tool call (allowed or denied) logged to `.signalos/agent-runs/<run-id>/tool-calls.jsonl` with timestamp, tool name, args, result, governance decision, duration | agent_loop.py | Full traceability |
| 2.7 | Gate detection: after each tool round, query wave_engine.inspect(). Pause at gate boundaries. Gate signing calls `sign.py:sign_artifact()` only (INV-3). | agent_loop.py | Gate checkpoints |
| 2.8 | Verdict handling: all 5 verdicts with policy. Approve --> sign + advance. Conditions --> sign + log conditions. Changes --> bounded rework (max 3). Reject --> bounded restart (max 2). Waive --> explicit justification required, audit logged, cannot satisfy mandatory proof (INV-1). | agent_loop.py + gate_review.py | Full verdict model |
| 2.9 | Security scan: injection scan on every write_file content, secret redaction on run_command output, PII detection on generated content | agent_loop.py + security_gate.py | Safe output |
| 2.10 | Run state persistence: `.signalos/agent-runs/<run-id>/state.json` with conversation history, current gate, tool-call count, resume checkpoint. Idempotent tool execution (re-running a completed write is a no-op). | agent_loop.py | Crash recovery (INV-5) |
| 2.11 | Graceful degradation: if provider doesn't support tool calling, fall back to text-only mode (agent describes what to do, user executes manually). No fake tool calls. | provider_adapter.py, agent_loop.py | Works with all providers |

### Phase 3: Wire Loop to UI

| Step | What | Files | Delivers |
|------|------|-------|----------|
| 3.1 | IPC command `agent:run`: sidecar handler that runs AgentLoop and streams events | signalos_ipc_server.py | Backend ready |
| 3.2 | IPC command `agent:verdict`: sends user verdict to paused loop. Validates verdict against gate_review.py policy before accepting. | signalos_ipc_server.py | Gate control |
| 3.3 | IPC command `agent:cancel`: cancels a running agent loop. Persists state for potential resume. | signalos_ipc_server.py | User can stop |
| 3.4 | IPC command `agent:resume`: resumes a previously paused/crashed run from persisted state | signalos_ipc_server.py | Crash recovery |
| 3.5 | Event routing in Rust: parse agent events from sidecar stdout, emit as Tauri `agent:event` | sidecar.rs | Frontend receives events |
| 3.6 | Frontend event handler: subscribe to `agent:event`, render as chat bubbles (text, tool calls, gate reviews, diffs) | agentEvents.ts (new), chat.js | Live agent conversation |
| 3.7 | Chat sendMsg switch: detect if message should go to agent loop vs direct sidecar command | chat.js | Unified input works |

### Phase 4: Parity Proof and Cleanup

DeliverView and TerminalView are only removed AFTER parity is proven by tests.

| Step | What | Files | Delivers |
|------|------|-------|----------|
| 4.1 | Parity test: delivery flow works entirely in chat (T33-T37 pass) | test suite | Prove Deliver parity |
| 4.2 | Parity test: real commands work in chat (T10 passes) | test suite | Prove Terminal parity |
| 4.3 | Remove feature flag, delete DeliverView.tsx and TerminalView.tsx | app.tsx, Toolbar.tsx | Clean codebase |
| 4.4 | Preview tab integration: auto-open Preview when agent starts dev server | PreviewView.tsx, agentEvents.ts | Seamless preview |

### Phase 5: Full E2E Validation

All tests in the test matrix must pass. No exceptions. No relaxing.

---

## Governance Module Consumption Map

| v3 Module | Consumed By | How |
|-----------|-------------|-----|
| `intent.py`, `design.py`, `questions.py`, `assumptions.py` | Gate agents G0-G1 | LLM calls these via agent system prompts |
| `acceptance.py`, `generation.py` | Gate agents G2-G4 | Acceptance matrix + output validation |
| `agent_packets.py` | Agent loop tool execution | Scoped execution boundaries |
| `gate_review.py` | GateReviewCard.tsx + agent_loop.py | All 5 verdicts with policy |
| `security_gate.py` | Agent loop governance check | Injection scan on every write |
| `validation.py` | Agent loop post-write | Build/test/lint after code generation |
| `proof.py` | Gate agent G5 | Runtime + UX proof |
| `deploy.py` | Gate agent G5 | Deploy decision |
| `closeout.py` | Gate agent G5 | Evidence summary |
| `lifecycle.py` | Agent loop state | Delivery state machine |
| `repair_loop.py` | Agent loop retry | Bounded rework on failures |
| `design_preview.py` | ChatPreviewBubble.tsx | Inline iframe preview |
| `wave_engine.py` | Agent loop gate detection | State machine + inspect |
| `orchestrator.py` | Agent loop file writing | Pre-write guard, audit |
| `sign.py` | Agent loop gate signing (INV-3) | The ONLY signing path |
| `validate_cmd.py` | Agent loop validators | 12 Layer 1 validators |
| `security.py` | Agent loop governance | OWASP/STRIDE checks |
| `data_privacy.py` | Agent loop governance | GDPR export/purge |
| `enforcement.rs` | Agent loop trust tiers | 13 runtime rules |
| `keychain.rs` | Provider adapter setup | OS credential storage |
| `sidecar.rs` | Agent event streaming | Sidecar spawn + event routing |
| `governance.rs` | Audit trail | Gate state + audit append |
| `_bundle/ (287 files)` | Gate agent system prompts | Constitution, contracts, standards |

---

## Test Matrix

Every row must pass before v4 ships. No exceptions. No relaxing.

### Provider Support (T01-T06)

| # | Test | What it proves | How to verify | CI? |
|---|------|---------------|--------------|-----|
| T01 | Anthropic key --> agent responds | Claude works | Set ANTHROPIC_API_KEY, send message, get response | Optional smoke |
| T02 | OpenAI key --> agent responds | GPT works | Set OPENAI_API_KEY, send message, get response | Optional smoke |
| T03 | Gemini key --> agent responds | Gemini works | Set GEMINI_API_KEY, send message, get response | Optional smoke |
| T04 | Ollama --> agent responds | Local LLM works | Start Ollama, send message, get response | Optional smoke |
| T05 | No key --> honest error | No fake output | No env vars, verify "connect a provider" message | Required CI |
| T06 | Switch provider mid-session | Multi-provider | Change key in vault, next message uses new provider | Optional smoke |
| T07 | Provider without tool-call support --> text-only mode | Graceful degradation | Use a provider that doesn't support tools, verify text-only | Required CI (TestProvider) |
| T08 | Capability detection returns correct flags | Adapter works | Query adapter for streaming/tools/schema, verify per provider | Required CI |

### Tool Use (T09-T15)

| # | Test | What it proves | How to verify | CI? |
|---|------|---------------|--------------|-----|
| T09 | Agent reads a file | read_file works | Ask "what's in package.json?", verify correct content | Required CI (mock) |
| T10 | Agent writes a file | write_file works | Ask "create hello.txt with hello", verify file exists | Required CI (mock) |
| T11 | Agent edits a file | edit_file works | Ask "change the title in App.tsx", verify diff | Required CI (mock) |
| T12 | Agent runs a command | run_command works | Ask "run npm test", verify output shown | Required CI (mock) |
| T13 | Agent searches files | search_files works | Ask "find all .tsx files", verify list | Required CI (mock) |
| T14 | File diff shown in chat | UI renders diff | After T11, verify green/red diff in chat | Required CI (component) |
| T15 | Tool call shown in chat | UI renders tool use | During T09, verify "Reading package.json..." bubble | Required CI (component) |

### Governance (T16-T25)

| # | Test | What it proves | How to verify | CI? |
|---|------|---------------|--------------|-----|
| T16 | Write to .env blocked | Forbidden paths | Ask agent to write .env, verify denied + denial shown | Required CI |
| T17 | Write to .signalos/ blocked | Forbidden paths | Ask agent to modify audit trail, verify denied | Required CI |
| T18 | rm -rf blocked | Forbidden actions | Ask agent to delete everything, verify denied | Required CI |
| T19 | git push --force blocked | Forbidden actions | Ask agent to force push, verify denied | Required CI |
| T20 | Command timeout enforced | Timeout policy | Run a command that hangs, verify killed after timeout | Required CI |
| T21 | Secret redacted in command output | Secret redaction | Run a command that prints an API key, verify redacted | Required CI |
| T22 | Denial shown to user | UX for denials | After T16, verify "Permission denied" in chat | Required CI |
| T23 | Denial logged in audit ledger | Audit trail | After T16, check tool-calls.jsonl has the denial entry | Required CI |
| T24 | Injection scan on write | Security gate | Agent writes code with eval(), verify security warning | Required CI |
| T25 | Trust tier enforced | T2 ceiling | Agent tries to touch auth code, verify tier block | Required CI |

### Gates (T26-T38)

| # | Test | What it proves | How to verify | CI? |
|---|------|---------------|--------------|-----|
| T26 | G0 pause shows intent | Gate checkpoint | Start delivery, verify intent review card in chat | Required CI (mock) |
| T27 | G1 pause shows direction | Gate checkpoint | After G0 approve, verify belief review card | Required CI (mock) |
| T28 | G2 pause shows plan | Gate checkpoint | After G1, verify plan card with tasks | Required CI (mock) |
| T29 | G3 pause shows design preview | Gate checkpoint | After G2, verify iframe preview in chat | Required CI (mock) |
| T30 | G4 pause shows build evidence | Gate checkpoint | After G3, verify test/build results | Required CI (mock) |
| T31 | G5 pause shows closeout | Gate checkpoint | After G4, verify closeout card | Required CI (mock) |
| T32 | APPROVE advances to next gate via sign.py API | INV-3 | Click Approve on G0, verify sign_artifact called, G1 starts | Required CI |
| T33 | APPROVE-WITH-CONDITIONS signs + logs conditions | Verdict policy | Click Conditions, enter text, verify signed + conditions in audit | Required CI |
| T34 | REQUEST CHANGES triggers bounded rework | Verdict policy | Click Changes with feedback, verify agent reworks, max 3 | Required CI |
| T35 | REJECT stops delivery | Verdict policy | Click Reject, verify agent stops, max 2 | Required CI |
| T36 | WAIVE requires justification | Verdict policy | Click Waive, verify justification required, audit logged | Required CI |
| T37 | WAIVE cannot satisfy mandatory proof (INV-1) | No-skip policy | Waive G5, verify delivery cannot close as "ready" | Required CI |
| T38 | Gate signed in audit trail | Audit | After T32, check AUDIT_TRAIL.jsonl has gate signature | Required CI |

### Full Delivery E2E (T39-T44)

| # | Test | What it proves | How to verify | CI? |
|---|------|---------------|--------------|-----|
| T39 | "Build task management" --> running app | Full flow (INV-2) | Type prompt, approve all gates, verify: product repo + tests + runtime proof + UX proof + closeout | Optional smoke (needs live provider) |
| T40 | "Build medical records HIPAA" --> GDPR flagged | Compliance | Verify security gate flags HIPAA + PII | Required CI (mock) |
| T41 | Vague prompt --> smart questions asked | LLM questions | Type "build something", verify agent asks domain questions | Optional smoke |
| T42 | User changes design mid-flow | Interactive | At G3, say "change to blue", verify design updates | Optional smoke |
| T43 | Closeout honest when partial | Honesty | Interrupt at G4, verify closeout says "partial" not "ready" | Required CI |
| T44 | Sidecar crash --> run resumes from checkpoint | INV-5 | Kill sidecar during delivery, restart, verify resumes | Required CI |

### UI/UX (T45-T52)

| # | Test | What it proves | How to verify | CI? |
|---|------|---------------|--------------|-----|
| T45 | Streaming text renders live | Streaming | Send message, verify text appears token by token | Required CI (component) |
| T46 | Markdown renders in chat | Rich text | Agent responds with headers/code/lists, verify rendering | Required CI (component) |
| T47 | Code blocks have syntax highlighting | Code UX | Agent shows code, verify highlighted | Required CI (component) |
| T48 | Chat scrolls to bottom on new message | Scroll UX | New messages auto-scroll | Required CI (component) |
| T49 | Workspace switcher works | Multi-project | Switch workspace, verify context changes | Required CI (component) |
| T50 | Command palette opens with / | Commands | Type /, verify palette shows | Required CI (component) |
| T51 | Mobile responsive | Layout | Resize to 375px, verify no overlap | Manual |
| T52 | No TestDebtPanel overlap | Fixed bug | Open chat, verify no floating panel | Required CI (component) |

### Regression (T53-T57)

| # | Test | What it proves | How to verify | CI? |
|---|------|---------------|--------------|-----|
| T53 | All Python tests pass | No regression | `python -m pytest python/ --ignore=live_e2e` | Required CI |
| T54 | TypeScript compiles clean | No regression | `npx tsc --noEmit` | Required CI |
| T55 | Frontend tests pass | No regression | `npx vitest run` | Required CI |
| T56 | CI all green (test-automation + Smoke + Pages) | CI | Push and verify | Required CI |
| T57 | Release workflow green (all platforms) | Release | Tag and verify installers | Required (Release) |

**57 tests. 45 required in CI. 11 optional smoke (live provider). 1 manual.**

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| LiteLLM tool calling varies by provider | Agent loop breaks on some providers | Capability detection adapter (T07-T08); fallback to text-only |
| Conversation context window overflow | Long sessions crash | Context compression; persist history to disk |
| Gate detection timing | Agent skips a gate | wave_engine.inspect() after EVERY tool round; test T26-T31 |
| Streaming performance | UI freezes on fast token output | Batch text-delta events (debounce 50ms); test T45 |
| Sidecar crash during agent loop | Lost state | Persisted run state + resume (INV-5); test T44 |
| LiteLLM bundle size | PyInstaller binary larger | Early ready line (already shipped); monitor cold-start |
| Agent forges gate signature | Governance bypass | INV-3 enforced: only sign.py API path; test T32 |
| Silent skip passes as proof | False readiness | INV-1: no-silent-skip policy; test T37 |

---

## Dependencies Between Steps

```
Phase 1 (UI)         Phase 2 (Runtime)         Phase 3 (Wire)       Phase 4 (Parity)
  1.1 (flag) --+
  1.2 ---------+
  1.3 ---------+---> 3.6 (events render)
  1.4 ---------+
  1.5 ---------+
  1.6 ---------+
  1.7 ---------+---> 3.7 (input routing)
  1.8
  1.9 ---------+---> 3.6
  1.10

               2.1 -> 2.2 -> 2.3 -> 2.4 -> 2.5 -> 2.6 -> 2.7 -> 2.8 -> 2.9 -> 2.10 -> 2.11
                                      |
                                      v
                                     3.1 -> 3.2 -> 3.3 -> 3.4
                                      |
                                      v
                                     3.5 -> 3.6 -> 3.7
                                                     |
                                                     v
                                                    4.1 (prove Deliver parity)
                                                    4.2 (prove Terminal parity)
                                                     |
                                                     v
                                                    4.3 (delete only after parity)
                                                    4.4
                                                     |
                                                     v
                                              Phase 5 (57-test validation)
```

Phase 1 and Phase 2 run in parallel. Phase 3 connects them. Phase 4 proves parity before deleting. Phase 5 validates everything.

---

## Final Layout (v4)

```
+-------------------------------------------------------------------+
| Toolbar: [WorkspaceSwitcher] [Build] [Preview] [Dashboard]        |
+---------------+---------------------------------------------------+
|               |                                                   |
|   Sidebar     |   Build (chat -- the only surface)                |
|   +---------+ |   +-------------------------------------------+  |
|   |Projects | |   | User: "Build task management for my team"  |  |
|   |Files    | |   |                                            |  |
|   |Gov      | |   | Agent: "I understand. Let me extract the   |  |
|   | Gates   | |   |  intent..." [reading workspace...]         |  |
|   | Debt    | |   |                                            |  |
|   +---------+ |   | G0 Review: [intent summary card]           |  |
|               |   | [Approve] [Conditions] [Changes] [Reject]  |  |
|   Vault       |   | [Waive]                                    |  |
|   Settings    |   |                                            |  |
|               |   | User: "Approve"                            |  |
|               |   |                                            |  |
|               |   | Agent: "Building plan..."                  |  |
|               |   | [writing src/components/Task.tsx] +        |  |
|               |   | [diff: +42 lines] [checkmark]             |  |
|               |   |                                            |  |
|               |   | G3 Review: [design preview iframe]         |  |
|               |   | [Approve] [Conditions] [Changes] [Reject]  |  |
|               |   | [Waive]                                    |  |
|               |   +-------------------------------------------+  |
|               |   [input: type a message or /command...]         |
+---------------+---------------------------------------------------+
```

3 tabs. 1 chat surface. Any provider. 5 verdicts. Full governance. 57 tests. No exceptions.
