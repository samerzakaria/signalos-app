/**
 * ipc.js — Frontend ↔ Tauri bridge
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
  // Browser mock — returns plausible data for UI development
  return mockInvoke(cmd, args);
}

async function invokeSidecar(cmd, args = {}, timeoutMs = 30000) {
  const id = await invoke(cmd, args);
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

// ─── WORKSPACE ────────────────────────────────────────────────────────────────

export const workspace = {
  set:      (path)         => invoke("set_workspace",           { path }),
  get:      ()             => invoke("get_workspace"),
  validate: (target)       => invoke("validate_workspace_write", { target }),
  startWatch: ()           => invoke("start_workspace_watch"),
};

// Listen for workspace file-system change events (T1-4)
export function onWorkspaceChange(cb) {
  if (!IS_TAURI || typeof listenTauri !== "function") return () => {};
  return listenTauri("workspace:changed", (e) => cb(e.payload));
}

// ─── SIGNAL COMMANDS ──────────────────────────────────────────────────────────

export const signal = {
  run: (command, args = []) => invoke("run_signal_command", { command, args }),
  runAndWait: (command, args = [], timeoutMs = 120000) =>
    invokeSidecar("run_signal_command", { command, args }, timeoutMs),
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

// ─── AUTO-UPDATER (T1-5) ─────────────────────────────────────────────────────

export const updater = {
  check: () => invoke("check_for_updates"),
};

// ─── WAVE STATE ───────────────────────────────────────────────────────────────

export const wave = {
  get: () => invokeSidecar("get_wave_state"),
};

// ─── GIT / WORKTREE ───────────────────────────────────────────────────────────

export const git = {
  status: () => invoke("get_git_status"),
};

// ─── GATES ────────────────────────────────────────────────────────────────────

export const gates = {
  getAll: ()                    => invokeSidecar("get_gate_status"),
  sign:   (gateId, signer)      => invokeSidecar("sign_gate", { gate_id: gateId, signer }),
};

// ─── BRAIN ────────────────────────────────────────────────────────────────────

export const brain = {
  search: (query)                => invokeSidecar("get_brain_entries",  { query }),
  add:    (text, entryType)      => invokeSidecar("add_brain_entry",    { text, entry_type: entryType }),
};

// ─── AUDIT ────────────────────────────────────────────────────────────────────

export const audit = {
  list: (limit = 50) => invokeSidecar("get_audit_trail", { limit }),
};

export const security = {
  secrets: () => invokeSidecar("run_signal_command", { command: "security:secrets", args: [] }),
};

// ─── PROVIDER + COST ──────────────────────────────────────────────────────────

export const provider = {
  list:         ()              => invoke("list_providers"),
  getActive:    ()              => invoke("get_active_provider"),
  setActive:    (p)             => invoke("set_active_provider",      { provider: p }),
  // Model + pricing are user-configurable — persisted to providers.json, never hardcoded
  setModel:     (p, model)      => invoke("set_provider_model",       { provider: p, model }),
  setPricing:   (p, i, o)       => invoke("set_provider_pricing",     { provider: p, price_in_1m: i, price_out_1m: o }),
  getCost:      ()              => invoke("get_cost_state"),
  recordTokens: (i, o)          => invoke("record_token_usage",       { tokens_in: i, tokens_out: o }),
  resetSession: ()              => invoke("reset_session_cost"),
  setBudget:    (usd)           => invoke("set_monthly_budget",       { budget_usd: usd }),
  // Fetch live model list from provider API (requires api_key for cloud providers)
  fetchModels:  (p, apiKey)     => invoke("fetch_provider_models",   { provider: p, api_key: apiKey || null }),
};

// ─── KEYCHAIN ─────────────────────────────────────────────────────────────────

export const keychain = {
  store:  (p, key) => invoke("store_api_key",  { provider: p, key }),
  has:    (p)      => invoke("has_api_key",    { provider: p }),
  delete: (p)      => invoke("delete_api_key", { provider: p }),
};

// ─── MOCK DATA (browser dev mode) ─────────────────────────────────────────────

function mockInvoke(cmd, args) {
  const delay = (ms) => new Promise((r) => setTimeout(r, ms));
  return delay(60).then(() => {
    switch (cmd) {
      case "get_workspace":       return null;
      case "get_active_provider": return "anthropic";
      // Mock provider list — model names match providers.json defaults.
      // In the real app these come from the user's providers.json, not this file.
      case "list_providers": return [
        { id: "anthropic", name: "Anthropic Claude", model: "claude-sonnet-4-6",  needs_key: true,  price_in_1m: 3.00,  price_out_1m: 15.00 },
        { id: "openai",    name: "OpenAI",           model: "gpt-4o",             needs_key: true,  price_in_1m: 5.00,  price_out_1m: 15.00 },
        { id: "gemini",    name: "Google Gemini",    model: "gemini-2.0-flash",   needs_key: true,  price_in_1m: 0.10,  price_out_1m: 0.40  },
        { id: "qwen",      name: "Qwen",             model: "qwen-plus",          needs_key: true,  price_in_1m: 0.00,  price_out_1m: 0.00  },
        { id: "ollama",    name: "Ollama (local)",   model: "",                   needs_key: false, price_in_1m: 0.00,  price_out_1m: 0.00  },
        { id: "openrouter",name: "OpenRouter",       model: "qwen/qwen-plus",     needs_key: true,  price_in_1m: 0.00,  price_out_1m: 0.00  },
        { id: "deepseek",  name: "DeepSeek",         model: "deepseek-chat",      needs_key: true,  price_in_1m: 0.00,  price_out_1m: 0.00  },
        { id: "mistral",   name: "Mistral",          model: "mistral-large-latest", needs_key: true, price_in_1m: 0.00, price_out_1m: 0.00 },
        { id: "groq",      name: "Groq",             model: "llama-3.3-70b-versatile", needs_key: true, price_in_1m: 0.00, price_out_1m: 0.00 },
        { id: "cerebras",  name: "Cerebras",         model: "llama-4-scout-17b-16e-instruct", needs_key: true, price_in_1m: 0.00, price_out_1m: 0.00 },
        { id: "together",  name: "Together AI",      model: "meta-llama/Llama-3.3-70B-Instruct-Turbo", needs_key: true, price_in_1m: 0.00, price_out_1m: 0.00 },
        { id: "xai",       name: "xAI",              model: "grok-4",             needs_key: true,  price_in_1m: 0.00,  price_out_1m: 0.00  },
      ];
      case "get_cost_state": return {
        tokens_in: 0, tokens_out: 0,
        session_usd: 0, monthly_usd: 0,
        budget_usd: 10.0, provider: "Claude",
      };
      case "get_git_status": return {
        branch: "", is_clean: true, ahead: 0, behind: 0,
        last_sync: new Date(Date.now() - 4 * 60 * 1000).toISOString(),
        worktrees: [],
      };
      case "has_api_key":             return false;
      case "get_brain_entries":       return [];
      case "get_audit_trail":         return [];
      case "get_gate_status":         return null;
      case "get_wave_state":          return null;
      case "check_for_updates":       return { available: false };
      case "start_workspace_watch":   return null;
      // Mock model lists for browser dev mode
      case "fetch_provider_models": {
        const p = args.provider;
        if (p === "anthropic") return [
          { id: "claude-opus-4-6",    name: "Claude Opus 4.6"   },
          { id: "claude-sonnet-4-6",  name: "Claude Sonnet 4.6" },
          { id: "claude-haiku-4-5-20251001", name: "Claude Haiku 4.5" },
        ];
        if (p === "openai") return [
          { id: "gpt-4o",       name: "GPT-4o"        },
          { id: "gpt-4o-mini",  name: "GPT-4o Mini"   },
          { id: "o3",           name: "o3"             },
          { id: "o4-mini",      name: "o4-mini"        },
        ];
        if (p === "gemini") return [
          { id: "gemini-2.0-flash",   name: "Gemini 2.0 Flash"  },
          { id: "gemini-1.5-pro",     name: "Gemini 1.5 Pro"    },
          { id: "gemini-1.5-flash",   name: "Gemini 1.5 Flash"  },
        ];
        if (p === "qwen") return [
          { id: "qwen-plus", name: "Qwen Plus" },
          { id: "qwen-max",  name: "Qwen Max"  },
          { id: "qwen-turbo", name: "Qwen Turbo" },
        ];
        if (p === "ollama") return [
          { id: "llama3.2",   name: "llama3.2"  },
          { id: "mistral",    name: "mistral"   },
          { id: "phi4",       name: "phi4"      },
        ];
        if (["openrouter", "deepseek", "mistral", "groq", "cerebras", "together", "xai"].includes(p)) {
          return [];
        }
        return [];
      }
      default:                  return null;
    }
  });
}
