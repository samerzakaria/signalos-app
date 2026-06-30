---
description: "Verify pinned external skills against hashes and a license policy (fail-closed)."
---

# skill-lock - Governed, License-Checked Skill Supply Chain

Verifies the declared external skills in `.signalos/skills-lock.json` against
their pinned SHA-256 hashes **and** a license policy. SignalOS enforces, never
advises: any skill whose content hash drifts, or whose license is missing,
unknown, or non-permissive, is REFUSED and the command exits non-zero.

## Usage

```text
signalos skill-lock verify [--repo-root <dir>] [--lock <path>] [--json] [--no-evidence]
signalos skill-lock list   [--repo-root <dir>] [--lock <path>] [--json]
```

`verify` is the default action when no subcommand is given.

## Lockfile

Schema `signalos.skills_lock.v1`, default location `.signalos/skills-lock.json`:

```json
{
  "version": 1,
  "skills": {
    "<id>": {
      "source": "https://github.com/org/repo",
      "source_type": "github",
      "skill_path": ".signalos/skills/<id>/SKILL.md",
      "sha256": "<64 hex of installed skill content>",
      "license": "MIT",
      "license_source": "license-file"
    }
  }
}
```

## License policy

A skill passes only if its license normalizes to one of the permissive SPDX
identifiers: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, Unlicense,
CC0-1.0. An absent or empty license is a refusal, never a pass — undeclared
licenses are never auto-trusted.

## Rules

- `verify` recomputes each installed skill's SHA-256 and compares it to the
  pinned `sha256`; a mismatch is a `hash-mismatch` refusal.
- A missing installed skill is a `missing` refusal.
- A missing/unknown/non-permissive license is a `license-refused` refusal.
- The overall result is OK only when ALL skills pass BOTH checks.
- On pass, an `skill-lock-verified` audit row is appended; on any refusal an
  `skill-lock-blocked` row is appended, and the command exits non-zero.
- Evidence is written to `.signalos/evidence/skills/skills-lock.json` unless
  `--no-evidence` is passed.

The lockfile composes with `signalos integrity-witness`, which watches
`.signalos/skills-lock.json` for tampering when it is present.
