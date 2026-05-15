/**
 * secrets.js — Replit-style Secrets pane (Wave 1 / G0-6)
 *
 * Surfaces a real .env secrets manager: list, reveal (audited, 10s window),
 * edit, delete, single-add, and bulk-import via "Edit as .env" with diff
 * preview. All writes are atomic (temp + fsync + rename) on the Rust side.
 *
 * Spec: docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.4c
 */

import * as ipc from "./ipc.js";

const REVEAL_TIMEOUT_MS = 10_000;
const KEYCHAIN_NAMES = new Set([
  "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "QWEN_API_KEY",
  "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY",
  "CEREBRAS_API_KEY", "TOGETHER_API_KEY", "XAI_API_KEY",
]);

const state = {
  workspace: "",
  file: ".env.local",
  search: "",
  entries: [],
  revealed: new Map(),       // name → { value, expiresAt, timer }
  modalOpen: false,
  modalMode: null,           // "new" | "edit" | "bulk"
  modalEntry: null,
};

let dom = {};
let onToast = (m) => console.log(m);
let onSecretsChanged = () => {};

export function attachSecretsPane({ container, toast, onChanged }) {
  if (!container) return;
  dom = {
    fileSelect:  container.querySelector("#secretFile"),
    search:      container.querySelector("#secretsSearch"),
    summary:     container.querySelector("#secrets-summary"),
    list:        container.querySelector("#secretsList"),
    btnNew:      container.querySelector("#secNew"),
    btnBulk:     container.querySelector("#secBulk"),
    modal:       container.querySelector("#secretsModal"),
    modalTitle:  container.querySelector("#secretsModalTitle"),
    modalBody:   container.querySelector("#secretsModalBody"),
    modalClose:  container.querySelector("#secModalClose"),
    modalCancel: container.querySelector("#secModalCancel"),
    modalSave:   container.querySelector("#secModalSave"),
  };
  if (toast) onToast = toast;
  if (onChanged) onSecretsChanged = onChanged;
  bindEvents();
}

export function setWorkspace(path) {
  state.workspace = path || "";
  if (!path) {
    state.entries = [];
    state.revealed.forEach((r) => clearTimeout(r.timer));
    state.revealed.clear();
  }
  refresh();
}

export async function refresh() {
  if (!state.workspace) {
    if (dom.list) dom.list.innerHTML = `<div class="empty">Choose a project to manage secrets.</div>`;
    if (dom.summary) dom.summary.textContent = "Choose a project to manage secrets.";
    return;
  }
  try {
    state.entries = await ipc.secrets.list(state.file);
  } catch (e) {
    state.entries = [];
    if (dom.summary) dom.summary.textContent = `Could not read ${state.file}: ${e?.message || e}`;
  }
  renderList();
  renderSummary();
}

function bindEvents() {
  dom.fileSelect?.addEventListener("change", (e) => {
    state.file = e.target.value;
    state.search = "";
    if (dom.search) dom.search.value = "";
    refresh();
  });
  dom.search?.addEventListener("input", (e) => {
    state.search = e.target.value.trim().toLowerCase();
    renderList();
  });
  dom.btnNew?.addEventListener("click", () => openModal("new"));
  dom.btnBulk?.addEventListener("click", () => openModal("bulk"));
  dom.modalClose?.addEventListener("click", closeModal);
  dom.modalCancel?.addEventListener("click", closeModal);
  dom.modalSave?.addEventListener("click", onModalSave);
}

function renderSummary() {
  if (!dom.summary) return;
  const total = state.entries.length;
  const masked = state.entries.filter((e) => !e.public_prefix).length;
  const updated = state.entries.reduce((acc, e) => Math.max(acc, e.updated_at || 0), 0);
  const when = updated ? new Date(updated).toLocaleString() : "—";
  dom.summary.innerHTML = total
    ? `${total} secrets in <code>${esc(state.file)}</code> · ${masked} masked, ${total - masked} public · last write ${esc(when)}`
    : `<code>${esc(state.file)}</code> is empty.`;
}

