<!-- SignalOS v1.1 — Created 2026-05-20 (M-W4) -->

# Agent — Design

## Purpose (one sentence)

Translate the signed Plan into a reviewable design — a written design-doc paired with a visually-inspectable prototype (or an explicit no-UI attestation) — so G3 catches the layout, navigation, accessibility, and state-transition decisions that markdown alone cannot expose.

## Activates at (which phase/gate)

Phase 3 (Design), immediately after Gate 2 (Plan signed by PO) and before Gate 4 (Build dispatch). The orchestrator auto-fires this agent on the G2→G3 transition per WAVE-ENGINE-DESIGN §2 and v0.2 audit §6.7. The user does not initiate G3.

## Prerequisites (signed artifacts required before activation)

- `core/strategy/BELIEF.md` — Gate 1 signed
- `core/strategy/EXPECTATION_MAP.md` — Gate 2 signed by PO
- `core/execution/PLAN.md` — produced by the Plan agent (PLAN.md need not be signed; signed Expectation Map is the contract; PLAN.md is the work breakdown)

If any signature is missing → refuse and emit a blocker bubble naming the missing artifact.

## Inputs (paths the agent reads)

- `core/execution/PLAN.md` — the task breakdown that the design must visualize
- `core/strategy/EXPECTATION_MAP.md` — success criteria the design must satisfy
- `core/strategy/BELIEF.md` — context for what the user is trying to learn
- `core/governance/Governance/SOUL-DOCUMENT.md` — stakeholder + scope ground truth
- Existing `core/strategy/DESIGN_NOTE.md` from a prior Wave (for continuity)
- Any externally-supplied design references attached to the request (Figma URL, screenshot file, mockup paths)

## Outputs (paths the agent writes, with template links)

The agent produces exactly **one of three valid shapes** per v0.2 audit §6.7. The validator (`_validate_design`) accepts any of them; failure of all three triggers smart-retry via `previous_failure` per §6.6.

| Shape | When to use | Files written |
|---|---|---|
| **doc + prototype/** | Default — the wave's tasks include a UI surface and the agent renders it as a visually-inspectable artifact | `.signalos/designs/<wave>/design-doc.md` + `.signalos/designs/<wave>/prototype/` (Storybook stories, static HTML mock, or a feature-flagged React component) |
| **doc + external-design-ref** | User supplied the design externally (Figma URL, attached image, mockup file) — record the reference rather than regenerating | `.signalos/designs/<wave>/design-doc.md` (with an `## External design reference` section linking to the provided artifact) |
| **doc + no-UI-attestation** | Task is backend-only / schema migration / CLI command / observability tweak — no UI surface to render | `.signalos/designs/<wave>/design-doc.md` (with the one-line attestation `UI surface: none — see attestation` plus rationale; validator verifies the wave's task file-list contains no `.tsx` / `.html` / `.css` writes) |

In addition the agent appends a single audit-trail entry describing which shape was chosen and why, so the wave's retrospective can show the design decision history without re-reading the doc.

## design-doc.md body shape (canonical sections)

A design-doc is not a wishlist. Sections in this order:

1. `## Context` — what we're designing for; ties to BELIEF + EXPECTATION_MAP.
2. `## Information architecture` — what data the UI surfaces, what hierarchy.
3. `## Constraints` — accessibility floor, mobile-vs-desktop, browser support, performance budget. Cite the SOUL-DOCUMENT if those constraints come from there.
4. `## Alternatives considered` — at least two; record what was rejected and why.
5. `## Chosen approach` — describe the decision; link to the prototype (or external ref, or no-UI attestation).
6. `## Open questions` — what the design is leaving for the build-time decision; bound it.

## Refusal conditions (when this agent STOPS and does not act)

- PLAN.md does not exist → refuse: "G3 requires a PLAN.md from Gate 2 — fire the Plan agent first."
- Expectation Map is unsigned → refuse: "G3 requires a signed Expectation Map — sign G2 before G3 fires."
- The wave's task list cannot be inspected (worktree-state missing) → refuse: "Cannot determine UI surface — task list missing."
- The chosen shape is "no-UI-attestation" but the task list contains `.tsx` / `.html` / `.css` writes → refuse: "Attestation contradicts task list; choose doc + prototype/ or doc + external-ref."
- The prototype directory exists but is empty after generation → refuse: "Prototype shape claimed but no artifacts produced."

## Handoff (who receives the output + what goes in the HAND entry)

Receiver: **PO** for the G3 sign.

HAND entry records:
- `design-doc.md` SHA
- Chosen shape (`doc+prototype` | `doc+external-ref` | `doc+no-UI-attestation`)
- For `doc+prototype`: top-level prototype paths (Storybook stories.tsx files, the HTML mock entry point, or the feature-flagged component name)
- For `doc+external-ref`: the external URL / file path
- For `doc+no-UI-attestation`: the verbatim attestation line + the validated empty UI file-list

## Trust Tier ceiling (from Charter, surface-overridable per Wave)

**T2** — proceeds with PO review at sign time. Writes only to `.signalos/designs/<wave>/`; never modifies production code or signed artifacts. The PO must approve the design shape before the Build agent fires; affirmation auto-signs G3 per WAVE-ENGINE-DESIGN §8.

## Notes for the wave engine (M-W4 callers)

- The agent's reply at the end of generation is interpreted by `WaveEngine.handle_user_reply`. An affirmative reply ("yes" / "approve" / "looks good") triggers `sign_current_gate` for G3 automatically. A refinement reply ("change the layout to two columns") re-invokes this agent with the refinement as additional context. A question ("does this approach handle the empty state?") is answered conversationally and does not advance the gate.
- When the user supplies an external design reference in the original wave request, the engine should pass it through as the `external_design_ref` field on the agent invocation so this agent picks `doc + external-design-ref` shape from the start rather than generating a prototype that will then be discarded.
- When all three shapes fail validation, the engine records the failure in `previous_failure` and re-fires the agent with that context — the same smart-retry pattern used by `_validate_security_audit` and `_validate_test_generation`.
