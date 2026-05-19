<!-- SignalOS v1.0 — Updated AMD-CORE-038 2026-05-01 -->

# Role Activation Card — Wave {N}

`Canonical path per Wave: core/execution/ROLE_ACTIVATION_CARD.md (current) or core/execution/role-activation-cards/wave-{N}-card.md (archived) · Authored by: Agent · Signed at: Gate 1 (PO)`

> The Role Activation Card is the **contract for the Wave**: which agent sub-roles are active, at what Trust Tier, and with what cardinality. The PO signs it once at Gate 1. Any mid-Wave tier change requires a fresh PO signature.

---

## Front-matter

```yaml
wave: {N}
scale_track: quick | wave | campaign
delivery_mode: fresh-wave | daemon
author: {PO name}
date: YYYY-MM-DD
model: one-man-show  # sole human seat is PO; all execution roles are agent sub-roles
```

---

## Activation levels

| Level | Symbol | Meaning |
|---|---|---|
| Active | ●●● | Primary this Wave |
| AI-heavy | ●● | Agent executes, PO reviews output |
| Minimal | ● | Background — PO pinged only if blocked |
| Deferred | ○ | Not needed this Wave |

---

## The card

*Fill every cell. A blank cell is a protocol violation — it means the trust tier and phase ownership are undeclared.*

| Seat | Init | Pre-Wave | Plan | Build | Review | Ship | Wave Review | Retro |
|---|---|---|---|---|---|---|---|---|
| **PO** (sole human — reads + signs) | | | | | | | | |
| Brainstorm agent | | | | | | | | |
| Architecture agent | | | | | | | | |
| Data agent | | | | | | | | |
| Security agent | | | | | | | | |
| Build ×N agents | | | | | | | | |
| Test agent | | | | | | | | |
| Review agent | | | | | | | | |
| Worktree-Sync agent | | | | | | | | |
| Release agent | | | | | | | | |
| Observability agent | | | | | | | | |

*(Add Onboarding agent row if this product is in its first Wave.)*

---

## Trust Tier declaration

*Declare the default Trust Tier for each active agent sub-role this Wave. T3 surfaces require explicit PO sign-off on the diff before commit.*

| Agent sub-role | Default tier | T3 surfaces this Wave (if any) |
|---|---|---|
| Architecture | T1 | — |
| Data | T1 | — |
| Security | T2 (advisory) | — |
| Build ×N | T2 | {list any T3 surfaces} |
| Test | T1 | — |
| Review | T2 | — |
| Release | T3 | all release-path surfaces |
| Observability | T1 | — |

---

## Escalation paths

| Scenario | Action |
|---|---|
| PO unavailable at any Gate | Wave pauses — no substitution possible |
| Agent detects T3 surface mid-Build declared as T2 | HARD STOP — agent reports; PO re-declares Trust Tier and re-signs |
| Primary metric dead during Signal Window | PO decides: extend window or mark Kill early |

---

## Gate 1 signature (as part of Belief signature)

**I confirm this card accurately reflects the agent sub-role activation for this Wave. All cells are filled. Trust Tiers are declared.**

Signed (PO): __________  *Date: __________*

---

## Change history

| Date | What changed mid-Wave | Signer |
|---|---|---|
| YYYY-MM-DD | Initial card at Gate 1 | PO |

*(Mid-Wave tier changes require a fresh PO signature row. An unsigned amendment is a protocol violation.)*
