<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# QA Activation Card — Wave {N}

`Canonical path per Wave: core/governance/QA_ACTIVATION_CARD.md (current) or core/governance/qa-activation-cards/wave-{N}-card.md (archived) · Authored by: QA · Signed at: Gate 4 entry (before Build opens PRs)`

> The QA Activation Card declares — **before Build opens PRs** — what "done" looks like for this Wave's testing. Which test packs run, what coverage floor applies, which Stage-2 manual reviews are required, which are waived and why. QA signs. The Card is the anti-ambiguity device that stops Stage-2 arguments at Gate 5.

---

## Front-matter

```yaml
wave: {N}
scale_track: quick | wave | campaign
trust_tier_ceiling: T1 | T2 | T3
author: {QA name}
date: YYYY-MM-DD
```

---

## Test packs declared for this Wave

| Pack | Scope | Runs when | Pass bar | Owner |
|---|---|---|---|---|
| Unit | Touched modules | Every PR | 100% of new tests green; existing suite green | Build agent + QA |
| Integration | Touched API routes + DB | Every PR + nightly | Green; no flaky retry > 2× | QA |
| E2E | Critical happy paths | Pre-merge + pre-deploy | Green | QA |
| Smoke (Deploy Health Gate) | Post-deploy first 60 min | Ship phase | 0 errors above baseline; p95 latency within SLO | DevOps + Release agent |
| Adversarial (Test agent) | Every Build PR | Every push | Agent-generated cases green OR flagged to QA | Test agent |
| Security (Security agent) | Every Build PR | Every push | No critical vulns; any permanently-T3 surface exposure = HARD BLOCK | Security agent |

*Packs not declared here are considered out-of-scope for this Wave. Add rows for product-specific packs (visual regression, accessibility, perf, load).*

---

## Coverage floor for this Wave

| Metric | Target | Current baseline | How measured |
|---|---|---|---|
| Line coverage on touched modules | {≥ 80%} | {report} | {tool name} |
| Branch coverage on touched modules | {≥ 70%} | | |
| Critical-path E2E pass rate | 100% | | |

A PR that lowers any of the above vs. baseline is flagged for PO + PE review before merge.

---

## Stage-2 manual review plan

*Stage-1 is automated (Review agent). Stage-2 is human. This section declares what a human reviewer will look at.*

| Area | Reviewer | Method | Time-box |
|---|---|---|---|
| {e.g. customer-visible copy} | PO | Read-through in staging | 15 min |
| {e.g. new migration dry-run} | PE + DevOps | Staging apply + rollback verification | 30 min |
| {e.g. accessibility of new UI} | QA | axe-core + keyboard-only walk-through | 20 min |

---

## Waivers (Stage-2 items skipped for this Wave)

*Required only if a Stage-2 item typical for this product is skipped. Each row is a QA-signed waiver with a reason.*

| Area | Why skipped | Risk acknowledged | QA initial |
|---|---|---|---|
| | | | |

*A waiver without a PO counter-initial on the risk row is a protocol violation.*

---

## Test data & environments

| Need | Source | Refreshed | Sensitive? |
|---|---|---|---|
| Staging DB snapshot | | | |
| Seed accounts | | | |
| Mock payment provider | | | |

---

## Gate 5 entry criteria (what QA will check to close Gate 5)

- [ ] All declared packs above green
- [ ] Coverage floor met on touched modules
- [ ] Stage-2 items reviewed OR waived with signed reason
- [ ] Signal Window threshold (from BELIEF.md) instrumented and flowing
- [ ] No unresolved RE-TIER-REQUIRED or HARD BLOCK from Security

---

## Signature

**I confirm this Wave's test plan is adequate for its Belief + Trust Tier + Scale Track. Any later shortfall is my accountability.**

Signed (QA): __________  *Date: __________*

---

## Amendment history

| Date | What changed mid-Wave | Signer |
|---|---|---|
| YYYY-MM-DD | Initial card | QA |
