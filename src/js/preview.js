/**
 * preview.js — Iframe preview pane + Run controller (Wave 2 / G1-11)
 *
 * Listens for `preview:event` from the Rust LocalProcessSupervisor, drives
 * the right-pane iframe to the captured localhost port, and exposes
 * Run / Pause / Restart / Stop. Run log is collapsible.
 *
 * Spec: docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.3
 */

import * as ipc from "./ipc.js";

const stackById = {
  "react-vite": "React / Vite",
  next: "Next.js",
  "node-express": "Node / Express",
  "python-flask": "Python / Flask",
  static: "Plain HTML",
};

const state = {
  workspace: "",
  stack: "react-vite",
  key: "",
  status: "stopped",
  url: null,
  log: [],
  logOpen: false,
  nodeProbe: null,
  unsubscribe: null,
};

let dom = {};
let onToast = (m) => console.log(m);

export function attachPreviewPane({ container, toast }) {
  if (!container) return;
  dom.container = container;
  onToast = toast || onToast;
  renderShell();
  bindEvents();
  // Subscribe once
  if (state.unsubscribe) state.unsubscribe();
  state.unsubscribe = ipc.onPreviewEvent((evt) => {
    if (!evt || !state.key || evt.key !== state.key) return;
    state.log.push(evt);
    if (state.log.length > 300) state.log = state.log.slice(-200);
    if (evt.kind === "port" && evt.message.startsWith("http://")) {
      state.url = evt.message;
      state.status = "running";
    }
    if (evt.kind === "status") {
      const m = evt.message.toLowerCase();
      if (m.includes("install")) state.status = "installing";
      else if (m.includes("start")) state.status = "starting";
      else if (m.includes("stopping")) state.status = "stopped";
    }
    if (evt.kind === "exit") state.status = "stopped";
    if (evt.kind === "error") state.status = "error";
    render();
  });
}

export function setWorkspace(path) {
  state.workspace = path || "";
  if (!path) {
    state.key = "";
    state.url = null;
    state.status = "stopped";
    state.log = [];
  }
  render();
}

export function setStack(id) {
  if (stackById[id]) state.stack = id;
  render();
}

function renderShell() {
  dom.container.innerHTML = `
    <div class="preview-pane">
      <div class="preview-toolbar">
        <div>
          <div class="preview-title" id="prev-title">Preview</div>
          <div class="preview-status" id="prev-status">No app running</div>
        </div>
        <div>
          <select id="prev-stack" title="Run stack">
            ${Object.entries(stackById).map(([id, label]) =>
              `<option value="${id}">${label}</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="preview-frame-wrap" id="prev-frame-wrap">
        <div class="preview-empty" id="prev-empty">
          <div>
            <strong>No preview yet.</strong>
            <div>Click ▶ Run to install dependencies and start the dev server.</div>
          </div>
        </div>
      </div>
      <div class="preview-controls">
        <button class="primary small" id="prev-run">▶ Run</button>
        <button class="secondary small" id="prev-reload">↻ Reload</button>
        <button class="ghost small" id="prev-stop">⏏ Stop</button>
        <button class="ghost small" id="prev-open-browser">Open in browser</button>
        <button class="ghost small" id="prev-open-folder">Open folder</button>
        <button class="ghost small" id="prev-open-ide">Open in VS Code</button>
        <button class="ghost small" id="prev-toggle-log">▾ Run log</button>
      </div>
      <pre class="preview-log" id="prev-log" hidden></pre>
    </div>
  `;
  dom.stack = dom.container.querySelector("#prev-stack");
  dom.status = dom.container.querySelector("#prev-status");
  dom.frameWrap = dom.container.querySelector("#prev-frame-wrap");
  dom.empty = dom.container.querySelector("#prev-empty");
  dom.btnRun = dom.container.querySelector("#prev-run");
  dom.btnReload = dom.container.querySelector("#prev-reload");
  dom.btnStop = dom.container.querySelector("#prev-stop");
  dom.btnBrowser = dom.container.querySelector("#prev-open-browser");
  dom.btnFolder = dom.container.querySelector("#prev-open-folder");
  dom.btnIDE = dom.container.querySelector("#prev-open-ide");
  dom.btnToggleLog = dom.container.querySelector("#prev-toggle-log");
  dom.log = dom.container.querySelector("#prev-log");
}

function bindEvents() {
  dom.stack?.addEventListener("change", (e) => { state.stack = e.target.value; render(); });
  dom.btnRun?.addEventListener("click", onRun);
  dom.btnReload?.addEventListener("click", onReload);
  dom.btnStop?.addEventListener("click", onStop);
  dom.btnBrowser?.addEventListener("click", openInBrowser);
  dom.btnFolder?.addEventListener("click", openFolder);
  dom.btnIDE?.addEventListener("click", openInIDE);
  dom.btnToggleLog?.addEventListener("click", () => {
    state.logOpen = !state.logOpen;
    render();
  });
}

function render() {
  if (!dom.container) return;
  dom.stack.value = state.stack;
  dom.status.textContent =
    state.status === "running"    ? `Running · ${state.url || ""}`    :
    state.status === "installing" ? "Installing dependencies…"        :
    state.status === "starting"   ? "Starting dev server…"            :
    state.status === "error"      ? "Errored — check log"             :
                                    "No app running";
  dom.status.className = `preview-status ${state.status}`;

  // Frame
  if (state.url) {
    dom.frameWrap.innerHTML = `<iframe class="preview-frame" id="prev-iframe" src="${escapeAttr(state.url)}" sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"></iframe>`;
  } else {
    dom.frameWrap.innerHTML = `<div class="preview-empty"><div><strong>No preview yet.</strong><div>${state.workspace ? "Click ▶ Run to install dependencies and start the dev server." : "Choose a project folder first."}</div></div></div>`;
  }

  // Buttons enabled/disabled
  dom.btnRun.disabled = !state.workspace || state.status === "installing" || state.status === "starting";
  dom.btnReload.disabled = !state.url;
  dom.btnStop.disabled = state.status === "stopped";
  dom.btnBrowser.disabled = !state.url;
  dom.btnFolder.disabled = !state.workspace;
  dom.btnIDE.disabled = !state.workspace;
  dom.btnToggleLog.textContent = `${state.logOpen ? "▾" : "▸"} Run log (${state.log.length})`;

  // Log
  if (state.logOpen) {
    dom.log.hidden = false;
    dom.log.innerHTML = state.log.slice(-100).map((e) =>
      `<div class="${escapeAttr(e.kind || "stdout")}">${escapeHtml(e.message)}</div>`
    ).join("");
    dom.log.scrollTop = dom.log.scrollHeight;
  } else {
    dom.log.hidden = true;
  }
}

