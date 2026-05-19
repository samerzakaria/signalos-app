<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.2 - W1.2 Wave Review. Filled in at Wave close. -->

# W1.2 - Wave Review

`Canonical path: core/governance/Retro/waves/W1.2/WAVE_REVIEW.md · Filled in by: PO + PE at Wave close · Distinct UserIds required on sign-off (product-Constitution §F.3)`

The W1.2 Wave introduced the **headless harness** as the 8th tool-adapter emitter, under `AMD-CORE-004`. This Wave Review is the narrative counterpart to `METRICS.md`: what shipped, what nearly didn't, which amendments became force-of-law, and which learnings carry into W1.3.

## What shipped

- The harness-mode Python library `cli/signalos_lib/harness.py` - `run_step`, `get_status`, `abort_call`, shelling into the same four `core/execution/hooks/<event>/<event>.sh` scripts the editor emitters fire. Proved by proof scenario 31.
- The `signalos harness ...` CLI surface (`call` / `status` / `abort`) via `cli/signalos_lib/commands/harness.py`, wired into the existing `cli/signalos` lazy-routing entry point. Exit-code contract documented in `core/execution/commands/harness-call.md`.
- The 8th tool-adapter emitter `core/tool-adapters/emitters/harness/emit.sh`, which takes the same `--commands-json` / `--skills-json` / `--hooks-json` / `--preamble` / `--output-dir` contract as the seven editor emitters and writes a self-contained `.signalos/harness/` tree. Proved by proof scenario 32.
- The dispatcher `--headless` flag on `core/tool-adapters/dispatcher/session-hook-dispatch.sh`, which forces the 8th-emitter path by exporting `SIGNALOS_TOOL=harness` - no change to `detect_tool` or `invoke_emitter`. Proved by proof scenario 33.
- The single new runtime third-party dep, `anthropic>=0.39,<1.0`, pinned in `cli/requirements.txt` and SBOM-tracked in `SBOM.md` section W1.2. No Node, no additional Python manifest. Proved by proof scenario 34.
- Core-scoped docs: the "Running without an editor" section in `core/README.md`, the section 13 glossary in `core/governance/Governance/CONSTITUTION.md` with `headless harness`, `8th emitter`, and `harness:call` entries, the new command doc at `core/execution/commands/harness-call.md`, and the new skill at `core/execution/skills/headless-execution/SKILL.md`.
- Wave-level hygiene: `docs/CHANGELOG.md` 1.2.0 entry, `SBOM.md` section W1.2, `core/governance/Retro/AMENDMENTS.md` AMD-CORE-004 row with measured hash anchor. Proved by proof scenario 35.

## What almost didn't

- **AMENDMENTS.md shipped truncated on `main` at v1.0.3.** The v1.0.3 baseline of `core/governance/Retro/AMENDMENTS.md` was cut off mid-sentence inside AMD-CORE-002 (at "...high-risk steps witho") and AMD-CORE-003 was missing entirely; proof scenario 30 did not catch this because it only grepped `docs/CHANGELOG.md` and `SBOM.md`, not `AMENDMENTS.md` itself. Surfaced while preparing the AMD-CORE-004 row, repaired in the W1.2 close commit, disclosed under the "W1.1 amendment-log repair" bullet of the `1.2.0` CHANGELOG entry. Scenario 35 now also asserts `AMD-CORE-004` is present in `AMENDMENTS.md` so the same class of drift cannot recur silently.
- **`core/execution/hooks/_lib/redact.py` was truncated at EOF.** The v1.0.3 file ended with `sys.exit(main` and nothing else - a syntax error that only surfaced when proof scenario 31 ran the real redaction pipeline through `metrics-append.sh`. Repaired in the same W1.2 close commit; self-test now passes (`redact.py --self-test: PASS`).
- **`core/execution/hooks/step-started/step-started.sh` did not set `SIGNALOS_*` env vars before sourcing `step-pause-check.sh`.** The pause library is env-var-driven (see its docstring lines 7-14), so any caller that fired step-started without pre-setting `SIGNALOS_SESSION_ID` + `SIGNALOS_STEP_ID` would crash with a missing-required-var error. Editor emitters apparently set these elsewhere; the harness' first W1.2 test run in scenario 31 did not. Fixed by exporting the two identifiers and gating the source on `SIGNALOS_PLAN_STEP_JSON` being present (no pause-spec -> no pause check, which matches the section 4 default).
- **Harness `_fire_hook` was sending `--actor` uniformly.** `step-completed.sh` / `step-failed.sh` reject unknown args, so the harness initially crashed them. Fixed by only passing `--actor` on `step-started` and wiring the correct per-event argument set (`--outcome` + `--duration-ms` on completed; `--reason` + `--exit-code` on failed). Now scenario 31 emits both happy-path events.

