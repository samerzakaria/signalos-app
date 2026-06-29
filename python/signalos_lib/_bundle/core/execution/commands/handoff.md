---
description: "Package operator handoff evidence without deploying."
---

# handoff - Operator Handoff Package

Builds a stack-neutral operator handoff package under `.signalos/handoff/`.
This command records what can be handed to an operator; it never deploys,
publishes, or pushes.

## Usage

```text
signalos handoff [--repo-root <path>] [--live-url <url>] [--release-tag <tag>]
signalos handoff --seeded-demo-data-note <text> --test-evidence <text> --known-limitations <text>
```

Add `--json` for a machine-readable manifest summary.

## Package Files

- `HANDOFF.md`
- `live-url.md`
- `local-run.md`
- `env-requirements.md`
- `seeded-demo-data.md`
- `test-evidence.md`
- `known-limitations.md`
- `audit-gate-summary.md`
- `operator-runbook.md`
- `handoff-manifest.json`

## Rules

- Run instructions come from `.signalos/product/CLOSEOUT.json` when present.
- Release tag defaults to the latest local git tag, then `unreleased-<sha>`,
  then `unreleased-local`.
- Audit summary comes from `.signalos/AUDIT_TRAIL.jsonl`.
- Known limitations are copied from closeout unless explicitly supplied.
- The command appends a `handoff-packaged` audit row after writing the package.
- It is technology-neutral: product stack commands are evidence-derived, not
  assumed from .NET, Node, Python, Go, or any other stack.
