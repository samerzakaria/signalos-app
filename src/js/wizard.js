/**
 * wizard.js — First-run onboarding wizard (Wave 1 / G0-2)
 *
 * Six steps: Welcome → Folder → Init consent → AI → Budget+Privacy → Done.
 * Persisted state: per-app via localStorage key `signalos.onboarding.wizard.v1`.
 * Resumes at the first incomplete step on re-launch. Forward-only-by-default,
 * Back allowed. Each step's `Continue` is gated on real validation (folder
 * exists/writable, AI real chat-ping succeeds, budget is numeric).
 *
 * Spec: docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.4b
 */

import * as ipc from "./ipc.js";
import { errorMessage, isProviderAuthFailure, providerConnectionMessage } from "./util.js";

const LS_WIZARD = "signalos.onboarding.wizard.v1";
// v2: bumped because WebView2 persists localStorage per app identifier
// (com.signalos.desktop), so saves from prior beta installs were skipping
// onboarding on v1.0.0-internal1. Bumping the version invalidates them.
const WIZARD_VERSION = 2;
const STEPS = ["welcome", "folder", "init", "identity", "ai", "budget", "done"];
const STEP_LABELS = ["Welcome", "Folder", "Init", "Identity", "AI", "Budget", "Done"];

export const wizardState = {
  active: false,
  current: 0,
  completedSteps: [],
  folder: "",
  folderCheck: null,         // { exists, writable, empty, entries }
  initMode: "keep",          // full | keep | minimal | skip
  identity: { name: "", role: "PO" },
  ai: {
    provider: "",
    apiKey: "",
    model: "",
    models: [],
    tested: false,
    testMessage: "",
  },
  budgetUsd: 10,
  privacy: {
    redactEnv: true,
    blockSecretFiles: true,
    localOnly: false,
  },
};

let host = null;        // host element where the wizard renders
let onComplete = null;  // callback fired when the wizard finishes
let providers = [];

// ─── Persistence ──────────────────────────────────────────────────────────────

function load() {
  try {
    const raw = localStorage.getItem(LS_WIZARD);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.version !== WIZARD_VERSION) return null;
    return parsed;
  } catch {
    return null;
  }
}

function save() {
  const payload = {
    version: WIZARD_VERSION,
    completedSteps: wizardState.completedSteps,
    current: wizardState.current,
    folder: wizardState.folder,
    initMode: wizardState.initMode,
    ai: {
      provider: wizardState.ai.provider,
      model: wizardState.ai.model,
      models: wizardState.ai.models,
      tested: wizardState.ai.tested,
    },
    budgetUsd: wizardState.budgetUsd,
    privacy: wizardState.privacy,
    finishedAt: wizardState.completedSteps.includes("done") ? new Date().toISOString() : null,
  };
  try { localStorage.setItem(LS_WIZARD, JSON.stringify(payload)); } catch {}
}

export function isFinished() {
  const s = load();
  if (!s?.completedSteps?.includes("done")) return false;
  // A genuinely completed onboarding recorded a projects root (finishOnboarding
  // writes `projectsRoot`; the legacy wizard used `folder`). A persisted "done"
  // flag with no root — corrupt/partial state, or a build predating the root
  // step — must re-show onboarding rather than drop the user into an app with
  // nowhere to place products and an active folder of (none).
  return Boolean(String(s?.projectsRoot || s?.folder || "").trim());
}

export function resetWizard() {
  try { localStorage.removeItem(LS_WIZARD); } catch {}
}

// ─── Mount / unmount ──────────────────────────────────────────────────────────

