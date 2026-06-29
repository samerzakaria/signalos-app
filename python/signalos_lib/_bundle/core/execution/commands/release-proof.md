---
description: "Validate technology-neutral release artifact proof."
---

# release-proof - Artifact, Signature, Installer, and Clean-Machine Evidence

Validates a releasable product artifact without assuming .NET, ABP, Node,
Python, or any other product technology. The artifact can be any package or
installer file. Signature, installer, clean-machine, and release-readiness proof
are supplied as evidence files and enforced only when policy requires them.

## Usage

```text
signalos release-proof validate --artifact <path> [--artifact-kind <kind>]
signalos release-proof validate --artifact <path> --signature <path> --require-signature
signalos release-proof validate --artifact <path> --clean-machine-proof <json> --require-clean-machine
signalos release-proof validate --artifact <path> --installer-proof <json> --require-installer-proof
signalos release-proof validate --artifact <path> --readiness-evidence <json> --require-readiness
```

Add `--repo-root <path>` to evaluate a different workspace. Add `--json` for a
machine-readable payload.

## Proof Rules

- The artifact must exist, be a file, be non-empty, and receives a SHA-256
  digest in the evidence payload.
- A signature proof may be an opaque signature file or JSON. JSON signatures can
  include `artifact_sha256`, `artifact_digest`, `sha256`, `digest`, or
  `subject.sha256`; when present, the digest must match the artifact.
- Clean-machine proof must be JSON with a passing status, a non-empty
  `environment`/`machine`/`runner` object, and at least one command/check.
- Installer proof must be JSON with a passing status and at least one
  command/check.
- Release-readiness proof must be a passing `release-readiness.json` payload
  when `--require-readiness` is used.
- Missing optional proof is recorded as not required. Missing required proof is
  a blocker.

## Evidence

Evidence is written to `.signalos/evidence/<wave>/release-proof.json`.
The default wave folder is `release-proof`.
