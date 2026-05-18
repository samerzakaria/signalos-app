/**
 * test-debt.js — Test Debt UI (Wave 5 / G4 rule 11 — zero manual regression).
 *
 * Renders the .signalos/test-debt.jsonl store in the History view and lets
 * the user add a manual-defect entry (which becomes the source of an
 * automated test that must be written before the fix lands).
 *
 * Spec: docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.4d rule 11
 */

import * as ipc from "./ipc.js";

let dom = {};
let onToast = (m) => console.log(m);

export function attachTestDebt({ container, toast }) {
  if (!container) return;
  dom = {
    summary: container.querySelector("#testDebtSummary"),
    list:    container.querySelector("#testDebtList"),
    addBtn:  container.querySelector("#testDebtAdd"),
    refresh: container.querySelector("#testDebtRefresh"),
  };
  if (toast) onToast = toast;
  dom.addBtn?.addEventListener("click", openAddModal);
  dom.refresh?.addEventListener("click", refreshList);
  refreshList();
}

export async function refreshList() {
  if (!dom.list) return;
  let summary = null;
  try {
    summary = await ipc.testAutomation.listDebt();
  } catch {
    dom.list.innerHTML = `<div class="empty">Choose a project to view test debt.</div>`;
    if (dom.summary) dom.summary.textContent = "Test debt: —";
    return;
  }
  const open = summary.open_count || 0;
  const resolved = summary.resolved_count || 0;
  if (dom.summary) {
    dom.summary.innerHTML = open === 0
      ? `<span style="color:var(--green)">✓ No open test debt</span> · ${resolved} resolved`
      : `<span style="color:var(--amber)">${open} open test debt item${open === 1 ? "" : "s"}</span> · ${resolved} resolved`;
  }
  const entries = summary.entries || [];
  if (entries.length === 0) {
    dom.list.innerHTML = `<div class="empty">No test debt entries yet. Use + Report a defect when you find one manually.</div>`;
    return;
  }
  dom.list.innerHTML = entries.map((e) => `
    <div class="secret-card ${e.resolved ? "public" : ""}">
      <div>
        <div class="secret-name">${escapeHtml(e.title)}</div>
        <div class="secret-value">${escapeHtml(e.detail || "")}</div>
        <div class="secret-info">${escapeHtml(e.kind)} · ${escapeHtml(e.area || "—")} · ${escapeHtml(e.ts || "")}${e.resolved ? " · <span style='color:var(--green)'>resolved</span>" : ""}</div>
      </div>
      <div class="secret-buttons">
        ${e.resolved ? "" : `<button class="secondary small" data-resolve="${escapeAttr(e.title)}">Resolve</button>`}
      </div>
    </div>
  `).join("");
  dom.list.querySelectorAll("[data-resolve]").forEach((b) => {
    b.addEventListener("click", async () => {
      try {
        await ipc.testAutomation.resolveDebt(b.dataset.resolve);
        onToast("Marked resolved.");
        await refreshList();
      } catch (e) { onToast(e?.message || "Could not resolve."); }
    });
  });
}

function openAddModal() {
  const host = document.createElement("div");
  host.className = "override-modal";
  host.innerHTML = `
    <div class="wizard-card" style="width:min(540px,100%)">
      <header class="wizard-header">
        <div class="wizard-title">Report a manually-found defect</div>
        <button class="wizard-skip" type="button" data-act="cancel">Close</button>
      </header>
      <div class="wizard-body">
        <p class="fine-print">SignalOS rule 11 (zero manual regression): every manually-found defect must become an automated test before the fix merges. Logging it here makes that promise.</p>
        <div class="wizard-field">
          <label for="td-title">Title</label>
          <input id="td-title" type="text" placeholder="Short defect summary" />
        </div>
        <div class="wizard-field">
          <label for="td-area">Area / file glob</label>
          <input id="td-area" type="text" placeholder="src/utils/dates.ts" />
        </div>
        <div class="wizard-field">
          <label for="td-detail">Repro + expected behavior</label>
          <textarea id="td-detail" rows="4" placeholder="Steps to reproduce and what the code should have done."></textarea>
        </div>
      </div>
      <footer class="wizard-footer">
        <button class="ghost" type="button" data-act="cancel">Cancel</button>
        <button class="primary" type="button" data-act="save">Log test debt</button>
      </footer>
    </div>
  `;
  document.body.appendChild(host);
  host.querySelectorAll("[data-act]").forEach((b) => {
    b.addEventListener("click", async () => {
      if (b.dataset.act === "cancel") { host.remove(); return; }
      const title = host.querySelector("#td-title").value.trim();
      const area = host.querySelector("#td-area").value.trim();
      const detail = host.querySelector("#td-detail").value.trim();
      if (!title) { onToast("Title is required."); return; }
      try {
        await ipc.testAutomation.addDebt("manual-defect", area, title, detail);
        host.remove();
        onToast("Test debt logged.");
        await refreshList();
      } catch (e) { onToast(e?.message || "Could not log."); }
    });
  });
}

function escapeHtml(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}
function escapeAttr(v) { return escapeHtml(v); }
