# Vendored SignalOS Core

This app vendors the packaged Core runtime under `python/signalos_lib` so the
desktop release can be built from `samerzakaria/signalos-app` alone.

Source snapshot:

- Repository: `https://github.com/samerzakaria/SignalOS`
- Commit: `43f7e116e72403182c49a4ab4a3149a4d0131348`
- Package: `signalos-core`
- Version: `2.17.0b3`

The release workflows must not checkout a separate private Core repository.
Refresh this vendored package intentionally when Core changes are ready to ship
inside the desktop app.
