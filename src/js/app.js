import * as ipc from "./ipc.js";

const LS_WORKSPACE = "signalos.workspace";
const LS_TRANSCRIPT_PREFIX = "signalos.transcript.";
const LS_GATE_SIGNER = "signalos.gateSigner";
const LS_ONBOARDING_PREFIX = "signalos.onboarding.";
const LS_UPDATE_CHANNEL = "signalos.updateChannel";

const COMMAND_CATALOG = [
  { command: "/signal-status", label: "Check project", status: "ready", detail: "Loads phase, gates, and next action." },
  { command: "/signal-init", label: "Set up project", status: "ready", detail: "Creates local SignalOS project files." },
  { command: "/signal-brain", label: "Show notes", status: "ready", detail: "Lists or searches project notes." },
  { command: "/signal-plan", label: "Plan tools", status: "advanced", detail: "Runs render, validate, and list subcommands." },
  { command: "/signal-qa", label: "QA", status: "advanced", detail: "Runs the QA command through the bundled core." },
  { command: "/signal-qa-only", label: "QA only", status: "advanced", detail: "Runs QA-only checks through the bundled core." },
  { command: "/signal-learn", label: "Learn", status: "advanced", detail: "Runs the learning/brain workflow." },
  { command: "/signal-cso", label: "Security", status: "advanced", detail: "Runs security review workflows." },
  { command: "/signal-autoplan", label: "Auto plan", status: "advanced", detail: "Runs velocity planning tools." },
  { command: "/signal-context-restore", label: "Context restore", status: "advanced", detail: "Restores project context." },
  { command: "/signal-setup-deploy", label: "Setup deploy", status: "advanced", detail: "Creates deployment records." },
  { command: "/signal-land-deploy", label: "Land deploy", status: "advanced", detail: "Runs deployment landing workflow." },
  { command: "/signal-canary-deploy", label: "Canary deploy", status: "advanced", detail: "Runs canary deployment workflow." },
  { command: "/signal-benchmark", label: "Benchmark", status: "advanced", detail: "Runs benchmark workflow." },
  { command: "/signal-devex-plan", label: "Devex plan", status: "advanced", detail: "Runs developer-experience planning." },
  { command: "/signal-devex", label: "Devex", status: "advanced", detail: "Runs developer-experience workflow." },
  { command: "/signal-retro-global", label: "Global retro", status: "advanced", detail: "Runs retrospective workflow." },
  { command: "/signal-careful", label: "Careful mode", status: "advanced", detail: "Runs safety workflow." },
  { command: "/signal-freeze", label: "Freeze", status: "advanced", detail: "Freezes unsafe work." },
  { command: "/signal-guard", label: "Guard", status: "advanced", detail: "Runs guard checks." },
  { command: "/signal-unfreeze", label: "Unfreeze", status: "advanced", detail: "Releases a freeze." },
  { command: "/signal-second-opinion", label: "Second opinion", status: "advanced", detail: "Runs second-opinion workflow." },
  { command: "/signal-second-opinion-record", label: "Record opinion", status: "advanced", detail: "Records a second-opinion result." },
  { command: "/signal-investigate", label: "Investigate", status: "advanced", detail: "Runs investigation workflow." },
  { command: "/signal-build", label: "Build", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-debrief", label: "Debrief", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-design", label: "Design", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-design-html", label: "Design HTML", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-design-review", label: "Design review", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-discovery", label: "Discovery", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-observe", label: "Observe", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-onboard", label: "Onboard", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-pause", label: "Pause", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-pre-design", label: "Pre-design", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-pre-wave", label: "Pre-wave", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-review", label: "Review", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-ship", label: "Ship", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
  { command: "/signal-wave-review", label: "Wave review", status: "preview", detail: "Command brief is available; guided execution is not wired yet." },
];

const PROJECT_TEMPLATES = [
  {
    id: "founder-mvp",
    name: "Founder MVP",
    detail: "Use when the project needs sharp scope, launch evidence, and quick risk control.",
    note: "Template: Founder MVP\nFocus: narrow offer, first-user journey, launch blockers, proof checklist, and risk log.",
  },
  {
    id: "engineering-delivery",
    name: "Engineering delivery",
    detail: "Use when implementation, tests, release readiness, and handoff quality matter most.",
    note: "Template: Engineering delivery\nFocus: implementation plan, acceptance tests, release gates, rollback path, and engineering notes.",
  },
  {
    id: "qa-hardening",
    name: "QA hardening",
    detail: "Use when the product exists but needs a serious bug, UX, and release-readiness pass.",
    note: "Template: QA hardening\nFocus: critical journeys, failing states, install validation, regression risk, and test evidence.",
  },
  {
    id: "release-candidate",
    name: "Release candidate",
    detail: "Use when the work is close to ship and every remaining gate must be explicit.",
    note: "Template: Release candidate\nFocus: signed build, update proof, installer lifecycle, docs, support report, and final acceptance.",
  },
];

const WORKFLOW_RECIPES = [
  ["New product", "Choose project, connect AI, run /signal-init, add first decision note, run /signal-status, then sign the first ready gate."],
  ["Bug fix", "Add a QA note, ask AI for risk, run the relevant command, record evidence, then export a handoff report."],
  ["Release check", "Run status, inspect Dashboard files, export issue report, validate installer checklist, then keep signing gates external."],
  ["Team handoff", "Refresh status, add a session note, export handoff, and send the generated .signalos export file."],
];

const BUILD_STACKS = {
  "react-vite": {
    label: "React / Vite app",
    entry: "package.json",
    run: "Run: npm install, then npm run dev",
    required: ["package.json", "index.html", "README.md"],
    prompt: [
      "Create a Vite React app.",
      "Required files include package.json, index.html, src/main.jsx, src/App.jsx, src/styles.css, and README.md.",
      "Use React state and browser storage where useful.",
    ],
  },
  next: {
    label: "Next.js app",
    entry: "package.json",
    run: "Run: npm install, then npm run dev",
    required: ["package.json", "README.md"],
    prompt: [
      "Create a Next.js app using the App Router.",
      "Required files include package.json, app/page.jsx, app/layout.jsx, app/globals.css, and README.md.",
      "Keep it local-first and avoid external services unless the user explicitly asks.",
    ],
  },
  "node-express": {
    label: "Node / Express app",
    entry: "package.json",
    run: "Run: npm install, then npm start",
    required: ["package.json", "README.md"],
    prompt: [
      "Create a Node Express app with a simple web UI.",
      "Required files include package.json, server.js, public/index.html, public/styles.css, public/app.js, and README.md.",
      "Use an in-memory or local JSON file flow unless the user asks for a database.",
    ],
  },
  "python-flask": {
    label: "Python / Flask app",
    entry: "README.md",
    run: "Run: python -m venv .venv, install requirements.txt, then python app.py",
    required: ["app.py", "requirements.txt", "README.md"],
    prompt: [
      "Create a Python Flask app with a simple web UI.",
      "Required files include app.py, requirements.txt, templates/index.html, static/styles.css, static/app.js, and README.md.",
      "Keep the first version local and easy to run.",
    ],
  },
  static: {
    label: "Plain HTML app",
    entry: "index.html",
    run: "Open index.html directly.",
    required: ["index.html", "README.md"],
    prompt: [
      "Create a plain HTML, CSS, and JavaScript app.",
      "Required files include index.html, styles.css, app.js, and README.md.",
      "The app must run by opening index.html directly.",
    ],
  },
  auto: {
    label: "SignalOS chooses",
    entry: "README.md",
    run: "Follow README.md for the generated run command.",
    required: ["README.md"],
    prompt: [
      "Choose the smallest appropriate app stack for the user's request.",
      "Prefer React / Vite for browser apps, Node / Express for local API apps, and Python / Flask for Python-focused requests.",
      "Include README.md with exact run commands.",
    ],
  },
};

const BUILDER_PHASES = [
  ["prepare", "Prepare", "SignalOS setup/status"],
  ["plan", "Plan", "Scope and tasks"],
  ["write", "Build", "Project files"],
  ["review", "Review", "Status and next step"],
];

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
  artifacts: null,
  attachments: [],
  git: null,
  statusChecked: false,
  busy: false,
  runningCommand: null,
  commandStartedAt: 0,
  commandTimer: null,
  view: "guide",
  guideTab: null,
  modelOptions: [],
  modelOptionsProvider: "",
  modelOptionsLoading: false,
  modelOptionsError: "",
  modelDraftProvider: "",
  modelDraftSelection: "",
  modelDraftCustom: "",
  builder: {
    status: "idle",
    message: "",
    files: [],
    summary: "",
    stack: "react-vite",
    phase: "",
    done: [],
    runInstructions: "",
    briefPath: "",
    entryPath: "",
  },
  aiConnection: { provider: "", status: "untested", message: "" },
  engine: { status: "unknown", message: "Not checked yet.", version: "", checkedAt: "" },
  sidecarError: "",
  lastSetup: null,
  gateSigner: localStorage.getItem(LS_GATE_SIGNER) || "",
  updateChannel: localStorage.getItem(LS_UPDATE_CHANNEL) || "beta",
  onboarding: {},
  transcriptWorkspace: "",
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
  buildPrompt: $("#buildPrompt"),
  buildStack: $("#buildStack"),
  buildApp: $("#buildApp"),
  buildStatus: $("#buildStatus"),
  buildPhaseList: $("#buildPhaseList"),
  buildRunbook: $("#buildRunbook"),
  buildFileList: $("#buildFileList"),
  openBuiltApp: $("#openBuiltApp"),
  openChatFromBuild: $("#openChatFromBuild"),
  projectPath: $("#projectPath"),
  projectPathDetail: $("#projectPathDetail"),
  keyStatus: $("#keyStatus"),
  keyStatusDetail: $("#keyStatusDetail"),
  statusText: $("#statusText"),
  nextActionText: $("#nextActionText"),
  providerSelect: $("#providerSelect"),
  providerModelSelect: $("#providerModelSelect"),
  providerModel: $("#providerModel"),
  fetchModels: $("#fetchModels"),
  providerKey: $("#providerKey"),
  keyField: $("#keyField"),
  modelHelp: $("#modelHelp"),
  providerHelp: $("#providerHelp"),
  gateList: $("#gateList"),
  activityLog: $("#activityLog"),
  attachmentDrop: $("#attachmentDrop"),
  attachmentInput: $("#attachmentInput"),
  attachmentPick: $("#attachmentPick"),
  attachmentButton: $("#attachmentButton"),
  attachmentList: $("#attachmentList"),
  commandForm: $("#commandForm"),
  commandInput: $("#commandInput"),
  cancelCommand: $("#cancelCommand"),
  sidecarWarning: $("#sidecarWarning"),
  setupResultPanel: $("#setupResultPanel"),
  setupResultMeta: $("#setupResultMeta"),
  setupArtifactList: $("#setupArtifactList"),
  runStatusFromResult: $("#runStatusFromResult"),
  copyDiagnostics: $("#copyDiagnostics"),
  commandCatalog: $("#commandCatalog"),
  dashboardProject: $("#dashboardProject"),
  dashboardProjectNote: $("#dashboardProjectNote"),
  dashboardAi: $("#dashboardAi"),
  dashboardAiNote: $("#dashboardAiNote"),
  dashboardEngine: $("#dashboardEngine"),
  dashboardEngineNote: $("#dashboardEngineNote"),
  dashboardNext: $("#dashboardNext"),
  dashboardNextNote: $("#dashboardNextNote"),
  dashboardGateList: $("#dashboardGateList"),
  dashboardArtifactList: $("#dashboardArtifactList"),
  dashboardRunStatus: $("#dashboardRunStatus"),
  dashboardOpenChat: $("#dashboardOpenChat"),
  dashboardExportHandoff: $("#dashboardExportHandoff"),
  onboardingChecklist: $("#onboardingChecklist"),
  brainSearch: $("#brainSearch"),
  brainList: $("#brainList"),
  brainForm: $("#brainForm"),
  brainType: $("#brainType"),
  brainText: $("#brainText"),
  historyList: $("#historyList"),
  statusSummary: $("#statusSummary"),
  historyArtifactList: $("#historyArtifactList"),
  exportHandoff: $("#exportHandoff"),
  exportIssueReport: $("#exportIssueReport"),
  settingsWorkspace: $("#settingsWorkspace"),
  updateChannelSelect: $("#updateChannelSelect"),
  updateChannelSummary: $("#updateChannelSummary"),
  settingsCheckUpdates: $("#settingsCheckUpdates"),
  settingsProvider: $("#settingsProvider"),
  settingsModel: $("#settingsModel"),
  settingsCost: $("#settingsCost"),
  budgetInput: $("#budgetInput"),
  saveBudget: $("#saveBudget"),
  resetSessionCost: $("#resetSessionCost"),
  settingsSecrets: $("#settingsSecrets"),
  settingsProviderSelect: $("#settingsProviderSelect"),
  settingsProviderModelSelect: $("#settingsProviderModelSelect"),
  settingsProviderModel: $("#settingsProviderModel"),
  settingsFetchModels: $("#settingsFetchModels"),
  settingsProviderKey: $("#settingsProviderKey"),
  settingsKeyField: $("#settingsKeyField"),
  settingsModelHelp: $("#settingsModelHelp"),
  settingsProviderHelp: $("#settingsProviderHelp"),
  settingsSaveProvider: $("#settingsSaveProvider"),
  settingsDeleteKey: $("#settingsDeleteKey"),
  settingsKeyStorage: $("#settingsKeyStorage"),
  settingsRefreshSecrets: $("#settingsRefreshSecrets"),
  settingsSecretList: $("#settingsSecretList"),
  secretName: $("#secretName"),
  secretValue: $("#secretValue"),
  secretFile: $("#secretFile"),
  saveSecret: $("#saveSecret"),
  clearSecretForm: $("#clearSecretForm"),
  engineStatus: $("#engineStatus"),
  engineDetails: $("#engineDetails"),
  testEngine: $("#testEngine"),
  restartEngine: $("#restartEngine"),
  copySettingsDiagnostics: $("#copySettingsDiagnostics"),
  settingsExportIssueReport: $("#settingsExportIssueReport"),
  gateSigner: $("#gateSigner"),
  templateGrid: $("#templateGrid"),
  recipeList: $("#recipeList"),
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

async function copyText(value, message = "Copied.") {
  const text = safeText(value);
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    toast(message);
  } catch (error) {
    window.prompt("Copy this value", text);
  }
}

