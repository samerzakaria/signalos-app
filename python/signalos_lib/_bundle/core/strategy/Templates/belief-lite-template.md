<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Belief-Lite — Wave {N}

`Quick-track form of Belief. Path: core/strategy/BELIEF_LITE.md · Authored by: PO · Used only when scale_track: quick`

> A Belief-lite replaces the full Belief + Expectation Map for Quick-scale Waves (≤1 day of work, T2 ceiling, no permanently-T3 surfaces touched). It is **not** a relaxation of Gate 1 — it is a scaled form of the gate artifact, per Constitution §11.4.

---

## Front-matter

```yaml
wave: {N}
scale_track: quick
trust_tier_ceiling: T2
author: {PO name}
date: YYYY-MM-DD
```

---

## The four lines

**Problem:** One sentence. Who is affected, what is broken.

> {…}

**Bet:** One sentence. The change and the expected effect.

> {…}

**Signal:** One sentence. How we'll know it worked within 48 hours.

> {…}

**Acceptance:** One line. What "done" looks like.

> {…}

---

## Scope safety check

- [ ] No change touches a permanently-T3 surface (auth, payments, migrations, secrets, IaC, Constitution). *If any T3 surface is touched, STOP and re-declare as `scale_track: wave`.*
- [ ] T2 ceiling respected in PR — PE will type the diff if any surface re-classifies to T3.
- [ ] Signal window ≤ 48 hours from ship.

---

## Gate 1 (scaled form) + Gate 2 (scaled form)

**I confirm this Belief-lite is falsifiable within 48 hours and does not touch any permanently-T3 surface. Expectation Map is replaced by the single Acceptance line above.**

Signed: __________  *PO — Date: __________*

*(Quick track continues directly to `/signal-plan` or in-PR execution — no separate Expectation Map, Design Note, or Trust Tier file. A Stage-1 PASS + optional QA Stage-2 waiver with reason closes Gate 5.)*
