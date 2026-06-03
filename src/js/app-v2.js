/**
 * app-v2.js — Main wiring layer for SignalOS v2 frontend.
 *
 * Connects all HTML elements in index.html to the real Tauri backend
 * via ipc.js. No mock data. Tauri runtime required.
 *
 * Architecture:
 *   - boot()              called on DOMContentLoaded
 *   - bootApp()           full app initialisation (post-onboarding)
 *   - bootListeners()     register Tauri event listeners
 *   - switchTab(tab)      navigate between views + load real data
 *   - sendMsg()           streaming chat → ipc.provider.chatStream
 *   - loadDashboard/Brain/Vault/History/Settings/Build()  per-view loaders
 */

import * as ipc from "./ipc.js";
import { isFinished as wizardFinished, resetWizard } from "./wizard.js";
import { activeBuildId, appendTurn, loadHistory as loadConvHistory } from "./conversation.js";
import { loadDashboard } from "./ui/dashboard.js";
import { loadBuild, addAIBubble, appendStreamToken, finaliseStream, showStreamError } from "./ui/chat.js";

// ─── Global state ──────────────────────────────────────────────────────────────

import { state } from "./state.js";

import { esc, errorMessage, isProviderAuthFailure, providerConnectionMessage, showError, showWarning } from "./util.js";

// ─── Boot sequence ─────────────────────────────────────────────────────────────

function workspaceNameFromPath(path) {
  return String(path || "").replace(/\\/g, "/").split("/").filter(Boolean).pop() || "";
}

function isStarterWorkspacePath(path) {
  const name = workspaceNameFromPath(path);
  return name === "SignalOS Workspace" || name === "Foundry Workspace";
}

document.addEventListener("DOMContentLoaded", () => {
  boot().catch((e) => showError("Boot failed: " + errorMessage(e)));
});

async function boot() {
  if (wizardFinished()) {
    // Skip onboarding — go straight to app
    state.onboardingVisible = false;
    state.appVisible = true;
    document.getElementById("onboarding").classList.remove("active");
    document.getElementById("app").classList.add("active");
    await bootApp();
  } else {
    // Show onboarding
    state.onboardingVisible = true;
    state.appVisible = false;
    document.getElementById("onboarding").classList.add("active");
    document.getElementById("app").classList.remove("active");
    initOnboarding();
  }
}

async function bootApp() {
  let savedWizard = {};
  try {
    savedWizard = JSON.parse(localStorage.getItem("signalos.onboarding.wizard.v1") || "{}");
    if (savedWizard?.projectsRoot) state.projectsRoot = savedWizard.projectsRoot;
    if (savedWizard?.identity?.name) state.userName = savedWizard.identity.name;
    if (savedWizard?.identity?.role) state.userRole = savedWizard.identity.role;
  } catch {}

  try {
    // Provider + cost
    const prov = await ipc.provider.getActive();
    if (prov) {
      state.ai = prov.provider || state.ai;
      state.aiModel = prov.model || state.aiModel;
      // Reactive state handles Titlebar updates natively.
    }
  } catch (e) {
    console.warn("Could not load provider:", errorMessage(e));
  }

  try {
    const cost = await ipc.provider.getCost();
    updateCostDisplay(cost);
  } catch (e) {
    console.warn("Could not load cost:", errorMessage(e));
  }

  try {
    // Workspace
    const ws = await ipc.workspace.get();
    if (ws) {
      const activeWorkspace = ws.path || ws || "";
      if (isStarterWorkspacePath(activeWorkspace)) {
        await ipc.workspace.clear().catch(() => null);
        state.workspace = "";
      } else {
        state.workspace = activeWorkspace;
      }
    }
    if (state.workspace) {
      const wsParts = state.workspace.replace(/\\/g, "/").split("/");
      const wsName = wsParts[wsParts.length - 1] || "Project";
      const crumbStrong = document.querySelector(".crumb strong");
      if (crumbStrong) crumbStrong.textContent = wsName;
    }
    applyWorkspaceStatus(await ipc.workspace.status().catch(() => null));
  } catch (e) {
    console.warn("Could not load workspace:", errorMessage(e));
  }

  try {
    // Identity is workspace-scoped when a product workspace is active; before
    // the first product exists, keep the onboarding identity from local state.
    if (state.workspace) {
      const id = await ipc.identity.get();
      if (id) {
        state.userName = id.name || state.userName || "";
        state.userRole = id.role || state.userRole || "";
      }
    }
    const signName = document.getElementById("signName");
    if (signName) signName.value = state.userName;
  } catch (e) {
    console.warn("Could not load identity:", errorMessage(e));
  }

  try {
    await ipc.workspace.startWatch();
  } catch (e) {
    console.warn("Could not start workspace watch:", errorMessage(e));
  }

  bootListeners();
  await switchTab("dashboard");
}

// ─── Tauri event listeners ─────────────────────────────────────────────────────

function bootListeners() {
  // Streaming chat tokens
  ipc.onChatToken(null, ({ stream_id, kind, delta }) => {
    if (kind === "delta") appendStreamToken(stream_id, delta);
    if (kind === "done") finaliseStream(stream_id);
    if (kind === "error") showStreamError(stream_id, delta);
  });

  // Workspace file changes → refresh file tree
  ipc.onWorkspaceChange(() => {
    refreshFileTree().catch(() => {});
  });

  // Sidecar errors
  const TAURI_EVENT = window.__TAURI__?.event;
  if (TAURI_EVENT?.listen) {
    TAURI_EVENT.listen("sidecar:error", (e) => showSidecarError(e.payload));
    TAURI_EVENT.listen("menu:nav", (e) => switchTab(e.payload));
    TAURI_EVENT.listen("menu:open-workspace", () => openNewProject());
    TAURI_EVENT.listen("menu:export-audit", () => exportHandoff(null));
    TAURI_EVENT.listen("menu:check-update", () => checkForUpdates());
  }
}

