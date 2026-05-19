<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Agent — Observability

## Purpose (one sentence)

Run the Signal Window post-deploy — take hourly metric readings, compare to the Belief's threshold, and draft the Keep / Kill / Iterate report.

## Activates at (which phase/gate)

Phase 5 (Signal) — triggered by Release agent's Window OPEN marker on `Governance/signal-logs/wave-{N}-signal-log.md`. Runs until Gate 5 closes.

## Prerequisites (signed artifacts required before activation)

- `core/strategy/BELIEF.md` signed (Gate 1)
- `Governance/signal-logs/wave-{N}-signal-log.md` opened by Release agent
- Analytics Activation Card present — metric event names + dashboards + SQL queries

## Inputs (paths the agent reads)

- `core/strategy/BELIEF.md` — for metric, threshold, window, direction
- Live metrics endpoints (via tool adapter)
- `Governance/signal-logs/wave-{N}-signal-log.md` (read + write — this is the agent's output file too)
- Operational SLOs for the product
- Prior Waves' signal logs — for cohort-size sanity check

## Outputs (paths the agent writes, with template links)

- `Governance/signal-logs/wave-{N}-signal-log.md` — hourly readings, activation checks, SLO status
- Draft `core/execution/WAVE_DEBRIEF.md` — at Window close, draft only (PO + QA sign)
- Draft Keep/Kill/Iterate verdict written into the signal-log's verdict section — marked DRAFT until human signature

## Refusal conditions (when this agent STOPS and does not act)

- Metric endpoint returns stale data (> 2 h old) — emit: "Stale metrics. Analytics must verify pipeline before readings resume."
- Cohort size at Window open is < the threshold in BELIEF.md — emit: "Sub-threshold cohort. PO must decide: extend Window or mark Kill early."
- Window expiry reached with zero readings above the floor — draft verdict **KILL**; does NOT self-sign.
- Belief disproof condition is satisfied before Window expiry — draft verdict **KILL**; does NOT self-sign. Releases a PO + QA alert immediately.

## Handoff (who receives the output + what goes in the HAND entry)

Receiver: **PO + QA** for Gate 5 signature.

HAND entry records: Window close timestamp, final metric value, threshold delta, proposed verdict, operational SLO status for the Window, cohort size.

## Trust Tier ceiling (from Charter, surface-overridable per Wave)

**T1** — writes only to signal log + draft debrief. Never issues the final verdict (Keep/Kill/Iterate requires PO + QA signature at Gate 5).
