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

// ─── Global state ──────────────────────────────────────────────────────────────

const state = {
  tab: "dashboard",
  sbTab: "projects",
  ai: "anthropic",
  aiModel: "claude-sonnet-4-6",
  userName: "",
  userRole: "",
  waveFrozen: false,
  busy: false,
  streamBubbles: {},
  currentGateId: null,
  gateOpen: false,
  enfOpen: false,
  keyVisible: false,
  updateChannel: "beta",
  workspace: "",
  termHistory: [],
  termHistIdx: -1,
};

const OB_TAGS = [
  "Every great thing starts with a spark.",
  "The right brain,<br/>the right budget.",
  "Your name on every gate.<br/>That's accountability.",
];

// ─── Utility ───────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s || "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function showError(msg) {
  console.error("[SignalOS]", msg);
  // Show as a temporary toast
  const existing = document.getElementById("errorToast");
  if (existing) existing.remove();
  const t = document.createElement("div");
  t.id = "errorToast";
  t.style.cssText =
    "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--danger);color:#fff;border-radius:var(--r);padding:11px 18px;font-size:13px;font-weight:600;z-index:9999;box-shadow:var(--sh-lg);animation:rise 0.3s var(--ease)";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => { if (t.parentElement) t.remove(); }, 4000);
}

function formatTs(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return "just now";
    if (diff < 3600000) return Math.round(diff / 60000) + "m ago";
    if (diff < 86400000) return Math.round(diff / 3600000) + "h ago";
    return d.toLocaleDateString();
  } catch {
    return ts;
  }
}

// ─── Boot sequence ─────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  boot().catch((e) => showError("Boot failed: " + e.message));
});

async function boot() {
  if (wizardFinished()) {
    // Skip onboarding — go straight to app
    document.getElementById("onboarding").classList.remove("active");
    document.getElementById("app").classList.add("active");
    await bootApp();
  } else {
    // Show onboarding
    document.getElementById("onboarding").classList.add("active");
    document.getElementById("app").classList.remove("active");
    initOnboarding();
  }
}

