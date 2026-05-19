<!-- Authored for SignalOS desktop app, 2026. -->

---
name: e2e-testing
description: "End-to-end browser verification with Playwright. Unit tests prove logic; this skill proves the UI actually renders + interactive. USE FOR: any task that ships a user-facing screen, route, form, or click handler. DO NOT USE FOR: pure data/logic tasks (use test-generation instead), CSS-only tweaks (visual regression isn't covered)."
license: MIT
---
# End-to-End Browser Testing Skill

## What this enforces

Unit tests can be green while the UI is broken: a hydration error,
an unhandled promise rejection, a CSS rule that overlaps the submit
button. The e2e-testing skill closes that gap.

When a task is tagged `e2e-testing`, the orchestrator:
  1. Spawns the project's dev server (`npm run dev` / `start` / `serve`)
  2. Waits for the localhost URL to come up (60s timeout)
  3. Launches Playwright in headless Chromium
  4. Navigates to the URL, waits for `networkidle`
  5. For each selector in the task description's `Selectors:` line,
     waits up to 5s for it to be visible
  6. Captures: console errors, page errors, failed requests
  7. Fails the task if ANY error / console error / missing selector

## How to tag a task

```yaml
- id: task-007
  title: Add the submit button to the contact form
  description: |
    The form needs a submit button that posts to /api/contact.
    Selectors: button[data-test=submit], input[name=email]
  files:
    - src/components/ContactForm.tsx
  skills:
    - e2e-testing
```

The `Selectors:` line is parsed out of the description; commas separate
multiple CSS selectors. Omit it to just verify the page loads with no
console errors.

## What it can catch

| Failure mode | Caught? |
|---|---|
| Hydration error throws on mount | ✅ pageerror |
| Component imports a missing module | ✅ requestfailed |
| Route returns 404 | ✅ requestfailed |
| Button overlapped by another element | ✅ selector not visible |
| `console.error('...')` somewhere in render | ✅ console.error |
| Dev server failed to start | ✅ port-wait timeout |
| Network request stalls forever | ⚠️  timeout (15s default) |
| Visual regression (pixel diff) | ❌ not implemented |
| Accessibility issues | ❌ not implemented |

## Prerequisites

- `npx playwright` must be installable, OR `@playwright/test` in
  devDependencies. If neither, the skill collapses to advisory (emits
  a warning, doesn't fail the task).
- `package.json` must have a `scripts.dev` / `start` / `serve` entry.
- Headless Chromium needs to install on first run; the AI agent should
  declare a `task-000` to run `npx playwright install chromium` if the
  project doesn't have it yet.
