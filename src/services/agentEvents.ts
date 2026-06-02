import { chatBubbles, busy, resumableRunId, type ChatBubble } from '../state';
import * as ipc from '../js/ipc.js';

// Phase 3 Stream C - frontend subscription for the agent loop.
//
// The Rust sidecar (src-tauri/src/sidecar.rs) forwards any sidecar stdout
// line whose `kind === "agent-event"` straight through as the Tauri event
// **"agent:event"**, with the full envelope as the payload. The Python
// agent loop (signalos_lib/product/agent_loop.py) emits these envelopes:
//
//   { kind:"agent-event", run_id, type, ...rest }
//
// where `type` is one of:
//   "text"       - streamed assistant text (rest: { text }).
//   "text_only"  - the turn produced text and no tools (end of a text turn).
//   "tool_done"  - a tool finished OK (rest: { tool, idempotent? }).
//   "tool_error" - a tool failed (rest: { tool, error }).
//   "tool_limit" - the loop hit its tool-call budget (rest: { limit }).
//   "tool_denied"- a tool call was denied by enforcement (rest: { tool, reason }).
//   "end_turn"   - the turn finished cleanly.
//   "error"      - an unrecoverable error (rest: { error }).
//   "cancelled"  - the run was cancelled.
//
// We map each event onto the `chatBubbles` signal so the BuildView renders
// streaming text, tool rows, and error/system bubbles in real time. Updates
// are keyed by run_id (and tool name) so concurrent runs don't clobber each
// other's bubbles.
//
// INV-4: errors are surfaced as visible bubbles, never swallowed.

export interface AgentEvent {
  kind?: string;
  run_id?: string;
  type?: string;
  text?: string;
  tool?: string;
  error?: string;
  reason?: string;
  idempotent?: boolean;
  limit?: number;
  // gate / diff / preview event fields (3.6)
  gate?: string;
  title?: string;
  question?: string;
  evidence?: string;
  path?: string;
  before?: string;
  after?: string;
  srcDoc?: string;
  url?: string;
  caption?: string;
  [key: string]: unknown;
}

// The agent loop runs one active run at a time, but some events (text,
// tool_done, tool_error, text_only) are emitted without a run_id in the
// envelope. We remember the last run_id we saw so those orphan events still
// attach to the right streaming bubble.
let lastRunId: string | null = null;

