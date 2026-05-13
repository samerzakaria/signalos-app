---
description: "Run a structured stakeholder interview and emit a Discovery Brief — the prerequisite artifact for /signal-onboard. (W16, AMD-CORE-037)."
---

<!-- SignalOS v1.0 — /signal-discovery command spec (W16, AMD-CORE-037). -->

# /signal-discovery — Stakeholder interview & Discovery Brief

**Phase:** discovery (pre-onboarding)
**Owner:** PO
**AMD:** AMD-CORE-037
**Wave:** W16

## Purpose

`/signal-discovery` is the entry point that makes the `stakeholder-interview` skill discoverable. Without this command, a new PO has no surfaced way to know that Discovery Briefs are required before `/signal-onboard` will run, and no documented path to produce one.

Each invocation runs one interview and emits one Discovery Brief. Most products need 3–5 briefs total.

## Output

`core/strategy/discovery-briefs/wave-0-session-N.md` (auto-incrementing N).

## Skill invoked

`core/execution/skills/stakeholder-interview/SKILL.md` — the structured interview script.

## Template

`core/strategy/Templates/discovery-brief-template.md` — the output shape.

## Exit criteria

- Discovery Brief file exists at the canonical path.
- Every section in the template is filled (use `(unknown — to surface in next session)` for genuinely unknown items).
- The brief is signed by the PO with the interview date.
- At least one **Disproof condition** is recorded.
- At least one **Surface item** is recorded.

## Sequencing

`/signal-discovery` runs **before** `/signal-onboard`. You may run it multiple times (one per stakeholder). `/signal-onboard` reads every brief at `core/strategy/discovery-briefs/wave-0-session-*.md`.

## Failure modes

- **No template found** → check that `core/strategy/Templates/discovery-brief-template.md` ships in your distro version.
- **No `core/strategy/discovery-briefs/` directory** → create it; `/signal-discovery` does not auto-create directories outside its output path.
- **Stakeholder unavailable** → file an "asynchronous brief" using stakeholder-supplied written input (email, Slack threads, recorded transcripts). Mark `interview_mode: async` in the brief.
