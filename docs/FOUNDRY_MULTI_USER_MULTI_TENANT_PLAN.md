# Foundry Multi-User / Multi-Tenant Fleet — Near-Future Plan

Status: **planned, not yet built.** This is a forward-looking plan, not a record of
shipped behavior. It extends [`GOVERNED_FLEET_RUNTIME_DESIGN.md`](GOVERNED_FLEET_RUNTIME_DESIGN.md)
(the single-machine governed-runtime foundation) to the multi-user / multi-tenant case.
Today Foundry is a single-user local desktop app (`v3.0.0-internal.*` beta); nothing in
this document is live yet.

Conventions: SignalOS enforces, never advises. Human-in-the-loop (HILP) gates stay
human-signed. No "sprint" vocabulary — work is organized as Waves, Phases, and Gates.

---

## 1. Goal

Let multiple users — and multiple machines — share a governed agent fleet, so that:

- a task list produced by the planner / delivery generation packet can be claimed and
  executed by a **fleet** of heterogeneous agent CLIs across machines, in parallel;
- every claim and state transition passes a **governance gate** and is recorded in a
  signed, append-only audit trail;
- **humans review the task list in and approve the output out** — the fleet only
  automates the middle (the HILP boundary the product already enforces for gate signing);
- progress is visible **live** in the existing UI tracker for every user with access.

Non-goal: removing the human from gate approval. The fleet scales *execution*, never
*sign-off*. An agent can never approve its own work — the same rule as the existing
agent-self-signature refusal in `ship`.

## 2. Where we are today (the foundation this builds on)

- **Tasks already exist.** The planner emits a validated tasks array; product delivery's
  generation packet is the task list (`task_ids` derived from the acceptance matrix). The
  multi-user work adds coordination, **not** task generation.
- **A single-agent executor already delivers.** `product/delivery.py` builds the
  generation + agent packet and `product/agent_dispatch.py` hands it to one agent; this is
  already gate-governed.
- **A governed-fleet foundation exists** (single machine): `fleet detect` (runtime
  detection), `fleet_runtime.governed_dispatch` (fail-closed admission), and `fleet gc`
  (workspace GC). See `GOVERNED_FLEET_RUNTIME_DESIGN.md`.
- **A live progress tracker already exists** for delivery: `product/delivery.py`
  `_emit_progress` streams structured `{kind:"progress", phase, step, status, message}`
  events, `DELIVERY_STATE.json` persists phase state, and the UI renders it
  (`src/services/macroProgress.ts`, `src/services/deliveryFlow.ts`,
  `src/components/ProgressDetail.tsx`). The fleet reuses this event shape.

## 3. Core design principle — abstract the substrate (`TaskStore`)

Do **not** hard-wire a backend. Define one interface and keep all governance above it:

```
trait TaskStore {
    claim(runtime_id, capabilities) -> Option<Task>   // atomic; never double-claims
    report(task_id, progress_event)                   // live tracker feed
    heartbeat(runtime_id)                             // liveness
    complete(task_id, result, evidence_ref)
    request_approval(task_id, kind)                   // HILP gate (task-list / output)
    record_audit(signed_event)                        // append-only
}
```

`governed_dispatch` and the gate/approval/audit logic sit **above** `TaskStore`. Then the
local/git/Postgres choice becomes a deployment decision, not a rewrite. Getting this
abstraction right now is the cheapest thing to do and the most expensive thing to retrofit.

Three implementations, in increasing capability:

| Store | Reach | Real-time | Throughput | New infra | Best for |
|---|---|---|---|---|---|
| **local-file** (`.signalos/fleet/`) | single machine | n/a (IPC) | low | none | today's single-user beta |
| **git** (GitHub repo/branches/PRs) | multi-machine | near-real-time (poll/webhook) | low–mid | none (GitHub hosts) | small teams; PR-as-approval |
| **postgres** (Rust service) | multi-user / multi-tenant | instant (LISTEN/NOTIFY → WS) | high | a real service | team / SaaS scale |

The two multi-user options follow.

## 4. Option A — Git as the coordination server (low infra)

Fits Foundry's DNA: everything is already signed files in git, plus worktrees + worktree-sync.

- **Tasks** = files under a control repo (`.signalos/fleet/queue/<task-id>.json`) **or**
  GitHub Issues (issue = task, assignee = claiming runtime, labels = state).
