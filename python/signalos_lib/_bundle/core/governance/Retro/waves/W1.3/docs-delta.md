<!-- SignalOS Core v1.3 - W1.3 docs delta. Append-only during the Wave. -->

# W1.3 - Docs delta

`Canonical path: core/governance/Retro/waves/W1.3/docs-delta.md · Authored by: PE · Signed by: PO + PE at Wave close`

Everything documentation-shaped that the context compression + plugin registry bundle changes across the SignalOS Core distro.

## Files touched by W1.3

| Path | Change | State | Why |
|---|---|---|---|
| `core/README.md` | **extend** - "Long sessions" and "Installing a plugin" sections. | done | AMD-CORE-005,006. |
| `core/governance/Governance/CONSTITUTION.md` | **append** - section 13 glossary entries for `rule-based compression`, `disk-truth unchanged invariant`, `plugin registry`, `cosign-signed tarball`, `T3-by-default for plugins`. | done | AMD-CORE-005,006 vocabulary. |
| `core/execution/skills/compress-context/SKILL.md` | **new** - 4-layer rule: verbatim/summary/headline/discard. Disk-truth untouched. | done | AMD-CORE-005. |
| `core/execution/skills/plugin-registry/SKILL.md` | **new** - install / verify / publish / uninstall. Cosign contract, T3 default, manifest schema link. | done | AMD-CORE-006. |
| `core/execution/commands/context-expand.md` | **new** - command doc. | done | AMD-CORE-005. |
| `core/execution/commands/signalos-install.md` | **new** - command doc. | done | AMD-CORE-006. |
| `core/execution/commands/signalos-publish.md` | **new** - command doc. | done | AMD-CORE-006. |
| `core/execution/commands/signalos-verify.md` | **new** - command doc. | done | AMD-CORE-006. |
| `core/registry/_schema/plugin-manifest.schema.json` | **new** - JSON Schema for plugin manifests. | done | AMD-CORE-006. |
| `core/registry/README.md` | **new** - registry layout, how a plugin is installed on disk. | done | AMD-CORE-006. |
| `core/tool-adapters/_shared/commands.json` | **extend** - register `signalos-install`, `signalos-publish`, `signalos-verify`, `context-expand`. | done (in W1.1 foundation pass) | AMD-CORE-005,006. |
| `core/tool-adapters/_shared/skills.json` | **extend** - register `compress-context`, `plugin-registry`. | done (in W1.1 foundation pass) | AMD-CORE-005,006. |
| `SBOM.md` | **append** - `## W1.3 (SignalOS Core 1.3.0)` section; pinned `cosign` binary version + install channel; zero new Python dep asserted. | done | Gate 5. |
| `docs/CHANGELOG.md` | **append** - `## 1.3.0 - 2026-04-23` entry. | done | Gate 5. |
| `core/governance/Retro/AMENDMENTS.md` | **append** - AMD-CORE-005 + AMD-CORE-006 rows with measured hash anchors on `cli/signalos_lib/context.py` and `cli/signalos_lib/registry.py`; W1.3 hash-anchor note appended. | done | AMD contract. |
| `UPGRADE-1.0.x-to-1.3.md` | **new** - migration path from v1.0.3 to v1.3 for existing products; section 6 rollback recipe verified by `proof/scenarios/45_upgrade_rollback.sh`. | done | Plan section 12 rollout sequence. |
| `plugin.json` + `.claude-plugin/plugin.json` | **bump** - `1.1.0` -> `1.3.0` so `proof/scenarios/42_core_readme_version_match.sh` remains green after the `core/README.md` "Core version:" line was bumped to `1.3.0`. | done | Release hygiene. |
| `core/governance/Retro/waves/W1.3/METRICS.md` | **new** - W1.3 measurements recorded at Wave close. | done / signed | Gate 5. |
| `core/governance/Retro/waves/W1.3/WAVE_REVIEW.md` | **new** - narrative Wave review with "what shipped / what almost didn't / learnings" sections; PO + PE closeout signed under distinct UserIds. | done / signed | Gate 5. |
| `proof/scenarios/45_upgrade_rollback.sh` | **new** - asserts section 6 rollback recipe is present in the UPGRADE doc and that purging `core/registry/plugins/` + `INSTALLED.jsonl` is safe (no dangling references in `core/`). | done | Gate 5. |

## Definition of done for W1.3 docs

- Every row above is `done`.
- `UPGRADE-1.0.x-to-1.3.md` contains an explicit **rollback** recipe, verified by a proof scenario.
- `core/registry/_schema/plugin-manifest.schema.json` passes `jsonschema --check-schema`.
- The 2 new AMD-CORE rows have measured Constitution hashes filled in.

## Authors

PE-drafted. PO + PE co-sign at W1.3 close.
