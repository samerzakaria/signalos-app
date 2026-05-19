<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.1 — W1.1 Wave Review template. Filled in at Wave close. -->

# W1.1 — Wave Review

`Canonical path: core/governance/Retro/waves/W1.1/WAVE_REVIEW.md · Filled in by: PO + PE at Wave close · Distinct UserIds required on sign-off (product-Constitution §F.3)`

The Wave Review is the narrative counterpart to `METRICS.md`: what actually shipped in W1.1, what nearly didn't, what amendments became force-of-law, and which learnings must be carried forward into W1.2 (headless harness) and W1.3 (context compression + plugin registry). This file is a **template**; every section below begins `<to be filled>` and is replaced with real content at Wave close.

## What shipped

Expected content: a 5–12 bullet list enumerating each capability that landed in W1.1 — AMD-CORE-001 session journal, AMD-CORE-002 step-pause, AMD-CORE-003 observability dashboard, the shared infrastructure (`redact.py`, `journal-append.sh`, `hook-registration-helper.sh`, `TRUST_TIER.md`, `.gitattributes`), the seven-emitter hook patches, the `signalos` Python CLI surface, the Core StartHere HTML, the `core/README.md`, and the proof scenarios that gate each. One bullet per user-visible capability, with a link to the scenario(s) that prove it.

* Session journaling landed as the disk-truth backbone of Core: append-only `journal.jsonl`, redaction at write time, and the `signalos session` surface for list/show/resume/archive.
* Step-pause landed as an opt-in per-step control point: `pause: true` in PLAN blocks, `signal-pause` for resume/abort, and a hard refusal on T3 surfaces.
* Observability landed as a metrics sidecar plus a zero-JS static dashboard, so the Wave can be reviewed without a browser runtime or a network dependency.
* The hook-registration path was made emitter-neutral: one helper, seven emitter `register-hooks.sh` scripts, and the same hook surface everywhere.
* The Core landing surfaces shipped together: `core/README.md`, `core/StartHere.html`, `core/CREDITS.md`, the `signalos` CLI, and the proof scenarios that pin behaviour.
* The release line is now proof-gated instead of narrative-gated: scenarios 18–30, 42, and 99 are the release contract for W1.1.

## What almost didn't

Expected content: the top 2–5 risks from §8 that materialised during the Wave, with the mitigation that was actually applied. Candidates (per `CORE_BABYSITTER_INTEGRATION_PLAN.md` §8): journal schema drift, emitter patch drift across the seven editors, journal hot-path regression, pause/Gate conceptual conflation, docs drift against shipped code, wording leakage of babysitter-native terms into user-facing docs. Each entry names the risk, the signal that surfaced it, and the corrective action taken before Gate 5.

* Journal-schema drift was the main technical risk. It was contained by forcing every write through `core/execution/hooks/_lib/journal-append.sh` and `redact.py`, so the journal stayed append-only and redacted on write.
* Pause-vs-gate confusion was another risk. It was contained by keeping pause opt-in per step, keeping it outside the Gate model, and refusing `pause: true` on T3 steps.
* Emitter drift across the seven editors was real. It was contained by pushing the same hook patch set through every emitter, not by special-casing one editor path.
* Docs drift had to be corrected as part of the Wave, especially the dashboard render path and scenario references. The visible docs were updated to match the real file layout and proof names.
* Wording drift was kept in check by using the Core vocabulary consistently in the shipped docs and not letting legacy phrasing leak back into the user-facing surfaces.

## Amendments ratified

Expected content: one row per AMD that crossed from `pending` to in-force during W1.1 — at minimum AMD-CORE-001, AMD-CORE-002, AMD-CORE-003 per `core/governance/Retro/AMENDMENTS.md`. Each row records the measured W1.1 hash anchor, the distinct-UserId PO and PE signatures, and the ratification Gate (W1.1 Gate 1) with the date of force.

| AMD-CORE-001 | `core/TRUST_TIER.md` sha256 `64c752485c4594d3c10f3f21fb6dc6e00a2daca3162f4dbd63a2dd992671e74a` | Samer Zakaria | Mohammed Shaban | W1.1 Gate 1 | 2026-04-22 |
| AMD-CORE-002 | `core/TRUST_TIER.md` sha256 `64c752485c4594d3c10f3f21fb6dc6e00a2daca3162f4dbd63a2dd992671e74a` | Samer Zakaria | Mohammed Shaban | W1.1 Gate 1 | 2026-04-22 |
| AMD-CORE-003 | `core/TRUST_TIER.md` sha256 `64c752485c4594d3c10f3f21fb6dc6e00a2daca3162f4dbd63a2dd992671e74a` | Samer Zakaria | Mohammed Shaban | W1.1 Gate 1 | 2026-04-22 |

## Learnings that flow into W1.2 / W1.3

Expected content: explicit carry-forward items. For W1.2 (headless harness, AMD-CORE-004): every decision about journal shape, redaction, and idempotency that the 8th emitter must honor without re-litigation. For W1.3 (context compression AMD-CORE-005 and plugin registry AMD-CORE-006): the measured journal size per session, the metrics-write budget, and any "never compress" additions that surfaced during W1.1 operation. One bullet per learning, each tagged `-> W1.2` or `-> W1.3`.

* `-> W1.2` The 8th emitter must keep using the same journal and metrics helpers so the harness emits the same event shape as the editor emitters.
* `-> W1.2` Step-pause remains opt-in and must continue to hard-stop T3 surfaces instead of creating a second runtime bypass.
* `-> W1.3` The dashboard stays zero-JS and no-network; the renderer should remain a pure file-to-file transform.
* `-> W1.3` Disk-truth files (`journal.jsonl`, `metrics.jsonl`, `AUDIT_TRAIL.jsonl`) stay immutable inputs to later compression and registry work.

## Sign-off (PO + PE distinct UserId)

Expected content: the two co-signer rows below, each carrying a distinct `UserId` per product-Constitution §F.3. The Wave cannot cross Gate 5 while either row is unfilled; a row with matching `UserId`s is a §F.3 invariant violation and is refused by `signalos validate-gate`.

| Role | Name | UserId | Date | Signature (SHA-256 of this file at sign-off) |
|---|---|---|---|---|
| PO | Samer Zakaria | Samer Zakaria | 2026-04-23 | `69676062352312153f24544df22fe01743a4a73066a899ccd156b81469ba2cf1` |
| PE | Mohammed Shaban | Mohammed Shaban | 2026-04-23 | `69676062352312153f24544df22fe01743a4a73066a899ccd156b81469ba2cf1` |

## Fill-in ritual

At Wave close the PE drafts every `<to be filled>` block, the PO reviews and edits, both co-sign with distinct UserIds, and the file is committed together with the populated `METRICS.md` and the measured-hash edits to `core/governance/Retro/AMENDMENTS.md`. Any remaining `<to be filled>` placeholder blocks the Wave from crossing Gate 5.
