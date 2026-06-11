# Roadmap — "AI Product Studio" big features (EPIC E)

These are the large product features from the findings analysis. Each is a
multi-step feature; this doc records the concrete approach so they can be picked
up incrementally. Status reflects work in this branch.

**Status: a first, tested cut of every item has landed.** What remains per item
is integration/UI polish, noted inline:
- E1 canvas editor — landed (`visualEdit.ts` + PreviewView toggle). Remaining:
  cross-origin previews need to include the picker snippet themselves.
- E2 agentic QA — landed (`ux_friction.py`). Remaining: run before the Validate
  gate and surface as a gate card.
- E3 time-travel replay — landed (`audit_replay.py` + `signalos replay`).
  Remaining: a visual scrubber in HistoryView over the timeline frames.
- E4 competitor ingestion — landed (`competitor.py`). Remaining: wire into the
  Brief phase and a UI to paste URLs.
- E5 multi-player — read-only share snapshot landed (`share_export.py`).
  Remaining: live co-editing needs a sync backend (separate project).
- E6 day-2 observability — ingest contract + summary landed
  (`observability.py`). Remaining: a thin SDK in generated apps + a view.
- E7 GTM — landed (`gtm.py`). Remaining: call at closeout and write the assets
  into the handoff bundle.
- F1 brownfield governance — landed (`brownfield.py`). Remaining: invoke from
  the existing-repo onboarding path.
- D1/D2 — landed (macro strip in BuildView; sign-as-required-role).

Original per-item approach notes follow.

## E2 — Simulated User Testing (Agentic QA) ✅ first cut landed

`python/signalos_lib/product/ux_friction.py`. Evaluates a generated UI surface
from five personas (Impatient, Colorblind, First-time, Mobile, Keyboard-only).
Deterministic static-analysis lenses run with no browser/LLM (fast, free,
tested); an optional LLM pass augments findings and resolves its key
product-first (EPIC A). Emits a structured UX Friction Report
(`{personas, summary}`), severities in plain language (high/medium/low).

**Remaining integration:** run it before the Validate gate, feeding it the
preview HTML the dev server already serves; surface the report as a gate review
card in `BuildView`. Optionally drive a real browser (reuse `qa_runner.py`'s
SBrowser) so personas *navigate* rather than statically analyse.

## E1 — Point-and-click Canvas editor
Add a Visual Edit mode to `PreviewView`. Inject a small selector script into the
preview iframe that, on element click, posts `{selector, tag, text}` to the host
via `postMessage`. The host opens a contextual prompt ("make this red"); the
instruction + selector is sent into the existing chat/agent loop as a scoped
edit. No DOM-hierarchy description required from the user.
**Risk:** iframe is sandboxed (CSP) — use `postMessage`, never inline injection
(see CSP-bootstrap shim convention).

## E3 — Time-travel audit replay
Backend: add `replay_audit(root, at_index)` over the existing append-only
`.signalos/AUDIT_TRAIL.jsonl`, reconstructing prompt/code-state/gate-decision at
any entry. Frontend: a scrubber in `HistoryView` that scrolls the reconstructed
timeline. The tamper-evident log already exists — this is read-side only.

## E4 — Competitor ingestion
During Brief: accept competitor URLs, fetch + extract (title, headings, CTAs,
pricing), and have the LLM build a Competitive UX Matrix. Backend module
`product/competitor.py` with a fetch+summarise pipeline; gate behind
`is_llm_available(root)`. **Risk:** scraping is fragile and has ToS/legal
considerations — fetch politely, cache, and make it opt-in.

## E5 — Multi-player
`window.shareProject()` is currently a stub (`Toolbar.tsx`). True multi-player
needs a sync backend (presence, shared workspace state, roles). Smallest first
step: a read-only shared link / export that a human QA or marketer can open.
Full collaboration is a server-side project, not a desktop-only change.

## E6 — Day-2 observability
A post-Handoff hub: live traffic, crash reports, and user feedback piped back
into the agent loop to seed the next wave. Requires the deployed product to emit
telemetry to an endpoint the app can read. New `ObservabilityView` + an ingest
contract. Largest of the set; depends on a deploy target.

## E7 — GTM auto-generation
During Deliver/closeout (`product/closeout.py`), generate SEO landing copy, app
store copy, and a Product Hunt post from the product intent. New
`product/gtm.py`, LLM-gated, output as handoff artifacts alongside the runbook.

## Sequencing
E2 integration and E3 are the cheapest high-value next steps (both lean on
infrastructure that already exists). E7 is self-contained. E1 is moderate
frontend work. E4 needs a scraping policy. E5 and E6 require backend/runtime
infrastructure beyond the desktop app and should be scoped as their own
projects.