function transcriptKey(workspace = state.workspace) {
  return workspace ? `${LS_TRANSCRIPT_PREFIX}${encodeURIComponent(workspace)}` : "";
}

function onboardingKey(workspace = state.workspace) {
  return workspace ? `${LS_ONBOARDING_PREFIX}${encodeURIComponent(workspace)}` : "";
}

function loadTranscript() {
  const key = transcriptKey();
  if (!key) {
    state.log = [];
    return;
  }
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "[]");
    state.log = Array.isArray(parsed) ? parsed.slice(-80) : [];
  } catch (error) {
    state.log = [];
  }
}

function persistTranscript() {
  const key = transcriptKey();
  if (!key) return;
  localStorage.setItem(key, JSON.stringify(state.log.slice(-80)));
}

function loadOnboarding() {
  const key = onboardingKey();
  if (!key) {
    state.onboarding = {};
    return;
  }
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "{}");
    state.onboarding = parsed && typeof parsed === "object" ? parsed : {};
  } catch (error) {
    state.onboarding = {};
  }
}

function persistOnboarding() {
  const key = onboardingKey();
  if (!key) return;
  localStorage.setItem(key, JSON.stringify(state.onboarding));
}

function markOnboarding(step) {
  if (!state.workspace || !step) return;
  state.onboarding = { ...state.onboarding, [step]: new Date().toISOString() };
  persistOnboarding();
}

function commandInfo(command) {
  const normalized = safeText(command).trim();
  const match = COMMAND_CATALOG.find((item) => item.command === normalized);
  return match || {
    command: normalized,
    label: normalized || "Command",
    status: "preview",
    detail: "May run as an advanced command or return a command brief.",
  };
}

function statusLabel(status) {
  if (status === "ready") return "Ready";
  if (status === "preview") return "Preview";
  if (status === "advanced") return "Advanced";
  if (status === "error") return "Error";
  return "Info";
}

function formatTime(ts = Date.now()) {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch (error) {
    return "";
  }
}

function providerNeedsKey() {
  return Boolean(state.activeProviderInfo?.needs_key);
}