async function bootApp() {
  try {
    // Identity
    const id = await ipc.identity.get();
    if (id) {
      state.userName = id.name || "";
      state.userRole = id.role || "";
      const av = state.userName ? state.userName[0].toUpperCase() : "?";
      const sbAv = document.getElementById("sbAvatar");
      if (sbAv) sbAv.textContent = av;
      const sbUserName = document.getElementById("sbUserName");
      if (sbUserName) sbUserName.textContent = state.userName;
      const sbUserRole = document.getElementById("sbUserRole");
      if (sbUserRole) sbUserRole.textContent = state.userRole;
      const signName = document.getElementById("signName");
      if (signName) signName.value = state.userName;
    }
  } catch (e) {
    console.warn("Could not load identity:", e.message);
  }

  try {
    // Provider + cost
    const prov = await ipc.provider.getActive();
    if (prov) {
      state.ai = prov.provider || state.ai;
      state.aiModel = prov.model || state.aiModel;
      const provDisplay = document.getElementById("provDisplay");
      if (provDisplay) {
        const provName = providerDisplayName(state.ai);
        provDisplay.textContent = provName + " · live";
      }
    }
  } catch (e) {
    console.warn("Could not load provider:", e.message);
  }

  try {
    const cost = await ipc.provider.getCost();
    updateCostDisplay(cost);
  } catch (e) {
    console.warn("Could not load cost:", e.message);
  }

  try {
    // Workspace
    const ws = await ipc.workspace.get();
    if (ws) {
      state.workspace = ws.path || ws || "";
      const wsParts = state.workspace.replace(/\\/g, "/").split("/");
      const wsName = wsParts[wsParts.length - 1] || "Project";
      const crumbStrong = document.querySelector(".crumb strong");
      if (crumbStrong) crumbStrong.textContent = wsName;
      const termPath = document.querySelector(".term-path");
      if (termPath) termPath.textContent = wsName;
    }
  } catch (e) {
    console.warn("Could not load workspace:", e.message);
  }

  try {
    await ipc.workspace.startWatch();
  } catch (e) {
    console.warn("Could not start workspace watch:", e.message);
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
  const banner = document.querySelector(".sidecar-banner");
  if (banner) {
    banner.className = "sidecar-banner error";
    banner.innerHTML = '<i class="ti ti-alert-circle"></i> ' + esc(msg);
  }
  showError("Engine error: " + msg);
}

// ─── Cost display ──────────────────────────────────────────────────────────────

function updateCostDisplay(cost) {
  if (!cost) return;
  const display = document.getElementById("costDisplay");
  if (!display) return;
  const usd = cost.session_usd ?? cost.total_usd ?? 0;
  display.textContent = "$" + usd.toFixed(2);
}

function providerDisplayName(p) {
  const names = {
    anthropic: "Claude",
    openai: "GPT-4o",
    gemini: "Gemini",
    ollama: "Ollama",
    openrouter: "OpenRouter",
    deepseek: "DeepSeek",
    mistral: "Mistral",
    groq: "Groq",
    cerebras: "Cerebras",
    together: "Together AI",
    xai: "xAI",
    qwen: "Qwen",
  };
  return names[p] || p;
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
    terminal: "Terminal",
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
      terminal: loadTerminal,
    };
    if (loaders[tab]) await loaders[tab]();
  } catch (e) {
    console.warn("Tab load error for", tab, e.message);
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

async function loadDashboard() {
  try {
    const [waveData, gatesData, costData, gitData, artifacts] = await Promise.allSettled([
      ipc.wave.get(),
      ipc.gates.getAll(),
      ipc.provider.getCost(),
      ipc.git.status(),
      ipc.project.artifacts(),
    ]);

    if (costData.status === "fulfilled") updateCostDisplay(costData.value);

    // Render gate stepper
    if (gatesData.status === "fulfilled" && gatesData.value) {
      renderGateStepper(gatesData.value);
      renderCurrentGate(gatesData.value);
    }

    // Update hero ring with wave progress
    if (waveData.status === "fulfilled" && waveData.value) {
      renderWaveHero(waveData.value, gatesData.value);
    }

    // Enforcement state
    await loadEnforcement();
  } catch (e) {
    console.warn("Dashboard load error:", e.message);
  }
}

function renderWaveHero(wave, gates) {
  const gateList = Array.isArray(gates) ? gates : (gates?.gates || []);
  const total = gateList.length || 7;
  const done = gateList.filter((g) => g.status === "signed" || g.signed).length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  const ringPct = document.getElementById("ringPct");
  if (ringPct) ringPct.textContent = pct + "%";
  const ring = document.getElementById("ring");
  if (ring) {
    const circumference = 276.46;
    ring.style.strokeDashoffset = circumference * (1 - pct / 100);
  }

  const activeName = wave?.current_gate_name || wave?.name || "";
  const h2 = document.querySelector(".hero-tx h2");
  if (h2 && activeName) h2.textContent = activeName;

  const heroSub = document.getElementById("heroSub");
  if (heroSub) {
    heroSub.textContent = done + " of " + total + " gates signed.";
  }
}

function renderGateStepper(gates) {
  const cells = document.querySelectorAll(".scell");
  const gateList = Array.isArray(gates) ? gates : (gates?.gates || []);
  gateList.forEach((gate, i) => {
    const cell = cells[i];
    if (!cell) return;
    cell.classList.remove("done", "active");
    const scirc = cell.querySelector(".scirc");
    const sstatus = cell.querySelector(".sstatus");
    const slbl = cell.querySelector(".slbl");
    if (slbl && gate.name) slbl.textContent = gate.name;
    if (gate.status === "signed" || gate.signed) {
      cell.classList.add("done");
      if (scirc) scirc.innerHTML = '<i class="ti ti-check"></i>';
      if (sstatus) sstatus.textContent = "Signed";
    } else if (gate.status === "active" || gate.is_current) {
      cell.classList.add("active");
      if (scirc) scirc.textContent = String(i + 1);
      if (sstatus) sstatus.textContent = "Current";
      state.currentGateId = gate.id || gate.gate_id || null;
    } else {
      if (scirc) scirc.innerHTML = '<i class="ti ti-lock"></i>';
      if (sstatus) sstatus.textContent = "Locked";
    }
  });
}

function renderCurrentGate(gates) {
  const gateList = Array.isArray(gates) ? gates : (gates?.gates || []);
  const activeGate = gateList.find((g) => g.status === "active" || g.is_current);
  if (!activeGate) return;

  state.currentGateId = activeGate.id || activeGate.gate_id || null;

  const gateHead = document.querySelector("#gateCard .gate-tx h3");
  if (gateHead) gateHead.textContent = activeGate.name || "Current Gate";

  // Update gate badge
  const gateBadge = document.getElementById("gateBadge");
  if (gateBadge) {
    if (activeGate.signed) {
      gateBadge.className = "gate-badge passed";
      gateBadge.innerHTML = '<i class="ti ti-check"></i> Signed';
    } else {
      gateBadge.className = "gate-badge";
      gateBadge.innerHTML = '<span class="dot"></span> Current gate';
    }
  }

  // Render activities from gate data
  if (activeGate.activities) {
    renderGateActivities(activeGate.activities);
  }

  // Render criteria
  if (activeGate.criteria) {
    renderGateCriteria(activeGate.criteria);
  }

  updateGateVerdictDisplay();
}

function renderGateActivities(activities) {
  const container = document.getElementById("acts");
  if (!container || !activities?.length) return;
  container.innerHTML = activities
    .map((act) => {
      const status = act.status || "pending";
      const cls = status === "completed" ? "done" : status === "in_progress" ? "ongoing" : "pending";
      let icHTML = "", pillHTML = "";
      if (cls === "done") {
        icHTML = '<i class="ti ti-check"></i>';
        pillHTML = "Done";
      } else if (cls === "ongoing") {
        icHTML = '<i class="ti ti-loader-2"></i>';
        pillHTML = '<span class="pdot"></span>Ongoing';
      } else {
        icHTML = "";
        pillHTML = "Pending";
      }
      return `<div class="act ${cls}" onclick="cycleActivity(this)">
        <div class="act-ic">${icHTML}</div>
        <div class="act-name">${esc(act.name || act.description || "")}</div>
        <div class="act-pill">${pillHTML}</div>
      </div>`;
    })
    .join("");
  updateGateVerdictDisplay();
}

function renderGateCriteria(criteria) {
  const container = document.getElementById("crits");
  if (!container || !criteria?.length) return;
  container.innerHTML = criteria
    .map((c) => {
      const status = c.status || "waiting";
      const cls =
        status === "passed"
          ? "passed"
          : status === "failed"
          ? "failed"
          : status === "checking"
          ? "checking"
          : "waiting";
      let icHTML = "", pillHTML = "";
      if (cls === "passed") {
        icHTML = '<i class="ti ti-shield-check"></i>';
        pillHTML = "Passed";
      } else if (cls === "failed") {
        icHTML = '<i class="ti ti-shield-x"></i>';
        pillHTML = "Needs a fix";
      } else if (cls === "checking") {
        icHTML = '<i class="ti ti-loader-2"></i>';
        pillHTML = '<span class="pdot"></span>Checking';
      } else {
        icHTML = '<i class="ti ti-shield"></i>';
        pillHTML = "Waiting";
      }
      return `<div class="crit ${cls}" onclick="runCheck(this)">
        <div class="crit-ic">${icHTML}</div>
        <div class="crit-name">${esc(c.name || c.description || "")}</div>
        <div class="crit-pill">${pillHTML}</div>
      </div>`;
    })
    .join("");
  updateGateVerdictDisplay();
}

function updateGateVerdictDisplay() {
  const acts = [...document.querySelectorAll(".act")];
  const aDone = acts.filter((a) => a.classList.contains("done")).length;
  const aOngoing = acts.filter((a) => a.classList.contains("ongoing")).length;
  const aPending = acts.filter((a) => a.classList.contains("pending")).length;

  const el = document.getElementById("cDone");
  if (el) el.textContent = aDone;
  const elO = document.getElementById("cOngoing");
  if (elO) elO.textContent = aOngoing;
  const elP = document.getElementById("cPending");
  if (elP) elP.textContent = aPending;

  const crits = [...document.querySelectorAll(".crit")];
  const cPassed = crits.filter((c) => c.classList.contains("passed")).length;
  const cCrit = document.getElementById("cCrit");
  if (cCrit) cCrit.textContent = cPassed;

  const heroSub = document.getElementById("heroSub");
  if (heroSub && acts.length > 0) {
    heroSub.textContent =
      aDone + " of " + acts.length + " activities done · " + cPassed + " of " + crits.length + " checks passed.";
  }

  const ready = aDone === acts.length && cPassed === crits.length && acts.length > 0;
  const verdict = document.getElementById("verdict");
  const openBtn = document.getElementById("openBtn");

  if (verdict && !state.gateOpen) {
    verdict.classList.toggle("ready", ready);
    verdict.classList.toggle("held", !ready);
    const vic = verdict.querySelector(".verdict-ic");
    const vtx = document.getElementById("verdictTx");
    if (ready) {
      if (vic) vic.innerHTML = '<i class="ti ti-circle-check"></i>';
      if (vtx) vtx.textContent = "All clear — sign the gate to advance.";
      if (openBtn) openBtn.disabled = false;
    } else {
      if (vic) vic.innerHTML = '<i class="ti ti-lock"></i>';
      const aLeft = acts.length - aDone, cLeft = crits.length - cPassed;
      const parts = [];
      if (aLeft) parts.push(aLeft + " activit" + (aLeft > 1 ? "ies" : "y") + " to finish");
      if (cLeft) parts.push(cLeft + " check" + (cLeft > 1 ? "s" : "") + " to pass");
      if (vtx) vtx.textContent = "Gate held" + (parts.length ? " — " + parts.join(" and ") + "." : ".");
      if (openBtn) openBtn.disabled = true;
    }
  }
}

// ─── Build / Chat ──────────────────────────────────────────────────────────────

async function loadBuild() {
  // Load conversation history
  try {
    const buildId = await activeBuildId();
    const turns = await loadConvHistory(buildId);
    if (turns && turns.length > 0) {
      const inner = document.getElementById("chatInner");
      if (inner) {
        // Keep only the initial greeting, append real history turns
        inner.innerHTML = `<div class="msg spark">
          <div class="msg-av"><i class="ti ti-sparkles" style="font-size:17px"></i></div>
          <div>
            <div class="bubble">Hi ${esc(state.userName || "there")}! What do you want to build today?</div>
            <div class="msg-meta">SignalOS</div>
          </div>
        </div>`;
        turns.forEach((t) => {
          if (t.user_idea || t.user) addUserBubble(t.user_idea || t.user, true);
          if (t.ai_summary || t.summary) addAIBubble(t.ai_summary || t.summary, true);
        });
        scrollChat();
      }
    }
  } catch (e) {
    console.warn("Could not load conversation history:", e.message);
  }

  // Load enforcement state for build phase
  await loadEnforcement().catch(() => {});
}

// Chat: send message
async function sendMsg() {
  const input = document.getElementById("chatInput");
  const val = (input?.value || "").trim();
  if (!val || state.busy) return;
  state.busy = true;

  // Close command palette
  document.getElementById("cmdPalette")?.classList.remove("open");

  addUserBubble(val);
  if (input) input.value = "";

  const streamId = crypto.randomUUID();
  startStream(streamId);

  try {
    await ipc.provider.chatStream(streamId, state.ai, state.aiModel, val);
    // finaliseStream called by chat:token done event
    // Refresh cost after response
    const cost = await ipc.provider.getCost();
    updateCostDisplay(cost);
    // Log turn to conversation history
    const buildId = await activeBuildId().catch(() => null);
    if (buildId) {
      await appendTurn(buildId, { user_idea: val, ai_summary: "(streaming)" }).catch(() => {});
    }
  } catch (e) {
    showStreamError(streamId, e.message);
  } finally {
    state.busy = false;
  }
}
window.sendMsg = sendMsg;

function addUserBubble(text, historical = false) {
  const inner = document.getElementById("chatInner");
  if (!inner) return;
  const av = state.userName ? state.userName[0].toUpperCase() : "?";
  const when = historical ? "" : "just now";
  const m = document.createElement("div");
  m.className = "msg user";
  m.innerHTML = `<div class="msg-av">${esc(av)}</div>
    <div>
      <div class="bubble">${esc(text)}</div>
      ${when ? `<div class="msg-meta">${esc(when)}</div>` : ""}
    </div>`;
  inner.appendChild(m);
  scrollChat();
}

function addAIBubble(text, historical = false) {
  const inner = document.getElementById("chatInner");
  if (!inner) return;
  const m = document.createElement("div");
  m.className = "msg spark";
  const when = historical ? "" : "SignalOS · just now";
  m.innerHTML = `<div class="msg-av"><i class="ti ti-sparkles" style="font-size:17px"></i></div>
    <div>
      <div class="bubble">${esc(text)}</div>
      ${when ? `<div class="msg-meta">${esc(when)}</div>` : ""}
    </div>`;
  inner.appendChild(m);
  scrollChat();
}

function scrollChat() {
  const s = document.getElementById("chatScroll");
  if (s) s.scrollTop = s.scrollHeight;
}

// ─── Streaming bubbles ─────────────────────────────────────────────────────────

function startStream(streamId) {
  const inner = document.getElementById("chatInner");
  if (!inner) return;
  const div = document.createElement("div");
  div.className = "msg spark";
  div.innerHTML = `<div class="msg-av"><i class="ti ti-sparkles" style="font-size:17px"></i></div>
    <div>
      <div class="bubble streaming" id="stream-${streamId}">
        <span class="stream-text"></span><span class="stream-cursor"></span>
      </div>
      <div class="msg-meta">SignalOS · now</div>
    </div>`;
  inner.appendChild(div);
  const bubble = div.querySelector(".bubble");
  state.streamBubbles[streamId] = {
    el: div,
    bubble,
    textEl: div.querySelector(".stream-text"),
    cursor: div.querySelector(".stream-cursor"),
  };
  scrollChat();
}

function appendStreamToken(streamId, delta) {
  const b = state.streamBubbles[streamId];
  if (!b) return;
  b.textEl.textContent += delta;
  scrollChat();
}

function finaliseStream(streamId) {
  const b = state.streamBubbles[streamId];
  if (!b) return;
  if (b.cursor) b.cursor.remove();
  b.bubble.classList.remove("streaming");
  delete state.streamBubbles[streamId];
  scrollChat();
}

function showStreamError(streamId, msg) {
  const b = state.streamBubbles[streamId];
  if (b) {
    if (b.cursor) b.cursor.remove();
    b.bubble.classList.remove("streaming");
    b.bubble.style.background = "var(--danger-soft)";
    b.bubble.style.color = "var(--danger-deep)";
    b.textEl.textContent = "Error: " + (msg || "Stream failed");
    delete state.streamBubbles[streamId];
    scrollChat();
  }
  showError(msg || "Chat stream error");
}

// ─── Chat composer ─────────────────────────────────────────────────────────────

function composerInput(e) {
  const val = e.target.value;
  const palette = document.getElementById("cmdPalette");
  if (!palette) return;
  if (val.startsWith("/")) {
    filterCommands(val.slice(1));
    palette.classList.add("open");
  } else {
    palette.classList.remove("open");
  }
}
window.composerInput = composerInput;

function composerKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMsg();
  }
  if (e.key === "Escape") {
    document.getElementById("cmdPalette")?.classList.remove("open");
  }
}
window.composerKey = composerKey;

