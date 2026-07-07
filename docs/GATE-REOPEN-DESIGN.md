# Gate Reopen — design contract (#4 + #5)

Source of truth for the reopen state machine, budgets, audit events, UI contract.

## State machine

A delivery (`GateOrchestrator` / `.signalos/agent-runs/<run_id>/delivery.json`)
may be reopened only while **parked**: status `awaiting-verdict`, `stopped`,
`complete`, or `reopened` — never `active` (gate agent mid-run).
`reopen_gate(gate, reason, name?, role?)` on a signed gate:

1. **Validate** — gate in GATE_ORDER (`unknown-gate`), gate in `state.signed`
   (`not-signed`), delivery parked (`delivery-busy`).
2. **Authorize** — `role` must be in the union of `required_roles` across the
   gate's manifest artifacts (the same set `sign.sign_gate` enforces).
   Refusal: `role-not-authorized`. Empty role defaults to `GATE_ROLES[gate]`.
3. **Budget** — per-gate counter `state.reopens[gate]`; budget
   `resolve_gate_reopen_budget` (default **3**, env
   `SIGNALOS_GATE_REOPEN_BUDGET`). Exceeding: `max-reopens`, audited
   (`gate-reopen-refused` — carries `gate_id` not `gate`), **no state change**.
4. **Cascade** — the target gate and every LATER signed gate lose their
   signature; later waived gates are un-waived. Every removal appends
   `{"gate","by","reason","cascade"[, "waived"]}` to `state.invalidated`
   and one audit row.
5. **Thread the reason** — stored via `_record_review(gate, "reopen", reason,
   n)` so `_gate_message` includes it verbatim when the gate re-runs
   (`[Reopen n - …]` header; audit event `gate_review` verdict `REOPEN`).
6. **Park** — `current_gate` ← reopened gate, status ← `reopened`, persist.
   The gate agent is **not** re-run; the caller resumes.

`resume_delivery` restores `reopens` / `invalidated`; older delivery.json files without those fields resume with `{}` / `[]`.

## Audit event kinds (`.signalos/AUDIT_TRAIL.jsonl`)

| kind | action | replay effect |
|---|---|---|
| `reopen` (target gate) | `gate.reopen` | un-signs `gate` (reverse marker "reopen") |
| `invalidate` (cascaded signed gate) | `gate.unsign` | un-signs `gate` (reverse marker "unsign") |
| `unwaive` (cascaded waived gate) | `gate.unwaive` | none (waivers aren't replay state) |
| `reopen-refused` (budget hit) | `gate-reopen-refused` | none (`gate_id`, no `gate` key) |

Rows carry `ts, run_id, gate, actor, role, reason, cascade, source_gate`;
`audit_replay._REVERSE_MARKERS` recognizes "reopen"/"unsign" for time travel.

## IPC: `agent:reopen-gate`

Args (one JSON object): `{run_id, gate, reason, name?, role?}` — `reason`
required. Active deliveries are mutated in place; otherwise the persisted
delivery.json is loaded one-shot (no provider/model needed — reopen never
calls the model) and persisted back. No delivery → clear error.
**Frontend next step** after a successful reopen (either):
- `agent:resume {run_id, provider, model}` — re-emits the reopened gate's
  checkpoint card and waits for a verdict; or
- `agent:verdict {run_id, verdict:"request-changes", feedback}` — re-runs
  the gate agent immediately with the reopen reason threaded in.

**Events the frontend receives** (agent-event envelope):
- `gate_reopened` — `{gate, invalidated: [...], reason, by, role,
  reopen_count}`; render as a governance card listing what was invalidated.
- `system` — plain-words one-liner of the same fact (renders today as-is).
- `error` — refusals (`not-signed`, `role-not-authorized`, `max-reopens`,
  `delivery-busy`), which are also the command's `data.status`.

## Scope drift vs later signed gates (#5)

`wave_engine.detect_scope_drift` now also compares the request against
signed **G2** (plan) and **G3** (design) artifacts. Conservative on purpose:
fires only when the request contains explicit contradiction language
(`_CONTRADICTION_MARKERS`) **and** names the gate's subject matter
(`_LATER_GATE_SUBJECTS`); plain refinements never trip it (ambiguity falls
through to the existing conservative no-drift default). A hit returns
`drifted: true, confidence 0.85, conflicting_gate: "G2"|"G3",
conflicting_summary, recommended_action: "reopen-gate"`.

`resolve_scope_drift` (and IPC `wave:scope-drift-resolve`) accepts a 5th
choice `e` / `reopen`: returns `{action: "reopen-gate", gate:
<conflicting_gate>, reason: <the request>}` — the caller then invokes
`agent:reopen-gate`; the engine never rewrites signatures itself. Existing
choices a–d are unchanged.
