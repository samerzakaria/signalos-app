/**
 * ipc.js - Frontend to Tauri bridge
 *
 * Wraps all Tauri invoke() calls in typed async functions.
 * Falls back to mock data when running outside Tauri (browser dev mode).
 */

const TAURI = typeof window !== "undefined" ? window.__TAURI__ : undefined;
const invokeTauri = TAURI?.core?.invoke || TAURI?.invoke;
const listenTauri = TAURI?.event?.listen;
const IS_TAURI = typeof invokeTauri === "function";
const pendingSidecar = new Map();
const completedSidecar = new Map();

if (IS_TAURI && typeof listenTauri === "function") {
  listenTauri("sidecar:response", (e) => {
    const resp = e.payload || {};
    const pending = pendingSidecar.get(resp.id);
    if (!pending) {
      completedSidecar.set(resp.id, resp);
      setTimeout(() => completedSidecar.delete(resp.id), 30000);
      return;
    }

    pendingSidecar.delete(resp.id);
    if (resp.ok) {
      pending.resolve(resp.data ?? resp.output ?? null);
    } else {
      pending.reject(new Error(resp.error || "SignalOS sidecar command failed"));
    }
  });
}

async function invoke(cmd, args = {}) {
  if (IS_TAURI) {
    return invokeTauri(cmd, args);
  }
  // No mocks. SignalOS is a native installed app - if the Tauri runtime
  // is missing, the shell is broken. Fail loudly so production never
  // silently renders fake data. (User directive: 2026-05-15.)
  throw new Error(
    `SignalOS native runtime not available. The Tauri shell must be running for "${cmd}" to work.`
  );
}

async function invokeSidecar(cmd, args = {}, timeoutMs = 30000, onId = null) {
  const id = await invoke(cmd, args);
  if (typeof onId === "function") {
    try { onId(id); } catch {}
  }
  if (!IS_TAURI || typeof id !== "string" || !id.startsWith("req-")) return id;

  const completed = completedSidecar.get(id);
  if (completed) {
    completedSidecar.delete(id);
    if (completed.ok) return completed.data ?? completed.output ?? null;
    throw new Error(completed.error || "SignalOS sidecar command failed");
  }

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pendingSidecar.delete(id);
      reject(new Error(`Timed out waiting for ${cmd}`));
    }, timeoutMs);

    pendingSidecar.set(id, {
      resolve: (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      reject: (error) => {
        clearTimeout(timer);
        reject(error);
      },
    });
  });
}

function rejectPendingSidecars(message = "Command stopped by user.") {
  for (const [, pending] of pendingSidecar.entries()) {
    pending.reject(new Error(message));
  }
  pendingSidecar.clear();
}

// WORKSPACE

export const workspace = {
  set:      (path)         => invoke("set_workspace",           { path }),
  clear:    ()             => invoke("clear_workspace"),
  get:      ()             => invoke("get_workspace"),
  status:   ()             => invoke("get_workspace_status"),
  validate: (target)       => invoke("validate_workspace_write", { target }),
  startWatch: ()           => invoke("start_workspace_watch"),
};

export const project = {
  artifacts: () => invoke("get_project_artifacts"),
  openPath: (relativePath) => invoke("open_workspace_path", { relative_path: relativePath }),
  exportFile: (kind, filename, content) =>
    invoke("write_workspace_export", { kind, filename, content }),
  writeFiles: (files, overwrite = true) =>
    invoke("write_workspace_files", { files, overwrite }),
  previewFiles: (files) =>
    invoke("preview_workspace_files", { files }),
  // Wave 5 closeout - read + list inside the workspace sandbox.
  readFile: (relativePath) =>
    invoke("read_workspace_file", { relative_path: relativePath }),
  listDir: (relativePath = ".") =>
    invoke("list_workspace_dir", { relative_path: relativePath }),
};

