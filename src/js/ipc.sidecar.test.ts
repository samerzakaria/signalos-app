import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type Listener = (event: { payload: unknown }) => void;

describe('ipc sidecar waits', () => {
  let listeners: Map<string, Listener>;
  let invoke: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetModules();
    listeners = new Map();
    invoke = vi.fn(async () => 'req-1');
    (window as any).__TAURI__ = {
      core: { invoke },
      event: {
        listen: vi.fn((name: string, cb: Listener) => {
          listeners.set(name, cb);
          return () => {};
        }),
      },
    };
  });

  afterEach(() => {
    delete (window as any).__TAURI__;
    vi.useRealTimers();
  });

  it('allows a no-timeout delivery wait to resolve after a long run', async () => {
    const { signal } = await import('./ipc.js');
    const wait = signal.runAndWait('deliver', ['--json'], 0);
    await invoke.mock.results[0].value;
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    let settled = false;
    wait.finally(() => {
      settled = true;
    });

    vi.advanceTimersByTime(2 * 60 * 60 * 1000);
    await Promise.resolve();
    expect(settled).toBe(false);

    listeners.get('sidecar:response')?.({
      payload: { id: 'req-1', ok: true, output: '{"ok":true}' },
    });

    await expect(wait).resolves.toBe('{"ok":true}');
  });

  it('rejects a no-timeout wait when the sidecar terminates', async () => {
    const { signal } = await import('./ipc.js');
    const wait = signal.runAndWait('deliver', ['--json'], 0);
    await invoke.mock.results[0].value;
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(listeners.has('sidecar:terminated')).toBe(true);
    listeners.get('sidecar:terminated')?.({ payload: 1 });

    await expect(wait).rejects.toThrow(/SignalOS Core stopped/);
  });

  it('does not reject a pending delivery when an unrelated stream error arrives', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const { signal } = await import('./ipc.js');
    const wait = signal.runAndWait('deliver', ['--json'], 0);
    await invoke.mock.results[0].value;
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    listeners.get('sidecar:error')?.({ payload: 'Stream failed' });

    listeners.get('sidecar:response')?.({
      payload: { id: 'req-1', ok: true, output: '{"ok":true}' },
    });

    await expect(wait).resolves.toBe('{"ok":true}');
    expect(warn).toHaveBeenCalledWith('Stream failed');
  });

  it('rejects only the matching sidecar request when an error carries an id', async () => {
    const { signal } = await import('./ipc.js');
    const wait = signal.runAndWait('deliver', ['--json'], 0);
    await invoke.mock.results[0].value;
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    listeners.get('sidecar:error')?.({
      payload: { id: 'req-1', error: 'Delivery command failed' },
    });

    await expect(wait).rejects.toThrow(/Delivery command failed/);
  });

  it('names the underlying SignalOS command in timeout errors', async () => {
    const { signal } = await import('./ipc.js');
    const wait = signal.runAndWait('deliver', ['--json'], 5000);
    await invoke.mock.results[0].value;
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    vi.advanceTimersByTime(5000);

    await expect(wait).rejects.toThrow(/Timed out waiting for SignalOS Core command "deliver"/);
  });
});
