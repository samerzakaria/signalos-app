/**
 * ipc.js - Frontend â†” Tauri bridge
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
  // Browser mock - returns plausible data for UI development
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

function rejectPendingSidecars(message = "Command stopped by user.") {
  for (const [, pending] of pendingSidecar.entries()) {
    pending.reject(new Error(message));
  }
  pendingSidecar.clear();
}

// â”€â”€â”€ WORKSPACE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const workspace = {
  set:      (path)         => invoke("set_workspace",           { path }),
  get:      ()             => invoke("get_workspace"),
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
};

export const secrets = {
  upsert: (name, value, filename = ".env.local") =>
    invoke("upsert_workspace_secret", { name, value, filename }),
};

// Listen for workspace file-system change events (T1-4)
export function onWorkspaceChange(cb) {
  if (!IS_TAURI || typeof listenTauri !== "function") return () => {};
  return listenTauri("workspace:changed", (e) => cb(e.payload));
}

// â”€â”€â”€ SIGNAL COMMANDS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const signal = {
  run: (command, args = []) => invoke("run_signal_command", { command, args }),
  runAndWait: (command, args = [], timeoutMs = 120000) =>
    invokeSidecar("run_signal_command", { command, args }, timeoutMs),
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

// â”€â”€â”€ AUTO-UPDATER (T1-5) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const updater = {
  check: (channel = "beta") => invoke("check_for_updates", { channel }),
};

// â”€â”€â”€ WAVE STATE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const wave = {
  get: () => invokeSidecar("get_wave_state"),
};

// â”€â”€â”€ GIT / WORKTREE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const git = {
  status: () => invoke("get_git_status"),
};

// â”€â”€â”€ GATES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const gates = {
  getAll: ()                    => invokeSidecar("get_gate_status"),
  sign:   (gateId, signer)      => invokeSidecar("sign_gate", { gate_id: gateId, signer }),
};

// â”€â”€â”€ BRAIN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const brain = {
  search: (query)                => invokeSidecar("get_brain_entries",  { query }),
  add:    (text, entryType)      => invokeSidecar("add_brain_entry",    { text, entry_type: entryType }),
};

// â”€â”€â”€ AUDIT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€â”€ PROVIDER + COST â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
  test:         (p, apiKey)     => invoke("test_provider_connection", { provider: p, api_key: apiKey || null }),
  chat:         (p, model, message) =>
    invoke("send_provider_message", { provider: p, model: model || null, message }),
};

// â”€â”€â”€ KEYCHAIN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const keychain = {
  store:  (p, key) => invoke("store_api_key",  { provider: p, key }),
  has:    (p)      => invoke("has_api_key",    { provider: p }),
  delete: (p)      => invoke("delete_api_key", { provider: p }),
};

// â”€â”€â”€ MOCK DATA (browser dev mode) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function mockInvoke(cmd, args) {
  const delay = (ms) => new Promise((r) => setTimeout(r, ms));
  return delay(60).then(() => {
    switch (cmd) {
      case "get_workspace":       return null;
      case "get_active_provider": return "anthropic";
      case "get_sidecar_status": return {
        running: true,
        pid: 12345,
        generation: 1,
        last_event: "Engine started",
        last_error: null,
        updated_at_ms: Date.now(),
      };
      case "restart_python_sidecar": return {
        running: true,
        pid: 12346,
        generation: 2,
        last_event: "Engine restarted",
        last_error: null,
        updated_at_ms: Date.now(),
      };
      case "get_project_artifacts": return {
        workspace: "Browser preview",
        initialized: true,
        artifacts: [
          { name: "Runtime state", path: ".signalos", kind: "folder", exists: true, detail: "Local SignalOS runtime folder is present." },
          { name: "Wave plan", path: "core/strategy/PLAN.md", kind: "file", exists: true, detail: "Project plan is present." },
          { name: "Command library", path: "core/execution/commands", kind: "folder", exists: true, detail: "49 command definition files found." },
          { name: "IDE integrations", path: "integrations", kind: "folder", exists: true, detail: "IDE integration files are present." },
          { name: "App manifest", path: "package.json", kind: "file", exists: true, detail: "Node/JavaScript app manifest is present." },
          { name: "App entry", path: "src/main.jsx", kind: "file", exists: true, detail: "Generated app entry found at src/main.jsx." },
        ],
      };
      case "open_workspace_path": return null;
      case "write_workspace_export": return {
        relative_path: `.signalos/${args.kind || "exports"}/${args.filename || "signalos-export.md"}`,
        absolute_path: `Browser preview/${args.filename || "signalos-export.md"}`,
      };
      case "write_workspace_files": return {
        files: (args.files || []).map((file) => ({
          relative_path: file.path,
          absolute_path: `Browser preview/${file.path}`,
          bytes: String(file.content || "").length,
        })),
      };
      case "upsert_workspace_secret": return {
        relative_path: args.filename || ".env.local",
        absolute_path: `Browser preview/${args.filename || ".env.local"}`,
        bytes: 24,
      };
      // Mock provider list - model names match providers.json defaults.
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
      case "check_for_updates":       return { available: false, channel: args.channel || "beta" };
      case "start_workspace_watch":   return null;
      case "run_signal_command":
        if (args.command === "attachment:analyze") {
          const files = JSON.parse(args.args?.[0] || "[]");
          return files.map((file) => ({
            name: file.name,
            size: file.size,
            kind: String(file.type || "").startsWith("image/") ? "image" : "text",
            status: file.name?.startsWith(".env") ? "blocked" : "accepted",
            summary: file.name?.startsWith(".env")
              ? "Secret or database files are blocked."
              : "File checked. Secret values are not shown in browser preview mode.",
            redacted: true,
          }));
        }
        if (args.command === "ping") {
          return { pong: true, version: "0.0.9" };
        }
        if (args.command === "/signal-init") {
          return "SignalOS project bootstrapped. Created .signalos runtime state, core strategy plan, command definitions, and IDE integrations.";
        }
        if (args.command === "/signal-status") {
          return "SignalOS status loaded. Phase: Onboarding. Next action: edit core/strategy/PLAN.md.";
        }
        if (args.command === "/signal-brain") {
          return "No notes yet.";
        }
        return null;
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
      case "test_provider_connection":
        return { ok: true, message: "Provider responded in browser preview mode.", model_count: 3 };
      case "send_provider_message":
        if (String(args.message || "").includes("SignalOS Builder")) {
          return {
            text: JSON.stringify({
              summary: "Generated a local React task management app.",
              stack: "react-vite",
              entry_path: "src/main.jsx",
              run_instructions: "Run: npm install, then npm run dev",
              signalos_plan: {
                goal: "Create a usable first version of the requested app.",
                user_journey: ["Open the app", "Add an item", "Edit state", "Filter the list"],
                scope: ["Local browser app", "Clean task workflow", "Persistent local storage"],
                tasks: ["Create React shell", "Add task state", "Add filters", "Document run command"],
                risks: ["No backend yet", "Local-only persistence"],
                acceptance: ["App starts locally", "User can create and complete tasks"],
              },
              files: [
                { path: "package.json", content: "{\n  \"scripts\": { \"dev\": \"vite\" },\n  \"dependencies\": { \"@vitejs/plugin-react\": \"^4.0.0\", \"vite\": \"^5.0.0\", \"react\": \"^18.2.0\", \"react-dom\": \"^18.2.0\" },\n  \"devDependencies\": {}\n}\n" },
                { path: "index.html", content: "<div id=\"root\"></div><script type=\"module\" src=\"/src/main.jsx\"></script>\n" },
                { path: "src/main.jsx", content: "import React from 'react';\nimport { createRoot } from 'react-dom/client';\nimport './styles.css';\nimport App from './App.jsx';\n\ncreateRoot(document.getElementById('root')).render(<App />);\n" },
                { path: "src/App.jsx", content: "export default function App() {\n  return <main className=\"app\"><h1>Task Manager</h1><p>Browser preview generated this starter.</p></main>;\n}\n" },
                { path: "src/styles.css", content: "body { margin: 0; font-family: system-ui, sans-serif; background: #f6f5f2; color: #1e1d1a; } .app { max-width: 880px; margin: 48px auto; padding: 24px; background: white; border: 1px solid #e5e1d8; border-radius: 8px; }\n" },
                { path: "README.md", content: "# Generated App\n\nRun `npm install` then `npm run dev`.\n" },
              ],
            }),
            tokens_in: 1200,
            tokens_out: 1800,
            provider: args.provider,
            model: args.model || "",
          };
        }
        return {
          text: `Browser preview response for: ${args.message || ""}`,
          tokens_in: 12,
          tokens_out: 18,
          provider: args.provider,
          model: args.model || "",
        };
      default:                  return null;
    }
  });
}
