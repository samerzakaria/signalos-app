/**
 * wired-commands.js — Real implementations of every catalog command that
 * previously dumped a markdown spec instead of doing work.
 *
 * Two flavors:
 *   1. State commands (pause/freeze/unfreeze/observe/onboard) — pure local
 *      operations against project state. No AI, just IPC + audit.
 *   2. Document-generating commands (discovery/design/design-html/
 *      design-review/debrief/pre-design/pre-wave/review/ship/wave-review)
 *      — single AI call with a command-specific prompt template, writes the
 *      result to .signalos/<kind>/<command>-<ts>.md, audit-logs, returns the
 *      summary. Same plumbing as Builder, smaller scope.
 *
 * Spec: docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.4 (no more "Preview" labels).
 */

import * as ipc from "./ipc.js";

// ─── Command set ──────────────────────────────────────────────────────────────

export const STATE_COMMANDS = new Set([
  "/signal-pause",
  "/signal-freeze",
  "/signal-unfreeze",
  "/signal-observe",
  "/signal-onboard",
]);

export const DOC_COMMANDS = new Set([
  "/signal-discovery",
  "/signal-debrief",
  "/signal-design",
  "/signal-design-html",
  "/signal-design-review",
  "/signal-pre-design",
  "/signal-pre-wave",
  "/signal-review",
  "/signal-ship",
  "/signal-wave-review",
]);

export function isWired(command) {
  const c = String(command || "").trim();
  return STATE_COMMANDS.has(c) || DOC_COMMANDS.has(c);
}

// ─── State command runner ────────────────────────────────────────────────────

/**
 * Run a state command. Returns a plain-text summary string.
 * Throws on errors so the caller can surface them.
 */
export async function runStateCommand(command, args = [], context = {}) {
  switch (command) {
    case "/signal-pause":
      return runPause(args, context);
    case "/signal-freeze":
      return runFreeze(context);
    case "/signal-unfreeze":
      return runUnfreeze(context);
    case "/signal-observe":
      return runObserve(context);
    case "/signal-onboard":
      return runOnboard(context);
    default:
      throw new Error(`Unknown state command: ${command}`);
  }
}

async function runPause(args, _ctx) {
  // /signal-pause: freeze the wave AND write a pause note as an export.
  // This is the practical effect of "pause": Build is blocked until /signal-unfreeze.
  const reason = (args || []).join(" ").trim() || "no reason given";
  const ts = new Date().toISOString();
  const body = [
    "# Pause record",
    "",
    `- Time: ${ts}`,
    `- Reason: ${reason}`,
    "",
    "Wave is now frozen. Use /signal-unfreeze to resume Build.",
  ].join("\n");
  const filename = `pause-${ts.replace(/[:.]/g, "-")}.md`;
  // Export records (audit entry written by Rust via redact_for_export path).
  try { await ipc.project.exportFile("pauses", filename, body); } catch {}
  await ipc.enforcement.freeze();
  return `Paused. Wave frozen. Reason: ${reason}\nRecord: .signalos/pauses/${filename}`;
}

async function runFreeze(_ctx) {
  await ipc.enforcement.freeze();
  return "Wave frozen. Build is now blocked until /signal-unfreeze or a new wave starts.";
}

async function runUnfreeze(_ctx) {
  await ipc.enforcement.unfreeze();
  return "Wave unfrozen. Build is unlocked.";
}

async function runObserve(_ctx) {
  // Snapshot: enforcement state + wave + gates + recent audit + secrets count.
  const [enf, wave, gates, audit] = await Promise.all([
    safe(() => ipc.enforcement.state()),
    safe(() => ipc.wave.get()),
    safe(() => ipc.gates.getAll()),
    safe(() => ipc.audit.list(20)),
  ]);
  const signedGates = enf?.signed_gates || [];
  const requiredGates = enf?.required_gates || [];
  const lines = [
    "SignalOS observation",
    "—".repeat(38),
    `Wave           : ${wave?.name || "no active wave"}`,
    `Phase          : ${wave?.phase_name || "—"}`,
    `Progress       : ${wave?.progress_pct || 0}%`,
    `Enforcement    : ${enf?.wave_frozen ? "FROZEN" : "active"}`,
    `Required gates : ${requiredGates.map((g) => "G" + g).join(", ") || "—"}`,
    `Signed gates   : ${signedGates.map((g) => "G" + g).join(", ") || "none"}`,
    `Gates loaded   : ${Array.isArray(gates) ? gates.length : 0}`,
    `Overrides      : ${enf?.overrides_this_wave || 0} this wave`,
    `Audit entries  : ${Array.isArray(audit) ? audit.length : 0} recent`,
  ];
  return lines.join("\n");
}