function showSidecarError(payload) {
  const msg = typeof payload === "string" ? payload : (payload?.error || "Sidecar error");
  showError("Engine error: " + msg);
}

// ─── Cost display ──────────────────────────────────────────────────────────────

export function updateCostDisplay(cost) {
  if (!cost) return;
  state.cost = cost.session_usd ?? cost.total_usd ?? 0;
}

function applyWorkspaceStatus(status) {
  if (!status) return;
  if (Array.isArray(status.recent_workspaces)) {
    state.recentWorkspaces = status.recent_workspaces;
  }
  const profileId = status.profile_id || "generic";
  state.selectedProductProfile = profileId;
  state.previewStack = profileId === "react-vite" ? "react-vite" : "";
}


// ─── Tab navigation ────────────────────────────────────────────────────────────

async function switchTab(tab) {
  state.tab = tab;
  // Update view visibility
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.dataset.view === tab)
  );
  // Update segment controls
  document.querySelectorAll(".seg-i[data-tab]").forEach((s) =>
    s.classList.toggle("active", s.dataset.tab === tab)
  );
  // Update sidebar nav items
  document.querySelectorAll(".nav[data-tab]").forEach((n) =>
    n.classList.toggle("active", n.dataset.tab === tab)
  );
  // Update breadcrumb view name
  const names = {
    build: "Build",
    preview: "Preview",
    dashboard: "Dashboard",
    vault: "Vault",
    settings: "Settings",
    help: "Help",
    history: "History",
    brain: "Brain",
  };
  const viewName = document.getElementById("viewName");
  if (viewName) viewName.textContent = names[tab] || tab;

  // Load view data
  try {
    const loaders = {
      dashboard: loadDashboard,
      build: loadBuild,
      brain: loadBrain,
      history: loadHistory,
      vault: loadVault,
      settings: loadSettings,
    };
    if (loaders[tab]) await loaders[tab]();
  } catch (e) {
      console.warn("Tab load error for", tab, errorMessage(e));
  }
}

// Make switchTab globally accessible (called from HTML onclick attrs)
window.switchTab = switchTab;

// ─── Sidebar tab switching ─────────────────────────────────────────────────────

function switchSbTab(tab) {
  state.sbTab = tab;
  document.querySelectorAll(".sb-tab").forEach((t, i) => {
    const tabs = ["projects", "files", "gov"];
    t.classList.toggle("active", tabs[i] === tab);
  });
  document.querySelectorAll(".sb-panel").forEach((p) => p.classList.remove("active"));
  const panel = document.getElementById("sb-" + tab);
  if (panel) panel.classList.add("active");

  if (tab === "files") refreshFileTree().catch(() => {});
  if (tab === "gov") loadGovPanel().catch(() => {});
}
window.switchSbTab = switchSbTab;

// ─── Dashboard ─────────────────────────────────────────────────────────────────


// ─── Build / Chat ──────────────────────────────────────────────────────────────


// ─── Enforcement ───────────────────────────────────────────────────────────────

export async function loadEnforcement() {
  try {
    const enfState = await ipc.enforcement.state();
    state.enforcementRules = enfState?.rules || [];
    state.waveFrozen = Boolean(enfState?.wave_frozen);
  } catch (e) {
    console.warn("Could not load enforcement state:", errorMessage(e));
  }
}

async function freezeWave() {
  try {
    await ipc.enforcement.freeze();
    state.waveFrozen = true;
    addAIBubble("Wave frozen. No AI file writes allowed until you unfreeze.");
    switchTab("build");
  } catch (e) {
    showError("Could not freeze wave: " + errorMessage(e));
  }
}
window.freezeWave = freezeWave;

async function unfreezeWave() {
  try {
    await ipc.enforcement.unfreeze();
    state.waveFrozen = false;
    addAIBubble("Wave unfrozen. Enforcement rules still active — proceed carefully.");
    switchTab("build");
  } catch (e) {
    showError("Could not unfreeze wave: " + errorMessage(e));
  }
}
window.unfreezeWave = unfreezeWave;

function toggleEnfPopover() {
  state.enfOpen = !state.enfOpen;
}
window.toggleEnfPopover = toggleEnfPopover;

// Close popover when clicking outside
document.addEventListener("click", (e) => {
  if (!e.target.closest(".enf-pill")) {
    state.enfOpen = false;
  }
});

async function openOverride() {
  state.enfOpen = false;
  openModal("overrideModal");
}
window.openOverride = openOverride;

async function confirmOverride() {
  const reasonInput = document.getElementById("overrideReason");
  const reason = reasonInput?.value.trim() || "";
  const ruleNameEl = document.getElementById("overrideRuleName");
  const rule = ruleNameEl?.textContent || "unknown";
  if (!reason) {
    showError("Please provide a reason for the override");
    return;
  }
  try {
    await ipc.enforcement.override(rule, reason, "manual-override");
    closeModal("overrideModal");
    addAIBubble("Override logged to audit trail. This is recorded permanently — proceed with care.");
    await loadEnforcement();
    switchTab("build");
  } catch (e) {
    showError("Override failed: " + errorMessage(e));
  }
}
window.confirmOverride = confirmOverride;

// ─── Gate operations ───────────────────────────────────────────────────────────

function currentGateId() {
  return state.currentGateId;
}

