<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Meta — Execution Pillar Support Files

`Folder: core/execution/meta/ · Maintained by: PE`

This folder contains support files for the Execution Pillar — context documents, extension guidelines, and internal tooling that is not part of the runtime agent/command/skill surface.

## Contents

| File | Purpose |
|---|---|
| `CLAUDE.md` | Context file loaded by Claude Code sessions operating inside this pillar |
| `AGENTS.md` | Guidelines for extending or modifying the agent swarm |
| `README-source.md` | This file — folder overview |

## Not part of the public API

Files in `Meta/` are internal support. They do not ship as skills, commands, or agent prompts. They are not referenced in `plugin.json` and are not surfaced to end users.

## Authors

Mohammed Shaban & Samer Zakaria
