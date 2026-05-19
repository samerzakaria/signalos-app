<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Trust Tier Scoring

Updated at: every Wave Debrief. Read at: every Phase 2 Plan (tier declaration).

Scoring concept adapted from AI-SDLC progressive autonomy system.

---

## Current recommended tier

> **Read this first at Phase 2 Plan.**

| Field | Value |
|-------|-------|
| Current recommended tier | **T{?}** ← update after each wave |
| Based on waves | {N} waves of history |
| Last updated | {YYYY-MM-DD} |
| Valid until | {next Debrief date} |

---

## How to read the score

Add up your Penalty Points from the table below.

| Score | Recommended Tier |
|-------|-----------------|
| 0 | T1 Proceed |
| 1–2 | T2 Propose |
| 3+ | T3 Suggest |

---

## Wave history

Update one row per wave at Debrief time.

| Wave | CI Pass Rate | Surprise Count | Security Findings | Debrief Score | Penalty Points | Tier Earned |
|------|-------------|---------------|-------------------|--------------|----------------|-------------|
| 1 | ___% | ___ | ___ | ___ | ___ | T? |
| 2 | ___% | ___ | ___ | ___ | ___ | T? |
| 3 | ___% | ___ | ___ | ___ | ___ | T? |

---

## Penalty point guide

| Event | Penalty Points |
|-------|----------------|
| CI pass rate below 90% | +1 |
| CI pass rate below 75% | +2 (replaces above) |
| Surprise count 3 or more in Debrief | +1 |
| Any security finding (non-critical) | +1 |
| Any critical security finding | +3 |
| Production incident (rollback triggered) | +2 |
| Foundation-tier CR discovered post-ship | +2 |

## Bonus point guide (reduce penalties)

| Event | Bonus Points |
|-------|-------------|
| Zero surprises in Debrief | −1 |
| Client rated session "no concerns" | −1 |
| Zero DEFER comments left uncaptured | −1 |

---

## Tier override

PO can manually override the computed tier at Phase 2 Plan with a written reason.
Log overrides here:

| Wave | Computed Tier | Override to | Reason |
|------|--------------|-------------|--------|
| — | — | — | — |

---

## Interpretation notes

- T1 Proceed does not mean no review. It means the human reviews the AI's review report,
  not every line of code. The AI review is still thorough.
- T3 Suggest is not a punishment — it is correct calibration after a rough wave.
  It costs more time in Phase 4 but saves much more time in production.
- Tier resets to T2 at the start of any new project (no history yet).
