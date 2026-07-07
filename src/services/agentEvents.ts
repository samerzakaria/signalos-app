import { ai, aiModel, chatBubbles, busy, resumableRunId, tab, previewUrl, govGatesList, type ChatBubble, type UxFrictionPersona, type UxFrictionFinding } from '../state';
import * as ipc from '../js/ipc.js';
import { notifyFromAgentEvent } from './notifications';

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
  // ux_friction event fields (#12) — per-persona findings from ux_friction.py
  findings?: unknown;
  count?: number;
  [key: string]: unknown;
}

// Coerce the ux_friction payload (heuristic_findings() output) into the typed
// shape the UxFrictionCard renders. Tolerant of malformed entries — a bad
// persona entry is dropped rather than crashing the event handler.
function parseUxFrictionPersonas(raw: unknown): UxFrictionPersona[] {
  if (!Array.isArray(raw)) return [];
  const personas: UxFrictionPersona[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const p = item as Record<string, unknown>;
    const findingsRaw = Array.isArray(p.findings) ? p.findings : [];
    const findings: UxFrictionFinding[] = [];
    for (const f of findingsRaw) {
      if (!f || typeof f !== 'object') continue;
      const fr = f as Record<string, unknown>;
      const issue = typeof fr.issue === 'string' ? fr.issue : '';
      if (!issue) continue;
      findings.push({
        severity: typeof fr.severity === 'string' ? fr.severity : 'medium',
        issue,
        suggestion: typeof fr.suggestion === 'string' ? fr.suggestion : undefined,
      });
    }
    personas.push({
      persona: typeof p.persona === 'string' ? p.persona : '',
      label: typeof p.label === 'string' ? p.label : (typeof p.persona === 'string' ? p.persona : 'Persona'),
      findings,
    });
  }
  return personas;
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

function readableAgentError(raw: string): string {
  const extracted = raw.match(/"message"\s*:\s*"([^"]+)"/)?.[1] || raw;
  const text = extracted
    .replace(/(?:Provider call failed:\s*)+/gi, '')
    .replace(/^(BadRequestError|AuthenticationError|RateLimitError|RuntimeError):\s*/i, '')
    .trim();
  const provider = raw.match(/\b(Anthropic|OpenAI|Gemini|Ollama|OpenRouter|DeepSeek|Mistral|Groq|Cerebras|Together AI|Together|xAI|Qwen)\b/i)?.[1] || 'AI provider';
  if (/credit balance is too low|insufficient.*credit|purchase credits/i.test(text)) {
    return `${provider} account credit is too low. Add credits with that provider or choose another provider/model in Settings.`;
  }
  if (/LLM Provider NOT provided|provider.*not provided|unmapped llm provider/i.test(text)) {
    return `Foundry could not route the selected model to ${provider}. Re-select the provider and model in Settings, then retry.`;
  }
  if (/HTTP 404|model.*not found|does not exist|not supported/i.test(text)) {
    return `${provider} rejected the selected model for chat. Pick a text/chat model in Settings, test it, then retry.`;
  }
  if (/api key|api_key|unauthori[sz]ed|authentication|401|forbidden/i.test(text)) {
    return `${provider} rejected the API key. Replace the key in Settings, then retry.`;
  }
  return text || 'Agent run failed.';
}

