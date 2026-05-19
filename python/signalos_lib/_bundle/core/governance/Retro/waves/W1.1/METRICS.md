<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.1 — W1.1 metrics template. Filled in at Wave close. -->

# W1.1 — Metrics

`Canonical path: core/governance/Retro/waves/W1.1/METRICS.md · Filled in by: PE at Wave close · Signed off by: PO + PE`

This file is a **template**. Numbers are not invented; every measurement field begins as `<to be filled at Wave close>` and is replaced with a real reading from the W1.1 journal only when the Wave-close ritual runs. Entries reference journal paths under `.signalos/sessions/<session-id>/journal.jsonl` and `.signalos/sessions/<session-id>/metrics.jsonl` per AMD-CORE-001 / AMD-CORE-003.

## Session journal performance

Expected measurement: median and p95 wall-clock time (milliseconds) of a single append to `.signalos/sessions/<session-id>/journal.jsonl` measured across the full W1.1 test corpus, as captured by `proof/scenarios/18_journal_append_perf.sh`. Budget per integration plan §5.1: median < 5 ms, p95 < 15 ms on SSD.

- **Median append latency (ms):** `152.909`
- **p95 append latency (ms):** `312.657`
- **Sample size (events):** `100`
- **Host class (SSD class, kernel version):** `Windows 11 + WSL2 on SSD, kernel 6.6.87.2-microsoft-standard-WSL2`
- **Scenario source:** `proof/scenarios/18_journal_append_perf.sh`

## Step-pause adoption

Expected measurement: count of PLAN step-specs across the W1.1 test corpus that declare `pause: true`, number of `step.paused` / `step.resumed` / `step.aborted` events emitted, and median pause-to-resume wall-clock time. Sourced from the aggregated journal across every W1.1 session. Feeds the AMD-CORE-002 ratification evidence and the first-Wave baseline used to judge over-pausing in W1.2.

- **Steps with `pause: true` declared:** `2`
- **`step.paused` events emitted:** `1`
- **`step.resumed` events emitted:** `1`
- **`step.aborted` events emitted:** `1`
- **Median pause-to-resume latency (s):** `0.0`
- **Median pause-to-abort latency (s):** `0.352`

## Observability dashboard render time

Expected measurement: wall-clock time (milliseconds) for `core/observability/render-dashboard.py` to produce the static HTML + inline SVG dashboard from a five-Wave `metrics.jsonl` fixture, measured by `proof/scenarios/26_dashboard_renders.sh`. Plus size of the emitted HTML in KiB so the "no external chart library" constraint stays visible.

- **Render wall-clock (ms):** `241.376`
- **Emitted HTML size (KiB):** `5.176`
- **Input session count:** `2`
- **Input event count:** `10`
- **Scenario source:** `proof/scenarios/26_dashboard_renders.sh`

## Fill-in ritual

At Wave close the PE runs the three scenarios above against a clean checkout, pastes the measured numbers into the fields marked `<to be filled at Wave close>`, and the PO + PE co-sign the Wave Review. A single placeholder remaining in this file blocks the Wave from crossing Gate 5.
