<!-- SignalOS Core v1.2 - W1.2 docs delta. Append-only during the Wave. -->

# W1.2 - Docs delta

`Canonical path: core/governance/Retro/waves/W1.2/docs-delta.md · Authored by: PE · Signed by: PO + PE at Wave close`

Everything documentation-shaped that the headless harness + 8th emitter change across the SignalOS Core distro.

## Files touched by W1.2

| Path | Change | State | Why |
|---|---|---|---|
| `core/README.md` | **extend** - "Running without an editor" section. | done | AMD-CORE-004 - surface the harness path for CI use. |
| `core/governance/Governance/CONSTITUTION.md` | **append** - section 13 Glossary with `headless harness`, `8th emitter`, `harness:call`. | done | AMD-CORE-004 vocabulary. (Landed at `core/governance/Governance/CONSTITUTION.md` - Core root has no `CONSTITUTION.md`; W1.1 hash anchor on `core/TRUST_TIER.md` already disclosed this path choice.) |
| `core/execution/skills/headless-execution/SKILL.md` | **new** - when/how to pick the harness over an editor, headless-safe step design. | done | AMD-CORE-004. |
| `core/execution/commands/harness-call.md` | **new** - command doc for `signalos harness call`. | done | AMD-CORE-004. |
| `core/tool-adapters/dispatcher/session-hook-dispatch.sh` | **extend** - `--headless` branch. | done | Exercised by proof scenario 33. |
| `core/tool-adapters/emitters/harness/emit.sh` | **new** - 8th emitter. | done | Exercised by proof scenario 32. |
| `core/tool-adapters/_shared/commands.json` | **extend** - register `harness-call`. | done (in W1.1 foundation pass) | AMD-CORE-004. |
| `core/tool-adapters/_shared/skills.json` | **extend** - register `headless-execution`. | done (in W1.1 foundation pass) | AMD-CORE-004. |
| `cli/requirements.txt` | **extend** - add `anthropic>=0.39,<1.0`. | done | AMD-CORE-004 - single new runtime dep in the W1.x series. Post-W1.2 sha256: `f390e1ee15158810c1932d11e6da03a3620e787ad5fc78305f46294cc61fd94f`. |
| `cli/signalos_lib/harness.py` | **new** - harness core (Python). | done | Exercised by proof scenario 31. |
| `cli/signalos_lib/commands/harness.py` | **new** - argparse CLI for `signalos harness ...`. | done | Wired into `cli/signalos` entry point. |
| `SBOM.md` | **append** - record the `anthropic` pin with source URL and SHA. | done | Gate 5. |
| `docs/CHANGELOG.md` | **append** - `## 1.2.0` entry. | done | Gate 5. |
| `core/governance/Retro/AMENDMENTS.md` | **append** - AMD-CORE-004 measured hash anchor. | done | AMD contract. |
| `core/execution/hooks/step-started/step-started.sh` | **fix** - export `SIGNALOS_SESSION_ID` / `SIGNALOS_STEP_ID` before sourcing `step-pause-check.sh`; gate the source on `SIGNALOS_PLAN_STEP_JSON` being set. | done | W1.1 integration bug surfaced by scenario 31 dry-run. Rolled into the W1.1 amendment-log repair disclosure. |
| `core/execution/hooks/_lib/redact.py` | **fix** - complete the truncated `sys.exit(main(sys.argv))` tail. | done | W1.1 hygiene repair; same disclosure. |
| `proof/scenarios/31_harness_emits_step_events.sh` | **new** - harness fires `step.started` + `step.completed` via the W1.1 hook path. | done | AMD-CORE-004 evidence. |
| `proof/scenarios/32_eighth_emitter_registered.sh` | **new** - `harness/emit.sh` honours the editor-emitter contract + registries reference the W1.2 docs. | done | AMD-CORE-004 evidence. |
| `proof/scenarios/33_dispatcher_headless_flag.sh` | **new** - dispatcher `--headless` forces `SIGNALOS_TOOL=harness` and routes to the 8th emitter. | done | AMD-CORE-004 evidence. |
| `proof/scenarios/34_single_new_runtime_dep.sh` | **new** - `cli/requirements.txt` has exactly one runtime dep (`anthropic>=0.39,<1.0`); no Node, no extra Python manifest. | done | W1.x budget invariant. |
| `proof/scenarios/35_w1_2_changelog_sbom_in_sync.sh` | **new** - CHANGELOG 1.2, SBOM W1.2, AMENDMENTS AMD-CORE-004, and requirements.txt agree. | done | Gate 5 sync invariant. |

## Definition of done for W1.2 docs

- Every row above is `done`.
- `cli/requirements.txt` is exactly one line of non-comment content.
- `SBOM.md` diff is additive only (no existing rows changed).
- The harness skill contains a side-by-side table: "editor emitter vs harness - when to pick which".

## Authors

PE-drafted. PO + PE co-sign at W1.2 close.