async function openGate() {
  const name = (document.getElementById("signName")?.value || "").trim() || state.userName;
  const roleEl = document.getElementById("signRole");
  const role = roleEl?.value || state.userRole;
  const gateId = currentGateId();

  if (!gateId) {
    showError("No active gate to sign");
    return;
  }
  if (!name) {
    document.getElementById("signName")?.focus();
    return;
  }

  try {
    const canSign = await ipc.identity.canSignGate(gateId);
    if (!canSign) {
      showError("Your role cannot sign this gate");
      return;
    }
    await ipc.gates.sign(gateId, { name, role });
    await ipc.brain.add(`Gate ${gateId} signed by ${name} (${role})`, "Decision");

    // Update UI to reflect signed gate
    state.gateOpen = true;
    document.getElementById("signForm").style.display = "none";

    const cells = document.querySelectorAll(".scell");
    const activeIdx = [...cells].findIndex((c) => c.classList.contains("active"));
    if (activeIdx >= 0) {
      cells[activeIdx].classList.remove("active");
      cells[activeIdx].classList.add("done");
      const scirc = cells[activeIdx].querySelector(".scirc");
      if (scirc) scirc.innerHTML = '<i class="ti ti-check"></i>';
      const sstatus = cells[activeIdx].querySelector(".sstatus");
      if (sstatus) sstatus.textContent = "Signed";
      if (cells[activeIdx + 1]) {
        cells[activeIdx + 1].classList.remove("locked");
        cells[activeIdx + 1].classList.add("active");
        const nextScirc = cells[activeIdx + 1].querySelector(".scirc");
        if (nextScirc) nextScirc.textContent = String(activeIdx + 2);
        const nextStatus = cells[activeIdx + 1].querySelector(".sstatus");
        if (nextStatus) nextStatus.textContent = "Current";
      }
    }

    const gateBadge = document.getElementById("gateBadge");
    if (gateBadge) {
      gateBadge.className = "gate-badge passed";
      gateBadge.innerHTML = '<i class="ti ti-check"></i> Signed';
    }

    const verdict = document.getElementById("verdict");
    if (verdict) {
      verdict.className = "verdict opened";
      const vic = verdict.querySelector(".verdict-ic");
      if (vic) vic.innerHTML = '<i class="ti ti-circle-check"></i>';
      const vtx = document.getElementById("verdictTx");
      if (vtx) vtx.textContent = `Gate signed by ${name} · ${role}. Next gate is now active.`;
      const openBtn = document.getElementById("openBtn");
      if (openBtn) openBtn.style.display = "none";
    }

    document.getElementById("gateCard")?.classList.add("gate-locked");
    document.getElementById("heroSub").textContent = "Gate signed. Next gate is active.";

    // Update gov sidebar
    await loadGovPanel().catch(() => {});
    await loadDashboard();
  } catch (e) {
    showError("Gate sign failed: " + errorMessage(e));
  }
}
window.openGate = openGate;

function showSignForm() {
  state.signFormOpen = !state.signFormOpen;
}
window.showSignForm = showSignForm;

function cycleActivity(el) {
  if (state.gateOpen) return;
  const order = ["pending", "ongoing", "done"];
  const cur = order.find((s) => el.classList.contains(s)) || "pending";
  const next = order[(order.indexOf(cur) + 1) % 3];
  el.classList.remove("pending", "ongoing", "done");
  el.classList.add(next);
  const ic = el.querySelector(".act-ic"), pill = el.querySelector(".act-pill");
  if (next === "done") {
    if (ic) ic.innerHTML = '<i class="ti ti-check"></i>';
    if (pill) pill.innerHTML = "Done";
  } else if (next === "ongoing") {
    if (ic) ic.innerHTML = '<i class="ti ti-loader-2"></i>';
    if (pill) pill.innerHTML = '<span class="pdot"></span>Ongoing';
  } else {
    if (ic) ic.innerHTML = "";
    if (pill) pill.innerHTML = "Pending";
  }
  updateGateVerdictDisplay();
}
window.cycleActivity = cycleActivity;

function runCheck(el) {
  if (state.gateOpen) return;
  if (el._timer) clearTimeout(el._timer);
  el.classList.remove("passed", "failed", "waiting");
  el.classList.add("checking");
  const ic = el.querySelector(".crit-ic"), pill = el.querySelector(".crit-pill");
  if (ic) ic.innerHTML = '<i class="ti ti-loader-2"></i>';
  if (pill) pill.innerHTML = '<span class="pdot"></span>Checking';
  updateGateVerdictDisplay();
  el._timer = setTimeout(() => {
    el._timer = null;
    if (state.gateOpen) return;
    el.classList.remove("checking");
    el.classList.add("passed");
    if (ic) ic.innerHTML = '<i class="ti ti-shield-check"></i>';
    if (pill) pill.innerHTML = "Passed";
    updateGateVerdictDisplay();
  }, 900);
}
window.runCheck = runCheck;

// ─── Gov sidebar panel ─────────────────────────────────────────────────────────

async function loadGovPanel() {
  try {
    const [gatesData, auditData] = await Promise.allSettled([
      ipc.gates.getAll(),
      ipc.audit.list(10),
    ]);

    // Wave label
    const gateList = gatesData.status === "fulfilled" ? (Array.isArray(gatesData.value) ? gatesData.value : (gatesData.value?.gates || [])) : [];
    state.govGates = gateList;

    if (auditData.status === "fulfilled" && auditData.value) {
      state.auditTrail = auditData.value.slice(0, 6);
    }
  } catch (e) {
    console.warn("Gov panel load error:", errorMessage(e));
  }
}

// ─── Brain ─────────────────────────────────────────────────────────────────────

async function loadBrain() {
  try {
    const entries = await ipc.brain.search("");
    state.brainEntries = entries || [];
  } catch (e) {
    console.warn("Brain load error:", errorMessage(e));
    showError("Could not load Brain: " + errorMessage(e));
  }
}