export async function maybeRunWizard({ hostEl, providerList, onDone }) {
  host = hostEl;
  providers = providerList || [];
  onComplete = onDone || (() => {});

  if (isFinished()) return false;

  const saved = load();
  if (saved) {
    Object.assign(wizardState, {
      current: Math.min(saved.current ?? 0, STEPS.length - 1),
      completedSteps: Array.isArray(saved.completedSteps) ? saved.completedSteps : [],
      folder: saved.folder || "",
      initMode: saved.initMode || "keep",
      budgetUsd: saved.budgetUsd ?? 10,
      privacy: { ...wizardState.privacy, ...(saved.privacy || {}) },
      ai: { ...wizardState.ai, ...(saved.ai || {}), apiKey: "", tested: false },
    });
  }

  wizardState.active = true;
  render();
  return true;
}

export function closeWizard() {
  wizardState.active = false;
  if (host) host.innerHTML = "";
  if (host) host.classList.remove("wizard-open");
  document.body.classList.remove("setup-pending");
}

// ─── Render ───────────────────────────────────────────────────────────────────

function render() {
  if (!host) return;
  host.classList.add("wizard-open");
  document.body.classList.add("setup-pending");
  const step = STEPS[wizardState.current];
  host.innerHTML = `
    <div class="wizard-overlay" role="dialog" aria-modal="true" aria-labelledby="wizard-title">
      <div class="wizard-card">
        <header class="wizard-header">
          <div class="wizard-title" id="wizard-title">Foundry &mdash; first-time setup</div>
        </header>
        <div class="wizard-dots" role="progressbar" aria-valuenow="${wizardState.current + 1}" aria-valuemin="1" aria-valuemax="${STEPS.length}">
          ${STEPS.map((id, i) => {
            const done = wizardState.completedSteps.includes(id);
            const here = i === wizardState.current;
            return `<span class="wizard-dot ${here ? "current" : ""} ${done ? "done" : ""}" title="${STEP_LABELS[i]}"></span>`;
          }).join("")}
        </div>
        <div class="wizard-body" id="wizard-body">${renderStep(step)}</div>
        <footer class="wizard-footer">
          <button class="wizard-back ghost" type="button" id="wiz-back" ${wizardState.current === 0 ? "disabled" : ""}>← Back</button>
          <div class="wizard-step-label">Step ${wizardState.current + 1} of ${STEPS.length} · ${STEP_LABELS[wizardState.current]}</div>
          <button class="wizard-next primary" type="button" id="wiz-next">${step === "done" ? "Start building →" : "Continue →"}</button>
        </footer>
      </div>
    </div>
  `;

  // Wire events
  host.querySelector("#wiz-back")?.addEventListener("click", onBack);
  host.querySelector("#wiz-next")?.addEventListener("click", onNext);
  wireStepEvents(step);
  refreshNextEnabled(step);
}