- **Claim** = first-push-wins. `git push` is a compare-and-swap: the loser's push is
  rejected and it retries / picks another task. Use **one file per task** and
  **branch-per-claim** so writers never collide.
- **Isolation** = per-task git worktree (already available).
- **Output + HILP gate** = the **Pull Request**. PR review/merge is the human approval;
  **branch protection requiring a human reviewer enforces "an agent cannot self-approve"**
  — exactly the existing agent-self-signature rule, now enforced by GitHub.
- **Liveness** = heartbeat commits to a `heartbeat/<runtime>` ref; staleness by timestamp.
- **Audit** = every transition is an immutable, attributable (signed) commit — stronger
  than a DB row.

**Limitations (decide with eyes open):**
1. Near-real-time only (poll cadence) unless you add GitHub **webhooks** → which need an
   HTTP endpoint (a thin server creeps back).
2. Push contention under high churn / many agents → mitigate with per-task files + branch-per-claim.
3. Conflict discipline: **one writer per file, append-only per-runtime shards** (the shared
   audit JSONL must become per-runtime files, not one shared file).
4. GitHub API rate limits → backoff, conditional/ETag requests, longer intervals.
5. No transactions / queries; everything modeled as files + commits.
6. History bloat → dedicated control repo, ephemeral branches deleted on completion, GC.
7. Secrets live in git history forever → private repo, no secrets in task payloads.
8. You depend on GitHub uptime/network (air-gapped = no coordination). Self-hosted
   Gitea/GitLab works identically.

**Verdict:** the right *first* multi-machine step — minimal infra, audit/HILP story is
actually stronger (PR review = approval). Ceiling = real-time + scale.

## 5. Option B — Rust + Postgres service (team / SaaS scale)

The faithful, better-governed SignalOS analog of Multica's Go+Postgres backend. Rust is the
natural choice because Foundry's desktop core is already Rust/Tauri (shared types, existing
competence).

### Stack
- **Server:** `axum + tokio` (HTTP + WebSocket); `sqlx` (compile-time-checked queries).
- **DB:** Postgres. `pgvector` only if semantic task/skill matching is wanted (optional).
- **Frontend:** the existing Tauri/React app, pointed at the server over WSS.
- **Daemon:** the local `fleet` runtime (detect → governed_dispatch → executor), talking to
  the server instead of a local queue.
- **Deploy:** `docker-compose` (server + Postgres), mirroring Multica's self-host compose.

### Schema (coordination + state + governance)
- `tenants` — tenant id, name, settings.
- `tasks` — id, tenant_id, wave_id, packet/payload, `status` (pending|claimed|running|done|failed),
  runtime_id, gate_state, priority, timestamps.
- `runtimes` — id, tenant_id, machine, cli, capabilities (JSONB), `last_heartbeat`, status.
- `events` — append-only; the live-stream + audit source (the `AUDIT_TRAIL.jsonl` equivalent,
  optionally hash-chained — a real home for an audit chain).
- `approvals` — HILP gates: task-list approval, output approval, signer, role, signature.

### The claim primitive (the reason a DB beats git for scale)
```sql
UPDATE tasks SET status='claimed', runtime_id=$1, claimed_at=now()
WHERE id = (
  SELECT id FROM tasks
  WHERE tenant_id=$3 AND status='pending' AND capabilities @> $2
  ORDER BY priority, created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
) RETURNING *;
```
`FOR UPDATE SKIP LOCKED` lets many runtimes claim concurrently with **no blocking and no
double-claim** — eliminating git's push-contention/retry problem.

### Real-time live tracker
Postgres **`LISTEN/NOTIFY`** → the Rust server fans changes to connected **WebSocket**
clients → the existing `ProgressDetail` / `macroProgress` UI updates **instantly**, no
polling. This is the live tracker that the git option cannot give without webhooks.

### Hybrid storage (keep git's strengths)
Postgres holds **coordination + state + audit**; **code artifacts + evidence stay in
git/worktrees**, with **PRs as the human review surface**. DB speed for the queue, git for
signed artifacts and review.

## 6. Multi-tenant isolation & auth

- **Identity:** OIDC / JWT at the server edge (reuse the roles the product already models —
  PO / PE / QA).
- **Tenant isolation:** Postgres **Row-Level Security** keyed on `tenant_id` (defense in
  depth on top of server-side scoping), so one tenant can never read/claim another's tasks.
- **RBAC:** the existing governance roles map to server permissions. Only the right human
  role can approve a given gate.
