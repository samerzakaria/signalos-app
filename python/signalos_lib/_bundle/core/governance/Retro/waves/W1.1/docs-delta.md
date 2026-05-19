<!-- SignalOS Core v1.1 — W1.1 docs delta. Append-only during the Wave. -->

# W1.1 — Docs delta

`Canonical path: core/governance/Retro/waves/W1.1/docs-delta.md · Authored by: PE · Signed by: PO + PE at Wave close`

Everything documentation-shaped that the **session-journal + step-pause + observability** trio changes across the SignalOS Core distro. One row per file touched, one reason per row. The Wave cannot close Gate 4 → Gate 5 while any row is `pending`.

## Files touched by W1.1

| Path | Change | State | Why |
|---|---|---|---|
| `core/README.md` | **new** — Core-only landing page that tracks `plugin.json` version. | pending | AMD-CORE-001,003 — gives the Core distro its own entry point distinct from `README.md` (which is SignalOS.NET-owned per plan §10.2). |
| `core/CREDITS.md` | **new** — attribution for every external concept source (babysitter MIT license text, etc.). | pending | Plan §10.1 — inline per-file attribution needs a central ledger. |
| `core/TRUST_TIER.md` | **new** — W1.1–W1.3 surface classifications. | done | AMD-CORE-001,002,003 — every new surface needs a declared tier. |
| `core/CONSTITUTION.md` | **append** — glossary entries for `session journal`, `step-pause`, `metrics sidecar`, `permanently-T3 surface (extended)`. | pending | AMD-CORE-001,002,003 vocabulary is not yet in the glossary. |
| `core/execution/skills/session-journal/SKILL.md` | **new** — skill doc: 10 canonical event types, redaction contract, resume flow. | pending | AMD-CORE-001 — new skill registered in `_shared/skills.json`. |
| `core/execution/skills/observability-dashboard/SKILL.md` | **new** — skill doc: how to read / refresh / extend the static HTML dashboard without adding Node. | pending | AMD-CORE-003. |
| `core/execution/commands/signal-pause.md` | **new** — command doc: opt-in pause semantics, PLAN `pause: true`, resume/abort. | pending | AMD-CORE-002. |
| `core/execution/commands/signalos-session.md` | **new** — command doc: `list / show / resume / archive`. | pending | AMD-CORE-001. |
| `core/tool-adapters/_shared/hooks.json` | **extend** — register `step-started`, `step-completed`, `step-failed`, `pre-session-compress`. | done | AMD-CORE-001,002,003. |
| `core/tool-adapters/_shared/commands.json` | **extend** — register `signal-pause`, `signalos-session`. | done | AMD-CORE-001,002. |
| `core/tool-adapters/_shared/skills.json` | **extend** — register `session-journal`, `observability-dashboard`. | done | AMD-CORE-001,003. |
| `core/tool-adapters/{claude-code,cursor,vscode,copilot,windsurf,codex,antigravity}/README.md` | **extend** — each emitter's README gets a W1.1 section listing the 4 new hooks and their purpose. | pending | Plan §5.4 — emitter neutrality: every emitter carries the same docs update. |
| `proof/scenarios/99_no_node.sh` | **new** — CI refuses any Node leak. | done | Plan §10 — hard constraint. |
| `docs/CHANGELOG.md` | **append** — `## 1.1.0 — 2026-04-22` entry with the W1.1 close summary. | pending | Gate 5 requirement. |
| `SBOM.md` | **append** — no new runtime deps in W1.1 (stdlib only); record the stdlib-only assertion. | pending | Gate 5 requirement. |
| `core/governance/Retro/AMENDMENTS.md` | **append** — AMD-CORE-001,002,003 move from planning placeholders into the ratified Core distro ledger with the measured W1.1 hash anchor. | pending at Wave close | AMD contract. |

## Definition of done for W1.1 docs

- Every row above is `done`.
- `core/README.md` passes the "read this in 10 minutes" check by two humans who have never used Core.
- Every new skill and command has a working example block.
- `docs/CHANGELOG.md` and `SBOM.md` are internally consistent with each other.
- The 3 new AMD-CORE rows have measured Constitution hashes filled in.

## Authors

PE-drafted. PO + PE co-sign at W1.1 close.