function renderStep(step) {
  switch (step) {
    case "welcome":
      return `
        <h2>Welcome to Foundry</h2>
        <p>Build apps with AI. Keep your folder, your keys, and your budget under your control.</p>
        <p>Before we start, we'll:</p>
        <ul class="wizard-list">
          <li>✓ Pick a project folder</li>
          <li>✓ Decide what Foundry may write to it</li>
          <li>✓ Connect an AI provider (cloud or local Ollama)</li>
          <li>✓ Set a monthly budget</li>
        </ul>
        <p class="fine-print">You can run this wizard again from Settings → Reset onboarding.</p>
      `;
    case "folder":
      return `
        <h2>Where do you want to work?</h2>
        <p>Foundry will only ever read or write inside this folder.</p>
        <div class="wizard-row">
          <input type="text" id="wiz-folder" value="${escapeAttr(wizardState.folder)}" placeholder="C:\\Users\\you\\projects\\my-app" />
          <button class="secondary small" type="button" id="wiz-browse">Browse…</button>
        </div>
        <div class="wizard-folder-check" id="wiz-folder-check">
          ${renderFolderCheck()}
        </div>
      `;
    case "init":
      return `
        <h2>What may Foundry write to your folder?</h2>
        <p>Foundry scaffolds its governance files (plan, runtime state, command library, IDE hooks) so it can guide your work.</p>
        <fieldset class="wizard-radios">
          ${initOption("full", "Full Foundry setup", "Write the entire bundle. Overwrites any colliding file. Use only on an empty or brand-new folder.")}
          ${initOption("keep", "Keep my files (recommended)", "Write the bundle but never overwrite anything that already exists. Safe for non-empty folders.")}
          ${initOption("minimal", "Minimal", "Only the .signalos/ runtime state. No command library, no integrations.")}
          ${initOption("skip", "Skip for now", "Initialize later from Settings or the Setup step.")}
        </fieldset>
        <div id="wiz-init-result"></div>
      `;
    case "identity":
      return `
        <h2>Who are you?</h2>
        <p>Gate-signing checks this. PO signs G0/G1/G2/G3; PE signs G3/G4; QA signs G4/G5; DevOps signs deploy gates.</p>
        <div class="wizard-field">
          <label for="wiz-id-name">Your name (used on every signed gate)</label>
          <input id="wiz-id-name" type="text" value="${escapeAttr(wizardState.identity.name)}" placeholder="e.g. Samer Z." />
        </div>
        <div class="wizard-field">
          <label for="wiz-id-role">Role</label>
          <select id="wiz-id-role">
            <option value="PO" ${wizardState.identity.role === "PO" ? "selected" : ""}>PO — Product Owner</option>
            <option value="PE" ${wizardState.identity.role === "PE" ? "selected" : ""}>PE — Product Engineer</option>
            <option value="QA" ${wizardState.identity.role === "QA" ? "selected" : ""}>QA — Quality</option>
            <option value="DevOps" ${wizardState.identity.role === "DevOps" ? "selected" : ""}>DevOps</option>
          </select>
          <div class="fine-print">You can change this later in Settings.</div>
        </div>
      `;
    case "ai":
      return renderAIStep();
    case "budget":
      return `
        <h2>Cost control & privacy</h2>
        <div class="wizard-field">
          <label for="wiz-budget">Monthly budget (USD)</label>
          <input id="wiz-budget" type="number" min="0" step="1" value="${wizardState.budgetUsd}" />
          <div class="fine-print">You'll see a warning at 80% and a stop at 100%.</div>
        </div>
        <div class="wizard-field">
          <label class="wizard-check"><input type="checkbox" id="wiz-priv-env" ${wizardState.privacy.redactEnv ? "checked" : ""} /> Redact <code>.env</code> values from chat and exports</label>
          <label class="wizard-check"><input type="checkbox" id="wiz-priv-block" ${wizardState.privacy.blockSecretFiles ? "checked" : ""} /> Block uploading database / certificate / key files</label>
          <label class="wizard-check"><input type="checkbox" id="wiz-priv-local" ${wizardState.privacy.localOnly ? "checked" : ""} /> Local AI only — never send anything to a cloud provider</label>
        </div>
      `;
    case "done":
      return `
        <h2>You're ready.</h2>
        <div class="wizard-summary">
          <div><strong>Project</strong><span>${escapeHtml(basename(wizardState.folder) || "—")}</span></div>
          <div><strong>AI</strong><span>${escapeHtml(providerName(wizardState.ai.provider))} · ${escapeHtml(wizardState.ai.model || "(default)")}</span></div>
          <div><strong>Budget</strong><span>$${Number(wizardState.budgetUsd || 0).toFixed(2)} / month</span></div>
          <div><strong>Init</strong><span>${initLabel(wizardState.initMode)}</span></div>
        </div>
        <p>Try saying:</p>
        <ul class="wizard-list">
          <li><code>Build a todo app with priorities</code></li>
          <li><code>/signal-status</code></li>
          <li><code>Add a settings page</code></li>
        </ul>
      `;
    default:
      return "";
  }
}