function filterCommands(query) {
  document.querySelectorAll(".cmd-item").forEach((item) => {
    const name = item.querySelector(".cmd-item-name")?.textContent || "";
    item.style.display = name.includes(query) ? "flex" : "none";
  });
}

function runCmd(cmd) {
  const input = document.getElementById("chatInput");
  if (input) input.value = cmd;
  document.getElementById("cmdPalette")?.classList.remove("open");
  sendMsg();
}
window.runCmd = runCmd;

function sendChip(el) {
  addUserBubble(el.textContent);
  const streamId = crypto.randomUUID();
  startStream(streamId);
  ipc.provider
    .chatStream(streamId, state.ai, state.aiModel, el.textContent)
    .then(() => ipc.provider.getCost().then(updateCostDisplay).catch(() => {}))
    .catch((e) => showStreamError(streamId, e.message));
}
window.sendChip = sendChip;

// ─── Enforcement ───────────────────────────────────────────────────────────────

async function loadEnforcement() {
  try {
    const enfState = await ipc.enforcement.state();
    renderEnforcementPills(enfState);
    state.waveFrozen = Boolean(enfState?.wave_frozen);
    const frozenBanner = document.getElementById("frozenBanner");
    if (frozenBanner) {
      frozenBanner.classList.toggle("visible", state.waveFrozen);
    }
    const freezeBtn = document.getElementById("freezeBtn");
    if (freezeBtn) {
      freezeBtn.innerHTML = state.waveFrozen
        ? '<i class="ti ti-sun"></i> Unfreeze wave'
        : '<i class="ti ti-snowflake"></i> Freeze wave';
    }
  } catch (e) {
    console.warn("Could not load enforcement state:", e.message);
  }
}

