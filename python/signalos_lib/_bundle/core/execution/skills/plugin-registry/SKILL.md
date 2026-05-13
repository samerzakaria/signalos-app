<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.3 — plugin-registry skill (AMD-CORE-006). -->

---
name: plugin-registry
description: "Apply plugin registry signing, namespace, compatibility, and trust-tier rules."
owner: PE
trust_tier:
  installer: T3
  sandbox:   T3
  list:      T2
  verify:    T2
amendment: AMD-CORE-006
wave: W1.3
---

# Skill: plugin-registry

Load this skill whenever the caller is installing, publishing,
re-verifying, or uninstalling a SignalOS plugin, or when they need to
reason about the namespace/trust-tier/cosign invariants that govern
the registry.

## What a plugin is

A **SignalOS plugin** is a cosign-signed gzipped tarball carrying a
fixed-shape `manifest.json` plus a payload directory matching one of
five categories (`skill`, `command`, `emitter`, `hook`, `overlay`).
The manifest is validated against
`core/registry/_schema/plugin-manifest.schema.json`. See the bottom of
this file for the manifest cheatsheet.

## Install flow

```bash
signalos install path/to/@signalos-foo-1.0.0.tar.gz
```

1. Installer looks for `<tarball>.sig` alongside the tarball.
2. Cosign verifies the signature. Unsigned or cosign-refused tarballs
   exit **3** unless `--allow-unsigned` is passed (which requires a
   co-signed Amendment; see below).
3. Manifest is validated: namespace, semver, type, entry_points,
   signature shape, optional dependencies / author / license.
4. `compat.signalos_core` is matched against the current Core version
   from `plugin.json`. Incompatibility exits **5**.
5. The tarball is extracted into `core/registry/<ns>/<name>/<version>/`
   via an atomic staging-then-rename.
6. One `plugin-install` row is appended to
   `.signalos/AUDIT_TRAIL.jsonl`. **The row always says
   `trust_tier: "T3"`.**

## Verify flow

```bash
signalos verify
```

Walks `core/registry/` and re-runs cosign against every cached
`.signature` file. Returns one row per package with
`{plugin_id, version, ok, reason}`. Exits 0 if all ok, 2 if any
refused. Trust Tier: **T2** — read-only.

## Publish flow

```bash
signalos publish ./my-plugin-src --out ./dist --key ./cosign.key
```

Bundles the directory as a tarball, validates the manifest, and —
if a `--key` is given — shells out to `cosign sign-blob`. In
`SIGNALOS_REGISTRY_TEST=1` mode a mock `.sig` file containing
`MOCK-COSIGN-SIG` is produced instead. Returns the absolute path to
the tarball.

## Uninstall flow

```bash
signalos uninstall @signalos/foo@1.0.0
```

Removes the install directory and appends a `plugin-uninstall` row to
the audit trail. Trust Tier for the uninstall event: **T3**. No
"demotion" is available — a package that was installed at T3 cannot
be uninstalled at a lower tier.

## Cosign contract

- Algorithm: `cosign` (sigstore). Fixed in the schema.
- Signature ref: `sha256:<64 hex>` over the tarball bytes.
- Shell-out is the **only** verification path in real mode:
  `cosign verify-blob --key <pubkey> --signature <sig> <tarball>`.
- No cosign binary on PATH → install refused with a clear error and
  a pointer to the sigstore install instructions.

## `SIGNALOS_REGISTRY_TEST=1` mode

Setting `SIGNALOS_REGISTRY_TEST=1` in the environment short-circuits
the cosign shell-out. A signature file is trusted **iff** it exists
and contains the literal marker string `MOCK-COSIGN-SIG`. Everything
else is refused. This is the exact same pattern as
`SIGNALOS_HARNESS_TEST=1` for the Anthropic SDK, and it lets the four
W1.3 proof scenarios (40–43) run on any POSIX box without installing
the cosign binary.

The mode is meant for CI and proof scenarios only. Production
deployments never set the variable; audit pipelines treat a set
variable as an install-time anomaly.

## T3-default invariant

Every installed plugin runs at Trust Tier **T3**. The manifest's
`trust_tier_default` field is advisory — the audit trail always
records T3 regardless of what the manifest says. Promotion to T2 or
T1 requires a co-signed Amendment (PO + PE + Security) in
`.signalos/AMENDMENTS.md`. The installer has no code path that writes
any other tier to the audit trail.

This is the last line of defence against a supply-chain compromise:
even if a malicious manifest declares `trust_tier_default: "T1"`, the
package still boots into T3 and cannot reach T1-only surfaces
(Constitution updates, Gate overrides, journal rewrites, session
archive). See proof scenario `42_registry_t3_default.sh`.

## Namespaces

| Namespace | Status | Who can publish |
|---|---|---|
| `@signalos/*` | Reserved | The SignalOS Core team (identity pinned in SBOM). |
| `community/*` | Open | Anyone with a cosign keypair. |
| anything else | Refused at install | — |

Refusal at install is exit code 4 (`RegistryNamespaceError`).

## `--allow-unsigned` (escape hatch)

`signalos install --allow-unsigned <tarball>` installs an unsigned or
cosign-refused tarball. The installer tags the audit row with
`unsigned: true` and proceeds. There is **no code-level check** that
an Amendment exists — the policy lives in
`core/governance/Retro/AMENDMENTS.md` and is enforced by review, not
by the installer. Operators wanting to police unsigned installs at
runtime grep the audit trail for `unsigned: true`.

## Manifest cheatsheet

```json
{
  "name": "@signalos/foo",
  "version": "1.0.0",
  "type": "skill",
  "compat": { "signalos_core": ">=1.3.0 <2.0.0" },
  "entry_points": { "skill": "SKILL.md" },
  "signature": { "algo": "cosign", "ref": "sha256:…" },
  "trust_tier_default": "T3",
  "dependencies": [
    { "name": "@signalos/observability", "version": "^1.3.0" }
  ],
  "author": { "name": "…", "url": "…" },
  "license": "MIT"
}
```

Required: `name`, `version`, `type`, `compat`, `entry_points`,
`signature`. Optional: `trust_tier_default`, `dependencies`,
`author`, `license`. Validation is stdlib-only (see
`cli/signalos_lib/registry.validate_manifest`).

## Pitfalls

- **Forgetting the `.sig` file.** The installer looks for
  `<tarball>.sig` alongside the tarball; any other naming (e.g.
  `.tar.gz.signature`) is treated as unsigned.
- **Installing as non-owner.** The install tree lives under
  `core/registry/` which is tracked by git; run the installer as a
  user who can write to the repo.
- **Treating test-mode as production.** A repo whose CI leaks
  `SIGNALOS_REGISTRY_TEST=1` into a production build will accept
  tarballs with a `MOCK-COSIGN-SIG` file and trust them. The
  observability dashboard should alert on the variable being set
  outside `proof/` paths.

## Related

- `core/execution/commands/signalos-install.md` — command spec for
  `signalos install`.
- `core/execution/commands/signalos-publish.md` — command spec for
  `signalos publish`.
- `core/execution/commands/signalos-verify.md` — command spec for
  `signalos verify`.
- `core/registry/_schema/plugin-manifest.schema.json` — normative
  manifest schema.
- `core/registry/README.md` — on-disk layout reference.

## Prior art

The registry concept (manifest shape, signing-first install,
namespace gate) is borrowed from `a5c-ai/babysitter` (MIT). No source
code copied; the SignalOS implementation is Python + POSIX shell.