function renderFolderCheck() {
  const c = wizardState.folderCheck;
  if (!c) return `<div class="fine-print">Pick a folder to validate it.</div>`;
  if (c.error) return `<div class="wizard-error">${escapeHtml(c.error)}</div>`;
  const lines = [];
  lines.push(`<div class="wizard-check-line ${c.exists ? "ok" : "warn"}">${c.exists ? "✓ Folder exists" : "⚠ Folder does not exist"}</div>`);
  lines.push(`<div class="wizard-check-line ${c.writable ? "ok" : "warn"}">${c.writable ? "✓ You can write here" : "⚠ Folder is not writable"}</div>`);
  lines.push(`<div class="wizard-check-line ${c.empty ? "ok" : "warn"}">${c.empty ? "✓ Folder is empty" : `⚠ Folder is not empty (${c.entries?.length || 0} existing items)`}</div>`);
  if (!c.empty && c.entries?.length) {
    lines.push(`<div class="fine-print">Existing: ${c.entries.slice(0, 8).map(escapeHtml).join(", ")}${c.entries.length > 8 ? "…" : ""}</div>`);
  }
  return lines.join("");
}

function initOption(value, label, desc) {
  const checked = wizardState.initMode === value ? "checked" : "";
  return `
    <label class="wizard-radio">
      <input type="radio" name="wiz-init" value="${value}" ${checked} />
      <span><strong>${escapeHtml(label)}</strong><em>${escapeHtml(desc)}</em></span>
    </label>
  `;
}

function renderAIStep() {
  const selected = wizardState.ai.provider;
  const provInfo = providers.find((p) => p.id === selected) || providers[0] || { id: selected, name: selected, needs_key: true, model: "" };
  const models = Array.isArray(wizardState.ai.models) ? wizardState.ai.models : [];
  const opts = providers.map((p) =>
    `<option value="${escapeAttr(p.id)}" ${p.id === selected ? "selected" : ""}>${escapeHtml(p.name)}</option>`
  ).join("");
  const modelOpts = models.length
    ? `<option value="">Select model</option>` + models.map((m) => `<option value="${escapeAttr(m.id)}" ${m.id === wizardState.ai.model ? "selected" : ""}>${escapeHtml(m.name || m.id)}</option>`).join("")
    : `<option value="">Fetch models first</option>`;
  return `
    <h2>Connect AI</h2>
    <div class="wizard-field">
      <label for="wiz-provider">Provider</label>
      <select id="wiz-provider">${opts || `<option value="${escapeAttr(selected)}">${escapeHtml(selected)}</option>`}</select>
    </div>
    ${provInfo.needs_key ? `
    <div class="wizard-field">
      <label for="wiz-key">API key</label>
      <input id="wiz-key" type="password" autocomplete="off" placeholder="Paste once — saved in OS keychain" value="${escapeAttr(wizardState.ai.apiKey)}" />
    </div>
    ` : `<div class="fine-print">Ollama runs locally on <code>localhost:11434</code> — no key needed.</div>`}
    <div class="wizard-field">
      <label for="wiz-model">Model</label>
      <div class="wizard-row">
        <select id="wiz-model" ${models.length ? "" : "disabled"}>${modelOpts}</select>
        <button class="secondary small" type="button" id="wiz-fetch">Fetch models</button>
      </div>
      <div class="fine-print" id="wiz-model-help">${models.length ? `${models.length} models available from ${escapeHtml(provInfo.name || selected)}.` : "Fetch models from the selected provider before testing."}</div>
    </div>
    <div class="wizard-row">
      <button class="secondary" type="button" id="wiz-test">Test connection</button>
      <button class="ghost" type="button" id="wiz-local">Use local Ollama</button>
    </div>
    <div class="wizard-test-result" id="wiz-test-result">${
      wizardState.ai.tested
        ? `<div class="wizard-check-line ok">✓ ${escapeHtml(wizardState.ai.testMessage || "Provider responded.")}</div>`
        : `<div class="fine-print">Send a real chat ping to verify your key + model work before continuing.</div>`
    }</div>
  `;
}

// ─── Step wiring ──────────────────────────────────────────────────────────────

