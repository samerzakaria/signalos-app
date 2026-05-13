---
description: "Phase 4 QA. Runs browser-based scenario suite via SBrowser, emits QUALITY_CHECK.md evidence pack."
---

<!-- SignalOS v1.0 — W7 Sprint QA -->

# /signal-qa — Phase 4: Quality Assurance

Owner: QA agent. Execution phase. Sits between Gate 4 (Trust Tier Declared) and Gate 5 (Quality Check).

## Your first action
Read `core/governance/Governance/SOUL-DOCUMENT.md` and `core/governance/QA_ACTIVATION_CARD.md`.
Confirm Gate 4 is passed: `core/execution/TRUST_TIER.md` must exist, be PE-signed, and carry a PO counter-signature.
If Gate 4 is not signed → **hard stop**. Print:
```
PROTOCOL VIOLATION: Gate 4 (Trust Tier Declared) not signed.
/signal-qa cannot run. PE must sign core/execution/TRUST_TIER.md; PO must counter-sign.
```

## Gate 4 gate-check (automated, before any scenario runs)

```
signalos wiring-guard --check C11
```

C11 verifies:
- `core/execution/TRUST_TIER.md` exists and is signed
- `core/governance/QA_ACTIVATION_CARD.md` exists for this Wave
- At least one scenario file exists at `core/governance/QA/scenarios/`

Any C11 failure → block QA run. Surface the failing check to the operator.

## Context budget check
- **0–60%** → run scenarios freely
- **60–80%** → pause between scenario groups, compress context
- **80%+** → hard stop. Dump partial evidence to `core/governance/QA/evidence/partial-{timestamp}.json`. Resume in fresh session with Soul Document loaded.

## QA run sequence

### Step 1 — Discover scenarios
Load all `*.yaml` files from `core/governance/QA/scenarios/`.
Each scenario file declares:
```yaml
id: qa-{N}
name: "{Human-readable name}"
url: "https://..."          # target URL (required)
steps:                      # ordered list of browser actions
  - action: navigate        # navigate | click | fill | wait_for | screenshot | evaluate
    args: ...
assertions:                 # post-step checks
  - type: console_errors    # console_errors | element_visible | url_contains | js_value
    args: ...
evidence:
  screenshot: true          # capture screenshot after final step
  vitals: true              # capture Web Vitals via measure_vitals()
```

### Step 2 — Execute via SBrowser
Import and use `signalos_lib.qa_runner.run_scenario_suite`.
Default: headless. Set `SIGNALOS_BROWSER_HEADED=1` to debug a failing scenario with a visible window.

For each scenario:
1. `SBrowser.navigate(url)`
2. Execute steps in order (click / fill / wait_for / evaluate)
3. Run assertions
4. Capture screenshot to `core/governance/QA/evidence/screenshots/{scenario_id}-{timestamp}.png`
5. If `vitals: true` → call `SBrowser.measure_vitals()`, append to evidence
6. Record PASS / FAIL + wall-clock duration

### Step 3 — Regression suite
After the main scenario suite, check for pending regressions:
```
signalos qa regression --run
```
This runs any auto-generated regression scenarios from `core/governance/QA/regressions/`.
Regressions are generated automatically when a bug fix PR is merged (see W7.5).

### Step 4 — Emit evidence pack
Write `core/governance/QA/evidence/wave-{N}-qa-evidence.json`:
```json
{
  "wave": "{N}",
  "run_at": "ISO-8601",
  "browser_engine": "SBrowser/playwright-{version}",
  "scenario_count": N,
  "regression_count": N,
  "pass": N,
  "fail": N,
  "skip": N,
  "scenarios": [ { "id": "...", "name": "...", "status": "pass|fail|skip", "duration_ms": N, "screenshot": "path", "vitals": {...}, "error": null } ],
  "qa_evidence_path": "core/governance/QA/evidence/wave-{N}-qa-evidence.json"
}
```

### Step 5 — Fill QUALITY_CHECK.md
Write `core/governance/QUALITY_CHECK.md` from `core/governance/Templates/quality-check-template.md`.
Populate all machine-readable fields from the evidence pack.
Leave QA signature line blank — **QA signs manually after reviewing the filled document**.

## Stage-2 manual review (after automated run)

QA reviews the filled QUALITY_CHECK.md and the screenshot archive at `core/governance/QA/evidence/screenshots/`.
For any FAIL:
- Write a finding in `core/governance/QA/findings/wave-{N}-finding-{id}.md`
- Tag severity: CRITICAL (blocks Gate 5) / MAJOR (must fix before ship) / MINOR (backlog)
- CRITICAL findings → route back to Phase 3 Build immediately

For all PASS + no CRITICAL findings:
- QA signs `core/governance/QUALITY_CHECK.md`
- Gate 5 is now unlockable by PE merge

## Exit criteria

- [ ] Gate 4 gate-check (C11) passed
- [ ] All scenarios executed (no partial runs)
- [ ] Evidence pack written to `core/governance/QA/evidence/wave-{N}-qa-evidence.json`
- [ ] Regression suite run
- [ ] `core/governance/QUALITY_CHECK.md` populated
- [ ] No unreviewed CRITICAL findings
- [ ] QA signature applied to QUALITY_CHECK.md

## Gate 5: Quality Check
QUALITY_CHECK.md signed by QA unlocks Gate 5. PE merges. DevOps deploys.
Any unsigned QUALITY_CHECK.md → merge blocked by `pre-merge` hook.

## Next phase
Run `/signal-ship` once Gate 5 is signed and PE approves merge.
