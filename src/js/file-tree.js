/**
 * file-tree.js — File tree with diff badges (§11.5/8).
 *
 * Walks the workspace via list_workspace_dir. Nodes that match files written
 * by the last Builder run get a "new" or "modified" badge. Click a directory
 * to expand/collapse; click a file to open it in the OS default app.
 *
 * Spec: docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.5/8
 */

import * as ipc from "./ipc.js";

const state = {
  expanded: new Set(["."]),   // path -> expanded
  entries: new Map(),          // path -> WorkspaceEntry[]
  loading: new Set(),
  recentBuilds: [],            // recently-written paths (from the Builder)
  filter: "",
};

let dom = {};
let onToast = (m) => console.log(m);

export function attachFileTree({ container, toast }) {
  if (!container) return;
  dom = { container };
  if (toast) onToast = toast;
  renderShell();
}

export function markRecentBuild(filePaths) {
  state.recentBuilds = Array.isArray(filePaths) ? filePaths.slice() : [];
  // Invalidate cached listings so badges refresh.
  state.entries.clear();
  refresh();
}

export async function refresh() {
  state.entries.clear();
  await loadDir(".");
  render();
}

function renderShell() {
  dom.container.innerHTML = `
    <div class="file-tree">
      <div class="file-tree-toolbar">
        <input id="ftree-filter" placeholder="Filter…" />
        <button class="ghost small" id="ftree-refresh">Refresh</button>
      </div>
      <div class="file-tree-body" id="ftree-body"></div>
    </div>
  `;
  dom.body = dom.container.querySelector("#ftree-body");
  dom.filter = dom.container.querySelector("#ftree-filter");
  dom.refreshBtn = dom.container.querySelector("#ftree-refresh");
  dom.filter.addEventListener("input", (e) => {
    state.filter = e.target.value.trim().toLowerCase();
    render();
  });
  dom.refreshBtn.addEventListener("click", refresh);
  loadDir(".").then(render);
}

async function loadDir(relPath) {
  if (state.loading.has(relPath)) return;
  state.loading.add(relPath);
  try {
    const list = await ipc.project.listDir(relPath);
    state.entries.set(relPath, Array.isArray(list) ? list : []);
  } catch (e) {
    state.entries.set(relPath, []);
  } finally {
    state.loading.delete(relPath);
  }
}

function render() {
  if (!dom.body) return;
  const root = state.entries.get(".") || [];
  if (root.length === 0 && !state.loading.has(".")) {
    dom.body.innerHTML = `<div class="empty compact-empty">No files in this folder.</div>`;
    return;
  }
  dom.body.innerHTML = renderEntries(root, 0).join("");
  // Wire clicks once after each render.
  dom.body.querySelectorAll("[data-tree-toggle]").forEach((el) => {
    el.addEventListener("click", async () => {
      const path = el.dataset.treeToggle;
      if (state.expanded.has(path)) {
        state.expanded.delete(path);
      } else {
        state.expanded.add(path);
        if (!state.entries.has(path)) await loadDir(path);
      }
      render();
    });
  });
  dom.body.querySelectorAll("[data-tree-open]").forEach((el) => {
    el.addEventListener("click", () => {
      ipc.project.openPath(el.dataset.treeOpen).catch((e) =>
        onToast(e?.message || "Could not open file.")
      );
    });
  });
}

function renderEntries(entries, depth) {
  return entries.flatMap((entry) => {
    const matches = !state.filter || entry.name.toLowerCase().includes(state.filter);
    const isExpanded = state.expanded.has(entry.path);
    const badge = badgeFor(entry.path);
    if (entry.kind === "dir") {
      const childHtml = isExpanded
        ? renderEntries(state.entries.get(entry.path) || [], depth + 1)
        : [];
      // Show the directory even if it doesn't match the filter, as long as a
      // child does.
      const childMatches = childHtml.length > 0;
      if (!matches && !childMatches && state.filter) return [];
      return [
        `<div class="ftree-row" style="padding-left:${depth * 14 + 6}px">
           <button type="button" class="ftree-toggle" data-tree-toggle="${escape(entry.path)}">
             <span class="ftree-chevron">${isExpanded ? "▾" : "▸"}</span>
             <span class="ftree-name">${escape(entry.name)}/</span>
           </button>
         </div>`,
        ...childHtml,
      ];
    }
    if (!matches && state.filter) return [];
    const sizeText = entry.bytes != null ? bytesLabel(entry.bytes) : "";
    return [
      `<div class="ftree-row" style="padding-left:${depth * 14 + 22}px">
         <button type="button" class="ftree-file" data-tree-open="${escape(entry.path)}">
           <span class="ftree-name">${escape(entry.name)}</span>
           ${badge ? `<span class="ftree-badge ${badge.cls}">${badge.label}</span>` : ""}
           <span class="ftree-size">${sizeText}</span>
         </button>
       </div>`,
    ];
  });
}

function badgeFor(path) {
  if (!state.recentBuilds.length) return null;
  if (state.recentBuilds.includes(path)) return { label: "new/mod", cls: "new" };
  return null;
}

function bytesLabel(b) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}

function escape(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}
