<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.3 - W1.3 Wave Review. Filled in at Wave close. -->

# W1.3 - Wave Review

`Canonical path: core/governance/Retro/waves/W1.3/WAVE_REVIEW.md · Filled in by: PO + PE at Wave close · Distinct UserIds required on sign-off (product-Constitution §F.3)`

The W1.3 Wave introduced **rule-based context compression** (AMD-CORE-005) and the **first-party plugin registry** (AMD-CORE-006). This Wave Review is the narrative counterpart to `METRICS.md`: what shipped, what nearly didn't, which amendments became force-of-law, and which learnings carry forward.

## What shipped

- The rule-based context compressor `cli/signalos_lib/context.py` - public API `compress_transcript`, `compress_transcript_to`, `expand_scope`; four-layer rule engine (VERBATIM / SUMMARY / HEADLINE / DISCARD); stdlib-only; `DiskTruthRefused` + `NeverCompressViolation` exceptions on the allowlist boundary. Proved by proof scenarios 36-39.
- The `signalos context ...` CLI surface (`compress` / `expand`) via `cli/signalos_lib/commands/context.py`, wired into the existing `cli/signalos` lazy-routing entry point. Exit-code contract `0 ok / 1 user error / 2 execution error / 3 disk-truth refused`. Command doc at `core/execution/commands/context-expand.md`, skill doc at `core/execution/skills/compress-context/SKILL.md`.
- The plugin registry `cli/signalos_lib/registry.py` - public API `install` / `verify` / `list_installed` / `uninstall` / `publish` / `validate_manifest`; cosign-signed tarball contract with `SIGNALOS_REGISTRY_TEST=1` mock for CI; namespace gate `@signalos/*` | `community/*`; T3-permanent audit invariant. Proved by proof scenarios 40, 41, 43, 44.
- The `signalos install / verify / list / uninstall / publish` CLI surface via `cli/signalos_lib/commands/registry.py`. Exit-code contract `0 ok / 1 user error / 2 registry error / 3 unsigned refused / 4 namespace refused / 5 compat refused`. Three new command docs under `core/execution/commands/signalos-{install,publish,verify}.md`, skill doc at `core/execution/skills/plugin-registry/SKILL.md`.
- The manifest schema `core/registry/_schema/plugin-manifest.schema.json` (JSON Schema draft-07) + registry layout doc `core/registry/README.md`.
- Core-scoped docs: the "Long sessions" and "Installing a plugin" sections in `core/README.md`, the section 13 glossary additions to `core/governance/Governance/CONSTITUTION.md` (five entries), the `UPGRADE-1.0.x-to-1.3.md` migration doc with an explicit rollback recipe.
- Wave-level hygiene: `docs/CHANGELOG.md` 1.3.0 entry, `SBOM.md` section W1.3, `core/governance/Retro/AMENDMENTS.md` AMD-CORE-005 and AMD-CORE-006 rows with measured hash anchors. Both `plugin.json` and `.claude-plugin/plugin.json` bumped to 1.3.0 so scenario 42 remains green.
- Proof scenario 45 - the UPGRADE-doc rollback recipe + purge-safety assertion.

## What almost didn't

- **Stub-placeholder hashes from W1.2 were still load-bearing.** At the start of W1.3, `cli/signalos_lib/context.py` and `cli/signalos_lib/registry.py` existed only as ~20-line stubs (a placeholder left in the W1.2 close commit so the CLI entry point could `import` them without failing). The W1.3 hash anchors for AMD-CORE-005 and AMD-CORE-006 specifically record the transition from those stubs to the full implementations - not `absent -> <hash>`, but `<stub-hash> -> <full-hash>`. The anchor note at the top of the "Core distro amendments" table in `AMENDMENTS.md` records this convention so a future reader does not misread the `86199e6...` / `66bd6ca...` left-hand values as mystery hashes.
- **The disk-truth-unchanged invariant had to be enforced at two boundaries, not one.** The original W1.3 plan enforced the allowlist only in `pre-session-compress.sh`. Scenario 38 was drafted to exercise that path, but a review noticed that any plugin that side-stepped the hook (for example, called `compress_transcript` directly from its own Python code) would defeat the invariant. Fix: `compress_transcript` now raises `DiskTruthRefused` on the same allowlist at the library boundary. Scenario 38 was extended to assert both refusals.
- **`trust_tier_default` in the manifest was almost an escape hatch.** An early draft of the registry wrote the manifest's `trust_tier_default` into the `AUDIT_TRAIL.jsonl` row directly. That would have let a malicious plugin advertise `trust_tier_default: "T1"` and short-circuit the section 2 fail-hard default without a product-Constitution declaration. Fix: AUDIT row always records `trust_tier: "T3"` regardless of the manifest. Scenario 43 asserts this.
- **`cosign` almost became a Python dep.** The `sigstore-python` package exposes a subset of the cosign surface but does not yet implement `verify-blob` against KMS key references. An early W1.3 draft pinned `sigstore` in `cli/requirements.txt`. Fix: shell out to the cosign Go binary (narrow, two functions), and document the out-of-band install path in `UPGRADE-1.0.x-to-1.3.md` section 3. Scenario 34 remains green; W1.3 adds zero new Python deps.
- **The compat gate needed its own refusal exit code.** The first draft used exit code 2 (generic registry error) for both corrupted-manifest and compat-mismatch. A semver version being out of range is a user-actionable condition, not a corruption; fix was to add exit code 5 exclusively for compat refusal so a CI system can distinguish "this tarball is broken" from "this tarball targets a different Core". Scenario 44 asserts the code.

