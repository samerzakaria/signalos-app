import { chatBubbles, type ChatBubble } from '../state';

// Listen for `sidecar:progress` events emitted by the Python sidecar's
// ProgressEmitter (signalos_ipc_server.py). The payload schema is:
//   { id, kind: "progress", phase, substep, state, detail, ts }
//
// We surface these as live updates to a single progress bubble per phase,
// so the chat doesn't get flooded with hundreds of one-line bubbles.

interface SidecarProgress {
  id?: string;
  kind?: string;
  phase?: string;
  substep?: string;
  state?: string; // "running" | "done" | "error"
  detail?: string | null;
  ts?: number;
}

// Phase -> running tally of (done substeps, total substeps).
// Total is not known in advance from the event stream; we estimate by
// counting unique substeps seen.
const phaseSubsteps = new Map<string, Set<string>>();
const phaseDone = new Map<string, Set<string>>();

function bubbleIdForPhase(phase: string): string {
  return `phase-${phase}`;
}

function upsertBubble(b: ChatBubble): void {
  const idx = chatBubbles.value.findIndex((x) => x.id === b.id);
  if (idx === -1) {
    chatBubbles.value = [...chatBubbles.value, b];
  } else {
    chatBubbles.value = chatBubbles.value.map((x) => (x.id === b.id ? b : x));
  }
}

function pushBubble(b: ChatBubble): void {
  chatBubbles.value = [...chatBubbles.value, b];
}

function updatePlanTaskStatus(taskId: string, state: string, detail?: string): void {
  // Map orchestrator task states -> PlanTask status values rendered in the plan card.
  const taskStatus = state === 'done' ? 'completed'
                    : state === 'error' ? 'failed'
                    : state === 'running' ? 'in_progress'
                    : 'pending';
  chatBubbles.value = chatBubbles.value.map((b) => {
    if (b.kind !== 'plan' || !b.plan) return b;
    // Cancelled bubbles ignore further progress -- the orchestrator may
    // still be finishing in-flight tasks but the UI has moved on.
    if (b.cancelled) return b;
    let touched = false;
    const nextPlan = b.plan.map((t) => {
      if (t.id === taskId || t.id === `task-${taskId}` || taskId === `task-${t.id}`) {
        touched = true;
        // On failure, record the detail so a subsequent retryTask can
        // thread it into the retry prompt as previous_failure context.
        const next = { ...t, status: taskStatus };
        if (state === 'error' && detail) next.previous_failure = detail;
        return next;
      }
      return t;
    });
    return touched ? { ...b, plan: nextPlan } : b;
  });
  if (detail && state === 'error') {
    pushBubble({
      id: 'err-' + Date.now() + '-' + Math.random(),
      kind: 'error',
      text: `Task ${taskId} failed: ${detail}`,
    });
  }
}

function handle(evt: SidecarProgress): void {
  if (!evt || evt.kind !== 'progress' || !evt.phase) return;
  const phase = evt.phase;
  const substep = evt.substep || '_';
  const state = evt.state || 'running';

  // Orchestrator emits phase="orchestrate" with substep=<task_id>. Correlate
  // those back into the active plan card so individual rows flip between
  // pending -> in_progress -> completed/failed in real time.
  if (phase === 'orchestrate' && evt.substep) {
    updatePlanTaskStatus(evt.substep, state, evt.detail || undefined);
    // also fall through to update the aggregate progress bubble below.
  }

  let seen = phaseSubsteps.get(phase);
  if (!seen) {
    seen = new Set();
    phaseSubsteps.set(phase, seen);
  }
  seen.add(substep);

  let done = phaseDone.get(phase);
  if (!done) {
    done = new Set();
    phaseDone.set(phase, done);
  }
  if (state === 'done') done.add(substep);

  if (state === 'error' && phase !== 'orchestrate') {
    // For non-orchestrate phases, surface errors as their own bubble.
    // Orchestrate task errors are already surfaced by updatePlanTaskStatus above.
    pushBubble({
      id: 'err-' + Date.now() + '-' + Math.random(),
      kind: 'error',
      text: `${phase}/${substep} failed${evt.detail ? ': ' + evt.detail : ''}`,
    });
    return;
  }

  const label = evt.detail || `${phase} · ${substep}${state === 'running' ? '…' : ''}`;
  upsertBubble({
    id: bubbleIdForPhase(phase),
    kind: 'progress',
    text: label,
    progress: { current: done.size, total: seen.size, label },
  });
}

function subscribe(): void {
  const tauri = window.__TAURI__ as unknown as {
    event?: { listen?: (event: string, cb: (e: { payload: SidecarProgress }) => void) => Promise<() => void> };
  } | undefined;
  const listen = tauri?.event?.listen;
  if (typeof listen !== 'function') {
    // Non-Tauri / browser dev mode -- nothing to do.
    return;
  }
  listen('sidecar:progress', (e) => {
    try { handle(e.payload); } catch (err) { console.warn('progress handler error:', err); }
  }).catch(() => {});
}

subscribe();
