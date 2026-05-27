import { beforeEach, describe, expect, it, vi } from 'vitest';

describe('ipc workspace release routes', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  async function loadIpcWithInvoke(invoke: ReturnType<typeof vi.fn>) {
    window.__TAURI__ = { core: { invoke } } as unknown as typeof window.__TAURI__;
    return import('./ipc.js');
  }

  it('routes workspace switch, status, and clear through explicit Tauri commands', async () => {
    const invoke = vi.fn(async () => ({ status: 'initialized' }));
    const { workspace } = await loadIpcWithInvoke(invoke);

    await workspace.set('C:/Products/One');
    await workspace.set('C:/Products/Two');
    const managed = await workspace.ensureDefault('SignalOS Workspace', 'C:/SignalOS Projects');
    const status = await workspace.status();
    await workspace.clear();

    expect(status).toEqual({ status: 'initialized' });
    expect(managed).toEqual({ status: 'initialized' });
    expect(invoke).toHaveBeenCalledWith('set_workspace', { path: 'C:/Products/One' });
    expect(invoke).toHaveBeenCalledWith('set_workspace', { path: 'C:/Products/Two' });
    expect(invoke).toHaveBeenCalledWith('ensure_default_workspace', {
      product_name: 'SignalOS Workspace',
      projects_root: 'C:/SignalOS Projects',
    });
    expect(invoke).toHaveBeenCalledWith('get_workspace_status', {});
    expect(invoke).toHaveBeenCalledWith('clear_workspace', {});
  });
});