## Amendments ratified

| AMD | Title | Hash anchor (measured) | PO | PE | Ratification Gate | Date of force |
|---|---|---|---|---|---|---|
| AMD-CORE-005 | Rule-based context compression | `cli/signalos_lib/context.py` sha256 `d396b06839e3d9ff7a31f10a3cac610bdc5f5afadb678391d6cf0af352478d60` | Samer Zakaria | Mohammed Shaban | W1.3 Gate 1 | 2026-04-23 |
| AMD-CORE-006 | Plugin registry (cosign-signed tarballs, T3-by-default) | `cli/signalos_lib/registry.py` sha256 `0afe80affc91be39506cc248e44bfb8ff5802d530d91f41e2a980a4f715627dc` | Samer Zakaria | Mohammed Shaban | W1.3 Gate 1 | 2026-04-23 |

See `core/governance/Retro/AMENDMENTS.md` for the canonical rows; the measured hashes in that file match the anchors above.

## Learnings that flow into W1.4 and beyond

- **"Stub anchor -> full anchor" is a first-class pattern.** The W1.3 anchors are not `absent -> <hash>`, they are `<stub-hash> -> <full-hash>`. Future Waves that land a library whose skeleton shipped in an earlier Wave should follow the same pattern and annotate the `Core distro amendments` hash-anchor note accordingly. -> W1.4.
- **Every new invariant needs to be enforced at two boundaries minimum.** The disk-truth-unchanged fix above generalised: a hook alone is not enough because a plugin can always call the underlying library directly. The general rule is "enforce the invariant at the narrowest surface you control plus the hook" - the library itself, plus any shell-script enforcer that runs in the hook slot. -> Amendment process §K.
- **Exit-code maps should be per-verb, not per-command-family.** The W1.3 registry added a 6-way exit-code map (`0/1/2/3/4/5`) because install, publish, and verify share the same binary but have different failure modes. Future verb-families should budget for one exit code per refusal mode at design time, not after the fact. -> W1.4.
- **Shell-out budgets matter.** The cosign decision made W1.3 add one external binary and zero Python deps. This is an operator-friendly outcome on Linux and macOS, but Windows adopters see a new install step. The UPGRADE doc section 3 handles it, but future Waves that consider shell-outs should measure the Windows ergonomic cost up front. -> W1.4.
- **Wave-close "regression drill" concept from W1.2 still open.** The W1.2 learning was that W1.1 hygiene debt surfaced late; W1.3 did run every prior-Wave scenario from the current tip and caught no new drift, so the discipline is now in place. Codify it as a `run_proof.sh` flag (`--all-waves`) in W1.4 so it is a single invocation. -> W1.4.

## Sign-off (PO + PE distinct UserId)

| Role | Name | UserId | Date | Signature (SHA-256 of this file at sign-off) |
|---|---|---|---|---|
| PO | Samer Zakaria | Samer Zakaria | 2026-04-23 | `2d56cedacb5f1104ab91277dd35fb0fcf14b3a599eebe8e2101ff0237a3dd650` |
| PE | Mohammed Shaban | Mohammed Shaban | 2026-04-23 | `2d56cedacb5f1104ab91277dd35fb0fcf14b3a599eebe8e2101ff0237a3dd650` |

## Fill-in ritual

At Wave close the PE re-runs scenarios 18-45 plus 99 on the user's real Windows workstation, records the close values in `METRICS.md` and this file, and both PO and PE co-sign under distinct UserIds. W1.3 is now fully recorded and signed.
