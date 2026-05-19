<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Belief Map — {Product Name}

`Canonical path: core/strategy/BELIEF_MAP.md · Owner: PO · Updated: every Pre-Wave + every Wave Debrief · Doctrine: core/strategy/BELIEF_MAP.md (doctrine page)`

---

## Front-matter

```yaml
product: {Product Name}
delivery_mode: fresh-wave | daemon
last_pre_wave_review: YYYY-MM-DD
last_debrief_review: YYYY-MM-DD
author: {PO name}
```

---

## Active & recent Beliefs

*Most recent at the top. Drop to archive after the third fully-resolved Wave to keep the map readable.*

| Belief ID | Wave | State | Headline | Signal Window | Opened | Resolves by | Evidence link | Notes |
|---|---|---|---|---|---|---|---|---|
| BEL-20260416-1 | W1 | 📝 Drafting | {one-line paraphrase of the Belief} | {event + threshold + days} | {date} | {date} | — | {e.g. seeded from kick-off} |
| BEL-20260401-3 | W0 | ✅ Keep | {headline} | {window} | {date} | {date} | `governance/debriefs/wave-0-debrief.md` | {lesson} |
| BEL-20260315-2 | W-1 | ❌ Kill | {headline} | {window} | {date} | {date} | `governance/debriefs/wave--1-debrief.md` | {lesson} |

### State legend

| Symbol | Meaning |
|---|---|
| 📝 Drafting | Belief being written, not yet signed |
| 🎯 Active | Belief signed, Wave in Build / Review / Ship |
| 🔭 Observing | Shipped — Signal Window open, not yet closed |
| ✅ Keep | Signal Window met, Belief confirmed |
| ❌ Kill | Signal Window unmet, Belief refuted |
| 🔁 Iterate | Partial signal, new Belief spawned |
| 🚧 Blocked | External dependency or scope conflict, Wave paused |

---

## Portfolio posture (current snapshot)

| Dimension | Count |
|---|---|
| 🎯 Active | |
| 🔭 Observing | |
| ✅ Keep (last 90 days) | |
| ❌ Kill (last 90 days) | |
| 🔁 Iterate (last 90 days) | |
| 🚧 Blocked | |

*If Blocked > 1 for more than two Pre-Waves, escalate to PE for structural review.*

---

## Lessons compounded

*After each Wave Debrief, add one row here if the resolution revealed something that should alter future bets. Not every Wave yields a compounded lesson — that's fine. Leave empty rather than force a row.*

| Date | Wave | Keep/Kill/Iterate | What we now believe | Applied to which future Beliefs |
|---|---|---|---|---|
| YYYY-MM-DD | W0 | Keep | {e.g. "Daily users will adopt features that save < 2 clicks; they will not adopt features that save > 2 clicks but require new mental model"} | BEL-{future ID}, BEL-{future ID} |

---

## Conflicts & coherence check

*Any Belief in the Active or Observing states that appears to contradict another. PO checks at every Pre-Wave.*

| Belief A | Belief B | Tension | Resolution |
|---|---|---|---|
| {ID} | {ID} | {e.g. "A assumes users want more notifications; B assumes users want fewer"} | {e.g. "Segment difference — A is admin users, B is end users; not a conflict"} |

*If Resolution is empty after Pre-Wave, one of the two Beliefs needs a PO decision (amend, kill, or park) before Gate 1.*

---

## Zombies & stale states

*Run this check at every Pre-Wave. Anything here is a protocol-compliance item, not a strategic one.*

- [ ] No Belief in 🔭 Observing past its "Resolves by" date
- [ ] No Belief in 🎯 Active for > 2 Wave cycles (Wave timebox exceeded)
- [ ] Every 🚧 Blocked Belief has an escalation owner named in Notes

---

## Amendment history

| Date | What changed | Signer |
|---|---|---|
| YYYY-MM-DD | Initial map | PO |

---

*Last reviewed:* __________  *PO — Date:* __________
