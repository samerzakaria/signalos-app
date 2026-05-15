# SignalOS App — Deep End-User Review

- Date: 2026-05-14
- Reviewed version: 0.0.9 (beta) — commits up to `184dde8`
- Reviewer role: end user playing the installer-first journey
- Scope: `signalos-app/` (Tauri shell + JS frontend + Python sidecar). Mother SignalOS Core not in scope.
- Inputs read: every file under [src/](../src/), [src-tauri/src/](../src-tauri/src/), [python/](../python/), plus [tauri.conf.json](../src-tauri/tauri.conf.json), capabilities, manifests.
- Method: source-level audit. No runtime execution. UX inferred from DOM + handlers.

This document is the standing **reference** for what is wrong, weak, or risky in SignalOS App as of v0.0.9 — to be used as the briefing for the next project's design pass.

---

## 0. Executive verdict (one paragraph)

The app's structure is sound: a Tauri 2 shell, an OS-keychain key store, a sandboxed workspace, a stdin/stdout Python sidecar, a 12-provider model layer, and a guided 4-step journey. The product surface, however, is **wider than the supported journey**. Half the sidebar leads to fully implemented panels (Build, Project, Chat, Dashboard, Secrets, Settings, History); the other half (Notes, Help) is reachable only from internal hyperlinks; and most of the 37 slash commands surface as "Preview — brief is available, execution is not wired yet." The first-run flow contains two **silent destructive behaviors** (`signalos init … --force` over a user-picked folder; auto-init inside the Build flow) that contradict the README's "writable project folder" promise. Backend correctness has narrow but real gaps: the audit-trail append is racy, governance state is dead-managed but unused, `test_provider_connection` only tests the model-list endpoint (not actually sending a message), the sidecar IPC is single-threaded so a long command blocks the ping/heartbeat, and updater pubkey is shipped but every release signature is the empty string. None of these are showstoppers in isolation — together they create the impression of a much more finished product than it is, which is exactly what hurts an installer-first first impression.

---

## 1. What the end user actually sees vs. what works

### 1.1 The sidebar promises 7 places, exposes 9, finishes 7

