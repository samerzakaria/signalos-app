---
description: "QA-only run. Executes the browser scenario suite without gate ceremony. Use for fast feedback during Build or ad-hoc validation."
---

<!-- SignalOS v1.0 — W7 Sprint QA -->

# /signal-qa-only — QA run without gate ceremony

Owner: Any agent or developer. Non-gating operational lever.

> `/signal-qa-only` is the fast-feedback sibling of `/signal-qa`. It runs the SBrowser scenario suite and emits an evidence snapshot **without** touching QUALITY_CHECK.md, gate state, or the audit trail. Use it during Build for rapid iteration, before opening a PR, or to spot-check a fix in a feature branch. It does not advance or block any gate.

## When to use

| Situation | Use |
|-----------|-----|
| Build agent wants a browser sanity check before opening PR | `/signal-qa-only` |
| Developer fixed a flaky scenario and wants to verify locally | `/signal-qa-only` |
| PE wants a quick vitals read on a branch without triggering Gate 5 ceremony | `/signal-qa-only` |
| Full QA run at end of Review phase, Gate 5 entry | `/signal-qa` |

## Your first action
No gate checks. No Soul Document read required.
If no scenario files exist at `core/governance/QA/scenarios/`, print:
```
No QA scenarios found at core/governance/QA/scenarios/.
Create at least one *.yaml scenario file to run signal-qa-only.
See core/execution/commands/signal-qa.md for scenario schema.
```
Then exit.

## Run options

```
signalos qa-only [--scenarios <glob>] [--headed] [--vitals] [--out <path>]
```

| Flag | Default | Effect |
|------|---------|--------|
| `--scenarios` | `core/governance/QA/scenarios/*.yaml` | Glob pattern to filter scenarios |
| `--headed` | off | Launch browser with visible window (overrides `SIGNALOS_BROWSER_HEADED`) |
| `--vitals` | off | Capture Web Vitals after each scenario's final step |
| `--out` | `core/governance/QA/evidence/qa-only-{timestamp}.json` | Evidence output path |

## Execution sequence

1. **Load scenarios** — discover all matching `*.yaml` files.
2. **Run via SBrowser** — same engine as `/signal-qa`:
   - navigate → steps → assertions → screenshot (always) → vitals (if `--vitals`)
3. **Print live summary** to stdout after each scenario:
   ```
   [PASS]  qa-001  Login happy path         (1243 ms)
   [FAIL]  qa-002  Dashboard load           (timeout @ wait_for "#dashboard")
   [PASS]  qa-003  Checkout flow            (3891 ms)
   ```
4. **Emit evidence snapshot** to `--out` path. Same JSON shape as `/signal-qa` evidence pack, with `"gating": false` to distinguish from Gate 5 evidence.
5. **Print final summary**:
   ```
   signal-qa-only complete — 3 scenarios · 2 pass · 1 fail
   Evidence: core/governance/QA/evidence/qa-only-20260430T2245Z.json
   Screenshots: core/governance/QA/evidence/screenshots/
   (non-gating run — QUALITY_CHECK.md not updated)
   ```

## Regressions

`/signal-qa-only` does **not** auto-run the regression suite. To include regressions:
```
signalos qa-only --scenarios "core/governance/QA/scenarios/*.yaml,core/governance/QA/regressions/*.yaml"
```

## What /signal-qa-only does NOT do

- Does **not** write or update `core/governance/QUALITY_CHECK.md`
- Does **not** check Gate 4 (Trust Tier Declared)
- Does **not** generate findings documents
- Does **not** trigger `wiring-guard --check C11`
- Does **not** advance or block any gate in the CONSTITUTION

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All scenarios passed (or no failures — skips are acceptable) |
| 1 | One or more scenario failures |
| 2 | No scenarios found / configuration error |

## Relationship to /signal-qa

`/signal-qa-only` produces a compatible evidence snapshot (`"gating": false`) that can be attached to a PR as supplemental context. When the full `/signal-qa` runs at Gate 5 entry, it executes a fresh, authoritative run regardless of any prior `qa-only` snapshots. A `qa-only` result never substitutes for a Gate 5 `/signal-qa` run.