function aiReady() {
  if (!state.activeProviderInfo) return false;
  const hasCredentials = !providerNeedsKey() || state.hasKey;
  return hasCredentials
    && state.aiConnection.provider === state.activeProvider
    && state.aiConnection.status === "ok";
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

const guideSteps = ["project", "ai", "status", "start"];

function stepDoneMap() {
  return {
    project: Boolean(state.workspace),
    ai: aiReady(),
    status: state.statusChecked || hasActiveWave(),
    start: hasActiveWave(),
  };
}

function selectedGuideStep() {
  if (!guideSteps.includes(state.guideTab)) {
    state.guideTab = currentStep();
  }
  return state.guideTab;
}

function modelLabel(model) {
  if (!model?.id) return "";
  return model.name && model.name !== model.id ? `${model.name} (${model.id})` : model.id;
}

function providerControlSets() {
  return [
    {
      providerSelect: el.providerSelect,
      providerModelSelect: el.providerModelSelect,
      providerModel: el.providerModel,
      fetchModels: el.fetchModels,
      providerKey: el.providerKey,
      keyField: el.keyField,
      modelHelp: el.modelHelp,
      providerHelp: el.providerHelp,
    },
    {
      providerSelect: el.settingsProviderSelect,
      providerModelSelect: el.settingsProviderModelSelect,
      providerModel: el.settingsProviderModel,
      fetchModels: el.settingsFetchModels,
      providerKey: el.settingsProviderKey,
      keyField: el.settingsKeyField,
      modelHelp: el.settingsModelHelp,
      providerHelp: el.settingsProviderHelp,
    },
  ].filter((controls) => controls.providerSelect);
}

function selectedProviderModel() {
  if (state.modelDraftProvider === state.activeProvider) {
    return state.modelDraftSelection === "__custom"
      ? state.modelDraftCustom.trim()
      : state.modelDraftSelection.trim();
  }
  return (state.activeProviderInfo?.model || "").trim();
}

function selectedBuildStack() {
  const value = el.buildStack?.value || state.builder.stack || "react-vite";
  return BUILD_STACKS[value] ? value : "react-vite";
}

function buildStackInfo(stackId = selectedBuildStack()) {
  return BUILD_STACKS[stackId] || BUILD_STACKS["react-vite"];
}

function setBuilderState(patch = {}) {
  state.builder = {
    status: "idle",
    message: "",
    files: [],
    summary: "",
    stack: selectedBuildStack(),
    phase: "",
    done: [],
    runInstructions: "",
    briefPath: "",
    entryPath: "",
    ...state.builder,
    ...patch,
  };
  renderBuilder();
}

function setBuilderPhase(phase, message) {
  const done = new Set(state.builder.done || []);
  for (const [id] of BUILDER_PHASES) {
    if (id === phase) break;
    done.add(id);
  }
  setBuilderState({
    phase,
    done: Array.from(done),
    message: message || state.builder.message,
  });
}

function providerKeyValue() {
  const controls = providerControlSets();
  const ordered = state.view === "settings" ? [...controls].reverse() : controls;
  for (const control of ordered) {
    const value = control.providerKey?.value?.trim();
    if (value) return value;
  }
  return "";
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
    const hasCredentials = !needsKey || state.hasKey || providerKeyValue();
    const shouldSave = Boolean(providerKeyValue()) || state.modelDraftProvider === state.activeProvider;
    return {
      label: shouldSave ? "Save and test AI" : "Test AI connection",
      title: needsKey && !hasCredentials ? "Paste the AI key once, then test it." : "Test the AI connection.",
      detail: "SignalOS will only mark AI ready after the selected provider responds.",
      run: shouldSave ? saveProvider : validateProviderConnection,
      disabled: !hasCredentials,
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
      run: () => runSignalCommand("/signal-init", [], { markChecked: true }),
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
  renderBuilder();
  renderProviderForm();
  renderGates();
  renderActivity();
  renderAttachments();
  renderSetupResult();
  renderCommandCatalog();
  renderDashboard();
  renderOnboardingChecklist();
  renderBrain();
  renderHistory();
  renderSettings();
  renderEngine();
  renderHelp();
}

function renderShell() {
  const projectName = state.workspace ? basename(state.workspace) : "No project chosen";
  const waveName = state.wave?.name || "No active wave";
  el.workspaceLabel.textContent = projectName;
  el.waveLabel.textContent = waveName;
  el.projectPath.textContent = state.workspace || "No folder selected yet.";
  if (el.projectPathDetail) el.projectPathDetail.textContent = state.workspace || "No folder selected yet.";
  el.providerLabel.textContent = state.activeProviderInfo?.name || "AI not connected";
  el.costLabel.textContent = currency.format(Number(state.cost?.session_usd || 0));
  if (el.cancelCommand) {
    el.cancelCommand.disabled = !state.runningCommand;
  }
  if (el.gateSigner && document.activeElement !== el.gateSigner) {
    el.gateSigner.value = state.gateSigner;
  }

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
    guide: ["Build", "Describe the app and generate the first working files."],
    project: ["Project", "Folder, AI connection, setup status, and generated files."],
    chat: ["Chat", "AI chat, slash commands, and command output."],
    dashboard: ["Dashboard", "Current project state, gates, files, and next action."],
    secrets: ["Secrets", "Project .env values stored locally and hidden from AI."],
    brain: ["Notes", "Saved beliefs, decisions, notes, and QA evidence."],
    history: ["History", "Audit trail and current project status."],
    settings: ["Settings", "Workspace, AI connection, engine, and updates."],
    help: ["Guide", "First-run flow and recovery reference."],
  };
  const [title, subtitle] = titles[state.view] || titles.guide;
  el.viewTitle.textContent = title;
  el.viewSubtitle.textContent = subtitle;

  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${state.view}`));
  $$("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
}

function renderSteps() {
  const step = currentStep();
  const done = stepDoneMap();
  const selected = selectedGuideStep();

  $$("[data-step], [data-step-row]").forEach((node) => {
    const id = node.dataset.step || node.dataset.stepRow;
    node.classList.toggle("active", id === step);
    node.classList.toggle("done", Boolean(done[id]));
    node.classList.toggle("selected", id === selected);
    if (node.dataset.stepTab) {
      node.setAttribute("aria-selected", id === selected ? "true" : "false");
    }
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

  const setupStep = currentStep();
  el.activeStepLabel.textContent = setupStep === "project" || setupStep === "ai"
    ? (stepNames[setupStep] || "Next step")
    : "Build";
  el.guideLead.textContent = setupStep === "project" || setupStep === "ai"
    ? action.title
    : "Describe the app. SignalOS will create the first working version.";
  el.guideDetail.textContent = setupStep === "project" || setupStep === "ai"
    ? action.detail
    : "The builder prepares SignalOS, records a scoped plan, writes the selected stack, and refreshes project status.";
  el.mainAction.textContent = state.busy ? "Working..." : action.label;
  el.mainAction.disabled = state.busy || Boolean(action.disabled);
  el.mainAction.onclick = () => action.run();

  el.secondaryAction.textContent = action.secondary?.label || "Refresh";
  el.secondaryAction.disabled = state.busy;
  el.secondaryAction.onclick = () => (action.secondary?.run || refreshAll)();

  const providerName = state.activeProviderInfo?.name || "No provider selected";
  const connectionForProvider = state.aiConnection.provider === state.activeProvider
    ? state.aiConnection
    : { status: "untested", message: "" };
  const keyStatus = aiReady()
    ? `${providerName} connected.`
    : connectionForProvider.status === "error"
      ? `Could not connect: ${connectionForProvider.message}`
      : providerNeedsKey() && !state.hasKey && !providerKeyValue()
        ? `${providerName} needs an API key.`
        : `${providerName} is saved but not tested.`;
  el.keyStatus.textContent = keyStatus;
  if (el.keyStatusDetail) el.keyStatusDetail.textContent = keyStatus;

  el.statusText.textContent = state.statusChecked
    ? (state.wave?.phase_name ? `${state.wave.phase_name}. ${state.wave?.progress_pct || 0}% complete.` : "Status loaded.")
    : "Not checked yet.";

  const gate = currentGate();
  el.nextActionText.textContent = hasActiveWave()
    ? (gate ? `Work toward ${gate.name}.` : "Review the latest status.")
    : "Set up the project after the check.";

  const selected = selectedGuideStep();
  $$("[data-step-tab], .phase-pane").forEach((node) => {
    const id = node.dataset.stepTab || node.dataset.stepRow;
    node.classList.toggle("selected", id === selected);
    if (node.dataset.stepTab) {
      node.setAttribute("aria-selected", id === selected ? "true" : "false");
    }
  });
}

function renderBuilder() {
  if (!el.buildStatus || !el.buildFileList) return;
  const files = Array.isArray(state.builder.files) ? state.builder.files : [];
  const stackId = state.builder.stack || selectedBuildStack();
  const stack = buildStackInfo(stackId);
  if (el.buildStack && el.buildStack.value !== stackId && BUILD_STACKS[stackId]) {
    el.buildStack.value = stackId;
  }
  if (el.buildStack) {
    el.buildStack.disabled = state.busy;
  }
  const statusCopy = {
    idle: "No build yet. Choose a folder, connect AI, pick a stack, then describe the app.",
    running: state.builder.message || "Building through SignalOS: setup, plan, files, then review.",
    success: state.builder.message || "Build finished. Open the app or refine the prompt.",
    error: state.builder.message || "Build failed. Fix the message and try again.",
  };
  el.buildStatus.textContent = statusCopy[state.builder.status] || statusCopy.idle;
  if (el.buildPhaseList) {
    const done = new Set(state.builder.done || []);
    el.buildPhaseList.innerHTML = BUILDER_PHASES.map(([id, title, detail]) => {
      const active = state.builder.phase === id;
      const complete = done.has(id) || (state.builder.status === "success" && id === "review");
      return `
        <div class="builder-phase ${active ? "active" : ""} ${complete ? "done" : ""}">
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(detail)}</span>
        </div>
      `;
    }).join("");
  }
  if (el.buildRunbook) {
    const run = state.builder.runInstructions || stack.run;
    const brief = state.builder.briefPath ? `\nEvidence: ${state.builder.briefPath}` : "";
    el.buildRunbook.textContent = state.builder.status === "idle"
      ? "Run instructions and SignalOS evidence will appear here after Build app."
      : `${stack.label}\n${run}${brief}`;
  }
  if (el.buildApp) {
    el.buildApp.disabled = state.busy;
    el.buildApp.textContent = state.builder.status === "running" ? "Building..." : "Build app";
  }
  el.buildFileList.innerHTML = files.length
    ? files.map((file) => `
      <div class="artifact-row">
        <div>
          <div class="item-title">${escapeHtml(file.relative_path || file.path || "Generated file")}</div>
          <div class="item-meta">${escapeHtml(file.bytes ? `${file.bytes} bytes` : "Ready")}</div>
        </div>
        <button class="ghost small" type="button" data-open-built-file="${escapeHtml(file.relative_path || file.path || "")}">Open</button>
      </div>
    `).join("")
    : `<div class="empty compact-empty">Generated files will appear here.</div>`;
  el.buildFileList.querySelectorAll("[data-open-built-file]").forEach((button) => {
    button.addEventListener("click", () => openProjectArtifact(button.dataset.openBuiltFile));
  });
  if (el.openBuiltApp) {
    const entry = state.builder.entryPath || stack.entry;
    const hasEntry = files.some((file) => (file.relative_path || file.path || "").toLowerCase() === entry.toLowerCase());
    el.openBuiltApp.textContent = entry === "index.html" ? "Open app" : "Open entry";
    el.openBuiltApp.disabled = !state.workspace || !hasEntry;
  }
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
  const providerOptions = `${primaryOptions}${moreOptions}`;
  providerControlSets().forEach((controls) => renderProviderControls(controls, providerOptions));
}

function renderProviderControls(controls, providerOptions) {
  controls.providerSelect.innerHTML = providerOptions;
  controls.providerSelect.value = state.activeProvider;

  const configuredModel = state.activeProviderInfo?.model || "";
  const fetchedForActive = state.modelOptionsProvider === state.activeProvider;
  const fetchedModels = fetchedForActive ? state.modelOptions : [];
  const hasConfiguredOption = fetchedModels.some((model) => model.id === configuredModel);
  const hasDraft = state.modelDraftProvider === state.activeProvider;
  const draftSelection = hasDraft
    ? state.modelDraftSelection
    : (configuredModel && !hasConfiguredOption ? "__custom" : configuredModel);
  const selectOptions = [
    `<option value="" ${draftSelection === "" ? "selected" : ""}>Use provider default</option>`,
    ...fetchedModels.map((model) => {
      const selected = model.id === draftSelection ? "selected" : "";
      return `<option value="${escapeHtml(model.id)}" ${selected}>${escapeHtml(modelLabel(model))}</option>`;
    }),
    `<option value="__custom" ${draftSelection === "__custom" ? "selected" : ""}>Other model...</option>`,
  ];
  controls.providerModelSelect.innerHTML = selectOptions.join("");

  const customModel = controls.providerModelSelect.value === "__custom";
  if (document.activeElement !== controls.providerModel) {
    controls.providerModel.value = customModel ? (hasDraft ? state.modelDraftCustom : configuredModel) : "";
  }
  controls.providerModel.classList.toggle("hidden", !customModel);
  controls.fetchModels.textContent = state.modelOptionsLoading ? "Fetching..." : "Fetch models";
  controls.fetchModels.disabled = state.modelOptionsLoading || state.busy;

  if (state.modelOptionsLoading) {
    controls.modelHelp.textContent = "Fetching available models from the provider.";
  } else if (state.modelOptionsError && fetchedForActive) {
    controls.modelHelp.textContent = state.modelOptionsError;
  } else if (fetchedModels.length) {
    controls.modelHelp.textContent = "Select a fetched model, or choose Other model to type one.";
  } else if (providerNeedsKey() && !state.hasKey && !providerKeyValue()) {
    controls.modelHelp.textContent = "Paste or save an API key, then fetch available models.";
  } else {
    controls.modelHelp.textContent = "Fetch models from the selected AI service, or choose Other model.";
  }

  controls.keyField.style.display = providerNeedsKey() ? "grid" : "none";
  const connectionForProvider = state.aiConnection.provider === state.activeProvider
    ? state.aiConnection
    : { status: "untested", message: "" };
  controls.providerHelp.textContent = connectionForProvider.status === "ok"
    ? `Connected. ${connectionForProvider.message}`
    : connectionForProvider.status === "error"
      ? `Not connected. ${connectionForProvider.message}`
      : providerNeedsKey()
        ? (state.hasKey ? "A key is saved. Test the connection before using chat." : "Keys are saved securely on this computer.")
        : "Test the local AI connection before using chat.";
}

function renderGates() {
  renderGateList(el.gateList, { actions: true });
}

function renderGateList(target, options = {}) {
  if (!target) return;
  if (!state.workspace) {
    target.innerHTML = `<div class="empty">Choose a project to see steps.</div>`;
    return;
  }

  if (!state.gates.length) {
    target.innerHTML = `<div class="empty">No project step status loaded yet.</div>`;
    return;
  }

  target.innerHTML = state.gates.map((gate) => {
    const gateId = gate.id ?? "";
    const status = gate.status || "open";
    const statusKey = safeText(status).toLowerCase();
    const canSign = options.actions && !["signed", "locked"].includes(statusKey);
    return `
    <div class="gate ${escapeHtml(gate.status || "")}">
      <div class="gate-id">G${escapeHtml(gate.id)}</div>
      <div>
        <div class="item-title">${escapeHtml(gate.name || `Gate ${gate.id}`)}</div>
        <div class="item-meta">${escapeHtml(gate.desc || "")}</div>
      </div>
      <div class="gate-actions">
        <div class="gate-status">${escapeHtml(status)}</div>
        ${canSign ? `<button class="secondary small" type="button" data-sign-gate="${escapeHtml(gateId)}">Sign</button>` : ""}
      </div>
    </div>
  `;
  }).join("");

  target.querySelectorAll("[data-sign-gate]").forEach((button) => {
    button.addEventListener("click", () => signGate(button.dataset.signGate));
  });
}

function renderActivity() {
  if (!state.log.length) {
    el.activityLog.innerHTML = `
      <div class="empty">
        <div>
          <strong>No messages yet.</strong>
          <div>Ask AI here after the connection test passes, or run a slash command.</div>
        </div>
      </div>
    `;
    return;
  }

  el.activityLog.innerHTML = state.log.map((entry) => {
    const status = entry.status || "";
    const kind = entry.kind || "";
    const meta = [entry.command, formatTime(entry.ts)].filter(Boolean).join(" . ");
    const cards = renderResultCards(entry.cards);
    return `
    <div class="log-entry ${escapeHtml([status, kind].filter(Boolean).join(" "))}">
      <strong>${escapeHtml(entry.title)}</strong>
      ${meta ? `<div class="log-meta">${escapeHtml(meta)}</div>` : ""}
      <pre>${escapeHtml(entry.body)}</pre>
      ${cards}
    </div>
  `;
  }).join("");
  el.activityLog.scrollTop = el.activityLog.scrollHeight;
}

function renderResultCards(cards) {
  if (!Array.isArray(cards) || !cards.length) return "";
  return `<div class="result-grid">${cards.map((card) => `
    <div class="result-card">
      <strong>${escapeHtml(card.label || "Result")}</strong>
      <span>${escapeHtml(card.value || "")}</span>
    </div>
  `).join("")}</div>`;
}

function renderAttachments() {
  if (!state.attachments.length) {
    el.attachmentList.innerHTML = "";
    return;
  }

  el.attachmentList.innerHTML = state.attachments.map((item, index) => {
    const blocked = item.status === "blocked";
    const badge = blocked ? "Blocked" : item.redacted ? "Redacted" : "Ready";
    return `
      <div class="attachment-item ${blocked ? "blocked" : ""}">
        <div>
          <div class="item-title">${escapeHtml(item.name || "Attachment")}</div>
          <div class="attachment-summary">${escapeHtml(item.summary || "")}</div>
        </div>
        <button class="ghost small" type="button" data-remove-attachment="${index}">${escapeHtml(badge)} x</button>
      </div>
    `;
  }).join("");

  $$("[data-remove-attachment]").forEach((button) => {
    button.addEventListener("click", () => {
      state.attachments.splice(Number(button.dataset.removeAttachment), 1);
      renderAttachments();
    });
  });
}

function renderSetupResult() {
  if (!el.setupResultMeta || !el.setupArtifactList) return;
  const initialized = Boolean(state.artifacts?.initialized);
  const last = state.lastSetup;
  if (!state.workspace) {
    el.setupResultMeta.textContent = "Choose a project folder to see setup results.";
  } else if (last?.status === "running") {
    el.setupResultMeta.textContent = "Setup is running. SignalOS is creating or checking local project files.";
  } else if (last?.status === "ok") {
    el.setupResultMeta.textContent = initialized
      ? "Setup finished and the expected project files are visible."
      : "Setup finished, but expected project files are still missing.";
  } else if (last?.status === "error") {
    el.setupResultMeta.textContent = "Setup did not finish. Check the command result and engine status.";
  } else if (initialized) {
    el.setupResultMeta.textContent = "This folder already has the expected SignalOS project files.";
  } else {
    el.setupResultMeta.textContent = "Run setup to create local SignalOS files in this project folder.";
  }
  renderArtifactList(el.setupArtifactList, state.artifacts);
}

function renderArtifactList(target, artifactState) {
  if (!target) return;
  if (!state.workspace) {
    target.innerHTML = `<div class="empty compact-empty">No project selected.</div>`;
    return;
  }
  const artifacts = Array.isArray(artifactState?.artifacts) ? artifactState.artifacts : [];
  if (!artifacts.length) {
    target.innerHTML = `<div class="empty compact-empty">No artifact check has run yet.</div>`;
    return;
  }
  target.innerHTML = artifacts.map((artifact) => {
    const ok = Boolean(artifact.exists);
    return `
      <div class="artifact-row">
        <div>
          <div class="item-title">${escapeHtml(artifact.name || artifact.path)}</div>
          <div class="item-meta">${escapeHtml(artifact.path || "")}</div>
          <div class="item-meta">${escapeHtml(artifact.detail || "")}</div>
        </div>
        <div class="gate-actions">
          <span class="artifact-state ${ok ? "ok" : "missing"}">${ok ? "Found" : "Missing"}</span>
          ${ok ? `<button class="ghost small" type="button" data-open-artifact="${escapeHtml(artifact.path || "")}">Open</button>` : ""}
        </div>
      </div>
    `;
  }).join("");

  target.querySelectorAll("[data-open-artifact]").forEach((button) => {
    button.addEventListener("click", () => openProjectArtifact(button.dataset.openArtifact));
  });
}

function renderCommandCatalog() {
  renderCommandChips();
  if (!el.commandCatalog) return;
  el.commandCatalog.innerHTML = COMMAND_CATALOG.map((item) => `
    <div class="command-row">
      <div>
        <div class="item-title">${escapeHtml(item.label)}</div>
        <div class="item-meta">${escapeHtml(item.command)} . ${escapeHtml(item.detail)}</div>
      </div>
      <span class="command-badge ${escapeHtml(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
    </div>
  `).join("");
}

function renderCommandChips() {
  $$(".chip").forEach((button) => {
    const info = commandInfo(button.dataset.command);
    button.className = `chip ${info.status}`;
    button.innerHTML = `${escapeHtml(info.label)} <span class="chip-status ${escapeHtml(info.status)}">${escapeHtml(statusLabel(info.status))}</span>`;
    button.title = `${info.command}: ${info.detail}`;
  });
}

async function openProjectArtifact(relativePath) {
  if (!relativePath) return;
  try {
    await ipc.project.openPath(relativePath);
  } catch (error) {
    toast(error.message || "Could not open project file.");
  }
}

function renderDashboard() {
  if (!el.dashboardProject) return;
  const initialized = Boolean(state.artifacts?.initialized);
  const engineOk = state.engine.status === "ok" && !state.sidecarError;
  const gate = currentGate();
  el.dashboardProject.textContent = state.workspace ? basename(state.workspace) : "No project";
  el.dashboardProjectNote.textContent = state.workspace
    ? (initialized ? "SignalOS project files are present." : "Setup files are missing.")
    : "Choose a folder to begin.";
  el.dashboardAi.textContent = aiReady() ? "Connected" : "Not ready";
  el.dashboardAiNote.textContent = state.activeProviderInfo
    ? `${state.activeProviderInfo.name}${state.activeProviderInfo.model ? ` . ${state.activeProviderInfo.model}` : ""}`
    : "Choose an AI provider.";
  el.dashboardEngine.textContent = engineOk ? "Ready" : state.engine.status === "error" ? "Needs fix" : "Unknown";
  el.dashboardEngineNote.textContent = state.sidecarError || state.engine.message || "Test the engine in Settings.";
  el.dashboardNext.textContent = gate ? `G${gate.id} ${gate.name || ""}`.trim() : currentStepLabel();
  el.dashboardNextNote.textContent = hasActiveWave()
    ? (gate?.desc || "Run status to refresh the next action.")
    : initialized
      ? "Run status to load the active wave."
      : "Set up the project from Chat.";
  renderGateList(el.dashboardGateList, { actions: false });
  renderArtifactList(el.dashboardArtifactList, state.artifacts);
}

function onboardingItems() {
  const initialized = Boolean(state.artifacts?.initialized);
  return [
    ["project", "Project selected", Boolean(state.workspace)],
    ["ai", "AI connected", aiReady()],
    ["setup", "Project setup visible", initialized || Boolean(state.onboarding.setup)],
    ["status", "Status checked", state.statusChecked || hasActiveWave() || Boolean(state.onboarding.status)],
    ["note", "First note saved", Boolean(state.onboarding.note)],
    ["gate", "First gate action recorded", Boolean(state.onboarding.gate)],
  ];
}

function renderOnboardingChecklist() {
  if (!el.onboardingChecklist) return;
  el.onboardingChecklist.innerHTML = onboardingItems().map(([key, label, done]) => `
    <div class="check-row ${done ? "done" : ""}">
      <div class="check-state">${done ? "OK" : ""}</div>
      <div>
        <div class="item-title">${escapeHtml(label)}</div>
        <div class="item-meta">${escapeHtml(onboardingHint(key, done))}</div>
      </div>
    </div>
  `).join("");
}

function onboardingHint(key, done) {
  if (done) return "Completed for this project.";
  const hints = {
    project: "Choose the folder the user will actually work in.",
    ai: "Fetch models, select one, then save and test the provider.",
    setup: "Run /signal-init and confirm project files are visible.",
    status: "Run /signal-status after setup.",
    note: "Save one useful decision, constraint, or QA note.",
    gate: "Use gate signing once a gate is ready.",
  };
  return hints[key] || "Not completed yet.";
}

function currentStepLabel() {
  const labels = {
    project: "Choose project",
    ai: "Connect AI",
    status: "Check status",
    start: "Start work",
  };
  return labels[currentStep()] || "Next step";
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
    el.historyList.innerHTML = `<div class="timeline">${state.audit.map((entry) => {
      const title = entry.event || entry.action || entry.type || "Audit entry";
      const body = entry.message || entry.summary || entry.path || JSON.stringify(entry, null, 2);
      const meta = entry.ts || entry.time || entry.created_at || "";
      return `
        <div class="timeline-entry">
          <div class="item-title">${escapeHtml(title)}</div>
          <div>${escapeHtml(body)}</div>
          <div class="item-meta">${escapeHtml(meta)}</div>
        </div>
      `;
    }).join("")}</div>`;
  }

  const branch = state.git?.branch ? `Branch ${state.git.branch}` : "No Git branch loaded";
  const clean = state.git ? (state.git.is_clean ? "clean" : "has changes") : "";
  el.statusSummary.textContent = [
    state.wave?.name || "No active wave",
    state.wave?.phase_name || "Onboarding",
    branch,
    clean,
  ].filter(Boolean).join(" . ");
  renderArtifactList(el.historyArtifactList, state.artifacts);
}

function renderSettings() {
  el.settingsWorkspace.textContent = state.workspace || "No project chosen";
  if (el.updateChannelSelect && document.activeElement !== el.updateChannelSelect) {
    el.updateChannelSelect.value = state.updateChannel;
  }
  if (el.updateChannelSummary) {
    el.updateChannelSummary.textContent = `Release channel is ${state.updateChannel}. Update checks use the selected manifest channel when available.`;
  }
  el.settingsProvider.textContent = state.activeProviderInfo?.name || "Not connected";
  el.settingsModel.textContent = state.activeProviderInfo?.model || "Not set";
  const sessionCost = Number(state.cost?.session_usd || 0);
  const monthlyCost = Number(state.cost?.monthly_usd || 0);
  const budget = Number(state.cost?.budget_usd || 0);
  el.settingsCost.textContent = `${currency.format(sessionCost)} this session . ${currency.format(monthlyCost)} monthly${budget ? ` of ${currency.format(budget)}` : ""}`;
  if (el.budgetInput && document.activeElement !== el.budgetInput) {
    el.budgetInput.value = budget ? String(budget) : "";
  }
  el.settingsSecrets.textContent = secretSummary();
  if (el.settingsKeyStorage) {
    const providerId = state.activeProvider || "provider";
    el.settingsKeyStorage.textContent = providerNeedsKey()
      ? `AI keys are stored in the operating system credential manager under service com.signalos.desktop and provider ${providerId}. Saved key values are never displayed.`
      : "This AI service does not store an API key.";
  }
  if (el.settingsDeleteKey) {
    el.settingsDeleteKey.disabled = state.busy || !providerNeedsKey() || !state.hasKey;
  }
  renderSecretLocations();
}

function renderHelp() {
  if (el.templateGrid) {
    el.templateGrid.innerHTML = PROJECT_TEMPLATES.map((template) => `
      <div class="template-card">
        <div>
          <strong>${escapeHtml(template.name)}</strong>
          <span>${escapeHtml(template.detail)}</span>
        </div>
        <button class="secondary small" type="button" data-apply-template="${escapeHtml(template.id)}">Use</button>
      </div>
    `).join("");
    el.templateGrid.querySelectorAll("[data-apply-template]").forEach((button) => {
      button.addEventListener("click", () => applyProjectTemplate(button.dataset.applyTemplate));
    });
  }

  if (el.recipeList) {
    el.recipeList.innerHTML = WORKFLOW_RECIPES.map(([title, detail]) => `
      <div class="item">
        <div class="item-title">${escapeHtml(title)}</div>
        <div class="item-meta">${escapeHtml(detail)}</div>
      </div>
    `).join("");
  }
}

function renderEngine() {
  if (!el.engineStatus || !el.engineDetails) return;
  const status = state.sidecarError ? "error" : state.engine.status;
  const label = status === "ok" ? "Ready" : status === "error" ? "Needs fix" : "Unknown";
  el.engineStatus.className = `engine-state ${status === "ok" ? "ok" : status === "error" ? "error" : "warn"}`;
  el.engineStatus.textContent = label;
  el.engineDetails.textContent = state.sidecarError
    ? `Engine failed: ${state.sidecarError}`
    : [
        state.engine.message || "Not checked yet.",
        state.engine.version ? `Version ${state.engine.version}` : "",
        state.engine.pid ? `PID ${state.engine.pid}` : "",
        state.engine.checkedAt ? `Checked ${formatTime(state.engine.checkedAt)}` : "",
      ].filter(Boolean).join(" . ");
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

function secretRelativePath(file) {
  return safeText(file?.path || file?.file || file?.name, "Secret file");
}

function secretAbsolutePath(file) {
  const rel = secretRelativePath(file);
  if (/^([A-Za-z]:[\\/]|\\\\|\/)/.test(rel) || !state.workspace) return rel;
  const sep = state.workspace.includes("\\") ? "\\" : "/";
  return `${state.workspace.replace(/[\\/]$/, "")}${sep}${rel.replace(/[\\/]/g, sep)}`;
}

function renderSecretLocations() {
  if (!el.settingsSecretList) return;
  if (!state.workspace) {
    el.settingsSecretList.innerHTML = `<div class="empty compact-empty">Choose a project to see secret file locations.</div>`;
    return;
  }

  const files = Array.isArray(state.secrets) ? state.secrets : [];
  if (!files.length) {
    el.settingsSecretList.innerHTML = `<div class="empty compact-empty">No .env or key files found in this project.</div>`;
    return;
  }

  el.settingsSecretList.innerHTML = files.map((file, index) => {
    const rel = secretRelativePath(file);
    const full = secretAbsolutePath(file);
    const variables = Array.isArray(file.variables) && file.variables.length
      ? `Variables: ${file.variables.slice(0, 12).map(escapeHtml).join(", ")}`
      : "Values are hidden.";
    return `
      <div class="item secret-location">
        <div>
          <div class="item-title">${escapeHtml(rel)}</div>
          <div class="item-meta">${escapeHtml(full)}</div>
          <div class="item-meta">${variables}</div>
        </div>
        <button class="ghost small" type="button" data-copy-secret-path="${index}">Copy path</button>
      </div>
    `;
  }).join("");

  $$("[data-copy-secret-path]").forEach((button) => {
    button.addEventListener("click", () => {
      const file = files[Number(button.dataset.copySecretPath)];
      copyText(secretAbsolutePath(file), "Path copied.");
    });
  });
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

  if (state.workspace !== state.transcriptWorkspace) {
    state.transcriptWorkspace = state.workspace || "";
    loadTranscript();
    loadOnboarding();
  }

  state.providers = await safeCall(() => ipc.provider.list(), []);
  state.activeProvider = await safeCall(() => ipc.provider.getActive(), state.activeProvider);
  state.activeProviderInfo = state.providers.find((provider) => provider.id === state.activeProvider) || state.providers[0] || null;
  if (state.activeProviderInfo && state.activeProviderInfo.id !== state.activeProvider) {
    state.activeProvider = state.activeProviderInfo.id;
  }
  if (state.aiConnection.provider && state.aiConnection.provider !== state.activeProvider) {
    state.aiConnection = { provider: state.activeProvider, status: "untested", message: "" };
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
    state.artifacts = null;
    state.git = null;
    render();
    return;
  }

  const [wave, gates, brain, audit, secrets, git, artifacts] = await Promise.all([
    safeCall(() => ipc.wave.get(), null),
    safeCall(() => ipc.gates.getAll(), []),
    safeCall(() => ipc.brain.search(el.brainSearch.value.trim()), []),
    safeCall(() => ipc.audit.list(50), []),
    safeCall(() => ipc.security.secrets(), []),
    safeCall(() => ipc.git.status(), null),
    safeCall(() => ipc.project.artifacts(), null),
  ]);

  state.wave = wave;
  state.gates = Array.isArray(gates) ? gates : [];
  state.brain = Array.isArray(brain) ? brain : [];
  state.audit = Array.isArray(audit) ? audit : [];
  state.secrets = Array.isArray(secrets) ? secrets : [];
  state.git = git;
  state.artifacts = artifacts;
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
    state.guideTab = null;
    state.lastSetup = null;
    state.artifacts = null;
    state.transcriptWorkspace = selected;
    loadTranscript();
    loadOnboarding();
    markOnboarding("project");
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
  state.secrets = [];
  state.git = null;
  state.statusChecked = false;
  state.guideTab = null;
  state.artifacts = null;
  state.transcriptWorkspace = "";
  state.log = [];
  state.onboarding = {};
  render();
  toast("Project forgotten in this app.");
}

async function saveProvider() {
  const provider = state.activeProvider;
  const info = state.providers.find((item) => item.id === provider);
  const model = selectedProviderModel();
  const key = providerKeyValue();

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
      providerControlSets().forEach((controls) => {
        controls.providerKey.value = "";
      });
    }
    state.modelDraftProvider = "";
    state.modelDraftSelection = "";
    state.modelDraftCustom = "";
    state.guideTab = null;
    await loadBasics();
    await validateProviderConnection({ keepBusy: true, silentSuccess: true });
  } catch (error) {
    toast(error.message || "Could not save AI service.");
  } finally {
    setBusy(false);
  }
}

async function validateProviderConnection(options = {}) {
  const provider = state.activeProvider;
  const info = state.providers.find((item) => item.id === provider);
  const key = providerKeyValue();

  if (info?.needs_key && !state.hasKey && !key) {
    toast("Paste the AI key first.");
    return false;
  }

  if (!options.keepBusy) setBusy(true);
  state.aiConnection = { provider, status: "testing", message: "Testing connection..." };
  render();
  try {
    const result = await ipc.provider.test(provider, key || null);
    state.aiConnection = {
      provider,
      status: result?.ok ? "ok" : "error",
      message: result?.message || "Provider responded.",
    };
    state.guideTab = null;
    if (state.aiConnection.status === "ok") markOnboarding("ai");
    addLog("AI connected", state.aiConnection.message, { status: "success" });
    if (!options.silentSuccess) toast("AI connection works.");
    await loadBasics();
    render();
    return true;
  } catch (error) {
    const message = friendlyProviderError(error, provider);
    state.aiConnection = {
      provider,
      status: "error",
      message,
    };
    addLog("AI connection failed", state.aiConnection.message, { status: "error" });
    toast("AI connection failed.");
    render();
    return false;
  } finally {
    if (!options.keepBusy) setBusy(false);
  }
}

async function fetchModelsForActiveProvider() {
  const provider = state.activeProvider;
  const info = state.providers.find((item) => item.id === provider);
  const key = providerKeyValue();

  if (info?.needs_key && !state.hasKey && !key) {
    toast("Paste the AI key first, or save the key and fetch again.");
    return;
  }

  state.modelOptionsLoading = true;
  state.modelOptionsError = "";
  state.modelOptions = [];
  state.modelOptionsProvider = provider;
  renderProviderForm();

  try {
    const models = await ipc.provider.fetchModels(provider, key || null);
    state.modelOptions = Array.isArray(models) ? models : [];
    state.modelOptionsProvider = provider;
    state.modelOptionsError = state.modelOptions.length
      ? ""
      : "No models were returned. Choose Other model to type one.";
    state.aiConnection = {
      provider,
      status: "ok",
      message: state.modelOptions.length
        ? `Provider responded with ${state.modelOptions.length} available models.`
        : "Provider responded, but no models were returned.",
    };
    state.guideTab = null;
    toast(state.modelOptions.length ? "Models fetched." : "No models returned.");
  } catch (error) {
    state.modelOptions = [];
    state.modelOptionsProvider = provider;
    state.modelOptionsError = friendlyProviderError(error, provider);
    state.aiConnection = {
      provider,
      status: "error",
      message: state.modelOptionsError,
    };
    toast("Could not fetch models.");
  } finally {
    state.modelOptionsLoading = false;
    renderProviderForm();
  }
}

async function deleteSavedProviderKey() {
  if (!state.activeProviderInfo?.needs_key) {
    toast("This AI service does not use a saved key.");
    return;
  }
  if (!state.hasKey) {
    toast("No saved key for this AI service.");
    return;
  }

  setBusy(true);
  try {
    await ipc.keychain.delete(state.activeProvider);
    state.hasKey = false;
    state.aiConnection = { provider: state.activeProvider, status: "untested", message: "" };
    providerControlSets().forEach((controls) => {
      controls.providerKey.value = "";
    });
    render();
    toast("Saved AI key deleted.");
  } catch (error) {
    toast(error.message || "Could not delete saved key.");
  } finally {
    setBusy(false);
  }
}

async function saveProjectSecret() {
  const name = el.secretName?.value?.trim() || "";
  const value = el.secretValue?.value || "";
  const filename = el.secretFile?.value || ".env.local";
  if (!state.workspace) {
    toast("Choose a project first.");
    await chooseWorkspace();
    if (!state.workspace) return;
  }
  if (!name || !value) {
    toast("Enter a secret name and value.");
    return;
  }

  setBusy(true);
  try {
    const result = await ipc.secrets.upsert(name, value, filename);
    if (el.secretValue) el.secretValue.value = "";
    addLog("Secret saved", `${name.toUpperCase()} saved to ${result.relative_path}. Value hidden.`, { status: "success" });
    await refreshProjectState(false);
    toast("Secret saved locally.");
  } catch (error) {
    addLog("Secret save failed", error.message || String(error), { status: "error" });
    toast(error.message || "Could not save secret.");
  } finally {
    setBusy(false);
  }
}

function clearSecretForm() {
  if (el.secretName) el.secretName.value = "";
  if (el.secretValue) el.secretValue.value = "";
  if (el.secretFile) el.secretFile.value = ".env.local";
}

async function saveBudget() {
  const value = Number(el.budgetInput?.value || 0);
  if (!Number.isFinite(value) || value < 0) {
    toast("Enter a valid budget.");
    return;
  }
  setBusy(true);
  try {
    await ipc.provider.setBudget(value);
    await loadBasics();
    toast("Budget saved.");
  } catch (error) {
    toast(error.message || "Could not save budget.");
  } finally {
    setBusy(false);
  }
}

async function resetSessionCost() {
  setBusy(true);
  try {
    await ipc.provider.resetSession();
    await loadBasics();
    toast("Session cost reset.");
  } catch (error) {
    toast(error.message || "Could not reset cost.");
  } finally {
    setBusy(false);
  }
}

async function applyProjectTemplate(templateId) {
  const template = PROJECT_TEMPLATES.find((item) => item.id === templateId);
  if (!template) return;
  if (!state.workspace) {
    toast("Choose a project first.");
    await chooseWorkspace();
    return;
  }

  setBusy(true);
  try {
    await ipc.brain.add(template.note, "decision");
    markOnboarding("note");
    addLog("Template applied", `${template.name} saved to Notes.`, { status: "success" });
    await refreshProjectState(false);
    toast("Template saved to Notes.");
  } catch (error) {
    toast(error.message || "Could not apply template.");
  } finally {
    setBusy(false);
  }
}

async function useLocalProvider() {
  setBusy(true);
  try {
    await ipc.provider.setActive("ollama");
    state.modelOptions = [];
    state.modelOptionsProvider = "";
    state.modelOptionsError = "";
    state.modelDraftProvider = "";
    state.modelDraftSelection = "";
    state.modelDraftCustom = "";
    state.guideTab = null;
    await loadBasics();
    await validateProviderConnection({ keepBusy: true, silentSuccess: true });
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

async function signGate(gateId) {
  if (!state.workspace) {
    toast("Choose a project first.");
    return;
  }
  const signer = safeText(state.gateSigner).trim();
  if (!signer) {
    toast("Enter signer name first.");
    el.gateSigner?.focus();
    return;
  }

  setBusy(true);
  addLog(`Signing G${gateId}`, `Signer: ${signer}`, {
    kind: "command",
    status: "running",
    command: `gate:sign G${gateId}`,
  });
  try {
    await ipc.gates.sign(Number(gateId), signer);
    replaceLastLog(`Signed G${gateId}`, `Signed by ${signer}.`, {
      kind: "command",
      status: "success",
      command: `gate:sign G${gateId}`,
    });
    await refreshProjectState(true);
    markOnboarding("gate");
    toast(`G${gateId} signed.`);
  } catch (error) {
    replaceLastLog(`Could not sign G${gateId}`, error.message || String(error), {
      kind: "command",
      status: "error",
      command: `gate:sign G${gateId}`,
    });
    toast("Gate signing failed.");
  } finally {
    setBusy(false);
  }
}

function parseCommand(input) {
  const parts = input.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return null;
  if (parts[0].toLowerCase() === "signalos" && parts[1]) {
    const command = parts[1].startsWith("/") ? parts[1] : `/${parts[1]}`;
    return { command, args: parts.slice(2) };
  }
  const command = parts[0].startsWith("/") ? parts[0] : `/${parts[0]}`;
  return { command, args: parts.slice(1) };
}

function looksLikeSignalCommand(value) {
  const trimmed = value.trim();
  return trimmed.startsWith("/")
    || /^signalos\s+signal-/i.test(trimmed)
    || /^signal-[a-z0-9-]+/i.test(trimmed);
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

  const info = commandInfo(parsed.command);
  const isSetup = parsed.command === "/signal-init" || parsed.command === "signal-init";
  if (isSetup) {
    state.lastSetup = { status: "running", command: parsed.command, startedAt: Date.now() };
  }

  state.runningCommand = parsed.command;
  setBusy(true);
  startCommandProgress(parsed.command, info.detail || "Waiting for the SignalOS engine.");
  addLog(
    info.status === "preview" ? "Preview command" : info.label,
    info.status === "preview"
      ? "This command may return a command brief instead of executing work."
      : "Waiting for the SignalOS engine...",
    { kind: "command", status: "running", command: parsed.command },
  );
  try {
    const result = await ipc.signal.runAndWait(parsed.command, parsed.args);
    await loadBasics();
    await refreshProjectState(Boolean(options.markChecked));
    if (options.markChecked) state.statusChecked = true;
    state.guideTab = null;
    if (isSetup) {
      state.lastSetup = {
        status: state.artifacts?.initialized ? "ok" : "error",
        command: parsed.command,
        completedAt: Date.now(),
        output: formatResult(result),
      };
      replaceLastLog(
        state.artifacts?.initialized ? "Project setup complete" : "Setup needs attention",
        formatSetupResult(result),
        {
          kind: "command",
          status: state.artifacts?.initialized ? "success" : "error",
          command: parsed.command,
          cards: buildCommandCards(parsed.command, result),
        },
      );
      if (state.artifacts?.initialized) markOnboarding("setup");
      state.guideTab = "start";
      toast(state.artifacts?.initialized ? "Project setup complete." : "Setup finished but files are missing.");
    } else {
      if (parsed.command === "/signal-status") markOnboarding("status");
      replaceLastLog(`${info.label} complete`, formatResult(result), {
        kind: "command",
        status: "success",
        command: parsed.command,
        cards: buildCommandCards(parsed.command, result),
      });
      toast(`${info.label} complete.`);
    }
  } catch (error) {
    if (isSetup) {
      state.lastSetup = {
        status: "error",
        command: parsed.command,
        completedAt: Date.now(),
        output: error.message || String(error),
      };
    }
    replaceLastLog("Could not finish", nextStepError(error), {
      kind: "command",
      status: "error",
      command: parsed.command,
    });
    toast("Command failed.");
  } finally {
    stopCommandProgress();
    state.runningCommand = null;
    setBusy(false);
  }
}

function formatSetupResult(result) {
  const output = formatResult(result);
  const artifacts = Array.isArray(state.artifacts?.artifacts) ? state.artifacts.artifacts : [];
  const found = artifacts.filter((artifact) => artifact.exists).length;
  const total = artifacts.length;
  const next = state.wave?.phase_name
    ? `Current phase: ${state.wave.phase_name}.`
    : "Run /signal-status to load the current phase.";
  return [
    output,
    total ? `Project files found: ${found}/${total}.` : "",
    next,
  ].filter(Boolean).join("\n\n");
}

function buildCommandCards(command, result) {
  const text = formatResult(result);
  const artifacts = Array.isArray(state.artifacts?.artifacts) ? state.artifacts.artifacts : [];
  const found = artifacts.filter((artifact) => artifact.exists).length;
  const total = artifacts.length;
  const info = commandInfo(command);
  const cards = [
    { label: "Command", value: `${info.label} (${statusLabel(info.status)})` },
  ];

  if (command === "/signal-init" || command === "signal-init") {
    cards.push({
      label: "Setup",
      value: state.artifacts?.initialized ? "Expected project files found" : "Expected files still missing",
    });
    if (total) cards.push({ label: "Project files", value: `${found}/${total} found` });
  }

  if (command === "/signal-status" || command === "signal-status" || command === "/signal-init" || command === "signal-init") {
    const phase = state.wave?.phase_name || extractResultField(text, "Phase") || extractResultField(text, "Current phase");
    const next = extractResultField(text, "Next action") || extractResultField(text, "Next") || extractStatusCardNextAction(text);
    cards.push({ label: "Phase", value: phase || "Not loaded" });
    cards.push({ label: "Next action", value: next || nextAction().title });
    if (state.gates.length) cards.push({ label: "Gates", value: `${state.gates.length} loaded` });
  }

  if (info.status === "advanced" || info.status === "preview") {
    const mode = detectCommandOutputMode(text);
    const next = commandNextStep(command, info.status, mode);
    cards.push({ label: "Output", value: mode });
    cards.push({ label: "Next", value: next });
  }

  if (!cards.some((card) => card.label === "Output")) {
    cards.push({ label: "Output", value: `${text.split(/\r?\n/).filter(Boolean).length || 1} line result` });
  }

  return cards.slice(0, 8);
}

function extractStatusCardNextAction(text) {
  const lines = safeText(text).split(/\r?\n/);
  const marker = lines.findIndex((line) => /NEXT ACTION/i.test(line));
  if (marker < 0) return "";
  for (const line of lines.slice(marker + 1, marker + 5)) {
    const cleaned = line
      .replace(/[║╚═╔╗╠╣]/g, "")
      .trim();
    if (cleaned && !/^[-=]+$/.test(cleaned)) return cleaned;
  }
  return "";
}

function extractResultField(text, label) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = safeText(text).match(new RegExp(`${escaped}\\s*:\\s*([^\\n.]+)`, "i"));
  return match ? match[1].trim() : "";
}

function detectCommandOutputMode(text) {
  const value = safeText(text);
  if (/command brief|not wired|preview|spec/i.test(value)) return "Command brief";
  if (/error|failed|traceback|exception/i.test(value)) return "Error";
  if (/created|updated|wrote|saved|signed|complete|done/i.test(value)) return "Executed";
  if (/usage:|options:|arguments:/i.test(value)) return "CLI help";
  return value.length > 600 ? "Detailed output" : "Short output";
}

function commandNextStep(command, status, mode) {
  if (mode === "Error") return "Open Settings, copy diagnostics, then retry after fixing the engine or input.";
  if (status === "preview") return "Use the brief as guidance, then run a ready command or ask AI to plan the action.";
  if (/plan/i.test(command)) return "Open the generated plan artifact or run /signal-status.";
  if (/qa|guard|careful|cso/i.test(command)) return "Record evidence in Notes and export a handoff if the result matters.";
  if (/deploy|ship|canary|land/i.test(command)) return "Verify release gates, then export an issue report before proceeding.";
  return "Review the output, save a note if it changes the project, then run /signal-status.";
}

function nextStepError(error) {
  const text = error?.message || String(error);
  if (/sidecar|engine|not started/i.test(text)) {
    return `${text}\n\nOpen Settings, test the SignalOS engine, then try the command again.`;
  }
  if (/timed out/i.test(text)) {
    return `${text}\n\nThe command may still be running or the engine may be stuck. Test the engine in Settings.`;
  }
  return text;
}

function friendlyProviderError(error, provider = state.activeProvider) {
  const raw = error?.message || String(error || "Connection failed.");
  const name = state.providers.find((item) => item.id === provider)?.name || provider || "AI provider";
  if (/401|403|unauthorized|forbidden|api key|invalid key|authentication/i.test(raw)) {
    return `${name} rejected the key. Replace the saved key, then test the connection again.`;
  }
  if (/404|model/i.test(raw)) {
    return `${name} could not use that model. Fetch models or choose Other model and type a valid model name.`;
  }
  if (/429|rate limit|quota/i.test(raw)) {
    return `${name} rate limit or quota was reached. Try later or choose another provider.`;
  }
  if (/network|dns|timed out|timeout|connect|connection/i.test(raw)) {
    return `SignalOS could not reach ${name}. Check network access or local Ollama status, then test again.`;
  }
  return raw;
}

function startCommandProgress(command, detail) {
  stopCommandProgress();
  state.commandStartedAt = Date.now();
  state.commandTimer = window.setInterval(() => {
    if (!state.runningCommand) return;
    const elapsed = Math.max(1, Math.round((Date.now() - state.commandStartedAt) / 1000));
    replaceLastLog("Still working", `${detail}\n\nElapsed: ${elapsed}s`, {
      kind: "command",
      status: "running",
      command,
    });
  }, 2500);
}

function stopCommandProgress() {
  if (state.commandTimer) {
    window.clearInterval(state.commandTimer);
    state.commandTimer = null;
  }
}

async function cancelRunningCommand() {
  if (!state.runningCommand) return;
  const command = state.runningCommand;
  ipc.signal.cancelPending("Command stopped by user.");
  stopCommandProgress();
  state.runningCommand = null;
  state.busy = false;
  replaceLastLog("Command stopped", `${command} was stopped. Restarting the SignalOS engine before the next command.`, {
    kind: "command",
    status: "error",
    command,
  });
  await restartEngineStatus({ silent: true });
  render();
  toast("Command stopped.");
}

async function askSignalOS(question) {
  if (isBuildIntent(question)) {
    if (el.buildPrompt) el.buildPrompt.value = question;
    switchView("guide");
    toast("That is a build request. Review it, then press Build app.");
    return;
  }
  if (!state.workspace) {
    toast("Choose a project first.");
    await chooseWorkspace();
    return;
  }
  if (!aiReady()) {
    state.guideTab = "ai";
    render();
    toast("Test the AI connection first.");
    return;
  }

  const context = state.attachments
    .filter((item) => item.status === "accepted")
    .map((item) => `${item.name}\n${item.summary}`)
    .join("\n\n");
  const prompt = [
    question,
    state.workspace ? `\nProject folder: ${state.workspace}` : "",
    context ? `\nAttached safe context:\n${context}` : "",
  ].filter(Boolean).join("\n");

  setBusy(true);
  addLog("You", question, { kind: "ai", status: "success" });
  addLog("SignalOS AI", "Thinking...", { kind: "ai", status: "running" });
  try {
    const response = await ipc.provider.chat(
      state.activeProvider,
      state.activeProviderInfo?.model || null,
      prompt,
    );
    replaceLastLog("AI response", response?.text || "The provider returned an empty response.", {
      kind: "ai",
      status: "success",
    });
    await loadBasics();
    renderSettings();
  } catch (error) {
    replaceLastLog("AI request failed", friendlyProviderError(error, state.activeProvider), {
      kind: "ai",
      status: "error",
    });
    toast("AI request failed.");
  } finally {
    setBusy(false);
  }
}

function isBuildIntent(text) {
  const value = safeText(text).toLowerCase();
  return /\b(build|create|make|scaffold|generate|start fresh|implement|want to do|want)\b/.test(value)
    && /\b(app|site|website|todo|task|dashboard|tool|project|management)\b/.test(value);
}

async function buildProjectFromPrompt() {
  const idea = el.buildPrompt?.value?.trim() || "";
  const stackId = selectedBuildStack();
  const stack = buildStackInfo(stackId);
  if (!idea) {
    toast("Describe the app first.");
    el.buildPrompt?.focus();
    return;
  }
  if (!state.workspace) {
    toast("Choose a project folder first.");
    await chooseWorkspace();
    if (!state.workspace) return;
  }
  if (!aiReady()) {
    state.guideTab = "ai";
    switchView("project");
    render();
    toast("Connect and test AI first.");
    return;
  }

  setBusy(true);
  setBuilderState({
    status: "running",
    stack: stackId,
    phase: "prepare",
    done: [],
    files: [],
    summary: "",
    message: "Preparing SignalOS for this project.",
    runInstructions: stack.run,
    briefPath: "",
    entryPath: stack.entry,
  });
  addLog("Build request", idea, { kind: "ai", status: "success" });
  addLog("SignalOS Builder", "Preparing project, planning scope, then generating files.", { kind: "ai", status: "running" });
  try {
    const prep = await prepareSignalOSForBuild();
    setBuilderPhase("plan", "Creating a SignalOS build plan and file bundle.");
    const response = await ipc.provider.chat(
      state.activeProvider,
      state.activeProviderInfo?.model || null,
      buildProjectPrompt(idea, stackId, prep),
    );
    const generated = parseGeneratedProject(response?.text || "", stackId);
    const brief = buildSignalOSBuildBrief(idea, generated, stack, prep);
    const evidence = await safeCall(
      () => ipc.project.exportFile("builds", `build-${buildTimestamp()}.md`, brief),
      null,
    );
    await safeCall(() => ipc.brain.add(brief, "decision"), null);

    setBuilderPhase("write", `Writing ${generated.files.length} ${generated.stackLabel || stack.label} files.`);
    const result = await ipc.project.writeFiles(generated.files, true);
    setBuilderPhase("review", "Refreshing SignalOS status after file write.");
    const statusOutput = await ipc.signal.runAndWait("/signal-status", []);
    await loadBasics();
    await refreshProjectState(true);
    state.statusChecked = true;
    markOnboarding("status");

    state.builder = {
      status: "success",
      message: generated.summary || `Wrote ${result.files.length} files to ${basename(state.workspace)}.`,
      files: result.files,
      summary: generated.summary || "",
      stack: generated.stack || stackId,
      phase: "review",
      done: BUILDER_PHASES.map(([id]) => id),
      runInstructions: generated.runInstructions || buildStackInfo(generated.stack || stackId).run,
      briefPath: evidence?.relative_path || "",
      entryPath: generated.entryPath || pickBuildEntry(result.files, generated.stack || stackId),
    };
    replaceLastLog("Build complete", state.builder.message, {
      kind: "ai",
      status: "success",
      cards: [
        { label: "Files", value: `${result.files.length} written` },
        { label: "Stack", value: buildStackInfo(state.builder.stack).label },
        { label: "Next", value: state.builder.runInstructions },
        { label: "Status", value: trimForCard(formatResult(statusOutput)) },
      ],
    });
    toast("App files written.");
  } catch (error) {
    const message = error.message || String(error);
    setBuilderState({
      status: "error",
      message,
      files: [],
      summary: "",
    });
    replaceLastLog("Build failed", message, { kind: "ai", status: "error" });
    toast("Build failed.");
  } finally {
    setBusy(false);
    renderBuilder();
  }
}

async function prepareSignalOSForBuild() {
  const prep = {
    initializedBefore: Boolean(state.artifacts?.initialized),
    initOutput: "",
    statusOutput: "",
    phase: state.wave?.phase_name || "",
    nextAction: "",
  };

  setBuilderPhase("prepare", "Checking SignalOS project setup.");
  if (!state.artifacts?.initialized) {
    prep.initOutput = formatResult(await ipc.signal.runAndWait("/signal-init", []));
    markOnboarding("setup");
  }

  prep.statusOutput = formatResult(await ipc.signal.runAndWait("/signal-status", []));
  await loadBasics();
  await refreshProjectState(true);
  state.statusChecked = true;
  markOnboarding("status");
  prep.phase = state.wave?.phase_name || "";
  prep.nextAction = nextActionTextFromState();
  return prep;
}

function nextActionTextFromState() {
  const gate = currentGate();
  if (hasActiveWave() && gate) return `Work toward ${gate.name}.`;
  if (hasActiveWave()) return "Review the latest SignalOS status.";
  return "Continue from the generated plan and run status after changes.";
}

function buildProjectPrompt(idea, requestedStack, prep = {}) {
  const stack = buildStackInfo(requestedStack);
  return [
    "You are SignalOS Builder, running inside a governed SignalOS project.",
    "Return ONLY valid JSON. No markdown, no prose, no code fences.",
    "Schema:",
    "{\"summary\":\"short build summary\",\"stack\":\"react-vite|next|node-express|python-flask|static\",\"entry_path\":\"path to main entry\",\"run_instructions\":\"exact local run command\",\"signalos_plan\":{\"goal\":\"...\",\"user_journey\":[\"...\"],\"scope\":[\"...\"],\"tasks\":[\"...\"],\"risks\":[\"...\"],\"acceptance\":[\"...\"]},\"files\":[{\"path\":\"relative/path\",\"content\":\"complete file contents\"}]}",
    "Rules:",
    `- Requested stack: ${requestedStack}. ${stack.label}.`,
    ...stack.prompt.map((line) => `- ${line}`),
    "- This is not a chat answer. Generate complete files that SignalOS can write to disk.",
    "- Include complete file contents, not snippets.",
    "- Keep paths relative and inside the project folder.",
    "- Do not write .signalos, core, integrations, .git, .env, private keys, certificates, or secrets.",
    "- Do not use external image/CDN URLs. Keep the first build runnable after dependencies are installed.",
    "- Include README.md with a short purpose, features, and exact run commands.",
    "- Make the UI product-quality, practical, and not a marketing page.",
    "- Keep the app focused on the user's requested workflow, with useful empty, error, and saved states.",
    "- The signalos_plan must be specific enough for a user to understand what was built and what remains.",
    "",
    `SignalOS phase: ${prep.phase || "not loaded"}`,
    `SignalOS next action: ${prep.nextAction || "not loaded"}`,
    `User request: ${idea}`,
  ].join("\n");
}

function parseGeneratedProject(text, requestedStack) {
  const raw = safeText(text).trim();
  const jsonText = extractJsonObject(raw);
  if (!jsonText) {
    throw new Error("AI did not return a file bundle. Try Build again or use a more direct app description.");
  }
  let parsed;
  try {
    parsed = JSON.parse(jsonText);
  } catch (error) {
    throw new Error("AI returned invalid build JSON. Try Build again.");
  }
  const requested = BUILD_STACKS[requestedStack] ? requestedStack : "react-vite";
  const returned = safeText(parsed.stack).trim();
  const stack = BUILD_STACKS[returned] && requested === "auto" ? returned
    : BUILD_STACKS[returned] && returned === requested ? returned
      : requested === "auto" ? inferBuildStack(parsed.files) : requested;
  const files = Array.isArray(parsed.files) ? parsed.files : [];
  const normalized = files
    .map((file) => ({
      path: normalizeGeneratedPath(file.path),
      content: safeText(file.content),
    }))
    .filter((file) => file.path && file.content);
  if (!normalized.length) {
    throw new Error("AI returned no writable files.");
  }
  validateGeneratedFiles(normalized, stack);
  return {
    summary: safeText(parsed.summary, `Generated ${normalized.length} files.`),
    stack,
    stackLabel: buildStackInfo(stack).label,
    entryPath: normalizeGeneratedPath(parsed.entry_path) || pickBuildEntry(normalized, stack),
    runInstructions: safeText(parsed.run_instructions, buildStackInfo(stack).run),
    plan: normalizeSignalOSPlan(parsed.signalos_plan),
    files: normalized,
  };
}

function normalizeGeneratedPath(value) {
  return safeText(value)
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "")
    .trim();
}

function inferBuildStack(files = []) {
  const paths = (Array.isArray(files) ? files : []).map((file) => normalizeGeneratedPath(file.path).toLowerCase());
  if (paths.includes("app.py") || paths.includes("requirements.txt")) return "python-flask";
  if (paths.some((path) => path.startsWith("app/")) || paths.some((path) => path.startsWith("pages/"))) return "next";
  if (paths.includes("server.js")) return "node-express";
  if (paths.includes("src/main.jsx") || paths.includes("src/main.tsx")) return "react-vite";
  if (paths.includes("index.html")) return "static";
  return "react-vite";
}

function validateGeneratedFiles(files, stackId) {
  const stack = buildStackInfo(stackId);
  const paths = new Set(files.map((file) => file.path.toLowerCase()));
  const missing = stack.required.filter((path) => !paths.has(path.toLowerCase()));
  if (missing.length) {
    throw new Error(`AI did not include required ${stack.label} file(s): ${missing.join(", ")}.`);
  }
  if (stackId === "react-vite" && !paths.has("src/main.jsx") && !paths.has("src/main.tsx")) {
    throw new Error("AI did not include src/main.jsx or src/main.tsx for the React app.");
  }
  if (stackId === "next") {
    const hasPage = paths.has("app/page.jsx") || paths.has("app/page.tsx") || paths.has("pages/index.jsx") || paths.has("pages/index.tsx") || paths.has("pages/index.js");
    if (!hasPage) throw new Error("AI did not include a Next.js page file.");
  }
  if (stackId === "node-express" && !paths.has("server.js") && !paths.has("index.js") && !paths.has("app.js")) {
    throw new Error("AI did not include a Node server entry file.");
  }
}

function pickBuildEntry(files, stackId) {
  const stack = buildStackInfo(stackId);
  const paths = (Array.isArray(files) ? files : []).map((file) => file.relative_path || file.path || "");
  const preferred = [
    stack.entry,
    "index.html",
    "README.md",
    "src/App.jsx",
    "src/main.jsx",
    "app/page.jsx",
    "server.js",
    "app.py",
    "package.json",
  ];
  return preferred.find((path) => paths.some((item) => item.toLowerCase() === path.toLowerCase())) || paths[0] || stack.entry;
}

function normalizeSignalOSPlan(value) {
  const plan = value && typeof value === "object" ? value : {};
  const list = (key) => Array.isArray(plan[key])
    ? plan[key].map((item) => safeText(item).trim()).filter(Boolean).slice(0, 12)
    : [];
  return {
    goal: safeText(plan.goal, "Create the requested first working app."),
    userJourney: list("user_journey"),
    scope: list("scope"),
    tasks: list("tasks"),
    risks: list("risks"),
    acceptance: list("acceptance"),
  };
}

function buildSignalOSBuildBrief(idea, generated, stack, prep) {
  const plan = generated.plan || normalizeSignalOSPlan(null);
  const section = (title, items) => [
    `## ${title}`,
    ...(items.length ? items.map((item) => `- ${item}`) : ["- Not specified."]),
    "",
  ].join("\n");
  return [
    `# SignalOS Build Brief - ${new Date().toISOString()}`,
    "",
    `Request: ${idea}`,
    `Stack: ${generated.stackLabel || stack.label}`,
    `SignalOS phase before build: ${prep.phase || "not loaded"}`,
    `SignalOS next action before build: ${prep.nextAction || "not loaded"}`,
    "",
    "## Goal",
    plan.goal,
    "",
    section("User Journey", plan.userJourney),
    section("Scope", plan.scope),
    section("Tasks", plan.tasks),
    section("Risks", plan.risks),
    section("Acceptance", plan.acceptance),
    "## Generated Files",
    ...generated.files.map((file) => `- ${file.path}`),
    "",
    "## Run",
    generated.runInstructions || stack.run,
    "",
  ].join("\n");
}

function buildTimestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function trimForCard(value, max = 120) {
  const text = safeText(value).replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

function extractJsonObject(text) {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fenced ? fenced[1].trim() : text;
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");
  if (start < 0 || end <= start) return "";
  return candidate.slice(start, end + 1);
}

function formatResult(result) {
  if (typeof result === "string") return result.trim() || "Done.";
  if (result === null || result === undefined) return "Done.";
  return JSON.stringify(result, null, 2);
}

async function handleAttachmentFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;

  setBusy(true);
  addLog("Reading files", "Checking file types and removing secrets...", { status: "running" });
  try {
    const payloads = [];
    let totalBytes = 0;
    for (const file of files.slice(0, 10)) {
      totalBytes += file.size || 0;
      if (totalBytes > 40 * 1024 * 1024) {
        toast("Too many files at once.");
        break;
      }
      payloads.push({
        name: file.name,
        type: file.type,
        size: file.size,
        data_base64: await readFileBase64(file),
      });
    }

    const analyzed = await ipc.attachments.analyze(payloads);
    const items = Array.isArray(analyzed) ? analyzed : [];
    state.attachments = [...state.attachments, ...items].slice(-12);
    const accepted = items.filter((item) => item.status === "accepted").length;
    const blocked = items.length - accepted;
    replaceLastLog("Files checked", `${accepted} ready. ${blocked} blocked. Secret values are not kept in chat context.`, { status: "success" });
    renderAttachments();
    toast(blocked ? "Files checked. Some were blocked." : "Files ready.");
  } catch (error) {
    replaceLastLog("Could not read files", error.message || String(error), { status: "error" });
    toast("Could not attach those files.");
  } finally {
    el.attachmentInput.value = "";
    setBusy(false);
  }
}

