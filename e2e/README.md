# Foundry user-journey E2E

The layer that was missing. Every UI *unit* test mocks the Tauri bridge, so
nothing ever walked the real cockpit — and journey-breaking bugs shipped
"green" (chat wiped on tab switch #50, the folder picker not registering #51,
the model-fetch race #52). This harness drives the **actual built UI** through
the real journey and asserts each stage, so a dead button / stuck onboarding /
silent failure can't reach a release again.

## What `user-journey.smoke.mjs` covers

Launch → shell renders (no blank, no console errors) → onboarding (provider +
key + projects-root) → **Seal & start** → the app boots (`#app` visible) →
**every major view renders** (build / dashboard / vault / settings / preview,
no dead views, no console errors) → **#53 build-with-no-workspace guard** (a
delivery intent with no active project surfaces the *New Project* guidance, not
a raw "No workspace selected") → no console errors surfaced during the whole
walk. It serves the built `dist` and injects a faithful `window.__TAURI__` mock
(the app runs with `withGlobalTauri: true`), so it exercises the packaged-shape
UI, not a dev mock.

## Run it

```sh
npm run build           # produce ./dist first
npm run e2e:journey     # === node e2e/user-journey.smoke.mjs
```

Exit 0 = journey passes; exit 1 = a stage broke (prints which). Playwright is a
declared devDependency; if the chromium binary is missing, run
`npx playwright install chromium` (or point `PLAYWRIGHT_BROWSERS_PATH` at a
shared cache).

## Enforced in CI

Wired into `test-automation.yml` as the **`L1 user-journey E2E (cockpit gate)`**
job (ubuntu, installs chromium, builds, runs `npm run e2e:journey`). It runs on
every push/PR, so a dead button / stuck onboarding / silent journey break can't
reach a release green again.

## Roadmap

- Assert the **sidecar handshake** (a real `sidecar:ready`/response reaches the
  UI) rather than a benign mock — the one integration a mock can't prove.
- Drive a **real chat turn** across a tab switch and assert history survives
  (the #50 regression, currently guarded by unit tests + the view sweep here).
- Graduate to **tauri-driver** to drive the real packaged app (Rust + real
  sidecar + webview) end-to-end.
