<!-- SignalOS Core v2.1 - W2.1 docs delta. Append-only during the Wave. -->

# W2.1 - Docs delta

`Canonical path: core/governance/Retro/waves/W2.1/docs-delta.md · Authored by: PE · Signed by: PO + PE at Wave close`

Everything documentation-shaped that the LLM provider abstraction + parallel orchestrator + wiring guard bundle changes across the SignalOS Core distro.

## Files touched by W2.1

| Path | Change | State | Why |
|---|---|---|---|
| `cli/signalos_lib/harness.py` | **rewrite** — `LLMProvider` Protocol, `AnthropicProvider`, `OpenAIProvider`, `GeminiProvider`, `OllamaProvider`, `TestProvider`, `_resolve_provider()`, `run_step()` gains `provider` param. Removed `_call_anthropic`, `_probe_anthropic`, `_ensure_anthropic`, `HARNESS_AVAILABLE`. | done | AMD-CORE-007. |
| `cli/signalos_lib/commands/harness.py` | **extend** — `--provider` flag on `call` subcommand. | done | AMD-CORE-007. |
| `cli/signalos_lib/orchestrator.py` | **new** — `run_wave()` parallel orchestration engine. | done | AMD-CORE-008. |
| `cli/signalos_lib/status.py` | **new** — `get_wave_status()`, `render_status_card()`, `print_status_card()`. | done | AMD-CORE-008. |
| `cli/signalos_lib/commands/orchestrate.py` | **new** — argparse wrapper for `signalos orchestrate`. | done | AMD-CORE-008. |
| `cli/signalos_lib/commands/status.py` | **new** — argparse wrapper for `signalos status`. | done | AMD-CORE-008. |
| `cli/signalos` | **extend** — dispatch for `orchestrate` and `status` commands; updated usage string. | done | AMD-CORE-008. |
| `core/execution/build/worktree-manager.sh` | **extend** — 4 fixes: HTML comment parser, journal routing, merge-tree fix, step_id field. | done | AMD-CORE-008. |
| `core/governance/Validators/wiring-guard.sh` | **new** — 7-check structural wiring validator. | done | AMD-CORE-009. |
| `core/execution/hooks/session-start` | **extend** — call wiring-guard with --quiet before Summary section. | done | AMD-CORE-009. |
| `.github/workflows/core-proof.yml` | **extend** — add wiring-guard step before proof scenarios. | done | AMD-CORE-009. |
| `core/tool-adapters/_shared/commands.json` | **extend** — register `signalos-orchestrate`, `signalos-status`. | done | AMD-CORE-008. |
| `core/tool-adapters/_shared/skills.json` | **extend** — register `parallel-orchestration`; fix 3 missing: `headless-execution`, `dispatching-parallel-agents`, `subagent-driven-development`. | done | AMD-CORE-008 + AMD-CORE-009. |
| `core/execution/commands/signalos-orchestrate.md` | **new** — command doc. | done | AMD-CORE-008. |
| `core/execution/commands/signalos-status.md` | **new** — command doc. | done | AMD-CORE-008. |
| `core/execution/skills/parallel-orchestration/SKILL.md` | **new** — skill doc. | done | AMD-CORE-008. |
| `integrations/rules/signalos-orchestrate.mdc` | **new** — Cursor rule. | done | AMD-CORE-008. |
| `integrations/rules/signalos-status.mdc` | **new** — Cursor rule. | done | AMD-CORE-008. |
| `core/governance/Governance/CONSTITUTION.md` | **append** — §13 glossary: `LLM provider abstraction`, `parallel wave orchestrator`, `wiring guard`, `Wave status card`. | done | AMD-CORE-007,008,009 vocabulary. |
| `core/governance/Retro/AMENDMENTS.md` | **append** — AMD-CORE-007, AMD-CORE-008, AMD-CORE-009 rows. | done | AMD contract. |
| `SBOM.md` | **append** — `## W2.1 (SignalOS Core 2.1.0)` section; no new required dep assertion; optional provider extras documented. | done | Gate 5. |
| `docs/CHANGELOG.md` | **prepend** — `## 2.1.0 — 2026-04-24` entry. | done | Gate 5. |
| `plugin.json` + `.claude-plugin/plugin.json` | **bump** — `1.3.0` → `2.1.0`. | done | Release hygiene. |
| `proof/scenarios/46_provider_abstraction_default.sh` | **new** — TestProvider via SIGNALOS_HARNESS_TEST=1. | done | AMD-CORE-007. |
| `proof/scenarios/47_provider_env_override.sh` | **new** — provider env var selection. | done | AMD-CORE-007. |
| `proof/scenarios/48_provider_unknown_fails.sh` | **new** — unknown provider → exit 1. | done | AMD-CORE-007. |
| `proof/scenarios/49_orchestrate_t1_wave.sh` | **new** — basic orchestrate with mocked harness. | done | AMD-CORE-008. |
| `proof/scenarios/50_status_card_renders.sh` | **new** — status card exit 0. | done | AMD-CORE-008. |
| `proof/scenarios/51_wiring_guard_pass.sh` | **new** — wiring-guard exits 0 on clean repo. | done | AMD-CORE-009. |
| `proof/scenarios/52_wiring_guard_missing_mdc.sh` | **new** — Check 3 failure on orphan command. | done | AMD-CORE-009. |
| `proof/scenarios/53_wiring_guard_unregistered_skill.sh` | **new** — Check 5 failure on unregistered SKILL.md. | done | AMD-CORE-009. |
| `proof/scenarios/54_wiring_guard_blocks_session.sh` | **new** — wiring gap → session-start exits 1. | done | AMD-CORE-009. |
| `core/governance/Retro/waves/W2.1/METRICS.md` | **new** — W2.1 measurements (placeholders at Wave open; to be measured at Wave close). | done | Gate 5. |
| `core/governance/Retro/waves/W2.1/WAVE_REVIEW.md` | **new** — narrative Wave review; status OPEN (not yet filled). | done | Gate 5. |
| `core/governance/Retro/waves/W2.1/docs-delta.md` | **new** — this file; per-file Wave-close ledger. | done | Gate 5. |

## Definition of done for W2.1 docs

- Every row above is `done`.
- `proof/scenarios/34_single_new_runtime_dep.sh` still green (no new required Python dep).
- `proof/scenarios/99_no_node.sh` still green.
- `proof/scenarios/51_wiring_guard_pass.sh` green (wiring guard passes on clean repo).
- The 3 new AMD rows have hash anchors filled in (pending Wave close measurement).
- `WAVE_REVIEW.md` filled in and co-signed by PO + PE at Wave close.

## Authors

PE-drafted. PO + PE co-sign at W2.1 close.
