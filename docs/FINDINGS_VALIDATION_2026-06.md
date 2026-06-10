# Findings Validation — June 2026

Validation of the external "Findings and Gaps Analysis" against the actual
codebase and live test runs. Each row is the verdict; corrections to the
original document are called out explicitly.

## Verified-true findings

| Finding | Verdict | Evidence |
| --- | --- | --- |
| Rust/Tauri + Python sidecar + Preact/Vite separation | TRUE | `src-tauri/`, `python/signalos_ipc_server.py`, `package.json` |
| Wave Engine state machine, Gates G0–G5 | TRUE | `python/signalos_lib/wave_engine.py` (`GATE_ORDER`) |
| OWASP/STRIDE + GDPR | TRUE | `_bundle/core/governance/SecurityAudit/`, `data_privacy.py` |
| Append-only, tamper-evident audit log | TRUE | `AUDIT_TRAIL_SPEC.md` (HMAC-SHA256), `sign.py` |
| TypeScript typecheck passes | TRUE | `npm run typecheck` → exit 0 |
| Vitest 239/239 in <35s | TRUE | `vitest run` → 239 passed, ~25s |
| Persona dropdown (PO/QA/PE/DevOps) to sign gates | TRUE | `SettingsView.tsx`, `sign.py` `VALID_ROLES` |
| Raw token spend shown ("Wave spend"), no biz abstraction | TRUE | `BuildView.tsx` |
| No multi-player; `shareProject()` is a stub | TRUE | `Toolbar.tsx`, `global.d.ts` |
| No post-launch / Day-2 observability | TRUE | pipeline ends at Handoff |
| Canvas editor / agentic QA personas / time-travel replay / competitor ingestion / GTM auto-gen | CONFIRMED MISSING | exhaustive search |

## Corrections to the original document

1. **"Python sidecar tests exhibit local failures / regressions" — FALSE.**
   `python -m pytest` passes **1196/1196** (plus the new secrets tests) with 0
   failures in a clean environment. The evaluator's local failures were an
   environment/fixture issue, not a codebase regression.

2. **"287 governance rules" — STALE.** The `_bundle/` tree now holds **425
   files** (221 md, 51 yaml, 50 sh, 50 mdc, …). Docs updated to "400+ (425 at
   time of writing)" in `README.md` and `docs/V4_GOVERNED_AGENT_LOOP_PLAN.md`.

3. **Phase strip — INCOMPLETE.** The user-facing lifecycle is
   Brief → Design → Build → Validate → Security → Launch → **Handoff** (7
   phases; the doc omitted Handoff). Internally these map to gate IDs G0–G5
   (Soul/Belief/Plan/Design/Build/Ship); the UI names differ from the internal
   IDs by design (no internal jargon in user-facing surfaces).

4. **Brownfield ingestion — PARTIALLY EXISTS, not missing.**
   `product/stacks.py` `ExistingRepoAdapter` detects existing repos and writes
   governance metadata; it does not yet retroactively refactor. (See EPIC F.)

5. **Blueprints — MECHANISM EXISTS, specific ones missing.**
   `product/blueprints/registry.json` ships Task Management and Financial
   Dashboard; Stripe/Auth0/Supabase boilerplates are not yet present. The
   capability is not missing — the content is. (See EPIC F.)

## Partially-true technical-gap findings

- **"Forced to use @preact/signals over useState"** — overstated. Signals are
  the convention, but `useState` works and is used (e.g. `VelocityPanel.tsx`).
- **"Fails without cargo; no fallbacks/containers"** — build scripts call
  `cargo` unguarded, but `scripts/test-gates.ps1` already skips gracefully when
  cargo is absent. No dev container exists. (See EPIC C.)
