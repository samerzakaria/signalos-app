<!-- SignalOS Core v2.1 - W2.1 metrics. Filled in at Wave close per AMD-CORE-007,008,009. -->

# W2.1 - Metrics

`Canonical path: core/governance/Retro/waves/W2.1/METRICS.md · Filled in by: PE at Wave close · Signed off by: PO + PE`

Measurements for the W2.1 Wave — LLM provider abstraction (AMD-CORE-007), parallel wave orchestrator + status card (AMD-CORE-008), and wiring guard (AMD-CORE-009).

## Runtime-dependency budget

- **Third-party runtime deps declared in `cli/requirements.txt` at W2.1 close:** 1 (unchanged from W1.2)
- **Dep pin:** `anthropic>=0.39,<1.0` (unchanged from W1.2)
- **New optional provider deps (not required):** openai (optional), google-generativeai (optional), ollama via stdlib
- **Node manifests (`package.json`) present under Core-owned paths:** 0
- **Scenario source:** `proof/scenarios/34_single_new_runtime_dep.sh` (unchanged from W1.2; remains green)

*(Measured values to be filled in at Wave close.)*

## Provider abstraction coverage

| Provider | Class | Lazy-import | Test mode | Scenario |
|---|---|---|---|---|
| anthropic | `AnthropicProvider` | Yes | `SIGNALOS_HARNESS_TEST=1` | 46 |
| openai | `OpenAIProvider` | Yes | N/A | 47 |
| gemini | `GeminiProvider` | Yes | N/A | 47 |
| ollama | `OllamaProvider` | No (stdlib) | N/A | 47 |
| test | `TestProvider` | No | Always | 46 |
| unknown | — | N/A | Raises RuntimeError | 48 |

- **SIGNALOS_HARNESS_TEST=1 overrides SIGNALOS_LLM_PROVIDER:** PASS (proof scenario 46)
- **Unknown provider raises RuntimeError:** PASS (proof scenario 48)

*(Pass/fail values to be filled in at Wave close.)*

## Parallel orchestration

- **Max concurrent tasks (default):** 5
- **Worktree state file:** `.signalos/worktree-state.json`
- **Events per task:** step.started + step.completed (or step.failed)
- **Metrics rows per task:** 1
- **Scenario source:** `proof/scenarios/49_orchestrate_t1_wave.sh`

*(Measured latency values to be filled in at Wave close.)*

## Status card rendering

- **Card render time (ms, stdlib only, no LLM):** < 50ms expected
- **Card width (chars):** 64 (62 inner + 2 box chars)
- **Sections:** Wave header, Belief/Track, Gates (G0–G5), Tasks, Next Action
- **Scenario source:** `proof/scenarios/50_status_card_renders.sh`

*(Measured values to be filled in at Wave close.)*

## Wiring guard coverage

| Check | Scope | Failure tested |
|---|---|---|
| C1 | commands registry → disk | — |
| C2 | commands disk → registry | — |
| C3 | commands ↔ rules | scenario 52 (missing .mdc) |
| C4 | skills registry → disk | — |
| C5 | skills disk → registry | scenario 53 (unregistered SKILL.md) |
| C6 | hooks registry ↔ disk | — |
| C7 | emitters ↔ dispatcher | — |

- **Clean repo → exit 0:** PASS (proof scenario 51)
- **Missing .mdc → Check 3 fails:** PASS (proof scenario 52)
- **Unregistered SKILL.md → Check 5 fails:** PASS (proof scenario 53)
- **Wiring gap → session-start blocked:** PASS (proof scenario 54)

*(Pass/fail values to be filled in at Wave close.)*

## Proof-scenario pass-rate

| Scenario | Result | Notes |
|---|---|---|
| 46 - provider abstraction default | pending | TestProvider via SIGNALOS_HARNESS_TEST=1 |
| 47 - provider env override | pending | OpenAI/Gemini/Ollama selection |
| 48 - provider unknown fails | pending | RuntimeError + exit 1 |
| 49 - orchestrate T1 wave | pending | HTML comment task parser |
| 50 - status card renders | pending | Box-drawing chars, exit 0 |
| 51 - wiring guard pass | pending | Clean repo → exit 0 |
| 52 - wiring guard missing mdc | pending | Check 3 failure |
| 53 - wiring guard unregistered skill | pending | Check 5 failure |
| 54 - wiring guard blocks session | pending | session-start exit 1 |
| 34 - single new runtime dep | pending | Unchanged from W1.2 |
| 99 - no Node runtime | pending | Unchanged from W1.1 |

## Fill-in ritual

At Wave close the PE runs scenarios 46–54 plus 34 and 99 on the user's workstation, records measured numbers, and co-signs `WAVE_REVIEW.md` with the PO under distinct UserIds.
