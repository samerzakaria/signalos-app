/**
 * plan-reader.js — PLAN.md parser for the SignalOS desktop app (T3)
 *
 * Parses the PLAN.md format produced by /signal-plan into structured
 * task objects that the dashboard and governance views can render.
 *
 * PLAN.md format (SignalOS standard):
 *
 *   ## Phase 3 — Build
 *
 *   ### Task: Implement user authentication
 *   Trust Tier: T2
 *   Owner: agent:pe
 *   Status: in-progress
 *   Defer: false
 *
 *   Description of the task...
 *
 *   ---
 */

// ─── TYPES ────────────────────────────────────────────────────────────────────

/**
 * @typedef {Object} PlanTask
 * @property {string}   id
 * @property {string}   title
 * @property {string}   phase
 * @property {'T1'|'T2'|'T3'} tier
 * @property {string}   owner
 * @property {'pending'|'in-progress'|'done'|'deferred'} status
 * @property {boolean}  defer
 * @property {string}   description
 */

/**
 * @typedef {Object} PlanSummary
 * @property {string}     waveName
 * @property {PlanTask[]} tasks
 * @property {Object}     stats
 */

// ─── PARSER ───────────────────────────────────────────────────────────────────

/**
 * Parse a PLAN.md string into a structured PlanSummary.
 * @param {string} markdown
 * @returns {PlanSummary}
 */
export function parsePlan(markdown) {
  const lines      = markdown.split("\n");
  const tasks      = [];
  let   currentPhase = "Unknown";
  let   waveName     = "Wave";
  let   currentTask  = null;
  let   descLines    = [];

  const flushTask = () => {
    if (currentTask) {
      currentTask.description = descLines.join("\n").trim();
      tasks.push(currentTask);
      currentTask = null;
      descLines   = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Wave name from H1
    if (line.startsWith("# ")) {
      waveName = line.slice(2).trim();
      continue;
    }

    // Phase from H2
    if (line.startsWith("## ")) {
      flushTask();
      currentPhase = line.slice(3).trim();
      continue;
    }

    // Task from H3
    if (line.startsWith("### Task:")) {
      flushTask();
      currentTask = {
        id:          `task-${tasks.length + 1}`,
        title:       line.slice("### Task:".length).trim(),
        phase:       currentPhase,
        tier:        "T2",
        owner:       "agent",
        status:      "pending",
        defer:       false,
        description: "",
      };
      continue;
    }

    if (!currentTask) continue;

    // Metadata fields
    const tierMatch   = line.match(/^Trust Tier:\s*(T[123])/i);
    const ownerMatch  = line.match(/^Owner:\s*(.+)/i);
    const statusMatch = line.match(/^Status:\s*(.+)/i);
    const deferMatch  = line.match(/^Defer:\s*(true|false)/i);

    if (tierMatch)   { currentTask.tier   = tierMatch[1].toUpperCase(); continue; }
    if (ownerMatch)  { currentTask.owner  = ownerMatch[1].trim();       continue; }
    if (deferMatch)  { currentTask.defer  = deferMatch[1] === "true";   continue; }
    if (statusMatch) {
      const s = statusMatch[1].trim().toLowerCase();
      currentTask.status = s === "in progress" ? "in-progress" : s;
      continue;
    }

    // Separator
    if (line.trim() === "---") { flushTask(); continue; }

    // Description accumulation
    descLines.push(line);
  }

  flushTask();

  return { waveName, tasks, stats: computeStats(tasks) };
}

// ─── STATS ────────────────────────────────────────────────────────────────────

function computeStats(tasks) {
  const total    = tasks.length;
  const done     = tasks.filter(t => t.status === "done").length;
  const active   = tasks.filter(t => t.status === "in-progress").length;
  const deferred = tasks.filter(t => t.defer || t.status === "deferred").length;
  const byTier   = { T1: 0, T2: 0, T3: 0 };
  tasks.forEach(t => { if (byTier[t.tier] !== undefined) byTier[t.tier]++; });

  return { total, done, active, deferred, byTier, pct: total ? Math.round((done / total) * 100) : 0 };
}

// ─── RENDERER ─────────────────────────────────────────────────────────────────

const TIER_COLORS = { T1: "#dc2626", T2: "#d97706", T3: "#6b7280" };
const TIER_BG     = { T1: "#fee2e2", T2: "#fef3c7", T3: "#f3f4f6" };

const STATUS_ICON = {
  "pending":     "○",
  "in-progress": "◉",
  "done":        "✓",
  "deferred":    "⊘",
};

/**
 * Render a PlanSummary into a container element.
 * @param {PlanSummary} plan
 * @param {HTMLElement} container
 */
export function renderPlan(plan, container) {
  if (!plan.tasks.length) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">📝</div>
        <h3>No PLAN.md yet</h3>
        <p>Run <span class="cmd-pill">/signal-plan</span> to generate your wave plan.</p>
      </div>`;
    return;
  }

  // Group by phase
  const phases = {};
  plan.tasks.forEach(t => {
    if (!phases[t.phase]) phases[t.phase] = [];
    phases[t.phase].push(t);
  });

  const statsHtml = `
    <div style="display:flex;gap:10px;margin-bottom:18px;flex-wrap:wrap">
      ${statChip(plan.stats.done + "/" + plan.stats.total, "Tasks done")}
      ${statChip(plan.stats.active, "In progress")}
      ${statChip(plan.stats.deferred, "Deferred")}
      ${statChip(plan.stats.pct + "%", "Complete")}
    </div>`;

  const phasesHtml = Object.entries(phases).map(([phase, tasks]) => `
    <div style="margin-bottom:20px">
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--text-3);margin-bottom:8px">${phase}</div>
      ${tasks.map(taskCard).join("")}
    </div>`).join("");

  container.innerHTML = statsHtml + phasesHtml;
}

function statChip(value, label) {
  return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 14px;text-align:center;min-width:80px">
    <div style="font-size:18px;font-weight:800;color:var(--accent)">${value}</div>
    <div style="font-size:10px;color:var(--text-3)">${label}</div>
  </div>`;
}

function taskCard(task) {
  const icon  = STATUS_ICON[task.status] || "○";
  const color = TIER_COLORS[task.tier]   || "#6b7280";
  const bg    = TIER_BG[task.tier]       || "#f3f4f6";
  const done  = task.status === "done";

  return `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px;margin-bottom:6px;display:flex;align-items:flex-start;gap:10px;${done ? "opacity:.6" : ""}">
      <span style="font-size:14px;margin-top:1px;flex-shrink:0">${icon}</span>
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;font-weight:600;color:var(--text-1);${done ? "text-decoration:line-through" : ""}">${task.title}</div>
        ${task.description ? `<div style="font-size:11.5px;color:var(--text-3);margin-top:3px;line-height:1.5">${task.description}</div>` : ""}
        <div style="font-size:10px;color:var(--text-3);margin-top:4px">${task.owner}</div>
      </div>
      <span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:99px;background:${bg};color:${color};flex-shrink:0">${task.tier}</span>
    </div>`;
}
