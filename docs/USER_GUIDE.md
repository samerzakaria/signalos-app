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
8. Open Build.
9. Describe the app you want.
10. Press Build app.
11. Open `index.html` from the Build result.
12. Use Project if you also want SignalOS governance files and `/signal-status`.

## Main Views

- Build: describe an app and write the first working static files into the selected project folder.
- Project: choose the folder, connect AI, check setup, and inspect project files.
- Chat: ask AI questions and run `/signal-*` commands. Build requests are moved to Build instead of returning walls of code.
- Dashboard: see project readiness, AI state, engine state, gates, files, and the first-project checklist.
- Secrets: add or update `.env.local` values without showing the values back in the UI or sending them to AI.
- Settings: manage AI provider, model, saved key state, engine diagnostics, budget, updates, and issue reports.
- History: review the audit timeline and export a team handoff.

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
- Project secrets can be saved from Secrets into `.env.local`, `.env`, or `.env.development`.
- Secret files are summarized by file and variable name, not raw value.
- Do not paste credentials, private keys, database dumps, or `.env` values into chat.

## Build An App

1. Open Build.
2. Write a direct app request, such as `Build a TODO task management app with priorities, due dates, filters, and local storage`.
3. Press Build app.
4. Open `index.html` from the Build result.
5. Refine the prompt and Build again when you want a new version.

The Builder writes plain HTML, CSS, JavaScript, and README files. It does not create `.env` files; use Secrets for secret values.

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

1. Open Project or Settings.
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
