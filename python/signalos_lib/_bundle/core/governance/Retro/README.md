# core/governance/Retro/ — Wave Retrospectives & Amendments

This directory holds the append-only governance record for every SignalOS Core Wave. It is the audit backbone: amendments, wave reviews, metrics, and per-file deltas all live here.

## Structure

```
Retro/
├── AMENDMENTS.md          ← Master ledger of all ratified amendments (AMD-CORE-001–009)
├── retro-template.md      ← Template for filling in WAVE_REVIEW.md at Wave close
├── retrospective.md       ← Retro ceremony guide
├── retro-run/SKILL.md     ← Skill for running the retro ceremony
├── retrospective-analyze/ ← Skill for analyzing retro output
└── waves/
    ├── W1.1/   WAVE_REVIEW.md · METRICS.md · docs-delta.md  (signed)
    ├── W1.2/   WAVE_REVIEW.md · METRICS.md · docs-delta.md  (signed)
    ├── W1.3/   WAVE_REVIEW.md · METRICS.md · docs-delta.md  (signed)
    └── W2.1/   WAVE_REVIEW.md · METRICS.md · docs-delta.md  (open — fill at Wave close)
```

## What gets signed

Per Constitution §F.3: only `WAVE_REVIEW.md` requires PO + PE co-signatures with distinct UserIds. `METRICS.md` and `docs-delta.md` are operator records — no signature required.

## Amendment process

1. PO or PE drafts an amendment proposal in AMENDMENTS.md with status `pending`.
2. The Wave delivers the code / docs that enact the amendment.
3. At Wave close, both PO and PE sign WAVE_REVIEW.md with the measured hash anchor.
4. The amendment row is updated to `in-force` with the hash anchor and ratification date.
5. The Constitution §13 glossary is updated with any new terms.

## Current amendment count

AMD-CORE-001 through AMD-CORE-009 ratified. Next: AMD-CORE-010 (W2.2).
