# SignalOS v4 — The Governed Agent Loop

## Goal

Build SignalOS v4 — the governed agent loop. Replace the current dumb chat relay and fake terminal with a real provider-agnostic agent runtime powered by LiteLLM. The agent uses tool calling (file ops, shell, search) natively from any provider. SignalOS's entire governance layer (6 gates, 12 validators, 13 runtime rules, 287 governance files, trust tiers, security scanning, audit trail, 5-verdict gate review, bounded rework, acceptance criteria, honest closeout) wraps every agent action. The agent works between gates and pauses at every gate for user review. The user talks naturally in chat — no separate Deliver page, no wizard, no fake terminal. One conversation surface. Any AI provider. The user is the client. The agents are the team. SignalOS is the software house.

Every module built in v3 must be used — nothing wasted, nothing bypassed.

---

## Architecture

```
User chat (single surface)
    |
    v
Chat runtime (THE NEW PIECE)
    |--- Send to LLM (any provider via LiteLLM)
    |--- LLM responds / requests tool calls
    |--- CHECK: is this a gate boundary? --> pause, show to user
    |--- CHECK: is this action allowed? --> governance layer
    |--- Execute allowed actions
    |--- Loop until LLM done or gate reached
    |
    v
Gate checkpoint --> user reviews --> verdict (approve / change / reject)
    |
    v
Continue or rework
```

### What exists (v3 -- must ALL be used)

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

## Implementation Phases

### Phase 1: UI/UX Revamp

The chat must look and feel as powerful as Claude Code's interface BEFORE we wire the agent loop.

| Step | What | Files | Delivers |
|------|------|-------|----------|
| 1.1 | Remove Deliver tab, Terminal tab from toolbar. 3 tabs: Build, Preview, Dashboard | Toolbar.tsx, app.tsx | Clean navigation |
| 1.2 | Chat redesign: full-width, streaming text, markdown rendering, code blocks with syntax highlighting, copy button | BuildView.tsx, styles.css | Professional chat |
| 1.3 | Tool call bubbles: compact cards showing file read/write/edit, command execution, search -- with spinner then checkmark | ToolCallBubble.tsx (new) | User sees agent working |
| 1.4 | File diff bubbles: inline green/red diffs for edits, collapsible full-file view | FileDiffBubble.tsx (new) | User sees what changed |
| 1.5 | Gate review cards: gate artifact preview, 3 verdict buttons (Approve, Request Changes, Reject), feedback text field | GateReviewCard.tsx (new) | User reviews at gates |
| 1.6 | Design preview inline: iframe in chat bubble for G3 design review, "pop out to Preview" button | ChatPreviewBubble.tsx (new) | User sees design in conversation |
| 1.7 | Command input: unified input bar -- type a message OR a command. Auto-detect `/signal-*` vs natural language | chat.js, BuildView.tsx | One input, everything |
| 1.8 | Sidebar cleanup: Projects + Files + Gov (with TestDebtPanel). No clutter | Sidebar.tsx | Clean workspace view |
| 1.9 | Streaming indicator: typing animation while agent responds, tool-use indicator while agent works | state.ts, BuildView.tsx | User knows agent is working |
| 1.10 | Mobile-responsive: chat and sidebar work on narrow screens | styles.css | Works everywhere |

### Phase 2: LiteLLM + Agent Loop

| Step | What | Files | Delivers |
|------|------|-------|----------|
| 2.1 | Install LiteLLM in sidecar bundle, add to requirements | bundle-sidecar.ps1, bundle-sidecar.sh | Any-provider support |
| 2.2 | Replace harness providers with LiteLLMProvider. Keep Protocol, TestProvider. Add `call_with_tools()` | harness.py | Unified provider |
| 2.3 | Agent loop core -- `agent_loop.py`: message --> LLM --> tool calls --> governance check --> execute --> loop | agent_loop.py (new) | The runtime |
| 2.4 | Tool definitions: read_file, write_file, edit_file, run_command, search_files, list_directory. Provider-agnostic format | agent_loop.py | Agent capabilities |
| 2.5 | Governance integration: every tool call checked against forbidden paths/actions, trust tier, audit logged | agent_loop.py | Governed execution |
| 2.6 | Gate detection: after each tool round, query wave_engine.inspect(). Pause at gate boundaries | agent_loop.py | Gate checkpoints |
| 2.7 | Verdict handling: resume with approve/request-changes/reject. Bounded rework (max 3 changes, max 2 rejects) | agent_loop.py + gate_review.py | User control |
| 2.8 | Security scan: injection scan on every write_file, run_command output | agent_loop.py + security_gate.py | Safe output |

