import * as ipc from "./ipc.js";

const LS_WORKSPACE = "signalos.workspace";

const state = {
  workspace: null,
  providers: [],
  activeProvider: "anthropic",
  activeProviderInfo: null,
  hasKey: false,
  cost: null,
  wave: null,
  gates: [],
  brain: [],
  audit: [],
  secrets: [],
  git: null,
  statusChecked: false,
  busy: false,
  view: "guide",
  sidecarError: "",
  log: [],
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const el = {
  workspaceLabel: $("#workspaceLabel"),
  waveLabel: $("#waveLabel"),
  viewTitle: $("#viewTitle"),
  viewSubtitle: $("#viewSubtitle"),
  statusPill: $("#statusPill"),
  providerLabel: $("#providerLabel"),
  costLabel: $("#costLabel"),
  activeStepLabel: $("#activeStepLabel"),
  guideLead: $("#guideLead"),
  guideDetail: $("#guideDetail"),
  mainAction: $("#mainAction"),
  secondaryAction: $("#secondaryAction"),
  projectPath: $("#projectPath"),
  keyStatus: $("#keyStatus"),
  statusText: $("#statusText"),
  nextActionText: $("#nextActionText"),
  providerSelect: $("#providerSelect"),
  providerModel: $("#providerModel"),
  providerKey: $("#providerKey"),
  keyField: $("#keyField"),
  providerHelp: $("#providerHelp"),
  gateList: $("#gateList"),
  activityLog: $("#activityLog"),
  commandForm: $("#commandForm"),
  commandInput: $("#commandInput"),
  sidecarWarning: $("#sidecarWarning"),
  brainSearch: $("#brainSearch"),
  brainList: $("#brainList"),
  brainForm: $("#brainForm"),
  brainType: $("#brainType"),
  brainText: $("#brainText"),
  historyList: $("#historyList"),
  statusSummary: $("#statusSummary"),
  settingsWorkspace: $("#settingsWorkspace"),
  settingsProvider: $("#settingsProvider"),
  settingsModel: $("#settingsModel"),
  settingsCost: $("#settingsCost"),
  settingsSecrets: $("#settingsSecrets"),
  toast: $("#toast"),
};

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function basename(path) {
  if (!path) return "";
  return String(path).split(/[\\/]/).filter(Boolean).pop() || path;
}

function safeText(value, fallback = "") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function escapeHtml(value) {
  return safeText(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function safeCall(fn, fallback = null) {
  try {
    return await fn();
  } catch (error) {
    console.warn(error);
    return fallback;
  }
}

function toast(message) {
  el.toast.textContent = message;
  el.toast.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.toast.classList.remove("show"), 2600);
}

function providerNeedsKey() {
  return Boolean(state.activeProviderInfo?.needs_key);
}

function aiReady() {
  if (!state.activeProviderInfo) return false;
  return !providerNeedsKey() || state.hasKey;
}

function hasActiveWave() {
  const name = safeText(state.wave?.name).toLowerCase();
  return Boolean(state.wave) && !name.includes("no active wave") && !name.includes("wave -");
}

function currentGate() {
  return state.gates.find((gate) => gate.status === "current") || state.gates[0] || null;
}

function currentStep() {
  if (!state.workspace) return "project";
  if (!aiReady()) return "ai";
  if (!state.statusChecked && !hasActiveWave()) return "status";
  return "start";
}

function nextAction() {
  const step = currentStep();
  if (step === "project") {
    return {
      label: "Choose project folder",
      title: "Choose the project you want to use with SignalOS.",
      detail: "After this, SignalOS remembers the folder and guides the next step.",
      run: chooseWorkspace,
      secondary: { label: "Check project", run: checkStatus },
    };
  }

  if (step === "ai") {
    const needsKey = providerNeedsKey();
    return {
      label: "Save AI connection",
      title: needsKey ? "Paste the AI key once, then save." : "Save the AI connection.",
      detail: "This unlocks one-click project checks and guided SignalOS actions.",
      run: saveProvider,
      disabled: needsKey && !state.hasKey && !el.providerKey.value.trim(),
      secondary: { label: "Use local AI", run: useLocalProvider },
    };
  }

  if (step === "status") {
    return {
      label: "Check project",
      title: "Let SignalOS check the project.",
      detail: "This checks whether the project is already set up and which step is next.",
      run: checkStatus,
      secondary: { label: "Choose another folder", run: chooseWorkspace },
    };
  }

  if (!hasActiveWave()) {
    return {
      label: "Set up project",
      title: "Set up this project for guided work.",
      detail: "This creates the local SignalOS project files so the team can start safely.",
      run: () => runSignalCommand("/signal-init"),
      secondary: { label: "Check again", run: checkStatus },
    };
  }

  const gate = currentGate();
  return {
    label: "Show next step",
    title: gate ? `Next gate: ${gate.name}` : "Keep moving with the next safe action.",
    detail: gate?.desc || "SignalOS will show the latest project status and suggested work.",
    run: () => runSignalCommand("/signal-status"),
    secondary: { label: "Open notes", run: () => switchView("brain") },
  };
}

function render() {
  renderShell();
  renderSteps();
  renderGuide();
  renderProviderForm();
  renderGates();
  renderActivity();
  renderBrain();
  renderHistory();
  renderSettings();
}

function renderShell() {
  const projectName = state.workspace ? basename(state.workspace) : "No project chosen";
  const waveName = state.wave?.name || "No active wave";
  el.workspaceLabel.textContent = projectName;
  el.waveLabel.textContent = waveName;
  el.projectPath.textContent = state.workspace || "No folder selected yet.";
  el.providerLabel.textContent = state.activeProviderInfo?.name || "AI not connected";
  el.costLabel.textContent = currency.format(Number(state.cost?.session_usd || 0));

  if (state.sidecarError) {
    el.sidecarWarning.textContent = `The SignalOS engine did not start: ${state.sidecarError}`;
    el.sidecarWarning.classList.add("show");
  } else {
    el.sidecarWarning.classList.remove("show");
  }

  const ready = state.workspace && aiReady();
  const error = Boolean(state.sidecarError);
  el.statusPill.className = `pill ${error ? "error" : ready ? "ready" : ""}`;
  el.statusPill.innerHTML = `<span class="pill-dot"></span><span>${error ? "Needs fix" : ready ? "Ready" : "Setting up"}</span>`;

  const titles = {
    guide: ["Guide", "One clear next step at a time."],
    brain: ["Notes", "Saved beliefs, decisions, notes, and QA evidence."],
    history: ["History", "Audit trail and current project status."],
    settings: ["Settings", "Workspace, AI connection, and secrets."],
  };
  const [title, subtitle] = titles[state.view] || titles.guide;
  el.viewTitle.textContent = title;
  el.viewSubtitle.textContent = subtitle;

  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${state.view}`));
  $$("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
}

function renderSteps() {
  const step = currentStep();
  const done = {
    project: Boolean(state.workspace),
    ai: aiReady(),
    status: state.statusChecked || hasActiveWave(),
    start: hasActiveWave(),
  };

  $$("[data-step], [data-step-row]").forEach((node) => {
    const id = node.dataset.step || node.dataset.stepRow;
    node.classList.toggle("active", id === step);
    node.classList.toggle("done", Boolean(done[id]));
  });
}

function renderGuide() {
  const action = nextAction();
  const stepNames = {
    project: "Step 1 of 4",
    ai: "Step 2 of 4",
    status: "Step 3 of 4",
    start: "Step 4 of 4",
  };

  el.activeStepLabel.textContent = stepNames[currentStep()] || "Next step";
  el.guideLead.textContent = action.title;
  el.guideDetail.textContent = action.detail;
  el.mainAction.textContent = state.busy ? "Working..." : action.label;
  el.mainAction.disabled = state.busy || Boolean(action.disabled);
  el.mainAction.onclick = () => action.run();

  el.secondaryAction.textContent = action.secondary?.label || "Refresh";
  el.secondaryAction.disabled = state.busy;
  el.secondaryAction.onclick = () => (action.secondary?.run || refreshAll)();

  const providerName = state.activeProviderInfo?.name || "No provider selected";
  el.keyStatus.textContent = aiReady()
    ? `${providerName} is ready.`
    : providerNeedsKey()
      ? `${providerName} needs an API key.`
      : "Choose and save an AI provider.";

  el.statusText.textContent = state.statusChecked
    ? (state.wave?.phase_name ? `${state.wave.phase_name}. ${state.wave?.progress_pct || 0}% complete.` : "Status loaded.")
    : "Not checked yet.";

  const gate = currentGate();
  el.nextActionText.textContent = hasActiveWave()
    ? (gate ? `Work toward ${gate.name}.` : "Review the latest status.")
    : "Set up the project after the check.";
}

function renderProviderForm() {
  const primaryProviderIds = ["anthropic", "openai", "gemini", "qwen", "ollama"];
  const renderOption = (provider) => {
    const selected = provider.id === state.activeProvider ? "selected" : "";
    return `<option value="${escapeHtml(provider.id)}" ${selected}>${escapeHtml(provider.name)}</option>`;
  };
  const primary = state.providers.filter((provider) => primaryProviderIds.includes(provider.id));
  const more = state.providers.filter((provider) => !primaryProviderIds.includes(provider.id));
  const primaryOptions = primary.map(renderOption).join("");
  const moreOptions = more.length
    ? `<optgroup label="More providers">${more.map(renderOption).join("")}</optgroup>`
    : "";
  el.providerSelect.innerHTML = `${primaryOptions}${moreOptions}`;

  if (document.activeElement !== el.providerModel) {
    el.providerModel.value = state.activeProviderInfo?.model || "";
  }

  el.keyField.style.display = providerNeedsKey() ? "grid" : "none";
  el.providerHelp.textContent = providerNeedsKey()
    ? (state.hasKey ? "A key is already saved on this computer." : "Keys are saved securely on this computer.")
    : "This AI service does not need a key.";
}

function renderGates() {
  if (!state.workspace) {
    el.gateList.innerHTML = `<div class="empty">Choose a project to see steps.</div>`;
    return;
  }

  if (!state.gates.length) {
    el.gateList.innerHTML = `<div class="empty">No project step status loaded yet.</div>`;
    return;
  }

  el.gateList.innerHTML = state.gates.map((gate) => `
    <div class="gate ${escapeHtml(gate.status || "")}">
      <div class="gate-id">G${escapeHtml(gate.id)}</div>
      <div>
        <div class="item-title">${escapeHtml(gate.name || `Gate ${gate.id}`)}</div>
        <div class="item-meta">${escapeHtml(gate.desc || "")}</div>
      </div>
      <div class="gate-status">${escapeHtml(gate.status || "open")}</div>
    </div>
  `).join("");
}

function renderActivity() {
  if (!state.log.length) {
    el.activityLog.innerHTML = `
      <div class="empty">
        <div>
          <strong>Nothing has run yet.</strong>
          <div>Use the big next button or check the project.</div>
        </div>
      </div>
    `;
    return;
  }

  el.activityLog.innerHTML = state.log.map((entry) => `
    <div class="log-entry">
      <strong>${escapeHtml(entry.title)}</strong>
      <pre>${escapeHtml(entry.body)}</pre>
    </div>
  `).join("");
  el.activityLog.scrollTop = el.activityLog.scrollHeight;
}

function renderBrain() {
  if (!state.workspace) {
    el.brainList.innerHTML = `<div class="empty">Choose a project before using notes.</div>`;
    return;
  }
  if (!state.brain.length) {
    el.brainList.innerHTML = `<div class="empty">No notes yet for this project.</div>`;
    return;
  }
  el.brainList.innerHTML = state.brain.map((entry) => `
    <div class="item">
      <div class="item-title">${escapeHtml(entry.type || "note")}</div>
      <div>${escapeHtml(entry.text || "")}</div>
      <div class="item-meta">${escapeHtml([entry.wave, entry.gate, entry.ts].filter(Boolean).join(" . "))}</div>
    </div>
  `).join("");
}

function renderHistory() {
  if (!state.workspace) {
    el.historyList.innerHTML = `<div class="empty">Choose a project to see history.</div>`;
  } else if (!state.audit.length) {
    el.historyList.innerHTML = `<div class="empty">No audit entries yet.</div>`;
  } else {
    el.historyList.innerHTML = state.audit.map((entry) => {
      const title = entry.event || entry.action || entry.type || "Audit entry";
      const body = entry.message || entry.summary || entry.path || JSON.stringify(entry, null, 2);
      const meta = entry.ts || entry.time || entry.created_at || "";
      return `
        <div class="item">
          <div class="item-title">${escapeHtml(title)}</div>
          <div>${escapeHtml(body)}</div>
          <div class="item-meta">${escapeHtml(meta)}</div>
        </div>
      `;
    }).join("");
  }

  const branch = state.git?.branch ? `Branch ${state.git.branch}` : "No Git branch loaded";
  const clean = state.git ? (state.git.is_clean ? "clean" : "has changes") : "";
  el.statusSummary.textContent = [
    state.wave?.name || "No active wave",
    state.wave?.phase_name || "Onboarding",
    branch,
    clean,
  ].filter(Boolean).join(" . ");
}

function renderSettings() {
  el.settingsWorkspace.textContent = state.workspace || "No project chosen";
  el.settingsProvider.textContent = state.activeProviderInfo?.name || "Not connected";
  el.settingsModel.textContent = state.activeProviderInfo?.model || "Not set";
  el.settingsCost.textContent = `${currency.format(Number(state.cost?.session_usd || 0))} this session`;
  el.settingsSecrets.textContent = secretSummary();
}

function secretSummary() {
  if (!state.workspace) return "Choose a project to check secrets.";
  const files = Array.isArray(state.secrets) ? state.secrets : [];
  if (!files.length) return "No .env or key files found.";

  const variableNames = files
    .flatMap((file) => Array.isArray(file.variables) ? file.variables : [])
    .slice(0, 8);
  const suffix = variableNames.length ? ` Variables: ${variableNames.join(", ")}.` : "";
  const fileLabel = files.length === 1 ? "secret file" : "secret files";
  return `${files.length} ${fileLabel} found. Values stay hidden.${suffix}`;
}

function setBusy(isBusy) {
  state.busy = isBusy;
  render();
}

async function loadBasics() {
  const savedWorkspace = localStorage.getItem(LS_WORKSPACE);
  const backendWorkspace = await safeCall(() => ipc.workspace.get(), null);

  if (backendWorkspace) {
    state.workspace = backendWorkspace;
    localStorage.setItem(LS_WORKSPACE, backendWorkspace);
  } else if (savedWorkspace) {
    const restored = await safeCall(async () => {
      await ipc.workspace.set(savedWorkspace);
      return savedWorkspace;
    }, null);
    state.workspace = restored;
  }

  state.providers = await safeCall(() => ipc.provider.list(), []);
  state.activeProvider = await safeCall(() => ipc.provider.getActive(), state.activeProvider);
  state.activeProviderInfo = state.providers.find((provider) => provider.id === state.activeProvider) || state.providers[0] || null;
  if (state.activeProviderInfo && state.activeProviderInfo.id !== state.activeProvider) {
    state.activeProvider = state.activeProviderInfo.id;
  }
  state.hasKey = state.activeProviderInfo
    ? await safeCall(() => ipc.keychain.has(state.activeProviderInfo.id), false)
    : false;
  state.cost = await safeCall(() => ipc.provider.getCost(), null);
}

async function refreshProjectState(markChecked = false) {
  if (!state.workspace) {
    state.wave = null;
    state.gates = [];
    state.brain = [];
    state.audit = [];
    state.secrets = [];
    state.git = null;
    render();
    return;
  }

  const [wave, gates, brain, audit, secrets, git] = await Promise.all([
    safeCall(() => ipc.wave.get(), null),
    safeCall(() => ipc.gates.getAll(), []),
    safeCall(() => ipc.brain.search(el.brainSearch.value.trim()), []),
    safeCall(() => ipc.audit.list(50), []),
    safeCall(() => ipc.security.secrets(), []),
    safeCall(() => ipc.git.status(), null),
  ]);

  state.wave = wave;
  state.gates = Array.isArray(gates) ? gates : [];
  state.brain = Array.isArray(brain) ? brain : [];
  state.audit = Array.isArray(audit) ? audit : [];
  state.secrets = Array.isArray(secrets) ? secrets : [];
  state.git = git;
  if (markChecked) state.statusChecked = true;
  render();
}

async function refreshAll() {
  await loadBasics();
  await refreshProjectState(false);
}

async function chooseWorkspace() {
  const selected = await pickFolder();
  if (!selected) return;

  setBusy(true);
  try {
    await ipc.workspace.set(selected);
    localStorage.setItem(LS_WORKSPACE, selected);
    state.workspace = selected;
    state.statusChecked = false;
    await safeCall(() => ipc.workspace.startWatch(), null);
    await refreshProjectState(false);
    toast("Project folder saved.");
  } catch (error) {
    toast(error.message || "Could not use that folder.");
  } finally {
    setBusy(false);
  }
}

async function pickFolder() {
  const dialog = window.__TAURI__?.dialog;
  if (dialog?.open) {
    const result = await dialog.open({
      directory: true,
      multiple: false,
      title: "Choose project folder",
    });
    return Array.isArray(result) ? result[0] : result;
  }
  return window.prompt("Project folder path") || null;
}

async function forgetWorkspace() {
  localStorage.removeItem(LS_WORKSPACE);
  state.workspace = null;
  state.wave = null;
  state.gates = [];
  state.brain = [];
  state.audit = [];
  state.git = null;
  state.statusChecked = false;
  render();
  toast("Project forgotten in this app.");
}

async function saveProvider() {
  const provider = el.providerSelect.value || state.activeProvider;
  const info = state.providers.find((item) => item.id === provider);
  const model = el.providerModel.value.trim();
  const key = el.providerKey.value.trim();

  if (info?.needs_key && !state.hasKey && !key) {
    toast("Paste the AI key first.");
    return;
  }

  setBusy(true);
  try {
    await ipc.provider.setActive(provider);
    if (model) await ipc.provider.setModel(provider, model);
    if (info?.needs_key && key) {
      await ipc.keychain.store(provider, key);
      el.providerKey.value = "";
    }
    await loadBasics();
    toast("AI connection saved.");
  } catch (error) {
    toast(error.message || "Could not save AI service.");
  } finally {
    setBusy(false);
  }
}

async function useLocalProvider() {
  setBusy(true);
  try {
    await ipc.provider.setActive("ollama");
    await loadBasics();
    toast("Local AI selected.");
  } catch (error) {
    toast(error.message || "Could not select local AI.");
  } finally {
    setBusy(false);
  }
}

async function checkStatus() {
  if (!state.workspace) {
    await chooseWorkspace();
    return;
  }
  await runSignalCommand("/signal-status", [], { markChecked: true });
}

function parseCommand(input) {
  const parts = input.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return null;
  const command = parts[0].startsWith("/") ? parts[0] : `/${parts[0]}`;
  return { command, args: parts.slice(1) };
}

async function runSignalCommand(commandInput, args = [], options = {}) {
  if (!state.workspace) {
    toast("Choose a project first.");
    await chooseWorkspace();
    return;
  }

  const parsed = Array.isArray(args) && args.length
    ? { command: commandInput, args }
    : parseCommand(commandInput);
  if (!parsed) return;

  setBusy(true);
  addLog("Working on it", "Waiting for the SignalOS engine...");
  try {
    const result = await ipc.signal.runAndWait(parsed.command, parsed.args);
    replaceLastLog("Done", formatResult(result));
    await loadBasics();
    await refreshProjectState(Boolean(options.markChecked));
    if (options.markChecked) state.statusChecked = true;
    toast("Done.");
  } catch (error) {
    replaceLastLog("Could not finish", error.message || String(error));
    toast("Command failed.");
  } finally {
    setBusy(false);
  }
}

function formatResult(result) {
  if (typeof result === "string") return result.trim() || "Done.";
  if (result === null || result === undefined) return "Done.";
  return JSON.stringify(result, null, 2);
}

function addLog(title, body) {
  state.log.push({ title, body, ts: Date.now() });
  state.log = state.log.slice(-12);
  renderActivity();
}

function replaceLastLog(title, body) {
  if (!state.log.length) {
    addLog(title, body);
    return;
  }
  state.log[state.log.length - 1] = { title, body, ts: Date.now() };
  renderActivity();
}

function switchView(view) {
  state.view = view;
  render();
}

function bindEvents() {
  $("[data-view='guide']").addEventListener("click", () => switchView("guide"));
  $("[data-view='brain']").addEventListener("click", () => switchView("brain"));
  $("[data-view='history']").addEventListener("click", () => switchView("history"));
  $("[data-view='settings']").addEventListener("click", () => switchView("settings"));

  $("#chooseProject").addEventListener("click", chooseWorkspace);
  $("#settingsChooseProject").addEventListener("click", chooseWorkspace);
  $("#forgetProject").addEventListener("click", forgetWorkspace);
  $("#saveProvider").addEventListener("click", saveProvider);
  $("#quickOllama").addEventListener("click", useLocalProvider);
  $("#refreshButton").addEventListener("click", () => refreshProjectState(true));

  el.providerSelect.addEventListener("change", async () => {
    const selected = el.providerSelect.value;
    state.activeProvider = selected;
    state.activeProviderInfo = state.providers.find((provider) => provider.id === selected) || null;
    state.hasKey = state.activeProviderInfo
      ? await safeCall(() => ipc.keychain.has(state.activeProviderInfo.id), false)
      : false;
    render();
  });

  el.providerKey.addEventListener("input", renderGuide);

  el.commandForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = el.commandInput.value.trim();
    el.commandInput.value = "";
    runSignalCommand(value);
  });

  $$(".chip").forEach((button) => {
    button.addEventListener("click", () => runSignalCommand(button.dataset.command));
  });

  el.brainForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = el.brainText.value.trim();
    if (!text) return;
    setBusy(true);
    try {
      await ipc.brain.add(text, el.brainType.value);
      el.brainText.value = "";
      await refreshProjectState(false);
      toast("Saved to Brain.");
    } catch (error) {
      toast(error.message || "Could not save note.");
    } finally {
      setBusy(false);
    }
  });

  let brainTimer = null;
  el.brainSearch.addEventListener("input", () => {
    clearTimeout(brainTimer);
    brainTimer = setTimeout(() => refreshProjectState(false), 250);
  });

  const listen = window.__TAURI__?.event?.listen;
  if (listen) {
    listen("menu:open-workspace", chooseWorkspace);
    listen("menu:check-update", checkForUpdates);
    listen("menu:export-audit", () => switchView("history"));
    listen("menu:nav", (event) => {
      const mapped = {
        chat: "guide",
        dashboard: "history",
        brain: "brain",
        audit: "history",
      };
      switchView(mapped[event.payload] || "guide");
    });
    listen("sidecar:error", (event) => {
      state.sidecarError = safeText(event.payload, "Unknown sidecar error");
      render();
    });
  }
}

async function checkForUpdates() {
  const update = await safeCall(() => ipc.updater.check(), { available: false });
  if (update?.available) {
    toast(`Update available: ${update.version}`);
  } else {
    toast("No update available.");
  }
}

async function init() {
  bindEvents();
  render();
  await loadBasics();
  render();
  await refreshProjectState(false);
}

init();
