# Foundry user-journey E2E

The layer that was missing. Every UI *unit* test mocks the Tauri bridge, so
nothing ever walked the real cockpit — and journey-breaking bugs shipped
"green" (chat wiped on tab switch #50, the folder picker not registering #51,
the model-fetch race #52). This harness drives the **actual built UI** through
the real journey and asserts each stage, so a dead button / stuck onboarding /
silent failure can't reach a release again.

## What `user-journey.smoke.mjs` covers

Launch → shell renders (no blank, no console errors) → onboarding (provider +
key + projects-root) → **Seal & start** → the app boots (`#app` visible) → no
console errors surfaced during the whole walk. It serves the built `dist` and
injects a faithful `window.__TAURI__` mock (the app runs with
`withGlobalTauri: true`), so it exercises the packaged-shape UI, not a dev
mock.

## Run it

```sh
npm run build           # produce ./dist first
node e2e/user-journey.smoke.mjs
```

Exit 0 = journey passes; exit 1 = a stage broke (prints which).

## Setup note (follow-up)

Playwright is **not yet a declared devDependency** — this harness was authored
against a locally-available Playwright + shared chromium cache. To wire it into
CI:

1. `npm i -D playwright` (and `npx playwright install chromium`, or point
   `PLAYWRIGHT_BROWSERS_PATH` at a shared cache).
2. Add an `e2e:journey` script and run it after `build` in the frontend CI job.

## Roadmap

- Assert the **sidecar handshake** (a real `sidecar:ready`/response reaches the
  UI) rather than a benign mock — the one integration a mock can't prove.
- Drive **tab navigation** (build → dashboard → build) and assert chat history
  survives (the #50 regression, currently guarded by unit tests).
- Graduate to **tauri-driver** to drive the real packaged app (Rust + real
  sidecar + webview) end-to-end.
