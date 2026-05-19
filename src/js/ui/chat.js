import * as ipc from '../ipc.js';
import { state } from '../state.js';
import { showError } from '../util.js';
import { activeBuildId, appendTurn, loadHistory as loadConvHistory } from '../conversation.js';
import { loadEnforcement, updateCostDisplay } from '../app-v2.js';
import { wrapWithSignalosContext, extractPlanWithErrors, isBuildIntent } from '../../services/signalosPrompt.ts';

function nowId() {
  return (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random();
}

export async function loadBuild() {
  try {
    const buildId = await activeBuildId();
    const turns = await loadConvHistory(buildId);
    const bubbles = [
      {
        id: 'welcome',
        kind: 'ai',
        text: `Hi ${state.userName || 'there'}! What do you want to build today?`,
        historical: true,
      },
    ];
    if (turns && turns.length > 0) {
      turns.forEach((t) => {
        if (t.user_idea || t.user) bubbles.push({ id: nowId(), kind: 'user', text: t.user_idea || t.user, historical: true });
        if (t.ai_summary || t.summary) bubbles.push({ id: nowId(), kind: 'ai', text: t.ai_summary || t.summary, historical: true });
      });
    }
    state.chatBubbles = bubbles;
  } catch (e) {
    console.warn('Could not load conversation history:', e.message);
  }

  await loadEnforcement().catch(() => {});
}

async function sendMsg() {
  const val = (state.chatInputValue || '').trim();
  if (!val || state.busy) return;
  state.busy = true;

  state.cmdPaletteOpen = false;

  addUserBubble(val);
  state.chatInputValue = '';

  // Slash commands route to the Python sidecar (signalos CLI), not to the AI provider.
  // Anything starting with "/signal-" hits dispatch_cli in signalos_ipc_server.py.
  if (val.startsWith('/signal-') || val.startsWith('/')) {
    try {
      const tokens = val.replace(/^\//, '').split(/\s+/).filter(Boolean);
      const command = tokens[0];
      const args = tokens.slice(1);
      const output = await ipc.signal.runAndWait(command, args, 60000);
      addAIBubble(typeof output === 'string' ? output : JSON.stringify(output ?? '(no output)'));
    } catch (e) {
      state.chatBubbles = [...state.chatBubbles, { id: nowId(), kind: 'error', text: 'Command failed: ' + (e.message || e) }];
      showError(e.message || 'Command failed');
    } finally {
      state.busy = false;
    }
    return;
  }

  const streamId = nowId();
  startStream(streamId);

  // For build-intent messages, wrap with the SignalOS protocol preamble so the
  // model emits a structured plan instead of free-form text. The user sees
  // their original message in the bubble; the AI sees the wrapped version.
  const wrapped = wrapWithSignalosContext(val);
  const intent = isBuildIntent(val);

  try {
    await ipc.provider.chatStream(streamId, state.ai, state.aiModel, wrapped);
    const cost = await ipc.provider.getCost();
    updateCostDisplay(cost);
    const buildId = await activeBuildId().catch(() => null);
    if (buildId) {
      await appendTurn(buildId, { user_idea: val, ai_summary: '(streaming)' }).catch(() => {});
    }
    // If this was a build-intent message, try to extract the plan from the
    // finalised bubble and upgrade its kind to 'plan'. If extraction fails
    // schema validation, surface the issues to the user instead of silently
    // dropping back to a regular AI text bubble.
    if (intent) {
      const last = state.chatBubbles.find((b) => b.id === streamId);
      if (last) {
        const result = extractPlanWithErrors(last.text || '');
        if ('tasks' in result) {
          state.chatBubbles = state.chatBubbles.map((b) =>
            b.id === streamId
              ? { ...b, kind: 'plan', plan: result.tasks, planStatus: 'pending' }
              : b
          );
          // Surface heuristic skill backfills so the user can see the
          // server-side defense in action. Also feeds the audit-trail-by-eye
          // mental model of "how often does the AI miss a tag?"
          if (result.backfills && result.backfills.length > 0) {
            const lines = result.backfills.map((bf) => {
              const adds = bf.added.map((a) => `${a.key} (${a.reason})`).join('; ');
              return `• ${bf.taskId}: ${adds}`;
            });
            state.chatBubbles = [
              ...state.chatBubbles,
              {
                id: nowId(),
                kind: 'system',
                text: 'Auto-tagged the following tasks with skills the AI didn\'t mark:\n' + lines.join('\n'),
              },
            ];
          }
        } else if (result.error.kind !== 'no_block') {
          // The model emitted a signalos-plan block but it didn't pass schema.
          // Push a system bubble explaining so the user can ask the model to
          // fix and retry, instead of staring at a raw JSON block.
          const detail = result.error.perTaskIssues?.length
            ? result.error.perTaskIssues.slice(0, 6).join('\n• ')
            : result.error.details;
          state.chatBubbles = [
            ...state.chatBubbles,
            {
              id: nowId(),
              kind: 'system',
              text: 'Plan didn\'t pass schema validation. Issues:\n• ' + detail + '\n\nAsk SignalOS to revise (e.g. "rewrite that plan with valid tiers").',
            },
          ];
        }
        // no_block case: leave the bubble as a regular AI message
      }
    }
  } catch (e) {
    showStreamError(streamId, e.message);
  } finally {
    state.busy = false;
  }
}
window.sendMsg = sendMsg;

export function addUserBubble(text) {
  state.chatBubbles = [...state.chatBubbles, { id: nowId(), kind: 'user', text, ts: 'just now' }];
}

export function addAIBubble(text) {
  state.chatBubbles = [...state.chatBubbles, { id: nowId(), kind: 'ai', text, ts: 'just now' }];
}

function startStream(streamId) {
  state.chatBubbles = [...state.chatBubbles, { id: streamId, kind: 'streaming', text: '' }];
}

export function appendStreamToken(streamId, delta) {
  state.chatBubbles = state.chatBubbles.map((b) =>
    b.id === streamId ? { ...b, text: b.text + delta } : b
  );
}

export function finaliseStream(streamId) {
  state.chatBubbles = state.chatBubbles.map((b) =>
    b.id === streamId ? { ...b, kind: 'ai', ts: 'just now' } : b
  );
}

export function showStreamError(streamId, msg) {
  state.chatBubbles = state.chatBubbles.map((b) =>
    b.id === streamId ? { ...b, kind: 'error', text: 'Error: ' + (msg || 'Stream failed') } : b
  );
  showError(msg || 'Chat stream error');
}

function composerInput(_e) {
  const val = state.chatInputValue || '';
  state.cmdPaletteOpen = val.startsWith('/');
}
window.composerInput = composerInput;

function composerKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMsg();
  }
  if (e.key === 'Escape') {
    state.cmdPaletteOpen = false;
  }
}
window.composerKey = composerKey;

function runCmd(cmd) {
  state.chatInputValue = cmd;
  state.cmdPaletteOpen = false;
  sendMsg();
}
window.runCmd = runCmd;

function sendChip(text) {
  const trimmed = (text || '').trim();
  if (!trimmed) return;
  addUserBubble(trimmed);
  const streamId = nowId();
  startStream(streamId);
  ipc.provider
    .chatStream(streamId, state.ai, state.aiModel, trimmed)
    .then(() => ipc.provider.getCost().then(updateCostDisplay).catch(() => {}))
    .catch((e) => showStreamError(streamId, e.message));
}
window.sendChip = sendChip;
