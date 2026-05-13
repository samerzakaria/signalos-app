---
name: headless-execution
description: "Choose when to run steps through headless harness versus editor emitters."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.2 — headless-execution skill (AMD-CORE-004). -->

# Skill: headless-execution

Owner: PE. Decision-support skill — not a runtime agent.

## When to invoke

Load this skill whenever the caller needs to decide between running a
PLAN step through an editor emitter (Claude Code, Cursor, Codex,
VS Code, Windsurf, GitHub Copilot, Antigravity) or through the
**harness** — the 8th emitter introduced in W1.2 (AMD-CORE-004).

The decision is routine enough that the skill's main job is to surface
a short checklist and a side-by-side table; it does not spawn a
sub-agent or otherwise leave the current context.

## Editor emitter vs harness — when to pick which

| Dimension | Editor emitter (1–7) | Harness (8th) |
|---|---|---|
| Attached human | Required — a real IDE session. | None. The call runs unattended. |
| Typical caller | Developer in flow, replying to the editor chat UI. | CI job, batch script, `signalos` cron. |
| Network dependency | Same as the editor — browser / RPC. | Direct HTTPS to `api.anthropic.com`. |
| Model choice | Whatever the editor is configured with. | Explicit `--model <id>`; default `claude-sonnet-4-5`. |
| Auth | Editor's own signed-in user. | `ANTHROPIC_API_KEY` env var (per-CI-runner secret). |
| Journal events emitted | `step.started`, `step.completed`, `step.failed`, `pre-session-compress` via the editor's emit.sh. | Same four events via `core/execution/hooks/<event>/<event>.sh`. Byte-identical shape. |
| Metrics row | One per hook fire. | One per call, plus the per-hook rows inherited from the hook scripts. |
| Pause behaviour | `pause: true` in PLAN → editor halts until `signalos pause resume`. | Same. The harness honours the pause gate via `step-started.sh`. |
| T3 hard-stop | Refused. | Refused. No emitter overrides Constitution §C.3. |
| Abort channel | Close the editor tab. | `signalos harness abort <call-id>` writes `abort.flag`; the running harness observes it and emits `step.failed`. |
| Review pathway | Human inspects in-IDE, merges. | CI posts the journal diff and response preview to the PR / issue; human reviews out-of-band. |
| Best when | Exploratory, interactive, or requires IDE-resident context the model cannot see. | Deterministic, parallelisable, or triggered outside business hours. |
| Wrong when | CI has to parallelise 50 steps across 3 Waves overnight. | The step needs the developer to read intermediate output and choose. |

## Headless-safe step design — checklist

Before a step is marked safe to run through the harness (e.g. before CI
is allowed to dispatch it), confirm:

1. **Self-contained prompt.** The prompt includes or resolves every file
   path, Belief, and Expectation the model will need. No "see my
   editor for context."
2. **Deterministic success criterion.** The step-spec names one file /
   diff / metric whose shape decides completion. The harness cannot
   judge vibes.
3. **Bounded token budget.** The step declares a reasonable `max_tokens`
   (the harness hard-caps at the `anthropic` SDK default, 1024, unless
   the code is updated).
4. **No IDE-only artifact.** No "open the diff in the sidebar" style
   instructions — the harness has no UI to open.
5. **Pause-gate expectation is explicit.** If the PLAN step-spec has
   `pause: true`, the dispatching CI job must either have a pause-
   release worker ready, or the step must be flagged
   `harness_unsafe: true` so CI skips it.
6. **Redaction survives.** Prompt and response are persisted only as a
   truncated preview (`response.preview.txt`) that has already been
   through `core/execution/hooks/_lib/redact.py`. If the step produces
   secrets on purpose (which should be very rare), wrap them in a
   recognised pattern so the redactor catches them before disk write.
7. **Observability row is meaningful.** The harness records
   `tokens_in`, `tokens_out`, `duration_ms` in `metrics.jsonl`. If the
   step's cost/latency are not well-modelled by those fields, extend
   the dashboard contract first — do not smuggle extra fields into the
   journal.

## Pitfalls

- **Running the harness inside an editor session.** The harness fires
  the same hook scripts as the editor emitter — running both for the
  same session id simultaneously double-emits events. Use a distinct
  `--session-id` for every harness call that runs alongside an editor
  session, or run the harness only when no editor is attached.
- **Forgetting `ANTHROPIC_API_KEY`.** The SDK raises at call time, the
  harness catches the exception, emits `step.failed`, and exits 2. This
  is correct behaviour but easy to diagnose as a harness bug — the log
  line on stderr names the missing env var.
- **Using `--headless` on the dispatcher but then invoking an editor
  emit.sh directly.** The dispatcher's `--headless` flag only affects
  auto-detection; if a caller invokes `core/tool-adapters/emitters/
  claude-code/emit.sh` explicitly, no amount of dispatcher flags will
  change that. Keep the entry point discipline.

## How this skill is invoked

Add `headless-execution` to a PLAN step's `skills:` list. The skill is
advisory — Core does not auto-route between editor and harness based
on its output. The PE still picks.

## Prior art

The headless-execution concept is borrowed from `a5c-ai/babysitter`
(MIT). No source code copied.
