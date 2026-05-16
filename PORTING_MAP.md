# SignalOS v2 → Real App Porting Map

Complete mapping of every mockup JS function AND every real Tauri command/event to its UI binding.
Two directions: mockup stubs → real invoke(), and real commands/events → UI that needs to exist.

---

## Port priority order

1. **Onboarding** — identity + workspace + API key seal
2. **Chat streaming** — `chat:token` event listener + streaming bubble
3. **File write feedback** — `write_workspace_files` response → file tree refresh
4. **Gate signing** — `sign_gate` + audit write
5. **Brain** — `get_brain_entries` + `add_brain_entry`
6. **Vault** — `list_workspace_secrets` + reveal/copy/add/delete
7. **History / export** — `get_audit_trail` + `write_workspace_export`
8. **Dashboard refresh** — wave state + gate status + cost + git
9. **Enforcement controls** — `set_rule_mode` + `unfreeze_wave`
10. **Event listeners** — native menu, workspace watch, sidecar errors

---

## 1. Onboarding

| Mockup function | Real binding | Notes |
|---|---|---|
| `nextStep()` | UI only | Advances `state.obStep`, swaps `.ob-step.active` |
| `prevStep()` | UI only | Reverse |
| `selectAI()` / `selectProv(el)` | UI only during OB | Stores `state.ai`, `state.aiModel` until finish |
| `toggleMoreProvs()` | UI only | Shows/hides secondary provider grid |
| `toggleKey()` | UI only | Eye toggle on API key field |
| `finishOnboarding()` | **Three sequential invokes** | 1→ `invoke('set_identity', { name, role })` 2→ `invoke('set_workspace', { path })` 3→ `invoke('store_api_key', { provider: state.ai, key })` → then fade to `#app` |

---

## 2. Navigation

| Mockup function | Real binding | Notes |
|---|---|---|
| `switchSbTab(tab)` | UI only | Swaps `.sb-tab.active` + `.sb-panel.active` |
| `switchTab(tab)` | UI only + **data fetch on arrival** | See section 14 for per-view load calls |

---

## 3. Build / Chat — STREAMING (critical path)

The real AI response is **not** a single string. It streams token-by-token via Tauri events.
The mockup's `addSignalOS(fullText)` model must be replaced with a streaming bubble.

| Mockup function | Real binding | Notes |
|---|---|---|
| `sendMsg()` | `invoke('run_signal_command', { cmd: 'chat', message })` | Returns `{ stream_id }`. Actual text arrives via `chat:token` events |
| `sendChip(el)` | Calls `sendMsg()` | Pre-fills composer with chip text |
| `runCmd(cmd)` | `invoke('run_signal_command', { cmd })` | Slash command palette commands |
| `addUser(txt)` | UI only | Renders user bubble |
| `addSignalOS(txt)` | **Replace with streaming version** | See streaming bubble spec below |
| `scrollChat()` | UI only | `chatScroll.scrollTop = chatScroll.scrollHeight` |
| `attachFile()` | Tauri dialog → attach blob to next `run_signal_command` | `@tauri-apps/api/dialog` open picker |
| `voiceInput()` | Browser `navigator.mediaDevices.getUserMedia` | Phase 2 |
| `composerInput(e)` | UI only | Slash palette filter |
| `composerKey(e)` | UI only | Enter → `sendMsg()`, Esc → close palette |
| `filterCommands(q)` | UI only | Filters local `COMMANDS` array |

### Streaming bubble spec

```js
// listen once on app boot
window.__TAURI__.event.listen('chat:token', ({ payload }) => {
  const { stream_id, kind, delta } = payload;
  if (kind === 'delta') appendToken(stream_id, delta);
  if (kind === 'done')  finaliseStream(stream_id);
  if (kind === 'error') showStreamError(stream_id, delta);
});

function startAIBubble(stream_id) {
  // Create bubble with blinking cursor, keyed on stream_id
}
function appendToken(stream_id, delta) {
  // Find bubble by stream_id, append delta text, keep cursor at end
}
function finaliseStream(stream_id) {
  // Remove cursor, enable copy button, scroll to bottom
}
```

---

## 4. File write-back (currently missing from mockup)

When the AI generates code, `write_workspace_files` is called by the sidecar.
The UI needs to react:

| Real event / command | Required UI | Notes |
|---|---|---|
| `write_workspace_files` response | Show "N files written" toast + auto-refresh file tree | Response shape: `{ written: ["src/game.js", …], skipped: [], errors: [] }` |
| `validate_workspace_write` | Called internally before any write | If it rejects, show error in chat bubble — no separate UI needed |
| `workspace:changed` Tauri event | Re-call `list_workspace_dir` → refresh Files sidebar panel | Fires on workspace path change |
| `start_workspace_watch` | Called once after workspace set | Polls every 2s; emits `workspace:changed` on file modification |

**New UI needed:** a dismissible toast/banner that appears in the bottom-right after file writes, showing file count and names. Auto-dismiss after 4s.

---

## 5. Enforcement