function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.split(",", 2)[1] : value);
    };
    reader.onerror = () => reject(reader.error || new Error("File read failed"));
    reader.readAsDataURL(file);
  });
}

function addLog(title, body, options = {}) {
  state.log.push({
    title,
    body: safeText(body),
    ts: Date.now(),
    kind: options.kind || "",
    status: options.status || "",
    command: options.command || "",
    cards: Array.isArray(options.cards) ? options.cards : [],
  });
  state.log = state.log.slice(-80);
  persistTranscript();
  renderActivity();
}

function replaceLastLog(title, body, options = {}) {
  if (!state.log.length) {
    addLog(title, body, options);
    return;
  }
  state.log[state.log.length - 1] = {
    ...state.log[state.log.length - 1],
    title,
    body: safeText(body),
    ts: Date.now(),
    ...options,
    cards: Array.isArray(options.cards) ? options.cards : [],
  };
  persistTranscript();
  renderActivity();
}

function switchView(view) {
  state.view = view;
  render();
}

async function selectProvider(selected) {
  state.activeProvider = selected;
  state.activeProviderInfo = state.providers.find((provider) => provider.id === selected) || null;
  state.hasKey = state.activeProviderInfo
    ? await safeCall(() => ipc.keychain.has(state.activeProviderInfo.id), false)
    : false;
  state.modelOptions = [];
  state.modelOptionsProvider = "";
  state.modelOptionsError = "";
  state.modelDraftProvider = "";
  state.modelDraftSelection = "";
  state.modelDraftCustom = "";
  state.aiConnection = { provider: selected, status: "untested", message: "" };
  render();
}

