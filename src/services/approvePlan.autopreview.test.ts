import { beforeEach, describe, expect, it, vi } from 'vitest';
import { chatBubbles, currentWave, previewStatus, userName, workspacePath } from '../state';
import { approvePlan } from './approvePlan';

// Claim 11c — after a successful wave, approvePlan auto-starts the preview,
// which first reads package.json to confirm the workspace is a runnable web
// project. That read must use `relative_path` (the Rust command's binding);
// the old `path` arg rejected, so the auto-preview never fired.

vi.mock('./preview', () => ({
  previewRun: vi.fn(async () => undefined),
}));

describe('approvePlan auto-preview — read_workspace_file arg name', () => {
  let invoke: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    chatBubbles.value = [{
      id: 'plan-1',
      kind: 'plan',
      text: 'Plan',
      planStatus: 'pending',
      plan: [{ id: 'T-001', title: 'Build UI', description: 'Build UI', files: [] }],
    } as never];
    workspacePath.value = 'C:/Products/Task App';
    userName.value = 'Sam';
    currentWave.value = '1';
    previewStatus.value = 'idle';

    invoke = vi.fn(async (cmd: string, args: Record<string, unknown>) => {
      if (cmd === 'build_precheck') return { allowed: true };
      if (cmd === 'get_cost_state') return { session_usd: 0 };
      if (cmd === 'read_workspace_file' && args?.relative_path === 'package.json') {
        return JSON.stringify({ scripts: { dev: 'vite' } });
      }
      if (cmd === 'run_signal_command') {
        const command = (args as { command?: string }).command;
        if (command === 'signal-checkpoint') return JSON.stringify({ ok: true, sha: 'abc1234' });
        if (command === 'signal-orchestrate') return 'built 1 task';
        return null; // signal-sign etc.
      }
      return null; // write_workspace_files
    });
    (window as unknown as { __TAURI__: unknown }).__TAURI__ = { core: { invoke } };
  });

  it('reads package.json via relative_path before auto-starting the preview', async () => {
    await approvePlan('plan-1');
    // autoStartPreviewAfterWave is fire-and-forget; let its async read settle.
    await new Promise((r) => setTimeout(r, 0));

    expect(invoke).toHaveBeenCalledWith('read_workspace_file', { relative_path: 'package.json' });
    const legacyCall = invoke.mock.calls.find(
      ([cmd, args]) => cmd === 'read_workspace_file' && args && Object.prototype.hasOwnProperty.call(args, 'path'),
    );
    expect(legacyCall).toBeUndefined();

    // The wave completed, so the auto-preview path was reached.
    expect(chatBubbles.value.find((b) => b.id === 'plan-1')?.planStatus).toBe('completed');
    expect(chatBubbles.value.some((b) => b.kind === 'system' && /Auto-starting preview/.test(b.text))).toBe(true);
  });
});