| Mockup function | Real binding | Notes |
|---|---|---|
| `toggleEnfPopover()` | UI only | Shows `.enf-pop` overlay |
| `freezeWave()` | `invoke('freeze_wave')` | Disables composer + locks gate UI |
| `openOverride()` | UI only | Opens `#overrideModal` |
| `confirmOverride()` | `invoke('override_rule', { rule, reason })` | Reads from modal fields |
| `runCheck()` | `invoke('build_precheck')` | Returns `[{ id, status, label }]` → feed to `paintCrit()` |
| `paintCrit()` | UI only | Maps `build_precheck` response to crit-row colours |

### Missing: `unfreeze_wave` + `set_rule_mode`

**`unfreeze_wave`** — needs a UI trigger. Suggested: when wave is frozen, show a banner in the enforcement popover with an "Unfreeze wave" button → `invoke('unfreeze_wave')`.

**`set_rule_mode`** — switches any rule between `strict` / `warn` / `off`. Suggested: add a mode selector (three-way toggle) next to each rule row in the enforcement popover.
Valid rule IDs: `gate-gating`, `plan-gating`, `secret-block`, `test-first`, `zero-manual-regression`.
Call: `invoke('set_rule_mode', { rule: 'test-first', mode: 'warn' })`.

---

## 6. Gate signing

| Mockup function | Real binding | Notes |
|---|---|---|
| `showSignForm()` | UI only | Toggles `#signForm` |
| `openGate()` | **Three sequential invokes** | 1→ `invoke('check_role_for_gate', { role, gateId })` — if allowed: 2→ `invoke('sign_gate', { name, role, gateId })` 3→ `invoke('add_brain_entry', { type: 'Decision', content })` → update stepper + Gov sidebar |
| `updateGate()` | `invoke('get_gate_status')` + `invoke('get_wave_state')` | Refreshes stepper dots + Gov panel gate nodes |

---

## 7. Modals

| Mockup function | Real binding | Notes |
|---|---|---|
| `openModal(id)` / `closeModal(id)` | UI only | Class toggle on `.modal-overlay` |
| `openAddSecret()` / `closeAddSecret()` | UI only | Opens `#addSecretModal` |
| `saveSecret()` | `invoke('upsert_workspace_secret', { name, value })` | On success refresh secret list |
| `openNewProject()` / `closeNewProject()` | UI only | Opens `#newProjectModal` |
| `createProject()` | Tauri file dialog → `invoke('set_workspace', { path })` | New project = pick folder → set as workspace |

---

## 8. Preview

| Mockup function | Real binding | Notes |
|---|---|---|
| `switchDevice(m)` | UI only | Swaps `.pv-device` class |
| `refreshPreview()` | `invoke('preview_workspace_files')` | Re-renders preview content |
| `openExternal()` | `invoke('open_workspace_path', { path: 'localhost:3000' })` | Opens system browser |

---

## 9. Vault

| Mockup function | Real binding | Notes |
|---|---|---|
| `toggleSecret(btn)` | `invoke('reveal_workspace_secret', { name })` → show / back to `••••` | Never store raw value in DOM |
| `copySecret(btn)` | `invoke('reveal_workspace_secret', { name })` → `navigator.clipboard.writeText()` → auto-clear after 30s | |
| Add secret (modal) | `invoke('upsert_workspace_secret', { name, value })` | Refresh list after |
| (missing) Delete secret | `invoke('delete_workspace_secret', { name })` | Add a delete icon to each secret row |

### Missing: `.env` management

**`apply_workspace_env_diff`** writes/merges key=value pairs into `.env.local`.
Suggested UI: a collapsible "Environment" card in the Vault view, showing current `.env.local` pairs with add/edit/remove. Calls `invoke('apply_workspace_env_diff', { env_text, allow_removals })`.

---

## 10. Brain

| Mockup function | Real binding | Notes |
|---|---|---|
| `filterBrain(el, type)` | UI only | Filters local `brainEntries` array by `.type` |
| `addBrainEntry(type)` | `invoke('add_brain_entry', { type, content, tags })` | Types: `Note`, `Decision`, `Artifact`, `QA`, `Session` |

---

## 11. Terminal

| Mockup function | Real binding | Notes |
|---|---|---|
| `termKey(e)` | UI only | Enter → `termSubmit()`, history up/down |
| `termChip(el)` | Calls `termSubmit()` with chip text | |
| `termSubmit()` / `termExec(cmd)` | `invoke('run_signal_command', { cmd })` | Returns `{ lines: [{ t, c }] }` → append to `.term-body` |

---

## 12. History / export

| Mockup function | Real binding | Notes |
|---|---|---|
| (load view) | `invoke('get_audit_trail')` + `invoke('get_cost_summary')` | Renders history rows + vstats |
| Export buttons | `invoke('write_workspace_export', { kind, filename, content })` | `kind`: `"audit"` or `"report"`. Content is redacted server-side before disk write |

---

## 13. Project artifacts (currently not in mockup)

**`get_project_artifacts`** returns a struct showing which SignalOS files exist in the workspace:
`PLAN.md`, `brain.jsonl`, `AUDIT_TRAIL.jsonl`, `signalos.json`, `package.json` / `requirements.txt`, app entry point, command count, issue reports, handoffs.

