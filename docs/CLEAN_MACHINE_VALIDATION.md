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
8. Choose Other model and confirm manual entry works.
9. Send a plain chat message.
10. Run `/signal-init`.
11. Confirm `.signalos/` exists.
12. Confirm `core/strategy/PLAN.md` exists.
13. Confirm Project result shows created files.
14. Run `/signal-status`.
15. Open Dashboard.
16. Confirm first-project checklist updates.
17. Save a Note.
18. Sign a gate if available.
19. Export an issue report.
20. Export a team handoff.
21. Open Settings.
22. Test engine.
23. Restart engine.
24. Delete or replace saved key.
25. Restart SignalOS.
26. Confirm project/provider/model state persists.
27. Confirm raw key stays hidden.
28. Run update check.
29. Upgrade over the installed build when another build is available.
30. Uninstall normally.

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