function renderList() {
  if (!dom.list) return;
  if (!state.workspace) return;
  const filtered = state.search
    ? state.entries.filter((e) => e.name.toLowerCase().includes(state.search))
    : state.entries;

  if (!filtered.length) {
    dom.list.innerHTML = `
      <div class="empty">
        ${state.entries.length ? "No matches." : `<div><strong>No secrets in <code>${esc(state.file)}</code>.</strong><div>Click + New secret or Edit as .env to add some.</div></div>`}
      </div>`;
    return;
  }

  dom.list.innerHTML = filtered.map((entry, idx) => {
    const revealed = state.revealed.get(entry.name);
    const value = revealed ? revealed.value : entry.masked_value || "";
    const clash = KEYCHAIN_NAMES.has(entry.name);
    const lastEdit = entry.updated_at ? timeAgo(entry.updated_at) : "—";
    const status = entry.public_prefix
      ? `public-prefixed · not redacted in chat`
      : `used by AI: <span style="color:var(--green)">blocked</span>`;
    return `
      <div class="secret-card ${entry.public_prefix ? "public" : ""}" data-idx="${idx}">
        <div>
          <div class="secret-name">${esc(entry.name)}</div>
          <div class="secret-value">${esc(value)}</div>
          <div class="secret-info">
            updated ${esc(lastEdit)} · ${status}
            ${clash ? `<br><span class="warn">⚠ also stored in OS keychain — prefer the AI section for this key</span>` : ""}
            ${revealed ? `<br><span class="warn">🔓 revealed — auto-hides in ${Math.max(0, Math.ceil((revealed.expiresAt - Date.now()) / 1000))}s</span>` : ""}
          </div>
        </div>
        <div class="secret-buttons">
          ${entry.public_prefix
            ? `<button class="ghost small" data-act="copy" data-name="${esc(entry.name)}">Copy</button>`
            : `<button class="secondary small" data-act="reveal" data-name="${esc(entry.name)}">${revealed ? "Hide" : "Reveal"}</button>
               <button class="ghost small" data-act="copy" data-name="${esc(entry.name)}">Copy</button>`
          }
          <button class="ghost small" data-act="edit" data-name="${esc(entry.name)}">Edit</button>
          <button class="ghost small" data-act="delete" data-name="${esc(entry.name)}">×</button>
        </div>
      </div>
    `;
  }).join("");

  dom.list.querySelectorAll("[data-act]").forEach((btn) => {
    btn.addEventListener("click", () => handleAction(btn.dataset.act, btn.dataset.name));
  });
}

async function handleAction(action, name) {
  try {
    if (action === "reveal") {
      const existing = state.revealed.get(name);
      if (existing) {
        clearTimeout(existing.timer);
        state.revealed.delete(name);
        renderList();
        return;
      }
      const value = await ipc.secrets.reveal(name, state.file);
      const expiresAt = Date.now() + REVEAL_TIMEOUT_MS;
      const timer = setTimeout(() => { state.revealed.delete(name); renderList(); }, REVEAL_TIMEOUT_MS);
      state.revealed.set(name, { value, expiresAt, timer });
      renderList();
      // refresh countdown every second
      const tick = setInterval(() => {
        if (!state.revealed.has(name)) { clearInterval(tick); return; }
        renderList();
      }, 1000);
    } else if (action === "copy") {
      let value;
      const revealed = state.revealed.get(name);
      if (revealed) {
        value = revealed.value;
      } else if (state.entries.find((e) => e.name === name)?.public_prefix) {
        value = state.entries.find((e) => e.name === name).masked_value;
      } else {
        value = await ipc.secrets.reveal(name, state.file);
      }
      await navigator.clipboard.writeText(value);
      onToast(`Copied ${name}.`);
    } else if (action === "edit") {
      openModal("edit", state.entries.find((e) => e.name === name));
    } else if (action === "delete") {
      if (!confirm(`Remove ${name} from ${state.file}?`)) return;
      await ipc.secrets.delete(name, state.file);
      onToast(`${name} removed.`);
      await refresh();
      onSecretsChanged();
    }
  } catch (e) {
    onToast(e?.message || `Could not ${action} secret.`);
  }
}

// ─── Modal ────────────────────────────────────────────────────────────────────

function openModal(mode, entry = null) {
  if (!state.workspace) {
    onToast("Choose a project first.");
    return;
  }
  state.modalMode = mode;
  state.modalEntry = entry;
  state.modalOpen = true;
  dom.modal.hidden = false;
  dom.modalTitle.textContent = mode === "new" ? "New secret" : mode === "edit" ? `Edit ${entry?.name || ""}` : "Edit as .env";
  if (mode === "bulk") {
    dom.modalBody.innerHTML = `
      <p class="fine-print">Paste a <code>.env</code> block. SignalOS diffs against the current file and shows what will change. Nothing is written until you confirm.</p>
      <textarea id="bulk-env" class="env-textarea" placeholder="DATABASE_URL=postgres://...&#10;STRIPE_SECRET_KEY=sk_test_...&#10;NEXT_PUBLIC_API_URL=http://localhost:3000"></textarea>
      <div style="margin-top:10px"><button class="secondary small" type="button" id="bulk-diff">Compute diff</button></div>
      <div class="env-diff" id="bulk-diff-result" style="margin-top:10px; display:none"></div>
      <div class="env-confirm" id="bulk-confirm" style="display:none">
        <input type="checkbox" id="bulk-confirm-remove">
        <label for="bulk-confirm-remove">I understand that secrets will be removed.</label>
      </div>
    `;
    dom.modal.querySelector("#bulk-diff")?.addEventListener("click", computeBulkDiff);
  } else {
    const isEdit = mode === "edit" && entry;
    dom.modalBody.innerHTML = `
      <div class="wizard-field">
        <label for="sec-name">Name</label>
        <input id="sec-name" type="text" autocomplete="off" placeholder="DATABASE_URL" value="${esc(isEdit ? entry.name : "")}" ${isEdit ? "" : ""} />
        <div class="fine-print">UPPER_SNAKE_CASE. Letters, digits, underscores. Cannot start with a digit.</div>
      </div>
      <div class="wizard-field">
        <label for="sec-value">Value</label>
        <input id="sec-value" type="password" autocomplete="off" placeholder="${isEdit ? "(unchanged unless you type a new value)" : "Paste secret value"}" />
        <div class="fine-print">${isEdit ? "Leave blank to keep the current value." : "We won't display this back unless you click Reveal."}</div>
      </div>
      ${isEdit ? "" : `
      <div class="wizard-field">
        <label for="sec-file-modal">File</label>
        <select id="sec-file-modal">
          <option value=".env.local"${state.file === ".env.local" ? " selected" : ""}>.env.local</option>
          <option value=".env"${state.file === ".env" ? " selected" : ""}>.env</option>
          <option value=".env.development"${state.file === ".env.development" ? " selected" : ""}>.env.development</option>
          <option value=".env.production"${state.file === ".env.production" ? " selected" : ""}>.env.production</option>
          <option value=".env.test"${state.file === ".env.test" ? " selected" : ""}>.env.test</option>
        </select>
      </div>
      `}
    `;
  }
}

