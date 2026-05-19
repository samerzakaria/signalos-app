<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Signal Logs

`Folder: Governance/signal-logs/ · Authored by: QA + Analytics · Cadence: one file per Wave's Signal Window · Template: core/governance/Templates/signal-log-template.md`

> One file per Wave, named `wave-{N}-signal-log.md`. Captures the Signal Window — hourly readings of the Wave's metric, activation checks, operational SLOs, and the final Keep / Kill / Iterate verdict signed by PO + QA.

---

## Why one file per Wave (not append-only like Decision DNA)

The Signal Window is **Wave-scoped** and **time-bounded** (≤ 48 h for Quick track, ≤ 14 d for Wave track, ≤ 60 d for Campaign track). Each Wave's log is a closed artifact — open at Gate 5 entry, closed at Keep/Kill/Iterate verdict. Older Waves' logs are frozen and serve as the historical evidence base for the Belief Map and the Wave Debrief.

---

## File naming

```
wave-01-signal-log.md
wave-02-signal-log.md
wave-03-signal-log.md
…
```

Zero-padded to 2 digits until Wave 100 (then 3 digits). A CI validator flags deviations.

---

## When the log is read

- **During the Window:** QA reviews hourly readings; if metric has crossed threshold early, QA may propose early closure.
- **At Gate 5:** PO + QA read the log end-to-end, sign the Keep/Kill/Iterate verdict, and file the Wave Debrief.
- **Quarterly (daemon mode):** The Product Belief retro reads the last N Signal Logs aggregated to decide whether the Product Belief still holds.
- **Post-incident:** If a Wave shipped and broke production, the Signal Log is the first read — it tells the incident timeline better than the git log does.

---

## Do not delete or edit closed logs

A signed Signal Log is part of the audit trail. Errors discovered after close are addressed by adding an "Addendum" section at the bottom of the log, signed and dated, not by editing prior entries.
