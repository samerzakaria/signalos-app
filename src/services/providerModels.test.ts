import { beforeEach, describe, expect, it, vi } from 'vitest';

describe('loadProviderModels', () => {
  type Invoke = <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

  beforeEach(() => {
    vi.resetModules();
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  });

  it('shows friendly auth guidance and clears stale rejected provider keys', async () => {
    const invoke = vi.fn(async <T,>(cmd: string): Promise<T> => {
      if (cmd === 'fetch_provider_models') {
        throw new Error('Anthropic model list failed: HTTP 401');
      }
      return null as T;
    });
    window.__TAURI__ = { core: { invoke: invoke as Invoke } };

    const state = await import('../state');
    state.ai.value = 'anthropic';
    state.providerModels.value = [];
    state.providerModelsError.value = null;
    const { loadProviderModels } = await import('./providerModels');

    const result = await loadProviderModels('anthropic', 'bad-key');

    expect(result).toEqual([]);
    expect(state.providerModelsError.value).toBe('Anthropic rejected the API key. Replace it in Settings, then refresh models.');
    expect(invoke).toHaveBeenCalledWith('delete_api_key', { provider: 'anthropic' });
  });

  it('does not expose raw model-list transport failures in Settings', async () => {
    const invoke = vi.fn(async <T,>(cmd: string): Promise<T> => {
      if (cmd === 'fetch_provider_models') {
        throw new Error('Anthropic model list returned an unreadable response');
      }
      return null as T;
    });
    window.__TAURI__ = { core: { invoke: invoke as Invoke } };

    const state = await import('../state');
    state.ai.value = 'anthropic';
    state.providerModels.value = [];
    state.providerModelsError.value = null;
    const { loadProviderModels } = await import('./providerModels');

    await loadProviderModels('anthropic', null);

    expect(state.providerModelsError.value).toBe('Anthropic models could not be loaded right now. Refresh again later or replace the key.');
  });

  it('keeps saved keys when provider model fetching is blocked by network edge policy', async () => {
    const invoke = vi.fn(async <T,>(cmd: string): Promise<T> => {
      if (cmd === 'fetch_provider_models') {
        throw new Error('This content is blocked. Contact the site owner to fix the issue.');
      }
      return null as T;
    });
    window.__TAURI__ = { core: { invoke: invoke as Invoke } };

    const state = await import('../state');
    state.ai.value = 'anthropic';
    state.providerModels.value = [];
    state.providerModelsError.value = null;
    const { loadProviderModels } = await import('./providerModels');

    await loadProviderModels('anthropic', null);

    expect(state.providerModelsError.value).toBe('Anthropic model fetching is blocked by the provider or network. The key can stay saved; refresh models again or switch provider.');
    expect(invoke).not.toHaveBeenCalledWith('delete_api_key', { provider: 'anthropic' });
  });

  it('clears a stale selected model instead of auto-selecting from the provider list', async () => {
    const invoke = vi.fn(async <T,>(cmd: string): Promise<T> => {
      if (cmd === 'fetch_provider_models') {
        return [
          { id: 'claude-sonnet-4-5-20250929', name: 'Claude Sonnet 4.5' },
          { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5' },
        ] as T;
      }
      return null as T;
    });
    window.__TAURI__ = { core: { invoke: invoke as Invoke } };

    const state = await import('../state');
    state.ai.value = '';
    state.aiModel.value = 'retired-model';
    state.providerModels.value = [];
    state.providerModelsError.value = null;
    const { loadProviderModels } = await import('./providerModels');

    const result = await loadProviderModels('anthropic', 'valid-key', { persistSelection: true });

    expect(result).toHaveLength(2);
    expect(state.aiModel.value).toBe('');
    expect(invoke).toHaveBeenCalledWith('set_provider_model', {
      provider: 'anthropic',
      model: '',
    });
  });

  it('#52 retries a TRANSIENT fetch failure and then succeeds', async () => {
    let calls = 0;
    const invoke = vi.fn(async <T,>(cmd: string): Promise<T> => {
      if (cmd === 'fetch_provider_models') {
        calls += 1;
        if (calls === 1) throw new Error('engine not ready yet'); // transient
        return [{ id: 'claude-sonnet-4-5', label: 'Claude' }] as T;
      }
      return null as T;
    });
    window.__TAURI__ = { core: { invoke: invoke as Invoke } };

    const state = await import('../state');
    state.ai.value = 'anthropic';
    state.providerModels.value = [];
    const { loadProviderModels } = await import('./providerModels');

    const result = await loadProviderModels('anthropic', 'good-key');

    expect(calls).toBe(2); // retried once, succeeded
    expect(result).toHaveLength(1);
    expect(state.providerModels.value).toHaveLength(1);
  });

  it('#52 retries an empty result once, then keeps the models when they arrive', async () => {
    let calls = 0;
    const invoke = vi.fn(async <T,>(cmd: string): Promise<T> => {
      if (cmd === 'fetch_provider_models') {
        calls += 1;
        // first attempt: engine not ready -> empty; second: models present.
        return (calls === 1 ? [] : [{ id: 'claude-sonnet-4-5', label: 'Claude' }]) as T;
      }
      return null as T;
    });
    window.__TAURI__ = { core: { invoke: invoke as Invoke } };

    const state = await import('../state');
    state.ai.value = 'anthropic';
    state.providerModels.value = [];
    const { loadProviderModels } = await import('./providerModels');

    const result = await loadProviderModels('anthropic', 'good-key');

    expect(calls).toBe(2); // an empty result is transient -> retried
    expect(result).toHaveLength(1);
  });
});