async function runOnboard(_ctx) {
  // Walk the user through the next required onboarding step.
  const enf = await safe(() => ipc.enforcement.state());
  const required = new Set((enf?.required_gates || []).map((g) => "G" + g));
  const signed = new Set((enf?.signed_gates || []).map((g) => "G" + g));
  const open = [...required].filter((g) => !signed.has(g));
  if (open.length === 0) {
    return "Onboarding complete: G0+G1+G2 are signed. You can run Build.";
  }
  const next = open[0];
  const labels = {
    G0: "Constitution — sign the project rules.",
    G1: "Belief — record what you believe and link tests.",
    G2: "Expectation Map — define measurable success criteria.",
  };
  return [
    "Onboarding checklist:",
    ...[...required].map((g) =>
      signed.has(g) ? `  [x] ${g} signed` : `  [ ] ${g}: ${labels[g] || "unsigned"}`
    ),
    "",
    `Next required action: sign ${next}.`,
  ].join("\n");
}

// ─── Document command runner ─────────────────────────────────────────────────

/**
 * Per-command prompt and output config.
 * Each entry produces a markdown file in .signalos/<kind>/<command>-<ts>.md
 * via the Rust workspace_export path (which applies server-side redaction).
 */
const DOC_SPECS = {
  "/signal-discovery": {
    kind: "discoveries",
    title: "Discovery Brief",
    prompt: (idea) => [
      "You are SignalOS Discovery. Produce a structured discovery brief in Markdown.",
      "Sections: Problem, User, Today's Workflow, Pain Points, Hypothesis, Success Criteria, Risks, Interview Questions (5-10), Next Step.",
      "Be specific and concrete. No filler.",
      "",
      `Request: ${idea || "(no request supplied — write a generic discovery template the user can fill in)"}`,
    ].join("\n"),
  },
  "/signal-debrief": {
    kind: "debriefs",
    title: "Wave Debrief",
    prompt: (idea, ctx) => [
      "You are SignalOS Debrief. Produce a wave retrospective in Markdown.",
      "Sections: What we delivered, What worked, What broke, Surprises, Decisions, Outcomes, Carryovers, Recommended next wave.",
      "Use the supplied wave context.",
      "",
      `Wave: ${ctx.wave?.name || "current"}`,
      `Phase: ${ctx.wave?.phase_name || "—"}`,
      `Notes from user: ${idea || "(none)"}`,
    ].join("\n"),
  },
  "/signal-design": {
    kind: "designs",
    title: "Design Note",
    prompt: (idea) => [
      "You are SignalOS Design. Produce a design note in Markdown.",
      "Sections: Goal, User Journey, Information Architecture, Key Screens, Component List, States (loading/empty/error/success), Open Questions.",
      "Avoid implementation detail; this is design, not engineering.",
      "",
      `Design request: ${idea || "(no request supplied)"}`,
    ].join("\n"),
  },
  "/signal-design-html": {
    kind: "design-html",
    title: "Static HTML Mockup",
    prompt: (idea) => [
      "You are SignalOS Design (HTML). Produce ONE self-contained HTML file (no external assets, no CDN, no JS frameworks).",
      "Wrap it in a Markdown fenced code block with language `html`. Include inline CSS in a <style> tag.",
      "Must use system fonts only. Must be < 50 KB. Must render statically with no JavaScript.",
      "",
      `Mockup request: ${idea || "Design a simple landing page for a SignalOS-built app."}`,
    ].join("\n"),
  },
  "/signal-design-review": {
    kind: "design-reviews",
    title: "Design Review",
    prompt: (idea, ctx) => [
      "You are SignalOS Design Review. Critique the provided design notes in Markdown.",
      "Sections: Strengths, Risks, Missing pieces, Trust-tier (T1/T2/T3) by area, Verdict (Approve / Approve-with-conditions / Reject), Conditions if any.",
      "Reference the active wave plan and gate state where relevant.",
      "",
      `Wave: ${ctx.wave?.name || "current"}`,
      `Design to review (paste from user): ${idea || "(no design supplied)"}`,
    ].join("\n"),
  },
  "/signal-pre-design": {
    kind: "pre-design",
    title: "Pre-Design Prep",
    prompt: (idea) => [
      "You are SignalOS Pre-Design. Produce the prep notes that need to land BEFORE design starts.",
      "Sections: Known constraints, Stakeholders, Existing decisions to honor, Open questions, Suggested research, Recommended design scope.",
      "",
      `Project idea: ${idea || "(none supplied)"}`,
    ].join("\n"),
  },
  "/signal-pre-wave": {
    kind: "pre-waves",
    title: "Pre-Wave Brief",
    prompt: (idea, ctx) => [
      "You are SignalOS Pre-Wave. Produce a one-page pre-wave brief in Markdown.",
      "Sections: Wave goal, Belief, Expectation map (3-5 measurable items), Scope, Out of scope, T-tier per item, First risks, Sign-off plan (G0-G2).",
      "",
      `Existing wave context: ${ctx.wave?.name || "new wave"}`,
      `Idea: ${idea || "(none)"}`,
    ].join("\n"),
  },
  "/signal-review": {
    kind: "reviews",
    title: "Code Review",
    prompt: (idea) => [
      "You are SignalOS Review. Produce a structured code review in Markdown.",
      "Sections: Summary, Strengths, Bugs / risks, Style / readability, Test coverage gaps, Suggested follow-ups, Verdict (LGTM / Changes-requested / Reject).",
      "Reference specific lines if the user supplied a diff.",
      "",
      `Material to review: ${idea || "(no diff supplied — produce a checklist a reviewer can use)"}`,
    ].join("\n"),
  },
  "/signal-ship": {
    kind: "ships",
    title: "Ship Readiness",
    prompt: (idea, ctx) => [
      "You are SignalOS Ship. Produce a release-readiness checklist in Markdown.",
      "Sections: Functional check, Tests passed, Performance baseline, Security scan, Documentation, Rollback plan, Sign-off plan (G3-G5), Verdict.",
      "Use the wave context.",
      "",
      `Wave: ${ctx.wave?.name || "current"} · Phase: ${ctx.wave?.phase_name || "—"}`,
      `User notes: ${idea || "(none)"}`,
    ].join("\n"),
  },
  "/signal-wave-review": {
    kind: "wave-reviews",
    title: "Wave Review",
    prompt: (idea, ctx) => [
      "You are SignalOS Wave Review. Produce a full retrospective for the wave in Markdown.",
      "Sections: Delivered, Not delivered, Gate sign log (chronological), Belief verification, Process wins, Process gaps, Carryovers into next wave, Recommended belief for next wave.",
      "",
      `Wave: ${ctx.wave?.name || "current"}`,
      `User notes: ${idea || "(none)"}`,
    ].join("\n"),
  },
};

