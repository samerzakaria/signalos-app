<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Acceptance Criteria — Wave {N}

Written in: Phase 2 Plan (authored by PE, reviewed by PO). Verified in: Phase 3 Build (Review agent + QA). Presented in: Phase 6 Wave Review + Debrief.

Instructions:
- Each criterion must be independently testable
- Verification method must be deterministic (not "looks good")
- **Signal to watch** is the post-ship metric the criterion implies — the bridge between a passing test and a passing Belief (Requirement DNA benefit, per SIGNAL_CONCEPTS §7)
- At verification: fill Status + Evidence columns. Do not alter the criterion text.

---

## Wave metadata

| Field | Value |
|-------|-------|
| Wave | {N} |
| Belief | {Problem + Bet + Signal} |
| Scale Track | Quick / Wave / Campaign |
| PO sign-off | {name — date} |
| QA sign-off | {name — date} |

---

## Acceptance criteria

### Happy path

| # | Criterion | Verification method | Signal to watch (post-ship) | Status | Evidence |
|---|-----------|-------------------|---|--------|----------|
| AC-01 | {e.g. User can register with email and password} | {e.g. POST /auth/register returns 201 with user_id in body} | {event: register_success; ≥ {N}/day for 7 days} | ⬜ Pending | — |
| AC-02 | {next criterion} | {verification} | {event + threshold} | ⬜ Pending | — |
| AC-03 | {next criterion} | {verification} | {event + threshold} | ⬜ Pending | — |

### Edge cases

| # | Criterion | Verification method | Signal to watch (post-ship) | Status | Evidence |
|---|-----------|-------------------|---|--------|----------|
| AC-04 | {e.g. Invalid email format returns 400 not 500} | {POST /auth/register with malformed email returns 400} | {event: register_400_rate; ≤ 2% of attempts} | ⬜ Pending | — |
| AC-05 | {next edge case} | {verification} | {event + threshold} | ⬜ Pending | — |

### Security

| # | Criterion | Verification method | Signal to watch (post-ship) | Status | Evidence |
|---|-----------|-------------------|---|--------|----------|
| AC-06 | {e.g. Password is hashed, not stored in plaintext} | {SELECT password FROM users — value starts with $2b$} | {audit sampling: 0 plaintext hits/month} | ⬜ Pending | — |

### Performance (if applicable)

| # | Criterion | Verification method | Signal to watch (post-ship) | Status | Evidence |
|---|-----------|-------------------|---|--------|----------|
| AC-07 | {e.g. Registration endpoint responds under 200ms at p95} | {Load test: 50 concurrent requests, check p95 latency} | {metric: register_p95_ms; ≤ 200 steady state} | ⬜ Pending | — |

---

## Status legend

| Symbol | Meaning |
|--------|---------|
| ⬜ Pending | Not yet verified |
| ✅ Pass | Verified with evidence |
| ❌ Fail | Verification failed — blocks merge |
| ⏭ Deferred | Moved to next wave with PO approval |

---

## Phase 4 sign-off

| Field | Value |
|-------|-------|
| Criteria total | {N} |
| Pass | {N} |
| Fail | {N} — list IDs |
| Deferred | {N} — list IDs |
| QC sign-off | {name — date} |
| Merge decision | ✅ Approved / ❌ Blocked |

---

## Notes

{Any context the client needs to understand these criteria — plain language, no jargon}
