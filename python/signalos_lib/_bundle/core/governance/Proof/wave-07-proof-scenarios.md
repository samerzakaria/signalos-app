<!-- SignalOS v1.0 — W7 Sprint QA -->

# Wave 07 Proof Scenarios

Wave: 07 — Sprint QA
Belief: Delivering a native browser-based QA engine lets SignalOS verify its own product at Gate 5 without any external QA tooling dependency.

---

## Overview

Four proof scenarios cover the W7 deliverables end-to-end. Scenarios 100–103 are executable as bash scripts in `proof/scenarios/`. They are also integrated into the CI workflow at `.github/workflows/core-proof.yml`.

| # | Scenario | What it proves | Playwright required |
|---|----------|----------------|---------------------|
| 100 | `sbrowser_import` | browser.py is importable; SBrowser, BrowserError, VitalsResult, ConsoleMessage are exported correctly | Soft — SKIPs gracefully if missing |
| 101 | `qa_runner_load_scenarios` | qa_runner.py loads YAML scenario files, validates required fields, rejects malformed input | No (stdlib + PyYAML) |
| 102 | `regression_generate` | regression.py generates a valid YAML regression scenario from a BugDescription; file is readable; fields are correct | No (stdlib) |
| 103 | `wiring_guard_c11_c12` | wiring-guard --check C11 passes (all W7 components present); --check C12 passes (quality-check template exists) | No (bash) |

---

## Scenario 100 — SBrowser import + API surface

**File:** `proof/scenarios/100_sbrowser_import.sh`

**Proves:**
- `cli/signalos_lib/browser.py` exists and is syntactically valid Python
- `__all__` exports: `SBrowser`, `BrowserError`, `VitalsResult`, `ConsoleMessage`
- `SBrowser` has all required methods: `open`, `close`, `navigate`, `screenshot`, `click`, `fill`, `wait_for`, `get_console_errors`, `measure_vitals`, `current_url`, `get_text`, `evaluate`
- If Playwright is installed: SBrowser context manager opens and closes without error against `about:blank`
- If Playwright is not installed: `ImportError` is raised with the correct install hint

**Pass condition:** All attribute checks pass. If Playwright absent → `SKIP` (not `FAIL`).

---

## Scenario 101 — QA runner loads scenarios

**File:** `proof/scenarios/101_qa_runner_load_scenarios.sh`

**Proves:**
- `cli/signalos_lib/qa_runner.py` exists and is importable
- `load_scenarios()` loads a well-formed YAML scenario → returns list with correct id/name/url
- `load_scenarios()` raises `ValueError` for a scenario missing `id`
- `load_scenarios()` raises `ValueError` for a scenario missing `url`
- `EvidencePack.as_dict()` round-trips through JSON without loss

**Pass condition:** All assertions pass within 10 seconds.

---

## Scenario 102 — Regression auto-generation

**File:** `proof/scenarios/102_regression_generate.sh`

**Proves:**
- `cli/signalos_lib/regression.py` exists and is importable
- `generate_regression()` creates a file in the target directory
- Generated file is valid YAML (parseable)
- Generated file contains: `id` starting with `reg-`, `name` containing `[regression]`, `bug_ref` matching input `bug_id`, `auto_generated: true`, `url`, at least one assertion
- Sequential ID increments: second call creates `reg-002` when `reg-001` already exists

**Pass condition:** All assertions pass. Temp directory cleaned up after run.

---

## Scenario 103 — Wiring guard C11 + C12

**File:** `proof/scenarios/103_wiring_guard_c11_c12.sh`

**Proves:**
- `core/governance/Validators/wiring-guard.sh --check C11` exits 0 (all W7 wiring components present)
- `core/governance/Validators/wiring-guard.sh --check C12 --warn` exits 0 (template exists; unsigned QUALITY_CHECK is a warning not a hard failure pre-wave)
- `--check` flag correctly filters to a single check (does not run all checks)

**Pass condition:** Both wiring-guard invocations exit 0.

---

## Running the proof suite

```bash
# Run W7 proof scenarios only:
for s in proof/scenarios/10{0,1,2,3}_*.sh; do bash "$s" && echo "PASS: $s"; done

# Run all proof scenarios via the capture harness:
bash proof/proof-capture.sh --wave-id 07

# Run a single scenario in verbose mode:
bash proof/scenarios/103_wiring_guard_c11_c12.sh
```

## CI integration

Scenarios 100–103 are registered in `.github/workflows/core-proof.yml` under the `wave-07-qa` job. The job runs after the main proof-capture step and gates the W7 merge check.

Playwright scenarios (100) run in a separate CI job with `playwright install chromium` in the setup step. Scenarios 101–103 run in the standard stdlib-only CI environment.

---

## Evidence captured

Each scenario emits to stdout:
```
scenario NNN_name: PASS
scenario NNN_name: SKIP (reason)
scenario NNN_name: FAIL — <detail>
```

The proof harness captures this into `proof/logs/` and the manifest at `proof/PROOF_REPORT_07.md`.
