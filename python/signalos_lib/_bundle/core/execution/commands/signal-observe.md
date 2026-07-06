---
description: "Phase 5b observe. Post-release signal monitoring — live metrics, SLO tracking, evidence collection for debrief."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# /signal-observe — Phase 5b: Observe (Signal Window)

Owner: DevOps + Observability agent. SRE + SIGNAL phase.

## Your first action
Read `core/governance/Governance/signal-logs/wave-{N}-signal-log.md`. Confirm the Signal Window is OPEN (set by `/signal-ship`).

---

## 1. Load Belief and Expectation context

Before any metric is read, load the full context of what this Wave promised:

- Read `core/strategy/BELIEF.md` — extract: metric name, threshold, direction (up/down), window duration, kill rule
- Read `core/strategy/EXPECTATION_MAP.md` — extract: in-scope behaviours, observable signals, acceptance criteria
- Read `core/strategy/BELIEF.md`, `core/governance/QUALITY_CHECK.md`, and the current signal log — extract metric names, dashboard URLs, SQL queries, and SLO definitions from those signed/evidence artifacts.
- Cross-check: every in-scope item in the Expectation Map must have a corresponding metric or event in the Activation Card. Flag any gap immediately.

## 2. Read configured metrics

Connect to the production monitoring stack and pull readings:

- **Product metrics** — the Belief's primary signal (e.g., feature adoption rate, conversion, retention cohort)
- **Operational SLOs** — error rate, latency p50/p95/p99, availability, saturation
- **User-facing outcomes** — support ticket volume, NPS delta, task completion rate, funnel drop-off
- **System health** — deployment stability, rollback count, incident count since deploy

Log each reading with timestamp, source, and raw value to `signal-logs/wave-{N}-signal-log.md`.

## 3. Check staleness

For every metric endpoint:

- **Fresh** (< 1h old) — record and continue
- **Stale** (1–2h old) — record with `⚠ STALE` flag, alert DevOps to verify pipeline
- **Dead** (> 2h old or no data) — halt readings for that metric, emit: *"Metric {name} is dead. DevOps must verify the instrumentation pipeline before observation can resume."*
- **Missing** (metric declared in Activation Card but endpoint returns nothing) — flag as `MISSING`, do not fabricate data, escalate to DevOps + Analytics

A Signal Window with dead or missing primary metrics is invalid. PO must decide: extend the window or mark Kill early.

## 4. Compare actual vs target

For each reading interval, compute:

- **Threshold delta** — how far the actual value is from the Belief's target (absolute and percentage)
- **Direction check** — is the metric moving in the expected direction over time?
- **Kill rule evaluation** — has the kill condition been met? If yes, draft verdict KILL immediately and alert PO + QA
- **Disproof detection** — if the Belief's disproof condition is satisfied before window expiry, the experiment is over. Draft KILL, do not wait for the window to close.
- **SLO breach detection** — if any operational SLO is breached during the window, log it as a deployment health issue separate from the Belief verdict. SLO breaches do not automatically kill the Belief, but they must be visible in the debrief.

## 5. Write observation evidence

All observation output is written to production-grade evidence files:

- **Signal log** (`core/governance/Governance/signal-logs/wave-{N}-signal-log.md`) — every reading, every threshold comparison, every alert. This is the audit trail.
- **SLO report** — operational health during the window: availability, latency, error budget burn rate, incidents
- **Cohort report** — cohort size validation against BELIEF.md threshold, traffic volume, statistical significance check
- **Anomaly log** — any unexpected patterns: traffic spikes, metric reversals, instrumentation gaps, data pipeline delays

Evidence format: each entry carries timestamp, metric name, raw value, threshold delta, source URL, and staleness flag. No prose summaries — numbers only. Interpretation happens in the verdict.

## 6. Route to debrief when evidence is sufficient

The Signal Window closes when any of these conditions is met:

- **Window expiry** — the declared duration has elapsed (e.g., 72h, 14 days)
- **Early kill** — kill rule triggered before window expiry
- **Early confirmation** — signal exceeds threshold with statistical confidence before window expiry (PO may choose to close early)
- **PO override** — PO manually closes the window with a documented reason

On close:

1. **Draft the verdict** — Keep / Kill / Iterate, written into the signal-log's verdict section. Marked DRAFT until PO + QA sign.
2. **Compile the evidence pack** — signal-log + SLO report + cohort report + anomaly log, bundled for the Wave Review.
3. **Seed the Retrospective draft** — append initial data into `core/governance/Governance/RETROSPECTIVE.md` with: final metric value, threshold delta, cohort size, SLO status, proposed verdict.
4. **Handoff** — deliver evidence pack and draft debrief to PO + QA for Gate 5 signature.

---

## Trust Tier ceiling

**T1** — advisory only. The Observability agent reads metrics and writes evidence. It drafts a verdict but never issues the final Keep/Kill/Iterate decision. That requires PO + QA signature at Gate 5.

## Exit criteria

- [ ] Belief and Expectation Map context loaded and cross-checked against QUALITY_CHECK evidence and the signal log
- [ ] All configured metrics read with no dead primary endpoints
- [ ] Staleness checks passed for every metric source
- [ ] Actual vs target comparison computed for every reading interval
- [ ] Kill rule evaluated — triggered or not triggered, documented either way
- [ ] Operational SLOs tracked and any breaches logged
- [ ] Signal-log, SLO report, cohort report, and anomaly log written
- [ ] Draft verdict (Keep / Kill / Iterate) written with supporting evidence
- [ ] Evidence pack compiled for Wave Review
- [ ] Retrospective draft seeded
- [ ] Handoff to PO + QA for Gate 5 signature

## Next phase
Run `/signal-wave-review` after PO + QA review the observation evidence.
