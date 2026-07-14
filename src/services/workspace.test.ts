import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// workspace.ts routes the init + Gate 0 sign through the AWAITED sidecar
// transport (ipc.signal.runAndWait) so success is only reported after the
// engine actually finished -- FIX 1 (Claim 3a). Mock that module so the
// tests can drive real resolution / rejection; every OTHER Tauri call in
// workspace.ts still goes through window.__TAURI__.core.invoke.
vi.mock('../js/ipc', () => ({
  signal: {
    run: vi.fn(),
    runAndWait: vi.fn(async () => null),
    cancelPending: vi.fn(),
  },
}));

import { workspacePath } from '../state';
import { approveGate0, createSignalosProject, initWorkspace, pickWorkspaceFolder } from './workspace';
import { signal } from '../js/ipc';

const runAndWait = signal.runAndWait as unknown as ReturnType<typeof vi.fn>;

describe('workspace factory helpers', () => {
  let invoke: ReturnType<typeof vi.fn>;
  let mkdir: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    workspacePath.value = '';
    runAndWait.mockReset();
    runAndWait.mockResolvedValue(null);
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

  it('creates the folder, awaits init, fills governance, and leaves Gate 0 awaiting the founder (no auto-sign)', async () => {
    const result = await createSignalosProject('C:/Products/Task App', 'Task App', 'react-vite');

    expect(mkdir).toHaveBeenCalledWith('C:/Products/Task App', { recursive: true });
    expect(invoke).toHaveBeenCalledWith('set_workspace', { path: 'C:/Products/Task App' });
    // init is AWAITED via the sidecar transport, not fire-and-forget.
    expect(runAndWait).toHaveBeenCalledWith(
      'signal-init',
      ['--mode', 'keep', '--name', 'Task App', '--profile', 'react-vite'],
      expect.any(Number),
    );
    expect(invoke).toHaveBeenCalledWith('set_identity', { name: 'User', role: 'PO' });
    // C1: Gate 0 is NOT auto-signed at creation -- signing is the founder's
    // explicit act (approveGate0, via the Approve button or a chat approval).
    // No signal-sign for G0 should fire during project creation.
    const signCalls = runAndWait.mock.calls.filter((c) => c[0] === 'signal-sign');
    expect(signCalls).toHaveLength(0);
    expect(invoke).toHaveBeenCalledWith('get_workspace_status', undefined);
    expect(workspacePath.value).toBe('C:/Products/Task App');
    expect(result.governance.signed).toBe(false);
    expect(result.governance.awaitingApproval).toBe(true);
    expect(result.status).toEqual({ status: 'initialized' });
  });

  it('approveGate0 signs Gate 0 under BOTH required roles (PO + PE) as an explicit approval', async () => {
    workspacePath.value = 'C:/Products/Task App';

    const result = await approveGate0({ via: 'chat' });

    // G0's manifest spans PO + PE; the solo founder holds both seats, so both
    // are signed with a valid verdict, each awaited.
    expect(runAndWait).toHaveBeenCalledWith(
      'signal-sign',
      ['G0', '--signer', 'User', '--role', 'PO', '--verdict', 'APPROVED'],
      expect.any(Number),
    );
    expect(runAndWait).toHaveBeenCalledWith(
      'signal-sign',
      ['G0', '--signer', 'User', '--role', 'PE', '--verdict', 'APPROVED'],
      expect.any(Number),
    );
    expect(result.signed).toBe(true);
  });

  it('approveGate0 reports unsigned when the awaited sidecar sign fails', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    workspacePath.value = 'C:/Products/Task App';
    runAndWait.mockImplementation(async (command: string, args: unknown[]) => {
      if (command === 'signal-sign' && Array.isArray(args) && args.includes('PE')) {
        throw new Error('gate validator rejected');
      }
      return null;
    });

    const result = await approveGate0({ via: 'button' });

    // The engine failed the PE half of G0 -> success is NOT reported.
    expect(result.signed).toBe(false);
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
    runAndWait.mockImplementation(async (command: string) => {
      if (command === 'signal-init') throw new Error('init failed');
      return null;
    });

    await expect(initWorkspace('C:/Products/Bad App', { strict: true })).rejects.toThrow('init failed');
    expect(workspacePath.value).toBe('C:/Products/Bad App');
  });
});