function wireStepEvents(step) {
  if (step === "folder") {
    host.querySelector("#wiz-folder")?.addEventListener("input", (e) => {
      wizardState.folder = e.target.value;
      wizardState.folderCheck = null;
      save();
      // Re-validate on debounce
      clearTimeout(wireStepEvents._t);
      wireStepEvents._t = setTimeout(() => { validateFolder().then(() => updateFolderCheckRender()); }, 400);
    });
    host.querySelector("#wiz-browse")?.addEventListener("click", async () => {
      const dialog = window.__TAURI__?.dialog;
      if (dialog?.open) {
        const result = await dialog.open({ directory: true, multiple: false, title: "Choose project folder" });
        const path = Array.isArray(result) ? result[0] : result;
        if (path) {
          wizardState.folder = path;
          save();
          render();
          await validateFolder();
          updateFolderCheckRender();
        }
      } else {
        const path = window.prompt("Project folder path");
        if (path) {
          wizardState.folder = path;
          save();
          render();
          await validateFolder();
          updateFolderCheckRender();
        }
      }
    });
    // Initial validate
    if (wizardState.folder && !wizardState.folderCheck) {
      validateFolder().then(() => updateFolderCheckRender());
    }
  }

  if (step === "init") {
    host.querySelectorAll('input[name="wiz-init"]').forEach((r) => {
      r.addEventListener("change", () => {
        wizardState.initMode = r.value;
        save();
        refreshNextEnabled(step);
      });
    });
  }

  if (step === "identity") {
    host.querySelector("#wiz-id-name")?.addEventListener("input", (e) => {
      wizardState.identity.name = e.target.value.trim();
      save();
      refreshNextEnabled(step);
    });
    host.querySelector("#wiz-id-role")?.addEventListener("change", (e) => {
      wizardState.identity.role = e.target.value;
      save();
    });
  }

  if (step === "ai") {
    host.querySelector("#wiz-provider")?.addEventListener("change", async (e) => {
      wizardState.ai.provider = e.target.value;
      wizardState.ai.tested = false;
      wizardState.ai.model = "";
      wizardState.ai.models = [];
      // Refresh saved-key info
      wizardState.ai.apiKey = "";
      save();
      render();
    });
    host.querySelector("#wiz-key")?.addEventListener("input", (e) => {
      wizardState.ai.apiKey = e.target.value;
      wizardState.ai.tested = false;
      refreshNextEnabled(step);
    });
    host.querySelector("#wiz-model")?.addEventListener("change", (e) => {
      wizardState.ai.model = e.target.value;
      wizardState.ai.tested = false;
      save();
      refreshNextEnabled(step);
    });
    host.querySelector("#wiz-fetch")?.addEventListener("click", onFetchModels);
    host.querySelector("#wiz-test")?.addEventListener("click", onTestAI);
    host.querySelector("#wiz-local")?.addEventListener("click", () => {
      wizardState.ai.provider = "ollama";
      wizardState.ai.tested = false;
      wizardState.ai.model = "";
      wizardState.ai.models = [];
      wizardState.privacy.localOnly = true;
      save();
      render();
    });
  }

  if (step === "budget") {
    host.querySelector("#wiz-budget")?.addEventListener("input", (e) => {
      const v = Number(e.target.value);
      wizardState.budgetUsd = Number.isFinite(v) && v >= 0 ? v : 0;
      save();
      refreshNextEnabled(step);
    });
    ["wiz-priv-env", "wiz-priv-block", "wiz-priv-local"].forEach((id) => {
      host.querySelector(`#${id}`)?.addEventListener("change", (e) => {
        if (id === "wiz-priv-env") wizardState.privacy.redactEnv = e.target.checked;
        if (id === "wiz-priv-block") wizardState.privacy.blockSecretFiles = e.target.checked;
        if (id === "wiz-priv-local") wizardState.privacy.localOnly = e.target.checked;
        save();
      });
    });
  }
}

function updateFolderCheckRender() {
  const node = host?.querySelector("#wiz-folder-check");
  if (node) node.innerHTML = renderFolderCheck();
  refreshNextEnabled("folder");
}

