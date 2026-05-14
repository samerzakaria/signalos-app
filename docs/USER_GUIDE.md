# SignalOS App User Guide

Date: 2026-05-14

This guide is for a user who has only the installed SignalOS App and a project folder. It does not assume access to the `signalos-app` repository or the SignalOS mother repository.

## What You Need

- SignalOS App installed for your operating system.
- A writable project folder.
- An AI provider key, or Ollama running locally with a pulled model.
- Network access for cloud AI providers.

## First Run

1. Open SignalOS.
2. Choose the project folder you want SignalOS to guide.
3. Select an AI provider.
4. Paste the API key once, or select Ollama for local AI.
5. Fetch models.
6. Select a fetched model, or choose Other model and type one.
7. Save and test the AI connection.
8. Run `/signal-init`.
9. Confirm the Project result panel shows `.signalos/` and `core/strategy/PLAN.md`.
10. Run `/signal-status`.

## Main Views

- Chat: ask AI questions and run `/signal-*` commands.
- Dashboard: see project readiness, AI state, engine state, gates, files, and the first-project checklist.
- Notes: save and search decisions, assumptions, QA evidence, and session notes.
- History: review the audit timeline and export a team handoff.
- Settings: manage project folder, AI provider, model, saved key state, engine diagnostics, secrets, budget, updates, and issue reports.
- Guide: apply templates, read workflow recipes, and review local privacy mode.

## Commands To Start With

```text
/signal-init
/signal-status
/signal-brain
```

Command labels matter:

- Ready commands are normal installed-app commands.
- Advanced commands call deeper SignalOS CLI behavior and may require careful review.
- Preview commands show a command brief and are not a full guided workflow.

## AI Keys And Secrets

- API keys are stored in the operating system credential manager.
- Raw API key values are not shown after saving.
- Secret files are summarized by file and variable name, not raw value.
- Do not paste credentials, private keys, database dumps, or `.env` values into chat.

## Local Privacy Mode

Use Ollama if you want local-only drafting:

1. Install and start Ollama.
2. Pull a model in Ollama.
3. Select Ollama in SignalOS.
4. Fetch models or type the local model name.
5. Save and test.

Local privacy mode still depends on what you type or attach. Avoid adding secrets to chat.

## Reports

SignalOS can write reports inside the selected project:

- Issue reports: `.signalos/issue-reports/`
- Team handoffs: `.signalos/handoffs/`

Reports include project state, recent activity, engine status, AI provider state, and redacted diagnostics. They do not include raw API keys.

## Troubleshooting

If AI is not connected:

1. Open Settings.
2. Fetch models.
3. Select a model.
4. Save and test AI.

If setup is unclear:

1. Run `/signal-status`.
2. Open Dashboard.
3. Inspect Project files.
4. Confirm `.signalos/` and `core/strategy/PLAN.md` are found.

If the engine fails:

1. Open Settings.
2. Test engine.
3. Restart engine.
4. Export an issue report if it still fails.

If updates are unclear:

1. Open Settings.
2. Choose beta or stable.
3. Check updates.
4. Treat unsigned local builds as update-check smoke tests only.
