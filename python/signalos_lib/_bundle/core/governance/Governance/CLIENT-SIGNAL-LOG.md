<!-- SignalOS v1.0 — Locked 2026-04-16 -->
<!-- SEED FILE: copy to your product repo at Governance/CLIENT-SIGNAL-LOG.md. Append-only verbatim record of client input. Template: core/governance/Templates/client-signal-log-template.md -->

# Client Signal Log — {Product Name}

`Canonical path: Governance/CLIENT-SIGNAL-LOG.md · Append-only · Authored by: PO (primary) + any agent that receives client input · Format: one SIG block per signal, newest at top`

> Every piece of client input — praise, complaint, feature request, objection, redline, cancellation threat — captured **verbatim** with source, channel, sentiment, and whether it triggered a Change Request or an Expectation Map redline. Append-only. The log is the receipt that the client was listened to; the log is also the signal that a Belief is decaying.

---

## How to add a new entry

1. Copy the template block (`core/governance/Templates/client-signal-log-template.md`).
2. Assign the next SIG number in sequence.
3. **Quote verbatim** — no paraphrase, no "they basically said". If the client wrote it, copy-paste. If they said it, transcribe in their words. If translation is needed, keep both language versions.
4. Prepend (newest at top).
5. If this SIG triggered a CR, link it: `triggered_cr: CR-{NNNN}`. If it triggered an Expectation-Map redline in the next Wave, link it: `triggered_redline: Wave-{N}-row-{M}`.

---

## Why verbatim matters

- A paraphrased complaint becomes a PO's opinion; a verbatim quote is evidence.
- Sentiment analysis (quarterly, daemon mode) requires original wording — paraphrase destroys signal.
- A client who sees their own words reflected back in a CR is 10× more likely to sign.

---

## SIG-0001 — {short tag, e.g. "client wants export to CSV"}

```yaml
date: YYYY-MM-DD
channel: email | call | meeting | Slack | in-app | survey | other
participants: {who was on the call / in the thread}
sentiment: positive | neutral | frustrated | blocked | angry | cancellation-risk
wave_context: Wave {N} or pre-Wave / post-Ship
triggered_cr: none | CR-{NNNN}
triggered_redline: none | Wave-{N}-row-{M}
```

**Verbatim quote**

> *"…exact words of the client…"*

**Why this matters**

One-line PO annotation — *not* a paraphrase of the quote, but what this quote implies for the product (e.g. "Signals that the weekly-report Belief may be decaying — they're asking for export because the in-app view isn't enough.").

**Disposition**

- [ ] Acknowledged to client (how + when)
- [ ] Logged as CR: {link}
- [ ] Absorbed into next Wave's Expectation Map redline
- [ ] Deferred — reason: {…}
- [ ] Declined — reason: {…}

---

## SIG-0002 — {title}

*(Populate as client input arrives. Newest at top.)*

---

## Aggregate view (updated quarterly in daemon mode)

| Sentiment | Count last quarter | Delta vs prior quarter |
|---|---|---|
| positive | | |
| neutral | | |
| frustrated | | |
| blocked | | |
| cancellation-risk | | |

*(A material increase in frustrated/blocked/cancellation-risk is a Product Belief disproof signal — Constitution §12.5.)*