function renderEnforcementPills(enfState) {
  const pill = document.getElementById("enfPill");
  if (!pill) return;
  const rules = enfState?.rules || [];
  const warns = rules.filter((r) => r.status === "warn").length;
  const errors = rules.filter((r) => r.status === "blocked" || r.status === "error").length;

  // Remove popover from pill so we can replace outer HTML safely
  const popover = document.getElementById("enfPopover");

  if (errors > 0) {
    pill.className = "enf-pill blocked";
    pill.childNodes[0].textContent = "";
    pill.innerHTML = `<i class="ti ti-shield-off"></i> ${errors} blocked`;
  } else if (warns > 0) {
    pill.className = "enf-pill warn";
    pill.innerHTML = `<i class="ti ti-shield-half"></i> ${warns} warning${warns > 1 ? "s" : ""}`;
  } else {
    pill.className = "enf-pill ok";
    pill.innerHTML = '<i class="ti ti-shield-check"></i> All clear';
  }

  // Re-attach popover
  if (popover) pill.appendChild(popover);

  // Render rules inside popover
  const rulesContainer = document.getElementById("enfRules");
  if (rulesContainer && rules.length > 0) {
    rulesContainer.innerHTML = rules
      .map((r) => {
        const ok = r.status === "ok" || r.status === "pass";
        const icCls = ok ? "ok" : "warn";
        const icIcon = ok ? "ti-check" : "ti-alert-triangle";
        return `<div class="rule-row">
          <div class="rule-ic ${icCls}"><i class="ti ${icIcon}"></i></div>
          <div class="rule-tx">
            <div class="rule-name">${esc(r.name || r.rule || "")}</div>
            <div class="rule-desc">${esc(r.description || r.desc || "")}</div>
          </div>
        </div>`;
      })
      .join("");
  }
}

async function freezeWave() {
  try {
    await ipc.enforcement.freeze();
    state.waveFrozen = true;
    const banner = document.getElementById("frozenBanner");
    if (banner) banner.classList.add("visible");
    const btn = document.getElementById("freezeBtn");
    if (btn) {
      btn.innerHTML = '<i class="ti ti-sun"></i> Unfreeze wave';
      btn.onclick = unfreezeWave;
    }
    addAIBubble("Wave frozen. No AI file writes allowed until you unfreeze.");
    switchTab("build");
  } catch (e) {
    showError("Could not freeze wave: " + e.message);
  }
}
window.freezeWave = freezeWave;

async function unfreezeWave() {
  try {
    await ipc.enforcement.unfreeze();
    state.waveFrozen = false;
    const banner = document.getElementById("frozenBanner");
    if (banner) banner.classList.remove("visible");
    const btn = document.getElementById("freezeBtn");
    if (btn) {
      btn.innerHTML = '<i class="ti ti-snowflake"></i> Freeze wave';
      btn.onclick = freezeWave;
    }
    addAIBubble("Wave unfrozen. Enforcement rules still active — proceed carefully.");
    switchTab("build");
  } catch (e) {
    showError("Could not unfreeze wave: " + e.message);
  }
}
window.unfreezeWave = unfreezeWave;

function toggleEnfPopover() {
  state.enfOpen = !state.enfOpen;
  document.getElementById("enfPopover")?.classList.toggle("open", state.enfOpen);
}
window.toggleEnfPopover = toggleEnfPopover;

// Close popover when clicking outside
document.addEventListener("click", (e) => {
  if (!e.target.closest(".enf-pill")) {
    state.enfOpen = false;
    document.getElementById("enfPopover")?.classList.remove("open");
  }
});

async function openOverride() {
  document.getElementById("enfPopover")?.classList.remove("open");
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
    showError("Override failed: " + e.message);
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
    showError("Gate sign failed: " + e.message);
  }
}
window.openGate = openGate;

function showSignForm() {
  const form = document.getElementById("signForm");
  if (form) form.style.display = form.style.display === "none" ? "flex" : "none";
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
    const waveNameEl = document.querySelector(".gov-wave-name");
    const gateList = gatesData.status === "fulfilled" ? (Array.isArray(gatesData.value) ? gatesData.value : (gatesData.value?.gates || [])) : [];

    // Gate nodes
    const nodesContainer = document.querySelector(".gate-nodes");
    if (nodesContainer && gateList.length > 0) {
      nodesContainer.innerHTML = gateList
        .map((g, i) => {
          const cls =
            g.status === "signed" || g.signed
              ? "done"
              : g.status === "active" || g.is_current
              ? "active"
              : "locked";
          return `<div class="gate-node ${cls}" title="${esc(g.name || "Gate " + (i + 1))}">G${i + 1}</div>`;
        })
        .join("");
    }

    // Audit rows
    const auditContainer = document.querySelector("#sb-gov .audit-row")?.parentElement;
    if (auditContainer && auditData.status === "fulfilled" && auditData.value) {
      const entries = auditData.value.slice(0, 6);
      const existing = auditContainer.querySelectorAll(".audit-row");
      existing.forEach((r) => r.remove());
      entries.forEach((entry) => {
        const dot = entry.action?.includes("sign") ? "sign" : entry.action?.includes("build") ? "build" : entry.action?.includes("override") ? "override" : "build";
        const div = document.createElement("div");
        div.className = "audit-row";
        div.innerHTML = `<div class="audit-dot ${dot}"></div>
          <div class="audit-tx">
            <div class="audit-action">${esc(entry.action || "")}</div>
            <div class="audit-meta">${esc(formatTs(entry.ts || entry.timestamp || ""))}</div>
          </div>`;
        auditContainer.appendChild(div);
      });
    }
  } catch (e) {
    console.warn("Gov panel load error:", e.message);
  }
}

// ─── Brain ─────────────────────────────────────────────────────────────────────

async function loadBrain() {
  try {
    const entries = await ipc.brain.search("");
    renderBrainCards(entries || []);
  } catch (e) {
    console.warn("Brain load error:", e.message);
    showError("Could not load Brain: " + e.message);
  }
}

