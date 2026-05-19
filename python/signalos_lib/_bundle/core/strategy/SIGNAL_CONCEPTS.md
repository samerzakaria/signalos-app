# Strategy strand — SIGNAL concepts (self-contained reference)

This file keeps SignalOS fully standalone. Everything you need from the SIGNAL strand is summarised here — no external doc required.

---

## 1. Soul Document
A one-page, pasteable brief for any AI session. Four sections:

```
### Stack & constraints
- Language / framework / infra
- Hard constraints (compliance, SLA, budget)

### Closed decisions (not up for debate)
- …

### Open questions
- …

### The point (2 sentences)
…
```

**Rule:** if it's longer than one page, it's not a Soul Document — it's a wiki.

---

## 2. Belief Statement
The smallest falsifiable sentence that justifies doing work.

> **"We believe that [USER] wants [OUTCOME] because [INSIGHT]. We'll know we're right if [METRIC] moves by [AMOUNT] within [TIMEFRAME]."**

### Examples
- *"We believe SMB marketers want one-click campaign summaries because they skip our weekly report 70% of the time. We'll know we're right if weekly-report opens rise by 25% within 2 weeks of launch."*
- *"We believe paying users want export-to-PDF because 18% of churn surveys cite 'can't share output offline'. We'll know we're right if 30-day retention of new Pro users rises by 4pp within 30 days of launch."*

A Belief is **falsifiable** (data can kill it), **specific** (one user, one metric), and **time-bound**.

---

## 3. Bet Score
A Belief is only worth running if:

```
Bet Score = (Risk × Impact) / Test Cost  >= 1.0
```

- **Risk** — 1 (safe) to 5 (existential)
- **Impact** — 1 (nice-to-have) to 5 (strategic)
- **Test Cost** — person-days to run one wave

If Bet Score < 1, find a cheaper way to test the Belief first — a smoke test, a fake door, a landing page, a Wizard-of-Oz.

---

## 4. Expectation Map
Two columns on one page. The client signs it.

| What the client expects | What we are actually building |
|---|---|
| "Users can share reports by email." | "A share button that copies a signed URL to clipboard." |
| "It works on mobile." | "Core flow responsive down to 375px — no native app." |

Any row the client redlines is a surfaced risk **before** a line of code is written. That is the point.

---

## 5. Role Activation Card
Fill in per wave. See `../Guidelines/ROLES.md` for full detail and the canonical template.

Five intensity levels: **Active · AI-heavy · Minimal · Deferred · External**.

---

## 6. Worlds
"Worlds" is SignalOS's concept of focused lenses. Each World has its own language, artifacts, and gating concerns. SignalOS v1.0 collapses what earlier drafts listed as 11 Worlds into four human *seats* that together carry all the concerns — plus the ten agent seats (see `../Team-Charters/`).

| World / concern | Human seat | Agent sub-role(s) that execute |
|---|---|---|
| Belief · Expectation · Analytics · Orchestration | **PO** | Brainstorm · Observability |
| Architecture · Planning · Build · Worktree hygiene · Security | **PO** | Plan (Architecture · Data · Security) · Build ×N · Worktree-Sync · Security |
| Testing · Quality review · Quality Check (Gate 5) | **PO** | Test · Review |
| Deploy · Rollback · Release health | **PO** | Release |

The older 11-World list (PO · BA · Architect · Developer · QA · QC · Analytics · DevOps · Security/Legal · Process Governor · Artifacts Manager) is provenance only. Under v1.0 (AMD-CORE-038): all execution concerns are agent sub-roles owned by the PO, the sole human seat.

A wave may only *activate* a subset of these concerns. That's what the Role Activation Card decides.

---

## 7. Wave Review (keep / kill / iterate)
At end of Phase 6, measure the signed metric against its threshold:

| Outcome | Signal | Decision |
|---|---|---|
| Metric crossed threshold in time window | **KEEP** | Ship, double down, write the next Belief on top |
| Metric did NOT cross | **KILL** | Harvest learning, write a *different* Belief, do not iterate on the same bet |
| Metric partially crossed / mixed | **ITERATE** | Refine the Belief (tighter segment, different metric), run one more wave |

> "Iterate" is a trap if used more than twice on the same Belief. After two iterations without signal, kill it.

---

## 8. Smallest Testable Build
The minimum code that can generate the signal. If you can't falsify the Belief without building a full feature, your Belief is too big — split it.

Heuristic: **one Belief ≤ 5 days of build time** for one SignalOS squad.