### Phase 3: Wire Loop to UI

| Step | What | Files | Delivers |
|------|------|-------|----------|
| 3.1 | IPC command `agent:run`: sidecar handler that runs AgentLoop and streams events | signalos_ipc_server.py | Backend ready |
| 3.2 | IPC command `agent:verdict`: sends user verdict to paused loop | signalos_ipc_server.py | Gate control |
| 3.3 | Event routing in Rust: parse agent events from sidecar stdout, emit as Tauri `agent:event` | sidecar.rs | Frontend receives events |
| 3.4 | Frontend event handler: subscribe to `agent:event`, render as chat bubbles (text, tool calls, gate reviews, diffs) | agentEvents.ts (new), chat.js | Live agent conversation |
| 3.5 | Chat sendMsg switch: detect if message should go to agent loop vs direct sidecar command | chat.js | Unified input works |

### Phase 4: Merge and Cleanup

| Step | What | Files | Delivers |
|------|------|-------|----------|
| 4.1 | Delivery flow in chat: user describes product --> agent extracts intent --> gate reviews --> builds --> closeout. All in conversation | chat.js, agent_loop.py | No more Deliver wizard |
| 4.2 | Terminal becomes governed shell: real commands through agent loop's run_command | TerminalView.tsx merged into chat | No more fake terminal |
| 4.3 | Delete DeliverView.tsx, TerminalView.tsx: remove dead code | app.tsx, Toolbar.tsx | Clean codebase |
| 4.4 | Preview tab integration: auto-open Preview when agent starts dev server | PreviewView.tsx, agentEvents.ts | Seamless preview |

### Phase 5: Full E2E Validation

All 50 tests in the test matrix must pass.

---

## Governance Module Consumption Map

Every v3 governance module is consumed by v4:

| v3 Module | Consumed By | How |
|-----------|-------------|-----|
| `intent.py`, `design.py`, `questions.py`, `assumptions.py` | Gate agents G0-G1 | LLM calls these via agent system prompts |
| `acceptance.py`, `generation.py` | Gate agents G2-G4 | Plan/build artifact generation |
| `agent_packets.py`, `agent_dispatch.py` | Agent loop tool execution | Scoped execution boundaries |
| `gate_review.py` | GateReviewCard.tsx | 5 verdicts rendered as buttons |
| `security_gate.py` | Agent loop governance check | Injection scan on every tool call |
| `validation.py` | Agent loop post-write | Build/test/lint after writes |
| `proof.py` | Gate agent G5 | Runtime + UX proof |
| `deploy.py` | Gate agent G5 | Deploy decision |
| `closeout.py` | Gate agent G5 | Evidence summary |
| `lifecycle.py` | Agent loop state | Delivery state machine |
| `repair_loop.py` | Agent loop retry | Bounded rework on failures |
| `design_preview.py` | Phase 1 step 1.6 | Inline iframe preview |
| `wave_engine.py` | Agent loop gate detection | State machine + inspect |
| `orchestrator.py` | Agent loop file writing | Pre-write guard, audit |
| `sign.py` | Agent loop gate signing | Multi-party signatures |
| `validate_cmd.py` | Agent loop validators | 12 Layer 1 validators |
| `security.py` | Agent loop governance | OWASP/STRIDE checks |
| `data_privacy.py` | Agent loop governance | GDPR export/purge |
| `enforcement.rs` | Agent loop trust tiers | 13 runtime rules |
| `keychain.rs` | LiteLLM provider setup | OS credential storage |
| `sidecar.rs` | Agent event streaming | Sidecar spawn + event routing |
| `governance.rs` | Audit trail | Gate state + audit append |
| `_bundle/ (287 files)` | Gate agent system prompts | Constitution, contracts, standards |

