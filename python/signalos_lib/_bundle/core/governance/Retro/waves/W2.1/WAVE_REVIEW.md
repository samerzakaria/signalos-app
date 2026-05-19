<!-- SignalOS Core v2.1 - W2.1 Wave Review. Status: OPEN — not yet filled. -->

# W2.1 - Wave Review

`Canonical path: core/governance/Retro/waves/W2.1/WAVE_REVIEW.md · Filled in by: PO + PE at Wave close · Distinct UserIds required on sign-off (product-Constitution §F.3)`

The W2.1 Wave introduced **LLM provider abstraction** (AMD-CORE-007), the **parallel wave orchestrator + Wave status card** (AMD-CORE-008), and the **wiring guard** (AMD-CORE-009). This Wave Review will be the narrative counterpart to `METRICS.md`: what shipped, what nearly didn't, which amendments became force-of-law, and which learnings carry forward.

## What shipped

*(Fill in at Wave close.)*

- AMD-CORE-007: `LLMProvider` Protocol + five concrete providers (Anthropic, OpenAI, Gemini, Ollama, Test). `_resolve_provider()` with env-var and flag override. `--provider` flag on `signalos harness call`. All existing tests pass unchanged.
- AMD-CORE-008: `cli/signalos_lib/orchestrator.py` — `run_wave()` with `ThreadPoolExecutor` dispatch, worktree lifecycle, status card integration. `cli/signalos_lib/status.py` — `get_wave_status()`, `render_status_card()`, `print_status_card()`. `signalos orchestrate` and `signalos status` CLI commands. Four worktree-manager.sh fixes.
- AMD-CORE-009: `core/governance/Validators/wiring-guard.sh` — seven checks. session-start integration. CI integration. Three missing skills.json entries fixed.
- Proof scenarios 46–54 cover all three amendments.

## What almost didn't

*(Fill in at Wave close.)*

## Amendments ratified

| AMD | Title | Hash anchor (measured) | PO | PE | Ratification Gate | Date of force |
|---|---|---|---|---|---|---|
| AMD-CORE-007 | LLM provider abstraction | `cli/signalos_lib/harness.py` sha256 TBD | — | — | W2.1 Gate 1 | 2026-04-24 |
| AMD-CORE-008 | Parallel wave orchestrator + status card | `cli/signalos_lib/orchestrator.py` sha256 TBD | — | — | W2.1 Gate 1 | 2026-04-24 |
| AMD-CORE-009 | Wiring guard (7 checks, CI + session-start) | `core/governance/Validators/wiring-guard.sh` sha256 TBD | — | — | W2.1 Gate 1 | 2026-04-24 |

See `core/governance/Retro/AMENDMENTS.md` for the canonical rows.

## Learnings that flow into W2.2 and beyond

*(Fill in at Wave close.)*

## Sign-off (PO + PE distinct UserId)

| Role | Name | UserId | Date | Signature (SHA-256 of this file at sign-off) |
|---|---|---|---|---|
| PO | | | | |
| PE | | | | |

## Fill-in ritual

At Wave close the PE re-runs scenarios 31–54 plus 99, records the close values in `METRICS.md` and this file, and both PO and PE co-sign under distinct UserIds.