function nowId(): string {
  return (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : String(Date.now()) + Math.random();
}

function runIdOf(evt: AgentEvent): string {
  const rid = typeof evt.run_id === 'string' && evt.run_id ? evt.run_id : null;
  if (rid) {
    lastRunId = rid;
    return rid;
  }
  if (lastRunId) return lastRunId;
  // No run_id has ever been seen - synthesize a stable one so all subsequent
  // orphan events for this turn group together.
  lastRunId = 'agent-run-' + nowId();
  return lastRunId;
}

// Deterministic bubble id for the streaming/finalized assistant bubble of a
// run. Tool bubbles get their own keyed ids (see toolBubbleId).
function streamBubbleId(runId: string): string {
  return 'agent-stream-' + runId;
}

function toolBubbleId(runId: string, tool: string): string {
  return 'agent-tool-' + runId + '-' + tool;
}

function pushBubble(b: ChatBubble): void {
  chatBubbles.value = [...chatBubbles.value, b];
}

function upsertBubble(b: ChatBubble): void {
  const idx = chatBubbles.value.findIndex((x) => x.id === b.id);
  if (idx === -1) {
    chatBubbles.value = [...chatBubbles.value, b];
  } else {
    chatBubbles.value = chatBubbles.value.map((x) => (x.id === b.id ? b : x));
  }
}

// Append streamed text to the run's streaming bubble, creating it if absent.
function appendText(runId: string, text: string): void {
  const id = streamBubbleId(runId);
  const existing = chatBubbles.value.find((b) => b.id === id);
  if (existing) {
    chatBubbles.value = chatBubbles.value.map((b) =>
      b.id === id ? { ...b, kind: 'streaming', text: (b.text || '') + text } : b,
    );
  } else {
    pushBubble({ id, kind: 'streaming', text });
  }
}

// Convert the streaming bubble for a run into a finalized AI bubble and clear
// the global working indicator. Safe to call when no streaming bubble exists
// (e.g. a tool-only turn) - in that case we just clear the working state.
function finalizeTurn(runId: string): void {
  const id = streamBubbleId(runId);
  const existing = chatBubbles.value.find((b) => b.id === id);
  if (existing && existing.kind === 'streaming') {
    chatBubbles.value = chatBubbles.value.map((b) =>
      b.id === id ? { ...b, kind: 'ai', ts: 'just now' } : b,
    );
  }
  busy.value = false;
}

export function handle(evt: AgentEvent): void {
  if (!evt || typeof evt !== 'object') return;
  // The Rust passthrough only forwards kind === "agent-event"; tolerate a
  // missing kind for resilience but require a recognizable type.
  const type = typeof evt.type === 'string' ? evt.type : '';
  if (!type) return;

  const runId = runIdOf(evt);

  switch (type) {
    case 'text': {
      resumableRunId.value = null;
      if (typeof evt.text === 'string' && evt.text) appendText(runId, evt.text);
      break;
    }

    case 'text_only':
    case 'end_turn': {
      finalizeTurn(runId);
      busy.value = false;
      resumableRunId.value = null;
      break;
    }

    case 'tool_done': {
      const tool = typeof evt.tool === 'string' ? evt.tool : 'tool';
      upsertBubble({
        id: toolBubbleId(runId, tool),
        kind: 'tool',
        text: '',
        tool: {
          name: tool,
          status: 'done',
          summary: evt.idempotent ? 'No change (idempotent)' : undefined,
        },
      });
      break;
    }

    case 'tool_error': {
      const tool = typeof evt.tool === 'string' ? evt.tool : 'tool';
      const error = typeof evt.error === 'string' ? evt.error : 'Tool failed';
      upsertBubble({
        id: toolBubbleId(runId, tool),
        kind: 'tool',
        text: '',
        tool: {
          name: tool,
          status: 'error',
          summary: error,
          detail: error,
        },
      });
      break;
    }

    case 'tool_denied': {
      const tool = typeof evt.tool === 'string' ? evt.tool : 'tool';
      const reason = typeof evt.reason === 'string' ? evt.reason : 'Denied by enforcement';
      upsertBubble({
        id: toolBubbleId(runId, tool),
        kind: 'tool',
        text: '',
        tool: {
          name: tool,
          status: 'denied',
          summary: reason,
          detail: reason,
        },
      });
      break;
    }

    case 'tool_limit': {
      // The loop stopped because it exhausted its tool-call budget. Surface a
      // system bubble so the user knows the turn ended for that reason, and
      // clear the working indicator.
      const limit = typeof evt.limit === 'number' ? evt.limit : null;
      pushBubble({
        id: nowId(),
        kind: 'system',
        text: limit !== null
          ? `Agent stopped after reaching its tool-call limit (${limit}).`
          : 'Agent stopped after reaching its tool-call limit.',
      });
      busy.value = false;
      break;
    }

    case 'error': {
      // INV-4: surface the failure as a visible error bubble.
      const error = typeof evt.error === 'string' ? evt.error : 'Agent run failed.';
      // If a streaming bubble exists for this run, convert it to the error so
      // the partial text isn't orphaned; otherwise push a fresh error bubble.
      const id = streamBubbleId(runId);
      const existing = chatBubbles.value.find((b) => b.id === id);
      if (existing && existing.kind === 'streaming') {
        chatBubbles.value = chatBubbles.value.map((b) =>
          b.id === id ? { ...b, kind: 'error', text: 'Error: ' + error } : b,
        );
      } else {
        pushBubble({ id: nowId(), kind: 'error', text: 'Error: ' + error });
      }
      busy.value = false;
      break;
    }

    case 'cancelled': {
      // Finalize any partial streaming text, drop a system bubble, and mark
      // the run resumable so the UI can offer a "Resume run" control.
      finalizeTurn(runId);
      pushBubble({ id: nowId(), kind: 'system', text: 'Agent run cancelled.' });
      resumableRunId.value = runId;
      busy.value = false;
      break;
    }

    case 'diff': {
      // A file write/edit, surfaced as an inline green/red diff (FileDiffBubble).
      const path = typeof evt.path === 'string' ? evt.path : '';
      if (!path) break;
      upsertBubble({
        id: `agent-diff-${runId}-${path}`,
        kind: 'diff',
        text: '',
        diff: {
          path,
          before: typeof evt.before === 'string' ? evt.before : undefined,
          after: typeof evt.after === 'string' ? evt.after : undefined,
        },
      });
      break;
    }

    case 'gate': {
      // A governance gate checkpoint - render the 5-verdict GateReviewCard and
      // clear the working indicator since the turn is now waiting on the user.
      const gate = typeof evt.gate === 'string' ? evt.gate : '';
      upsertBubble({
        id: `agent-gate-${runId}-${gate || 'g'}`,
        kind: 'gate',
        text: typeof evt.evidence === 'string' ? evt.evidence : '',
        gateReview: {
          gate: gate || 'G?',
          title: typeof evt.title === 'string' ? evt.title : 'Gate review',
          question: typeof evt.question === 'string' ? evt.question : 'Approve to continue?',
          resolvedVerdict: null,
        },
      });
      busy.value = false;
      break;
    }

    case 'preview': {
      // An inline design preview (G3) - iframe bubble (ChatPreviewBubble).
      const srcDoc = typeof evt.srcDoc === 'string' ? evt.srcDoc : undefined;
      const url = typeof evt.url === 'string' ? evt.url : undefined;
      if (!srcDoc && !url) break;
      upsertBubble({
        id: `agent-preview-${runId}`,
        kind: 'preview',
        text: '',
        preview: {
          srcDoc,
          url,
          caption: typeof evt.caption === 'string' ? evt.caption : undefined,
        },
      });
      break;
    }

    default:
      // Unknown event types are ignored (forward-compatible).
      break;
  }
}

// --- agent control commands (3.2-3.4): verdict / cancel / resume -----------
// These ride the same sidecar command channel chat.js uses (ipc.signal.run).
// run_id is the active run tracked by runIdOf(); the backend handlers live in
// signalos_ipc_server.py (agent:verdict / agent:cancel / agent:resume).

function sendAgentCommand(command: string, payload: Record<string, unknown>): void {
  try {
    const sig = (ipc as unknown as { signal?: { run?: (c: string, a: string[]) => Promise<unknown> } }).signal;
    if (sig && typeof sig.run === 'function') {
      void sig.run(command, [JSON.stringify(payload)]);
    }
  } catch (err) {
    pushBubble({
      id: nowId(),
      kind: 'error',
      text: command + ' failed: ' + (err instanceof Error ? err.message : String(err)),
    });
  }
}

/** Send the user's gate verdict to the paused agent loop (agent:verdict). */
export function submitGateVerdict(_bubbleId: string, verdict: string, feedback: string): void {
  if (!lastRunId) {
    pushBubble({ id: nowId(), kind: 'error', text: 'No active agent run to submit a verdict to.' });
    return;
  }
  sendAgentCommand('agent:verdict', { run_id: lastRunId, verdict, feedback: feedback || '' });
}

/** Cancel the in-flight agent run (agent:cancel). */
export function cancelAgentRun(): void {
  if (!lastRunId) return;
  sendAgentCommand('agent:cancel', { run_id: lastRunId });
}

/** Resume a previously cancelled/crashed run (agent:resume). */
export function resumeAgentRun(runId?: string): void {
  const rid = runId || resumableRunId.value || lastRunId;
  if (!rid) return;
  resumableRunId.value = null;
  busy.value = true;
  sendAgentCommand('agent:resume', { run_id: rid });
}

function subscribe(): void {
  const tauri = window.__TAURI__ as unknown as {
    event?: { listen?: (event: string, cb: (e: { payload: AgentEvent }) => void) => Promise<() => void> };
  } | undefined;
  const listen = tauri?.event?.listen;
  if (typeof listen !== 'function') {
    // Non-Tauri / browser dev mode - nothing to subscribe to.
    return;
  }
  listen('agent:event', (e) => {
    try {
      handle(e.payload);
    } catch (err) {
      // INV-4: a handler bug must not silently swallow the event. Surface it.
      console.warn('agent:event handler error:', err);
      try {
        pushBubble({
          id: nowId(),
          kind: 'error',
          text: 'Agent event handler error: ' + (err instanceof Error ? err.message : String(err)),
        });
      } catch { /* last-resort: don't throw out of the listener */ }
    }
  }).catch(() => {});
}

let initialized = false;

/**
 * Wire the `agent:event` Tauri subscription. Idempotent - calling more than
 * once is a no-op so multiple import sites can't register duplicate listeners.
 */
export function initAgentEvents(): void {
  if (initialized) return;
  initialized = true;
  const w = window as unknown as Record<string, unknown>;
  w.submitGateVerdict = submitGateVerdict;
  w.cancelAgentRun = cancelAgentRun;
  w.resumeAgentRun = resumeAgentRun;
  subscribe();
}

// Auto-init on import (mirrors orchestratorEvents.ts, which calls subscribe()
// at module load). main.tsx imports this module for its side effect.
initAgentEvents();
