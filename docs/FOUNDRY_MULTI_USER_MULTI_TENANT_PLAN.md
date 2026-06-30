# Foundry Multi-User / Multi-Tenant Fleet — Near-Future Plan (v2, research-validated)

Status: **planned, not yet built.** Forward-looking plan, not shipped behavior. It extends
[`GOVERNED_FLEET_RUNTIME_DESIGN.md`](GOVERNED_FLEET_RUNTIME_DESIGN.md) (the single-machine
governed-runtime foundation) to the multi-user / multi-tenant case. Today Foundry is a
single-user local desktop app (`v3.1.0-internal.*`); nothing here is live yet.

**v2 changes (this revision):** every load-bearing decision was validated against current
domain best practice (sources at the bottom), not first-principles reasoning. Three things
changed materially: **Phase 2 (git-as-queue) is dropped**; the live-tracker transport is
corrected (`LISTEN/NOTIFY` is a *hint*, not the source of truth); and **multi-tenant agent
execution requires microVM isolation**, not shared-kernel Docker. The rest is hardened with
idempotency / lease / dead-letter / RLS best practices.

Conventions: SignalOS enforces, never advises (fail-closed). HILP gates stay human-signed.
No "sprint" vocabulary — Waves, Phases, Gates.

---

## 0. The reality check that justifies the whole approach

As of early 2026, **only ~11–14% of enterprise AI-agent pilots reach production at scale; ~86–89% fail to realize durable value — and the hard parts are not the agent loops, they're "memory propagation, retry semantics, observability, and human-in-the-loop gating"** ([orchestration patterns 2026](https://jobsbyculture.com/blog/ai-agent-orchestration-patterns-2026)). That is *exactly* the layer Foundry already invests in (gates, audit, proof, HILP). So this plan deliberately spends effort on the boring robustness — idempotency, leases, dead-letter, durable execution, tenant isolation — because that, not the LLM, is where agent products die.

## 1. Goal

Let multiple users and machines share a governed agent fleet so the planner's task list is
claimed and executed by a **fleet** of heterogeneous agent CLIs in parallel, every claim and
transition passes a **gate** and is recorded in a signed audit trail, **humans approve the
task list in and the output out**, and progress is visible **live**. The fleet scales
*execution*, never *sign-off*; an agent can never approve its own work (same invariant as the
`ship` agent-self-signature refusal).

## 2. What already exists (the foundation)

- **Tasks already exist** — planner tasks array + the delivery generation packet (the
  `task_ids` derived from the acceptance matrix, as seen in the live `--agent none` smoke).
  This plan adds coordination, **not** task generation.
- **A single-agent executor already delivers** (`delivery.py` → `agent_dispatch.py`).
- **A governed-fleet foundation** (single machine): `fleet detect`, `governed_dispatch`
  admission, `fleet gc`. See `GOVERNED_FLEET_RUNTIME_DESIGN.md`.
- **A live progress tracker** for delivery: `_emit_progress` events + `DELIVERY_STATE.json`
  plus UI (`macroProgress.ts`, `deliveryFlow.ts`, `ProgressDetail.tsx`).
- **A Docker sandbox** (`src-tauri` `sandbox::tests`) — adequate for single-user; see §7 for
  the multi-tenant upgrade.

## 3. Core principle — one abstraction, but don't over-abstract it

