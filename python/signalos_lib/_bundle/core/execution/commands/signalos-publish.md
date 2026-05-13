---
description: "Bundle a plugin package and optionally sign the tarball with cosign."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.3 — /signalos-publish command spec (AMD-CORE-006). -->

# /signalos-publish — Bundle and sign a plugin

Owner: PE. Operational lever — **not a Gate**.

## What it is

`signalos publish` packages a directory containing a `manifest.json`
plus payload into a `.tar.gz` and — if a `--key` is supplied — signs
it with cosign. The output is a single tarball (plus, in test mode or
when `--key` is given, a detached `.sig` sidecar) ready to hand to
`signalos install`.

The command does **not** upload anything. Registry hosting for
v1.3.0 is a static `index.json` in a GitHub Release (see
`CORE_BABYSITTER_INTEGRATION_PLAN.md` §5.4); publishing the tarball
into that release is an operator task outside the `signalos` CLI.

## What it is NOT

- **Not a way to mint a trust tier.** The manifest's
  `trust_tier_default` field is advisory only — the installer
  records every install at T3 regardless.
- **Not a network call.** The tarball is written to `--out`; the
  adopter ships it through whatever transport they prefer.
- **Not a refusal path for bad manifests.** It refuses loudly. The
  manifest is validated against the same schema the installer uses.

## CLI surface

```bash
# Test-mode publish (SIGNALOS_REGISTRY_TEST=1) — emits a MOCK-COSIGN-SIG sidecar
SIGNALOS_REGISTRY_TEST=1 signalos publish ./my-plugin --out ./dist

# Real-mode publish — shells out to `cosign sign-blob`
signalos publish ./my-plugin --out ./dist --key ./cosign.key

# No signing — bundle only; operator signs later out-of-band
signalos publish ./my-plugin --out ./dist
```

## Arguments

| Flag | Required | Purpose |
|---|---|---|
| `<package-dir>` | yes | Directory containing `manifest.json` at the root. |
| `--out <dir>` | no | Output directory (default: cwd). Created if missing. |
| `--key <ref>` | no | Cosign key reference. When present and real-mode, shells out to `cosign sign-blob --key <ref>`. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Tarball written. Path returned on stdout as `{"tarball": "…"}`. |
| 1 | Usage error. |
| 2 | Manifest invalid, package dir missing, tarball write failure, or cosign sign-blob non-zero. |
| 4 | Namespace refused. |

`signalos publish` never writes to `.signalos/AUDIT_TRAIL.jsonl`.
Publishing is a build-time action; the audit trail is for
install/uninstall events on the adopter side.

## Output filename

```
<namespace-flattened>-<name>-<version>.tar.gz
```

Example: `@signalos/foo` at `1.0.0` → `signalos-foo-1.0.0.tar.gz`.
Example: `community/bar` at `0.2.1` → `community-bar-0.2.1.tar.gz`.

The `.sig` sidecar — when produced — lives at
`<tarball>.sig` (i.e. the exact path the installer looks for).

## Failure modes

- **`manifest.json` missing in the package dir.** Exit code 2 with
  `RegistryManifestError`.
- **Manifest fails schema validation.** Exit code 2 with the full
  error list.
- **Namespace in the manifest is not `@signalos/*` or
  `community/*`.** Exit code 4.
- **`--key` supplied but cosign is not on PATH.** Exit code 2 with a
  pointer to the sigstore install docs.
- **`cosign sign-blob` returns non-zero.** Exit code 2 with the
  cosign return code and stderr surfaced in the error.

## What it writes to AUDIT_TRAIL

Nothing. Publishing does not touch the audit trail. Install / uninstall
events on the adopter side are the sole audit surfaces for the
registry.

## Prior art

The `signalos publish` CLI borrows shape from `a5c-ai/babysitter`'s
`bby publish` (MIT). No source code copied; the SignalOS
implementation is Python + POSIX shell.
