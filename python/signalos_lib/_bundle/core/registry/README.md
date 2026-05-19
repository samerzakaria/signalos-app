<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.3 — Plugin registry layout (AMD-CORE-006). -->

# core/registry/ — installed plugin tree

Owner: PE + Security. Written by `cli/signalos_lib/registry.py`.

`core/registry/` is where the **SignalOS plugin registry** materialises
on-disk after a `signalos install` call. Every subdirectory under this
root corresponds to exactly one installed package at exactly one
version. The registry is append-only in spirit — `signalos uninstall`
is the only path that removes a subtree, and both install and uninstall
emit rows to `.signalos/AUDIT_TRAIL.jsonl`.

## On-disk layout

```
core/registry/
├── _schema/
│   └── plugin-manifest.schema.json    # normative manifest shape (Draft-07)
├── @signalos/
│   └── <name>/
│       └── <version>/
│           ├── manifest.json          # copy of the tarball's manifest
│           ├── .signature             # detached cosign signature
│           ├── .install.json          # installer bookkeeping
│           └── skill|command|emitter|hook|overlay/ …
└── community/
    └── <name>/
        └── <version>/ …
```

`<namespace>` is always `@signalos` or `community` — the two values
accepted by the manifest schema. Any other namespace is refused at
install time with exit code 4 (`RegistryNamespaceError`).

`.install.json` is the installer's private bookkeeping file. It holds
`{plugin_id, version, sha256, signer, trust_tier, unsigned, installed_at}`.
The `trust_tier` field is always `"T3"` — see the invariant below.

## Install flow (happy path)

1. User runs `signalos install <tarball-path>`.
2. Installer looks for a detached signature at `<tarball-path>.sig`.
3. Cosign verifies the signature against the pinned public key (see
   `SBOM.md` for the pin). In `SIGNALOS_REGISTRY_TEST=1` mode the
   verification is short-circuited to a mock that accepts signatures
   containing the literal string `MOCK-COSIGN-SIG`.
4. The tarball is extracted into a temp dir.
5. `manifest.json` is loaded and validated against
   `core/registry/_schema/plugin-manifest.schema.json`. Validation is
   stdlib-only — no `jsonschema` dependency — so adopter Python
   installs do not need to grow a new runtime dep.
6. Namespace is confirmed (`@signalos/*` or `community/*`).
7. `compat.signalos_core` is evaluated against the current Core
   version in `plugin.json`. An unsatisfied range fails with exit
   code 5 (`RegistryCompatError`).
8. The extracted tree is staged (`<dir>.staging`) and atomically
   renamed into place at `core/registry/<ns>/<name>/<version>/`.
9. A row is appended to `.signalos/AUDIT_TRAIL.jsonl`:
   ```json
   {"action":"plugin-install","plugin_id":"@signalos/foo","version":"1.0.0",
    "sha256":"…","signer":"…","trust_tier":"T3","unsigned":false,"ts":"…"}
   ```

## Uninstall flow

1. User runs `signalos uninstall <plugin-id>@<version>`.
2. Installer looks up `core/registry/<ns>/<name>/<version>/` and reads
   `.install.json` for the cached sha256 + signer.
3. The directory is removed (`shutil.rmtree`).
4. A row is appended to `.signalos/AUDIT_TRAIL.jsonl` with
   `action: "plugin-uninstall"`.

The `trust_tier` field in the uninstall row is also `"T3"` — see the
invariant. A plugin cannot be "softened" on the way out.

## Cosign verification contract

- Algorithm: `cosign` (sigstore). Fixed in the manifest schema as
  `signature.algo === "cosign"`.
- Signature ref: `sha256:<64 hex>` — the digest over the tarball
  bytes. Stored in the manifest so a detached `.sig` file is not the
  only source of truth.
- Real-mode shell-out:
  ```bash
  cosign verify-blob \
      --key <pinned-pubkey-path> \
      --signature <tarball>.sig \
      <tarball>
  ```
  Exit code 0 means trusted. Cosign's stdout is ignored; the manifest's
  `signature.ref` is the canonical identity.
- Test-mode shim: `SIGNALOS_REGISTRY_TEST=1` bypasses the cosign
  binary entirely. A signature file containing the literal marker
  `MOCK-COSIGN-SIG` is treated as trusted; any other body is refused.
  This lets proof scenarios 40–43 run on a fresh CI image without
  installing cosign.
- No cosign binary on PATH (in real mode) is an **install-time
  refusal**, not a silent downgrade. The installer surfaces a clear
  error referencing the sigstore release page and the `brew install
  cosign` path for macOS.

## T3-default invariant

Every installed package is recorded with `trust_tier: "T3"` in the
AUDIT_TRAIL — regardless of what the manifest's
`trust_tier_default` field says. The manifest field is advisory only;
it communicates the author's intent, but promotion from T3 to T2 or
T1 requires a co-signed Amendment landing at
`.signalos/AMENDMENTS.md`.

The installer has **no code path** that writes anything other than
`"T3"` to the audit trail. A malicious tarball claiming
`trust_tier_default: "T1"` installs exactly like a tarball with
`"T3"` would — see proof scenario `42_registry_t3_default.sh`.

## The `--allow-unsigned` escape hatch

`signalos install --allow-unsigned <tarball>` lets a caller install a
tarball whose signature is absent, malformed, or cosign-refused. This
exists for offline-bootstrap and test scenarios; it is **not** a
sanctioned production path.

Requirements for using the flag in production:

1. A co-signed Amendment must exist at `.signalos/AMENDMENTS.md`
   naming the specific plugin+version and the reason for bypass.
   Co-signers: PO, PE, Security.
2. The installer tags the audit row `unsigned: true`. Operators can
   filter for unsigned installs with `jq`.
3. The Amendment must be ratified before the CI gate for the Wave
   that introduces the bypass. Gates 1 and 5 both check for
   unexpected unsigned installs.

**The current installer does not enforce (1)** — the Amendment check
is defined in the Amendment contract, not in code. The code accepts
`--allow-unsigned` at face value and records `unsigned: true`. This
keeps the policy surface visible in the audit trail without
entangling the installer with Amendment parsing, which belongs to
`core/governance/`.

## Listing and re-verification

- `signalos list` walks `core/registry/` for every `.install.json`
  and prints one row per package. Trust Tier: T2 (read-only).
- `signalos verify` re-runs cosign verification against the cached
  `.signature` files and reports per-package ok/reason. Trust Tier:
  T2. In `SIGNALOS_REGISTRY_TEST=1` mode it checks the mock marker
  only — no cosign binary required.

## Prior art

The registry concept (manifest shape, namespacing, signing-first
install) is borrowed from `a5c-ai/babysitter` (MIT). SignalOS rewrote
the implementation from scratch in Python + POSIX shell; no source
code copied. The babysitter installer is npm-shaped and Node-native;
SignalOS Core stays Python + cosign.
