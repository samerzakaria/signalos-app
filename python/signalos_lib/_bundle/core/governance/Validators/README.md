<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Validators

`Folder: core/governance/Validators/ · Authored by: DevOps + PE · Cadence: amended when a validator is added/changed/retired`

> The mechanical arm of SignalOS enforcement. Every validator here is a standalone CI check that runs on every PR, fails closed, and has a one-line job description that does not change without a Constitution amendment. v1.0 ships with 9 runnable `.sh` scripts in this folder.

---

## Current validator set (v1.0)

| Validator | Trigger | What it checks | What it rejects |
|---|---|---|---|
| `gate-signature-guard` | PR modifies a signed artifact | Signature line is non-empty on touched Gate artifact | Commits that move an artifact forward without signing |
| `trust-tier-guard` | PR diff touches any surface | Surface's declared tier matches PR claim | PR that touches permanently-T3 surface without T3 declaration |
| `tier-sheet-guard` | Daemon mode active | Every PR surface is listed in `PRODUCT_TIER_SHEET.md` | PRs touching unmapped surfaces |
| `artifact-shape-guard` | PR modifies any artifact with a template | All required template sections present, non-empty headings | Artifacts stripped of their canonical structure |
| `path-consistency-guard` | SignalOS distro change | All internal path references resolve to real files | Commands / charters referencing dead paths |
| `expectation-redline-guard` | PR modifies `EXPECTATION_MAP.md` (Wave track) | Redlines section is populated OR PO-note explains zero-redlines | Frictionless Expectation Maps in Wave track |
| `constitution-amendment-guard` | PR modifies `CONSTITUTION.md` | Amendment follows §13 path (proposal + retro window + sign-off) | Direct Constitution edits without amendment record |
| `decision-dna-guard` | PR modifies `DECISION-DNA.md` | Append-only (new entries prepended, old entries only editable to add `Superseded by DEC-{NNNN}` line) | Edits that rewrite existing DECs |
| `client-signal-verbatim-guard` | PR modifies `CLIENT-SIGNAL-LOG.md` | New SIG entry has a quoted block AND sentiment field | Paraphrased client input |

---

## Validator contract

Every validator file follows a fixed shape:

```
# Validator — {name}

## Purpose
## Triggers (when does it run)
## Input (what it reads)
## Rejection rule
## Exit codes
## Amendment history
```

A validator that cannot be expressed in this shape is either too large (split it) or too vague (sharpen the rejection rule).

---

## How to add a new validator

1. Author the validator file at `core/governance/Validators/{name}.md` following the contract above.
2. Add a row to the table in this README.
3. Implement the CI check (language-agnostic — bash / node / python).
4. Propose a Constitution amendment under §13 if the validator encodes a new rule (not just a new mechanical check for an existing rule).
5. Run the validator in "warn-only" mode for one full Wave; promote to "reject" after one clean Wave.

---

## How to retire a validator

1. Move its file to `core/governance/Validators/retired/`.
2. Remove its row from the table above; add a row to the "Retired" table below.
3. Record the reason — typically "rule absorbed into a broader validator" or "rule no longer applies after Constitution amendment X".

---

## Retired validators

| Validator | Retired on | Why | Replacement |
|---|---|---|---|
| | YYYY-MM-DD | | |

*(None yet — v1.0 is the first locked set.)*
