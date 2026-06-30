# Governed Fleet Runtime — Design

Status: **foundation landed + roadmap.** This document is the clean-room design
for the full autonomous, multi-agent fleet runtime. The detection, governed
dispatch admission, and workspace GC pieces are **built** (see
`python/signalos_lib/fleet_runtime.py` and `python/signalos_lib/commands/fleet.py`).
The autonomous daemon (live executor) is **not yet implemented** — it is
roadmap, and this doc is explicit about the boundary.

## Attribution and clean-room note

The *concepts* in this runtime — detecting installed agent CLIs, registering
them as runtimes, running each task in an isolated workspace, and garbage-
collecting those workspaces — are inspired by the **publicly documented
behaviour** of Multica's agent runtime. Multica's implementation is under a
restrictive commercial license. **No Multica source was read or copied.** This
is a clean-room design derived only from the described public behaviour, and the
governance overlay below is original to SignalOS.

The defining difference: **Multica dispatches agents ungoverned; SignalOS
governs every dispatch.** In the SignalOS fleet, no agent hand-off happens
without passing a gate/admission check, every result is validated against its
agent packet, and every step leaves evidence + an audit row. SignalOS enforces,
never advises: the absence of governance is a fail-closed refusal, not a
warning.

---

## 1. Runtime detection / registration — DONE

`detect_runtimes(path_env=None, is_windows=None)` scans PATH for the agent CLIs
SignalOS already emits tool adapters for. The detect set is derived from the
emitter directory names under
`python/signalos_lib/_bundle/core/tool-adapters/emitters/`:

| id | cli | probe executable(s) | kind |
| --- | --- | --- | --- |
| `claude-code` | claude-code | `claude` | agent-cli |
| `codex` | codex | `codex` | agent-cli |
| `cursor` | cursor | `cursor-agent`, `cursor` | agent-cli |
| `github-copilot` | github-copilot | `copilot` | agent-cli |
| `windsurf` | windsurf | `windsurf` | agent-cli |
| `gemini` | gemini | `gemini` | agent-cli |
| `antigravity` | antigravity | `antigravity` | agent-cli |
| `vs-code` | vs-code | `code` | editor |
| `harness` | harness | `signalos` | headless |

Each runtime is reported as `{id, cli, executable, kind, detected}`. PATH is
**injectable** (parameter or `PATH` env) so detection is deterministic in tests
and never depends on the host machine's real PATH.

**Governance overlay:** detection writes a runtimes evidence record to
`.signalos/evidence/fleet/runtimes.json` so the registered fleet is auditable.

## 2. Governed dispatch admission — DONE

`governed_dispatch(repo_root, task, *, packet=None, gate_check=None,
dry_run=True, ...)` is a **thin admission layer** in front of any agent
hand-off. Before an agent runs it requires **either**:

- an **active wave/gate** (via a `gate_check` callable; a conservative file-truth
  default reads the Journey), **or**
- a **structurally-valid agent packet** — validated by composing with the
  existing packet contract in `product/agent_packets.py` (the same required
  contract fields the agent-result validator enforces), not a re-implementation.

With neither, admission is **refused** (`admitted=False`) — fail-closed. Every
decision (admitted or refused) writes an evidence row to
`.signalos/evidence/fleet/dispatch.jsonl` and a
`fleet-dispatch-admitted` / `fleet-dispatch-refused` audit row.

This foundation **does not spawn a CLI.** The returned decision carries
`executed: false` and an `execution_note` pointing at this doc. Keeping
admission separate from execution makes the governance contract fully testable
without a live agent.

## 3. Workspace GC — DONE

`gc_task_workspaces(root, *, now_ts, done_ttl_s, orphan_ttl_s, artifact_ttl_s,
artifact_globs=("node_modules", ".next", ".turbo"))` prunes isolated per-task
workspaces under a configured tasks root:

- **done/idle** task dirs past `done_ttl_s` are removed whole;
- **artifact dirs** (node_modules/.next/.turbo) past `artifact_ttl_s` are pruned
  from otherwise-kept tasks, while **source, `.git`, and `logs` are preserved**;
