import { chatBubbles, workspacePath, userName, currentWave, currentCost, previewStatus, type ChatBubble, type PlanTask } from '../state';
import { requiredRoleForGate } from './gateRoles';
import { planToYaml, planToMarkdownTaskList } from './signalosPrompt';
import { previewRun } from './preview';

// Bridge to Tauri IPC. We reuse the JS ipc module via window-bound helpers
// added in app-v2.js / chat.js. The pattern: any /signal-* command goes
// through ipc.signal.runAndWait which dispatches into the Python sidecar's
// dispatch_cli -> map_slash_command -> core CLI subcommand.

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = window.__TAURI__;
  const invoke = tauri?.core?.invoke || tauri?.invoke;
  if (!invoke) throw new Error('Tauri runtime not available');
  return invoke<T>(cmd, args);
}

function nowId(): string {
  return (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random();
}

function updateBubble(id: string, patch: Partial<ChatBubble>): void {
  chatBubbles.value = chatBubbles.value.map((b) => (b.id === id ? { ...b, ...patch } : b));
}

function pushBubble(b: ChatBubble): void {
  chatBubbles.value = [...chatBubbles.value, b];
}

async function runSignal(command: string, args: string[]): Promise<unknown> {
  return tauriInvoke('run_signal_command', { command, args });
}

/**
 * After a successful wave, auto-start the preview if:
 *   1. There's no preview already running.
 *   2. The workspace has a package.json with a "dev" or "start" script
 *      (heuristic for "this is a runnable web project").
 * The user is shown the rendered app instead of having to find the
 * Preview tab and click Run themselves.
 */
async function autoStartPreviewAfterWave(): Promise<void> {
  // Already running -> don't restart.
  if (previewStatus.value === 'running' || previewStatus.value === 'starting' || previewStatus.value === 'installing') {
    return;
  }
  // Read package.json to check it's a runnable web project.
  let pkgRaw: string;
  try {
    pkgRaw = await tauriInvoke<string>('read_workspace_file', { path: 'package.json' });
  } catch {
    return; // No package.json -> not a Node web project; skip silently.
  }
  let pkg: { scripts?: Record<string, string> };
  try {
    pkg = JSON.parse(pkgRaw);
  } catch {
    return;
  }
  const scripts = pkg.scripts || {};
  if (!scripts.dev && !scripts.start && !scripts.serve) return;

  pushBubble({
    id: nowId(),
    kind: 'system',
    text: 'Auto-starting preview…',
  });
  await previewRun();
}

async function captureCost(): Promise<number> {
  try {
    const c = await tauriInvoke<{ session_usd?: number; total_usd?: number }>('get_cost_state');
    return typeof c?.session_usd === 'number' ? c.session_usd : (typeof c?.total_usd === 'number' ? c.total_usd : currentCost.value);
  } catch {
    return currentCost.value;
  }
}

export async function approvePlan(bubbleId: string): Promise<void> {
  const bubble = chatBubbles.value.find((b) => b.id === bubbleId);
  if (!bubble || bubble.kind !== 'plan' || !bubble.plan) {
    pushBubble({ id: nowId(), kind: 'error', text: 'Could not find that plan to approve.' });
    return;
  }
  if (bubble.planStatus !== 'pending') {
    return;
  }

  const tasks: PlanTask[] = bubble.plan;
  const wave = currentWave.value || '1';
  const ws = workspacePath.value;

  if (!ws) {
    pushBubble({ id: nowId(), kind: 'error', text: 'No project workspace is set. Finish onboarding or use Settings -> Workspace.' });
    return;
  }

  updateBubble(bubbleId, { planStatus: 'approved' });

  // Capture starting cost so we can show the wave's delta on completion.
  const costBefore = await captureCost();
  updateBubble(bubbleId, { costBefore });

  // Checkpoint pre-wave HEAD so "Undo Wave" can restore. Best-effort:
  // failure (no git, no commits yet) just disables rollback for this
  // wave -- the rest of the flow continues.
  try {
    const checkpointJson = await runSignal('signal-checkpoint', ['--wave', wave]);
    if (typeof checkpointJson === 'string') {
      const parsed = JSON.parse(checkpointJson) as { ok?: boolean; sha?: string };
      if (parsed.ok && parsed.sha) {
        updateBubble(bubbleId, { preWaveSha: parsed.sha, filesWritten: [] });
      }
    }
  } catch (e) {
    pushBubble({
      id: nowId(),
      kind: 'system',
      text: 'Note: pre-wave checkpoint failed (' + ((e as Error).message || String(e)).slice(0, 120) + '). Undo Wave will be unavailable for this wave.',
    });
  }

  // 1. Write PLAN.tasks.yaml AND its PLAN.md companion. PLAN.tasks.yaml
  //    is the source of truth (description / files / skills); PLAN.md
  //    is the HTML-comment task index that worktree-manager.sh parses
  //    on the with-bash orchestrator path. Writing both keeps the two
  //    code paths (with-worktrees / no-worktrees) in agreement.
  const yaml = planToYaml(tasks, wave);
  const md = planToMarkdownTaskList(tasks, wave);
  try {
    await tauriInvoke('write_workspace_files', {
      files: [
        { path: 'PLAN.tasks.yaml', content: yaml },
        { path: 'PLAN.md', content: md },
      ],
      overwrite: true,
    });
  } catch (e) {
    updateBubble(bubbleId, { planStatus: 'failed' });
    pushBubble({ id: nowId(), kind: 'error', text: 'Could not write plan files: ' + ((e as Error).message || String(e)) });
    return;
  }

  pushBubble({ id: nowId(), kind: 'system', text: `Wrote PLAN.tasks.yaml + PLAN.md (${tasks.length} task${tasks.length !== 1 ? 's' : ''}) to ${ws}` });

  // 2. Sign Gate 2 — sign as the role this gate requires (no dropdown role-play;
  // the accountable human is still recorded as the signer).
  const signer = (userName.value || 'User').trim();
  const role = requiredRoleForGate('G2');
  try {
    await runSignal('signal-sign', ['G2', '--signer', signer, '--role', role, '--verdict', 'pass']);
    pushBubble({ id: nowId(), kind: 'system', text: `Gate 2 signed by ${signer} (${role}). Audit trail updated.` });
  } catch (e) {
    pushBubble({ id: nowId(), kind: 'system', text: 'Note: Gate 2 sign failed (' + ((e as Error).message || String(e)).slice(0, 200) + '). Proceeding.' });
  }

  // 3. Dispatch orchestrator
  updateBubble(bubbleId, { planStatus: 'running' });
  pushBubble({
    id: 'wave-' + wave,
    kind: 'progress',
    text: 'Orchestrator running',
    progress: { current: 0, total: tasks.length, label: `Wave ${wave} · dispatching ${tasks.length} task${tasks.length !== 1 ? 's' : ''}` },
  });

  try {
    const result = await runSignal('signal-orchestrate', [
      '--wave', wave,
      '--plan', 'PLAN.tasks.yaml',
      '--max-concurrent', '3',
    ]);
    const costAfter = await captureCost();
    // If the user clicked Cancel mid-run, planStatus is 'cancelled'.
    // Don't override that with 'completed'.
    const current = chatBubbles.value.find((b) => b.id === bubbleId);
    if (current?.planStatus !== 'cancelled') {
      updateBubble(bubbleId, { planStatus: 'completed', costAfter });
    } else {
      updateBubble(bubbleId, { costAfter });
    }
    updateBubble('wave-' + wave, {
      kind: 'progress',
      text: 'Orchestrator complete',
      progress: { current: tasks.length, total: tasks.length, label: `Wave ${wave} · complete` },
    });
    pushBubble({
      id: nowId(),
      kind: 'ai',
      text: typeof result === 'string'
        ? `Wave ${wave} done. ${result}\n\nNext: type /signal-sign G3 to mark the build complete.`
        : `Wave ${wave} done. Next: /signal-sign G3 to mark the build complete.`,
      ts: 'just now',
    });
    // Auto-start the preview if the wave succeeded and the workspace
    // looks like a runnable web project. Saves the user from having to
    // switch to the Preview tab and click Run manually -- the whole
    // point is "you said build a todo app, here it is running."
    if (current?.planStatus !== 'cancelled') {
      autoStartPreviewAfterWave().catch((e) => {
        // Non-fatal: the user can still click Run in the Preview tab.
        console.warn('[approvePlan] auto-preview failed (non-fatal):', e);
      });
    }
  } catch (e) {
    const costAfter = await captureCost();
    updateBubble(bubbleId, { planStatus: 'failed', costAfter });
    pushBubble({ id: nowId(), kind: 'error', text: 'Orchestrator failed: ' + ((e as Error).message || String(e)) });
  }
}

/**
 * Best-effort cancel: flips the bubble to 'cancelled' so further progress
 * events get filtered out by orchestratorEvents.ts. The Python orchestrator
 * keeps running whatever task batch is in flight (we can't kill the
 * subprocess from here without a deeper cancel IPC), but the user sees the
 * wave as stopped and can move on.
 */
export function cancelWave(bubbleId: string): void {
  const bubble = chatBubbles.value.find((b) => b.id === bubbleId);
  if (!bubble || bubble.kind !== 'plan') return;
  if (bubble.planStatus !== 'running' && bubble.planStatus !== 'approved') return;
  updateBubble(bubbleId, { planStatus: 'cancelled', cancelled: true });
  pushBubble({
    id: nowId(),
    kind: 'system',
    text: 'Wave cancelled. The current task batch will finish but no new tasks will dispatch in the UI. You can start a fresh build whenever.',
  });
}

/**
 * Re-dispatch a single failed task. Writes a one-task PLAN.tasks.yaml to
 * .signalos/plans/retry-<taskId>.yaml and runs the orchestrator against it.
 * Same code path as approvePlan but scoped to one task, so file writes +
 * per-task progress + per-task error all flow through the same wiring.
 */
export async function retryTask(bubbleId: string, taskId: string): Promise<void> {
  const bubble = chatBubbles.value.find((b) => b.id === bubbleId);
  if (!bubble || !bubble.plan) return;
  const task = bubble.plan.find((t) => t.id === taskId);
  if (!task) return;
  const wave = currentWave.value || '1';
  const ws = workspacePath.value;
  if (!ws) {
    pushBubble({ id: nowId(), kind: 'error', text: 'No workspace set; can\'t retry.' });
    return;
  }

  // Flip the task status to pending so the orchestrator's progress events
  // can flip it through in_progress -> completed/failed cleanly. We keep
  // previous_failure on the in-memory task so it threads into the YAML
  // (and thus the LLM prompt), but the visible status is "pending" again.
  chatBubbles.value = chatBubbles.value.map((b) => {
    if (b.id !== bubbleId || !b.plan) return b;
    return { ...b, plan: b.plan.map((t) => (t.id === taskId ? { ...t, status: 'pending' } : t)) };
  });

  const retryFile = `.signalos/plans/retry-${taskId}.yaml`;
  // Smart retry: planToYaml emits previous_failure (set by orchestratorEvents
  // when the original task hit state=error) so the Python side's
  // _build_task_prompt prepends a "## Previous attempt failed" section
  // to the LLM prompt. The LLM sees what went wrong and adjusts.
  const yaml = planToYaml([task], wave);
  if (task.previous_failure) {
    pushBubble({
      id: nowId(),
      kind: 'system',
      text: `Retrying ${taskId} with prior-failure context: "${task.previous_failure.slice(0, 200)}"`,
    });
  }
  try {
    await tauriInvoke('write_workspace_files', {
      files: [{ path: retryFile, content: yaml }],
      overwrite: true,
    });
  } catch (e) {
    pushBubble({ id: nowId(), kind: 'error', text: 'Could not write retry plan: ' + ((e as Error).message || String(e)) });
    return;
  }

  pushBubble({ id: nowId(), kind: 'system', text: `Retrying ${taskId}…` });

  try {
    await runSignal('signal-orchestrate', [
      '--wave', wave,
      '--plan', retryFile,
      '--max-concurrent', '1',
    ]);
  } catch (e) {
    pushBubble({ id: nowId(), kind: 'error', text: `Retry of ${taskId} failed: ` + ((e as Error).message || String(e)) });
  }
}

/**
 * Roll a wave back to the pre-approval workspace state.
 *
 * Calls /signal-rollback in the sidecar, which:
 *   1. Verifies the captured pre-wave SHA is still reachable
 *   2. Restores tracked files to the captured checkpoint
 *   3. Deletes any of the wave-written files that are now untracked
 *   4. Appends a wave_rolled_back entry to AUDIT_TRAIL.jsonl
 *
 * Destructive: a confirm dialog runs first. If the bubble has no
 * preWaveSha (wave ran before checkpointing was wired), we refuse
 * rather than silently no-op.
 */
export async function rollbackWave(bubbleId: string): Promise<void> {
  const bubble = chatBubbles.value.find((b) => b.id === bubbleId);
  if (!bubble || bubble.kind !== 'plan') return;
  if (!bubble.preWaveSha) {
    pushBubble({
      id: nowId(),
      kind: 'error',
      text: 'Cannot roll back this wave: no checkpoint was captured at approval time. Future waves will be rollback-able.',
    });
    return;
  }
  if (bubble.rolledBack) {
    pushBubble({
      id: nowId(),
      kind: 'system',
      text: 'This wave was already rolled back. Nothing to do.',
    });
    return;
  }

  // Destructive-action confirm.
  const wave = bubble.preWaveSha.slice(0, 8);
  const fileCount = (bubble.filesWritten || []).length;
  const confirmed = typeof window.confirm === 'function'
    ? window.confirm(
        `Roll back this wave?\n\n` +
        `This will:\n` +
        `  • restore tracked files to checkpoint ${wave}\n` +
        `  • delete ${fileCount} file(s) the wave wrote\n\n` +
        `Unrelated changes you made manually after approval may also be lost.\n` +
        `This cannot be undone.`,
      )
    : true;
  if (!confirmed) return;

  const wave_id = bubble.id.startsWith('wave-') ? bubble.id.slice(5) : (currentWave.value || '1');
  const filesArg = (bubble.filesWritten || []).join(',');
  pushBubble({ id: nowId(), kind: 'system', text: 'Rolling back wave...' });

  try {
    const args = ['--wave', wave_id];
    if (filesArg) {
      args.push('--files', filesArg);
    }
    const json = await runSignal('signal-rollback', args);
    let parsed: { ok?: boolean; sha?: string; files_deleted?: string[]; note?: string; error?: string } = {};
    if (typeof json === 'string') {
      try { parsed = JSON.parse(json); } catch { /* fall through */ }
    }
    if (!parsed.ok) {
      pushBubble({
        id: nowId(),
        kind: 'error',
        text: 'Rollback failed: ' + (parsed.error || 'unknown error'),
      });
      return;
    }
    updateBubble(bubbleId, { rolledBack: true });
    pushBubble({
      id: nowId(),
      kind: 'system',
      text: `Wave rolled back to ${(parsed.sha || '').slice(0, 8)}. ${parsed.note || ''}`,
    });
  } catch (e) {
    pushBubble({
      id: nowId(),
      kind: 'error',
      text: 'Rollback IPC failed: ' + ((e as Error).message || String(e)),
    });
  }
}

window.approvePlan = approvePlan;
window.cancelWave = cancelWave;
window.retryTask = retryTask;
window.rollbackWave = rollbackWave;