async function validateFolder() {
  const path = wizardState.folder.trim();
  if (!path) {
    wizardState.folderCheck = null;
    return;
  }
  try {
    // Reuse get_project_artifacts as a folder existence + read probe.
    // Setting workspace also validates exists+is_dir on the Rust side.
    await ipc.workspace.set(path);
    const artifacts = await ipc.project.artifacts();
    const existing = Array.isArray(artifacts?.artifacts)
      ? artifacts.artifacts.filter((a) => a.exists).map((a) => a.path)
      : [];
    // Heuristic: empty if no existing artifacts AND no top-level user files we can guess at.
    // We don't have a list_dir IPC, so consider "any artifact exists" → not empty.
    wizardState.folderCheck = {
      exists: true,
      writable: true,
      empty: existing.length === 0,
      entries: existing,
      error: null,
    };
  } catch (e) {
    wizardState.folderCheck = { exists: false, writable: false, empty: true, entries: [], error: errorMessage(e) };
  }
}

async function onFetchModels() {
  const btn = host.querySelector("#wiz-fetch");
  if (btn) { btn.disabled = true; btn.textContent = "Fetching…"; }
  let shouldRender = false;
  try {
    const models = await ipc.provider.fetchModels(wizardState.ai.provider, wizardState.ai.apiKey || null);
    const help = host.querySelector("#wiz-model-help");
    if (Array.isArray(models) && models.length) {
      wizardState.ai.models = models;
      if (wizardState.ai.model && !models.some((model) => model.id === wizardState.ai.model)) {
        wizardState.ai.model = "";
      }
      if (help) help.textContent = `${models.length} models found. Select one before testing.`;
      shouldRender = true;
    } else {
      wizardState.ai.models = [];
      wizardState.ai.model = "";
      if (help) help.textContent = "Provider returned no models.";
      shouldRender = true;
    }
  } catch (e) {
    const help = host.querySelector("#wiz-model-help");
    if (isProviderAuthFailure(e)) {
      try { await ipc.keychain.delete(wizardState.ai.provider); } catch {}
    }
    if (help) help.textContent = providerConnectionMessage(e, providerName(wizardState.ai.provider));
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Fetch models"; }
    save();
    if (shouldRender) render();
    refreshNextEnabled("ai");
  }
}

