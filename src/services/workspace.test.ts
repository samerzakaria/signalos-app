import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { workspacePath } from '../state';
import { createSignalosProject, initWorkspace, pickWorkspaceFolder } from './workspace';

describe('workspace factory helpers', () => {
  let invoke: ReturnType<typeof vi.fn>;
  let mkdir: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    workspacePath.value = '';
    mkdir = vi.fn(async () => undefined);
    invoke = vi.fn(async (cmd: string) => {
      if (cmd === 'read_workspace_file') throw new Error('not found');
      if (cmd === 'get_workspace_status') return { status: 'initialized' };
      return null;
    });
    window.__TAURI__ = {
      core: { invoke },
      fs: { mkdir },
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('creates the folder, initializes SignalOS with the product name, signs G0, and refreshes status', async () => {
    const result = await createSignalosProject('C:/Products/Task App', 'Task App');

    expect(mkdir).toHaveBeenCalledWith('C:/Products/Task App', { recursive: true });
    expect(invoke).toHaveBeenCalledWith('set_workspace', { path: 'C:/Products/Task App' });
    expect(invoke).toHaveBeenCalledWith('run_signal_command', {
      command: 'signal-init',
      args: ['--mode', 'keep', '--name', 'Task App'],
    });
    expect(invoke).toHaveBeenCalledWith('run_signal_command', {
      command: 'signal-sign',
      args: ['G0', '--signer', 'User', '--role', 'PO', '--verdict', 'pass'],
    });
    expect(invoke).toHaveBeenCalledWith('get_workspace_status', undefined);
    expect(workspacePath.value).toBe('C:/Products/Task App');
    expect(result.status).toEqual({ status: 'initialized' });
  });

  it('switches between product repos by setting the active workspace each time', async () => {
    await initWorkspace('C:/Products/One');
    await initWorkspace('C:/Products/Two');

    expect(invoke).toHaveBeenCalledWith('set_workspace', { path: 'C:/Products/One' });
    expect(invoke).toHaveBeenCalledWith('set_workspace', { path: 'C:/Products/Two' });
    expect(workspacePath.value).toBe('C:/Products/Two');
  });

  it('uses the Tauri folder dialog for workspace picking when available', async () => {
    const open = vi.fn(async () => 'C:/Products/Picked');
    window.__TAURI__ = {
      core: { invoke },
      fs: { mkdir },
      dialog: { open },
    };

    await pickWorkspaceFolder();

    expect(open).toHaveBeenCalledWith({
      directory: true,
      multiple: false,
      title: 'Choose project folder',
    });
    expect(workspacePath.value).toBe('C:/Products/Picked');
  });

  it('rethrows init failures when strict mode is enabled', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    invoke = vi.fn(async (cmd: string, args?: Record<string, unknown>) => {
      if (cmd === 'set_workspace') return null;
      if (cmd === 'run_signal_command' && args?.command === 'signal-init') {
        throw new Error('init failed');
      }
      return null;
    });
    window.__TAURI__ = { core: { invoke } };

    await expect(initWorkspace('C:/Products/Bad App', { strict: true })).rejects.toThrow('init failed');
    expect(workspacePath.value).toBe('C:/Products/Bad App');
  });
});
