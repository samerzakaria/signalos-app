# SignalOS Clean-Machine Validation

Date: 2026-05-14

This checklist proves the installed-user path. It must be run on a clean machine or clean VM, not this development checkout.

## Machine Requirements

- No `signalos-app` source repository.
- No SignalOS mother repository.
- No Python requirement for the user path.
- No Rust, Node, Cargo, or Tauri requirement for the user path.
- Network access for cloud AI provider tests.

## Test Steps

Before this clean-machine test, the development machine should have passed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -SmokeInstalledBuild -InstallNsisSmoke
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1 -InstalledRuntimeSmoke
```

If the smoke command reports that SignalOS is already running, close SignalOS and retry.

The installer-only runtime smoke proves the bundled engine can set up and inspect a fresh project outside the source repo. This clean-machine checklist still exists because it proves the actual installed UI journey on a separate machine.

1. Install SignalOS from NSIS or MSI.
2. Launch SignalOS from the installed shortcut/menu.
3. Confirm the screen scrolls.
4. Choose a fresh writable project folder.
5. Connect AI.
6. Fetch models.
7. Select a fetched model.
8. Send a plain chat message.
9. Run `/signal-init`.
10. Confirm `.signalos/` exists.
11. Confirm `core/strategy/PLAN.md` exists.
12. Confirm Project result shows created files.
13. Run `/signal-status`.
14. Open Dashboard.
15. Confirm first-project checklist updates.
16. Save a Note.
17. Sign a gate if available.
18. Export an issue report.
19. Export a team handoff.
20. Open Settings.
21. Test engine.
22. Restart engine.
23. Delete or replace saved key.
24. Restart SignalOS.
25. Confirm project/provider/model state persists.
26. Confirm raw key stays hidden.
27. Run update check.
28. Upgrade over the installed build when another build is available.
29. Uninstall normally.

## Pass Criteria

- No source repo is needed.
- No terminal is needed.
- Chat is obvious.
- Setup shows what changed.
- Settings is operational.
- Secrets stay hidden.
- Reports export inside the project.
- State persists after restart.
- Uninstall works.
