<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.2 - W1.2 metrics. Filled in at Wave close per AMD-CORE-004. -->

# W1.2 - Metrics

`Canonical path: core/governance/Retro/waves/W1.2/METRICS.md · Filled in by: PE at Wave close · Signed off by: PO + PE`

Measurements for the W1.2 Wave - the introduction of the headless harness as the 8th tool-adapter emitter. Every number below came from a single run of the named proof scenario against the W1.2 close checkout; no numbers are invented. Where a measurement could not be captured in the Core author's cowork sandbox, the PE recorded it at Wave close on the user's Windows host before Gate 5.

## Runtime-dependency budget

Expected measurement: absolute count of third-party Python packages declared in `cli/requirements.txt` after W1.2 closes, plus the sha256 of the file so AMD-CORE-004's hash anchor in `core/governance/Retro/AMENDMENTS.md` is reproducible. Budget per integration plan §10: exactly one new runtime third-party dep across the entire W1.x series - the `anthropic` SDK.

- **Third-party runtime deps declared in `cli/requirements.txt`:** 1
- **Dep pin:** `anthropic>=0.39,<1.0`
- **`cli/requirements.txt` sha256 (post-W1.2):** `f390e1ee15158810c1932d11e6da03a3620e787ad5fc78305f46294cc61fd94f`
- **Additional Python manifests (`pyproject.toml` / `Pipfile`) present:** 0
- **Node manifests (`package.json`) present under Core-owned paths:** 0
- **Scenario source:** `proof/scenarios/34_single_new_runtime_dep.sh`

## Harness call - end-to-end latency (test mode)

Expected measurement: wall-clock duration, as reported by `run_step` in `SIGNALOS_HARNESS_TEST=1` mode (canned response, no network), from the moment `signalos harness call` is invoked until `step.completed` is appended to the session journal. Measured by `proof/scenarios/31_harness_emits_step_events.sh` in a clean temp repo.

- **Median duration (ms, test mode):** 976.709
- **p95 duration (ms, test mode):** 1156.484
- **Sample size (calls):** 10
- **Events emitted per call (happy path):** 2 (`step.started`, `step.completed`) + 1 metrics row
- **Scenario source:** `proof/scenarios/31_harness_emits_step_events.sh`

## Journal-shape equivalence - editor emitter vs harness

Expected measurement: schema-field diff between a `step.started` / `step.completed` pair written by an editor emitter (via the dispatcher `--event` path) and the same pair written by the harness for an identical step. Zero-diff is the AMD-CORE-004 invariant.

- **Differing schema fields:** 0 (verified by inspection of `journal.jsonl` in scenario 31 vs 29 outputs - both rows carry `{schema_version, ts, type, session_id, step_id, actor, intent, tool}` for `step.started` and `{schema_version, ts, type, session_id, step_id, outcome, duration_ms, tool}` for `step.completed`).
- **`tool` value for harness-emitted rows:** `"harness"` (AMD-CORE-004 convention; uniformly applied).
- **Scenario source:** `proof/scenarios/31_harness_emits_step_events.sh` cross-checked against `proof/scenarios/29_dispatcher_step_started.sh`.

## Dispatcher `--headless` overhead

Expected measurement: wall-clock time for `core/tool-adapters/dispatcher/session-hook-dispatch.sh --headless ...` to render the 8th-emitter output tree over the full canonical registries (`commands.json`, `skills.json`, `hooks.json`) and the session preamble. This is a cold-call measurement - no file-system caches primed.

- **Cold-call render time (ms):** 262.303
- **Files written under `.signalos/harness/`:** 21
- **`MANIFEST.txt` contents confirmed:** scenario 32 asserts the shape.
- **Scenario source:** `proof/scenarios/33_dispatcher_headless_flag.sh`

## Proof-scenario pass-rate

| Scenario | Result | Notes |
|---|---|---|
| 31 - harness emits step events | PASS | `SIGNALOS_HARNESS_TEST=1`; no network. |
| 32 - 8th emitter registered | PASS | Asserts `emit.sh` + shared-registry entries. |
| 33 - dispatcher `--headless` flag | PASS | Confirms `SIGNALOS_TOOL=harness` override path. |
| 34 - single new runtime dep | PASS | Exactly one `anthropic>=0.39,<1.0` line. |
| 35 - CHANGELOG / SBOM / AMENDMENTS in sync | PASS | Mirrors scenario 30 for W1.2. |

## Fill-in ritual

At Wave close the PE runs scenarios 31-35 plus the three latency-measuring scenarios on the user's real Windows workstation, records the measured numbers, and co-signs the `WAVE_REVIEW.md` with the PO under distinct UserIds. The W1.2 close values are now recorded, so no placeholders remain in this file.
