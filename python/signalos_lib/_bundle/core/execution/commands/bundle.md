---
description: "Inspect or extract the embedded SignalOS framework bundle."
---

# bundle - Embedded Framework Bundle Inspection

Lists and extracts the framework files shipped inside the app bundle.

## Usage

```text
signalos bundle list [--category <name>] [--count]
signalos bundle extract --category <name> --output <dir>
```

## Categories

Known categories are `commands`, `hooks`, `scripts`, `integrations`, `prompts`,
`build`, `quality`, and `journey`.

## Rules

- `list` prints logical bundle paths like `commands/signal-build.md`.
- `list --count` prints one count per known category.
- `extract` copies one category into the requested output directory while
  preserving logical subpaths.
- Unknown categories fail fast.

Use this to inspect what `signalos init` can install into adopter workspaces
without needing a .NET resource extractor or a source checkout of another repo.