async function addBrainEntry() {
  const text = document.getElementById("brainText")?.value.trim();
  const type = document.getElementById("brainTypeSelect")?.value || "Note";
  if (!text) {
    // Switch to build and prompt user
    addAIBubble(
      "Use /signal-brain in the Build tab to add a note, or I can add one for you — what do you want to remember?"
    );
    switchTab("build");
    return;
  }
  try {
    await ipc.brain.add(text, type);
    await loadBrain();
  } catch (e) {
    showError("Could not add brain entry: " + errorMessage(e));
  }
}
window.addBrainEntry = addBrainEntry;

function filterBrain(_el, type) {
  state.brainFilter = type;
  ipc.brain
    .search(type === "all" ? "" : type)
    .then((entries) => {
      const filtered = type === "all" ? entries : entries.filter((e) => (e.entry_type || e.type || "").toLowerCase() === type);
      state.brainEntries = filtered || [];
    })
    .catch((e) => console.warn("Brain filter error:", errorMessage(e)));
}
window.filterBrain = filterBrain;

// ─── Vault ─────────────────────────────────────────────────────────────────────

async function loadVault() {
  try {
    const list = await ipc.secrets.list();
    state.secrets = list || [];
  } catch (e) {
    console.warn("Vault load error:", errorMessage(e));
    showError("Could not load Vault: " + errorMessage(e));
  }
}

async function toggleSecret(name) {
  if (!name) return;
  const current = state.revealedSecrets || {};
  if (name in current) {
    // Hide
    const { [name]: _omit, ...rest } = current;
    state.revealedSecrets = rest;
    return;
  }
  try {
    const raw = await ipc.secrets.reveal(name);
    state.revealedSecrets = { ...current, [name]: raw };
    // Auto-hide after 30 seconds
    setTimeout(() => {
      const cur = state.revealedSecrets || {};
      if (name in cur) {
        const { [name]: _o, ...rest } = cur;
        state.revealedSecrets = rest;
      }
    }, 30000);
  } catch (e) {
    showError("Could not reveal secret: " + errorMessage(e));
  }
}
window.toggleSecret = toggleSecret;

async function copySecret(name) {
  if (!name) return;
  try {
    const raw = await ipc.secrets.reveal(name);
    await navigator.clipboard.writeText(raw);
    state.copiedSecret = name;
    setTimeout(() => {
      if (state.copiedSecret === name) state.copiedSecret = null;
    }, 1500);
  } catch (e) {
    showError("Could not copy secret: " + errorMessage(e));
  }
}
window.copySecret = copySecret;

async function deleteSecret(name) {
  if (!name) return;
  if (!confirm("Delete secret " + name + "? This cannot be undone.")) return;
  try {
    await ipc.secrets.delete(name);
    await loadVault();
  } catch (e) {
    showError("Could not delete secret: " + errorMessage(e));
  }
}
window.deleteSecret = deleteSecret;

function openAddSecret() {
  openModal("addSecretModal");
}
window.openAddSecret = openAddSecret;

function openBulkImport() {
  // Delegates to VaultView's signal-driven modal (state.bulkImportOpen)
  state.bulkImportOpen = true;
}
window.openBulkImport = openBulkImport;

async function saveSecret() {
  const name = document.getElementById("newSecretName")?.value.trim();
  const valueInput = document.getElementById("newSecretValue");
  const value = valueInput?.value.trim();
  const fileSelect = document.getElementById("newSecretFile");
  const filename = fileSelect?.value || ".env.local";

  if (!name) {
    showError("Secret name is required");
    return;
  }
  if (!value) {
    showError("Secret value is required");
    return;
  }

  try {
    await ipc.secrets.upsert(name, value, filename);
    closeModal("addSecretModal");
    if (document.getElementById("newSecretName")) document.getElementById("newSecretName").value = "";
    if (valueInput) valueInput.value = "";
    await loadVault();
  } catch (e) {
    showError("Could not save secret: " + errorMessage(e));
  }
}
window.saveSecret = saveSecret;

// ─── History ───────────────────────────────────────────────────────────────────

async function loadHistory() {
  try {
    const [entries, cost] = await Promise.all([
      ipc.audit.list(100),
      ipc.provider.getCost(),
    ]);
    state.auditTrail = entries || [];
    if (cost) updateCostDisplay(cost);
  } catch (e) {
    console.warn("History load error:", errorMessage(e));
    showError("Could not load History: " + errorMessage(e));
  }
}

async function exportHandoff(btn) {
  const origHTML = btn?.innerHTML;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="ti ti-loader-2" style="animation:spin 1s linear infinite"></i> Exporting…';
  }
  try {
    const entries = await ipc.audit.list(500);
    const content = entries.map((e) => [e.ts || "", e.action || ""].join("\t")).join("\n");
    await ipc.project.exportFile("handoffs", `handoff-${Date.now()}.md`, content);
    if (btn) {
      btn.innerHTML = '<i class="ti ti-circle-check" style="color:var(--success)"></i> Exported';
      setTimeout(() => {
        if (btn.parentElement) {
          btn.innerHTML = origHTML;
          btn.disabled = false;
        }
      }, 2000);
    }
  } catch (e) {
    showError("Export failed: " + errorMessage(e));
    if (btn) {
      btn.innerHTML = origHTML;
      btn.disabled = false;
    }
  }
}
window.exportHandoff = exportHandoff;