function closeModal() {
  state.modalOpen = false;
  dom.modal.hidden = true;
  dom.modalBody.innerHTML = "";
}

async function computeBulkDiff() {
  const text = dom.modal.querySelector("#bulk-env")?.value || "";
  const result = dom.modal.querySelector("#bulk-diff-result");
  const confirm = dom.modal.querySelector("#bulk-confirm");
  try {
    const diff = await ipc.secrets.applyDiff(state.file, text, false);
    result.style.display = "grid";
    confirm.style.display = diff.removed.length ? "flex" : "none";
    result.innerHTML = `
      ${diff.added.map((n) => `<div class="added">+ ${esc(n)}</div>`).join("")}
      ${diff.changed.map((n) => `<div class="changed">~ ${esc(n)}</div>`).join("")}
      ${diff.unchanged.map((n) => `<div class="unchanged">= ${esc(n)}</div>`).join("")}
      ${diff.removed.map((n) => `<div class="removed">− ${esc(n)} (will be removed)</div>`).join("")}
      ${(!diff.added.length && !diff.changed.length && !diff.removed.length)
        ? `<div class="unchanged">No changes.</div>` : ""}
    `;
    if (diff.applied) {
      onToast("Secrets updated.");
      closeModal();
      await refresh();
      onSecretsChanged();
    }
  } catch (e) {
    result.style.display = "grid";
    result.innerHTML = `<div class="removed">${esc(e?.message || e)}</div>`;
  }
}

async function onModalSave() {
  if (!state.workspace) return;
  if (state.modalMode === "bulk") {
    const text = dom.modal.querySelector("#bulk-env")?.value || "";
    const removeOk = dom.modal.querySelector("#bulk-confirm-remove")?.checked || false;
    try {
      const diff = await ipc.secrets.applyDiff(state.file, text, true);
      if (!diff.applied && diff.removed.length && !removeOk) {
        onToast(`${diff.removed.length} secrets would be removed. Check the box to confirm.`);
        return;
      }
      // Force-apply when user has consented
      const final = await ipc.secrets.applyDiff(state.file, text, removeOk || diff.removed.length === 0);
      if (final.applied) {
        onToast("Secrets applied.");
        closeModal();
        await refresh();
        onSecretsChanged();
      }
    } catch (e) {
      onToast(e?.message || "Could not apply diff.");
    }
    return;
  }

  const name = dom.modal.querySelector("#sec-name")?.value?.trim() || "";
  const value = dom.modal.querySelector("#sec-value")?.value || "";
  const file = state.modalMode === "edit"
    ? state.file
    : (dom.modal.querySelector("#sec-file-modal")?.value || state.file);

  if (!name) {
    onToast("Name is required.");
    return;
  }
  if (state.modalMode === "edit") {
    // Rename support: if name changed, delete old then upsert new.
    if (state.modalEntry && state.modalEntry.name !== name) {
      try { await ipc.secrets.delete(state.modalEntry.name, state.file); } catch (e) {
        onToast(`Could not rename: ${e?.message || e}`); return;
      }
    }
    if (!value) {
      // No value change requested. If we also didn't rename, this is a no-op.
      if (state.modalEntry && state.modalEntry.name === name) {
        onToast("Nothing to update."); return;
      }
      // If rename only, we already deleted; need a value to re-create.
      onToast("Re-typing the value is required when renaming.");
      return;
    }
  } else {
    if (!value) {
      onToast("Value is required.");
      return;
    }
  }
  try {
    await ipc.secrets.upsert(name, value, file);
    onToast(`${name} saved.`);
    closeModal();
    if (file !== state.file && state.modalMode !== "edit") {
      state.file = file;
      if (dom.fileSelect) dom.fileSelect.value = file;
    }
    await refresh();
    onSecretsChanged();
  } catch (e) {
    onToast(e?.message || "Could not save.");
  }
}

function esc(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function timeAgo(ts) {
  const diff = Date.now() - ts;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} min ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} h ago`;
  return `${Math.floor(diff / 86_400_000)} d ago`;
}
