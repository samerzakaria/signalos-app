<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Belief Map — portfolio view of active Waves

`Canonical path (product-level): core/strategy/BELIEF_MAP.md · Authored by: PO · Reviewed: Pre-Wave (every Wave) and Wave Debrief`

> A Belief is a single-Wave object. A product typically runs more than one Wave — sequentially, in parallel, or with daemon-mode products continuously running alongside discrete fresh-wave bets. The **Belief Map** is the portfolio view: every Belief the product is currently holding, their state, their Signal Window, and the shape of the picture they collectively paint. Without it, the product has a pile of Waves but no doctrine about what they mean together.

---

## 1 · Why this exists

**Problem without a Belief Map.** Waves close one at a time and the retro only sees each one in isolation. The PO cannot answer: *"Out of our last four Waves, which kind of bet is converting? Where are we compounding? Where are we failing and pretending we aren't?"* Beliefs resolve; lessons do not accumulate.

**What the map gives you.**

- A single board to see what the product is currently betting on
- A legible state per Belief — active · observing · resolved (Keep) · resolved (Kill) · resolved (Iterate) · blocked
- Continuity across Waves — last Wave's resolved Belief becomes this Wave's Soul Document input
- Visibility for stakeholders who need the picture but shouldn't need to read every Belief file

The Belief Map is to Beliefs what a portfolio review is to investments — not a replacement for the investment memo, but the only thing you can read in one sitting.

---

## 2 · Where it lives

| Scope | Canonical path | Updated |
|---|---|---|
| Product-level (daemon products) | `core/strategy/BELIEF_MAP.md` | Every Pre-Wave + every Wave Debrief |
| Wave-scoped snapshot (fresh-wave products) | `core/strategy/belief-maps/wave-{N}-map.md` | At Gate 5 close |

For single-Wave, one-shot products the map may be omitted. For any product running ≥ 2 Beliefs simultaneously or in sequence the map is mandatory.

---

## 3 · Belief states

| State | Meaning | Set when |
|---|---|---|
| 📝 Drafting | Belief being written, not yet signed | Pre-Wave |
| 🎯 Active | Belief signed, Wave in Build / Review / Ship | Gate 1 close |
| 🔭 Observing | Shipped — Signal Window open, not yet closed | Deploy |
| ✅ Resolved — Keep | Signal Window met, Belief confirmed | Wave Debrief |
| ❌ Resolved — Kill | Signal Window unmet, Belief refuted | Wave Debrief |
| 🔁 Resolved — Iterate | Partial signal, new Belief spawned | Wave Debrief |
| 🚧 Blocked | External dependency or scope conflict, Wave paused | PO decision, logged in Decision DNA |

Stale states are a protocol smell. A Belief in 🔭 Observing past its Signal Window end date means either the PO owes a Debrief or the map is out of date.

---

## 4 · What a Belief Map enables

- **Pre-Wave reality check.** Before signing a new Belief, the PO reads the map and asks: *Is this really the most important bet right now, or am I choosing it because it's easy?*
- **Portfolio awareness.** Two Beliefs that conflict (*users want less notification noise* and *users need a new notification stream*) become visible before both ship.
- **Compounding lessons.** Kills cluster. If three consecutive Waves kill Beliefs in the same product area, the product has a structural insight, not a string of local failures.
- **Stakeholder communication.** A board with six rows beats a folder with thirty files.

---

## 5 · Relationship to other artifacts

| Reads from | Writes to |
|---|---|
| `core/strategy/BELIEF.md` (per Wave) | `governance/DECISION-DNA.md` — any Belief state transition that embodies a strategic decision |
| `core/execution/PLAN.md` (for Wave state) | `governance/conversations/` — Wave Debrief transcripts that updated the map |
| `core/governance/Governance/CLIENT-SIGNAL-LOG.md` (for resolution evidence) | Next Wave's `BELIEF.md` if Iterate state spawns follow-on |

The map is a read-heavy artifact at Pre-Wave and a write-heavy artifact at Wave Debrief.

---

## 6 · How it is maintained

- **PO owns the map.** No agent writes to it.
- **Every Pre-Wave**, the PO opens the map, reviews stale rows, and decides whether the new Wave's Belief is coherent with what the map already says.
- **Every Wave Debrief**, the PO updates the resolving Belief's row — state, evidence link, lessons.
- **Amendments are cheap.** The map is not append-only. State transitions in place; the Belief file itself keeps the history.

---

## 7 · Anti-patterns

- **"Map-as-roadmap."** The Belief Map is a record of bets taken, not a marketing plan. Do not list aspirational future Beliefs.
- **"Map-as-list-of-features."** A row is a Belief (falsifiable claim), not a feature. If a row has no Signal Window, it doesn't belong.
- **"Zombie Observing."** A Belief stuck in 🔭 Observing with no Debrief scheduled. Either close it or log why it can't close.

---

## 8 · Use the template

Use `core/strategy/Templates/belief-map-template.md` to start a new map. Fill the portfolio table and the lessons-compounded section after every Wave Debrief.

---

*Reviewed by:* __________  *PO — Date:* __________
