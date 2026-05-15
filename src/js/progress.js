/**
 * progress.js — Style A progress renderer (Wave 2 / G1-8)
 *
 * Listens to `sidecar:progress` events from the Rust multiplexer and
 * renders per-phase, per-substep progress with live updates. Each substep
 * has 4 states: pending (○), running (▶), done (✓), error (✕).
 *
 * The phase contract for a command is fetched once at start via
 * `phase:contract <name>` so the strip renders rows up-front in pending state.
 *
 * Spec: docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.2
 */

import * as ipc from "./ipc.js";

const sessions = new Map(); // reqId → { hostId, contract, states }

const STATE_ICON = {
  pending: "○",
  running: "▶",
  done:    "✓",
  error:   "✕",
};

let listenerAttached = false;

export async function startProgress({ reqId, hostId, contractName }) {
  ensureListener();
  let contract = null;
  try {
    const result = await ipc.invokeProgressContract(contractName);
    contract = result?.phases || null;
  } catch {
    contract = null;
  }
  sessions.set(reqId, {
    hostId,
    contract,
    states: {}, // `${phase}:${substep}` → state
    activePhase: contract ? contract[0][0] : "",
    started: Date.now(),
  });
  render(reqId);
}

export function endProgress(reqId, ok = true) {
  const s = sessions.get(reqId);
  if (!s) return;
  // Mark all running substeps as done/error.
  if (s.contract) {
    for (const [phase, substeps] of s.contract) {
      for (const sub of substeps) {
        const key = `${phase}:${sub}`;
        if (s.states[key] === "running") {
          s.states[key] = ok ? "done" : "error";
        }
      }
    }
  }
  render(reqId);
  setTimeout(() => sessions.delete(reqId), 4000);
}

function ensureListener() {
  if (listenerAttached) return;
  listenerAttached = true;
  ipc.onSidecarProgress?.((p) => {
    const s = sessions.get(p.id);
    if (!s) return;
    s.states[`${p.phase}:${p.substep}`] = p.state;
    if (p.state === "running") s.activePhase = p.phase;
    s.lastDetail = p.detail || "";
    render(p.id);
  });
}

function render(reqId) {
  const s = sessions.get(reqId);
  if (!s) return;
  const host = document.getElementById(s.hostId);
  if (!host) return;

  const elapsed = Math.max(1, Math.floor((Date.now() - s.started) / 1000));
  const elapsedStr = elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed/60)}m ${elapsed%60}s`;

  if (!s.contract) {
    host.innerHTML = `<div class="progress-strip"><div class="progress-running">Working… (${elapsedStr})</div></div>`;
    return;
  }

  const totalSubsteps = s.contract.reduce((a, [, subs]) => a + subs.length, 0);
  const doneCount = Object.values(s.states).filter((v) => v === "done").length;
  const pct = Math.round((doneCount / totalSubsteps) * 100);

  let html = `<div class="progress-strip">`;
  html += `<div class="progress-bar"><div class="progress-bar-fill" style="width:${pct}%"></div></div>`;
  html += `<div class="progress-meta">${doneCount} / ${totalSubsteps} substeps · ${elapsedStr}</div>`;

  for (const [phase, substeps] of s.contract) {
    const allDone = substeps.every((sub) => s.states[`${phase}:${sub}`] === "done");
    const anyRunning = substeps.some((sub) => s.states[`${phase}:${sub}`] === "running");
    const anyError = substeps.some((sub) => s.states[`${phase}:${sub}`] === "error");
    const phaseState = anyError ? "error" : allDone ? "done" : anyRunning ? "running" : "pending";

    html += `<div class="progress-phase ${phaseState}">`;
    html += `<div class="progress-phase-title">${STATE_ICON[phaseState]} ${escapeHtml(phaseTitle(phase))}</div>`;
    html += `<div class="progress-substeps">`;
    for (const sub of substeps) {
      const st = s.states[`${phase}:${sub}`] || "pending";
      const cls = `progress-substep ${st}`;
      html += `<div class="${cls}"><span class="ps-icon">${STATE_ICON[st]}</span><span>${escapeHtml(substepTitle(sub))}</span></div>`;
    }
    html += `</div></div>`;
  }
  if (s.lastDetail) {
    html += `<div class="progress-detail">${escapeHtml(s.lastDetail)}</div>`;
  }
  html += `</div>`;
  host.innerHTML = html;
}

function phaseTitle(id) {
  return ({
    prepare: "Prepare",
    plan:    "Plan",
    build:   "Build",
    review:  "Review",
    write:   "Write",
    read:    "Read",
    render:  "Render",
  })[id] || id;
}

function substepTitle(id) {
  // turn snake_case into "Snake case"
  return id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function escapeHtml(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}