/**
 * Run an AI document command. Returns a markdown string with the result body
 * AND the relative path of the saved file. Caller renders both.
 *
 * Wave 5 closeout — streams tokens. context.onDelta({chars, accumulated})
 * fires for every chunk so the caller can update the log entry live.
 */
export async function runDocCommand(command, args = [], context = {}) {
  const spec = DOC_SPECS[command];
  if (!spec) throw new Error(`Unknown document command: ${command}`);

  const provider = context.activeProvider || "anthropic";
  const model = context.activeProviderInfo?.model || null;
  const idea = (args || []).join(" ").trim();

  const prompt = spec.prompt(idea, context);
  // Use streaming when the caller supplied a streamFn (the app passes the
  // streamingProviderChat helper). Fall back to non-streaming only when no
  // helper is available.
  const streamFn = typeof context.streamFn === "function" ? context.streamFn : null;
  const onDelta = typeof context.onDelta === "function" ? context.onDelta : null;
  const response = streamFn
    ? await streamFn(provider, model, prompt, onDelta)
    : await ipc.provider.chat(provider, model, prompt);
  const text = (response?.text || "").trim();
  if (!text) throw new Error(`${spec.title}: provider returned empty text.`);

  // Persist under .signalos/<kind>/<command>-<timestamp>.md
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const cleanCommand = command.replace(/^\//, "").replace(/[^a-z0-9-]/g, "");
  const filename = `${cleanCommand}-${ts}.md`;
  const fileBody = [
    `# ${spec.title} — ${new Date().toISOString()}`,
    "",
    `Command: \`${command}\``,
    `Wave: ${context.wave?.name || "—"}`,
    `Provider: ${provider} · model: ${model || "(default)"}`,
    `Tokens: in=${response?.tokens_in ?? "?"} out=${response?.tokens_out ?? "?"}`,
    "",
    "---",
    "",
    text,
  ].join("\n");

  let saved = null;
  try {
    saved = await ipc.project.exportFile(spec.kind, filename, fileBody);
  } catch (e) {
    // If save fails (no workspace, IO error), surface but don't lose the body.
    return `${spec.title}: generated but not saved (${e.message || e}).\n\n${text}`;
  }

  // Audit entry comes from write_workspace_export already (Wave 3 / G2-20).
  return `${spec.title} saved to ${saved.relative_path}.\n\n${text}`;
}

// ─── helpers ──────────────────────────────────────────────────────────────────

async function safe(fn) {
  try { return await fn(); } catch { return null; }
}
