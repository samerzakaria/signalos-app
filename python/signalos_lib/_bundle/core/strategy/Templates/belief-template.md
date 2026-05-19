<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Belief — Wave {N}

`Canonical path per Wave: core/strategy/BELIEF.md (current) or core/strategy/beliefs/wave-{N}-belief.md (archived) · Authored by: PO · Signed at: Gate 1`

> Every Wave carries a signed Belief. The Belief is the **smallest falsifiable sentence that justifies doing work**. The PO writes the Belief; data (not opinion) will kill it.

---

## Front-matter

```yaml
wave: {N}
scale_track: wave            # quick | wave | campaign
delivery_mode: fresh-wave    # fresh-wave | daemon
author: {PO name}
date: YYYY-MM-DD
```

---

## The Belief

> **We believe that {USER}** wants {OUTCOME} **because** {INSIGHT}. **We'll know we're right if** {METRIC} **moves by** {AMOUNT} **within** {TIMEFRAME}.

### Worked example

> *We believe that SMB marketers want one-click campaign summaries because they skip our weekly report 70% of the time. We'll know we're right if weekly-report opens rise by 25% within 2 weeks of launch.*

### Rules

1. **Falsifiable.** Data can kill it. If you can't kill it with data, rewrite it.
2. **Specific.** One user. One outcome. One insight. One metric. One time window.
3. **Time-bound.** No vague "over time" — a concrete window.

---

## Disproof condition

*What, specifically, would disprove this Belief? The PO must state this explicitly. If disproof is missing → Gate 1 blocks.*

> {e.g. "If weekly-report opens stay flat or fall within the 14-day window, the Belief is disproved and we Kill the bet — we do not iterate on this same feature."}

---

## Bet Score

```
Bet Score = (Risk × Impact) / Test Cost
```

| Factor | Value | Notes |
|---|---|---|
| **Risk** (1 safe → 5 existential) | | {one-line rationale} |
| **Impact** (1 nice-to-have → 5 strategic) | | {one-line rationale} |
| **Test Cost** (person-days to run one Wave) | | |
| **Bet Score** | | *Must be ≥ 1.0. If < 1, find a cheaper test first (smoke test, fake door, landing page, Wizard-of-Oz).* |

---

## Smallest Testable Build

*The minimum code that can generate the signal. If you can't falsify the Belief without building a full feature, the Belief is too big — split it.*

> {2–4 sentences describing the smallest build that still produces the metric}

**Heuristic:** one Belief ≤ 5 days of build time for one SignalOS squad.

---

## Signal threshold

| Metric | Direction | Threshold | Window | Data source |
|---|---|---|---|---|
| {metric} | ≥ / ≤ | {number} | {days / hours} | {analytics event or SQL} |

---

## User served (primary)

- **Persona:** {name + role}
- **Cohort definition:** {how we identify them in data}
- **Cohort size at ship:** {count}

---

## Constitution alignment

| Constitution clause | How this Wave complies |
|---|---|
| §1 Fail-hard default | Scale Track declared: **{quick | wave | campaign}** |
| §2.2 Permanently-T3 surfaces | Touched: {yes — which; or no} |
| §4 Gate 1 signature required | Signed below |
| §11 Scale Track ceiling | {e.g. "Quick track — T2 ceiling respected; no permanently-T3 surface touched"} |

---

## Gate 1 signature

**I confirm this Belief is falsifiable, time-bound, and measurable. I accept the disproof condition. I am prepared to Kill the bet if data so requires.**

Signed: __________  *PO — Date: __________*

*(Gate 2 Expectation Map + Gate 3 Design Approval follow in separate artifacts.)*