// ─── Actions ──────────────────────────────────────────────────────────────────

async function onRun() {
  if (!state.workspace) {
    onToast("Choose a project folder first.");
    return;
  }
  state.log = [];
  state.url = null;
  state.status = "installing";
  render();
  try {
    // Probe Node for JS stacks before spawning anything that will fail loudly
    if (["react-vite", "next", "node-express"].includes(state.stack)) {
      const probe = await ipc.preview.probeNode();
      state.nodeProbe = probe;
      if (!probe?.found) {
        onToast(probe?.message || "Node not found.");
        state.status = "error";
        state.logOpen = true;
        state.log.push({ kind: "stderr", message: probe?.message || "Node not found.", ts_ms: Date.now() });
        render();
        return;
      }
      if (probe?.major && probe.major < 18) {
        onToast(`Node ${probe.version} is too old for ${state.stack}. Install Node 18+.`);
      }
    }
    const rt = await ipc.preview.start(state.stack, state.workspace);
    state.key = rt.key;
    state.status = rt.status || "starting";
    state.url = rt.url || null;
    render();
  } catch (e) {
    state.status = "error";
    state.log.push({ kind: "stderr", message: e?.message || String(e), ts_ms: Date.now() });
    state.logOpen = true;
    render();
  }
}

function onReload() {
  const iframe = dom.container.querySelector("#prev-iframe");
  if (iframe && state.url) iframe.src = state.url + (state.url.includes("?") ? "&" : "?") + "_t=" + Date.now();
}

async function onStop() {
  if (!state.key) {
    state.status = "stopped";
    state.url = null;
    render();
    return;
  }
  try { await ipc.preview.stop(state.key); } catch {}
  state.status = "stopped";
  state.url = null;
  render();
}

function openInBrowser() {
  if (state.url) {
    try { window.open(state.url, "_blank", "noopener"); } catch { onToast("Could not open browser."); }
  }
}

function openFolder() {
  // Reuse the workspace path opener — opens the workspace root externally.
  // ipc.project.openPath("") would error; instead use empty path → opens root.
  ipc.project.openPath?.(".").catch(() => onToast("Could not open folder."));
}

function openInIDE() {
  // Best-effort: invoke `code <workspace>` via Tauri's shell plugin.
  const sh = window.__TAURI__?.shell;
  if (sh?.Command) {
    try {
      const cmd = new sh.Command("code", [state.workspace]);
      cmd.execute().catch(() => onToast("VS Code not detected. Install `code` on PATH or open the folder manually."));
    } catch {
      onToast("VS Code not detected. Install `code` on PATH or open the folder manually.");
    }
  } else {
    onToast("VS Code launch requires the Tauri shell plugin.");
  }
}

function escapeHtml(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}
function escapeAttr(v) { return escapeHtml(v); }