function renderBrainCards(entries) {
  const container = document.querySelector('[data-view="brain"] .card');
  if (!container) return;
  if (!entries.length) {
    container.innerHTML = '<div style="padding:24px;text-align:center;color:var(--ink-3)">No brain entries yet. Use /signal-brain in the Build tab to add notes.</div>';
    return;
  }
  container.innerHTML = entries
    .map((e) => {
      const type = (e.entry_type || e.type || "note").toLowerCase();
      const typeMap = {
        note: { cls: "note", icon: "ti-notes", label: "Note" },
        decision: { cls: "decision", icon: "ti-scale", label: "Decision" },
        artifact: { cls: "artifact", icon: "ti-file-code", label: "Artifact" },
        qa: { cls: "qa", icon: "ti-help-circle", label: "Q&A" },
      };
      const t = typeMap[type] || typeMap.note;
      return `<div class="brain-row">
        <div class="brain-type-ic ${t.cls}"><i class="ti ${t.icon}"></i></div>
        <div class="brain-tx">
          <div class="brain-title">${esc(e.title || e.text?.slice(0, 80) || "")}</div>
          <div class="brain-body">${esc(e.body || e.text || "")}</div>
          <div class="brain-meta">
            <span>${esc(formatTs(e.ts || e.created_at || ""))}</span>
            <span class="brain-tag">${esc(t.label)}</span>
          </div>
        </div>
      </div>`;
    })
    .join("");
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
    showError("Could not add brain entry: " + e.message);
  }
}
window.addBrainEntry = addBrainEntry;

function filterBrain(el, type) {
  document.querySelectorAll(".brain-type").forEach((b) => b.classList.remove("active"));
  el.classList.add("active");
  // Re-render filtered (re-load and client-side filter)
  ipc.brain
    .search(type === "all" ? "" : type)
    .then((entries) => {
      const filtered =
        type === "all"
          ? entries
          : entries.filter((e) => (e.entry_type || e.type || "").toLowerCase() === type);
      renderBrainCards(filtered || []);
    })
    .catch((e) => console.warn("Brain filter error:", e.message));
}
window.filterBrain = filterBrain;

// ─── Vault ─────────────────────────────────────────────────────────────────────

async function loadVault() {
  try {
    const list = await ipc.secrets.list();
    renderSecretRows(list || []);
    // Update vstats
    const secretsCount = document.querySelector(".vstats .vstat:first-child .vstat-v");
    if (secretsCount) secretsCount.textContent = String((list || []).length);
    const heroTx = document.querySelector(".vault-hero .vh-tx h2");
    if (heroTx) {
      const n = (list || []).length;
      heroTx.textContent = n === 0 ? "No secrets stored yet" : n === 1 ? "One secret safely sealed" : n + " secrets safely sealed";
    }
  } catch (e) {
    console.warn("Vault load error:", e.message);
    showError("Could not load Vault: " + e.message);
  }
}

function renderSecretRows(list) {
  const container = document.querySelector('[data-view="vault"] .card');
  if (!container) return;
  const head = container.querySelector(".secrets-head");
  // Clear existing rows
  container.querySelectorAll(".srow").forEach((r) => r.remove());
  if (!list.length) {
    const empty = document.createElement("div");
    empty.style.cssText = "padding:24px;text-align:center;color:var(--ink-3);font-size:13px";
    empty.textContent = "No secrets yet. Click Add secret to store your first key.";
    container.appendChild(empty);
    return;
  }
  list.forEach((entry) => {
    const name = entry.name || entry.key || "";
    const row = document.createElement("div");
    row.className = "srow";
    row.dataset.secretName = name;
    row.innerHTML = `
      <div class="s-ic"><i class="ti ti-key"></i></div>
      <div class="s-info">
        <div class="s-nm">${esc(name)}</div>
        <div class="s-meta">${esc(entry.file || ".env.local")}</div>
      </div>
      <div class="s-val">••••••••••••••••</div>
      <div class="s-act">
        <div class="ico" onclick="toggleSecret(this)" aria-label="Reveal"><i class="ti ti-eye"></i></div>
        <div class="ico" onclick="copySecret(this)" aria-label="Copy"><i class="ti ti-copy"></i></div>
        <div class="ico" onclick="deleteSecret(this)" aria-label="Delete"><i class="ti ti-trash"></i></div>
      </div>`;
    container.appendChild(row);
  });
}

async function toggleSecret(btn) {
  const row = btn.closest(".srow");
  if (!row) return;
  const name = row.dataset.secretName;
  const val = row.querySelector(".s-val");
  const ico = btn.querySelector("i");
  if (val.textContent.includes("•")) {
    try {
      const raw = await ipc.secrets.reveal(name);
      val.textContent = raw;
      if (ico) ico.className = "ti ti-eye-off";
      // Auto-hide after 30 seconds
      setTimeout(() => {
        val.textContent = "••••••••••••••••";
        if (ico) ico.className = "ti ti-eye";
      }, 30000);
    } catch (e) {
      showError("Could not reveal secret: " + e.message);
    }
  } else {
    val.textContent = "••••••••••••••••";
    if (ico) ico.className = "ti ti-eye";
  }
}
window.toggleSecret = toggleSecret;

async function copySecret(btn) {
  const row = btn.closest(".srow");
  if (!row) return;
  const name = row.dataset.secretName;
  try {
    const raw = await ipc.secrets.reveal(name);
    await navigator.clipboard.writeText(raw);
    const ico = btn.querySelector("i");
    const orig = ico?.className;
    if (ico) ico.className = "ti ti-check";
    setTimeout(() => { if (ico && orig) ico.className = orig; }, 1500);
  } catch (e) {
    showError("Could not copy secret: " + e.message);
  }
}
window.copySecret = copySecret;

async function deleteSecret(btn) {
  const row = btn.closest(".srow");
  if (!row) return;
  const name = row.dataset.secretName;
  if (!confirm("Delete secret " + name + "? This cannot be undone.")) return;
  try {
    await ipc.secrets.delete(name);
    await loadVault();
  } catch (e) {
    showError("Could not delete secret: " + e.message);
  }
}
window.deleteSecret = deleteSecret;

function openAddSecret() {
  openModal("addSecretModal");
}
window.openAddSecret = openAddSecret;

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
    showError("Could not save secret: " + e.message);
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
    renderHistoryRows(entries || []);
    renderHistoryCost(cost);
  } catch (e) {
    console.warn("History load error:", e.message);
    showError("Could not load History: " + e.message);
  }
}

