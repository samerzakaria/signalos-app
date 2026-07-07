# SignalOS — Wave Engine Design

**Status:** Design — to be reviewed before implementation.
**Scope:** Software delivery only (multi-artifact deferred to a later roadmap).
**Author:** PO + Claude (Opus 4.7).
**Drafted:** 2026-05-20.

This document is the design contract for extending today's `orchestrator.run_wave` (G4-only, parallel task dispatcher) into the **wave engine**: a single state machine that owns the full G0→G5 lifecycle, fires the right per-gate agent at each step, and drives the user conversationally instead of pushing protocol paperwork at them.

The document was preceded by a long discussion (captured in the prior conversation turns); this is the codified outcome. Implementation does NOT begin until this design is signed off.

---

## 1. Core principle

**The system is the executor.** The user states intent ("build me X"); the engine drives the pipeline. The user never sees `G0`, `G1`, etc. as labels — they see a conversation that asks the right questions, then a plan, then a design preview, then the running app, then a "shipped" notification. The audit trail captures every gate sign with the user's actual words as evidence.

**The engine never refuses normal flow.** It re-routes. "Build me X" with no Soul signed → don't refuse, fire the G0 agent first. "Ship this" with build incomplete → fire the build agent first. The only hard refusals are pathological cases (status read fails, gate state corrupt).

**Speed is variable; integrity is not.** Expert user submits a fully-formed belief in the first message → engine inspects, translates externally-supplied artifacts to the SignalOS format, signs, fast-forwards. Novice user submits "do something with React" → engine drives the full conversation. Same final integrity. Different speeds.

---

## 2. Pipeline shape (G0 → G5)

| Gate | Owns | Today's status | Wave-engine agent |
|---|---|---|---|
| G0 — Soul | Who/why/constraints/success criteria | Not driven | Load `_bundle/core/execution/agents/onboarding.md` as LLM system prompt; conduct 2-5 question interview; capture into `core/governance/Governance/SOUL-DOCUMENT.md`; user affirms → silent sign |
| G1 — Belief | Operational hypothesis | Not driven | Load `_bundle/core/execution/agents/brainstorm.md`; draft belief from G0 + user request; user confirms → sign |
| G2 — Plan | Decomposed work | Today's chat LLM does this informally via `signalos-plan` block | Load `_bundle/core/execution/agents/plan.md`; emit `signalos-plan`; user clicks "Approve & run" → sign (existing flow) |
| G3 — Design | Doc + UI prototype | **Missing — deferred** | Load a (to-be-created) `design.md` agent; produce doc + prototype per §6.7 of the v0.2 audit; user reviews → sign |
| G4 — Build | Produce files | **Wired** — `run_wave`'s current job | Load `_bundle/core/execution/agents/build.md`; parallel task dispatch (today's worktree + ThreadPool path) |
| G5 — Ship | Deliver | Manual today | Load `_bundle/core/execution/agents/observability.md`; emit retro + run M4's git push |

The engine moves through these sequentially. At each gate it picks the right work shape:

- **G0/G1/G2/G3/G5** — sequential single-LLM turns (interview, draft, review)
- **G4** — parallel multi-task dispatch (today's path)

The existing parallel dispatcher inside `run_wave` is the G4 implementation. It does NOT get rewritten — it gets called as one branch of the gate-dispatch.

---

## 3. State model

### 3.1 Wave state machine

```
ENTRY → INSPECT → DECIDE → DISPATCH → AWAIT_USER_CONFIRM → SIGN → ADVANCE → (next gate)
                                                                              ↘ (last gate) → COMPLETE
                    ↓
                  SCOPE_DRIFT → 4-WAY PROMPT → (a/b/c/d) → re-enter
```

| State | Means |
|---|---|
| `ENTRY` | A new wave begins (user message arrives) |
| `INSPECT` | Read `.signalos/` to discover existing artifacts (Soul present? Belief present? Plan present?) |
| `DECIDE` | Determine: which gate is next? Does the existing artifact for this gate fit the new request, or has scope drifted? |
| `SCOPE_DRIFT` | An existing signed artifact doesn't fit the new request — surface 4-way prompt (amend / new project same workspace / new project new workspace / same project, keep going) |
| `DISPATCH` | Fire the per-gate agent for the current gate |
| `AWAIT_USER_CONFIRM` | Agent produced its output; wait for user's reply (treated as the gate sign) |
| `SIGN` | Record the gate signature with the user's reply as evidence in `.signalos/AUDIT_TRAIL.jsonl` and the gate's signature block |
| `ADVANCE` | Move to the next gate |
| `COMPLETE` | All 6 gates signed; wave finished |

### 3.2 `project_id` parameter (multi-project plumbing)

From day one, every state-touching function takes a `project_id: str = "default"` parameter:

- Gate state lives at `.signalos/projects/<project_id>/gates/`
- Audit trail lives at `.signalos/projects/<project_id>/AUDIT_TRAIL.jsonl`
- PLAN files live at `.signalos/projects/<project_id>/plans/`
- Soul/Belief artifacts live at `core/governance/projects/<project_id>/...` (or workspace-root for project_id=="default" — preserves today's layout for single-project workspaces)

Today only `"default"` is used; the UI does not expose a project picker. When (a future milestone) the Sidebar's "Projects" tab is wired to manage multiple projects, no engine refactor needed — just UI exposure.

**Migration**: existing workspaces (gate state at `.signalos/gates/`) are read as project_id `"default"` via a backwards-compat shim. No file moves required.

**Status (shipped)**: the §3.2 gate-artifact namespacing milestone is implemented.
The single resolver is `projects.project_governance_dir(root, project_id)`:
`"default"` → the workspace root itself (byte-identical to today's layout);
any other id → `.signalos/projects/<id>/governance/` as the base under which the
canonical `core/governance/...` / `core/strategy/...` / `core/execution/...`
rel_paths resolve unchanged. (`.signalos/projects/<id>/governance/` was chosen
over the `core/governance/projects/<id>/` sketch above because the gate manifest
spans three `core/` subtrees — a base-dir swap keeps every rel_path identical.)
All gate readers/writers route through it: `sign.check_gate`/`sign_gate`,
`wave_engine.inspect`, `status` gate detection + belief/soul reads,
`orchestrator._route_next_gate_action` (via status), `validate-gate` /
`validate-wave-status`, and `product.gate_orchestrator`. Artifact *generation*
is namespaced on the same resolver: the delivery bridge's `AgentLoop` physically
rebases file writes addressed to the three canonical subtrees under the
project's governance base (`_artifact_base` in `product/agent_loop.py`), so a
non-default delivery's gate agent creates its artifact exactly where the sign
path reads it; product-source writes never rebase, enforcement keeps operating
on the canonical rel_paths, and the default project stays byte-identical.
`GateOrchestrator` persists the delivery's `project_id` in `delivery.json` so a
resumed delivery keeps its namespace binding. Per-project
`PLAN.tasks.yaml` resolves via `projects.project_plan_path`
(`.signalos/projects/<id>/PLAN.tasks.yaml`). The audit trail deliberately stays
one workspace-global chain at `.signalos/AUDIT_TRAIL.jsonl` (rows record
`project_id` context where relevant).

---

## 4. Per-gate agent contract

Each agent is defined by a markdown file under `_bundle/core/execution/agents/`. The wave engine treats each as:

| Agent file | Role |
|---|---|
| `onboarding.md` | G0 agent |
| `brainstorm.md` | G1 agent |
| `plan.md` | G2 agent |
| `design.md` (TO BE CREATED) | G3 agent |
| `build.md` | G4 agent (calls today's `run_wave` G4 branch) |
| `observability.md` | G5 agent |

**Agent invocation contract:**

```python
def invoke_gate_agent(
    gate: str,                # e.g. "G0", "G1", ...
    project_id: str,
    prior_artifacts: dict,    # signed artifacts from earlier gates
    user_request: str,        # the user's current intent text
    conversation_so_far: list[dict],  # chat history relevant to this wave
) -> AgentTurnResult:
    """Load the gate's agent.md as system prompt; send to LLM with the
    provided context. Return the LLM's response — typically a question
    (interview), a draft artifact (brainstorm/plan/design/ship), or a
    progress event (build)."""
```

Returns an `AgentTurnResult`:
- `kind`: `"question" | "draft_artifact" | "build_progress" | "ready_to_sign"`
- `text`: the response text shown in chat
- `artifact_path`: when the agent produced a file, where it landed
- `proposed_sign_evidence`: the agent's claim about what should be recorded as the user's sign-evidence on confirmation

The user's next chat reply is interpreted as either:
- **Affirmation of artifact** → sign the gate, advance
- **Refinement request** → re-invoke same agent with the refinement
- **Out-of-band question** → answer conversationally, do NOT advance

---

## 5. Re-routing UX (transparent status)

When the engine re-routes (e.g., "ship this" but G4 not done):

```
User: ship this
SignalOS: Build (G4) isn't done yet — kicking off the build agent first.
          [progress: dispatching 5 tasks…]
          Build complete.
          Now firing the ship agent.
          [retro draft]
          Ready to ship. Confirm to push.
User: confirm
[push runs, success bubble]
```

Rules:
- Every re-route emits a `system`-kind chat bubble naming what the system is about to do
- Every gate sign emits a small system bubble: "Captured your answer as G1 sign-off — saved to audit trail"
- The user can interrupt at any point (Cancel button on the progress bubble cancels the current gate's agent and leaves the wave in `AWAIT_USER_CONFIRM` state)

---

## 6. Scope-drift detection

At `INSPECT` time, the engine compares the user's new request against signed prior artifacts.

**Detection signals** (cheap heuristics; LLM-judged on ambiguous cases):
- Domain words in the new request don't appear anywhere in the signed Soul (e.g., Soul says "todo app for personal use" and new request says "financial dashboard for clients")
- File-list intent (new request implies tech stack that's incompatible with the Plan stack)
- Stakeholder mismatch (Soul says "for me alone" and new request says "share with my team")

**Action on drift detected:**

The engine emits the 4-way prompt as a system bubble with clickable options:

```
SignalOS: This new request feels different from the project we signed Soul for:

   Current Soul: "[one-line summary of signed Soul]"
   New request:  "[one-line summary of new request]"

   How should we treat this?
   (a) Amend the current Soul — same project, evolving direction
   (b) New project, same workspace (parallel)
   (c) New project, new workspace folder
   (d) Same project, just keep going (you read the difference wrong)
```

User picks → engine acts:
- (a) Re-fire G0 agent in amend-mode (preserves prior G1+ artifacts unless they too become inconsistent)
- (b) Create new `project_id`, route into ENTRY for the new project
- (c) Prompt for new folder path, run `signal-init` there, route into ENTRY at the new workspace
- (d) Treat the new request as a refinement under the existing project; no Soul change

Old project is NEVER discarded automatically.

---

## 7. Inspect-first / fast-forward (expert user path)

At `INSPECT`, before deciding to fire any agent:

1. Read each gate's artifact from disk (if present)
2. Check signed status
3. For each existing artifact:
   - If signed AND fits the new request → **skip the agent**, fast-forward
   - If unsigned but artifact exists in SignalOS format → ask user to confirm artifact + sign (one-turn, faster than re-drafting)
   - If artifact exists in **non-SignalOS format** (e.g., user pasted a Figma URL, attached a free-text belief doc, points at an external Jira) → fire the gate agent in **translator-mode**: ingest the external artifact, produce the SignalOS-format version, user confirms the translation captures their intent, sign

This is the "speed varies, integrity doesn't" property. The engine never fast-forwards past a gate without a signed artifact in SignalOS format. But it gets there as fast as the existing evidence allows.

---

## 8. Auto-sign protocol

When an agent's turn ends with `kind: "ready_to_sign"`:

**Interactive path (default):**
- Engine emits a clear ask: "Captured this as your G1 belief. Confirm? (yes / refine / cancel)"
- User replies "yes" or equivalent affirmative
- Engine writes the gate signature with the user's reply text as `evidence` field
- Audit-trail entry: `{ action: "gate-signed", gate: "G1", evidence: "<user reply text>", ts: ... }`

**Headless path (CI / non-interactive only):**
- Env var `SIGNALOS_GATE_OVERRIDE=1` allows gate sign without interactive confirmation
- Every such sign is logged as `{ action: "gate-signed-override", gate: "G1", evidence: "headless-override", ts: ... }`
- This is for CI runs / scripted workflows ONLY — interactive use never relies on the env var

**Per-violation explicit confirmation:**
- When a skill validator fails (e.g., code-review found 3 findings) and the user wants to ship anyway:
- Engine emits: *"The code-review skill reported 3 findings. Ship now would skip the fix. (a) Fix first, (b) Defer to next wave, (c) Ship anyway as logged violation"*
- User picks (c) explicitly → engine records the violation with user's confirmation text + reasoning as audit evidence

NO silent overrides in interactive mode. The user always types or clicks an explicit confirmation; that text becomes the audit evidence.

---

## 9. Refusal taxonomy (when the engine does say "no")

Per the discussion that preceded this design, true refusals are rare. The engine refuses only in these categories:

**Category A — Hard safety (no override):**
- LLM provider safety refusals (illegal/harmful content) — these surface from the provider, engine just passes through
- "Delete user's entire workspace without confirmation" type actions — engine confirms-then-acts, never silently destroys

**Category E — Defense floor (silent refuse + audit):**
- Direct CLI bypass of the wave engine (script invokes `signalos orchestrate` against an unsigned workspace) — engine refuses, logs `enforcement-refused-orchestrate` to audit. User never sees this because it only fires when something bypassed the UI/agent layer.
- Status read failure or gate state corruption — engine fails closed (refuses dispatch) + logs `enforcement-error-orchestrate-gate-check`. Surfaces to user as a system bubble: *"Couldn't read project state — paused for safety. See audit trail."*

**Re-route, not refuse (Categories B/C):**
- All "missing prior gate" cases → re-route to that gate's agent
- All "missing infrastructure" cases (no workspace, no API key, no git remote) → ask user for the missing thing, never refuse

**Override-with-audit (Category D):**
- All "user wants to skip a protection" cases → surface the violation, ask explicit confirmation, proceed + log

---

## 10. What `_check_orchestrate_gates` becomes

Today (uncommitted scaffolding) it's a refuse-by-default function. It gets reshaped during implementation:

**Rename:** `_route_next_gate_action`

**New return shape:**
```python
{
    "action": "build" | "fire-agent-G0" | "fire-agent-G1" | ... | "refuse-pathological" | "override-with-audit",
    "current_gate": "G0" | "G1" | ... | None,
    "evidence": "<reason / context for the caller>",
}
```

**Default branch:** `"fire-agent-<next-unsigned-gate>"` — the engine handles re-routing, not the floor function.

**Refuse branch:** ONLY for pathological cases (status read fails, gate state corrupt). Not for "gates not signed."

**Override branch:** Returns `"override-with-audit"` when `SIGNALOS_GATE_OVERRIDE=1` is set; caller logs the violation and proceeds.

The audit-logging + override-detection bones already in the uncommitted code are kept. The default action is what flips.

---

## 11. Out of scope for the wave engine (deferred to future milestones)

To prevent scope creep, the wave engine deliberately does NOT include in this design:

- **Multi-artifact dispatch** (slides, research, docs) — software only for now
- **Multi-project UI exposure** — `project_id` plumbing is there, but Sidebar Projects tab doesn't yet manage multiple projects per workspace
- **Real-time multi-user collaboration** — single-user assumption
- **Prompt caching across turns** — the protocol preamble is re-sent each turn; cache optimization is a v0.3+ concern
- **Cross-wave learning** — each wave is independent; the Brain is read-only at gate time
- **Federated agents** (running agents on a remote machine) — local-only

These are deliberate non-goals for the immediate wave-engine milestone.

---

## 12. Implementation milestones

After this design is signed off, implementation proceeds as:

| Milestone | Scope | Effort |
|---|---|---|
| **M-W1** | Reshape `_check_orchestrate_gates` → `_route_next_gate_action`; thread `project_id` through state.py / status.py / orchestrator.py (default `"default"`) | 1-2 days |
| **M-W2** | Wave-engine state machine + `INSPECT` + scope-drift detection (heuristics + LLM-judge) | 3-5 days |
| **M-W3** | G0/G1 agents loaded; auto-sign on user affirmation; system bubbles for re-routing | 3-5 days |
| **M-W4** | G2/G3 agents loaded; design-agent definition for G3 (`design.md` agent file) | 3-5 days |
| **M-W5** | G5 agent loaded; integration with M4's git push | 2-3 days |
| **M-W6** | Translator-mode for inspect-first fast-forward | 2-3 days |
| **M-W7** | Refusal taxonomy + violation-confirmation flow | 2-3 days |
| **Total** | | **~16-26 days of focused engineering** |

Each milestone is independently shippable. M-W1 is mostly the Layer 3 reshape + adding `project_id`; once landed, the wave isn't broken (it just behaves like today). M-W2+ progressively replace today's "regex + chat" with the engine.

---

## 13. Open questions — RESOLVED 2026-05-20

1. **Sign-evidence storage** — **RESOLVED: both.** Engine continues writing the `## Signatures` block in the gate's artifact markdown (human-readable + greppable in the workspace) AND appends to `AUDIT_TRAIL.jsonl` (machine-parseable integrity record). On conflict the audit trail is the authoritative source.

2. **Scope-drift LLM cost** — **RESOLVED: accepted, with caching.** The cheap heuristic check (regex + signal lookup) runs first. When unsure, fall back to a small LLM-judged call. Cache the verdict for the duration of the wave so we don't re-pay per turn. ~1 small LLM call (a few cents) per "is this still the same project?" check. Worth it — a wrong answer here builds the wrong thing and damages trust.

3. **What counts as "affirmation"** for auto-sign — **RESOLVED: strict for v1, LLM-judged for v2.** v1 accepts an allowlist (`yes`, `confirm`, `approve`, `looks good`, `proceed`, `sign`, `ok`, button click). v2 (after the affirmation classifier has earned trust) lets the LLM judge from richer replies. v1's failure mode is false-negative ("is that a yes?") — one extra turn. False-positives would silently sign on ambiguity — integrity loss; not acceptable.

4. **Translator-mode supported external formats at launch** — **RESOLVED: six formats.**
   - (a) plain markdown files in the workspace (any structure)
   - (b) Figma URLs (read via Figma's public API if a token is set; otherwise record the URL as evidence)
   - (c) text pasted in the chat ("here's my belief: ...")
   - (d) **PDF files** in the workspace (text-extracted via `pypdf` or equivalent stdlib-adjacent option)
   - (e) **`.md` files** explicitly (covered by (a), called out separately for clarity)
   - (f) **`.docx` / `.doc` files** (parsed via `python-docx` or equivalent)

   Other formats (Notion, Linear, Jira, Confluence, etc.) get added when a real user surfaces the need. Avoid speculative integrations.

   **Dependency note:** PDF + docx parsing introduces 2 new Python deps (`pypdf`, `python-docx`). Both are pure-Python, ~few hundred KB each, no native compilation. Acceptable per the "stdlib-only by default, justified additions OK" pattern in DECISION-DNA AMD-CORE-030.

---

## 14. What happens after sign-off

Once the user approves this design:

1. Update `MEMORY.md` and v0.2 audit doc to reference this design as the source of truth for the wave-engine work
2. Begin M-W1 implementation (Layer 3 reshape + `project_id` plumbing) — the smallest unit that lands working code without changing behaviour
3. Each subsequent milestone gets its own commit + push, following the same "validate-locally-then-ship" pattern from Phase B closure

Until sign-off, the uncommitted scaffolding in `orchestrator.py::_check_orchestrate_gates` + `test_orchestrator_gate_floor.py` stays in the working tree. Layer 2 (the chat-message refusal) has already been reverted.

---

*End of design.*

*Maintainer: Samer Zakaria. Drafted with Claude Opus 4.7. Last edit: 2026-05-20.*