function updateModelDraft(selection) {
  state.modelDraftProvider = state.activeProvider;
  state.modelDraftSelection = selection;
  if (state.modelDraftSelection === "__custom") {
    state.modelDraftCustom = state.modelDraftCustom || state.activeProviderInfo?.model || "";
  } else {
    state.modelDraftCustom = "";
  }
  renderProviderForm();
}

function bindEvents() {
  $("[data-view='guide']")?.addEventListener("click", () => switchView("guide"));
  $("[data-view='project']")?.addEventListener("click", () => switchView("project"));
  $("[data-view='chat']")?.addEventListener("click", () => switchView("chat"));
  $("[data-view='dashboard']")?.addEventListener("click", () => switchView("dashboard"));
  $("[data-view='brain']")?.addEventListener("click", () => switchView("brain"));
  $("[data-view='secrets']")?.addEventListener("click", () => switchView("secrets"));
  $("[data-view='history']")?.addEventListener("click", () => switchView("history"));
  $("[data-view='settings']")?.addEventListener("click", () => switchView("settings"));
  $("[data-view='help']")?.addEventListener("click", () => switchView("help"));
  $$("[data-step-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.guideTab = button.dataset.stepTab;
      render();
    });
  });

  $("#chooseProject").addEventListener("click", chooseWorkspace);
  el.buildApp?.addEventListener("click", buildProjectFromPrompt);
  el.buildStack?.addEventListener("change", () => {
    setBuilderState({
      stack: selectedBuildStack(),
      entryPath: "",
      runInstructions: "",
    });
  });
  el.openBuiltApp?.addEventListener("click", () => {
    const stack = buildStackInfo(state.builder.stack || selectedBuildStack());
    openProjectArtifact(state.builder.entryPath || stack.entry);
  });
  el.openChatFromBuild?.addEventListener("click", () => switchView("chat"));
  $("#settingsChooseProject").addEventListener("click", chooseWorkspace);
  $("#forgetProject").addEventListener("click", forgetWorkspace);
  $("#saveProvider").addEventListener("click", saveProvider);
  el.settingsSaveProvider?.addEventListener("click", saveProvider);
  el.settingsDeleteKey?.addEventListener("click", deleteSavedProviderKey);
  el.settingsRefreshSecrets?.addEventListener("click", () => refreshProjectState(false));
  el.saveSecret?.addEventListener("click", saveProjectSecret);
  el.clearSecretForm?.addEventListener("click", clearSecretForm);
  el.saveBudget?.addEventListener("click", saveBudget);
  el.resetSessionCost?.addEventListener("click", resetSessionCost);
  el.runStatusFromResult?.addEventListener("click", checkStatus);
  el.copyDiagnostics?.addEventListener("click", copyAppDiagnostics);
  el.testEngine?.addEventListener("click", () => testEngineStatus());
  el.restartEngine?.addEventListener("click", () => restartEngineStatus());
  el.copySettingsDiagnostics?.addEventListener("click", copyAppDiagnostics);
  el.settingsExportIssueReport?.addEventListener("click", exportIssueReport);
  el.cancelCommand?.addEventListener("click", cancelRunningCommand);
  el.dashboardRunStatus?.addEventListener("click", checkStatus);
  el.dashboardOpenChat?.addEventListener("click", () => switchView("chat"));
  el.dashboardExportHandoff?.addEventListener("click", exportHandoffReport);
  el.exportHandoff?.addEventListener("click", exportHandoffReport);
  el.exportIssueReport?.addEventListener("click", exportIssueReport);
  el.settingsCheckUpdates?.addEventListener("click", checkForUpdates);
  el.updateChannelSelect?.addEventListener("change", () => {
    state.updateChannel = el.updateChannelSelect.value === "stable" ? "stable" : "beta";
    localStorage.setItem(LS_UPDATE_CHANNEL, state.updateChannel);
    renderSettings();
  });
  el.gateSigner?.addEventListener("input", () => {
    state.gateSigner = el.gateSigner.value.trim();
    localStorage.setItem(LS_GATE_SIGNER, state.gateSigner);
  });
  $("#quickOllama").addEventListener("click", useLocalProvider);
  $("#refreshButton").addEventListener("click", () => refreshProjectState(true));
  el.attachmentPick.addEventListener("click", () => el.attachmentInput.click());
  el.attachmentButton.addEventListener("click", () => el.attachmentInput.click());
  el.attachmentInput.addEventListener("change", (event) => handleAttachmentFiles(event.target.files));
  ["dragenter", "dragover"].forEach((eventName) => {
    el.attachmentDrop.addEventListener(eventName, (event) => {
      event.preventDefault();
      el.attachmentDrop.classList.add("drag-over");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    el.attachmentDrop.addEventListener(eventName, (event) => {
      event.preventDefault();
      el.attachmentDrop.classList.remove("drag-over");
    });
  });
  el.attachmentDrop.addEventListener("drop", (event) => {
    handleAttachmentFiles(event.dataTransfer?.files);
  });

  providerControlSets().forEach((controls) => {
    controls.providerSelect.addEventListener("change", () => selectProvider(controls.providerSelect.value));
    controls.fetchModels.addEventListener("click", fetchModelsForActiveProvider);
    controls.providerModelSelect.addEventListener("change", () => {
      updateModelDraft(controls.providerModelSelect.value);
    });
    controls.providerModel.addEventListener("input", () => {
      state.modelDraftProvider = state.activeProvider;
      state.modelDraftSelection = "__custom";
      state.modelDraftCustom = controls.providerModel.value;
    });
    controls.providerKey.addEventListener("input", () => {
      renderGuide();
      renderProviderForm();
      renderSettings();
    });
  });

  el.commandForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = el.commandInput.value.trim();
    el.commandInput.value = "";
    if (value && !looksLikeSignalCommand(value)) {
      askSignalOS(value);
      return;
    }
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
      markOnboarding("note");
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
    listen("menu:export-audit", exportHandoffReport);
    listen("menu:nav", (event) => {
      const mapped = {
        chat: "chat",
        dashboard: "dashboard",
        brain: "brain",
        audit: "history",
      };
      switchView(mapped[event.payload] || "guide");
    });
    listen("sidecar:error", (event) => {
      state.sidecarError = safeText(event.payload, "Unknown sidecar error");
      state.engine = { status: "error", message: state.sidecarError, version: "", checkedAt: Date.now() };
      addLog("Engine failed", state.sidecarError, { status: "error" });
      render();
    });
    listen("sidecar:stderr", (event) => {
      state.engine = {
        ...state.engine,
        status: state.engine.status === "ok" ? "ok" : "unknown",
        message: safeText(event.payload, "Engine wrote a diagnostic message."),
        checkedAt: Date.now(),
      };
      renderEngine();
    });
    listen("sidecar:status", (event) => {
      state.engine = normalizeEngineRuntime(event.payload);
      if (state.engine.status === "ok") state.sidecarError = "";
      render();
    });
    listen("sidecar:terminated", (event) => {
      state.engine = {
        status: "error",
        message: `Engine terminated${event.payload === null || event.payload === undefined ? "" : ` with code ${event.payload}`}.`,
        version: state.engine.version,
        checkedAt: Date.now(),
      };
      render();
    });
  }
}

