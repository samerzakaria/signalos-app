/**
 * left-tabs.js — Files / Gov / Mem tabs in the left sidebar (§11.1 deeper UX).
 *
 * Three tabs that swap the content of the left rail:
 *   📁 Files — workspace file tree with diff badges (file-tree.js)
 *   ⚖  Gov   — current wave, gates strip, plan summary
 *   🧠 Mem   — recent Brain notes + recent audit entries
 *
 * Includes intent-driven auto-switch: when the chat composer sends a build
 * intent we jump to Files; gate/freeze/observe → Gov; remember/note → Mem.
 */

import * as ipc from "./ipc.js";
import { attachFileTree, refresh as refreshTree, markRecentBuild } from "./file-tree.js";

const TABS = ["files", "gov", "mem"];
let activeTab = "files";
let dom = {};

export function attachLeftTabs() {
  dom = {
    buttons: Array.from(document.querySelectorAll(".left-tab")),
    panels: {
      files: document.getElementById("leftPanelFiles"),
      gov:   document.getElementById("leftPanelGov"),
      mem:   document.getElementById("leftPanelMem"),
    },
    fileTreeHost: document.getElementById("leftFileTree"),
    govWave:  document.getElementById("leftGovWave"),
    govGates: document.getElementById("leftGovGates"),
    govPlan:  document.getElementById("leftGovPlan"),
    memNotes: document.getElementById("leftMemNotes"),
    memAudit: document.getElementById("leftMemAudit"),
  };
  if (!dom.buttons.length) return;
  attachFileTree({ container: dom.fileTreeHost });
  dom.buttons.forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.leftTab));
  });
}

export function switchTab(tab) {
  if (!TABS.includes(tab)) return;
  activeTab = tab;
  dom.buttons.forEach((btn) => {
    const active = btn.dataset.leftTab === tab;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  TABS.forEach((t) => {
    if (dom.panels[t]) dom.panels[t].hidden = t !== tab;
  });
  if (tab === "gov") refreshGov();
  else if (tab === "mem") refreshMem();
  else if (tab === "files") refreshTree();
}

export function autoSwitchForIntent(text) {
  const t = String(text || "").toLowerCase();
  if (/\b(build|make|create|add|fix|edit|generate)\b/.test(t)) return switchTab("files");
  if (/\b(sign|gate|freeze|unfreeze|pause|observe|wave|status|onboard)\b/.test(t)) return switchTab("gov");
  if (/\b(remember|note|brain|audit|history|debrief|review|retrospective)\b/.test(t)) return switchTab("mem");
}

export function notifyBuildCompleted(filePaths) {
  markRecentBuild(filePaths || []);
}

async function refreshGov() {
  try {
    const enf = await ipc.enforcement.state();
    const required = new Set((enf.required_gates || []).map((g) => "G" + g));
    const signed = new Set((enf.signed_gates || []).map((g) => "G" + g));
    let firstUnsigned = null;
    const allGates = ["G0", "G1", "G2", "G3", "G4", "G5"];
    const items = allGates.map((g) => {
      const isSigned = signed.has(g);
      const isRequired = required.has(g);
      let cls = "locked";
      let label = "○";
      if (isSigned) { cls = "signed"; label = "✓"; }
      else if (isRequired && !firstUnsigned) { cls = "current"; label = "▶"; firstUnsigned = g; }
      return `<div class="left-gov-gate ${cls}">${label} <span>${g}</span></div>`;
    }).join("");
    if (dom.govGates) dom.govGates.innerHTML = items;
    if (dom.govWave) {
      dom.govWave.textContent = enf.wave_frozen
        ? "Wave frozen — Build blocked"
        : `Wave active · ${signed.size}/${required.size} required gates signed`;
    }
  } catch (e) {
    if (dom.govGates) dom.govGates.innerHTML = `<div class="fine-print">${escape(e?.message || e)}</div>`;
  }
  try {
    const plan = await ipc.project.readFile("core/strategy/PLAN.md");
    if (dom.govPlan) {
      const firstLines = String(plan || "").split("\n").slice(0, 8).join("\n").trim();
      dom.govPlan.innerHTML = firstLines
        ? `<pre style="font-size:10px;color:var(--muted);white-space:pre-wrap;margin:0">${escape(firstLines)}</pre>`
        : `<div class="fine-print">No plan content.</div>`;
    }
  } catch {
    if (dom.govPlan) dom.govPlan.innerHTML = `<div class="fine-print">No PLAN.md yet.</div>`;
  }
}

async function refreshMem() {
  try {
    const notes = await ipc.brain.search("");
    if (dom.memNotes) {
      if (!Array.isArray(notes) || !notes.length) {
        dom.memNotes.innerHTML = `<div class="fine-print">No notes yet.</div>`;
      } else {
        dom.memNotes.innerHTML = notes.slice(0, 6).map((n) => `
          <div class="left-mem-entry">
            <div>${escape(String(n.text || "").slice(0, 120))}</div>
            <div class="meta">${escape(n.type || "note")} · ${escape(n.ts || "")}</div>
          </div>
        `).join("");
      }
    }
  } catch {
    if (dom.memNotes) dom.memNotes.innerHTML = `<div class="fine-print">No notes (workspace not set?).</div>`;
  }
  try {
    const audit = await ipc.audit.list(8);
    if (dom.memAudit) {
      if (!Array.isArray(audit) || !audit.length) {
        dom.memAudit.innerHTML = `<div class="fine-print">No audit entries.</div>`;
      } else {
        dom.memAudit.innerHTML = audit.slice(0, 8).map((a) => `
          <div class="left-mem-entry">
            <div>${escape(a.action || a.event || "entry")}</div>
            <div class="meta">${escape(a.detail || a.message || "")} · ${escape(a.ts || "")}</div>
          </div>
        `).join("");
      }
    }
  } catch {
    if (dom.memAudit) dom.memAudit.innerHTML = `<div class="fine-print">No audit entries.</div>`;
  }
}

function escape(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}