**Suggested UI:** a compact "Project health" card on the Dashboard showing which artifacts exist (green tick / grey dash). Calls `invoke('get_project_artifacts')` on dashboard load.

---

## 14. File viewer (currently not in mockup)

**`read_workspace_file({ relative_path })`** reads any file in the workspace (up to 2 MB).

**Suggested UI:** clicking any file in the Files sidebar panel opens a read-only code viewer panel (inline or as a slide-over). Highlights the file's diff status badge if present.

---

## 15. Settings

| Action | Real binding | Notes |
|---|---|---|
| Load view | `invoke('get_identity')` + `invoke('has_api_key', { provider })` for active provider | |
| Change provider | Update `state.ai` + `invoke('store_api_key', { provider, key })` | |
| Remove API key | `invoke('delete_api_key', { provider })` | Add "Remove key" button next to "Replace key" |
| Forget folder | `invoke('set_workspace', { path: null })` | |
| Restart engine | `invoke('run_signal_command', { cmd: 'restart' })` | |
| Check updates | `invoke('check_for_updates')` | Wire to "Check now" button in Updates card |

---

## 16. Close / exit

| Mockup function | Real binding | Notes |
|---|---|---|
| `openExit()` | UI only | Opens `#exitModal` |
| `exitApp(save=true)` | Flush sequence → `window.__TAURI__.window.getCurrent().close()` | Runs "Saving…" animation before Tauri close |
| `exitApp(save=false)` | `window.__TAURI__.window.getCurrent().close()` directly | |

---

## 17. View data loading on `switchTab()`

```js
// Add to switchTab() after swapping active class:
const loaders = {
  dashboard: () => Promise.all([
    invoke('get_wave_state'),
    invoke('get_gate_status'),
    invoke('get_cost_summary'),
    invoke('get_git_status'),
    invoke('get_project_artifacts'),  // new: project health card
  ]).then(renderDashboard),

  build: () => Promise.all([
    invoke('get_wave_state'),
    invoke('get_enforcement_state'),
    invoke('build_precheck'),
  ]).then(renderBuild),

  brain:   () => invoke('get_brain_entries').then(renderBrain),
  history: () => Promise.all([invoke('get_audit_trail'), invoke('get_cost_summary')]).then(renderHistory),
  vault:   () => invoke('list_workspace_secrets').then(renderVault),      // names only, never raw
  settings:() => Promise.all([invoke('get_identity'), invoke('has_api_key', { provider: state.ai })]).then(renderSettings),
  // terminal, preview, help: no initial load
};
if (loaders[tab]) loaders[tab]();
```

---

## 18. Sidebar panel data loading on `switchSbTab()`

| Panel | Invokes on load |
|---|---|
| `projects` | `invoke('get_workspace')` → recent path list |
| `files` | `invoke('list_workspace_dir', { path: '.' })` → file tree |
| `gov` | `invoke('get_wave_state')` + `invoke('get_gate_status')` → gate nodes + wave label |

---

## 19. Tauri event listeners (boot-time, all missing from mockup)

Register all of these once in a `bootListeners()` function called right after `#app` becomes visible:

| Event | Payload | UI action |
|---|---|---|
| `chat:token` | `{ stream_id, kind, delta, provider, model }` | `kind=delta` → append token to streaming bubble; `kind=done` → finalise; `kind=error` → show error |
| `workspace:changed` | workspace path string | Re-call `list_workspace_dir` → refresh Files panel + project name in sidebar |
| `sidecar:error` | error string | Show dismissible error banner at top of main content area |
| `menu:open-workspace` | — | Open Tauri folder dialog → `invoke('set_workspace')` |
| `menu:export-audit` | — | Call `invoke('get_audit_trail')` → `invoke('write_workspace_export', { kind: 'audit', … })` |
| `menu:nav` | `"chat"` / `"dashboard"` / `"brain"` / `"audit"` | Call `switchTab(payload)` |
| `menu:check-update` | — | Call `invoke('check_for_updates')` → show result toast |

---

## 20. Not wired / future / confirmed no-ops

| Function | Status |
|---|---|
| `changeStack()` | No-op — stack is read-only per wave, badge is informational |
| Voice input | Phase 2 — browser mic API |
| `showNotifications()` | Phase 2 — Tauri event bus subscription |
| `shareProject()` | Future collaboration feature |
| `cycleActivity()` / `paintActivity()` | UI animation only, no backend |

---

## Summary counts

| Category | Mockup stubs → invoke | Real commands with no mockup UI yet | Tauri events needing listeners |
|---|---|---|---|
| Count | 28 | 8 | 7 |

**8 commands needing new UI:** `write_workspace_files` feedback toast · `read_workspace_file` viewer · `get_project_artifacts` health card · `apply_workspace_env_diff` env panel · `set_rule_mode` toggles · `unfreeze_wave` button · `write_workspace_export` wired buttons · `check_for_updates` button

**7 events needing listeners:** `chat:token` · `workspace:changed` · `sidecar:error` · `menu:open-workspace` · `menu:export-audit` · `menu:nav` · `menu:check-update`