export const secrets = {
  upsert: (name, value, filename = ".env.local") =>
    invoke("upsert_workspace_secret", { name, value, filename }),
  // Wave 1 / G0-6 - Replit-style secrets manager
  list:   (filename = ".env.local") =>
    invoke("list_workspace_secrets", { filename }),
  reveal: (name, filename = ".env.local") =>
    invoke("reveal_workspace_secret", { name, filename }),
  delete: (name, filename = ".env.local") =>
    invoke("delete_workspace_secret", { name, filename }),
  applyDiff: (filename, envText, allowRemovals) =>
    invoke("apply_workspace_env_diff", { filename, env_text: envText, allow_removals: allowRemovals }),
};

// Listen for workspace file-system change events (T1-4)
export function onWorkspaceChange(cb) {
  if (!IS_TAURI || typeof listenTauri !== "function") return () => {};
  return listenTauri("workspace:changed", (e) => cb(e.payload));
}

// SIGNAL COMMANDS

export const signal = {
  run: (command, args = []) => invoke("run_signal_command", { command, args }),
  runAndWait: (command, args = [], timeoutMs = 120000, onId = null) =>
    invokeSidecar("run_signal_command", { command, args }, timeoutMs, onId),
  cancelPending: (message) => rejectPendingSidecars(message),
};

export const engine = {
  ping: () => invokeSidecar("run_signal_command", { command: "ping", args: [] }, 8000),
  status: () => invoke("get_sidecar_status"),
  restart: () => invoke("restart_python_sidecar"),
};

// Listen for async sidecar responses
export function onSidecarResponse(cb) {
  if (!IS_TAURI || typeof listenTauri !== "function") return () => {};
  return listenTauri("sidecar:response", (e) => cb(e.payload));
}

export function onSidecarLog(cb) {
  if (!IS_TAURI || typeof listenTauri !== "function") return () => {};
  return listenTauri("sidecar:log", (e) => cb(e.payload));
}

// Wave 2 / G1-7: sidecar progress events (PhaseContract substep updates).
export function onSidecarProgress(cb) {
  if (!IS_TAURI || typeof listenTauri !== "function") return () => {};
  return listenTauri("sidecar:progress", (e) => cb(e.payload));
}

// Fetch a phase contract definition from the sidecar.
export function invokeProgressContract(name) {
  return invokeSidecar("run_signal_command", { command: "phase:contract", args: [name] }, 5000);
}

// AUTO-UPDATER (T1-5)

export const updater = {
  check: (channel = "beta") => invoke("check_for_updates", { channel }),
};

// WAVE STATE

export const wave = {
  get: () => invokeSidecar("get_wave_state"),
};

// GIT / WORKTREE

export const git = {
  status: () => invoke("get_git_status"),
};

// GATES

export const gates = {
  getAll: ()                    => invokeSidecar("get_gate_status"),
  sign:   (gateId, signer)      => invokeSidecar("sign_gate", { gate_id: gateId, signer }),
};

// BRAIN

export const brain = {
  search: (query)                => invokeSidecar("get_brain_entries",  { query }),
  add:    (text, entryType)      => invokeSidecar("add_brain_entry",    { text, entry_type: entryType }),
};

// AUDIT

export const audit = {
  list: (limit = 50) => invokeSidecar("get_audit_trail", { limit }),
};

export const security = {
  secrets: () => invokeSidecar("run_signal_command", { command: "security:secrets", args: [] }),
};

export const attachments = {
  analyze: (files) => invokeSidecar(
    "run_signal_command",
    { command: "attachment:analyze", args: [JSON.stringify(files)] },
    120000,
  ),
};

// IDENTITY + ROLE (Wave 3)

export const identity = {
  set:           (name, role)    => invoke("set_identity", { name, role }),
  get:           ()              => invoke("get_identity"),
  canSignGate:   (gateId)        => invoke("check_role_for_gate", { gate_id: gateId }),
};

// TEST AUTOMATION (Wave 5 / G4)

export const testAutomation = {
  listDebt:         ()                                  => invoke("list_test_debt"),
  addDebt:          (kind, area, title, detail)         => invoke("add_test_debt", { entry: { kind, area, title, detail } }),
  resolveDebt:      (title)                             => invoke("resolve_test_debt", { title }),
  checkMutation:    (score, area)                       => invoke("check_mutation_threshold", { args: { score, area } }),
  checkTestFirst:   (testRefs)                          => invoke("check_test_first", { args: { test_refs: testRefs } }),
  // Wave 5 / G4 + rule 12 - read mutation score from .signalos/mutation-score.json
  readMutationScore: ()                                  => invoke("read_mutation_score"),
};

