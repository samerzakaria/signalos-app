---
description: "Manage and validate Trust Tier surface declarations."
---

# trust-tier - Surface Tier Lifecycle

Records app-native Trust Tier surfaces and validates touched paths against a
declared session tier.

## Usage

```text
signalos trust-tier surface register --surface-id <path-or-pattern> --tier T1|T2|T3 --justification <reason> [--permanent]
signalos trust-tier surface show --surface-id <path-or-pattern>
signalos trust-tier surface get-by-surface --surface-id <path-or-pattern>
signalos trust-tier surface list [--tier T1|T2|T3] [--tenant-id <id> | --all-tenants]
signalos trust-tier surface promote --surface-id <id> --to T2|T3 --justification <reason>
signalos trust-tier surface demote --surface-id <id> --to T1|T2 --justification <reason>
signalos trust-tier validate --declared-tier T1|T2|T3 --touched <path> [--touched <path> ...]
```

Add `--tenant-id <id>` to any command when a surface classification belongs
to a tenant namespace. Without it, the host namespace is used. The same
`surface-id` may exist once per tenant, but duplicate declarations in the same
tenant fail unless `--force` is supplied.

## Rules

- Every surface declaration requires a non-empty justification.
- Surface ids are limited to 400 characters; justifications are limited to
  1000 characters.
- Permanently-T3 surfaces must start at `T3`.
- Promotion must move upward only.
- Demotion must move downward only.
- Permanently-T3 surfaces cannot be demoted.
- `get-by-surface` returns a nullable lookup result and uses the stable
  `trusttiers:surface:<tenant>:<surface>` lookup key.
- Validation fails when a touched path requires a higher tier than declared.
- Validation fails on unclassified touched paths unless `--allow-unclassified`
  is explicitly supplied.

## Storage

Surface records live under `.signalos/trust-tiers/surfaces/`.
Validation evidence is written under `.signalos/evidence/trust-tiers/`.
