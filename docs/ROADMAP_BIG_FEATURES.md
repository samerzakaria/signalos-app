# Roadmap — "AI Product Studio" big features (EPIC E)

These are the large product features from the findings analysis. Each is a
multi-step feature; this doc records the concrete approach so they can be picked
up incrementally. Status reflects work in this branch.

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