---

## Test Matrix

Every row must pass before v4 ships. No exceptions. No relaxing.

### Provider Support (T01-T06)

| # | Test | What it proves | How to verify |
|---|------|---------------|--------------|
| T01 | Anthropic key --> agent responds | Claude works | Set ANTHROPIC_API_KEY, send message, get response |
| T02 | OpenAI key --> agent responds | GPT works | Set OPENAI_API_KEY, send message, get response |
| T03 | Gemini key --> agent responds | Gemini works | Set GEMINI_API_KEY, send message, get response |
| T04 | Ollama --> agent responds | Local LLM works | Start Ollama, send message, get response |
| T05 | No key --> honest error | No fake output | No env vars, verify "connect a provider" message |
| T06 | Switch provider mid-session | Multi-provider | Change key in vault, next message uses new provider |

### Tool Use (T07-T13)

| # | Test | What it proves | How to verify |
|---|------|---------------|--------------|
| T07 | Agent reads a file | read_file works | Ask "what's in package.json?", verify correct content |
| T08 | Agent writes a file | write_file works | Ask "create hello.txt with hello", verify file exists |
| T09 | Agent edits a file | edit_file works | Ask "change the title in App.tsx", verify diff |
| T10 | Agent runs a command | run_command works | Ask "run npm test", verify output shown |
| T11 | Agent searches files | search_files works | Ask "find all .tsx files", verify list |
| T12 | File diff shown in chat | UI renders diff | After T09, verify green/red diff in chat |
| T13 | Tool call shown in chat | UI renders tool use | During T07, verify "Reading package.json..." bubble |

### Governance (T14-T21)

| # | Test | What it proves | How to verify |
|---|------|---------------|--------------|
| T14 | Write to .env blocked | Forbidden paths | Ask agent to write .env, verify denied + denial shown |
| T15 | Write to .signalos/ blocked | Forbidden paths | Ask agent to modify audit trail, verify denied |
| T16 | rm -rf blocked | Forbidden actions | Ask agent to delete everything, verify denied |
| T17 | git push --force blocked | Forbidden actions | Ask agent to force push, verify denied |
| T18 | Denial shown to user | UX for denials | After T14, verify "Permission denied" in chat |
| T19 | Denial logged in audit | Audit trail | After T14, check AUDIT_TRAIL.jsonl has the denial |
| T20 | Injection scan on write | Security gate | Agent writes code with eval(), verify security warning |
| T21 | Trust tier enforced | T2 ceiling | Agent tries to touch auth code, verify tier block |

### Gates (T22-T32)

| # | Test | What it proves | How to verify |
|---|------|---------------|--------------|
| T22 | G0 pause shows intent | Gate checkpoint | Start delivery, verify intent review card in chat |
| T23 | G1 pause shows direction | Gate checkpoint | After G0 approve, verify belief review card |
| T24 | G2 pause shows plan | Gate checkpoint | After G1, verify plan card with tasks |
| T25 | G3 pause shows design preview | Gate checkpoint | After G2, verify iframe preview in chat |
| T26 | G4 pause shows build evidence | Gate checkpoint | After G3, verify test/build results |
| T27 | G5 pause shows closeout | Gate checkpoint | After G4, verify closeout card |
| T28 | Approve advances to next gate | Verdict works | Click Approve on G0, verify G1 starts |
| T29 | Request Changes triggers rework | Verdict works | Click "Change" with feedback, verify agent reworks |
| T30 | Reject stops delivery | Verdict works | Click Reject, verify agent stops |
| T31 | Max 3 changes enforced | Bounded rework | Request changes 4 times, verify escalation |
| T32 | Gate signed in audit trail | Audit | After T28, check audit trail has gate signature |

### Full Delivery E2E (T33-T37)

| # | Test | What it proves | How to verify |
|---|------|---------------|--------------|
| T33 | "Build task management" --> running app | Full flow | Type prompt, approve all gates, verify app runs in Preview |
| T34 | "Build medical records HIPAA" --> GDPR flagged | Compliance | Verify security gate flags HIPAA + PII |
| T35 | Vague prompt --> smart questions asked | LLM questions | Type "build something", verify agent asks domain questions |
| T36 | User changes design mid-flow | Interactive | At G3, say "change to blue", verify design updates |
| T37 | Closeout honest when partial | Honesty | Interrupt at G4, verify closeout says "partial" not "ready" |

