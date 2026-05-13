---
description: "Re-verify cached installed plugin signatures without mutating registry state."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.3 — /signalos-verify command spec (AMD-CORE-006). -->

# /signalos-verify — Re-verify cached plugin signatures

Owner: PE + Security. Operational lever — **not a Gate**. Trust Tier:
**T2** (read-only — does not mutate `core/registry/` or the audit
trail).

## What it is

`signalos verify` walks `core/registry/` for every installed package,
re-runs cosign verification against the cached `.signature` file, and
prints one JSON row per package to stdout. It is the post-install
audit equivalent of `signalos install`'s signature check — useful for
catching a tampered `.signature` file, a rotated cosign key, or a
package that was installed with `--allow-unsigned` and should now be
re-evaluated.

## What it is NOT

- **Not a mutator.** `verify` never removes a package, rewrites a
  signature, or appends to the audit trail.
- **Not a dependency resolver.** The registry uses flat deps; verify
  only checks cosign on the cached `.signature` — it does not walk
  the `dependencies` array.
- **Not network-bound.** Verification is local: cosign against the
  cached `.signature` file. No fetch.

## CLI surface

```bash
# Real-mode verify — shells out to cosign for every installed package
signalos verify --key ./cosign.pub

# Test-mode verify — checks that every .signature contains MOCK-COSIGN-SIG
SIGNALOS_REGISTRY_TEST=1 signalos verify
```

Output is a JSON array, one object per installed package:

```json
[
  {
    "plugin_id": "@signalos/foo",
    "version": "1.0.0",
    "sha256": "…",
    "trust_tier": "T3",
    "ok": true,
    "reason": "ok"
  },
  {
    "plugin_id": "community/bar",
    "version": "0.2.1",
    "sha256": "…",
    "trust_tier": "T3",
    "ok": false,
    "reason": "cosign-rc=1"
  }
]
```

## Arguments

| Flag | Required | Purpose |
|---|---|---|
| `--key <ref>` | no | Cosign key reference for the shell-out. Ignored in `SIGNALOS_REGISTRY_TEST=1` mode. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every installed package verified cleanly. |
| 1 | Usage error. |
| 2 | One or more packages failed verification. The failing entries appear in the stdout JSON with `ok: false` and a `reason`. |

Trust Tier: **T2**. Verify reads but does not mutate; the audit
trail is not touched. The observability dashboard can treat a `verify`
exit code of 2 as a supply-chain alert.

## What it writes to AUDIT_TRAIL

Nothing. Verify is read-only.

## Failure modes

- **No cosign on PATH in real mode.** Every package returns
  `reason: "cosign-not-on-path"` and the command exits 2. The
  operator installs cosign (sigstore release tarball / brew) and
  retries.
- **Cached `.signature` file missing.** Package returns
  `reason: "no-signature"`. This can happen for packages installed
  with `--allow-unsigned` — the installer still writes `.signature`
  if a file was present at install time, but may skip it if the file
  was absent. Inspect `.install.json` for `unsigned: true` to
  cross-check.
- **Signature cosign-refused.** `reason: "cosign-rc=<N>"` with the
  verify-blob return code.
- **`SIGNALOS_REGISTRY_TEST=1` set in production.** This is an
  environment drift, not a verify bug, but verify will happily
  return `ok: true` on mock signatures. The observability dashboard
  should alert on the variable being set outside proof scenarios.

## Prior art

The `signalos verify` CLI borrows shape from `a5c-ai/babysitter`'s
`bby verify` (MIT). No source code copied; the SignalOS implementation
is Python + POSIX shell.