async function testEngineStatus(options = {}) {
  const runtime = await safeCall(() => ipc.engine.status(), null);
  if (runtime) {
    state.engine = normalizeEngineRuntime(runtime);
    renderEngine();
  }
  state.engine = { ...state.engine, status: "unknown", message: "Testing engine...", checkedAt: Date.now() };
  renderEngine();
  try {
    const result = await ipc.engine.ping();
    state.sidecarError = "";
    state.engine = {
      status: "ok",
      message: "Engine responded to ping.",
      version: safeText(result?.version),
      checkedAt: Date.now(),
    };
    if (!options.silent) {
      addLog("Engine ready", state.engine.message, { status: "success" });
      toast("SignalOS engine is ready.");
    }
    render();
    return true;
  } catch (error) {
    state.engine = {
      status: "error",
      message: error.message || String(error),
      version: "",
      checkedAt: Date.now(),
    };
    state.sidecarError = state.engine.message;
    if (!options.silent) {
      addLog("Engine check failed", state.engine.message, { status: "error" });
      toast("SignalOS engine needs attention.");
    }
    render();
    return false;
  }
}

async function restartEngineStatus(options = {}) {
  state.engine = { ...state.engine, status: "unknown", message: "Restarting engine...", checkedAt: Date.now() };
  state.sidecarError = "";
  renderEngine();
  try {
    const runtime = await ipc.engine.restart();
    state.engine = normalizeEngineRuntime(runtime, "Engine restarted.");
    state.sidecarError = "";
    if (!options.silent) {
      addLog("Engine restarted", state.engine.message, { status: "success" });
      toast("SignalOS engine restarted.");
    }
    render();
    return true;
  } catch (error) {
    state.engine = {
      status: "error",
      message: error.message || String(error),
      version: "",
      checkedAt: Date.now(),
    };
    state.sidecarError = state.engine.message;
    if (!options.silent) {
      addLog("Engine restart failed", state.engine.message, { status: "error" });
      toast("Engine restart failed.");
    }
    render();
    return false;
  }
}

