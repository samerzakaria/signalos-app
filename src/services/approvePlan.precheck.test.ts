import { beforeEach, describe, expect, it, vi } from 'vitest';
import { chatBubbles, currentWave, userName, workspacePath } from '../state';
import { approvePlan, retryTask } from './approvePlan';

vi.mock('./preview', () => ({
  previewRun: vi.fn(async () => undefined),
}));

describe('approvePlan build precheck', () => {
  let invoke: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    chatBubbles.value = [{
      id: 'plan-1',
      kind: 'plan',
      text: 'Plan',
      planStatus: 'pending',
      plan: [{ id: 'T-001', title: 'Build UI', description: 'Build UI', files: [] }],
    } as any];
    workspacePath.value = 'C:/Products/Task App';
    userName.value = 'Sam';
    currentWave.value = '1';
    invoke = vi.fn(async (cmd: string) => {
      if (cmd === 'build_precheck') {
        return {
          allowed: false,
          blocking_rule: 'wave-freeze',
          reason: 'Wave is frozen. Sign G5 Quality Check and start a new wave to continue.',
        };
      }
      return null;
    });
    window.__TAURI__ = { core: { invoke } };
  });

  it('blocks approve-and-run when wave-freeze denies the build entrypoint', async () => {
    await approvePlan('plan-1');

    expect(invoke).toHaveBeenCalledWith('build_precheck', {
      args: { stack: 'auto', rules: ['wave-freeze'] },
    });
    expect(invoke).not.toHaveBeenCalledWith(
      'run_signal_command',
      expect.objectContaining({ command: 'signal-orchestrate' }),
    );
    expect(chatBubbles.value.find((b) => b.id === 'plan-1')?.planStatus).toBe('failed');
    expect(chatBubbles.value.some((b) => b.kind === 'error' && b.text.includes('Wave is frozen'))).toBe(true);
  });

  it('blocks retry dispatch before writing a retry plan when wave-freeze denies the entrypoint', async () => {
    await retryTask('plan-1', 'T-001');

    expect(invoke).toHaveBeenCalledWith('build_precheck', {
      args: { stack: 'auto', rules: ['wave-freeze'] },
    });
    expect(invoke).not.toHaveBeenCalledWith(
      'write_workspace_files',
      expect.objectContaining({ overwrite: true }),
    );
    expect(invoke).not.toHaveBeenCalledWith(
      'run_signal_command',
      expect.objectContaining({ command: 'signal-orchestrate' }),
    );
    expect(chatBubbles.value.some((b) => b.kind === 'error' && b.text.includes('Wave is frozen'))).toBe(true);
  });
});
