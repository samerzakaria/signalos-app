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
    expect(state.providerModelsError.value).toBe('anthropic rejected the API key. Replace it in Settings, then refresh models.');
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

    expect(state.providerModelsError.value).toBe('anthropic models could not be loaded right now. Refresh again later or replace the key.');
  });
});