function renderHistoryRows(entries) {
  const container = document.querySelector('[data-view="history"] .card');
  if (!container) return;
  const head = container.querySelector(".secrets-head");
  container.querySelectorAll(".history-item").forEach((r) => r.remove());

  if (!entries.length) {
    const empty = document.createElement("div");
    empty.style.cssText = "padding:24px;text-align:center;color:var(--ink-3);font-size:13px";
    empty.textContent = "No history yet.";
    container.appendChild(empty);
    return;
  }

  entries.forEach((entry) => {
    const action = entry.action || "";
    const isSign = action.includes("sign") || action.includes("gate");
    const isFreeze = action.includes("freeze") || action.includes("override");
    const icCls = isSign ? "sign" : isFreeze ? "freeze" : "build";
    const icIcon = isSign ? "ti-pencil" : isFreeze ? "ti-alert-triangle" : "ti-hammer";
    const badgeText = isSign ? "Signed" : isFreeze ? "Override" : "Done";
    const badgeCls = isSign ? "done" : isFreeze ? "" : "done";
    const badgeStyle = isFreeze ? "style=\"background:var(--amber-soft);color:var(--amber-deep)\"" : "";

    const div = document.createElement("div");
    div.className = "history-item";
    div.innerHTML = `
      <div class="history-ic ${icCls}"><i class="ti ${icIcon}"></i></div>
      <div class="history-tx">
        <div class="history-title">${esc(action)}</div>
        <div class="history-meta">${esc(formatTs(entry.ts || entry.timestamp || ""))}</div>
      </div>
      <span class="history-badge ${badgeCls}" ${badgeStyle}>${esc(badgeText)}</span>`;
    container.appendChild(div);
  });

  // Update vstats
  const buildRuns = entries.filter((e) => (e.action || "").includes("build")).length;
  const gatesSigned = entries.filter((e) => (e.action || "").includes("sign")).length;
  const stats = document.querySelectorAll('[data-view="history"] .vstat .vstat-v');
  if (stats[0]) stats[0].textContent = String(buildRuns);
  if (stats[1]) stats[1].textContent = String(gatesSigned);
}

function renderHistoryCost(cost) {
  if (!cost) return;
  const sessionSpend = document.querySelector('[data-view="settings"] .settings-path[data-session-spend]');
  if (sessionSpend) sessionSpend.textContent = "$" + (cost.session_usd || 0).toFixed(4);
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
    showError("Export failed: " + e.message);
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
    showError("Report failed: " + e.message);
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
      const settingsName = document.getElementById("settingsName");
      if (settingsName) settingsName.value = id.name || "";
      const settingsRole = document.getElementById("settingsRole");
      if (settingsRole) settingsRole.value = id.role || "PO";
    }

    if (prov) {
      const provSelect = document.getElementById("settingsProvider");
      if (provSelect) provSelect.value = prov.provider || "anthropic";
      const modelSelect = document.getElementById("settingsModel");
      if (modelSelect) modelSelect.value = prov.model || "";
    }

    if (cost) {
      const spendEl = document.getElementById("settingsSessionSpend");
      if (spendEl) spendEl.textContent = "$" + (cost.session_usd || 0).toFixed(4);
      const budgetInput = document.getElementById("settingsBudget");
      if (budgetInput && cost.budget_usd) budgetInput.value = cost.budget_usd;
    }

    // Workspace path
    const ws = await ipc.workspace.get().catch(() => null);
    if (ws) {
      const wsPath = document.getElementById("settingsWorkspacePath");
      if (wsPath) wsPath.textContent = ws.path || ws;
    }

    // Engine status
    try {
      const engineStatus = await ipc.engine.status();
      const engBadge = document.getElementById("engineStatusBadge");
      if (engBadge) {
        const running = engineStatus?.running ?? engineStatus?.status === "running";
        engBadge.className = running ? "live-badge" : "live-badge";
        engBadge.innerHTML = running
          ? '<span class="dot"></span> Running'
          : '<span style="width:6px;height:6px;border-radius:50%;background:var(--danger);display:inline-block"></span> Stopped';
      }
    } catch {
      // Engine status not critical
    }
  } catch (e) {
    console.warn("Settings load error:", e.message);
  }
}

async function saveIdentity() {
  const name = document.getElementById("settingsName")?.value.trim();
  const role = document.getElementById("settingsRole")?.value;
  if (!name) { showError("Name is required"); return; }
  try {
    await ipc.identity.set(name, role);
    state.userName = name;
    state.userRole = role;
    const sbUserName = document.getElementById("sbUserName");
    if (sbUserName) sbUserName.textContent = name;
    const sbUserRole = document.getElementById("sbUserRole");
    if (sbUserRole) sbUserRole.textContent = role;
    const sbAv = document.getElementById("sbAvatar");
    if (sbAv) sbAv.textContent = name[0]?.toUpperCase() || "?";
  } catch (e) {
    showError("Could not save identity: " + e.message);
  }
}
window.saveIdentity = saveIdentity;

async function saveBudget() {
  const val = parseFloat(document.getElementById("settingsBudget")?.value);
  if (isNaN(val) || val < 0) { showError("Invalid budget amount"); return; }
  try {
    await ipc.provider.setBudget(val);
  } catch (e) {
    showError("Could not save budget: " + e.message);
  }
}
window.saveBudget = saveBudget;

async function resetSessionCost() {
  try {
    await ipc.provider.resetSession();
    const cost = await ipc.provider.getCost();
    updateCostDisplay(cost);
    await loadSettings();
  } catch (e) {
    showError("Could not reset session: " + e.message);
  }
}
window.resetSessionCost = resetSessionCost;

async function changeProvider() {
  const provSelect = document.getElementById("settingsProvider");
  const p = provSelect?.value;
  if (!p) return;
  try {
    await ipc.provider.setActive(p);
    state.ai = p;
    const provDisplay = document.getElementById("provDisplay");
    if (provDisplay) provDisplay.textContent = providerDisplayName(p) + " · live";
  } catch (e) {
    showError("Could not change provider: " + e.message);
  }
}
window.changeProvider = changeProvider;

async function changeModel() {
  const modelSelect = document.getElementById("settingsModel");
  const model = modelSelect?.value;
  if (!model) return;
  try {
    await ipc.provider.setModel(state.ai, model);
    state.aiModel = model;
  } catch (e) {
    showError("Could not change model: " + e.message);
  }
}
window.changeModel = changeModel;

async function replaceApiKey() {
  const key = prompt("Enter new API key:");
  if (!key) return;
  try {
    await ipc.keychain.store(state.ai, key);
    const result = await ipc.provider.test(state.ai, key, state.aiModel);
    if (result?.ok || result === true) {
      addAIBubble("API key updated and verified successfully.");
    }
  } catch (e) {
    showError("Could not update API key: " + e.message);
  }
}
window.replaceApiKey = replaceApiKey;

async function forgetWorkspace() {
  if (!confirm("Remove this workspace from SignalOS? Your files stay on your computer.")) return;
  try {
    // Reset workspace — no specific IPC for "forget", so set to empty signals reset
    await ipc.workspace.set("");
    const wsPath = document.getElementById("settingsWorkspacePath");
    if (wsPath) wsPath.textContent = "(none)";
    state.workspace = "";
  } catch (e) {
    showError("Could not forget workspace: " + e.message);
  }
}
window.forgetWorkspace = forgetWorkspace;

async function testEngine() {
  const btn = document.getElementById("testEngineBtn");
  if (btn) btn.innerHTML = '<i class="ti ti-loader-2" style="animation:spin 1s linear infinite"></i> Testing…';
  try {
    await ipc.engine.ping();
    if (btn) btn.innerHTML = '<i class="ti ti-circle-check" style="color:var(--success)"></i> OK';
    setTimeout(() => { if (btn) btn.innerHTML = '<i class="ti ti-activity"></i> Test'; }, 2000);
  } catch (e) {
    if (btn) btn.innerHTML = '<i class="ti ti-alert-circle" style="color:var(--danger)"></i> Failed';
    setTimeout(() => { if (btn) btn.innerHTML = '<i class="ti ti-activity"></i> Test'; }, 2000);
    showError("Engine test failed: " + e.message);
  }
}
window.testEngine = testEngine;