// ENFORCEMENT (Wave 3 / G2-21..26)

export const enforcement = {
  state:    ()                      => invoke("get_enforcement_state"),
  precheck: (stack)                 => invoke("build_precheck", { args: { stack } }),
  override: (rule, reason, context) => invoke("override_rule", { args: { rule, reason, context: context || null } }),
  setMode:  (rule, mode)            => invoke("set_rule_mode", { rule, mode }),
  freeze:   ()                      => invoke("freeze_wave"),
  unfreeze: ()                      => invoke("unfreeze_wave"),
};

// PREVIEW (Wave 2 / G1-10+11)

export const preview = {
  probeNode: ()                  => invoke("probe_node"),
  start:     (stack, workspace)  => invoke("start_preview", { stack, workspace }),
  stop:      (key)               => invoke("stop_preview", { key }),
  list:      ()                  => invoke("list_previews"),
  get:       (key)               => invoke("get_preview",  { key }),
};

export function onPreviewEvent(cb) {
  if (!IS_TAURI || typeof listenTauri !== "function") return () => {};
  return listenTauri("preview:event", (e) => cb(e.payload));
}

// PROVIDER + COST

export const provider = {
  list:         ()              => invoke("list_providers"),
  getActive:    ()              => invoke("get_active_provider"),
  setActive:    (p)             => invoke("set_active_provider",      { provider: p }),
  // Model + pricing are user-configurable - persisted to providers.json, never hardcoded
  setModel:     (p, model)      => invoke("set_provider_model",       { provider: p, model }),
  setPricing:   (p, i, o)       => invoke("set_provider_pricing",     { provider: p, price_in_1m: i, price_out_1m: o }),
  getCost:      ()              => invoke("get_cost_state"),
  recordTokens: (i, o)          => invoke("record_token_usage",       { tokens_in: i, tokens_out: o }),
  resetSession: ()              => invoke("reset_session_cost"),
  setBudget:    (usd)           => invoke("set_monthly_budget",       { budget_usd: usd }),
  // Fetch live model list from provider API (requires api_key for cloud providers)
  fetchModels:  (p, apiKey)     => invoke("fetch_provider_models",   { provider: p, api_key: apiKey || null }),
  // Wave 1 / G0-3: model is now passed through so the test is a real chat round-trip.
  test:         (p, apiKey, model) => invoke("test_provider_connection", { provider: p, api_key: apiKey || null, model: model || null }),
  chat:         (p, model, message) =>
    invoke("send_provider_message", { provider: p, model: model || null, message }),
  // Wave 5 closeout - streaming chat. Caller listens via onChatToken(streamId, cb).
  chatStream:   (streamId, p, model, message) =>
    invoke("send_provider_message_stream", { stream_id: streamId, provider: p, model: model || null, message }),
};

// Listen for streaming chat token events. Filter by streamId so multiple
// concurrent streams don't interleave. Returns an unsubscribe function.
export function onChatToken(streamId, cb) {
  if (!IS_TAURI || typeof listenTauri !== "function") return () => {};
  const unsub = listenTauri("chat:token", (e) => {
    const p = e.payload || {};
    if (!streamId || p.stream_id === streamId) cb(p);
  });
  // Tauri's listen() returns a Promise<unsubscribe>; normalize to a fn.
  return () => { unsub.then((f) => { try { f(); } catch {} }); };
}

// KEYCHAIN

export const keychain = {
  store:  (p, key) => invoke("store_api_key",  { provider: p, key }),
  has:    (p)      => invoke("has_api_key",    { provider: p }),
  delete: (p)      => invoke("delete_api_key", { provider: p }),
};


// Mocks intentionally removed. Production-grade: native runtime is required.
// User directive 2026-05-15: "no mock for production grade".
