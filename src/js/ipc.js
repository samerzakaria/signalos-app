/**
 * ipc.js — Frontend ↔ Tauri bridge
 *
 * Wraps all window.__TAURI__.invoke() calls in typed async functions.
 * Falls back to mock data when running outside Tauri (browser dev mode).
 */

const IS_TAURI = typeof window.__TAURI__ !== "undefined";

async function invoke(cmd, args = {}) {
  if (IS_TAURI) {
    return window.__TAURI__.invoke(cmd, args);
  }
  // Browser mock — returns plausible data for UI development
  return mockInvoke(cmd, args);
}

// ─── WORKSPACE ────────────────────────────────────────────────────────────────

export const workspace = {
  set:      (path)         => invoke("set_workspace",           { path }),
  get:      ()             => invoke("get_workspace"),
  validate: (target)       => invoke("validate_workspace_write", { target }),
};

// ─── SIGNAL COMMANDS ──────────────────────────────────────────────────────────

export const signal = {
  run: (command, args = []) => invoke("run_signal_command", { command, args }),
};

// Listen for async sidecar responses
export function onSidecarResponse(cb) {
  if (!IS_TAURI) return () => {};
  return window.__TAURI__.event.listen("sidecar:response", (e) => cb(e.payload));
}

export function onSidecarLog(cb) {
  if (!IS_TAURI) return () => {};
  return window.__TAURI__.event.listen("sidecar:log", (e) => cb(e.payload));
}

// ─── WAVE STATE ───────────────────────────────────────────────────────────────

export const wave = {
  get: () => invoke("get_wave_state"),
};

// ─── GATES ────────────────────────────────────────────────────────────────────

export const gates = {
  getAll: ()                    => invoke("get_gate_status"),
  sign:   (gateId, signer)      => invoke("sign_gate", { gate_id: gateId, signer }),
};

// ─── BRAIN ────────────────────────────────────────────────────────────────────

export const brain = {
  search: (query)                => invoke("get_brain_entries",  { query }),
  add:    (text, entryType)      => invoke("add_brain_entry",    { text, entry_type: entryType }),
};

// ─── AUDIT ────────────────────────────────────────────────────────────────────

export const audit = {
  list: (limit = 50) => invoke("get_audit_trail", { limit }),
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
  get:    (p)      => invoke("get_api_key",    { provider: p }),
  has:    (p)      => invoke("has_api_key",    { provider: p }),
  delete: (p)      => invoke("delete_api_key", { provider: p }),
};

// ─── MOCK DATA (browser dev mode) ─────────────────────────────────────────────

function mockInvoke(cmd, args) {
  const delay = (ms) => new Promise((r) => setTimeout(r, ms));
  return delay(60).then(() => {
    switch (cmd) {
      case "get_workspace":       return "/Users/samer/Projects/MyProduct";
      case "get_active_provider": return "anthropic";
      // Mock provider list — model names match providers.json defaults.
      // In the real app these come from the user's providers.json, not this file.
      case "list_providers": return [
        { id: "anthropic", name: "Anthropic Claude", model: "claude-sonnet-4-6",  needs_key: true,  price_in_1m: 3.00,  price_out_1m: 15.00 },
        { id: "openai",    name: "OpenAI",           model: "gpt-4o",             needs_key: true,  price_in_1m: 5.00,  price_out_1m: 15.00 },
        { id: "gemini",    name: "Google Gemini",    model: "gemini-2.0-flash",   needs_key: true,  price_in_1m: 0.10,  price_out_1m: 0.40  },
        { id: "ollama",    name: "Ollama (local)",   model: "",                   needs_key: false, price_in_1m: 0.00,  price_out_1m: 0.00  },
      ];
      case "get_cost_state": return {
        tokens_in: 12400, tokens_out: 3200,
        session_usd: 0.42, monthly_usd: 2.18,
        budget_usd: 10.0, provider: "Claude",
      };
      case "has_api_key": return true;
      case "get_brain_entries": return [];
      case "get_audit_trail":   return [];
      case "get_gate_status":   return null;
      case "get_wave_state":    return null;
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
        if (p === "ollama") return [
          { id: "llama3.2",   name: "llama3.2"  },
          { id: "mistral",    name: "mistral"   },
          { id: "phi4",       name: "phi4"      },
        ];
        return [];
      }
      default:                  return null;
    }
  });
}