function selectedProviderPayload(): { provider: string; model: string } | null {
  const provider = (ai.value || '').trim();
  const model = (aiModel.value || '').trim();
  if (!provider || !model) return null;
  return { provider, model };
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

  // #22 — incident/gate/reopen/completion events also feed the notification
  // bell (incl. gate_signed for the final G5 sign and delivery_complete, which
  // have no bubble rendering below). Chatty types (text, tool_done, …) are
  // filtered inside the service.
  try { notifyFromAgentEvent(evt); } catch { /* feed must never break chat */ }

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
      const error = readableAgentError(typeof evt.error === 'string' ? evt.error : 'Agent run failed.');
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

    case 'ux_friction': {
      // 5-persona UX friction report (#12), emitted by the gate orchestrator
      // right before the design-gate `gate` checkpoint. Informational — no
      // verdict; the bubble lands ahead of the gate review card so the human
      // sees the friction findings when signing.
      const gate = typeof evt.gate === 'string' ? evt.gate : '';
      const personas = parseUxFrictionPersonas(evt.findings);
      if (personas.length === 0) break;
      upsertBubble({
        id: `agent-friction-${runId}-${gate || 'g'}`,
        kind: 'friction',
        text: '',
        uxFriction: { gate: gate || 'design', personas },
      });
      break;
    }

    case 'gate_reopened': {
      // GATE-REOPEN-DESIGN: a signed gate was reopened; every later signed
      // gate lost its signature (cascade). The backend also emits a plain
      // `system` event with the same fact — that event type is NOT rendered
      // by this handler (unknown types are ignored below), so rendering the
      // structured event here cannot double-bubble. The bubble id is
      // deterministic per (run, gate, reopen_count) so a re-delivery upserts
      // instead of duplicating.
      const gate = typeof evt.gate === 'string' && evt.gate ? evt.gate : 'Gate';
      const invalidated = Array.isArray(evt.invalidated)
        ? (evt.invalidated as unknown[]).filter((g): g is string => typeof g === 'string' && !!g)
        : [];
      const by = typeof evt.by === 'string' && evt.by ? evt.by : 'user';
      const reason = typeof evt.reason === 'string' && evt.reason ? evt.reason : '';
      let text = `${gate} reopened by ${by}${reason ? ': ' + reason : ''}.`;
      if (invalidated.length > 0) {
        text += ` Also invalidated: ${invalidated.join(', ')}.`;
      }
      const count = typeof evt.reopen_count === 'number' ? evt.reopen_count : 0;
      upsertBubble({
        id: `agent-reopened-${runId}-${gate}-${count}`,
        kind: 'system',
        text,
      });
      // Mirror the cascade into the gate rail immediately: the reopened gate
      // becomes current, invalidated gates drop their signature.
      const affected = new Set<string>([gate, ...invalidated]);
      govGatesList.value = govGatesList.value.map((g) => {
        const code = String(g.gate_id ?? g.id ?? '');
        if (!affected.has(code)) return g;
        const isTarget = code === gate;
        return {
          ...g,
          signed: false,
          status: isTarget ? 'current' : 'locked',
          is_current: isTarget,
        };
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
      // 4.4: a live dev-server URL auto-opens the Preview tab.
      if (url) {
        previewUrl.value = url;
        tab.value = 'preview';
      }
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

/** The run id a reopen would target: the active run if one has been seen,
 *  else the resumable (cancelled/parked) run. Null when no run exists. */
export function getAgentRunId(): string | null {
  return lastRunId || resumableRunId.value || null;
}

// User-facing fallbacks for the reopen refusal statuses the backend can
// return (GATE-REOPEN-DESIGN §IPC). The backend's own message wins when set.
const REOPEN_REFUSAL_TEXT: Record<string, string> = {
  'not-signed': 'That gate is not signed, so there is nothing to reopen.',
  'role-not-authorized': 'Your role is not authorized to reopen this gate.',
  'max-reopens': 'This gate has hit its reopen budget — it cannot be reopened again.',
  'delivery-busy': 'The delivery is busy right now. Wait for the current gate agent to finish, then retry.',
  'unknown-gate': 'Unknown gate.',
};

export interface ReopenGateResult {
  status: string;
  message?: string;
  error?: string;
  gate?: string;
  invalidated?: string[];
  [key: string]: unknown;
}

/**
 * Reopen a signed gate (agent:reopen-gate). Uses the awaited variant of the
 * agent:verdict transport so refusal statuses (not-signed /
 * role-not-authorized / max-reopens / delivery-busy) come back to the caller
 * for inline display. Success also arrives as a `gate_reopened` agent event,
 * which handle() renders as the system bubble + gate-rail update.
 */
export async function reopenGate(
  gate: string,
  reason: string,
  runId?: string,
): Promise<ReopenGateResult> {
  const rid = runId || getAgentRunId();
  if (!rid) {
    return { status: 'no-run', error: 'No agent run to reopen a gate for.' };
  }
  const sig = (ipc as unknown as {
    signal?: {
      run?: (c: string, a: string[]) => Promise<unknown>;
      runAndWait?: (c: string, a: string[], t?: number) => Promise<unknown>;
    };
  }).signal;
  const call = sig?.runAndWait || sig?.run;
  if (typeof call !== 'function') {
    return { status: 'error', error: 'Engine transport unavailable.' };
  }
  const payload = JSON.stringify({ run_id: rid, gate, reason });
  const raw = await call.call(sig, 'agent:reopen-gate', [payload], 15000);
  const res: ReopenGateResult = raw && typeof raw === 'object'
    ? (raw as ReopenGateResult)
    : { status: 'ok' };
  if (typeof res.status !== 'string' || !res.status) res.status = 'ok';
  if (res.status !== 'ok') {
    res.error = res.error
      || res.message
      || REOPEN_REFUSAL_TEXT[res.status]
      || `Reopen refused (${res.status}).`;
  }
  return res;
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
  const selected = selectedProviderPayload();
  if (!selected) {
    pushBubble({
      id: nowId(),
      kind: 'error',
      text: 'Choose an AI provider and model in Settings before resuming the agent.',
    });
    busy.value = false;
    return;
  }
  resumableRunId.value = null;
  busy.value = true;
  sendAgentCommand('agent:resume', { run_id: rid, ...selected });
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