## Amendments ratified

| AMD | Title | Hash anchor (measured) | PO | PE | Ratification Gate | Date of force |
|---|---|---|---|---|---|---|
| AMD-CORE-004 | Headless harness as 8th tool-adapter emitter | `cli/requirements.txt` sha256 `f390e1ee15158810c1932d11e6da03a3620e787ad5fc78305f46294cc61fd94f` | Samer Zakaria | Mohammed Shaban | W1.2 Gate 1 | 2026-04-23 |

See `core/governance/Retro/AMENDMENTS.md` for the canonical row; the measured hash in that file matches the anchor above.

## Learnings that flow into W1.3

- **Hook scripts need an arg-contract skill/spec, not just per-script docstrings.** The `--actor`-only-on-`step-started` gotcha was avoidable. W1.3 should land a short spec under `core/execution/hooks/ARG_CONTRACT.md` enumerating the required/optional args per event so any new emitter (context-compression in AMD-CORE-005, registry-related in AMD-CORE-006) can be validated against it. -> W1.3.
- **Proof-scenario drift check must include `AMENDMENTS.md`.** Scenario 30 missed the v1.0.3 truncation. Scenario 35 now also greps AMENDMENTS.md for AMD-CORE-004, but a generic "every ratified AMD-CORE-00n must appear in AMENDMENTS.md" assertion should land in W1.3. -> W1.3.
- **`SIGNALOS_HARNESS_TEST=1` is load-bearing for CI.** The canned-response short-circuit let us exercise the full event-emission path without an API key; W1.3 should establish the same pattern for `signalos context expand` (stubbed compression endpoint) and `signalos install` (file-backed registry, no network). -> W1.3.
- **The single-new-runtime-dep budget is now a codified invariant.** Scenario 34 turns the section 10 rule into an automated assertion; AMD-CORE-006 (plugin registry) must land without a new Python dep - `cosign` is a pinned external binary, not a Python import. -> W1.3.
- **W1.1 hygiene debt surfaced late.** The AMENDMENTS + redact.py + step-started fixes were all W1.1 regressions that only showed up when W1.2 leaned on their output paths. A Wave-close "regression drill" - re-run every prior Wave's scenarios from the current tip - should be a gate-condition for W1.3 close. -> W1.3.

## Sign-off (PO + PE distinct UserId)

| Role | Name | UserId | Date | Signature (SHA-256 of this file at sign-off) |
|---|---|---|---|---|
| PO | Samer Zakaria | Samer Zakaria | 2026-04-23 | `2cd226d48e082c874822c4b355faffc7f0517dc4f518ae69f6653745c77945c4` |
| PE | Mohammed Shaban | Mohammed Shaban | 2026-04-23 | `2cd226d48e082c874822c4b355faffc7f0517dc4f518ae69f6653745c77945c4` |

## Fill-in ritual

At Wave close the PE re-runs scenarios 31-35 (plus the broader 18-30 regression set) on the user's real Windows workstation, records the close values in `METRICS.md` and this file, and both PO and PE co-sign under distinct UserIds. W1.2 is now fully recorded and signed.