function normalizeEngineRuntime(runtime, fallback = "") {
  const running = Boolean(runtime?.running);
  return {
    status: running ? "ok" : "error",
    message: runtime?.last_error || runtime?.last_event || fallback || (running ? "Engine is running." : "Engine is not running."),
    version: state.engine.version || "",
    checkedAt: runtime?.updated_at_ms ? Number(runtime.updated_at_ms) : Date.now(),
    pid: runtime?.pid || null,
    generation: runtime?.generation || 0,
  };
}

function diagnosticsPayload() {
  return {
    generated_at: new Date().toISOString(),
    workspace: state.workspace,
    app_view: state.view,
    engine: state.engine,
    sidecar_error: state.sidecarError,
    ai: {
      provider: state.activeProvider,
      provider_name: state.activeProviderInfo?.name || "",
      model: state.activeProviderInfo?.model || "",
      has_saved_key: Boolean(state.hasKey),
      connection: state.aiConnection,
    },
    project: {
      status_checked: state.statusChecked,
      wave: state.wave,
      gates: state.gates,
      artifacts: state.artifacts,
      git: state.git,
      secret_file_count: Array.isArray(state.secrets) ? state.secrets.length : 0,
    },
    recent_log: state.log.slice(-10),
  };
}

function timestampSlug() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function markdownList(items) {
  return items.filter(Boolean).map((item) => `- ${safeText(item).replace(/\n/g, " ")}`).join("\n");
}

function issueReportMarkdown() {
  const diagnostics = diagnosticsPayload();
  return [
    "# SignalOS Issue Report",
    "",
    `Generated: ${diagnostics.generated_at}`,
    `Workspace: ${diagnostics.workspace || "No project selected"}`,
    "",
    "## Summary",
    markdownList([
      `App view: ${diagnostics.app_view}`,
      `Engine: ${diagnostics.engine?.status || "unknown"} - ${diagnostics.engine?.message || ""}`,
      `AI: ${diagnostics.ai?.provider_name || diagnostics.ai?.provider || "not selected"} / ${diagnostics.ai?.model || "no model"}`,
      `AI connected: ${diagnostics.ai?.connection?.status || "untested"}`,
      `Secret files found: ${diagnostics.project?.secret_file_count || 0}`,
    ]),
    "",
    "## Recent Activity",
    markdownList(state.log.slice(-12).map((entry) => {
      const label = [entry.title, entry.command, entry.status].filter(Boolean).join(" | ");
      return `${label}: ${safeText(entry.body).slice(0, 500)}`;
    })),
    "",
    "## Redacted Diagnostics",
    "```json",
    JSON.stringify(diagnostics, null, 2),
    "```",
  ].join("\n");
}

function handoffMarkdown() {
  const gate = currentGate();
  const artifacts = Array.isArray(state.artifacts?.artifacts) ? state.artifacts.artifacts : [];
  return [
    "# SignalOS Team Handoff",
    "",
    `Generated: ${new Date().toISOString()}`,
    `Project: ${state.workspace ? basename(state.workspace) : "No project selected"}`,
    `Workspace: ${state.workspace || "No project selected"}`,
    "",
    "## Current State",
    markdownList([
      `Wave: ${state.wave?.name || "No active wave"}`,
      `Phase: ${state.wave?.phase_name || "Unknown"}`,
      `Next gate: ${gate ? `G${gate.id} ${gate.name || ""}`.trim() : "Not loaded"}`,
      `AI: ${aiReady() ? "connected" : "not ready"}`,
      `Engine: ${state.engine.status || "unknown"}`,
    ]),
    "",
    "## Project Files",
    markdownList(artifacts.map((artifact) => `${artifact.exists ? "Found" : "Missing"} ${artifact.path}: ${artifact.detail}`)),
    "",
    "## Recent Notes",
    markdownList(state.brain.slice(0, 10).map((entry) => `${entry.type || "note"}: ${entry.text || ""}`)),
    "",
    "## Recent Activity",
    markdownList(state.log.slice(-12).map((entry) => `${entry.title}${entry.command ? ` (${entry.command})` : ""}: ${safeText(entry.body).slice(0, 500)}`)),
    "",
    "## Next Operating Rule",
    "Run `/signal-status`, verify the project files shown in Dashboard, and record evidence before signing the next gate.",
  ].join("\n");
}

async function exportWorkspaceFile(kind, filename, content, label) {
  if (!state.workspace) {
    toast("Choose a project first.");
    await chooseWorkspace();
    return null;
  }

  setBusy(true);
  try {
    const result = await ipc.project.exportFile(kind, filename, content);
    addLog(label, `Written to ${result.relative_path}.`, { status: "success" });
    await refreshProjectState(false);
    toast(`${label} exported.`);
    return result;
  } catch (error) {
    addLog(`${label} failed`, error.message || String(error), { status: "error" });
    toast(`${label} failed.`);
    return null;
  } finally {
    setBusy(false);
  }
}

async function exportIssueReport() {
  const filename = `issue-report-${timestampSlug()}.md`;
  await exportWorkspaceFile("issue-reports", filename, issueReportMarkdown(), "Issue report");
}

async function exportHandoffReport() {
  const filename = `team-handoff-${timestampSlug()}.md`;
  await exportWorkspaceFile("handoffs", filename, handoffMarkdown(), "Team handoff");
}

function copyAppDiagnostics() {
  copyText(JSON.stringify(diagnosticsPayload(), null, 2), "Diagnostics copied.");
}

async function checkForUpdates() {
  const update = await safeCall(() => ipc.updater.check(state.updateChannel), { available: false });
  if (update?.available) {
    toast(`Update available on ${update.channel || state.updateChannel}: ${update.version}`);
  } else if (update?.signatures_missing) {
    toast(`No ${state.updateChannel} update. Manifest signatures are not release-ready yet.`);
  } else if (update?.error) {
    toast(`Update check failed: ${update.error}`);
  } else {
    toast(`No ${state.updateChannel} update available.`);
  }
}

async function init() {
  bindEvents();
  render();
  await loadBasics();
  render();
  await refreshProjectState(false);
  setTimeout(() => testEngineStatus({ silent: true }), 1500);
}

init();