async function exportReport(btn) {
  const origHTML = btn?.innerHTML;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="ti ti-loader-2" style="animation:spin 1s linear infinite"></i> Generating…';
  }
  try {
    const [entries, brainEntries] = await Promise.all([
      ipc.audit.list(200),
      ipc.brain.search(""),
    ]);
    const content = [
      "# Issue Report",
      "",
      "## Audit Trail",
      entries.map((e) => `- ${e.ts || ""}: ${e.action || ""}`).join("\n"),
      "",
      "## Brain Notes",
      brainEntries.map((e) => `- [${e.entry_type || "Note"}] ${e.title || e.text || ""}`).join("\n"),
    ].join("\n");
    await ipc.project.exportFile("reports", `report-${Date.now()}.md`, content);
    if (btn) {
      btn.innerHTML = '<i class="ti ti-circle-check" style="color:var(--success)"></i> Saved';
      setTimeout(() => {
        if (btn.parentElement) {
          btn.innerHTML = origHTML;
          btn.disabled = false;
        }
      }, 2000);
    }
  } catch (e) {
    showError("Report failed: " + errorMessage(e));
    if (btn) {
      btn.innerHTML = origHTML;
      btn.disabled = false;
    }
  }
}
window.exportReport = exportReport;

// ─── Settings ──────────────────────────────────────────────────────────────────

async function loadSettings() {
  try {
    const [id, prov, cost] = await Promise.all([
      ipc.identity.get(),
      ipc.provider.getActive(),
      ipc.provider.getCost(),
    ]);

    if (id) {
      state.userName = id.name || "";
      state.userRole = id.role || "PO";
    }

    if (prov) {
      state.ai = prov.provider || "anthropic";
      state.aiModel = prov.model || "";
    }

    if (cost) {
      state.cost = cost.session_usd ?? cost.total_usd ?? 0;
      if (cost.budget_usd) state.monthlyCap = cost.budget_usd;
    }

    const ws = await ipc.workspace.get().catch(() => null);
    if (ws) state.workspacePath = ws.path || ws;
    applyWorkspaceStatus(await ipc.workspace.status().catch(() => null));

    try {
      const engineStatus = await ipc.engine.status();
      state.engineRunning = Boolean(engineStatus?.running ?? engineStatus?.status === "running");
    } catch {
      // Engine status not critical
    }
  } catch (e) {
    console.warn("Settings load error:", errorMessage(e));
  }
}

async function saveIdentity() {
  const name = (state.userName || "").trim();
  const role = state.userRole;
  if (!name) { showError("Name is required"); return; }
  try {
    await ipc.identity.set(name, role);
  } catch (e) {
    showError("Could not save identity: " + errorMessage(e));
  }
}
window.saveIdentity = saveIdentity;

async function saveBudget() {
  const val = state.monthlyCap;
  if (val === null || isNaN(val) || val < 0) { showError("Invalid budget amount"); return; }
  try {
    await ipc.provider.setBudget(val);
  } catch (e) {
    showError("Could not save budget: " + errorMessage(e));
  }
}
window.saveBudget = saveBudget;

async function resetSessionCost() {
  try {
    await ipc.provider.resetSession();
    const cost = await ipc.provider.getCost();
    updateCostDisplay(cost);
  } catch (e) {
    showError("Could not reset session: " + errorMessage(e));
  }
}
window.resetSessionCost = resetSessionCost;

async function changeProvider() {
  const p = state.ai;
  if (!p) return;
  state.providerModels = [];
  state.providerModelsError = null;
  try {
    await ipc.provider.setActive(p);
    const models = await ipc.provider.fetchModels(p, null);
    if (Array.isArray(models) && models.length > 0) {
      state.providerModels = models;
      if (!models.some((model) => model.id === state.aiModel)) {
        state.aiModel = models[0].id;
        await ipc.provider.setModel(p, state.aiModel);
      }
    } else {
      state.providerModelsError = `${p} returned no models. Refresh again or replace the key.`;
    }
  } catch (e) {
    if (isProviderAuthFailure(e)) {
      try { await ipc.keychain.delete(p); } catch {}
    }
    state.providerModels = [];
    state.providerModelsError = providerConnectionMessage(e, p);
  }
}
window.changeProvider = changeProvider;

async function changeModel() {
  const model = state.aiModel;
  if (!model) return;
  try {
    await ipc.provider.setModel(state.ai, model);
  } catch (e) {
    showError("Could not change model: " + errorMessage(e));
  }
}
window.changeModel = changeModel;

async function refreshCurrentProviderModels(apiKey) {
  state.providerModelsError = null;
  try {
    const models = await ipc.provider.fetchModels(state.ai, apiKey || null);
    if (!Array.isArray(models) || models.length === 0) {
      throw new Error(`No models were returned for ${state.ai}.`);
    }
    state.providerModels = models;
    if (!models.some((model) => model.id === state.aiModel)) {
      state.aiModel = models[0].id;
    }
    return models;
  } catch (e) {
    state.providerModels = [];
    state.providerModelsError = providerConnectionMessage(e, state.ai);
    throw e;
  }
}

async function replaceApiKey() {
  const key = prompt("Enter new API key:");
  if (!key) return;
  try {
    await ipc.keychain.store(state.ai, key);
    await refreshCurrentProviderModels(key);
    await ipc.provider.setModel(state.ai, state.aiModel);
    const result = await ipc.provider.test(state.ai, key, state.aiModel);
    if (result?.ok || result === true) {
      try { await ipc.sidecar.restart(); } catch {}
      addAIBubble("API key saved, models fetched, and provider verified.");
    }
  } catch (e) {
    if (isProviderAuthFailure(e)) {
      try { await ipc.keychain.delete(state.ai); } catch {}
      showError("Could not update API key: " + providerConnectionMessage(e, state.ai));
    } else {
      showWarning("API key saved, but models were not fetched: " + providerConnectionMessage(e, state.ai));
    }
  }
}
window.replaceApiKey = replaceApiKey;

async function switchWorkspace(path) {
  const target = String(path || "").trim();
  if (!target) return;
  try {
    await ipc.workspace.set(target);
    state.workspace = target;
    applyWorkspaceStatus(await ipc.workspace.status().catch(() => null));
    await bootApp();
  } catch (e) {
    showError("Could not switch workspace: " + errorMessage(e));
  }
}
window.switchWorkspace = switchWorkspace;