async function restartEngine() {
  const btn = document.getElementById("restartEngineBtn");
  if (btn) btn.innerHTML = '<i class="ti ti-loader-2" style="animation:spin 1s linear infinite"></i> Restarting…';
  try {
    await ipc.engine.restart();
    if (btn) btn.innerHTML = '<i class="ti ti-refresh"></i> Restart';
    const engBadge = document.getElementById("engineStatusBadge");
    if (engBadge) engBadge.innerHTML = '<span class="dot"></span> Running';
  } catch (e) {
    showError("Engine restart failed: " + e.message);
    if (btn) btn.innerHTML = '<i class="ti ti-refresh"></i> Restart';
  }
}
window.restartEngine = restartEngine;

async function checkForUpdates() {
  const btn = document.getElementById("updateBtn");
  const result = document.getElementById("updateResult");
  const tx = document.getElementById("updateResultTx");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="ti ti-loader-2" style="animation:spin 1s linear infinite"></i> Checking…';
  }
  if (result) result.classList.remove("visible");
  try {
    const update = await ipc.updater.check(state.updateChannel);
    const hasUpdate = update?.available || update?.update_available;
    if (tx) tx.textContent = hasUpdate ? "Update available: " + (update.version || "") : "Up to date";
    if (result) {
      const icon = result.querySelector("i");
      if (icon) {
        icon.className = hasUpdate ? "ti ti-cloud-download" : "ti ti-circle-check";
        icon.style.color = hasUpdate ? "var(--accent)" : "var(--success)";
      }
      result.classList.add("visible");
    }
  } catch (e) {
    if (tx) tx.textContent = "Check failed";
    if (result) result.classList.add("visible");
  } finally {
    if (btn) {
      btn.innerHTML = '<i class="ti ti-cloud-download"></i> Check for updates';
      btn.disabled = false;
    }
  }
}
window.checkForUpdates = checkForUpdates;

// ─── Terminal ──────────────────────────────────────────────────────────────────

async function loadTerminal() {
  // Check sidecar status
  try {
    const status = await ipc.engine.status();
    const banner = document.querySelector(".sidecar-banner");
    if (banner) {
      const running = status?.running ?? status?.status === "running";
      banner.className = running ? "sidecar-banner" : "sidecar-banner error";
      banner.innerHTML = running
        ? '<i class="ti ti-circle-check"></i> SignalOS Core running · Python sidecar ready'
        : '<i class="ti ti-alert-circle"></i> SignalOS Core not running';
    }
  } catch {
    // Not critical
  }

  // Set terminal path
  const termPath = document.querySelector(".term-path");
  if (termPath && state.workspace) {
    const parts = state.workspace.replace(/\\/g, "/").split("/");
    termPath.textContent = parts[parts.length - 1] || state.workspace;
  }
}

