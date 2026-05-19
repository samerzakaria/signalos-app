<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Analytics Activation Card — Wave {N}

`Canonical path per Wave: core/governance/ANALYTICS_ACTIVATION_CARD.md (current) or core/governance/analytics-activation-cards/wave-{N}-card.md (archived) · Authored by: PO + Observability agent · Signed at: Gate 4 entry (before Build opens PRs) · Counter-signed: QA at Gate 5`

> The Analytics Activation Card declares — **before Build opens PRs** — what data this Wave needs to produce in order for its Belief to be resolvable. Which events fire, which metrics are tracked, which dashboards exist, which SLOs apply, how the Signal Window will actually observe the thing the Belief promised. Without this card, "ship" and "measure" drift apart and Waves close with no basis for Keep / Kill / Iterate.

---

## Front-matter

```yaml
wave: {N}
belief_id: {BEL-YYYYMMDD-N}
signal_window_days: {from BELIEF.md}
scale_track: quick | wave | campaign
author: {PO name}
observability_agent_run: {run ID or date}
date: YYYY-MM-DD
```

---

## Events to instrument

*Every event the Belief's "Signal to watch" depends on, plus edge-case counters needed to distinguish "no signal" from "broken instrumentation".*

| Event name | Fires when | Properties (required) | Owner to emit | Test coverage |
|---|---|---|---|---|
| {e.g. `register_success`} | {POST /auth/register returns 201} | `user_id`, `source`, `timestamp` | Build agent | Adversarial test verifies emission |
| {e.g. `register_validation_error`} | {POST /auth/register returns 4xx} | `error_code`, `timestamp` | Build agent | Unit test covers each error path |
| {e.g. `register_page_view`} | {Client loads /register} | `referrer`, `session_id` | Build agent | E2E test asserts fire |

*Events not listed here are not trusted for this Wave's Belief resolution.*

---

## Metrics derived from events

| Metric | Formula | Purpose in this Wave | Baseline |
|---|---|---|---|
| {e.g. `register_daily`} | `count(register_success) per day` | Signal Window threshold from BELIEF.md | {current value or "new"} |
| {e.g. `register_conversion`} | `register_success / register_page_view` | Secondary — sanity check funnel | |
| {e.g. `register_error_rate`} | `register_validation_error / total register attempts` | Quality check — fail-fast if > 5% | |

---

## Dashboards & queries

*Live before the feature ships — not built after the fact.*

| Artifact | Location | Live by (date) | Watched by |
|---|---|---|---|
| Signal Window dashboard | {e.g. Grafana `/d/wave-{N}-signal`} | {Gate 4 close} | PO daily + Observability agent |
| Saved query for Wave Debrief | {e.g. metabase #{NNN}, or `analytics/queries/wave-{N}-debrief.sql`} | {Gate 5 close} | PO at Wave Debrief |
| Alert — Signal Window stall | {e.g. Grafana alert `wave-{N}-no-signal-24h`} | {Deploy} | PO (email + slack) |

---

## SLOs & deploy-health gates

*The thresholds the Release agent and DevOps will enforce. If any of these breach post-deploy, the Release agent's auto-rollback T1 exception (Constitution §8.3) fires.*

| SLO | Steady-state target | Deploy-health window | Action if breached |
|---|---|---|---|
| {e.g. register endpoint p95 latency} | {≤ 200ms} | {first 60 min post-deploy} | Alert PE + auto-rollback if > 2× baseline |
| {e.g. register error rate} | {≤ 1%} | {first 60 min post-deploy} | Alert PE + auto-rollback if > 5% |
| {e.g. upstream identity-provider timeout rate} | {≤ 0.5%} | {first 60 min post-deploy} | Alert only — not auto-rollback (external dependency) |

---

## Data quality checks

*Protects against the "we shipped, nothing fired, we concluded 'users don't want it'" failure mode.*

- [ ] Smoke test: after deploy, Observability agent fires a synthetic `register_success` and verifies it appears in the Signal Window dashboard within 10 minutes
- [ ] Cardinality check: event property values stay within expected enum — no runaway string dimensions
- [ ] Completeness check: at 24h post-deploy, event count > 0 for every Signal Window event (else alert PO — likely instrumentation bug, not user behaviour)
- [ ] Time-zone sanity: daily rollups use the declared product time-zone, not server-local

---

## Signal Window readiness

*The Signal Window is defined in `core/strategy/BELIEF.md`. This section is the proof it can actually be observed.*

| Signal Window requirement | Status |
|---|---|
| Threshold numeric & unambiguous | ☐ confirmed |
| Event feeding threshold is in "Events to instrument" above | ☐ confirmed |
| Dashboard above shows current value live | ☐ confirmed |
| Alert fires if no data arrives in Signal Window | ☐ confirmed |
| PO knows where to read the result at Wave Debrief | ☐ confirmed |

A Signal Window with no live dashboard is a Wave with no basis for Keep / Kill / Iterate.

---

## Retention & privacy

| Dimension | Declaration |
|---|---|
| PII in events | {none / pseudonymised `user_id` only / other — list} |
| Retention window | {e.g. 90 days raw, 13 months aggregated} |
| Export path (if client owns the data) | {e.g. weekly S3 drop, or "N/A"} |
| DPIA / consent reviewed? | {Yes by {name — date} / N/A} |

---

## Gate 5 entry criteria (what QA + Observability will check to close Gate 5)

- [ ] Every event in "Events to instrument" is emitting in staging
- [ ] Dashboard shows live values (not empty / not "No data")
- [ ] Alerts configured and tested with synthetic breach
- [ ] Saved query for Wave Debrief runs without error
- [ ] Data quality checks above all pass

---

## Signatures

**I confirm this Wave's analytics plan makes its Belief resolvable. The Signal Window is observable. The dashboards are live before ship, not after.**

Signed (PO): __________  *Date: __________*

Signed (Observability agent run ID): __________  *Date: __________*

Counter-signed (QA at Gate 5): __________  *Date: __________*

---

## Amendment history

| Date | What changed mid-Wave | Signer |
|---|---|---|
| YYYY-MM-DD | Initial card | PO |