async function forgetWorkspace() {
  if (!confirm("Remove this workspace from Foundry? Your files stay on your computer.")) return;
  try {
    await ipc.workspace.clear();
    state.workspace = "";
    applyWorkspaceStatus(await ipc.workspace.status().catch(() => null));
  } catch (e) {
    showError("Could not forget workspace: " + errorMessage(e));
  }
}
window.forgetWorkspace = forgetWorkspace;

async function testEngine() {
  state.engineTestState = "testing";
  try {
    await ipc.engine.ping();
    state.engineTestState = "ok";
    setTimeout(() => { state.engineTestState = "idle"; }, 2000);
  } catch (e) {
    state.engineTestState = "failed";
    setTimeout(() => { state.engineTestState = "idle"; }, 2000);
    showError("Engine test failed: " + errorMessage(e));
  }
}
window.testEngine = testEngine;

async function restartEngine() {
  state.engineRestartState = "restarting";
  try {
    await ipc.engine.restart();
    state.engineRestartState = "idle";
    state.engineRunning = true;
  } catch (e) {
    state.engineRestartState = "idle";
    showError("Engine restart failed: " + errorMessage(e));
  }
}
window.restartEngine = restartEngine;

async function checkForUpdates() {
  state.updateCheck = { checking: true, visible: false, hasUpdate: false, message: "" };
  try {
    const update = await ipc.updater.check(state.updateChannel);
    const hasUpdate = update?.available || update?.update_available;
    state.updateCheck = {
      checking: false,
      visible: true,
      hasUpdate,
      message: hasUpdate ? "Update available: " + (update.version || "") : "Up to date",
    };
  } catch (e) {
    state.updateCheck = { checking: false, visible: true, hasUpdate: false, message: "Check failed" };
  }
}
window.checkForUpdates = checkForUpdates;

// ─── File tree ─────────────────────────────────────────────────────────────────

async function refreshFileTree() {
  try {
    const entries = await ipc.project.listDir(".");
    renderFileTree(entries || []);
  } catch (e) {
    console.warn("File tree load error:", errorMessage(e));
  }
}

function renderFileTree(entries) {
  const container = document.querySelector(".ftree");
  if (!container) return;
  container.innerHTML = "";

  function renderEntries(items, depth) {
    (items || []).forEach((entry) => {
      const item = document.createElement("div");
      const isDir = entry.is_dir || entry.kind === "dir";
      item.className = "ftree-item" + (isDir ? " dir" : "") + (depth > 0 ? " child" : "");
      const icon = isDir ? (entry.children?.length ? "ti-folder-open" : "ti-folder") : "ti-file-code";
      item.innerHTML = `<i class="ti ${icon}"></i> ${esc(entry.name || "")}`;
      if (!isDir) {
        item.style.cursor = "pointer";
        item.addEventListener("click", () => openFile(entry.path || entry.name));
      }
      container.appendChild(item);
      if (isDir && entry.children) {
        renderEntries(entry.children, depth + 1);
      }
    });
  }

  renderEntries(entries, 0);
}

async function openFile(path) {
  try {
    const content = await ipc.project.readFile(path);
    showFileViewer(path, content);
  } catch (e) {
    showError("Could not open file: " + errorMessage(e));
  }
}
window.openFile = openFile;

function showFileViewer(path, content) {
  // The Build conversation is the single surface (v4), so render an opened
  // file as a code block in the chat instead of sending users to another view.
  switchTab("build");
  try {
    const id = (typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID() : String(Date.now()) + Math.random();
    const body = (content || "").split("\n").slice(0, 400).join("\n");
    const md = "**" + path + "**\n\n```\n" + body + "\n```";
    state.chatBubbles = [...(state.chatBubbles || []), { id, kind: "ai", text: md, ts: "file" }];
  } catch (e) {
    showError("Could not display file: " + errorMessage(e));
  }
}

// ─── File write toast ──────────────────────────────────────────────────────────

function showFileWriteToast(files) {
  const existing = document.getElementById("fileToast");
  if (existing) existing.remove();
  const names = files.slice(0, 3).join(", ") + (files.length > 3 ? ` +${files.length - 3} more` : "");
  const toast = document.createElement("div");
  toast.className = "file-toast";
  toast.id = "fileToast";
  toast.innerHTML = `<i class="ti ti-files file-toast-ic"></i>
    <div class="file-toast-tx">
      <strong>${files.length} file${files.length > 1 ? "s" : ""} written</strong>
      <span>${esc(names)}</span>
    </div>
    <div class="file-toast-close"><i class="ti ti-x"></i></div>`;
  toast.querySelector(".file-toast-close").addEventListener("click", () => toast.remove());
  document.body.appendChild(toast);
  setTimeout(() => { if (toast.parentElement) toast.remove(); }, 5000);
}
window.showFileWriteToast = showFileWriteToast;

// ─── Modals ────────────────────────────────────────────────────────────────────

function openModal(id) {
  state.modalOpen = id;
}
window.openModal = openModal;

function closeModal(id) {
  if (state.modalOpen === id) state.modalOpen = null;
}
window.closeModal = closeModal;

function openNewProject() {
  openModal("newProjectModal");
}
window.openNewProject = openNewProject;

function closeNewProject(e) {
  if (e.target === e.currentTarget) closeModal("newProjectModal");
}
window.closeNewProject = closeNewProject;

let creatingProject = false;

function setNewProjectStatus(message) {
  const status = document.getElementById("newProjStatus");
  if (!status) return;
  status.textContent = message || "";
}

function setCreateProjectBusy(busy) {
  const btn = document.getElementById("createProjectBtn");
  if (!btn) return;
  btn.disabled = Boolean(busy);
}

function safeProjectFolderName(name) {
  return String(name || "Project")
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "-")
    .replace(/[. ]+$/g, "")
    || "Project";
}