async function onTestAI() {
  const btn = host.querySelector("#wiz-test");
  const result = host.querySelector("#wiz-test-result");
  if (btn) { btn.disabled = true; btn.textContent = "Testing…"; }
  if (result) result.innerHTML = `<div class="fine-print">Sending a real chat ping…</div>`;
  try {
    await ipc.provider.setActive(wizardState.ai.provider);
    if (!wizardState.ai.model) {
      throw new Error("Select a model before testing the provider.");
    }
    if (wizardState.ai.model) {
      await ipc.provider.setModel(wizardState.ai.provider, wizardState.ai.model);
    }
    const res = await ipc.provider.test(
      wizardState.ai.provider,
      wizardState.ai.apiKey || null,
      wizardState.ai.model || null
    );
    wizardState.ai.tested = Boolean(res?.ok);
    wizardState.ai.testMessage = res?.message || "Provider responded.";
    if (wizardState.ai.tested && wizardState.ai.apiKey && providers.find((p) => p.id === wizardState.ai.provider)?.needs_key) {
      await ipc.keychain.store(wizardState.ai.provider, wizardState.ai.apiKey);
    }
    if (result) {
      result.innerHTML = wizardState.ai.tested
        ? `<div class="wizard-check-line ok">✓ ${escapeHtml(wizardState.ai.testMessage)}</div>`
        : `<div class="wizard-error">${escapeHtml(wizardState.ai.testMessage)}</div>`;
    }
  } catch (e) {
    if (isProviderAuthFailure(e)) {
      try { await ipc.keychain.delete(wizardState.ai.provider); } catch {}
    }
    wizardState.ai.tested = false;
    wizardState.ai.testMessage = providerConnectionMessage(e, providerName(wizardState.ai.provider));
    if (result) result.innerHTML = `<div class="wizard-error">${escapeHtml(wizardState.ai.testMessage)}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Test connection"; }
    save();
    refreshNextEnabled("ai");
  }
}

// ─── Navigation ───────────────────────────────────────────────────────────────

function refreshNextEnabled(step) {
  const btn = host?.querySelector("#wiz-next");
  if (!btn) return;
  btn.disabled = !canAdvance(step);
}

function canAdvance(step) {
  switch (step) {
    case "welcome":  return true;
    case "folder":   return Boolean(wizardState.folder && wizardState.folderCheck && !wizardState.folderCheck.error);
    case "init":     return ["full", "keep", "minimal", "skip"].includes(wizardState.initMode);
    case "identity": return Boolean(wizardState.identity.name && wizardState.identity.role);
    case "ai":       return wizardState.ai.provider === "ollama" || wizardState.ai.tested;
    case "budget":   return Number.isFinite(wizardState.budgetUsd) && wizardState.budgetUsd >= 0;
    case "done":     return true;
    default:         return false;
  }
}

async function onNext() {
  const step = STEPS[wizardState.current];
  if (!canAdvance(step)) return;

  if (!wizardState.completedSteps.includes(step)) {
    wizardState.completedSteps.push(step);
  }

  // Side effects when leaving each step
  if (step === "init" && wizardState.initMode !== "skip" && wizardState.folder) {
    const result = host.querySelector("#wiz-init-result");
    // Don't move forward until the init actually completes — surface the
    // success or failure inline so the user can react.
    try {
      const text = await ipc.signal.runAndWait("/signal-init", ["--mode", wizardState.initMode]);
      // Probe artifacts to confirm scaffolding really landed.
      const artifacts = await ipc.project.artifacts();
      if (!artifacts?.initialized) {
        const msg = `Init ran (${wizardState.initMode}) but expected Foundry governance files are still missing.\n\n${text || ""}`;
        if (result) result.innerHTML = `<div class="wizard-error">${escapeHtml(msg)}</div>`;
        throw new Error(msg);
      }
    } catch (e) {
      const errMsg = `Init (${wizardState.initMode}) failed: ${errorMessage(e)}`;
      if (result) result.innerHTML = `<div class="wizard-error">${escapeHtml(errMsg)}</div>`;
      // Block forward navigation: re-add the step to the list of incomplete.
      wizardState.completedSteps = wizardState.completedSteps.filter((s) => s !== "init");
      save();
      // Surface to the toast layer too if the host wired one.
      try { onComplete?.({ failed: true, reason: errMsg }); } catch {}
      return;
    }
  }
  if (step === "identity") {
    try {
      await ipc.identity.set(wizardState.identity.name, wizardState.identity.role);
    } catch (e) {
      console.warn("Could not set identity:", e);
    }
  }
  if (step === "budget") {
    try { await ipc.provider.setBudget(Number(wizardState.budgetUsd || 0)); } catch {}
  }

  if (wizardState.current >= STEPS.length - 1) {
    save();
    closeWizard();
    onComplete?.();
    return;
  }
  wizardState.current += 1;
  save();
  render();
}

function onBack() {
  if (wizardState.current > 0) {
    wizardState.current -= 1;
    save();
    render();
  }
}

// ─── helpers ──────────────────────────────────────────────────────────────────

function escapeHtml(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(v) {
  return escapeHtml(v);
}

function basename(path) {
  if (!path) return "";
  return String(path).split(/[\\/]/).filter(Boolean).pop() || path;
}

function initLabel(mode) {
  return { full: "Full Foundry setup", keep: "Keep my files", minimal: "Minimal", skip: "Skipped (run /signal-init later)" }[mode] || mode;
}

function providerName(id) {
  return providers.find((p) => p.id === id)?.name || id;
}