- **orphan** dirs (no `.gc_meta.json`) past `orphan_ttl_s` are removed.

`now_ts` is **passed in** — the function never calls `time.time()`, so GC is
deterministic. A small `.gc_meta.json` marker per task dir records `status` and
`last_active_ts`. GC returns a structured summary of what was pruned/kept and is
**housekeeping, not a gate**: the `fleet gc` command exits non-zero only on a
real error (e.g. a failed removal), never merely because nothing was prunable.

---

## Roadmap — NOT yet implemented

The following are designed here but **not built**. Each carries its mandatory
SignalOS governance overlay so the roadmap cannot regress into ungoverned
dispatch.

### R1. Task lifecycle: claim → execute → heartbeat → stream

A live executor that pulls a governed work item, **claims** it (single-owner
lease), **executes** it against a detected runtime, emits **heartbeats** so a
stalled agent can be reaped, and **streams** output back to the app.

- **Governance overlay:** every `claim` passes `governed_dispatch` admission
  (active gate or valid packet) before the runtime is spawned; the claim lease,
  every heartbeat, and the terminal result each append an audit row. The result
  is validated against the claim's agent packet via the existing
  `agent_packets.validate_agent_result` before it is allowed to bind.

### R2. Isolated per-task workspaces

Each claimed task runs in its own workspace (a git worktree or copy) so
concurrent agents never collide. The workspace carries a `.gc_meta.json` marker
(status, `last_active_ts`) that GC (built) already understands.

- **Governance overlay:** workspace creation is scoped to the packet's
  `allowed_paths` / `forbidden_paths`; writes outside scope are rejected by the
  same path checks `agent_packets` already enforces. The GC TTL policy keeps the
  fleet bounded without operator intervention.

### R3. Autopilots (cron / webhook → governed work item)

Triggers (a cron tick or an inbound webhook) that turn an external event into a
**governed work item** routed into a wave/ceremony, composing with the app's
existing scheduling rather than introducing a second scheduler.

- **Governance overlay:** an autopilot never dispatches directly — it produces a
  work item that must still pass `governed_dispatch` admission (a wave/gate must
  be active or a packet built) and is recorded as evidence + audit. A trigger
  with no active governance is refused, not queued ungoverned.

### R4. "Squads"-style routing

Routing a work item to the best-fit runtime/role (a "squad") based on the task's
capability profile and the detected runtimes.

- **Governance overlay:** routing decisions are evidence-backed (which runtime,
  why) and the chosen runtime still goes through admission; the route cannot
  bypass the gate. Every routed result is validated against its packet.

### Not yet implemented (explicit list)

- The live, server-backed **executor daemon** (R1) — claim/execute/heartbeat/
  stream against a real CLI. `governed_dispatch` is admission-only today.
- **Isolated workspace provisioning** (R2) — GC of workspaces exists; automated
  creation/leasing does not.
- **Autopilot triggers** (R3) — cron/webhook → governed work item.
- **Squad routing** (R4) — capability-aware runtime selection.

---

## Honest comparison vs Multica

**Where SignalOS is better:**

- **Governed / fail-closed:** every dispatch requires an active gate or a valid
  agent packet, and refuses otherwise. Multica's documented behaviour dispatches
  ungoverned.
- **Audited:** every admission decision (admitted *and* refused) leaves an
  evidence row and an audit row; GC and detection are evidenced too.
- **Validated results:** agent output is non-binding until it passes the agent
  packet's allowed-path / forbidden-path / result-schema validation — a contract
  SignalOS already owns.

**Where SignalOS is currently behind:**

- **No live server-backed executor yet.** Multica's documented value is an
  autonomous runtime that actually claims, spawns, heartbeats, and streams a CLI
  across a fleet. SignalOS has the governance spine and the GC, but the live
  executor (R1) is roadmap. Until it lands, the SignalOS fleet is a governed
  *admission + housekeeping* foundation, not a running autonomous daemon.
