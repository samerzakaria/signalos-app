---
description: "Check or create the governance integrity witness."
---

# integrity-witness - Governance Integrity Witness

Creates or checks `.signalos/INTEGRITY_WITNESS.yaml`, a human-approved hash
witness for governance artifacts and execution hooks.

## Usage

```text
signalos integrity-witness --init --actor <human> --role <role>
signalos integrity-witness --refresh --actor <human> --role <role>
signalos integrity-witness [--repo-root <path>] [--json]
```

## Behavior

- Init/refresh requires a human actor and role.
- Actor names that look like agents, bots, or automation are refused.
- The witness records `path + sha256` for governance artifacts and hooks.
- Check mode fails when the witness is missing, a witnessed file changed, a
  witnessed file disappeared, or a new watched file appears.
- Init/refresh appends `integrity-witness-init` or
  `integrity-witness-refresh` to `.signalos/AUDIT_TRAIL.jsonl`.
- Exit `0` means the witness is valid or was written; exit `1` means drift;
  exit `2` means bad arguments.

## Command Name

The app keeps `signalos verify` for plugin registry verification. Use
`signalos integrity-witness` for the SignalOS.NET-style governance witness
concept so both behaviors remain available.