[src/index.html:1147-1155](../src/index.html#L1147-L1155) ships 7 nav buttons: **Build, Project, Chat, Dashboard, Secrets, Settings, History**.

[src/js/app.js:706-716](../src/js/app.js#L706-L716) has title metadata for **9 views**: `guide, project, chat, dashboard, secrets, brain, history, settings, help`.

- The **Notes** view (`view-brain`, [index.html:1418](../src/index.html#L1418)) and **Help** view (`view-help`, [index.html:1609](../src/index.html#L1609)) are fully built — note add/search forms, project templates, workflow recipes, recovery playbook, local-privacy guidance — but **have no sidebar button**. Users only reach them via:
  - Notes: `nextAction()` secondary "Open notes" in the step-4 state, and the brain-search hook from inside the Brain view itself.
  - Help: never. There is no link to `switchView("help")` anywhere in the file. The Help screen is unreachable in the production UI.
- The README, USER_GUIDE, and the SIGNALOS_INSTALLED_APP_REFERENCE_REVIEW all describe a "Notes and Brain search for project memory" as a top-level feature. From the actual UI you cannot get there in one click.

**End-user effect:** users believe the product is smaller than it is and discover Notes/Help only by exporting handoffs or by reading the docs.

### 1.2 The command catalog promises 37, finishes 12

[src/js/app.js:9-48](../src/js/app.js#L9-L48) defines 37 commands. 11 are `ready`, 11 are `advanced`, 15 are `preview`. The `preview` rows render a "Preview" pill in Chat with detail text "Command brief is available; guided execution is not wired yet."

The Python sidecar — [signalos_ipc_server.py:129-183](../python/signalos_ipc_server.py#L129-L183) — agrees: any command not in the explicit `direct` set or `signal-plan/qa/init/status/brain` returns `read_command_spec(command)`, i.e. just dumps the markdown spec for the command. So `/signal-build`, `/signal-design`, `/signal-review`, `/signal-ship`, etc. are visible in the catalog but only print the spec doc.

**End-user effect:** when a beta tester finds 15 of the 37 chips render a wall of markdown instead of doing anything, they conclude the product is unfinished. The fix is either to hide preview commands in this release, or to add a single "Brief only" UI pattern instead of presenting them as first-class commands.

### 1.3 Two slash commands missing from `direct` are in the catalog

`signal-pause` and `signal-pre-wave` etc. appear in [COMMAND_CATALOG](../src/js/app.js#L34-L47) but are not in `dispatch_cli`'s `direct` set ([signalos_ipc_server.py:160-179](../python/signalos_ipc_server.py#L160-L179)). They will fall through to `read_command_spec` and only ever return a brief.

---

## 2. First-run journey: where it fails the installer-first promise

### 2.1 `/signal-init --force` is invoked on whatever folder the user picked

The sidecar maps `/signal-init` with no args to `["init", cwd, "--force"]` at [signalos_ipc_server.py:138](../python/signalos_ipc_server.py#L138).

In [signalos_lib/commands/init.py:112-132](../python/signalos_lib/commands/init.py#L112-L132), `_copy_bundle(target, force=True)` overwrites every file from the packaged `_bundle` (49 command md files, integrations folder, scaffolding) into the user's project folder. The protection set is **3 files**: `.env`, `PLAN.md.signed`, `.signalos/AUDIT_TRAIL.jsonl`. Any other pre-existing file with a name colliding with the bundle (e.g. an existing `README.md`, anything under `core/`, anything under `integrations/`) is silently replaced.

The "is target empty?" check in `init.py:61-71` is **bypassed** because `--force` is always passed.

**End-user effect:** "Choose project folder", then any of:
- the Setup chip in Chat,
- the "Set up project" next action,
- starting a Build,

results in a destructive write across the user's folder with no preview, no confirmation, no diff, no backup. README.md will be replaced with the SignalOS template ([init.py:224-267](../python/signalos_lib/commands/init.py#L224-L267)).

**Severity: High.** This is the worst end-user-facing defect in the product — it violates the "your project folder" promise that the README and USER_GUIDE both make in the first paragraph.

### 2.2 The Build flow auto-runs init silently

[src/js/app.js:2272-2275](../src/js/app.js#L2272-L2275) — inside `prepareSignalOSForBuild`:

```js
if (!state.artifacts?.initialized) {
  prep.initOutput = formatResult(await ipc.signal.runAndWait("/signal-init", []));
  markOnboarding("setup");
}
```

So a user who picks a folder, types "build me a todo app", and presses **Build app**, triggers `/signal-init --force` before the AI is ever called. The UI never says "I'm about to write 49 SignalOS files into your folder." It says "Preparing SignalOS for this project." The phase strip just shows "Prepare".

**End-user effect:** the user wanted you to *build their app*. You silently scaffold a wave-delivery framework into their folder, then write the requested app on top of it. Both kinds of writes happen before the user can opt out.

### 2.3 Test of AI is not actually a test of AI

[src-tauri/src/provider.rs:524-538](../src-tauri/src/provider.rs#L524-L538) — `test_provider_connection` calls `fetch_provider_models`. For Anthropic that hits `/v1/models`, for OpenAI `/v1/models`, etc. **No message is sent.**

For Gemini, the API key is embedded in the URL query string ([provider.rs:787](../src-tauri/src/provider.rs#L787)) — works for listing, but a key that lists models successfully might still fail on `generateContent` for the chosen model (different ACL on Vertex/AI Studio sub-products is common).

**End-user effect:** "AI is connected" pill turns green; the user pastes a query; the first real chat fails with a 403/404. Diagnostics will say "AI connected", because the connection status was set by the `/models` call that succeeded.

### 2.4 Default Anthropic model is stale and lower than capability

[provider.rs:150-156](../src-tauri/src/provider.rs#L150-L156): default Anthropic model is `claude-sonnet-4-6`. As of 2026-05 the Claude family ships 4.7. Any user with a key minted *after* their first launch of SignalOS sees the older default and has to know to "Fetch models" → pick the new one. The Builder workflow includes a strict JSON contract ([app.js:2294-2317](../src/js/app.js#L2294-L2317)) which is more reliable on 4.6/4.7 than 4.5; users will see flaky Build runs if they accept the stale default.

The OpenAI default is `gpt-4o` (similar staleness story — `o4-mini`/`o3` are now usually a better Builder choice). Gemini default is `2.0-flash`, also generation behind.

### 2.5 Builder JSON requires a wall of constraints — fragile against any provider

The Builder prompt ([app.js:2294-2317](../src/js/app.js#L2294-L2317)) says:

```
Return ONLY valid JSON. No markdown, no prose, no code fences.
```

`parseGeneratedProject` ([app.js:2320-2356](../src/js/app.js#L2320-L2356)) then runs `extractJsonObject` which strips ```` ``` ```` fences — but the very next failure mode (model emits leading prose + then JSON) is also handled. The real fragility is the **strict required-files validation** in `validateGeneratedFiles` ([app.js:2376-2393](../src/js/app.js#L2376-L2393)):

- `react-vite`: must include `package.json`, `index.html`, `README.md`, **and** `src/main.jsx` OR `src/main.tsx`. Models that prefer `src/index.jsx` will fail.
- `next`: must include a page file at `app/page.jsx|tsx` or `pages/index.jsx|tsx|js`. Models that prefer `src/app/page.tsx` will fail.

When validation fails, error surfaces as `"AI did not include required ... file(s): src/main.jsx"`. The user thinks the AI failed; really they hit a hard-coded path expectation that doesn't survive the variance between providers and models.

**End-user effect:** Build is rejected and re-tried. No partial files are written, no recovery — start over, re-spend tokens.

### 2.6 No "preview before write" stage for the Builder

`buildProjectFromPrompt` ([app.js:2161-2260](../src/js/app.js#L2161-L2260)) calls AI → parses → immediately writes. Compare with most product-quality builders (Bolt/Lovable/v0/Cursor Compose) which always show files first and let the user accept. SignalOS just writes. There is also no "undo" — once 5-30 files are written into the user's folder, they're committed to disk.

---

## 3. Backend correctness

### 3.1 Audit-trail append is race-prone and quadratic

[src-tauri/src/governance.rs:136-150](../src-tauri/src/governance.rs#L136-L150):

```rust
fs::OpenOptions::new().create(true).append(true).open(&path)?;
fs::write(&path, {
    let existing = fs::read_to_string(&path).unwrap_or_default();
    existing + &line
})?;
```

The file is opened **for append**, then the handle is dropped, then the function reads the whole file and rewrites it with `existing + line`. So:
- Two concurrent appends can both read the same `existing` and the second overwrites the first.
- Every append rewrites the entire file → O(n²) on the audit length.
- The `OpenOptions::new().create(true).append(true).open()` call exists only to ensure the file exists. Use `.write_all(line.as_bytes())` on an `append(true)` handle and you get correct append semantics for free.

This module is **not currently invoked** by any IPC handler (see §3.2), so the bug is dormant. It is still a landmine for whoever wires it up.

### 3.2 `GovernanceState` is managed but never read

[main.rs:36](../src-tauri/src/main.rs#L36) does `app.manage(governance::GovernanceState::new())`. No `#[tauri::command]` in any of the registered handlers ([main.rs:61-106](../src-tauri/src/main.rs#L61-L106)) accepts `State<GovernanceState>`. Gate/wave/audit state for the frontend all come from the Python sidecar ([ipc.rs:500-630](../src-tauri/src/ipc.rs#L500-L630)).

`governance::append_audit`, `read_audit`, `persist_gate_state`, `load_gate_state` are all dead code. Most of `governance.rs` (the `default_wave()` builder, the `Gate`/`WaveSnapshot` types) duplicates structures that are now defined by the sidecar's JSON shape.

**End-user effect:** none today. **Cost:** ~180 lines of Rust pretending to be the source of truth, plus the misleading "single source of truth for the frontend's gate/phase UI" doc comment at the top.

### 3.3 Python sidecar is strictly serial — long commands block ping

[signalos_ipc_server.py:382-395](../python/signalos_ipc_server.py#L382-L395) reads from stdin in a `for` loop and writes one JSON response per line. There is no worker pool. While `signal-qa` or `signal-orchestrate` runs synchronously inside `core_main([...])`, every other incoming request — including the heartbeat `ping` ([ipc.js:118](../src/js/ipc.js#L118)) — queues behind it.

The frontend ping has an 8-second timeout. The Settings page "Test engine" can fail during a legitimate long command, prompting users to Restart the engine — which kills the running command.

`testEngineStatus` runs on a 1.5-second delayed init ([app.js:3022](../src/js/app.js#L3022)). If init or the first command takes long, the silent ping at startup may flip Engine to "Needs fix" with no obvious cause.

**End-user effect:** intermittent "Engine timed out" toasts on the first session and on any long QA/Plan run.

### 3.4 `os.chdir` is process-global, not per-request

[signalos_ipc_server.py:51-52](../python/signalos_ipc_server.py#L51-L52) does `os.chdir(cwd)` for every request that carries a cwd. Since the loop is serial, two workspaces can't currently collide. But:
- A request without a cwd inherits whatever the previous request set. If the user happens to switch workspaces and a stale request fires, it lands in the previous folder.
- `core_main` and its subcommands can call `os.getcwd()` internally — they will see the chdir'd location for the lifetime of the process, regardless of the `--repo-root` flag the server adds. The CLI is implicitly dependent on chdir state.

### 3.5 The provider HTTP layer has no timeouts

`provider.rs` constructs `reqwest::Client::new()` ad-hoc in every fetch and chat helper ([provider.rs:732-1051](../src-tauri/src/provider.rs)). None set a request timeout. `provider_request_error` checks `is_timeout` ([provider.rs:1103-1112](../src-tauri/src/provider.rs#L1103-L1112)), but no timeout will ever fire because none was configured. A hung provider response will hang the IPC call for as long as the OS keeps the TCP connection.

`invokeSidecar` in the frontend has timeouts (30s default / 120s for builder), but pure-Rust calls like `provider.chat` are bare `invoke` ([ipc.js:201](../src/js/ipc.js#L201)) with no client-side timeout either. So an Anthropic request that never returns will hang the chat panel indefinitely without surfacing "request stalled".

### 3.6 Anthropic chat hardcoded `max_tokens: 8192`

[provider.rs:925](../src-tauri/src/provider.rs#L925). Newer models support 64K outputs, and the Builder JSON contract can absolutely blow past 8K when scaffolding a 10-file React app. Truncated JSON → `JSON.parse` fails → "AI returned invalid build JSON. Try Build again." The user is asked to retry, pays more tokens, and gets the same result.

### 3.7 The updater advertises versions whose signatures are empty

[distribution/update-manifest/beta.json](../distribution/update-manifest/beta.json) and [latest.json](../distribution/update-manifest/latest.json) both ship every platform's `signature: ""`. `tauri.conf.json` includes a real `pubkey` and points to those manifests.

`check_for_updates` ([ipc.rs:807-829](../src-tauri/src/ipc.rs#L807-L829)) detects `signatures_missing: true` and the frontend tells the user "No beta update. Manifest signatures are not release-ready yet." That part is fine.

But the Tauri updater plugin is registered at `main.rs:21` and capabilities include `updater:default`. The plugin itself reads the same endpoints from `tauri.conf.json` and is willing to download — if the in-plugin update flow is ever wired to the frontend (or the user picks "Check for Updates" from the Help menu, which currently only emits `menu:check-update` — see §4.2), the downloaded payload won't verify and the update will fail silently from the user's POV. There is no end-user telemetry to surface that.

**End-user effect:** clicking "Check for Updates" from the native menu only emits the event; the frontend calls `check_for_updates` via reqwest and gets the manifest-aware path. The Tauri plugin updater is loaded but never invoked. So today nothing breaks. It is configuration drift waiting to bite.

### 3.8 `validate_workspace_write` is registered but never called

[ipc.rs:46-71](../src-tauri/src/ipc.rs#L46-L71) is exposed as a Tauri command (`ipc::validate_workspace_write` at [main.rs:65](../src-tauri/src/main.rs#L65)) and `ipc.js` exports `workspace.validate` ([ipc.js:84](../src/js/ipc.js#L84)). `app.js` never calls it. The actual write-path guards live inside `write_workspace_export` / `write_workspace_files` / `upsert_workspace_secret`, which is fine — but the unused command is dead surface that suggests caller-side validation that doesn't exist.

### 3.9 `start_workspace_watch` never cancels and ignores rename/move

[ipc.rs:838-871](../src-tauri/src/ipc.rs#L838-L871) spawns a tokio task that polls the workspace mtime forever. There's no shutdown signal, no debounce, and switching workspace (`set_workspace`) doesn't stop the old watcher. After two folder switches, two watchers race and both emit `workspace:changed` events with stale paths. The frontend currently does not subscribe to `workspace:changed` (the only listener is `ipc.onWorkspaceChange` and it has no caller in `app.js`), so this is dormant — but if anyone wires it up, expect duplicate refresh storms.

### 3.10 `record_token_usage` is a Tauri command that's never called

[main.rs:100](../src-tauri/src/main.rs#L100) registers `provider::record_token_usage`. The frontend never invokes it. Token costs are recorded server-side in `send_provider_message` via `record_chat_cost` ([provider.rs:896-915](../src-tauri/src/provider.rs#L896-L915)) — which is the correct path. The hand-rolled `provider.recordTokens` in `ipc.js:194` is dead.

---

## 4. UI / UX problems

### 4.1 The 4-step Build flow conflates "what to build" with "is SignalOS set up"

The header reads "**Build** — describe the app and generate the first working version" ([index.html:1175-1179](../src/index.html#L1175-L1179)) but the **same page** offers `mainAction` and `secondaryAction` buttons that are dynamically labeled "Choose project folder", "Save and test AI", "Check project", "Set up project", etc., depending on `currentStep()` ([app.js:602-656](../src/js/app.js#L602-L656)).

So when AI is not connected, the screen says "Build" at the top, "Step 2 of 4" in the kicker, the textarea says "What do you want to build?", and the primary CTA below says "Save and test AI". The user is asked to fill in a build prompt that won't be used and to also do an AI setup task. Pressing **Build app** while AI isn't ready calls `switchView("project")` and toasts "Connect and test AI first." — the build prompt the user typed stays put.

This is the only screen in the product that combines onboarding and execution in the same panel. It should split: dedicated setup steps until step 4, then a clean Build prompt UI.

### 4.2 Native menu items emit events for tabs that don't exist

[main.rs:163-188](../src-tauri/src/main.rs#L163-L188): the View menu has Chat / Dashboard / **Brain** / Audit Trail.

`app.js:2745-2753` maps menu nav payloads to views:

```js
const mapped = { chat: "chat", dashboard: "dashboard", brain: "brain", audit: "history" };
switchView(mapped[event.payload] || "guide");
```

So `Cmd+3` navigates to a Brain view that has no sidebar entry, and `Cmd+4` shows the History panel labeled "Audit Trail" in the menu but "History" in the sidebar. The vocabulary is inconsistent across menu, sidebar, code, and docs (`brain` vs `notes` vs `Notes`; `audit` vs `history`; `guide` vs `build`).

### 4.3 The mobile breakpoint hides the sidebar entirely

[index.html:1068-1097](../src/index.html#L1068-L1097): at `max-width: 1040px`, `.sidebar { display: none }`. There is no hamburger, no top-tab bar, no way to navigate. The only surface left is the topbar (title, status pill, provider pill, cost pill) and whatever view was last active. The user on a 13" laptop with browser zoom or scaling can lose access to every view except the one they were on at the moment of resize.

### 4.4 The Brain (Notes) view loads on demand of a non-existent button

`renderBrain` ([app.js:1205-1221](../src/js/app.js#L1205-L1221)) is included in the global `render()` cycle, so it draws into the DOM whenever any state changes — but it's only visible if `state.view === "brain"`. Since you have to type-deep to get there (menu shortcut or post-setup CTA), the brain-search input ([index.html:1424](../src/index.html#L1424)) doesn't even surface to the user on a normal day. On the Project page, the brain rendering still consumes CPU on every refresh.

### 4.5 Toast notifications are the only feedback surface for many critical actions

`toast()` ([app.js:368-373](../src/js/app.js#L368-L373)) shows for 2.6s. Critical confirmations like:

- "Project folder saved." — only confirmation that the workspace switched
- "Saved AI key deleted." — only confirmation that a credential was wiped
- "Files ready" / "Some were blocked" — the actual list of blocked files is in the chat log if you happen to be on the Chat view

… don't persist anywhere durable. A user on Dashboard who clicks "Reset session" sees a flash; no audit-trail entry, no settings-page note.

### 4.6 The "Stop" button is wired to a fake cancel

`cancelRunningCommand` ([app.js:2083-2098](../src/js/app.js#L2083-L2098)) calls `ipc.signal.cancelPending(...)` which only rejects local JS promises — `rejectPendingSidecars` in [ipc.js:72-77](../src/js/ipc.js#L72-L77). The Python sidecar **continues running the command**; the only way to actually stop it is `restartEngineStatus` which the cancel handler does call afterwards. So pressing Stop in the middle of `signal-qa` will appear to cancel the UI promise, but the actual subprocess work continues until the next request kills the engine.

The toast says "Command stopped." — that's misleading.

### 4.7 The 1700-line single `index.html` and 3000-line `app.js` are fragile

No componentization. `bindEvents()` does ~80 manual `.addEventListener` calls. Every view-render is a full document re-render via `innerHTML = …` — even when only one row of one panel changed. `escapeHtml` is applied per render; for the audit/timeline list with 50 entries this is O(n) reflows per state update.

`renderActivity` rewrites `el.activityLog.innerHTML` every time a log line changes — including during the `setInterval(2500)` progress ticker ([app.js:2065](../src/js/app.js#L2065)). The cursor/selection in `commandInput` survives only because the input is outside the rewritten subtree.

The DOM has multiple **duplicate provider form** controls (the Project view and Settings view each have their own `providerSelect / providerModelSelect / providerKey / fetchModels`). `renderProviderForm` syncs both forks of state. Bugs that fix one don't automatically fix the other (e.g. `providerKeyValue()` ([app.js:592-600](../src/js/app.js#L592-L600)) has a special-case "if in settings, look at settings first" tie-breaker that exists exactly because of this duplication).

### 4.8 The Settings page is too crowded to be useful

A single section under "Workspace" stacks: project picker, update channel, AI service+model+key form, session cost, monthly budget, reset/delete buttons. The Engine section is on the right. There is no visual hierarchy, no "danger" treatment for "Delete saved key", and no confirmation dialog. One mis-click on "Delete saved key" silently removes a credential.

### 4.9 The Secrets view cannot remove or list a secret's values, can only overwrite

[index.html:1453-1493](../src/index.html#L1453-L1493). Add/update is supported. No "delete" button. The list view ([app.js:1350-1387](../src/js/app.js#L1350-L1387)) shows file paths and variable names, but no way to remove a single variable from a file via the UI. To delete a leaked key, the user must open `.env.local` in their editor and remove it manually — exactly what the README says they shouldn't have to do.

The `quote_env_value` function ([ipc.rs:1032-1039](../src-tauri/src/ipc.rs#L1032-L1039)) **always** quotes the value. A pre-existing unquoted line gets converted to quoted on update — diff-noise that confuses users who read `.env.local` later.

### 4.10 Provider selector groups "primary" vs "more" but does not surface why

[app.js:866-877](../src/js/app.js#L866-L877) splits providers into `[anthropic, openai, gemini, qwen, ollama]` and the rest into an `<optgroup label="More providers">`. There is no visual or in-help explanation of why those five and not others, and no easy way to "favorite" a provider you actually use. OpenRouter — arguably the most pragmatic choice for newcomers — is buried under "More".

### 4.11 The Chat view does not persist scroll across renders

`renderActivity` sets `el.activityLog.scrollTop = el.activityLog.scrollHeight` ([app.js:1004](../src/js/app.js#L1004)) on every render. If the user is scrolled up reading an earlier response, any incoming progress tick (every 2.5s during a running command — [app.js:2065](../src/js/app.js#L2065)) jumps the view back to the bottom. Scrolling to read older messages while a command is running is impossible.

### 4.12 Localization is partially ASCII-fied

The CSS / index.html avoids special characters everywhere (em-dashes are typed as "-", periods serve as separators). But the audit timeline ([app.js:1245-1250](../src/js/app.js#L1245-L1250)) uses ` . ` as a separator — three characters that look like nothing in a tight font. The status pill text "Setting up" — there is no spinner, no progress bar, just a small amber dot. From across the room, the user cannot tell whether the app is working.

### 4.13 No keyboard accessibility on the 4 phase tabs

`.phase-tab` buttons ([index.html:1227-1242](../src/index.html#L1227-L1242)) are buttons (good), have `role="tab"` and `aria-controls` (good), but the panes have **no `tabindex`**, there is no roving-tabindex pattern, and arrow-key navigation between tabs is not implemented in `bindEvents`. Keyboard users can tab into a tab and press Enter, but can't arrow-left/right between tabs as the WAI-ARIA tabs pattern requires.

### 4.14 Status pill semantics are wrong when AI is partially broken

[app.js:701-704](../src/js/app.js#L701-L704):

```js
const ready = state.workspace && aiReady();
const error = Boolean(state.sidecarError);
el.statusPill.className = `pill ${error ? "error" : ready ? "ready" : ""}`;
```

If AI fails connection but the engine is fine, the pill stays amber "Setting up", which understates the problem. If the engine errors mid-session, the pill goes red — even though the user might still be able to chat with a working provider for tasks that don't need the engine.

---

## 5. Security / privacy

### 5.1 Secret redaction is regex-based and conservative; high false-negative rate

[signalos_secret_guard.py:38-78](../python/signalos_secret_guard.py#L38-L78) defines patterns for:
- env-style `KEY=value` lines where the key name or value matches certain hints,
- known prefixes: `sk-`, `sk-ant-`, `AKIA…`, `Bearer …`, DB URLs with creds.

It will **not** catch:
- Vertex AI service-account JSON (no `private_key` line is itself flagged; the BEGIN/END block is, but the surrounding JSON metadata isn't redacted)
- Most OAuth tokens (no specific pattern beyond `Bearer …` — a raw token in a JSON field named `auth_token` and value `eyJabc…` will not match the `_TOKEN` field regex unless the key name matches `SECRET|TOKEN|…`).
- AWS session tokens prefixed `IQoJ…`.
- Most Stripe keys (covered? `stripe` in the SECRET_NAME_RE, but value-only `pk_live_…` is not in `HIGH_CONFIDENCE_SECRET_PATTERNS`).

For an installer-first product that markets "secrets stay out of AI prompts," this is a soft guarantee — `redact_text` is best-effort. The README and USER_GUIDE could be read as a stronger guarantee than the implementation provides.

### 5.2 PDF "text extraction" is regex-on-latin-1 — silently wrong on most PDFs

[signalos_attachments.py:197-207](../python/signalos_attachments.py#L197-L207): `extract_pdf_text` does `raw.decode("latin-1", errors="ignore")` then `re.finditer(r"\(([^()]|\\.){2,}\)")`. This will not work on compressed (FlateDecode) PDF content streams, which is the majority of modern PDFs. Output will be incomplete or empty.

`analyze_document` ([signalos_attachments.py:140-143](../python/signalos_attachments.py#L140-L143)) then says "Document attached. Text extraction did not find readable text." — which sounds normal but obscures the systemic bug.

### 5.3 Attachment XML parsing is XXE-soft

`xml.etree.ElementTree.fromstring(raw)` ([signalos_attachments.py:219](../python/signalos_attachments.py#L219)) is called on attacker-controlled bytes (`.docx`, `.pptx`, `.xlsx` are zip archives of XML). `ElementTree` does not resolve external entities by default, so the worst-known XXE vectors are blocked, but it remains vulnerable to **billion-laughs / quadratic blowup DTDs** on adversarial input. A user who attaches a malformed `.docx` could OOM the sidecar.

`defusedxml` is the standard mitigation; not in use here.

### 5.4 The CSP allows `script-src 'unsafe-inline'` and remote font CSS

[tauri.conf.json:31](../src-tauri/tauri.conf.json#L31):

```
default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; script-src 'self' 'unsafe-inline'
```

`script-src 'unsafe-inline'` exists because some inline handlers slipped in — searching the codebase, all event handlers are added via `addEventListener` in `app.js`, so `'unsafe-inline'` is not strictly needed for the current frontend. Removing it would harden the CSP without code changes.

The frontend never actually loads Google Fonts (the `<head>` has no `<link>` to googleapis). The CSP allowlist is therefore a dead permission. Tighten the CSP.

### 5.5 Provider keys are passed through Tauri IPC as plain strings

The keychain layer ([keychain.rs](../src-tauri/src/keychain.rs)) is correct: OS keystore for at-rest. But the API key crosses the IPC boundary in plaintext in two places:
- `store_api_key(provider, key)` — necessary, one-time.
- `fetch_provider_models(provider, api_key)` and `test_provider_connection(provider, api_key)` — accept an `Option<String>` *every call* if the user typed a key into the input box (`providerKeyValue()` returns it; `app.js:1622` passes it). The Tauri webview ↔ Rust IPC is in-process so this is fine in theory, but if you ever add devtools (`#[cfg(debug_assertions)]` does — [main.rs:51-54](../src-tauri/src/main.rs#L51-L54)), the key is visible in the network panel for the duration of the call. Acceptable for an installer; flagged for awareness.

### 5.6 Updater pubkey is shipped but signatures are empty everywhere

See §3.7. The end user sees a friendly "no update available" toast because the frontend explicitly checks `signatures_missing`. But that detection lives in the Rust `check_for_updates` handler — if a future build replaces it with the standard Tauri updater plugin call, **any unsigned manifest will resolve to "no update"** silently. The current design relies on a one-off custom HTTP path doing what the Tauri updater plugin would otherwise validate.

---

## 6. Cross-cutting code-quality observations

- **Duplication of provider list of strings.** Hard-coded in 4 places: [keychain.rs:57-59](../src-tauri/src/keychain.rs#L57-L59), [Provider::all](../src-tauri/src/provider.rs#L108-L123), [ProviderConfig::defaults](../src-tauri/src/provider.rs#L147-L249), [ipc.js mock](../src/js/ipc.js#L267-L280). Add a 13th provider and you touch 4 files plus the Settings UI.
- **Magic numbers as string literals.** Version `"0.0.9"` appears in tauri.conf.json, manifests, README expected outputs, and as a hardcoded "version" in the sidecar's `ping` response ([signalos_ipc_server.py:103](../python/signalos_ipc_server.py#L103)).
- **Two ways to send to the sidecar.** `invoke("run_signal_command", ...)` (one-shot, returns id, fire-and-forget) and `invokeSidecar("run_signal_command", ...)` (returns the resolved data). Most callers use the latter. `ipc.signal.run` (raw) is exposed but only used implicitly. The naming is confusing: one says "run", the other says "runAndWait".
- **`completedSidecar` cache and `pendingSidecar` map race.** `ipc.js:14-31`: if a response arrives before `invokeSidecar` registers its pending promise, the response is parked in `completedSidecar` for 30s. Edge cases on cancel/restart can leak entries; there is no cap on map size beyond the per-key 30s timeout.
- **`safeCall` swallows all errors and logs to console.** ([app.js:359-366](../src/js/app.js#L359-L366)). For a desktop app with no built-in console for the user, this means a misbehaving IPC silently returns `null` and the UI rendering branches into "empty" states. There is no surfaced error to the user.
- **Browser mock is rich and looks like real data.** When `IS_TAURI` is false, `mockInvoke` ([ipc.js:214-401](../src/js/ipc.js#L214-L401)) returns plausible artifacts, providers, a generated React app, etc. Opening `index.html` directly looks like a working app. The README documents this. The danger: in screenshots or QA sessions someone might capture mock data as if it were real.
- **`__pycache__` directories are checked into the python folder.** Pre-built `.pyc` files ship with the sidecar bundle and can drift from source.
- **CHANGELOG.md** — not checked, but worth verifying it tracks all of 0.0.4 → 0.0.9.

---

## 7. End-user journey — what actually happens, step by step

### 7.1 First launch, no project, no AI configured

1. Topbar status pill: **Setting up** (amber). Provider pill: **AI not connected**. Cost: **$0.00**.
2. Build view is active. Hero says "Describe the app." Below it: "No folder selected yet." and "AI is not connected."
3. Primary button reads **Choose project folder** (computed by `nextAction()`).
4. Phase strip below shows 4 empty cards.
5. The "Build result" panel below the hero says "No build yet."

**Friction:** "Build" is the screen title but the primary action is "Choose project folder." If you type a prompt and hit **Build app**, you're told to choose a folder. The same screen flips identity between onboarding and execution.

### 7.2 User picks a folder

1. Native folder picker via `tauri-plugin-dialog`. Works.
2. After selection, `state.workspace` set, watch started, status refresh fires. Sidebar shows the folder basename, hero updates.
3. Primary button now reads **Test AI connection** (or **Save and test AI** if there's typing in the key field).

**Friction:** picking a folder writes nothing. But the next button click can write 50 files into it (see §2.1, §2.2). There is no preview of what SignalOS is about to do to that folder.

### 7.3 User connects Anthropic (cloud)

1. Sidebar Project → AI panel. Provider dropdown defaults to Anthropic. Model defaults to `claude-sonnet-4-6` (stale, §2.4).
2. Paste key. Click **Save connection**.
3. `saveProvider()` stores in keychain → calls `validateProviderConnection({ silentSuccess: true })` → which calls `fetch_provider_models` (NOT a real chat) → if it returns >0 models, pill goes green "Anthropic Claude connected."
4. Engine pill stays unchanged.

**Friction:** "Connected" is not validated against the model the user will actually chat with. See §2.3.

### 7.4 User types into Build prompt and clicks Build

1. `buildProjectFromPrompt` runs.
2. **Silently**: if not initialized, runs `/signal-init` → which is `init --force` → which copies the SignalOS bundle into the user's folder, overwriting any colliding files (§2.1).
3. Runs `/signal-status`.
4. Sends a heavily-constrained JSON prompt to AI.
5. Parses; validates file list against hard-coded path expectations (§2.5).
6. Writes up to 40 files, 400 KB each, 2 MB total ([ipc.rs:337-369](../src-tauri/src/ipc.rs#L337-L369)).
7. Runs `/signal-status` again. Marks success.

**Friction:** at no point did the user say "yes write to my folder". The "Setup" phase card is purely visual — it doesn't prompt for consent.

### 7.5 User signs a gate

1. Chat view → Project steps sidebar shows `gateList`.
2. User types signer name. Clicks **Sign**.
3. `signGate(gateId)` → `ipc.gates.sign(id, signer)` → sidecar `gate:sign` → `signalos sign G<id>` CLI inside the bundled core.
4. Frontend logs success and refreshes.

**Works.** The Rust `governance.rs` is bypassed; truth lives in the bundled core.

### 7.6 User exports an issue report

1. Settings or History → **Export issue report**.
2. Markdown file written to `.signalos/issue-reports/issue-report-<timestamp>.md` via `write_workspace_export` ([ipc.rs:286-326](../src-tauri/src/ipc.rs#L286-L326)).
3. Includes `diagnosticsPayload()` JSON — which includes `state.audit`, `state.brain`, `state.log[-12]`, plus AI provider/model and engine status.
4. `redact_text` is **not applied here** — this Markdown is written by the JS side directly. The Markdown will include whatever text the user typed in `addLog`/notes, raw. If they pasted a secret into the chat input, the issue report will contain it.

**Friction:** the path is named "redacted issue report export" in the README, but the frontend doesn't run a redaction pass on the export. The redaction guarantees only apply to data that *crossed the Python sidecar boundary*; logs originating in JS skip redaction.

### 7.7 User restarts the engine

1. Settings → Engine → **Restart engine**.
2. Rust kills the current child, spawns a new sidecar (`restart_python_sidecar` → `start_python_sidecar(replace_existing: true)`).
3. Any in-flight `invokeSidecar` Promises hang until their per-call timeout fires (no shared cancel propagation).
4. Engine pill flips to "Restarting…" → "Ready" on next ping.

**Works.** But: in-flight commands can produce confusing "Timed out" errors *after* the restart succeeded, because the new sidecar doesn't see the old request IDs.

---

## 8. Prioritized fix list (severity × visibility)

### P0 — destructive or trust-damaging

1. **Stop forcing `--force` on `/signal-init`.** Either remove `--force`, or only set it when the folder is empty after the ignore-set check ([signalos_ipc_server.py:138](../python/signalos_ipc_server.py#L138)). For non-empty folders, render the would-be-changed file list in the UI and require explicit user confirmation.
2. **Add explicit "files will be written" preview to Build.** Before `write_workspace_files`, show the file count + paths + diff for any overwritten files. Require a Confirm click.
3. **Add a "files will be created/overwritten by setup" preview** to `/signal-init` exposed by the UI. Even better: surface SignalOS Setup as a clearly marked side panel, not the same button as Build.

### P1 — broken promises or silent failure

4. **Validate AI by sending one message, not by listing models.** Change `test_provider_connection` to do a tiny `send_provider_message("ping")` and verify the chat path. Update the friendly error mapping.
5. **Refresh provider defaults to current model generations.** Anthropic 4.7, OpenAI o4-mini/o3, Gemini 2.0/2.5. The shipped `providers.json` defaults set the user up to fail Build until they manually Fetch+select.
6. **Apply `redact_text` to issue-report and handoff Markdown** on the Rust side before write, or move the report generation into the Python sidecar.
7. **Make Stop actually stop.** Either send a cancel message into the sidecar that interrupts the running CLI, or do not pretend to stop. Today's behavior is misleading.
8. **Set HTTP timeouts on every reqwest call** in `provider.rs` (chat: 60s, models: 15s).
9. **Raise Anthropic `max_tokens` to model-appropriate values** (use 32K for Sonnet 4.6, 64K for Opus 4.7). Keep the Builder reliable.
10. **Surface the unreachable views.** Add Notes and Help (or Templates+Recipes) to the sidebar, or remove the dead Help view if it isn't on the roadmap.

### P2 — UX papercuts

11. Audit-trail append: switch to a true append handle ([governance.rs:136-150](../src-tauri/src/governance.rs#L136-L150)).
12. Stop sidecar mtime watcher on workspace change.
13. Don't auto-scroll the activity log during a running command unless the user is already at the bottom.
14. Add a "delete secret variable" affordance to the Secrets view.
15. Add confirmation dialogs for **Delete saved key**, **Forget project**, **Reset session**.
16. Trim CSP: drop `script-src 'unsafe-inline'`, drop the Google Fonts entries the frontend never uses.
17. Replace the global ` . ` dot-separator pattern in audit/timeline with a real bullet or em-dash; it's invisible.
18. Add an ARIA tablist keyboard pattern to the 4 phase tabs.
19. Add a topbar hamburger or kebab nav for the `max-width: 1040px` case so the sidebar is not the only entry point.

### P3 — code hygiene

20. Remove `governance.rs` dead code or wire it up.
21. Remove `validate_workspace_write` (unused) or call it from frontend writes.
22. Remove `provider::record_token_usage` (unused) — costs are recorded on `send_provider_message` already.
23. Remove `plan-reader.js` (orphaned, uses CSS variables that no longer exist) or rebuild a Plan tab on top of it.
24. Consolidate the duplicate provider form (Project view + Settings view) into a single component.
25. Move provider IDs to a single source of truth shared between Rust and the mock.
26. Set `defusedxml` (or the equivalent constraint) for attachment XML parsing.

---

## 9. What it would feel like to use this on the next project

**Day 1.** Installer runs. App opens. The hero says "Build". You type a prompt. You're nudged to set up AI. You paste a key, see "Connected" in 2 seconds. You hit Build. The app silently scaffolds a wave-delivery framework into your project folder and writes a React app on top of it. You didn't know about the wave-delivery framework. Now your folder has `core/`, `integrations/`, `.signalos/`, `core/strategy/PLAN.md`, plus the React app. The "Build result" panel shows 6 files — those are *just the React files*. The other 49+ are invisible in the UI.

**Day 2.** You want to do a follow-up: "add a dark mode toggle." You type it into the same Build prompt and click Build. The system re-runs init (idempotent on the bundle) and then re-prompts the AI. Half the time the AI returns the same files it did yesterday; other times it edits but doesn't include all required files (`src/main.jsx` missing → `validateGeneratedFiles` blocks the write). You retry, pay tokens, repeat. Cost meter climbs.

**Day 3.** You try `/signal-qa`. It's marked Advanced. It runs. It works. But it takes 90 seconds and during that time the engine ping fails. Engine pill flips to red. You hit Restart. The QA was almost done — restarting kills it. You lose progress.

**Day 4.** You find Notes. You wonder why it isn't in the sidebar. You save a decision. The next day you forget where Notes lives. There is no global search. You go back to the chat log to find your own note.

**Day 5.** You export an issue report to share with a teammate. It includes your local repo path, your sidecar PID, your provider, and (because redaction is JS-side missing) the API key you pasted into chat yesterday by accident.

That is the gap between "installer-first" and "ready-to-use for a non-SignalOS user." The product reads as a sophisticated governance shell with a chat front-end stitched on; what the next project needs is the reverse: a chat-and-builder front-end that uses governance silently when it helps, and never imposes it on a fresh folder without an explicit conversation.

---

## 10. Inventory of dead, partially-wired, or duplicated code

| Item | Path | Status |
|---|---|---|
| `governance.rs::append_audit / read_audit / persist_gate_state / load_gate_state / GovernanceState` | [src-tauri/src/governance.rs](../src-tauri/src/governance.rs) | Managed but never read; ~180 lines of dead code |
| `validate_workspace_write` IPC command + `ipc.workspace.validate` | [ipc.rs:46](../src-tauri/src/ipc.rs#L46), [ipc.js:84](../src/js/ipc.js#L84) | Registered + exported, no caller |
| `record_token_usage` IPC command + `ipc.provider.recordTokens` | [provider.rs:492](../src-tauri/src/provider.rs#L492), [ipc.js:194](../src/js/ipc.js#L194) | Registered + exported, no caller (costs recorded server-side) |
| `plan-reader.js` | [src/js/plan-reader.js](../src/js/plan-reader.js) | Orphaned. Uses CSS vars (`--surface`, `--border`) that don't exist in `index.html` |
| `onWorkspaceChange` listener | [ipc.js:103](../src/js/ipc.js#L103) | Exported, no caller |
| Help view (`view-help`) | [index.html:1609](../src/index.html#L1609) | Built, no nav link, unreachable |
| Notes view (`view-brain`) | [index.html:1418](../src/index.html#L1418) | Built, no sidebar link, reachable only via secondary CTA |
| Native menu `Cmd+3` (Brain) | [main.rs:177](../src-tauri/src/main.rs#L177) | Navigates to view not in sidebar |
| `tauri-plugin-updater` plugin | [main.rs:21](../src-tauri/src/main.rs#L21) | Loaded; never invoked. Custom `check_for_updates` HTTP path used instead |
| Preview commands in catalog | [app.js:34-47](../src/js/app.js#L34-L47) | 15 of 37 — return spec text only |
| `signal-pause`/`signal-pre-wave`/`signal-pre-design` (and others) | [app.js:42-44](../src/js/app.js#L42-L44) | In catalog, not in sidecar `direct` set → spec only |
| Mock provider list | [ipc.js:267-280](../src/js/ipc.js#L267-L280) | Duplicates Rust defaults; drift risk |

---

## Appendix A. File-by-file map

```
signalos-app/
├── src/
│   ├── index.html                     1700 lines, single-file UI shell
│   └── js/
│       ├── app.js                     3025 lines, all behavior, no components
│       ├── ipc.js                      402 lines, Tauri bridge + browser mock
│       └── plan-reader.js              216 lines, ORPHANED
│
├── src-tauri/src/
│   ├── main.rs                         248 lines, entry + native menu
│   ├── ipc.rs                         1223 lines, workspace + signal + git + updater
│   ├── provider.rs                    1112 lines, 12-provider chat/fetch + cost
│   ├── sidecar.rs                      257 lines, Python subprocess manager
│   ├── keychain.rs                      61 lines, OS keystore CRUD
│   ├── governance.rs                   181 lines, DEAD CODE (managed but unread)
│   └── lib.rs                           10 lines, module exports
│
├── python/
│   ├── signalos_ipc_server.py          399 lines, NDJSON IPC server
│   ├── signalos_attachments.py         257 lines, attachment intake + redaction
│   ├── signalos_secret_guard.py        199 lines, regex secret matcher
│   └── signalos_lib/                  12k lines, vendored SignalOS Core
│
├── src-tauri/tauri.conf.json           78 lines, app + bundle + updater config
├── src-tauri/capabilities/default.json  18 lines, permissions allowlist
├── distribution/update-manifest/{beta,latest}.json   shipping signatures = ""
└── docs/                               existing reference reviews + this file
```

---

## Appendix B. The 7 verbatim quotes from README / USER_GUIDE that the implementation does not yet match

1. **"OS keychain storage for API keys; raw keys are not shown after save."**  ← True for the keychain. False for `redact_response` round-trips: a key typed into the input field is sent through IPC as plaintext on every Fetch/Test click, surfaced in devtools network in debug builds. **Mostly true; flag the edge case.**

2. **"Redacted issue-report export and team handoff export into the selected project."**  ← The Markdown body is assembled in JS (`issueReportMarkdown`, `handoffMarkdown`) and written via Rust `write_workspace_export`. No `redact_text` pass is run on the JS-built content. **False in the cases that matter (raw chat input, raw notes).**

3. **"Secret summary that shows risky file names and variable names without exposing values."**  ← True for `.env*` files. Does not detect or summarize secrets stored elsewhere (e.g. inline in `config.json`, in scripts under `bin/`).

4. **"Multi-provider AI setup with model fetch, model picker, and manual Other model entry."**  ← True. Solid.

5. **"Engine diagnostics with ping, status, restart, and redacted diagnostic copy."**  ← True for ping/status/restart. "Redacted diagnostic copy" copies `JSON.stringify(diagnosticsPayload())` which includes `state.log[-12]`. No redaction is applied on the JS side. **Same gap as #2.**

6. **"Beta/stable update-channel preference for release checks."**  ← True for the surface. The actual updater plugin is not used; manifests have empty signatures. **Surface-level true, runtime-level wired wrong.**

7. **"They should not need: this repository, the separate SignalOS core repository, Python installed system-wide, Rust/Node/Cargo/Tauri or build tools."**  ← Mostly true thanks to `externalBin` Python sidecar. The user *does* need `git` on PATH for `_git_init` and `get_git_status` to work; `bash` on PATH for `_register_ide_hooks`. Neither is enforced or documented as a prerequisite. **Partially false.**

---

---

## 11. UI redesign requirements (added 2026-05-14, post-review session)

The end-user requirement re-stated, verbatim from the session:

> "I need a very simple UI and very clear progress bar and each running step shows its detailed progress where we are, what we have done and what's coming during this step or gate or whatever. Clean and good shape that beats Replit or Lovable. I need to be able to view the work and run it. Also I need the functionality of a **fully wired & enforced SignalOS** app."

This means three things, all of which must land together — none alone is enough. "**Fully wired & enforced**" is a positioning decision, not a wording tweak: SignalOS competes on *governed* delivery, not on raw chat-to-app. If governance is optional it's a CLAUDE.md file. The differentiator is that the app actively makes users follow the protocol — they can override, but every override is named, logged, and visible.

### 11.1 Three-pane shell — left (nav, collapsible), center (chat), right (preview)

Reference points:
- **Replit**: left file tree + nav, center editor/chat, right preview/web. Tree collapses to icons.
- **Lovable / Bolt / v0.dev**: chat left, preview right, files hidden in a drawer.
- **Today's SignalOS App**: a single tab at a time. No coexistence of chat and preview. No file tree. Forces the user to leave the app to see results.

**Direct requirement from the session, verbatim:**

> "A real front end on the left can be hidden, then the middle becomes for chatting, the right side for view."

So the layout is Replit-shaped, **not** Lovable-shaped:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  SignalOS · my-todo-app · Sonnet 4.7 · $0.12 · Ready ●          [?] [⚙]    │   topbar
├────────────────┬─────────────────────────────────┬──────────────────────────┤
│  ☰             │  Chat                           │  Preview                 │
│                │                                 │                          │
│  ▸ Project     │   You · 14:02                   │  ┌─────────────────────┐ │
│    my-todo-app │   build a todo app with         │  │                     │ │
│    Sonnet 4.7  │   priorities                    │  │   Live iframe of    │ │
│                │                                 │  │   the running app   │ │
│  ▸ Files       │   SignalOS · 14:02              │  │                     │ │
│    package.json│   ─ Progress ─────────────      │  │   localhost:5173    │ │
│    src/        │    ✓ Prepare                    │  │                     │ │
│      main.jsx  │    ▶ Plan      drafting…  12s   │  │                     │ │
│      App.jsx   │       ↳ 1,420 in · 3,108 out    │  └─────────────────────┘ │
│      styles.css│    ○ Build · 8 files            │                          │
│    index.html  │    ○ Review                     │  [ ▶ Run ] [ ↻ ] [ ⏏ ]   │
│    README.md   │   ───────────────────────────   │  [ Open in browser ]     │
│                │                                 │  [ Open folder ]         │
│  ▸ Steps       │   ┌───────────────────────────┐ │  [ Open in VS Code ]     │
│    G2 current  │   │ Ask SignalOS or run /…    │ │                          │
│    G3 locked   │   └───────────────────────────┘ │  ▾ Run log (collapsed)   │
│                │   [ Build ] [ /signal-status ]  │     npm install…         │
│  ▸ Notes       │                                 │     vite v5.0.0 dev      │
│  ▸ History     │                                 │     ready in 312ms       │
│  ▸ Settings    │                                 │                          │
└────────────────┴─────────────────────────────────┴──────────────────────────┘
   240 px,                center,                       40-50%, resizable,
   collapsible to 56 px   minWidth 480 px               collapsible
```

**Hard rules for the three panes:**

- **Left pane — full SignalOS surface, collapsible.** The whole nav lives here: Project, Files (the file tree), Steps (gates), Notes, History, Settings. A `☰` button at the top collapses the pane to a 56 px icon-only rail; click an icon to re-expand. Persist the collapsed/expanded state per project.
- **Files tree is real.** Not a flat artifact list. Generated files and existing files are visible, with a small badge for "new" / "modified" / "untracked". Right-click → Open, Reveal in OS, Copy path.
- **Center pane — chat only.** No other widgets compete for space. Chat history scrolls; progress is a card inside chat; the composer pins to the bottom. The composer is the **one** entry point for Builder prompts, `/signal-*` commands, and free-form AI questions — auto-detected (today's `looksLikeSignalCommand` + `isBuildIntent` logic, fixed and unified).
- **Right pane — preview, resizable + collapsible.** Default split 60% chat / 40% preview, drag-divider to resize, click-collapse to hide. The preview iframe is the **hero** when a build exists; when no build exists, the pane shows a clean empty state with the project's README rendered.
- **No tabs in the center.** Switching from Chat to Project (or Files, Steps, etc.) is done by **clicking the left-pane item**, which swaps the center content — but the chat remains as a switchable view, not the only view. (Some clicks like "Notes" open a drawer over the right pane, not the full center; details are component-level.)
- **Topbar is one line.** Project name, model, cost, status. `?` for help, `⚙` for settings drawer.
- **No `optgroup` "More providers".** All 12 in one searchable combobox.
- **Settings is a drawer**, not a peer page. Triggered by `⚙` from anywhere.

**Why this beats Replit for SignalOS's audience:** Replit shows code; SignalOS shows phase progress + governance + preview in the same place. The user sees *both* the running app **and** the wave-gate state — no other product does that combo today.

**Why this beats Lovable:** Lovable hides the file tree because it doesn't have one worth showing. SignalOS does (governance files, plan, gates, audit, notes), so it should put them in front of the user, collapsible when the user wants to focus.

### 11.2 Progress bar — chosen style: A (per-phase + per-substep, live)

Reasoning: the user's stated need is "know where we are, what we did, what's next." Style B (per-phase only) cannot express that inside a long Plan or Build phase. Style A always answers the three questions.

**Design contract for Style A:**

- Top-level progress bar = 0-100% based on completed substeps across all phases. Each substep contributes equally.
- Each phase has a **named substep list** that is known up front (declared in the orchestrator). Substeps cannot be invented mid-flight.
- Each substep has 4 states: `pending` (○), `running` (▶, animated), `done` (✓), `error` (✕). Plus optional `detail` line for live data (token counts, file paths, elapsed seconds).
- Substeps are written **per phase, in the orchestrator config** — not in the UI. The UI is dumb and reads a stream.
- A `substep.update` event carries `{ phase, substep, state, detail, ts }` and overwrites the row.
- Click on any done substep → expands to show what it produced (file list, AI response, CLI output, audit entry).
- A "Show full log" toggle reveals every line (current chat-style activity log) for power users.

**The 4 standard phases** (locked, ordered, named):

1. **Prepare** — folder checks, SignalOS init (with consent prompt if non-empty), engine ping, AI ping. Substeps: `Read folder`, `Check engine`, `Test AI`, `Init SignalOS (if needed, with consent)`, `Load current status`.
2. **Plan** — write a scoped plan, save evidence. Substeps: `Draft plan with <model>`, `Validate plan schema`, `Save to .signalos/builds/`, `Record decision note in Brain`.
3. **Build** — preview files, write on confirm, write SignalOS audit entry. Substeps: `Generate files (8/8)` with sub-counter, `Validate against stack contract`, `Show file diff preview`, `Wait for user confirm`, `Write files`, `Append audit entry`.
4. **Review** — refresh status, run preview, surface next action. Substeps: `Refresh /signal-status`, `Run gate check`, `Start preview server`, `Open preview pane`, `Surface next safe action`.

The same phase contract is used for **every** SignalOS workflow, not just Builder — `/signal-qa`, `/signal-plan`, `/signal-ship`, etc. all declare their own substeps. The UI is uniform across commands.

### 11.3 View the work and run it — in-app live preview pane

This is the Lovable parity feature. Without it, "what did SignalOS build me?" is a question the user has to leave the app to answer.

**Minimum viable preview pane:**

- Detect stack from the build (`react-vite`, `next`, `node-express`, `python-flask`, `static`).
- **Static**: no install, just `file://` or a tiny local static server on `localhost:<random-port>` → iframe.
- **react-vite / next / node-express**: on **first Run**, run `npm install` in a managed terminal (visible in a collapsible drawer under the preview). Then run `npm run dev` or `npm start`. Capture the port from stdout. Point the iframe at `http://localhost:<port>`.
- **python-flask**: create `.venv`, install `requirements.txt`, run `python app.py`. Capture port. Point iframe.
- A persistent **Run controller** in the right pane: ▶ Run / ⏸ Pause / ↻ Restart / ⏏ Stop / log drawer. Same component for every stack.
- The preview iframe is sandboxed (`sandbox="allow-scripts allow-same-origin allow-forms"`). Hot-reload comes free with Vite/Next/Flask debug.
- **Errors visible**: if `npm install` fails, surface the failing line in the run controller card with a "Copy log" button. Do not just toast "Run failed."
- **"Open in browser"** opens the same `localhost:<port>` URL externally; **"Open folder"** opens the file manager at the workspace; **"Open in VS Code"** invokes `code <workspace>` if VS Code is on PATH (and silently no-ops if not — display a "VS Code not detected" hint once).

**Constraints on what we ship inside the app**:
- Already shipped: bundled Python runtime via `externalBin` and an `.exe` sidecar.
- **Missing**: bundled `node` runtime. Two options — (a) require user to have Node 18+ installed and detect-and-warn cleanly, or (b) bundle a small portable Node alongside the Python sidecar (~50 MB on Windows). Option (a) is the pragmatic v1; (b) is the only way to truly beat Lovable's zero-install promise.
- **Missing**: a process supervisor. The current `sidecar.rs` manages exactly one Python child. We need a generic `LocalProcessSupervisor` keyed by `(workspace, stack)` that starts, stops, restarts, captures stdout/stderr, and emits `preview:event` to the frontend.

### 11.4 Fully wired & enforced SignalOS — every command lives, every rule bites

The user's directive: **every command in the catalog must actually do work, and every rule the product names must actually block**. Today 15 of 37 commands just dump the spec md file (not wired), and the product names governance rules in its README and `CONSTITUTION.md` but enforces almost none of them in the UI (not enforced). Both are deal-breakers for v1.

What "fully wired" means, per command class:

- **Ready commands** (`/signal-status`, `/signal-init`, `/signal-brain`, `/signal-qa`, `/signal-plan`, etc.) — already working. Keep.
- **Advanced commands** (`/signal-learn`, `/signal-cso`, `/signal-autoplan`, `/signal-deploy`, etc.) — `direct` set in `signalos_ipc_server.py:160-179` forwards them to the bundled core. They run but their UI is generic. Each must declare its **phase + substep contract** (see §11.2) so the progress UI is meaningful, not a raw stdout dump.
- **Preview commands** (`/signal-build`, `/signal-debrief`, `/signal-design`, `/signal-design-html`, `/signal-design-review`, `/signal-discovery`, `/signal-observe`, `/signal-onboard`, `/signal-pause`, `/signal-pre-design`, `/signal-pre-wave`, `/signal-review`, `/signal-ship`, `/signal-wave-review`) — **each must be wired to a concrete pipeline**, the same way the Builder is wired today: a prompt template, a JSON output contract, file writes, audit entries, and a phase/substep declaration. Until that happens, they should be **hidden from the catalog**, not labeled "Preview."

The "fully wired" work is the single largest unfinished pillar of the product. Plan section is rough order of magnitude:

| Command surface | Phases | Estimated wiring effort | Pipeline analogue |
|---|---|---|---|
| `/signal-discovery` | Prepare / Discover / Synthesize / Save | 1 day | Builder, but writes discovery brief + interview questions |
| `/signal-pre-wave` | Prepare / Outline / Sign / Save | 0.5 day | Builder, lighter |
| `/signal-design` | Prepare / Sketch / Critique / Save | 2 days | Builder, two AI passes |
| `/signal-design-html` | Prepare / Generate HTML mockup / Open preview | 1 day | Builder, generates static preview |
| `/signal-design-review` | Prepare / Review against gates / Score / Save | 1 day | Plan analogue |
| `/signal-build` (governed) | Prepare / Plan / Build / Review | already in Builder; rename + dedupe | Existing builder |
| `/signal-debrief` | Read history / Summarize / Save retro | 1 day | Plan analogue |
| `/signal-discovery` … `/signal-ship` | each declares its own substeps | 6-10 days total | All Builder-shaped |
| `/signal-review` | Read change / Critique / Score / Save | 1 day | Code-review style |
| `/signal-pause` / `/signal-onboard` / `/signal-observe` | Lightweight: read state, record action | 0.5 day each | State write + audit |

Hard rule: **no command appears in the catalog unless it has a wired pipeline and a declared phase contract**. The "Preview" pill is removed entirely.

### 11.4b First-run onboarding wizard — must-have setup, guided

Today's "first-run" is implicit: the user lands on the Build view with all 4 steps in `pending` state and is expected to drift through them. There is no wizard. The result is the silent-destructive `/signal-init --force` problem (§2.1, §2.2) and the half-finished AI connection problem (§2.3). A real wizard fixes all five of those at once and gives the product a Lovable-grade first impression.

**The wizard is a 6-step modal that runs the very first time the app launches** (and re-opens automatically if the user force-quit mid-flow). Steps are forward-only-by-default, with a `← Back` link. Each step persists its decision to disk before `Continue` enables — so closing the app mid-wizard never leaves the user in a half-state.

**The 6 steps:**

1. **Welcome.** One paragraph + a 4-bullet list of what the wizard will set up. Two buttons: `Skip for now` (lands on Build with a persistent "Finish setup" topbar banner) and `Continue →`.
2. **Project folder.** Browse-to-pick. Real-time checks: folder exists, writable, empty-or-not, list of existing top-level entries. The only step where `Skip` is **not allowed** — SignalOS needs a target.
3. **Init consent.** Names exactly what SignalOS would add/replace/never-touch in the chosen folder. Four radio choices:
   - **Full SignalOS** — write the entire bundle (today's `--force` behavior, but only with explicit consent here).
   - **Keep my files** — write the bundle but skip overwriting any pre-existing file with a matching path. Default if folder is non-empty.
   - **Minimal** — only `.signalos/` runtime state; no command library or integrations. Matches the existing `signalos init --minimal` flag.
   - **Skip for now** — initialize later from Settings.
4. **AI provider.** Provider combobox (all 12, type-ahead), API key field, `Test connection` button that sends a **real chat ping** (1-token max output), then enables model fetch + selection. "Use Ollama" link for the local path. Green check requires an actual chat round-trip, not a `/models` listing.
5. **Budget + privacy.** Monthly USD budget (default $10), three checkboxes: redact .env from prompts (default ON), block secret/database/cert file uploads (default ON), local-AI-only (default OFF; auto-checked if user picked Ollama in step 4).
6. **Done.** Summary card: project, AI, budget, init mode. CTA = `Start building →`. Shows 2-3 example prompts to remove cold-start friction.

**Persisted state:**

- `app_config_dir/onboarding.json` carries `{ completedSteps, version }`. The wizard reads this on startup and resumes at the first incomplete step. Version field lets future releases force-rerun specific steps (e.g. "we added a new privacy setting").
- A `Reset onboarding` button in the new Settings drawer wipes this file and re-opens the wizard.

**Design rules:**

- Modal, full-window, no shell visible behind it. The user cannot click into the chat or files until the wizard closes.
- Step indicator across the top (6 dots, current solid). Always visible.
- Each step's `Continue` button is disabled until server-side validation passes — folder must canonicalize and be writable; AI test must succeed; budget must be a non-negative number.
- The wizard owns folder-creation: if the user types a path that doesn't exist, the step offers `Create this folder` instead of `Continue`.
- The wizard's `Test connection` reuses the same code path the main shell uses — no special test-only path. So if it works in the wizard, it works in chat.
- Every wizard decision becomes the state the main shell reads on first render. No second source of truth.

**This wizard collapses §2.1, §2.2, §2.3, §2.4, §5 (redact JS-side reports) into one gated flow.** It is the single highest-leverage UI change in the redesign — it is also entirely additive (no existing screen needs to be modified to ship it).

### 11.4c Secrets manager — Replit parity, with .env diff

Today's Secrets view ([§4.9](#49-the-secrets-view-cannot-remove-or-list-a-secrets-values-can-only-overwrite)) can only add/overwrite; it cannot delete, edit, reveal, or bulk-import. A user who pastes a wrong key has to leave the app and edit `.env.local` by hand — exactly the friction the installer-first promise was supposed to remove.

The new Secrets pane should match Replit's Secrets surface and add a `.env` diff-import that Replit doesn't have. It lives in the left pane's **Files** tab as a top-level `🔒 Secrets` entry, or in the Settings drawer — both routes open the same surface.

**Surface:**

- Header: `[ + New secret ]` and `[ Edit as .env ]` buttons; below them a file picker (`.env / .env.local / .env.development / .env.production / .env.test / Custom…`) and a search input.
- One card per secret. Card shows: name, masked or plaintext value, `[Reveal] [Copy] [Edit] [×]`, last-updated relative time, and a small status line — `used by AI: blocked` (default), or `public-prefixed · not redacted` for `NEXT_PUBLIC_*`/`VITE_*`/`REACT_APP_*`/`EXPO_PUBLIC_*`.
- An empty-state line at the bottom: `+ Add another secret`.
- A footer row with `[ Import from .env file ]` (native file picker → Edit-as-.env modal pre-populated) and `[ Export visible to clipboard ]` (only revealed values come through plaintext; hidden ones export as `KEY=<redacted>`).

**Add secret modal:** name (UPPER_SNAKE_CASE, validated), value (password-masked with eye toggle to peek-while-typing), target .env file (combobox), `[Save]`. Validation reuses the existing `normalize_secret_name` and `normalize_secret_filename` in [ipc.rs:1005-1029](../src-tauri/src/ipc.rs#L1005-L1029).

**Edit as .env modal:** textarea pre-filled with the current file contents (values revealed only after the user clicks `Reveal current values`, otherwise masked). User edits → diff appears live below: `+` added, `~` changed, `=` unchanged, `−` removed. Removals require an explicit `[✓] I understand N secrets will be removed` checkbox before `Apply diff` enables. Comments and blank lines are preserved.

**Behavior rules:**

1. **Reveal is opt-in and time-limited.** Click `Reveal` → value shows for 10 seconds → auto-hides. Each reveal writes a `secret:reveal { name, file, ts }` line to `.signalos/AUDIT_TRAIL.jsonl`.
2. **Public-prefixed variables are visible by default.** No reveal needed; they're framework-shipped to the browser anyway. UI states `public-prefixed · not redacted in chat`.
3. **Sensitive-named keys (`SECRET|TOKEN|PASSWORD|KEY|URL`) keep the mask** and are always redacted in chat and exports.
4. **Edit supports rename.** Rename = delete + upsert in one transaction. Value-only edit = upsert.
5. **Delete confirms** with a 5-second `Undo` toast that re-writes the prior value.
6. **OS-keychain collision detection.** If a name matches a provider keychain key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.), the card shows a warning and links to the AI section — those belong in the OS keychain, not in `.env.local`.
7. **Order preserved.** New secrets append at the end of the file; edits stay in place. Comments and blank lines round-trip.

**New IPC commands needed (the current `upsert_workspace_secret` is not enough):**

| Command | Purpose |
|---|---|
| `list_workspace_secrets(filename)` | Return `[{ name, masked_value, public_prefix, updated_at, file }]`. Public-prefixed values unmasked. |
| `reveal_workspace_secret(name, filename)` | Return plaintext once. Log to audit. Rate-limit 10/min per workspace. |
| `delete_workspace_secret(name, filename)` | Remove the matching line; preserve comments + ordering. |
| `apply_workspace_env_diff(filename, env_text, allow_removals)` | Parse pasted block, diff vs current, return planned changes; on confirm apply atomically (temp file + rename). |

Frontend `ipc.secrets` gains matching methods: `list / reveal / delete / applyDiff`. `secrets.upsert` stays for single Add.

**Atomic writes:** `apply_workspace_env_diff` writes to `<file>.tmp` → `fsync` → `rename`. Failure leaves the original untouched. Same pattern for `delete_workspace_secret`. The current `upsert_workspace_secret` uses `std::fs::write` which is not atomic on power-loss — should be migrated to the same temp-file pattern.

**Security:**

- `reveal_workspace_secret` is the **only** path that returns plaintext values to the webview.
- The pasted .env text in `Edit as .env` lives in the frontend only until Apply; it never reaches the Python sidecar.
- The Rust handler validates each parsed line against `normalize_secret_name` and `normalize_secret_filename` — same guards as today's upsert.
- A future enhancement (P2): per-secret expiry — a `.signalos/secret-meta.json` sidecar file records `{ name, file, expires_at }` and the UI flags stale secrets. Out of scope for v1.

This collapses the §4.9 papercut, gives users the Replit affordance they expect, and turns the Secrets view from "add-only" into a real secrets manager.

### 11.4d Enforcement model — what "enforced" means in the UX

"Enforced" is not a doc-page promise. It is a set of UI behaviors that **prevent or audit** every action that would violate SignalOS governance. Each rule has three modes: `strict` (block, with audited override), `warn` (allow, show a yellow warning), `off` (allow silently). v1 ships **strict by default**; the wizard exposes a per-project Settings switch to loosen specific rules with an audit entry recording who loosened what.

#### The eight enforcement rules

1. **Gate gating on Build.** The Build button is disabled if the wave's required gates aren't signed. The current required set is `G0 Constitution + G1 Belief + G2 Expectation Map` before any Build runs. Tooltip on the disabled button: "Sign G2 Expectation Map first — open Gov tab." A single click navigates left-pane → Gov → focus on the unsigned gate. Override path: `Build anyway (audit)` → modal asks for a one-line reason → audit entry `enforcement:override { rule: gate-gate, gate: G2, actor, reason, ts }`.

2. **Plan gating on file writes.** Any file the Builder is about to write must trace to a task in the current wave's `PLAN.md`. If the Build's plan output declares tasks the wave plan doesn't know about, the file-diff preview surfaces them in a `New tasks` strip — the user picks `Add to PLAN.md` (which appends them and re-signs G3 Plan if applicable) or `Build off-plan (audit)`.

3. **Trust-tier enforcement.** Tasks declared T1 (high blast radius) require an explicit `[✓] I confirm this is T1 work` checkbox before Build runs. T2 just warns. T3 is silent. The bundled core already records trust tiers in `PLAN.md`; the UI must read them and act on them.

4. **Audit append is mandatory and atomic.** Every action that changes project state (file write, secret write, gate sign, wizard step complete, enforcement override, init mode chosen) appends one line to `.signalos/AUDIT_TRAIL.jsonl` *before* the action completes. If the append fails, the action fails. No state-change can outrun its audit entry. This kills the current race-prone implementation in [governance.rs:136-150](../src-tauri/src/governance.rs#L136-L150).

5. **Secret-file write block, not just redact.** Today secrets get redacted in chat output. Enforcement adds a hard rule: the Builder cannot write files named `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, or paths under `.signalos/secrets/` even with a confirm. The reserved-path check in [ipc.rs:991-1003](../src-tauri/src/ipc.rs#L991-L1003) already covers this — it just needs to be visible in the UI ("SignalOS refused to write `.env` — secrets go through the Secrets pane").

6. **Role enforcement on gate signing.** Today any signer name signs any gate. Enforcement requires the signer's role to match the gate's owner declared in `CONSTITUTION.md` (PO signs G0/G1/G3, PE signs G3/G4, QA signs G4/G5, DevOps signs deploy). A role is selected in the wizard step 4.5 (new) and persisted to `.signalos/identity.json`. Override path same as gates.

7. **Stack contract enforcement on Build output.** The current `validateGeneratedFiles` ([app.js:2376-2393](../src/js/app.js#L2376-L2393)) is wired but its failure mode is "the user re-runs Build and pays tokens again." Enforced version: when the AI returns files that miss the stack contract, the Builder **silently issues a corrective follow-up prompt** to the same provider with the missing-files list, up to 2 retries. Only after the third miss does it surface an error. The user sees "Build · attempt 2/3" in the progress strip — never a raw "files missing" toast.

8. **Wave freeze.** When a Quality Check gate (G5) is signed, the wave enters a `frozen` state. The next Build is blocked with a `Wave frozen — start Wave N+1?` prompt. Today `/signal-freeze` and `/signal-unfreeze` exist in the command catalog but are unwired ([app.js:28-30](../src/js/app.js#L28-L30)); enforcement wires them and makes the UI respect the frozen state.

9. **Test-first for beliefs (test-automation rule).** Every Belief must have automated verification criteria *before* implementation begins. The test is the specification. Surfaces in the UI as: when the user signs G1 (Belief), the gate-sign modal requires linking ≥1 test file or test plan entry. Override path lives; audit entry records the unverified belief.

10. **Gate compliance is binary (test-automation rule).** No artifact advances to the next environment without passing ALL test-automation gates for that layer. In practice: Build cannot promote files until L0+L1 gates pass; Run cannot deploy a preview until L2 gates pass. The progress strip surfaces the failing gate.

11. **Zero manual regression (test-automation rule).** If a defect is found manually, it's a testing gap. An automated test MUST be written before the fix merges. Tracked in `.signalos/test-debt.jsonl`. The UI exposes a "Test Debt" entry in the left-pane **Gov** tab so the user sees their own gap list.

12. **Mutation threshold (test-automation rule).** Generated business-logic code must achieve ≥ 95% mutation score. Framework/boilerplate code is exempt by path-glob. Builder writes are gated on this score for files matching `src/**/*.{ts,tsx,js,jsx,py,rs}` excluding `**/{node_modules,vendor,_generated}/**`. Below threshold → "Mutation score 72% — add tests for: <list>". Override path: log and continue.

Rules 9-12 come from the test-automation module ([docs/test-automation/00-INDEX.md](test-automation/00-INDEX.md)). They are wired in Wave 5 — see §11.6.

#### Visible enforcement in the topbar

A new pill renders in the topbar between `provider` and `cost`:

```
…Sonnet 4.7  · 🛡 Enforced  · $0.12 …
```

Click the pill → a popover lists the eight rules with their current mode (strict/warn/off) and the count of overrides this wave. Color shifts:

- 🛡 Green — all strict, no overrides this wave.
- 🛡 Amber — all strict, ≥1 override this wave.
- ⚠ Amber — at least one rule loosened.
- ⚠ Red — all rules off (the user has effectively turned governance off and the product is now advisory; topbar shows red until they re-enable).

#### Override audit format

Every override writes to `.signalos/AUDIT_TRAIL.jsonl`:

```json
{"ts": "2026-05-15T14:02:11Z", "action": "enforcement:override",
 "actor": "Samer Z.", "rule": "gate-gate", "context": {"gate": "G2"},
 "reason": "Spike for design exploration — will sign G2 before merging.",
 "wave": "Wave 1", "phase": "Discovery"}
```

The History view filters by `action: enforcement:override` so the user can review their own overrides at any time. Exporting a handoff includes the override list with reasons — that's the audit story the product was always promising.

#### What enforcement is *not*

- It is not a brick wall. Every rule has a labeled override path. The product respects the user's decision to override; it just records it.
- It is not surprise enforcement. The wizard step 6 ("you're ready") becomes: "you're ready — the first Build will require signing G0 Constitution and G1 Belief. Want to sign them now, or later when you hit Build?"
- It is not optional in v1. The whole positioning shifts if these rules ship as off-by-default. The wizard ships strict; the per-rule loosen control lives in Settings.

#### What this adds to the work plan

Five new pipelines:
- Identity + role assignment (wizard step 4.5, persisted to `.signalos/identity.json`).
- Atomic audit-append in Rust (replaces [governance.rs:136-150](../src-tauri/src/governance.rs#L136-L150)).
- The enforcement pill + popover in the topbar.
- The per-rule override modal + audited entry.
- Self-healing Builder retries (rule 7) using the existing `validateGeneratedFiles` shape.

All five slot into Wave 3 — see §11.6.

### 11.5 New issue inventory — what this redesign forces us to fix that wasn't in §1-§10

These are issues that are **only visible once you accept the new requirements above**. They were not in the original review because the current product never tried to do these things.

#### P0 — required for the new UX to exist at all

1. **No in-app preview/run pane** — `sidecar.rs` manages exactly one Python child. To run a generated React app we need a generic process supervisor that can spawn `npm install`, `npm run dev`, `python app.py`, etc. and stream logs back to a frontend panel. **New module needed: `src-tauri/src/runtime.rs`.**
2. **No bundled Node runtime; no Node detection** — picking Vite/Next/Express today silently assumes Node is on PATH. The app must either (a) detect Node 18+ at startup and surface a clear install link, or (b) bundle a portable Node like we bundle Python.
3. **No iframe-aware CSP** — current CSP forbids `frame-src`. To embed a local-server iframe we must add `frame-src http://localhost:* http://127.0.0.1:*` (and `connect-src` for HMR websockets).
4. **No structured progress stream from the sidecar** — today the Python sidecar returns one final response per command. We need NDJSON `progress` events emitted mid-command, carrying `{ id, phase, substep, state, detail }`. Both `signalos_ipc_server.py` and `sidecar.rs` must learn to multiplex `progress` events without confusing `response`.
5. **Builder phase contract is ad-hoc** — `BUILDER_PHASES` in [app.js:153-158](../src/js/app.js#L153-L158) is one constant. We need a typed `PhaseContract` shared across commands.

#### P1 — required for "beats Lovable" credibility

6. **No file-diff / file-preview before write** — `write_workspace_files` in [ipc.rs:328-405](../src-tauri/src/ipc.rs#L328-L405) writes immediately. We need a `preview_workspace_files(files)` that returns a diff per file (existing vs. new) and lets the UI ask for explicit confirm before `write_workspace_files` fires.
7. **No undo / restore** — once a Build writes, there's no way to revert. Either (a) git-commit before/after on the user's repo, or (b) keep a per-session snapshot in `.signalos/snapshots/<ts>/`.
8. **No file tree in the UI** — Lovable shows generated files in a tree. SignalOS lists "artifacts" in a flat list and doesn't show what the AI just wrote vs. what was already there.
9. **No streaming AI tokens** — `send_provider_message` returns the full response on completion. To make Plan/Build *feel* fast (which is what Lovable users love), tokens must stream to the UI. Anthropic supports SSE; OpenAI supports SSE; Gemini supports streaming. Implement once in `provider.rs`, expose as `chat_stream`.
10. **No "regenerate just this file"** — if Build writes 8 files and one is wrong, the user has to re-run the whole Build. Lovable lets you ask for a single-file edit. Need a `/signal-edit-file <path>` flow with diff preview.
11. **No multi-step conversation in the Builder** — every Build call is one-shot. The user has no way to say "looks good, but make the button bigger" without re-stating the whole prompt. Need a Builder-aware chat history, persisted per project under `.signalos/builds/<ts>/conversation.jsonl`.

#### P2 — required to ship "fully wired SignalOS"

12. **15 commands are labeled Preview** — must either wire each (see §11.4 table) or hide from the catalog.
13. **Two commands (`signal-pause`, `signal-pre-wave`, etc.) appear in the catalog but are missing from the sidecar's `direct` set** — already noted in §1.3; surfaces here because the new rule is "wired or hidden."
14. **Phase/substep contracts are not yet a thing in the sidecar** — every CLI command in `signalos_lib/commands/*` must declare its substeps and emit `progress` events. This is a sweep across 30+ files.
15. **No central "command runner" surface** — today the user picks a chip in Chat or runs a Build. There is no consistent place where "I want to run /signal-qa with these args" feels natural. Build's progress UI must generalize to any wired command.

#### P3 — supporting work that pays off downstream

16. **The split-pane layout must survive resize** — today `max-width: 1040px` hides the sidebar. The new three-pane layout needs to gracefully collapse to two (work + preview) at narrow widths and to one (with a top tab switcher) at <800px.
17. **The CSP needs to be re-tightened after iframe allowance** — adding `frame-src` widens attack surface. Drop `script-src 'unsafe-inline'` at the same time and adopt a nonce-based CSP.
18. **Provider routing layer needs a `chat_stream`** — see #9.
19. **Settings becomes a drawer** — move all of view-settings into a slide-over panel, free up the center pane for chat/preview.

### 11.6 Updated priority view — the only fix list that matters

The original §8 fix list stands. This is the **superset** the user has now asked for. The two lists merge into the following sequence:

```
WAVE 1 — Stop bleeding · ship the wizard
  Phase: Stabilize
    G0 / 1   Stop --force on /signal-init           (§2.1)
    G0 / 2   First-run onboarding wizard            (§11.4b)
    G0 / 3   Real AI test with chat ping            (§2.3)
    G0 / 4   Refresh provider defaults              (§2.4)
    G0 / 5   Redact JS-side reports                 (§5 + Appendix B/2)
    G0 / 6   Replit-style Secrets manager           (§11.4c)

WAVE 2 — New shell · chat + preview + governance
  Phase: Build
    G1 / 7   Three-pane layout (left collapsible)   (§11.1)
    G1 / 8   Files / Gov / Mem left tabs            (§11.1)
    G1 / 9   LocalProcessSupervisor in Rust         (§11.5 / 1)
    G1 / 10  Node detection or bundled Node         (§11.5 / 2)
    G1 / 11  Iframe preview pane + Run controller   (§11.3)
    G1 / 12  Progress event stream from sidecar     (§11.5 / 4)
    G1 / 13  PhaseContract + Style A renderer       (§11.2)

WAVE 3 — Fully wired & enforced SignalOS
  Phase: Land
    G2 / 14  Streaming AI tokens                    (§11.5 / 9)
    G2 / 15  File diff preview before write         (§11.5 / 6)
    G2 / 16  File tree with diff badges             (§11.5 / 8)
    G2 / 17  Per-file regenerate                    (§11.5 / 10)
    G2 / 18  Builder-aware conversation history     (§11.5 / 11)
    G2 / 19  Wire the 15 placeholder commands       (§11.4 table)
    G2 / 20  Identity + role assignment             (§11.4d rule 6)
    G2 / 21  Atomic audit-append in Rust            (§11.4d rule 4)
    G2 / 22  Enforcement pill + popover in topbar   (§11.4d)
    G2 / 23  Per-rule override modal + audit entry  (§11.4d)
    G2 / 24  Self-healing Builder retries           (§11.4d rule 7)
    G2 / 25  Gate gating + plan gating on Build     (§11.4d rules 1, 2)
    G2 / 26  Wave freeze respected by UI            (§11.4d rule 8)

WAVE 4 — Polish · before signed release
  Phase: Harden
    G3 / 20  Confirmation dialogs (destructive)
    G3 / 21  Real Stop (cancel in sidecar)
    G3 / 22  HTTP timeouts on every reqwest call
    G3 / 23  Anthropic max_tokens raise
    G3 / 24  Audit append fix (atomic + truly append)
    G3 / 25  CSP tighten (nonce-based, drop unsafe-inline)
    G3 / 26  Accessibility (ARIA tabs, keyboard nav)
    G3 / 27  Narrow-window layout (no nav loss)

WAVE 5 — test-automation · zero manual testing
  Phase: Verify   ·   Spec: docs/test-automation/
    G4 / 28  Layer 0 dev-machine gates                (lint, format, type, secret scan, affected unit tests)
    G4 / 29  Layer 1 CI pipeline                      (full tests, integration via testcontainers, contract, schema fuzz, mutation, SAST, SCA, license, image scan)
    G4 / 30  Layer 2 CD test env                      (deploy, migrate, E2E UI, visual regression, a11y, API smoke, perf baseline)
    G4 / 31  Layer 3 CD preprod                       (full load, DAST, chaos, migration dry-run, cross-env contract verify)
    G4 / 32  Layer 4 production canary                (10% traffic, error rate compare, latency, business KPI, auto-rollback, progressive rollout)
    G4 / 33  Layer 5 continuous validation            (synthetic monitors, SLO burn rate, anomaly detect, dep health, cert expiry, resource trends)
    G4 / 34  Layer 6 nightly deep validation          (extended chaos, full pentest, soak, data audits, backup verify, cert rotation dry-run)
    G4 / 35  Test data management infrastructure      (factories, seeding, isolation, cleanup, compliance)
    G4 / 36  Wire enforcement rules 9-12 to §11.4d    (test-first, gate compliance, zero manual regression, mutation threshold)
    G4 / 37  Metrics dashboard + maturity model       (KPIs, flaky management, coverage decay alerts)
    G4 / 38  Migration roadmap from current state     (per docs/test-automation/13 — Manual → Zero)
```

Wave 5 applies test-automation to **two** scopes: (a) SignalOS App itself — the Tauri desktop binary — where Layer 0-3 fully apply and Layers 4-6 apply in adapted form (no Kubernetes, but crash telemetry, update-channel canary, and signed-installer smoke replace them); and (b) every project SignalOS App *builds*, where the full chain applies depending on stack. The Builder declares the test-automation contract for the generated stack (e.g. React/Vite gets unit + E2E + visual regression by default), writes the corresponding workflow files into `.github/workflows/`, and refuses to mark a Build "done" until the Layer 0+1 gates green-light. This is what enforcement rules 9-12 in §11.4d *mean* in practice.

The user's instruction "**fully wired & enforced**" means Wave 3 is not optional, and the eight enforcement rules in §11.4d are not optional. The user's instruction "view the work and run it" means Wave 2 is not optional. The user's instruction "beats Replit or Lovable" means the three-pane layout, streaming tokens, and the first-run wizard are not optional. Each wave ends at its named Gate; nothing in the next wave starts until the gate signs — and the gates themselves are now enforced by the rules in §11.4d, so this is not metaphor.

### 11.7 What we get after Wave 4 — the v1.0 deliverable

After Wave 4 signs at **G3 Plan**, SignalOS App is at the state we'd label `signed-ready SignalOS App v1.0`. The product positioning that has been aspirational throughout this review is now **literally true** of the code.

**For the end user, on first launch:**

- The first-run wizard walks them through folder, init consent, AI provider (with real chat test), budget, and privacy in ~90 seconds.
- They never get their folder silently overwritten. Three init modes (Full / Keep my files / Minimal) and a `Skip for now` exit, all consented to, all audit-logged.
- They paste a key once; it lives in their OS keychain; it's redacted out of every export and prompt.
- They see all 12 providers in one searchable combobox, with model lists fetched live from each provider's `/models` endpoint, with provider defaults that point to current-generation models.

**For the end user, when they build:**

- They type "build a todo app", click Build, and watch a four-phase progress strip with live substeps and streaming AI tokens. They always know what's done, what's happening, and what's coming.
- They see the file diff *before* anything writes to their folder. They click "Apply 8 files" or "Cancel" or "Regenerate just `src/App.jsx`".
- Once files write, the right-pane iframe spins up `npm install` → `npm run dev` automatically, captures the port, and shows the running app. `Run / Pause / Restart / Stop` lives there permanently. `Open in browser`, `Open folder`, `Open in VS Code` are one click.
- If the AI returns a file bundle missing the stack contract, SignalOS *silently* asks for a corrective response up to 2 times. The user sees "attempt 2/3" — never a raw "files missing" error.
- They can ask "make the button bigger" as a follow-up. The Builder reuses the conversation history per project; they don't re-state the whole prompt.

**For the end user, when SignalOS enforces:**

- Build is blocked until `G0 Constitution + G1 Belief + G2 Expectation Map` are signed in the current wave. The disabled button explains why and one-clicks them to the unsigned gate.
- Every file write traces to a task in `PLAN.md`. New tasks generated by the Builder either go *into* PLAN.md (re-signing G3) or get marked `off-plan (audit)` with a logged reason.
- Trust-tier T1 tasks demand explicit confirmation.
- Gates can only be signed by the role declared in `CONSTITUTION.md`. Role identity is set up in the wizard.
- Every state change appends to `.signalos/AUDIT_TRAIL.jsonl` *before* the action completes. The audit append is atomic. There is no path through the product that mutates state without an audit entry.
- The topbar pill (🛡 Enforced) always tells the user whether they're in strict mode, whether they've overridden anything, and how many overrides this wave.

**For the end user, when SignalOS commands run:**

- All 37 commands in the catalog do work. None say "Preview — execution is not wired yet." The 15 previously-stubbed commands (`/signal-build`, `/signal-design`, `/signal-design-html`, `/signal-design-review`, `/signal-discovery`, `/signal-debrief`, `/signal-observe`, `/signal-onboard`, `/signal-pause`, `/signal-pre-design`, `/signal-pre-wave`, `/signal-review`, `/signal-ship`, `/signal-wave-review`, and the missing-from-direct-set ones) each have their own declared phase contract and substeps in the progress strip.
- Long-running commands (`/signal-qa`, `/signal-plan`, `/signal-orchestrate`) emit progress events. The engine ping no longer flakes during a long command. The `Stop` button actually stops the sidecar subprocess.
- The Replit-style Secrets pane lists, reveals (audited), edits, deletes, and bulk-imports `.env`-style secrets with diff preview. Atomic writes throughout.

**For the end user, on quality:**

- No CSP `'unsafe-inline'` for scripts. Nonce-based CSP. Iframe preview lives inside a tight `frame-src http://localhost:*` allowlist.
- Every reqwest call has a timeout. Hung providers surface as "request stalled — retry?" instead of spinning forever.
- Anthropic `max_tokens` is set per-model. Builder output won't truncate.
- All destructive UI actions confirm. Forget project, Delete saved key, Reset session, Delete secret, Override enforcement — every one has a confirm + an audit entry.
- Keyboard nav and ARIA tabs match the WAI-ARIA tab pattern. Narrow windows degrade to a top-tab switcher instead of hiding the sidebar entirely.

**For the end user, on truth-in-advertising:**

Every Appendix B claim — "redacted issue-report export", "OS keychain storage", "secret summary", "multi-provider AI", "engine diagnostics", "beta/stable update channel", "no Python/Rust/Node needed" — is now *fully* true. There are no asterisks left.

#### What's still external (not in Wave 4 — release gates, not implementation gates)

These are intentionally outside the implementation pass. They block calling the product `signed public beta`, not `signed-ready v1.0`:

1. **Code signing** — Windows Authenticode cert, macOS Developer ID cert, signed installers.
2. **Notarization** — macOS notarytool round-trip, gatekeeper validation.
3. **Updater signature population** — minisign signatures filled into `beta.json` and `latest.json`. The pubkey is already shipped.
4. **Clean-machine VM validation** — fresh Win11 VM, install signed installer, run the wizard, build an app, sign a gate, no SignalOS dev environment present.
5. **Public download landing** — the page in `distribution/landing/` needs the release URLs wired to the signed assets.

These are checklisted in [docs/CLEAN_MACHINE_VALIDATION.md](CLEAN_MACHINE_VALIDATION.md) and [SIGNING.md](../SIGNING.md). They run **after** Wave 4 signs.

#### What's explicitly out of scope for v1.0

These are real product features, deliberately not in Wave 1-4. They become Wave 5+:

- **Multi-project / project switcher.** v1.0 is one workspace at a time. Switching projects = `Forget` + `Choose folder`. Wave 5 adds a left-pane project picker with recent-projects history.
- **Team collaboration.** No multi-user, no shared waves, no co-signing. Audit is single-actor.
- **Cloud sync of plan/wave state.** Everything is local. No central server. No account.
- **Plugin marketplace.** The bundled core supports plugins via `signalos_lib.registry`, but the desktop app doesn't expose an install/browse UI yet.
- **VS Code / JetBrains extension integration.** The IDE-emitter scaffolding exists in `_bundle/integrations/`, but the desktop app does not yet co-ordinate state with an IDE extension running in parallel.
- **Custom enforcement rules.** v1.0 ships the eight rules in §11.4d as a fixed set. Wave 5+ exposes per-project rule authoring in `.signalos/enforcement.json`.
- **Diff-aware re-prompt for non-Build commands.** Only `/signal-build` gets the file-diff preview in Wave 2. `/signal-design`, `/signal-design-html`, etc. write whatever they produce. Wave 5+ generalizes the diff gate to every command that mutates the workspace.
- **Bundled Node runtime.** v1.0 detects Node 18+ on PATH and surfaces a clear install link if missing. Bundling a portable Node (~50 MB) is Wave 5+.

#### The pitch after Wave 4

> "SignalOS App v1.0 is the first installer-first AI builder that ships with its governance built in. You describe an app. SignalOS plans it under your wave, writes it under your gates, runs it in your preview pane, and audits every action — including the times you decide to override the rules. Your folder, your keys, your budget, your audit trail. No cloud, no account, no surprises."

That's what we get after Wave 4.

---

---

## 12. What actually shipped (reference of record)

This section is the **reference of record** — written after the v1.0 push completed, not before. §11 describes the plan; §12 describes what actually landed on disk. If the two diverge, §12 is the source of truth.

### 12.1 New Rust modules

| Path | Lines | Purpose | Public IPC commands |
|---|---|---|---|
| [src-tauri/src/runtime.rs](../src-tauri/src/runtime.rs) | ~440 | `LocalProcessSupervisor` — spawns `npm install` / `npm run dev` / `python app.py`, captures localhost port via regex, atomic stop. Probes Node 18+ on PATH. | `probe_node`, `start_preview`, `stop_preview`, `list_previews`, `get_preview` |
| [src-tauri/src/enforcement.rs](../src-tauri/src/enforcement.rs) | ~360 | 12-rule enforcement engine (Strict / Warn / Off per rule). Build precheck refuses unsigned-gate state and frozen-wave state. Override path logs audit entry. | `get_enforcement_state`, `build_precheck`, `override_rule`, `set_rule_mode`, `freeze_wave`, `unfreeze_wave` |
| [src-tauri/src/test_automation.rs](../src-tauri/src/test_automation.rs) | ~290 | Test-automation rules 9–12 wired: test-first gate, mutation threshold (≥95%), test-debt store. | `list_test_debt`, `add_test_debt`, `resolve_test_debt`, `check_mutation_threshold`, `check_test_first` |

### 12.2 New Rust IPC commands in existing modules

In [src-tauri/src/ipc.rs](../src-tauri/src/ipc.rs):
- `read_workspace_file` (sandboxed, 2 MB cap)
- `list_workspace_dir` (sandbox-bounded, hidden-dir skip list)
- `preview_workspace_files` (dry-run diff: new / modified / unchanged + per-file status)
- `list_workspace_secrets`, `reveal_workspace_secret` (audited), `delete_workspace_secret`, `apply_workspace_env_diff` (atomic temp+rename)
- `set_identity`, `get_identity`, `check_role_for_gate` (`.signalos/identity.json`)
- `audit(action, detail)` private helper now wired into `set_workspace`, `write_workspace_files`, `write_workspace_export`, `upsert_workspace_secret`

In [src-tauri/src/provider.rs](../src-tauri/src/provider.rs):
- `send_provider_message_stream` — streaming chat for all 12 providers via `StreamEmitter`. Anthropic SSE / OpenAI-compat SSE (9 providers) / Gemini SSE / Ollama NDJSON.
- `http()` singleton with 10s connect + 60s total timeouts (replaced 9 ad-hoc `reqwest::Client::new()` call sites).
- `anthropic_max_tokens_for(model)` per-model (Opus 4.7 = 64K, Sonnet 4 / Opus = 32K, Haiku = 16K, fallback 8K). Replaced hardcoded 8192.

### 12.3 Rust unit tests grew from 16 to 32

New tests in `test_automation.rs`:
- `mutation_threshold_passes_at_or_above_95`, `mutation_threshold_refuses_below_95`, `mutation_threshold_boundary_at_threshold`
- `test_first_refuses_empty_refs`, `test_first_refuses_only_whitespace_refs`, `test_first_accepts_any_nonempty_ref`
- `ensure_test_debt_creates_file`

New tests in `provider.rs`:
- `opus_4_7_gets_64k`, `sonnet_4_gets_32k`, `haiku_gets_16k`, `unknown_model_gets_8k`, `case_insensitive`
- `from_str_round_trip`, `from_str_is_case_insensitive`, `ollama_does_not_need_api_key`, `all_others_need_api_key`

Plus the 16 pre-existing tests in `ipc.rs` for workspace path validation, uuid format, git struct serialization, sandbox-boundary checks, sanitization helpers.

### 12.4 New JS modules in `src/js/`

| Path | Lines | Purpose |
|---|---|---|
| [src/js/wizard.js](../src/js/wizard.js) | ~600 | First-run wizard — 7 steps (Welcome / Folder / Init / Identity / AI / Budget / Done), persisted, resumable, real chat-ping test |
| [src/js/secrets.js](../src/js/secrets.js) | ~370 | Replit-style secrets manager — list, reveal (10s auto-hide, audited), edit, delete, bulk-import via `Edit as .env` with diff |
| [src/js/preview.js](../src/js/preview.js) | ~260 | Iframe preview pane + Run/Reload/Stop controller + collapsible run log |
| [src/js/progress.js](../src/js/progress.js) | ~140 | Style A phase/substep renderer driven by `sidecar:progress` events |
| [src/js/enforcement.js](../src/js/enforcement.js) | ~210 | Topbar 🛡 pill + per-rule popover + override modal |
| [src/js/conversation.js](../src/js/conversation.js) | ~110 | Builder-aware conversation history at `.signalos/builds/<id>/conversation.jsonl` |
| [src/js/file-tree.js](../src/js/file-tree.js) | ~180 | Workspace file tree with diff badges (new/mod) for recent Build outputs |
| [src/js/left-tabs.js](../src/js/left-tabs.js) | ~170 | Files / Gov / Mem tabs with intent-driven auto-switch from the chat composer |
| [src/js/wired-commands.js](../src/js/wired-commands.js) | ~360 | The 15 previously-placeholder `/signal-*` commands, all routed through `streamingProviderChat` |

`src/js/app.js` itself grew from ~3025 lines to ~3700 lines with the wiring of all of the above, plus `streamingProviderChat()` helper and `regenerateSingleFile()`.

### 12.5 Streaming AI tokens — every high-token path routes through one helper

`streamingProviderChat(provider, model, prompt, onDelta)` in [src/js/app.js](../src/js/app.js) wraps `ipc.provider.chatStream` + `ipc.onChatToken`. Five callers:

1. `askSignalOS` — chat panel; tokens land in the log entry as they arrive
2. `actuallyRunBuild` main pass — Builder phase message shows running char count
3. `actuallyRunBuild` retry path — same, with "Retry N/3" label
4. `regenerateSingleFile` — builder state message shows char count
5. `runDocCommand` in wired-commands.js — onDelta updates the log entry; activates for all 10 wired AI doc commands (`/signal-discovery`, `/signal-design`, `/signal-design-html`, `/signal-design-review`, `/signal-debrief`, `/signal-pre-design`, `/signal-pre-wave`, `/signal-review`, `/signal-ship`, `/signal-wave-review`)

### 12.6 Test infrastructure

| Path | Type | Count |
|---|---|---|
| [scripts/test-gates.ps1](../scripts/test-gates.ps1) | L0/L1 gate runner (PowerShell) | 6 mandatory gates |
| [scripts/test-gates.sh](../scripts/test-gates.sh) | Same (POSIX) | 6 mandatory gates |
| [.github/workflows/test-automation.yml](../.github/workflows/test-automation.yml) | CI workflow | L0 / L1 / L2 / L3 / L6 jobs |
| [python/test_onboarding.py](../python/test_onboarding.py) | Live integration tests against the sidecar subprocess | 9 cases |

Current results when running `bash scripts/test-gates.sh`:

```
Passed: 6
  + L0: cargo fmt check        (1 file/sec)
  + L0: cargo clippy           (-D warnings; clean)
  + L0: cargo check            (clean)
  + L0: cargo test --lib       (32 tests pass)
  + L0: python tests           (25 tests pass: 16 + 9 onboarding)
  + L0: secret scan            (clean; test fixtures excluded)
```

### 12.7 Real bugs the live smoke test found (and fixed)

Running `cargo run --release` and then launching `src-tauri/target/release/signalos-desktop.exe` directly surfaced two bugs that no unit test catches:

1. **Stale bundled sidecar binary.** `src-tauri/bin/signalos-python-x86_64-pc-windows-msvc.exe` was built before Wave 2 — it reported `version: "0.0.7"` and `Unknown command` for `phase:contract`. The Tauri build pipeline doesn't re-bundle the sidecar automatically; you have to re-run `scripts/bundle-sidecar.ps1` when the Python source changes. **Fix:** ran the bundle script; re-smoked; now ping reports `0.0.9` and the phase contract returns the right 4-phase structure.
2. **Missing sidecar-spawn permission in the v2 capability file.** `tauri-plugin-shell` v2 requires explicit `shell:allow-spawn` (and `shell:allow-execute` on some setups) with a `binaries/signalos-python` scope for the externalBin to be launched. The capability file shipped with only `shell:default` and `shell:allow-open`. Result: desktop binary boots in 614ms, but the Python sidecar process never appears. **Fix:** added both `shell:allow-spawn` and `shell:allow-execute` permissions with the sidecar scope in [src-tauri/capabilities/default.json](../src-tauri/capabilities/default.json). Re-built, re-launched, verified at t=4s that `signalos-python.exe` is alive alongside `signalos-desktop.exe`.

Both bugs were dormant during unit-test runs because the unit tests never spawned the sidecar via the Tauri shell plugin — they invoked Python directly. The smoke test caught them. This is exactly what `Slide 4 — Run` of the interactive onboarding promises the user, and what the unit-test gates alone could not verify.

### 12.8 What §11 said but isn't yet shipped (honest gap list)

These items were described in §11 but are explicitly NOT in v1.0:

- **Live `cargo tauri dev` end-to-end click-through.** I verified the binary boots and the sidecar spawns. I have not visually walked the wizard, hit Build with a real AI key, watched files land, etc. That last mile of manual verification requires a desktop session and a paid AI key.
- **External release gates** — Authenticode + Developer ID signing, macOS notarization, filled minisign signatures in `beta.json` / `latest.json`, clean-machine VM validation. Out of scope by design; see [SIGNING.md](../SIGNING.md).
- **Wave 6 features** (multi-project, team co-signing, plugin marketplace UI, bundled Node) — explicitly deferred per §11.7.
- **Onboarding tour ↔ wizard parity in code paths** — the tour simulates each step; the wizard runs each step against the real IPC. They were aligned on data shape but not on visual frames; only the design tokens are now identical.

### 12.9 Onboarding tour as a living spec

[docs/onboarding-tour.html](onboarding-tour.html) is a 23-slide single-file interactive tour, design-system-unified with `src/index.html` (same `:root` tokens, same button class shape). Each slide maps to real code:

| Slide | Code it exercises |
|---|---|
| Folder | `set_workspace`, `list_workspace_dir` |
| Init | `/signal-init --mode {keep,full,minimal,skip}` |
| AI | `test_provider_connection` (real chat ping) |
| Identity | `set_identity` → `.signalos/identity.json` |
| Budget | `set_monthly_budget` |
| Files | `write_workspace_files`, `list_workspace_dir`, `read_workspace_file` |
| Diff | `preview_workspace_files` |
| Mutation | `check_mutation_threshold` |
| Run | `start_preview` + `LocalProcessSupervisor` |
| Regenerate | `streamingProviderChat` + `preview_workspace_files` + `write_workspace_files` |
| Streaming | `send_provider_message_stream` + `onChatToken` |
| Tabs | `attachLeftTabs` + intent-driven auto-switch |
| Secrets | `list_workspace_secrets`, `reveal_workspace_secret`, `apply_workspace_env_diff` |
| Test Debt | `add_test_debt`, `list_test_debt` |
| Enforce | `build_precheck`, `override_rule` |
| Sign | `sign_gate`, `check_role_for_gate` |
| Audit | `.signalos/AUDIT_TRAIL.jsonl` (atomic `append_audit`) |
| Ship | the real generated repo: VS Code / git / Vercel/Netlify/Fly + the bundled `.github/workflows/test-automation.yml` |
| Freeze | `freeze_wave`, `unfreeze_wave`, `build_precheck` |

The tour is **not test evidence**. It's the user-facing introduction to the v1.0 product. It happens to also be a manual smoke test because every interactive widget hits a real IPC name when transcribed into the desktop app.

### 12.10 Memory / convention enforcement

Project conventions saved to long-term memory (`~/.claude/projects/.../memory/`):
- `feedback_no_sprints.md` — never use "sprint"; use Wave / Phase / Gate.
- `feedback_enforced_signalos.md` — product positioning is "fully wired & enforced"; never "advisory."
- `feedback_no_signal_guard.md` — never use "SIGNAL Guard"; call the quality-verification module "test-automation."

### 12.11 Audit trail

`.signalos/AUDIT_TRAIL.jsonl` in the repo root contains the full signing record for Waves 1–5 plus every resign with cumulative evidence. As of the latest entry: `enforcement_as_code: true`, `design_system_unified: true`, `real_repo_not_prototype: true`, all five gates signed.

---

*End of report.*