function joinPath(root, child) {
  const sep = String(root || "").includes("\\") ? "\\" : "/";
  return `${String(root).replace(/[\\/]+$/g, "")}${sep}${child}`;
}

async function createProject() {
  if (creatingProject) return;
  const name = document.getElementById("newProjName")?.value.trim();
  let path = document.getElementById("newProjPath")?.value.trim();
  const profile = document.getElementById("newProjProfile")?.value || state.selectedProductProfile || "generic";
  if (!name) { showError("Project name is required"); return; }
  if (!path && state.projectsRoot) {
    path = joinPath(state.projectsRoot, safeProjectFolderName(name));
  }
  if (!path) { showError("Choose a projects root in onboarding or enter a folder path"); return; }

  if (typeof window.createSignalosProject !== "function") {
    showError("Project setup is not ready. Reload Foundry and try again.");
    return;
  }

  creatingProject = true;
  setCreateProjectBusy(true);
  setNewProjectStatus("Creating product repo...");

  try {
    state.selectedProductProfile = profile;
    state.previewStack = profile === "react-vite" ? "react-vite" : "";
    const result = await window.createSignalosProject(path, name, profile);
    state.workspace = path;
    state.previewStack = profile === "react-vite" ? "react-vite" : "";
    setNewProjectStatus("Refreshing workspace status...");
    applyWorkspaceStatus(await ipc.workspace.status().catch(() => null));
    closeModal("newProjectModal");
    await bootApp();
    if (result?.governance && !result.governance.signed) {
      showError("Project was created, but Gate 0 was not signed automatically. Check governance status before building.");
    }
  } catch (e) {
    const message = errorMessage(e);
    setNewProjectStatus("Could not create project: " + message);
    showError("Could not create project: " + message);
  } finally {
    creatingProject = false;
    setCreateProjectBusy(false);
  }
}
window.createProject = createProject;

function closeAddSecret(e) {
  if (e.target === e.currentTarget) closeModal("addSecretModal");
}
window.closeAddSecret = closeAddSecret;

// ─── Exit ──────────────────────────────────────────────────────────────────────

function openExit() {
  openModal("exitModal");
}
window.openExit = openExit;

async function exitApp(save) {
  const status = document.getElementById("exitStatus");
  const statusTx = document.getElementById("exitStatusTx");
  const saveBtn = document.getElementById("exitSaveBtn");
  const rawBtn = document.getElementById("exitRawBtn");
  const cancelBtn = document.getElementById("exitCancelBtn");

  if (save) {
    if (saveBtn) { saveBtn.disabled = true; saveBtn.innerHTML = '<i class="ti ti-loader-2" style="animation:spin 1s linear infinite"></i> Saving…'; }
    if (rawBtn) rawBtn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;
    if (status) status.style.display = "flex";

    setTimeout(() => { if (statusTx) statusTx.textContent = "Audit trail sealed."; }, 600);
    setTimeout(() => { if (statusTx) statusTx.textContent = "Brain notes flushed."; }, 1100);
    setTimeout(() => { if (statusTx) statusTx.textContent = "All saved. Closing…"; }, 1600);
    setTimeout(() => _doExit(), 2100);

    // Actually flush — list audit to trigger any pending writes
    try {
      await ipc.audit.list(1);
    } catch {}
  } else {
    _doExit();
  }
}
window.exitApp = exitApp;

function _doExit() {
  const win = document.querySelector(".window");
  if (win) {
    win.style.transition = "opacity 0.35s ease, transform 0.35s ease";
    win.style.opacity = "0";
    win.style.transform = "scale(0.97)";
  }
  // Tauri 2 renamed getCurrent() to getCurrentWindow(); the old name was
  // removed in 2.0 and silently returned undefined here, so the OS window
  // never actually closed — the JS hid .window via display:none and left
  // the user staring at the body's cream gradient with no controls.
  setTimeout(() => {
    const tauri = window.__TAURI__?.window;
    const handle = tauri?.getCurrentWindow?.() ?? tauri?.getCurrent?.();
    if (handle?.close) {
      handle.close();
    } else if (win) {
      // Last resort — at least restore visibility so the user can Alt+F4.
      win.style.opacity = "";
      win.style.transform = "";
      showError("Could not close the window. Press Alt+F4 to exit.");
    }
  }, 380);
}

// ─── Onboarding ────────────────────────────────────────────────────────────────

function initOnboarding() {
  state.obStep = 1;
}

function nextStep() {
  if (state.obStep >= 3) return;
  state.obStep = state.obStep + 1;
}
window.nextStep = nextStep;

function prevStep() {
  if (state.obStep <= 1) return;
  state.obStep = state.obStep - 1;
}
window.prevStep = prevStep;

function selectProv(provider, model, label) {
  state.ai = provider || "anthropic";
  state.aiModel = model || "";
  state.keyLabel = label || "API key";
}
window.selectProv = selectProv;
window.selectAI = selectProv;

function toggleMoreProvs() {
  state.provMoreOpen = !state.provMoreOpen;
}
window.toggleMoreProvs = toggleMoreProvs;

function toggleKey() {
  state.keyVisible = !state.keyVisible;
}
window.toggleKey = toggleKey;