- **Per-agent credentials:** each runtime authenticates with a scoped token; capability
  declarations are server-verified, not self-asserted.

## 7. HILP enforcement at the server (not by convention)

These are server policies + DB constraints, so they cannot be bypassed by an agent:

- A task cannot reach `done` / `approved` without a **human approver of the correct role**.
- **An agent can never approve its own work** (DB constraint: approver_id != runtime's
  actor) — the same invariant as the `ship` agent-self-signature refusal.
- Every claim / transition / approval writes a **signed, append-only audit event**.
- The two human gates are explicit and visible in the tracker:
  **(1) approve the task list in, (2) approve the output out.** The fleet automates only
  the span between them.

## 8. Live tracker (UI)

Reuse the existing components. The board renders **tasks × runtimes × status**, fed by:
- local-file / git store → the current IPC / poll path;
- Postgres store → WebSocket events from `LISTEN/NOTIFY`.

Heartbeats surface stalled/dead agents as **stale** rather than silently hanging. The two
HILP gates appear as explicit review actions in the tracker.

## 9. Phased delivery (Waves & Gates — not sprints)

- **Phase 0 — Abstraction (prerequisite).** Land the `TaskStore` trait + move
  governed_dispatch / gate / audit logic above it. Reimplement today's local-file path
  behind the trait. Gate: existing single-user delivery still green.
- **Phase 1 — Live executor (single machine).** Build the roadmap executor (claim → spawn
  agent CLI in an isolated worktree → stream → heartbeat → complete) behind `TaskStore`.
  Gate: a real product delivered by the fleet on one machine, fully governed + tracked.
- **Phase 2 — Git store (multi-machine, small team).** Implement the git `TaskStore`
  (Option A) with PR-as-approval and branch-protection HILP. Gate: two machines safely
  claim/execute/approve without double-claim; audit intact.
- **Phase 3 — Rust+Postgres service (multi-tenant).** axum + sqlx + Postgres with
  `SKIP LOCKED` claims and `LISTEN/NOTIFY` → WS; OIDC/JWT + RLS multi-tenant; docker-compose
  deploy. Gate: N tenants, M machines, real-time tracker, server-enforced HILP, no
  cross-tenant leakage.
- **Phase 4 — Hardening.** Rate-limit/backoff (git), backups/migrations/monitoring
  (Postgres), audit-chain verification, load testing, security review.

Each phase ends at a human-signed Gate; nothing advances on agent self-approval.

## 10. Risks & limitations

- **Real-time vs infra trade-off:** git = near-real-time + low infra; Postgres = instant +
  a service to operate. Pick per audience.
- **Operational burden (Postgres path):** you now run a service — hosting, backups,
  migrations, monitoring, security. Not a desktop concern anymore.
- **Conflict-free design (git path):** requires strict one-writer-per-file / append-only
  shards; easy to get subtly wrong.
- **Secrets & privacy:** task payloads must avoid secrets (git history; tenant data).
- **The executor is the gating dependency:** none of the multi-user value lands until the
  live executor (Phase 1) exists. Multi-user without an executor is coordination over
  nothing.

## 11. Out of scope (for this plan)

- The autonomous executor itself is specified in `GOVERNED_FLEET_RUNTIME_DESIGN.md`; this
  plan covers only the **multi-user / multi-tenant coordination + governance** layer on top.
- Squad-style routing (grouping runtimes under a leader) is a later refinement once the
  Postgres store exists.

## 12. Honest comparison vs Multica

| Dimension | Multica | This plan |
|---|---|---|
| Coordination backend | Go + Postgres server | Rust + Postgres (Option B) or git (Option A) |
| Claim primitive | Postgres queue | `SELECT … FOR UPDATE SKIP LOCKED` / git CAS |
| Real-time | WebSocket | LISTEN/NOTIFY → WS (Postgres) / webhook (git) |
| Governance on each claim | none (ungoverned) | **gate + signed audit, fail-closed** |
| Human approval | optional | **mandatory HILP gates; agent self-approval blocked** |
| Multi-tenant isolation | workspace-level | tenant RLS + RBAC roles |
| Artifacts | workspace dirs | git/worktrees + PR review (hybrid) |

**Better:** governed, fail-closed, audited, HILP-enforced, and it reuses Foundry's existing
git/worktree/signature/role machinery. **Missing today:** the live executor (roadmap) and
the server itself (a real build + ops commitment). The plan sequences both so the
governance layer never has to be rewritten when the substrate changes.
