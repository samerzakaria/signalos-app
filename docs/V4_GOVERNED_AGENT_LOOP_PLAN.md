# Foundry by SignalOS -- v4 Plan

**Your AI agentic product team, governed from idea to launch.**

## Product Identity

### Brand

- **Wordmark:** Foundry
- **Mark:** Approved dark-frame negative-space F + governed signal
- **Tagline:** Your AI agentic product team, governed from idea to launch.
- **Full name:** Foundry by SignalOS

### Visual Identity

- **Background:** Off-white (#f6f5f2)
- **Text:** Charcoal (#1e1d1a)
- **Trust color:** Deep blue (#2457d6)
- **Active build accent:** Signal orange (#d97845)
- **Style:** Premium software studio -- not hacker tool, not chatbot toy, not internal devops screen

### Visual Principles

- Calm, clean, confident
- No overlapping translucent panels
- No tiny unclear pills
- No fake terminal as primary UX
- No childish wizard
- No heavy technical dashboard on first use
- Strong spacing, clear hierarchy, readable labels
- Status colors only where they matter
- Cards only for meaningful things: approvals, evidence, project summaries
- Think: enterprise product studio + client portal

---

## Goal

Build Foundry v4 -- the governed agent loop. Replace the current chat relay and fake terminal with a real provider-agnostic agent runtime powered by LiteLLM behind a capability-detecting adapter contract. The agent uses tool calling (file ops, shell, search) natively from any provider. SignalOS's entire governance layer (6 gates, 12 validators, 12 runtime rules, 287 governance files, trust tiers, security scanning, audit trail, 5-verdict gate review, bounded rework, acceptance criteria, honest closeout) wraps every agent action. The agent works between gates and pauses at every gate for user review. The user talks naturally -- one conversation surface. Any AI provider. The user is the client. The agents are the team. Foundry is the software house.

Every v3 module is either actively used, intentionally marked optional with justification, or de-scoped with proof. No forced integration. No fake usage.

---

## Main UX Shape

The whole app is one simple flow:

### 1. New Product
User types: "I want a task management system for my team to manage tasks, workload, utilization, and KPIs."

### 2. Product Brief
Foundry explains what it understood -- in business language, not developer jargon. Entities, workflows, users, surfaces -- presented as a clear brief the client can read and approve.

### 3. Team Plan
Shows who will do what. The user sees the team, not the tools:

| Role | What they do |
|------|-------------|
| **Product Strategist** | Understands intent, asks clarifying questions |
| **Domain Analyst** | Extracts entities, workflows, acceptance criteria |
| **UX Designer** | Selects design system, creates interactive preview |
| **Solution Architect** | Plans the build, selects stack, scopes tasks |
| **Full-Stack Engineer** | Writes product code (components, API, tests) |
| **QA Lead** | Validates build, tests, mutation score |
| **Security Reviewer** | Injection scan, PII detection, compliance |
| **Release Manager** | Deploy decision, closeout, handoff |

The user does NOT manage agents manually. Foundry assigns and coordinates them. The user sees progress, not process.

### 4. Approvals
User approves decisions at meaningful points only -- not every micro-step:

- **Product scope** (G0-G1): "Is this what you want built?"
- **Design direction** (G3): "Does this look right?" (with interactive preview)
- **Launch decision** (G5): "Ready to ship?"

Everything else is the team doing their job. The user watches progress, not approves every file write.

### 5. Build Progress
Clear visible stages -- not a phase strip with internal codes:

```
Brief --> Design --> Build --> Validate --> Security --> Launch --> Handoff
```

Each stage shows: what's happening, who's working, what's done, what's next.

### 6. Evidence
Build/test/runtime/UX/security proof shown in plain language:

- "All 12 tests pass"
- "Dev server starts and responds on port 5173"
- "No injection vulnerabilities found"
- "HIPAA compliance flagged -- audit trail enabled"

Not: "BLOCK_MERGE severity, Layer 1 validator exit code 0."

### 7. Handoff
What was built, where it lives, how to run it, what passed, what still needs attention. A document the client can share with their team.

---

## Navigation

### App-level sidebar

| Item | Purpose | Dead? |
|------|---------|-------|
| **Projects** | List of products, workspace switcher | Active |
| **New Product** | Start a new delivery | Active |
| **Team** | See agent team status and activity | Active |
| **Evidence** | Cross-project evidence and proof | Active |
| **Settings** | Provider, identity, preferences | Active |
| **Help** | Guide, onboarding, support | Active |

No dead pages. Every nav item either works or is hidden.

### Inside a project

| Item | Purpose | Dead? |
|------|---------|-------|
| **Build** | The conversation -- where everything happens | Active |
| **Preview** | Live iframe of running product | Active |
| **Evidence** | Build/test/security/proof evidence for this project | Active |
| **Handoff** | What was built, how to run, what's next | Active |
| **Activity** | Audit trail, gate history, agent actions | Active |

---

## Agent Team

The agents are presented as **Foundry specialists**, not random bots or internal role codes.

| Internal role | User-facing name | Gate |
|--------------|-----------------|------|
| G0 onboarding agent | **Product Strategist** | G0 |
| G1 brainstorm agent | **Domain Analyst** | G1 |
| G3 design agent | **UX Designer** | G3 |
| G2 plan agent | **Solution Architect** | G2 |
| G4 build agent | **Full-Stack Engineer** | G4 |
| G4 test agent | **QA Lead** | G4 |
| Security agent | **Security Reviewer** | G4-G5 |
| G5 release agent | **Release Manager** | G5 |

In the chat, agent messages show the specialist name:

```
[Product Strategist] I've analyzed your request. Here's what I understand...
[UX Designer] Based on the product scope, I recommend Mantine for the UI...
[Full-Stack Engineer] Building the Task component... [writing src/components/Task.tsx]
[QA Lead] All 12 tests pass. Mutation score: 87%.
```

The user never selects, configures, or manages agents. Foundry assigns the right specialist for each phase.

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

### INV-7: Text-only mode cannot close delivery
If a provider does not support tool calling, Foundry can explain, plan, and guide in text-only mode. But delivery closure (status "ready") REQUIRES tool execution, a real product repo, executed tests, runtime proof, UX proof, and evidence closeout. Text-only mode can produce a Brief and Team Plan but cannot produce a shipped product. The closeout must honestly state "text-only mode -- no product built" if tools were never executed.

---

## Architecture Decisions

Answers to the dev team's architectural questions. Each decision is final for v4 implementation.

### Q1: Provider Protocol redesign

**Decision: New `AgentProvider` protocol alongside the frozen `LLMProvider`.**

The existing `LLMProvider.call(prompt, model) -> (text, tokens_in, tokens_out)` is frozen. It continues to serve the legacy harness path (`signalos harness call`, one-shot orchestrator commands, existing `refine_intent_with_llm` calls). No changes to it.

The agent loop uses a new `AgentProvider` protocol:

```python
class AgentProvider(Protocol):
    def chat(
        self,
        messages: list[dict],       # OpenAI-format messages array
        model: str,
        tools: list[dict] | None,   # tool definitions (provider-agnostic)
        stream: bool = False,
    ) -> AgentResponse: ...

@dataclass
class AgentResponse:
    content: str | None                    # text response (if any)
    tool_calls: list[ToolCall] | None      # tool call requests (if any)
    stop_reason: str                       # "end_turn" | "tool_use" | "max_tokens"
    usage: TokenUsage
    stream: AsyncIterator[StreamDelta] | None  # if stream=True
```

`LiteLLMAgentProvider` implements `AgentProvider` by wrapping `litellm.completion()`. It translates our tool definitions to LiteLLM's format and normalizes responses.

The `ProviderAdapter` (from the architect's feedback) wraps `LiteLLMAgentProvider` and adds capability detection:

```python
class ProviderAdapter:
    supports_streaming: bool
    supports_tool_calls: bool
    supports_json_schema: bool
    context_length: int
    model_list: list[str]

    def chat(...) -> AgentResponse  # delegates to LiteLLMAgentProvider
    def detect_capabilities(model: str) -> ProviderCapabilities
```

If `supports_tool_calls` is False, the agent loop falls back to text-only mode (INV-7: cannot close delivery).

**The old harness is NOT retired.** It stays for one-shot CLI commands. The agent loop uses the new protocol.

### Q2: Tool execution -- Python executes, Rust is governance authority

**Decision: Python executes tools. Rust is the single source of truth for governance rules. Python reads rules from Rust at loop start, not per-call.**

Flow:
1. At agent loop start, Python calls Rust IPC `get_enforcement_state()` once to get the full rule set (12 rules, forbidden paths, forbidden actions, trust tier ceiling)
2. Python caches these rules for the duration of the loop run
3. On each tool call, Python checks the cached rules (fast, no round-trip)
4. Python executes the tool (file read/write via `pathlib`, command via `subprocess`)
5. For file writes specifically, Python calls Rust IPC `validate_workspace_write()` before writing (Rust double-checks the path is within workspace and not reserved)
6. Audit entries are written by Python to the JSONL ledger (Python already owns the audit trail format)

Why not round-trip Rust for every tool call: latency. A tool-use loop can fire 20-50 tool calls per gate. Round-tripping Rust for each would add 200-500ms of IPC overhead per call.

Why not re-implement all governance in Python: governance would drift. Python reads the canonical rules from Rust at loop start. If rules change mid-run (e.g., user changes trust tier in Settings), the loop picks up new rules on the next run, not mid-run. This is acceptable because a single agent run is a bounded operation (one gate at a time).

The `validate_workspace_write()` Rust call on file writes is the safety net: even if Python's cached rules are stale, Rust blocks the actual write if the path is reserved.

**Command policy is frozen for the run's duration.** Unlike file writes (which have a Rust safety net), command execution has no secondary Rust check. If the user downgrades trust tier mid-run, the cached command allowlist remains in effect until the current loop run completes and the orchestrator starts the next one (which re-reads rules from Rust). This is acceptable because: (a) a single loop run is bounded (one gate), (b) trust-tier changes mid-build are rare, and (c) the audit ledger records every command executed so post-hoc review catches any gap. A future `validate_command()` Rust IPC can close this gap if needed.

### Q3: Frontend layer -- Preact components, chat.js is the send/receive bridge

**Decision: All new UI components are Preact `.tsx`. `chat.js` becomes a thin bridge between Preact state and the sidecar IPC.**

Today `chat.js` does too much: state management, message formatting, response parsing, provider streaming. In v4:

- **Preact owns rendering**: `BuildView.tsx` renders the conversation, `ToolCallBubble.tsx`, `FileDiffBubble.tsx`, `GateReviewCard.tsx` render their bubble types
- **Preact signals own state**: `chatBubbles`, `agentRunning`, `currentGate` etc. live in `state.ts`
- **`chat.js` owns IPC**: `sendMsg()` calls `ipc.agent.run()` and subscribes to `agent:event`. It updates Preact signals with incoming events. No rendering logic.
- **`agentEvents.ts` (new)**: subscribes to Tauri `agent:event`, maps events to signal updates. This is the bridge between sidecar events and Preact state.

Shared state flow: `chat.js:sendMsg()` --> sidecar --> events --> `agentEvents.ts` --> Preact signals --> components re-render.

No component reads from `chat.js` directly. All state flows through signals.

### Q4: Agent loop vs orchestrator -- one loop per gate, orchestrator selects the prompt

**Decision: Each gate is one agent-loop run with a gate-specific system prompt. The orchestrator selects which gate to run and provides the system prompt. The loop executes it.**

Concretely:
1. `orchestrator.py` determines the current gate (e.g., G2 -- plan)
2. Orchestrator calls `agent_loader.load_agent("G2")` to get the plan agent's `.md` system prompt
3. Orchestrator constructs the user-facing specialist name ("Solution Architect")
4. Orchestrator calls `agent_loop.run(system_prompt, specialist_name)` with the user's message
5. The loop runs until the gate artifact is produced or the user intervenes
6. On gate completion, the loop emits `GatePause` and returns control to the orchestrator
7. Orchestrator waits for user verdict (via `agent:verdict` IPC)
8. On approve, orchestrator signs the gate and advances to the next one

There is NOT one long-lived loop that changes persona. Each gate gets a fresh loop with a fresh system prompt. Conversation history from the previous gate is summarized and included as context (not the full transcript -- context compression applies).

**Gate boundary detection**: the orchestrator checks `wave_engine.inspect()` AFTER each agent-loop run completes. The agent loop does NOT detect gates internally -- it runs until the LLM signals completion (`stop_reason: "end_turn"`) or the tool-call limit is reached. Then the orchestrator checks if the gate artifact was produced. If not, the orchestrator can re-run the loop with additional guidance.

This means the agent loop is stateless regarding gates. It runs a bounded conversation. The orchestrator is the gate-aware supervisor.

### Q5: Underspecified mechanisms

#### Q5a: INV-5 idempotency -- content hash

**Decision: Content hash comparison.**

Each tool call is recorded in `.signalos/agent-runs/<run-id>/tool-calls.jsonl` with:
```json
{
  "seq": 42,
  "tool": "write_file",
  "args": {"path": "src/App.tsx", "content": "..."},
  "content_sha256": "abc123...",
  "status": "completed",
  "ts": "2026-06-01T..."
}
```

On resume after crash:
1. Read `tool-calls.jsonl` to get the last completed sequence number
2. Replay the conversation up to that point (from `conversation.jsonl`)
3. For the next tool call, check: does the file at `path` already have content matching `content_sha256`? If yes, skip (idempotent). If no, execute.

Mtime is not reliable (clock skew, different filesystem). Ledger replay is too slow (would re-execute everything). Content hash is deterministic and fast.

#### Q5b: Context compression -- LLM summarizes, gate evidence is pinned

**Decision: LLM summarizes older messages. Gate artifacts and evidence are pinned (never dropped).**

When conversation history exceeds 80% of the provider's context window:
1. Messages older than the last gate checkpoint are candidates for compression
2. An LLM call summarizes the candidate messages into a single "context summary" message
3. Gate artifacts (the actual signed documents), evidence paths, and the user's latest message are PINNED -- they are never included in the summary candidates
4. The compressed conversation is: `[system prompt] + [context summary] + [pinned gate artifacts] + [recent messages]`

The pinning rule: any message that contains a gate artifact path, a signed document, or evidence reference is excluded from compression. This guarantees T44 (crash resume) preserves gate evidence.

### Smaller open items

#### Trust-tier path allowlists

**Decision: Define per-tier allowlists in a new config file `.signalos/trust-tier-paths.json`, seeded by `signalos init`.**

```json
{
  "T1": {
    "read": ["**"],
    "write": [],
    "execute": []
  },
  "T2": {
    "read": ["**"],
    "write": ["src/**", "public/**", "tests/**", "package.json", "tsconfig.json"],
    "execute": ["npm install", "npm run build", "npm test", "npm run dev", "git status", "git diff", "git log"]
  },
  "T3": {
    "read": ["**"],
    "write": ["**"],
    "execute": ["**"]
  },
  "forbidden_always": {
    "write": [".signalos/AUDIT_TRAIL.jsonl", ".signalos/gates.json", ".env", ".env.local", "*.pem", "*.key"],
    "execute": ["rm -rf", "git push --force", "git reset --hard", "npm publish", "docker push"]
  }
}
```

The agent loop reads this at startup (alongside the enforcement state from Rust). `agent_packets.py` currently has `_DEFAULT_FORBIDDEN_PATHS` -- this is replaced by the config file. Typed paths (explicit strings), not globs, for `forbidden_always`. T2 write allowlist uses globs for the source directory but explicit names for config files.

#### Streaming -- fully greenfield

**Decision: Acknowledge greenfield. The streaming path is new end-to-end.**

```
Python agent_loop.py
  --> yields StreamDelta events
  --> signalos_ipc_server.py writes JSON lines: {"kind": "agent-event", "type": "text-delta", "text": "..."}
  --> sidecar.rs stdout parser recognizes "agent-event" kind
  --> emits Tauri event "agent:event"
  --> agentEvents.ts receives event, updates chatBubbles signal
  --> BuildView.tsx re-renders with new text
```

No existing streaming infrastructure is reused. The current `chat:token` Tauri events from `provider.rs` are for the legacy direct-to-provider chat path. The new path is sidecar-mediated. Both paths can coexist during migration (legacy chat still works until the agent loop fully replaces it).

### Reference: Claude Code public repo

The Claude Code source is publicly visible at `github.com/anthropics/claude-code` (proprietary license -- read and learn from, not embed or redistribute). The following patterns can be studied and adapted for our implementation where relevant:

- **Tool-use loop structure**: how Claude Code iterates between LLM calls and tool execution, handles tool results, and manages conversation state
- **Tool definitions**: the specific tool schemas for file read/write/edit, shell execution, search -- our tool definitions should match the quality and granularity of theirs
- **Streaming architecture**: how token deltas are emitted and rendered incrementally
- **Context management**: how conversation history is compressed/truncated for long sessions
- **Permission model**: how Claude Code gates tool execution (user approval prompts before destructive actions)
- **Error handling**: how tool failures, LLM errors, and timeout conditions are surfaced

We build our own implementation -- the code is ours, the governance layer is ours, the multi-provider support is ours. But the patterns and design decisions in Claude Code are a proven reference for the tool-use loop, streaming, and UX that we should study before implementing each phase. No need to guess at patterns that are already visible and battle-tested.

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
    |--- Loop until LLM signals completion or tool-call limit reached
    |--- Emit WorkComplete event to orchestrator
    |
    v
Orchestrator checks wave_engine.inspect() --> gate boundary? --> user reviews --> 5 verdicts:
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
- `enforcement.rs` -- 12 runtime rules
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
| `enforcement.rs` | ACTIVE | 12 runtime rules checked on every tool call |
| `keychain.rs` | ACTIVE | OS credential storage for all providers |
| `sidecar.rs` | ACTIVE (modified) | Event routing for agent loop events |
| `governance.rs` | ACTIVE | Audit trail, gate state persistence |
| `DeliverView.tsx` | DE-SCOPED | Removed from navigation in 1.1. Useful logic extracted to services/deliveryFlow.ts in 1.1b. Source file deleted in 4.3. |
| `TerminalView.tsx` | DE-SCOPED | Removed from navigation in 1.1. Command routing extracted to services/governedShell.ts in 1.1b. Source file deleted in 4.3. |

---

## Implementation Phases

### Phase 1: UI/UX Revamp

The chat must look and feel as powerful as Claude Code's interface BEFORE we wire the agent loop.

DeliverView and TerminalView are removed from navigation immediately. Useful logic is preserved as internal modules/services behind the Build conversation. Rollback is through git/release, not through in-app flags.

| Step | What | Files | Delivers |
|------|------|-------|----------|
| 1.1 | Remove Deliver and Terminal tabs from navigation. Preserve useful logic as internal services. 3 project tabs: Build, Preview, Evidence. | Toolbar.tsx, app.tsx | Clean navigation |
| 1.1b | Extract reusable logic from DeliverView and TerminalView into services: delivery flow state machine -> services/deliveryFlow.ts, terminal command routing -> services/governedShell.ts, preview launch -> services/preview.ts (already exists). Verify imports compile. | services/deliveryFlow.ts (new), services/governedShell.ts (new) | Backend logic preserved before view deletion |
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
| 2.7 | Loop completion: agent loop runs until LLM end_turn or tool-call limit. Returns control to orchestrator. No gate awareness in the loop. | agent_loop.py | Orchestrator owns gates |
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

### Phase 4: Test-Gated Cleanup

DeliverView and TerminalView are already removed from navigation (Phase 1). Phase 4 deletes the dead source files after the Build conversation compiles and tests pass.

| Step | Status | What | Files | Delivers |
|------|--------|------|-------|----------|
| 4.1 | Done | Delivery-flow parity works entirely in Build conversation. T39-T44 remain Phase 5 E2E matrix rows and are not marked complete here. | deliveryFlow.test.ts, BuildView.test.tsx, app.view-shell.test.tsx | Build-surface delivery parity proven without stale Deliver page |
| 4.2 | Done | Real commands work in Build conversation (T12 covered by governed shell tests) | governedShell.ts, governedShell.test.ts | Governed shell proven |
| 4.3 | Done | Delete DeliverView.tsx and TerminalView.tsx source files, plus stale terminal service, old app-v2 terminal handlers, and dead Deliver/Terminal CSS | app.tsx, app-v2.js, styles.css, state.ts | Dead code removed |
| 4.4 | Done | Preview tab integration: auto-open Preview when agent starts dev server | PreviewView.tsx, agentEvents.ts | Seamless preview |

Phase 4 validation on 2026-06-02:

- `npm.cmd test`: 37 test files, 236 tests passed.
- `npm.cmd run build`: passed.
- Legacy surface sweep for `DeliverView`, `TerminalView`, `data-view="deliver"`, `data-view="terminal"`, terminal state/globals, and `deliver-`/`term-` selectors: clean.
- Preview auto-open verified in `agentEvents.ts`: preview events set `previewUrl` and switch `tab` to `preview`.
- Cleanup committed as `2280831 refactor: remove obsolete delivery terminal surfaces`.
- T39-T44 are still tracked in Phase 5. They must only be marked done when their specific CI/mock or smoke scenarios pass.

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
| `wave_engine.py` | Orchestrator gate detection | State machine + inspect (called by orchestrator after each loop run, not by agent loop) |
| `orchestrator.py` | Agent loop file writing | Pre-write guard, audit |
| `sign.py` | Orchestrator gate signing (INV-3) | The ONLY signing path |
| `validate_cmd.py` | Agent loop validators | 12 Layer 1 validators |
| `security.py` | Agent loop governance | OWASP/STRIDE checks |
| `data_privacy.py` | Agent loop governance | GDPR export/purge |
| `enforcement.rs` | Agent loop trust tiers | 12 runtime rules |
| `keychain.rs` | Provider adapter setup | OS credential storage |
| `sidecar.rs` | Agent event streaming | Sidecar spawn + event routing |
| `governance.rs` | Audit trail | Gate state + audit append |
| `_bundle/ (287 files)` | Gate agent system prompts | Constitution, contracts, standards |

---

## Test Matrix

Every row must pass before v4 ships. No exceptions. No relaxing.

### Provider Support (T01-T08)

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

Live-provider validation reported on 2026-06-02: 9 pass, 0 fail, 1 skip. Passing rows: T01 Anthropic, T02 OpenAI, T03 Gemini (`gemini-flash-lite-latest`), T04 Ollama, T05 no-key honesty, T06 provider switch, T39 gate walk + real response, T41 vague prompt, T42 request-changes rework. The skipped item is not marked as pass unless separately mapped to a tracker row with a `not-applicable` reason.

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

Gate-matrix validation on 2026-06-02: T26-T38 passed through `python -m pytest python/test_product_gate_orchestrator.py python/test_product_e2e_v4.py -q` with 17 total tests passing. `test_product_gate_orchestrator.py` covers the G0-G5 gate walk, all five verdict paths, sign-on-approve via the signing API, bounded request-changes/reject loops, waiver non-readiness, G3 preview, delivery resume, and real sign audit evidence.

### Full Delivery E2E (T39-T44)

| # | Test | What it proves | How to verify | CI? |
|---|------|---------------|--------------|-----|
| T39 | "Build task management" --> running app | Full flow (INV-2) | Type prompt, approve all gates, verify: product repo + tests + runtime proof + UX proof + closeout | Optional smoke (needs live provider) |
| T40 | "Build medical records HIPAA" --> GDPR flagged | Compliance | Verify security gate flags HIPAA + PII | Required CI (mock) |
| T41 | Vague prompt --> smart questions asked | LLM questions | Type "build something", verify agent asks domain questions | Optional smoke |
| T42 | User changes design mid-flow | Interactive | At G3, say "change to blue", verify design updates | Optional smoke |
| T43 | Closeout honest when partial | Honesty | Interrupt at G4, verify closeout says "partial" not "ready" | Required CI |
| T44 | Sidecar crash --> run resumes from checkpoint | INV-5 | Kill sidecar during delivery, restart, verify resumes | Required CI |

Live-provider smoke note: T39 gate walk + real provider response passed on 2026-06-02. Full T39 delivery closure still requires the INV-2 evidence listed in the row: real product repo, executed tests, runtime proof, UX proof, and evidence closeout.

Required E2E validation on 2026-06-02: T40/T43/T44 passed through `python -m pytest python/test_product_gate_orchestrator.py python/test_product_e2e_v4.py -q` with 17 total tests passing. `test_product_e2e_v4.py` covers HIPAA/GDPR/PII flagging, honest partial closeout when proofs are missing, and persisted delivery resume after simulated sidecar memory loss.

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

**57 tests. 47 required in CI. 8 optional smoke (live provider). 1 manual. 1 required release.**

T56 evidence on 2026-06-02: commit `abab505273b0830ed492f48de322c4c7c5c6826f` passed Pages run `26843112515`, Smoke run `26843111914`, and test-automation run `26843111965`. The test-automation run completed green across L0 pre-commit gates, L1 build/verify on macOS/Windows/Ubuntu, L1 Docker sandbox integration, L2 installer smoke on macOS/Windows/Ubuntu, and L3 pre-prod gates including `Run extended test-gates`.

T57 evidence on 2026-06-02: tag `v3.0.0-internal.28` on commit `7824e4aa05900c2d8962e47f7fc309a96671777f` passed Release run `26845234306`. Required jobs completed green: RustSec audit policy, Build macOS ARM, Build macOS Intel, Build Windows, Build Linux, and Publish update manifest. The release workflow also pushed manifest commit `f7794f0` to `main`.

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| LiteLLM tool calling varies by provider | Agent loop breaks on some providers | Capability detection adapter (T07-T08); fallback to text-only |
| Conversation context window overflow | Long sessions crash | Context compression; persist history to disk |
| Gate detection timing | Agent skips a gate | Orchestrator calls wave_engine.inspect() after each loop run; test T26-T31 |
| Streaming performance | UI freezes on fast token output | Batch text-delta events (debounce 50ms); test T45 |
| Sidecar crash during agent loop | Lost state | Persisted run state + resume (INV-5); test T44 |
| LiteLLM bundle size | PyInstaller binary larger | Early ready line (already shipped); monitor cold-start |
| Agent forges gate signature | Governance bypass | INV-3 enforced: only sign.py API path; test T32 |
| Silent skip passes as proof | False readiness | INV-1: no-silent-skip policy; test T37 |

---

## Dependencies Between Steps

```
Phase 1 (UI)         Phase 2 (Runtime)         Phase 3 (Wire)       Phase 4 (Parity)
  1.1 ---------+
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
                                                    4.1 (delivery parity in chat)
                                                    4.2 (governed shell in chat)
                                                     |
                                                     v
                                                    4.3 (delete dead view files)
                                                    4.4
                                                     |
                                                     v
                                              Phase 5 (57-test validation)
```

Phase 1 and Phase 2 run in parallel. Phase 3 connects them. Phase 4 deletes dead view files after Build-surface delivery parity and T12 pass. Phase 5 owns the full T39-T44 delivery E2E matrix and the rest of the 57-test validation.

---

## Final Layout (v4)

### App-level (no project open)

```
+-------------------------------------------------------------------+
|  Foundry                                          [Settings] [Help]|
+---------------+---------------------------------------------------+
|               |                                                   |
|  Projects     |   Welcome to Foundry                              |
|  + New Product|                                                   |
|               |   Your AI product team, governed from idea        |
|  Team         |   to launch. Start by describing what you         |
|  Evidence     |   want to build.                                  |
|  Settings     |                                                   |
|  Help         |   [+ New Product]                                 |
|               |                                                   |
|  Recent:      |   Recent projects:                                |
|  - TeamPulse  |   [TeamPulse] [MedUnify] [RecipeApp]              |
|  - MedUnify   |                                                   |
+---------------+---------------------------------------------------+
```

### Inside a project

```
+-------------------------------------------------------------------+
|  Foundry > TeamPulse                   [Build] [Preview] [Evidence]|
+---------------+---------------------------------------------------+
|               |                                                   |
|  Build        |   Build (the conversation)                        |
|  Preview      |   +-------------------------------------------+  |
|  Evidence     |   | User: "Build task management for my team"  |  |
|  Handoff      |   |                                            |  |
|  Activity     |   | [Product Strategist]                       |  |
|               |   | "I've analyzed your request. Here's what   |  |
|               |   |  I understand..."                          |  |
|               |   | [Product Brief card]                       |  |
|               |   |                                            |  |
|               |   | Scope Approval:                            |  |
|               |   | "Does this match what you want built?"     |  |
|               |   | [Approve] [Request Changes] [Reject]       |  |
|               |   |                                            |  |
|               |   | User: "Yes, but add KPI dashboards"        |  |
|               |   |                                            |  |
|               |   | [UX Designer]                              |  |
|               |   | "Here's the design direction..."           |  |
|               |   | [interactive preview iframe]               |  |
|               |   |                                            |  |
|               |   | Design Approval:                           |  |
|               |   | [Approve] [Request Changes]                |  |
|               |   |                                            |  |
|               |   | [Full-Stack Engineer]                      |  |
|               |   | "Building..."                              |  |
|               |   | [writing src/components/Task.tsx] [check]  |  |
|               |   | [diff: +42 lines]                         |  |
|               |   |                                            |  |
|               |   | Brief -> Design -> [Build] -> Validate ... |  |
|               |   +-------------------------------------------+  |
|               |   [Type a message...]                             |
+---------------+---------------------------------------------------+
```

### Visual rules applied

- Off-white background, charcoal text, deep blue for trust, signal orange for active build
- Agent messages show specialist name, not "SignalOS" or "AI"
- Approval cards are large, clear, meaningful -- not tiny pills
- Progress bar shows business stages (Brief, Design, Build...) not gate codes
- Evidence shown in plain language
- No overlapping panels, no translucent layers
- Strong spacing between cards
- Status colors only on approvals and evidence

---

Foundry by SignalOS. Premium product studio. Any provider. 5 verdicts. Full governance. 57 tests. No exceptions.
