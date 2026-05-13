---
description: "Install a local plugin tarball with signature and manifest enforcement."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.3 — /signalos-install command spec (AMD-CORE-006). -->

# /signalos-install — Install a cosign-signed plugin tarball

Owner: PE + Security. Operational lever — **not a Gate**.

## What it is

`signalos install` is the single code path that materialises a plugin
tarball under `core/registry/<ns>/<name>/<version>/`. It verifies the
tarball's detached cosign signature, validates the manifest against
`core/registry/_schema/plugin-manifest.schema.json`, enforces the
namespace and compat invariants, and appends one row to
`.signalos/AUDIT_TRAIL.jsonl`.

The installer is Python + POSIX shell. Cosign is an **external binary
pin** (see `SBOM.md`), not a Python dependency — it is shelled out to.
In `SIGNALOS_REGISTRY_TEST=1` mode the shell-out is short-circuited
to a deterministic mock so CI does not need the cosign binary.

## What it is NOT

- **Not a network call.** The installer assumes the tarball and its
  `.sig` sidecar are already on local disk. Fetching is the operator's
  job (curl, git fetch, manual copy).
- **Not a Trust Tier upgrade path.** Every installed package lands at
  T3 in the audit trail, full stop. Promotion requires a co-signed
  Amendment.
- **Not a way to run Node.** A plugin may ship Node code inside its
  payload, but that is content — Core's runtime stays Python + shell.
- **Not a free bypass of cosign.** The `--allow-unsigned` flag exists
  for offline bootstrap and test paths; it tags the audit row with
  `unsigned: true` and requires an Amendment out of band.

## CLI surface

```bash
# Happy path — verify, extract, install
signalos install ./dist/@signalos-foo-1.0.0.tar.gz

# Bootstrap / test path — refused signature, tagged in the audit trail
signalos install --allow-unsigned ./unsigned-plugin.tar.gz

# Real-mode signing with a pinned cosign public key
signalos install --key ./cosign.pub ./dist/@signalos-foo-1.0.0.tar.gz
```

The command writes a single JSON blob to stdout on success and exits
with a policy-specific code (see below) on refusal.

## Arguments

| Flag | Required | Purpose |
|---|---|---|
| `<tarball>` | yes | Path to a `.tar.gz` file. The matching signature is looked up at `<tarball>.sig`. |
| `--allow-unsigned` | no | Install without (or despite) a cosign-verified signature. Tags the audit row `unsigned: true`. |
| `--key <ref>` | no | Cosign key reference passed through to `cosign verify-blob --key`. Ignored in `SIGNALOS_REGISTRY_TEST=1` mode. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Install completed. `plugin-install` row appended. |
| 1 | Usage error (bad args). No audit row. |
| 2 | Manifest invalid, tarball unreadable, or IO failure. No audit row. |
| 3 | Signature refused and `--allow-unsigned` was not passed. No audit row. |
| 4 | Namespace refused (name is not `@signalos/*` or `community/*`). No audit row. |
| 5 | Compat range does not match the current Core version in `plugin.json`. No audit row. |

## What it writes to AUDIT_TRAIL

One line, JSON, sha256-sorted keys:

```json
{"action":"plugin-install","plugin_id":"@signalos/foo","version":"1.0.0",
 "sha256":"…","signer":"mock-cosign","trust_tier":"T3","unsigned":false,
 "ts":"2026-04-23T12:00:00Z"}
```

The `trust_tier` field is **always** `"T3"`. The `unsigned` field is
`true` iff `--allow-unsigned` was honoured. The `signer` field is the
cosign-verified signer label (or `"mock-cosign"` in test mode, or
`"no-signature"` / `"cosign-rc=N"` when unsigned was permitted).

## On-disk layout after a successful install

```
core/registry/<ns>/<name>/<version>/
├── manifest.json          # copy of the manifest inside the tarball
├── .signature             # the detached cosign signature (kept for re-verify)
├── .install.json          # {plugin_id, version, sha256, signer, trust_tier, unsigned, installed_at}
└── skill|command|emitter|hook|overlay/ …
```

## Failure modes

- **No `.sig` file next to the tarball.** Exit code 3 unless
  `--allow-unsigned` is passed. In real mode, cosign is never
  invoked (there is nothing to verify). In test mode the mock
  returns `reason="no-signature"`.
- **Signature fails cosign verify-blob.** Exit code 3. Cosign's
  non-zero exit code is surfaced in the error message as
  `cosign-rc=<N>`.
- **Namespace is neither `@signalos/` nor `community/`.** Exit
  code 4 with `RegistryNamespaceError`. No temp files leak.
- **Manifest fails schema validation.** Exit code 2 with a
  multi-line error listing every failed rule. The schema is
  `core/registry/_schema/plugin-manifest.schema.json`.
- **`compat.signalos_core` does not satisfy the current Core
  version.** Exit code 5 with the current version and the refused
  range in the error message.
- **Install dir already exists.** Exit code 2 — we do not silently
  overwrite. `signalos uninstall <id>@<version>` first, then retry.
- **Cosign binary missing (real mode).** Exit code 3 with a
  `cosign-not-on-path` reason. The operator installs cosign and
  retries.

## Prior art

The `signalos install` CLI borrows shape from `a5c-ai/babysitter`'s
`bby install` (MIT). No source code copied; the SignalOS
implementation is Python + POSIX shell.
