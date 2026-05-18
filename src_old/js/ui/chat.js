import * as ipc from '../ipc.js';
import { state } from '../state.js';
import { esc, showError } from '../util.js';
import { activeBuildId, appendTurn, loadHistory as loadConvHistory } from '../conversation.js';
import { loadEnforcement, updateCostDisplay } from '../app-v2.js';

export async function loadBuild() {
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
