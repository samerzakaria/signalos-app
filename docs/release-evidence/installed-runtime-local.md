# SignalOS Installed Runtime Evidence

Date: 2026-05-14T16:10:57

Runtime source: NSIS installed package

Project: `%TEMP%\signalos-installed-runtime-*\next-project`

## Passed Checks

- NSIS installer-only install: Installed to a temp folder outside the repo.
- Bundled engine startup: JSON IPC startup response received.
- Engine ping: Sidecar responded with pong.
- Project setup: /signal-init created runtime state, plan, and command library.
- Secret redaction: Variable names are reported and values stay hidden.
- Project status: /signal-status returned a next action.
- Notes and Brain: Note was saved and found through the bundled engine.
- Gate status: Six governance gates returned through the bundled engine.
- NSIS uninstall: Silent uninstall completed.
