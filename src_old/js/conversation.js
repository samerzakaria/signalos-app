/**
 * conversation.js — Builder-aware conversation history (§11.5/11).
 *
 * Persists every Builder turn (user prompt + AI response + chosen stack +
 * file count) under .signalos/builds/<buildId>/conversation.jsonl. Reload
 * the last build's conversation on follow-up so the user can say
 * "make the button bigger" without restating the whole prompt.
 *
 * The current build is identified by an opaque buildId (timestamp slug).
 * The latest buildId is recorded at .signalos/builds/last.txt so the
 * Builder can resume across app restarts.
 */

import * as ipc from "./ipc.js";

const LAST_FILE = "builds";
const LAST_NAME = "last.txt";

/// Resolve the active buildId. If none, create a new one and persist it.
export async function activeBuildId() {
  const existing = await safeRead(`.signalos/${LAST_FILE}/${LAST_NAME}`);
  if (existing && /^\d{4}-\d{2}-\d{2}/.test(existing.trim())) {
    return existing.trim();
  }
  return newBuildId();
}

export async function newBuildId() {
  const id = new Date().toISOString().replace(/[:.]/g, "-");
  // Mark this build active.
  await ipc.project.exportFile(LAST_FILE, LAST_NAME, id);
  return id;
}

/// Append one turn to the active build's conversation.
export async function appendTurn(buildId, turn) {
  const id = buildId || (await activeBuildId());
  // We append by reading-modify-writing the file via exportFile.
  // exportFile's atomic write replaces the file each call — so we read first.
  const path = `.signalos/builds/${id}/conversation.jsonl`;
  const existing = (await safeRead(path)) || "";
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    ...turn,
  });
  await ipc.project.exportFile(`builds/${id}`, "conversation.jsonl", existing + line + "\n");
  return id;
}

/// Load all turns of a build's conversation. Returns [] if none.
export async function loadHistory(buildId) {
  if (!buildId) return [];
  const path = `.signalos/builds/${buildId}/conversation.jsonl`;
  const text = await safeRead(path);
  if (!text) return [];
  return text
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try { return JSON.parse(line); } catch { return null; }
    })
    .filter(Boolean);
}

/// Compress prior turns into a short context block for the next prompt.
/// Keeps the latest 3 turns verbatim and summarizes earlier ones.
export function compressHistory(turns) {
  if (!turns?.length) return "";
  const recent = turns.slice(-3);
  const earlier = turns.slice(0, -3);
  const summary = earlier.length
    ? `Earlier in this build (${earlier.length} turns, summarized):\n${earlier
        .map((t, i) => `  ${i + 1}. ${oneLine(t.user_idea || t.user || "(no prompt)")}`)
        .join("\n")}\n`
    : "";
  const recentBlock = recent
    .map((t, i) => {
      const idx = (earlier.length || 0) + i + 1;
      const ai = oneLine(t.ai_summary || t.summary || "");
      const userIdea = oneLine(t.user_idea || t.user || "(no prompt)");
      const filesCount = Array.isArray(t.files_written) ? t.files_written.length : 0;
      return [
        `### Turn ${idx} — ${t.ts || ""}`,
        `User: ${userIdea}`,
        `Result: ${filesCount ? `${filesCount} files written` : "(no files)"} — ${ai}`,
      ].join("\n");
    })
    .join("\n\n");
  return [summary, recentBlock].filter(Boolean).join("\n\n");
}

async function safeRead(rel) {
  try {
    return await ipc.project.readFile(rel);
  } catch { return null; }
}

function oneLine(s) {
  return String(s || "").replace(/\s+/g, " ").trim().slice(0, 240);
}
