<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Signal Log — Wave {N}

`Path per Wave: core/governance/Governance/signal-logs/wave-{N}-signal.md · Authored by: Observability agent + PO · Updated: continuously during the Signal Window`

> Every Wave defines a **Signal Window** (default 72 hours after ship). During the window, the Observability agent drafts this log continuously; the PO reads it at Wave close to sign Keep / Kill / Iterate. Silence in the window is itself a signal.

---

## Wave metadata

| Field | Value |
|---|---|
| Wave | {N} |
| Belief | {paste from `core/strategy/BELIEF.md` — problem · bet · signal} |
| Scale Track | Quick / Wave / Campaign |
| Ship date | YYYY-MM-DD HH:MM {timezone} |
| Signal Window | {e.g. 72 hours — closes YYYY-MM-DD HH:MM} |
| Instrumentation source | {e.g. PostHog, Mixpanel, Datadog} |

---

## Signal thresholds (from Belief)

| Clause | Metric | Direction | Amount | Window | Disproof condition |
|---|---|---|---|---|---|
| 1 | {e.g. D1 retention of cohort X} | ≥ | 42% | 7 days | {what would kill the Belief} |

---

## Live activation checks (filled at ship + 1h)

| Check | Status | Evidence |
|---|---|---|
| Instrumentation fires in prod | ⬜ | {link or event ID} |
| Dashboards render real data | ⬜ | {link} |
| Alert thresholds active | ⬜ | {link} |
| Error rate baseline | ⬜ | {number} |

---

## Hourly / daily readings

| Timestamp | Metric | Value | Commentary |
|---|---|---|---|
| YYYY-MM-DD HH:MM | {metric} | {value} | {1-line observation} |

*(Observability agent appends. No hand-wavy language — "looks good" is rejected. Cite a number and a range.)*

---

## Operational signals

| SLO | Target | Actual | Status |
|---|---|---|---|
| {e.g. API p95 latency} | < 200 ms | {number} | ✅ / ⚠️ / ❌ |
| {e.g. Error rate} | < 0.1% | {number} | |

---

## Client Signal Log cross-reference

*Entries in `core/governance/Governance/CLIENT-SIGNAL-LOG.md` filed during this Wave's Signal Window:*

- SIG-{NNNN} — {short title}
- …

---

## Window close verdict

| Clause | Threshold | Actual | Verdict |
|---|---|---|---|
| 1 | {from Belief} | {final value} | Keep / Kill / Iterate |

**Overall verdict:** Keep / Kill / Iterate  
**Rationale (2–4 sentences):** {why the data supports this verdict}

---

*Signed at Window close:* __________  *PO — Date:* __________  
*Instrumentation signed-off:* __________  *Observability agent owner (PO) — Date:* __________