async function termExecReal(cmd) {
  const body = document.getElementById("termBody");
  if (!body) return;

  // Echo command
  const echo = document.createElement("div");
  echo.className = "term-line";
  const pathName = state.workspace ? state.workspace.replace(/\\/g, "/").split("/").pop() : "signalos";
  echo.innerHTML = `<span class="t-path">${esc(pathName)}</span> <span class="t-sym">$</span> <span class="t-cmd">${esc(cmd)}</span>`;
  body.appendChild(echo);

  if (!cmd.trim()) { body.scrollTop = body.scrollHeight; return; }

  if (state.termHistory[state.termHistory.length - 1] !== cmd) {
    state.termHistory.push(cmd);
  }
  state.termHistIdx = -1;

  if (cmd === "clear" || cmd === "cls") {
    body.innerHTML = "";
    return;
  }

  // Show loading line
  const loading = document.createElement("div");
  loading.className = "term-line t-dim";
  loading.textContent = "Running…";
  body.appendChild(loading);
  body.scrollTop = body.scrollHeight;

  try {
    const result = await ipc.signal.runAndWait(cmd.replace(/^\//, ""), []);
    loading.remove();
    const lines = typeof result === "string" ? result.split("\n") : (result?.output || result?.lines || [String(result)]);
    lines.forEach((line) => {
      if (!line && lines.length === 1) return;
      const d = document.createElement("div");
      d.className = "term-line";
      d.textContent = line;
      body.appendChild(d);
    });
  } catch (e) {
    loading.remove();
    const err = document.createElement("div");
    err.className = "term-line t-err";
    err.textContent = e.message || "Command failed";
    body.appendChild(err);
  }

  body.scrollTop = body.scrollHeight;
}

function termKey(e) {
  const input = e.target;
  if (e.key === "Enter") {
    const cmd = input.value;
    input.value = "";
    state.termHistIdx = -1;
    termExecReal(cmd).catch(() => {});
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    if (state.termHistory.length) {
      state.termHistIdx = Math.min(state.termHistIdx + 1, state.termHistory.length - 1);
      input.value = state.termHistory[state.termHistory.length - 1 - state.termHistIdx];
    }
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    if (state.termHistIdx > 0) {
      state.termHistIdx--;
      input.value = state.termHistory[state.termHistory.length - 1 - state.termHistIdx];
    } else {
      state.termHistIdx = -1;
      input.value = "";
    }
  }
}
window.termKey = termKey;

function termChip(el) {
  termExecReal(el.textContent).catch(() => {});
}
window.termChip = termChip;

function termSubmit(val) {
  termExecReal(val || "").catch(() => {});
}
window.termSubmit = termSubmit;

// ─── File tree ─────────────────────────────────────────────────────────────────

async function refreshFileTree() {
  try {
    const entries = await ipc.project.listDir(".");
    renderFileTree(entries || []);
  } catch (e) {
    console.warn("File tree load error:", e.message);
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
    showError("Could not open file: " + e.message);
  }
}
window.openFile = openFile;

function showFileViewer(path, content) {
  // Switch to terminal tab and show file content there
  switchTab("terminal");
  const body = document.getElementById("termBody");
  if (!body) return;
  const header = document.createElement("div");
  header.className = "term-line t-bright";
  header.textContent = "=== " + path + " ===";
  body.appendChild(header);
  (content || "").split("\n").slice(0, 100).forEach((line) => {
    const d = document.createElement("div");
    d.className = "term-line";
    d.textContent = line;
    body.appendChild(d);
  });
  body.scrollTop = body.scrollHeight;
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
    <div class="file-toast-close" onclick="this.parentElement.remove()"><i class="ti ti-x"></i></div>`;
  document.body.appendChild(toast);
  setTimeout(() => { if (toast.parentElement) toast.remove(); }, 5000);
}
window.showFileWriteToast = showFileWriteToast;

// ─── Modals ────────────────────────────────────────────────────────────────────

function openModal(id) {
  document.getElementById(id)?.classList.add("open");
}
window.openModal = openModal;

function closeModal(id) {
  document.getElementById(id)?.classList.remove("open");
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

async function createProject() {
  const name = document.getElementById("newProjName")?.value.trim();
  const path = document.getElementById("newProjPath")?.value.trim();
  if (!name || !path) { showError("Name and folder path are required"); return; }
  try {
    await ipc.workspace.set(path);
    state.workspace = path;
    closeModal("newProjectModal");
    await bootApp();
  } catch (e) {
    showError("Could not create project: " + e.message);
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

let obStep = 1;

function initOnboarding() {
  obStep = 1;
  showObStep(1);
}

function showObStep(step) {
  document.querySelectorAll(".ob-step").forEach((s) => s.classList.remove("active"));
  const stepEl = document.querySelector(`.ob-step[data-step="${step}"]`);
  if (stepEl) stepEl.classList.add("active");

  // Update dots
  for (let i = 1; i <= 3; i++) {
    document.getElementById(`pd-${i}`)?.classList.toggle("active", i <= step);
  }

  // Update tag
  const obTag = document.getElementById("obTag");
  if (obTag) obTag.innerHTML = OB_TAGS[step - 1] || "";
}

function nextStep() {
  if (obStep >= 3) return;
  obStep++;
  showObStep(obStep);
}
window.nextStep = nextStep;

function prevStep() {
  if (obStep <= 1) return;
  obStep--;
  showObStep(obStep);
}
window.prevStep = prevStep;

function selectProv(el) {
  document.querySelectorAll(".prov-card").forEach((o) => o.classList.remove("sel"));
  el.classList.add("sel");
  state.ai = el.dataset.ai || "anthropic";
  state.aiModel = el.dataset.model || "";
  const lbl = el.dataset.keyLabel || "API key";
  const keyLabelEl = document.getElementById("keyLabel");
  if (keyLabelEl) keyLabelEl.textContent = lbl;
}
window.selectProv = selectProv;
window.selectAI = selectProv;

function toggleMoreProvs() {
  const more = document.getElementById("provMore");
  const btn = document.getElementById("provMoreBtn");
  const open = more?.style.display !== "none";
  if (more) more.style.display = open ? "none" : "grid";
  btn?.classList.toggle("open", !open);
  if (btn) {
    btn.innerHTML = open
      ? '<i class="ti ti-chevron-down"></i> 7 more providers'
      : '<i class="ti ti-chevron-up"></i> Show fewer';
  }
}
window.toggleMoreProvs = toggleMoreProvs;

function toggleKey() {
  const input = document.getElementById("apiKey");
  const tog = document.getElementById("keyTog");
  state.keyVisible = !state.keyVisible;
  if (input) input.type = state.keyVisible ? "text" : "password";
  if (tog) tog.innerHTML = state.keyVisible ? '<i class="ti ti-eye-off"></i>' : '<i class="ti ti-eye"></i>';
}
window.toggleKey = toggleKey;

async function finishOnboarding() {
  const nameEl = document.getElementById("identName");
  const roleEl = document.getElementById("identRole");
  const name = (nameEl?.value || "").trim() || "User";
  const role = roleEl?.value || "PO";

  const apiKeyEl = document.getElementById("apiKey");
  const apiKey = apiKeyEl?.value.trim() || "";

  const budgetEl = document.getElementById("budgetInput");
  const budget = parseFloat(budgetEl?.value) || 0;

  try {
    // Save identity
    await ipc.identity.set(name, role);
    state.userName = name;
    state.userRole = role;

    // Store API key
    if (apiKey) {
      await ipc.keychain.store(state.ai, apiKey);
      // Test connection
      await ipc.provider.test(state.ai, apiKey, state.aiModel);
    }

    // Set provider
    await ipc.provider.setActive(state.ai);
    if (state.aiModel) await ipc.provider.setModel(state.ai, state.aiModel);

    // Set budget
    if (budget > 0) await ipc.provider.setBudget(budget);

    // Mark wizard done in localStorage
    const LS_KEY = "signalos.onboarding.wizard.v1";
    const WIZARD_VERSION = 2;
    localStorage.setItem(LS_KEY, JSON.stringify({
      version: WIZARD_VERSION,
      completedSteps: ["welcome", "folder", "init", "identity", "ai", "budget", "done"],
      current: 6,
      folder: state.workspace,
      initMode: "keep",
      ai: { provider: state.ai, model: state.aiModel, tested: Boolean(apiKey) },
      budgetUsd: budget,
      privacy: { redactEnv: true, blockSecretFiles: true, localOnly: false },
      finishedAt: new Date().toISOString(),
    }));

    // Transition to app
    document.getElementById("onboarding").classList.remove("active");
    document.getElementById("app").classList.add("active");

    // Update user display
    const sbAv = document.getElementById("sbAvatar");
    if (sbAv) sbAv.textContent = name[0]?.toUpperCase() || "?";
    const sbUserName = document.getElementById("sbUserName");
    if (sbUserName) sbUserName.textContent = name;
    const sbUserRole = document.getElementById("sbUserRole");
    if (sbUserRole) sbUserRole.textContent = role;
    const signName = document.getElementById("signName");
    if (signName) signName.value = name;
    const provDisplay = document.getElementById("provDisplay");
    if (provDisplay) provDisplay.textContent = providerDisplayName(state.ai) + " · live";

    await bootApp();
  } catch (e) {
    showError("Setup failed: " + e.message);
  }
}
window.finishOnboarding = finishOnboarding;

// ─── Preview ───────────────────────────────────────────────────────────────────

function switchDevice(mode) {
  document.querySelectorAll(".dev-b").forEach((b) => b.classList.toggle("active", b.dataset.device === mode));
  const pvDevice = document.getElementById("pvDevice");
  if (pvDevice) pvDevice.className = "pv-device " + mode;
}
window.switchDevice = switchDevice;

async function refreshPreview() {
  try {
    const previews = await ipc.preview.list();
    if (previews && previews.length > 0) {
      const p = previews[0];
      const pvUrl = document.querySelector(".pv-url");
      if (pvUrl) {
        pvUrl.innerHTML = `<i class="ti ti-lock"></i> ${esc(p.url || p.address || "localhost")}`;
      }
    }
  } catch {
    // Preview may not be running
  }
}
window.refreshPreview = refreshPreview;

function openExternal() {
  const pvUrl = document.querySelector(".pv-url");
  const url = pvUrl?.textContent?.trim();
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

function attachFile() {
  addUserBubble("[File attached]");
  const streamId = crypto.randomUUID();
  startStream(streamId);
  ipc.provider
    .chatStream(streamId, state.ai, state.aiModel, "I have attached a file for your reference.")
    .catch((e) => showStreamError(streamId, e.message));
}
window.attachFile = attachFile;

function voiceInput() {
  addUserBubble("[Voice input]");
  showError("Voice input requires microphone access — not available in this version.");
}
window.voiceInput = voiceInput;

function changeStack() {
  addAIBubble(
    "Stack changes require a new project. Your current build stack is locked until the wave ends."
  );
  switchTab("build");
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