Define a single `TaskStore` and keep governance above it, so local/Postgres is a deployment
choice, not a rewrite. **Best-practice constraint (from the queue/outbox literature):** the
abstraction must NOT prevent **transactional enqueue** — the queue should live in the *same*
Postgres as wave/delivery state so a task and its business state commit atomically (both or
neither). River and the transactional-outbox pattern both make this the headline rule
([River](https://github.com/riverqueue/river), [outbox](https://tiarebalbi.com/en/blog/the-transactional-outbox-is-not-a-queue)). A store interface so abstract it forces a network hop per enqueue throws this away.

```rust
trait TaskStore {
    // idempotent: same task_id enqueued twice is a no-op (at-least-once world)
    enqueue(task) -> TaskId
    // atomic claim; never double-claims; sets a lease/visibility deadline
    claim(runtime_id, capabilities, lease_ttl) -> Option<LeasedTask>
    heartbeat(lease_id)          // renews the lease for long agent runs
    report(task_id, progress)    // live-tracker feed
    complete(task_id, result, evidence_ref)
    fail(task_id, error, retryable)   // -> backoff+retry OR dead-letter
    request_approval(task_id, kind)   // HILP gate (task-list / output)
    record_audit(signed_event)        // append-only
}
```

Three job-semantics best practices are baked into the interface from day one (so every impl
shares them), because retrofitting them is painful ([Background Jobs 2026](https://www.digitalapplied.com/blog/background-job-queue-patterns-2026-engineering-reference), [idempotency/DLQ](https://baxchain.com/blogs/resilient-event-driven-architecture-idempotency-retries-and-dead-letter-queues/)):

1. **At-least-once + idempotent execution.** Don't chase exactly-once. Every task has a
   stable id; re-running a task is safe; record processed ids with a unique constraint so a
   duplicate is a no-op.
2. **Lease / visibility timeout via heartbeat.** A claimed task carries a lease longer than
   its expected runtime; if the heartbeat lapses (dead agent), the task becomes reclaimable.
   This is what stops a crashed runtime from holding work forever.
3. **Dead-letter on permanent failure.** Transient errors (LLM rate-limit, network) retry
   with exponential backoff + jitter, capped; permanent failures (e.g. still failing after N)
   route to a dead-letter state surfaced to a human — never silently dropped or retried
   forever. A growing dead-letter set is the leading failure indicator.

## 4. Phase 0 — TaskStore abstraction (prerequisite gate)

- Land the trait above + the governance layer (gate / approval / audit) over it.
- Reimplement today's local path behind it (no behavior change for single-user).
- Bake in idempotent enqueue, lease/heartbeat, retry+backoff+jitter, dead-letter.
- **Outbox mental model** ([source](https://tiarebalbi.com/en/blog/the-transactional-outbox-is-not-a-queue)): the active-task table is "a bounded, append-mostly table whose rows live seconds, not minutes." Keep it small; move completed/failed rows to a history table; monitor depth + oldest-row age.

Gate: existing single-user delivery still green. **User-visible value: none directly** — this
is the safety harness that lets Phase 1 land without destabilizing the shipped delivery path.

## 5. Phase 1 — Live executor (single machine), supervisor/worker

The executor that claims tasks, runs an agent CLI in an **isolated git worktree**, streams,
heartbeats, and completes — wrapped in governance. Architecture = the **supervisor/worker**
pattern, which the orchestration literature identifies as the right fit precisely when you
have "a homogeneous pool of agents doing similar work (code generation), variable quality
requiring gating, and dynamic tasks needing load balancing" — the supervisor retries,
reassigns, or **escalates to a human** on poor output ([patterns 2026](https://jobsbyculture.com/blog/ai-agent-orchestration-patterns-2026)).

Best practices to build in:

- **Worktrees for per-task isolation** — a recommended parallel-AI-agent pattern ([Augment](https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution)); Foundry already has worktree-sync. Keep it.
- **Durable execution** for crash recovery + HILP pauses — a Temporal-style durable state
  machine wrapping the agent loop so a server/desktop restart resumes mid-delivery and an
  approval pause survives crashes ([control-plane/durable](https://jobsbyculture.com/blog/ai-agent-orchestration-patterns-2026)). For single-machine this can be a lightweight local-store-backed state machine, not a Temporal dependency.
- **Sandbox (single-user threat model):** Docker + worktree is acceptable here because the
  user runs their own agents on their own machine. **But this changes at multi-tenant — see §7.**
- Idempotent task execution, lease heartbeats, retry/backoff/jitter, dead-letter (from §3).

Gate: a real product delivered by the parallel fleet on one machine, fully governed +
tracked. **User-visible value: faster deliveries (parallel tasks), per-task robustness, and a
task-level live tracker** — a genuine upgrade to the current release.

## 6. Phase 2 — REMOVED (git-as-coordination-queue), replaced by "git for what it's good at"

The v1 plan proposed git/GitHub as a multi-machine claim queue. **Validation says don't.** The
domain evidence is one-sided:

- Git-as-a-database is a repeatedly-failed pattern — "package managers keep using git as a
  database, **it never works out**"; "databases have locking, git doesn't"; and the
  documented immediate failure for *multi-agent concurrent access* is "**simultaneous writes
  to the same file where one agent's changes silently overwrite another's**" — i.e. exactly
  the claim race ([Nesbitt](https://nesbitt.io/2025/12/24/package-managers-keep-using-git-as-a-database.html), [DB-as-queue anti-pattern](http://mikehadlow.blogspot.com/2012/04/database-as-queue-anti-pattern.html)).
- Postgres `SKIP LOCKED` is the proven, standard claim substrate (Oban/River/Graphile/
  GoodJob/pg-boss), atomic and race-free, comfortably past this domain's throughput
  ([dbpro](https://www.dbpro.app/blog/postgresql-skip-locked), [10k/sec](https://gist.github.com/chanks/7585810)).

**Keep git for what the evidence says it *is* good at, not as the queue:** worktrees for
per-task isolation, and **PRs as the human-approval (HILP) surface** (review + signed
artifacts). So the architecture is **Postgres for coordination, git/worktrees for isolation,
and PRs for approval** — go straight from Phase 1 to Phase 3.

## 7. Phase 3 — Rust + Postgres multi-tenant service

The faithful, better-governed analog of Multica's Go+Postgres backend. Rust because Foundry's
desktop core is already Rust/Tauri (shared types, existing competence).

### Stack

- **Server:** `axum + tokio`; `sqlx` (compile-time-checked queries). **DB:** Postgres
  (managed — Neon/Supabase/RDS — if you want the multi-machine step with zero DB ops).
- **Frontend:** existing Tauri/React app over WSS. **Daemon:** the Phase-1 `fleet` runtime,
  now talking to the server. **Deploy:** docker-compose (server + Postgres).

### Coordination queue (best practices)

- `SELECT … FOR UPDATE SKIP LOCKED` for atomic, race-free claims, scoped by `tenant_id` +
  capability match.
- **Transactional enqueue** in the same DB as wave state (§3).
- Keep the active table small; archive completed; **partition only above ~10k writes/sec**
  (this domain is orders of magnitude below that, so partitioning is a non-issue); monitor
  depth/oldest-age; watch the `xmin` horizon (long-running queries elsewhere stall the queue)
  ([outbox ops](https://tiarebalbi.com/en/blog/the-transactional-outbox-is-not-a-queue)).

### Live tracker transport — CORRECTED from v1
v1 said "`LISTEN/NOTIFY` → WebSocket." **Best practice: `NOTIFY` is a low-latency *wake-up
hint*, not the source of truth.** It does not scale past ~1,000 notifications/sec, takes a
**global lock that serializes NOTIFY transactions**, **does not persist** (lose it if no
listener), caps payload at **8 KB**, and is **unsupported through pgbouncer transaction-mode
pooling / some managed Postgres** ([does-not-scale](https://lobste.rs/s/pzqxqm/postgres_listen_notify_does_not_scale), [scaling NOTIFY](https://pgdog.dev/blog/scaling-postgres-listen-notify), [real-time guide](https://www.jusdb.com/blog/postgresql-listen-notify-realtime-events)). So:

- Use `NOTIFY` to carry a **signal + a task id**, not state; the WS bridge re-reads the row.
- Back it with **durable state + a polling fallback** for guaranteed delivery: "real-time
  notifications run jobs immediately; occasional polling for abandoned entries ensures all
  are eventually processed" ([source](https://www.jusdb.com/blog/postgresql-listen-notify-realtime-events)).
- For horizontal scale-out (multiple server instances), if pooling kills `NOTIFY`, fan out
  via an in-process broadcast or a small pub/sub — don't assume `NOTIFY` reaches every worker.

### Multi-tenant isolation (RLS) — best practices

- **`SET LOCAL` per transaction, never `SET`** — with pooling, plain `SET` leaks the previous
  tenant's context into the next request; this is "the single most common way teams
  accidentally break tenant isolation" ([RLS mastery](https://ricofritzsche.me/mastering-postgresql-row-level-security-rls-for-rock-solid-multi-tenancy/)).
- **`FORCE ROW LEVEL SECURITY` + a non-owner app role** (the table owner silently bypasses
  RLS).
- **Composite indexes with `tenant_id` as the leading column** — the #1 perf killer;
  without it RLS is ~2 orders of magnitude slower; with it, no measurable degradation at 500
  connections ([AWS RLS](https://aws.amazon.com/blogs/database/multi-tenant-data-isolation-with-postgresql-row-level-security/)).
- **No tenant context → zero rows (fail-closed, secure by default)** — this matches
  SignalOS's fail-closed ethos exactly; an unset GUC must return nothing, never everything.
- **Audit every `SECURITY DEFINER` function** against RLS tables — runs as owner, "the most
  common way to accidentally hand out cross-tenant access."
- **Simple role model:** one app role + migration/admin roles; **no per-tenant DB users**
  (pooling explosion).

### Agent execution sandbox — SECURITY-CRITICAL upgrade for multi-tenant
At multi-tenant, tenant A's agent runs code that must never touch tenant B. **Shared-kernel
Docker is explicitly insufficient for untrusted agent-generated code** — a container escape
yields full host access ([Northflank](https://northflank.com/blog/how-to-sandbox-ai-agents), [Augment sandbox](https://www.augmentcode.com/guides/agent-execution-sandbox)). Best practice:

- **MicroVMs (Firecracker / Kata) are the de-facto standard** for executing untrusted
  LLM-generated code (hardware boundary; ~125 ms boot, <5 MiB overhead; reportedly ~50% of
  Fortune 500 for agent workloads).
- **gVisor** is the acceptable lighter middle ground (user-space kernel, syscall interception).
- **Standard Docker = minimum, single-tenant-only.** So: single-user local (Phase 1) may keep
  Docker; **multi-tenant cloud (Phase 3) requires microVM/gVisor per agent run.** This is a
  hard requirement, not a nice-to-have.

### Auth & HILP at the server

- OIDC/JWT at the edge; short-lived, per-agent scoped tokens; capability claims server-verified.
- HILP as **server policy + DB constraints**: a task can't reach `approved` without a human
  approver of the correct role; `approver_id != runtime_actor` (agent-self-approval blocked at
  the DB) — the same invariant as `ship`. Every transition writes a signed, append-only audit
  event.

Gate: N tenants, M machines, real-time tracker, server-enforced HILP, microVM isolation, no
cross-tenant leakage (proven by an explicit negative test).

## 8. Phase 4 — Hardening

Backups/migrations/monitoring (Postgres); queue depth + DLQ alerting + replay; audit-chain
verification; load test at realistic fleet volume; and a **multi-tenant security review of
auth + RLS + sandbox escape** (human-heavy, the one thing that must not be rushed).

## 9. Phased delivery (Waves & Gates)

- **Phase 0 — TaskStore abstraction + job semantics.** Gate: single-user delivery still green.
- **Phase 1 — Supervisor/worker live executor (single machine), Docker sandbox.** Gate: a
  real product delivered by the parallel governed fleet on one machine.
- **Phase 3 — Rust+Postgres multi-tenant service** (SKIP LOCKED + transactional enqueue;
  NOTIFY-as-hint + durable state + poll; RLS per best practices; microVM sandbox; OIDC/JWT;
  WS tracker). Gate: multi-tenant e2e with no cross-tenant leak.
- **Phase 4 — Hardening + security review.**

(Phase 2 intentionally absent — see §6.) Each phase ends at a human-signed Gate; nothing
advances on agent self-approval.

## 10. Honest comparison vs Multica

| Dimension | Multica | This plan (validated) |
|---|---|---|
| Coordination backend | Go + Postgres | Rust + Postgres (`SKIP LOCKED` + transactional enqueue) |
| Real-time | WebSocket | `NOTIFY` *as a hint* + durable state + poll → WS (not NOTIFY-as-truth) |
| Job semantics | queue | at-least-once + idempotent + lease/heartbeat + dead-letter |
| Governance per claim | none | **gate + signed audit, fail-closed** |
| Human approval | optional | **mandatory HILP; agent self-approval blocked at the DB** |
| Tenant isolation | workspace-level | RLS (SET LOCAL / FORCE / non-owner / tenant-leading index / zero-rows-default) |
| Agent code execution | container | **microVM/gVisor for untrusted multi-tenant code** |
| Artifacts | workspace dirs | git/worktrees + PR review (isolation + HILP surface) |

**Better:** governed, fail-closed, audited, HILP-enforced, tenant-isolated, sandbox-hardened —
reusing Foundry's git/worktree/signature/role machinery. **Missing today:** the live executor
(roadmap) and the server (a real build + ops + security commitment). The plan sequences both
so the governance layer never gets rewritten when the substrate changes.

---

## Sources (domain best practices this plan is validated against)

Postgres queue / coordination: [SKIP LOCKED job queue](https://www.dbpro.app/blog/postgresql-skip-locked) · [River](https://github.com/riverqueue/river) · [Graphile Worker](https://github.com/graphile/worker) · [Choose Postgres queue tech (HN)](https://news.ycombinator.com/item?id=37636841) · [10k jobs/sec](https://gist.github.com/chanks/7585810) · [You don't need Kafka… considered harmful](https://www.morling.dev/blog/you-dont-need-kafka-just-use-postgres-considered-harmful/)
Git-as-database anti-pattern: [Package managers keep using git as a database](https://nesbitt.io/2025/12/24/package-managers-keep-using-git-as-a-database.html) · [Database-as-Queue anti-pattern](http://mikehadlow.blogspot.com/2012/04/database-as-queue-anti-pattern.html) · [Git worktrees for parallel AI agents](https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution)
LISTEN/NOTIFY: [does not scale](https://lobste.rs/s/pzqxqm/postgres_listen_notify_does_not_scale) · [scaling NOTIFY](https://pgdog.dev/blog/scaling-postgres-listen-notify) · [real-time events guide](https://www.jusdb.com/blog/postgresql-listen-notify-realtime-events)
Multi-tenant RLS: [Mastering RLS](https://ricofritzsche.me/mastering-postgresql-row-level-security-rls-for-rock-solid-multi-tenancy/) · [AWS: RLS data isolation](https://aws.amazon.com/blogs/database/multi-tenant-data-isolation-with-postgresql-row-level-security/)
Agent sandboxing: [How to sandbox AI agents (Northflank)](https://northflank.com/blog/how-to-sandbox-ai-agents) · [Agent execution sandbox (Augment)](https://www.augmentcode.com/guides/agent-execution-sandbox) · [AI agent sandbox guide](https://www.firecrawl.dev/blog/ai-agent-sandbox)
Outbox / job semantics: [The transactional outbox is not a queue](https://tiarebalbi.com/en/blog/the-transactional-outbox-is-not-a-queue) · [Push-based outbox (Postgres logical replication)](https://event-driven.io/en/push_based_outbox_pattern_with_postgres_logical_replication/) · [Background Jobs 2026 reference](https://www.digitalapplied.com/blog/background-job-queue-patterns-2026-engineering-reference) · [Idempotency, retries, DLQ](https://baxchain.com/blogs/resilient-event-driven-architecture-idempotency-retries-and-dead-letter-queues/)
Orchestration: [AI agent orchestration patterns 2026](https://jobsbyculture.com/blog/ai-agent-orchestration-patterns-2026)
