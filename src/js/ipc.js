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
  list:             ()         => invoke("list_providers"),
  getActive:        ()         => invoke("get_active_provider"),
  setActive:        (p)        => invoke("set_active_provider",  { provider: p }),
  getCost:          ()         => invoke("get_cost_state"),
  recordTokens:     (i, o)     => invoke("record_token_usage",   { tokens_in: i, tokens_out: o }),
  resetSession:     ()         => invoke("reset_session_cost"),
  setBudget:        (usd)      => invoke("set_monthly_budget",   { budget_usd: usd }),
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
      case "list_providers":      return [
        { id: "anthropic", name: "Anthropic Claude", model: "claude-sonnet-4-6",  price_in: 3.00,  price_out: 15.00 },
        { id: "openai",    name: "OpenAI",           model: "gpt-4o",             price_in: 5.00,  price_out: 15.00 },
        { id: "gemini",    name: "Google Gemini",    model: "gemini-1.5-pro",     price_in: 1.25,  price_out: 5.00  },
        { id: "ollama",    name: "Ollama (local)",   model: "llama3",             price_in: 0.00,  price_out: 0.00  },
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
      default:                  return null;
    }
  });
}