async function finishOnboarding() {
  const name = (state.userName || "").trim() || "User";
  const role = state.userRole || "PO";
  const apiKey = (state.apiKeyInput || "").trim();
  const budget = parseFloat(state.budgetInputValue) || 0;
  const projectsRoot = (state.projectsRoot || "").trim();
  let providerReady = false;
  let providerWarning = "";

  try {
    // Onboarding chooses the root folder where Foundry will place product
    // repos. Product workspaces are created later by Deliver/New Project.
    if (!projectsRoot) {
      throw new Error("Choose a projects root folder.");
    }
    // The user picks a path in onboarding but Browse only returns existing
    // dirs and typed paths aren't validated. mkdir-recursive here so the
    // chosen root reliably exists on disk for the first New Project.
    try {
      const fsApi = window.__TAURI__?.fs;
      if (fsApi?.mkdir) await fsApi.mkdir(projectsRoot, { recursive: true });
    } catch {
      // If mkdir fails, surface it via New Project later — don't block setup.
    }
    try { await ipc.workspace.clear(); } catch {}
    state.workspace = "";
    state.userName = name;
    state.userRole = role;

    if (apiKey) {
      try {
        await ipc.keychain.store(state.ai, apiKey);
        await refreshCurrentProviderModels(apiKey);
        await ipc.provider.test(state.ai, apiKey, state.aiModel);
        // Restart sidecar so it picks up the newly-stored key from keychain.
        // Without this, the sidecar (spawned at app launch before onboarding)
        // runs without the API key in its environment.
        try { await ipc.sidecar.restart(); } catch {}
        providerReady = true;
      } catch (e) {
        providerWarning = providerConnectionMessage(e, state.ai);
        if (isProviderAuthFailure(e)) {
          try { await ipc.keychain.delete(state.ai); } catch {}
        }
      }
    } else if (state.ai === "ollama") {
      try {
        await refreshCurrentProviderModels(null);
        await ipc.provider.test(state.ai, null, state.aiModel);
        providerReady = true;
      } catch (e) {
        providerWarning = providerConnectionMessage(e, "Ollama");
      }
    }

    try {
      await ipc.provider.setActive(state.ai);
      if (state.aiModel) await ipc.provider.setModel(state.ai, state.aiModel);
    } catch (e) {
      providerWarning = providerWarning || `Provider preferences were not saved: ${errorMessage(e)}. You can set them again in Settings.`;
    }

    if (budget > 0) {
      await ipc.provider.setBudget(budget);
      state.monthlyCap = budget;
    }

    const LS_KEY = "signalos.onboarding.wizard.v1";
    const WIZARD_VERSION = 2;
    localStorage.setItem(LS_KEY, JSON.stringify({
      version: WIZARD_VERSION,
      completedSteps: ["welcome", "projects-root", "identity", "ai", "budget", "done"],
      current: 6,
      folder: "",
      projectsRoot,
      identity: { name, role },
      initMode: "keep",
      ai: { provider: state.ai, model: state.aiModel, tested: providerReady },
      budgetUsd: budget,
      privacy: { redactEnv: true, blockSecretFiles: true, localOnly: false },
      finishedAt: new Date().toISOString(),
    }));

    state.onboardingVisible = false;
    state.appVisible = true;
    document.getElementById("onboarding").classList.remove("active");
    document.getElementById("app").classList.add("active");

    await bootApp();
    if (providerWarning) {
      showWarning(providerWarning);
    }
  } catch (e) {
    showError("Setup failed: " + errorMessage(e));
  }
}
window.finishOnboarding = finishOnboarding;

// ─── Preview ───────────────────────────────────────────────────────────────────

function switchDevice(mode) {
  state.previewDevice = mode;
}
window.switchDevice = switchDevice;

async function refreshPreview() {
  try {
    const previews = await ipc.preview.list();
    if (previews && previews.length > 0) {
      const p = previews[0];
      state.previewUrl = p.url || p.address || "";
    }
  } catch {
    // Preview may not be running
  }
}
window.refreshPreview = refreshPreview;

function openExternal() {
  const url = state.previewUrl;
  if (url && window.__TAURI__) {
    window.__TAURI__?.shell?.open(url).catch(() => {});
  }
}
window.openExternal = openExternal;

// ─── Misc UI ───────────────────────────────────────────────────────────────────

function openSearch() {
  const t = document.getElementById("chatInput");
  if (t) {
    switchTab("build");
    setTimeout(() => t.focus(), 100);
  }
}
window.openSearch = openSearch;

function showNotifications() {
  addAIBubble("No unread notifications right now.");
  switchTab("build");
}
window.showNotifications = showNotifications;

function shareProject() {
  addAIBubble("Sharing exports a read-only build report. Run /signal-ship to generate the full handoff package.");
  switchTab("build");
}
window.shareProject = shareProject;

// Note: `window.attachFile` is now owned by chat.js's `attachExternalDoc()`
// (WAVE-ENGINE-DESIGN §7 translator-mode). The old stub here just emitted a
// canned chat message; the engine wiring is the right home for it.

function voiceInput() {
  addUserBubble("[Voice input]");
  showError("Voice input requires microphone access — not available in this version.");
}
window.voiceInput = voiceInput;

function changeStack() {
  const next = state.selectedProductProfile || "generic";
  state.previewStack = next === "react-vite" ? "react-vite" : "";
}
window.changeStack = changeStack;


// ─── Traffic light window controls ────────────────────────────────────────────
// Wired after DOM ready. decorations:false means we own these buttons.

document.addEventListener("DOMContentLoaded", () => {
  const win = window.__TAURI__?.window?.getCurrentWindow?.()
            ?? window.__TAURI__?.window?.getCurrent?.();
  if (!win) return; // running in browser dev — no-op

  document.querySelector(".tl-red")?.addEventListener("click", (e) => {
    e.stopPropagation(); // don't trigger drag region
    openExit();          // our save-and-exit modal
  });
  document.querySelector(".tl-yellow")?.addEventListener("click", (e) => {
    e.stopPropagation();
    win.minimize();
  });
  document.querySelector(".tl-green")?.addEventListener("click", (e) => {
    e.stopPropagation();
    win.toggleMaximize();
  });
});