### UI/UX (T38-T45)

| # | Test | What it proves | How to verify |
|---|------|---------------|--------------|
| T38 | Streaming text renders live | Streaming | Send message, verify text appears token by token |
| T39 | Markdown renders in chat | Rich text | Agent responds with headers/code/lists, verify rendering |
| T40 | Code blocks have syntax highlighting | Code UX | Agent shows code, verify highlighted |
| T41 | Chat scrolls to bottom on new message | Scroll UX | New messages auto-scroll |
| T42 | Workspace switcher works | Multi-project | Switch workspace, verify context changes |
| T43 | Command palette opens with / | Commands | Type /, verify palette shows |
| T44 | Mobile responsive | Layout | Resize to 375px, verify no overlap |
| T45 | No TestDebtPanel overlap | Fixed bug | Open chat, verify no floating panel |

### Regression (T46-T50)

| # | Test | What it proves | How to verify |
|---|------|---------------|--------------|
| T46 | All Python tests pass | No regression | `python -m pytest python/ --ignore=live_e2e` |
| T47 | TypeScript compiles clean | No regression | `npx tsc --noEmit` |
| T48 | Frontend tests pass | No regression | `npx vitest run` |
| T49 | CI all green (test-automation + Smoke + Pages) | CI | Push and verify |
| T50 | Release workflow green (all platforms) | Release | Tag and verify installers |

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| LiteLLM tool calling varies by provider | Agent loop breaks on some providers | Test T01-T04 cover all major providers; fallback to text-only mode |
| Conversation context window overflow | Long sessions crash | Implement context compression (summarize older messages) |
| Gate detection timing | Agent skips a gate | Wave engine inspect() is authoritative; check after EVERY tool round |
| Streaming performance | UI freezes on fast token output | Batch text-delta events (debounce 50ms) |
| Sidecar restart during agent loop | Lost conversation state | Persist conversation to .signalos/sessions/; resume on restart |
| LiteLLM bundle size | PyInstaller binary too large | --onefile cold-start already addressed (early ready line); monitor |

---

## Dependencies Between Steps

```
Phase 1 (UI)     Phase 2 (Runtime)     Phase 3 (Wire)     Phase 4 (Merge)
  1.1 ----+
  1.2 ----+
  1.3 ----+----> 3.4 (events render in new bubbles)
  1.4 ----+
  1.5 ----+
  1.6 ----+
  1.7 ----+----> 3.5 (input routing)
  1.8
  1.9 ----+----> 3.4
  1.10

           2.1 --> 2.2 --> 2.3 --> 2.4 --> 2.5 --> 2.6 --> 2.7 --> 2.8
                                    |
                                    v
                                   3.1 --> 3.2
                                    |
                                    v
                                   3.3 --> 3.4 --> 3.5
                                                    |
                                                    v
                                                   4.1 --> 4.2 --> 4.3 --> 4.4
                                                                            |
                                                                            v
                                                                    Phase 5 (Validation)
```

Phase 1 and Phase 2 can run in parallel. Phase 3 connects them. Phase 4 cleans up. Phase 5 validates everything.

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
|               |   | [Approve] [Request Changes] [Reject]       |  |
|   Vault       |   |                                            |  |
|   Settings    |   | User: "Approve"                            |  |
|               |   |                                            |  |
|               |   | Agent: "Building plan..."                  |  |
|               |   | [writing src/components/Task.tsx] +        |  |
|               |   | [diff: +42 lines] [checkmark]             |  |
|               |   |                                            |  |
|               |   | G3 Review: [design preview iframe]         |  |
|               |   | [Approve] [Request Changes] [Reject]       |  |
|               |   +-------------------------------------------+  |
|               |   [input: type a message or /command...]         |
+---------------+---------------------------------------------------+
```

3 tabs. 1 chat surface. Any provider. Full governance. 50 tests. No exceptions.
