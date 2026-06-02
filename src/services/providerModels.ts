import { effect } from '@preact/signals';
import {
  ai,
  aiModel,
  providerModels,
  providerModelsError,
  providerModelsLoading,
  type ProviderModel,
} from '../state';

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = window.__TAURI__;
  const invoke = tauri?.core?.invoke || tauri?.invoke;
  if (!invoke) throw new Error('Tauri runtime not available');
  return invoke<T>(cmd, args);
}

function messageFrom(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error.trim()) return error;
  if (error && typeof error === 'object') {
    for (const key of ['message', 'error', 'detail', 'reason']) {
      const value = (error as Record<string, unknown>)[key];
      if (typeof value === 'string' && value.trim()) return value;
    }
  }
  return 'Could not fetch models';
}

function isExpectedMissingKey(message: string): boolean {
  return /requires an api key|api key.*not found|no api key/i.test(message);
}

function isProviderAuthFailure(message: string): boolean {
  return /401|unauthori[sz]ed|invalid api key|invalid key|forbidden/i.test(message);
}

function isProviderNetworkBlock(message: string): boolean {
  return /content is blocked|site owner|cloudflare|blocked by/i.test(message);
}

function providerDisplayName(provider: string): string {
  const names: Record<string, string> = {
    anthropic: 'Anthropic',
    openai: 'OpenAI',
    gemini: 'Gemini',
    qwen: 'Qwen',
    ollama: 'Ollama',
    openrouter: 'OpenRouter',
    deepseek: 'DeepSeek',
    mistral: 'Mistral',
    groq: 'Groq',
    cerebras: 'Cerebras',
    together: 'Together AI',
    xai: 'xAI',
  };
  return names[provider] || provider;
}

function providerConnectionMessage(provider: string, message: string): string {
  const name = providerDisplayName(provider);
  if (isProviderAuthFailure(message)) {
    return `${name} rejected the API key. Replace it in Settings, then refresh models.`;
  }
  if (isProviderNetworkBlock(message)) {
    return `${name} model fetching is blocked by the provider or network. The key can stay saved; refresh models again or switch provider.`;
  }
  if (isExpectedMissingKey(message)) {
    return `${name} needs an API key before models can be fetched.`;
  }
  if (/model list|fetch.*models|returned no models|no models/i.test(message)) {
    return `${name} models could not be loaded right now. Refresh again later or replace the key.`;
  }
  return message || `${name} models could not be loaded right now.`;
}

interface LoadProviderModelsOptions {
  persistSelection?: boolean;
  quietMissingKey?: boolean;
}

export async function loadProviderModels(
  provider = ai.value,
  apiKey?: string | null,
  options: LoadProviderModelsOptions = {},
): Promise<ProviderModel[]> {
  if (!provider) return [];
  providerModelsLoading.value = true;
  providerModelsError.value = null;
  try {
    const models = await tauriInvoke<ProviderModel[]>('fetch_provider_models', {
      provider,
      api_key: apiKey || null,
    });
    const next = models || [];
    providerModels.value = next;
    let selectedChanged = false;
    if (next.length > 0 && !next.some((model) => model.id === aiModel.value)) {
      aiModel.value = next[0].id;
      selectedChanged = true;
    }
    if (selectedChanged && options.persistSelection) {
      try {
        await tauriInvoke('set_provider_model', { provider, model: aiModel.value });
      } catch (e) {
        providerModelsError.value = `Model selected but not saved: ${messageFrom(e)}`;
      }
    }
    return next;
  } catch (e) {
    const message = messageFrom(e);
    providerModels.value = [];
    if (isProviderAuthFailure(message)) {
      try {
        await tauriInvoke('delete_api_key', { provider });
      } catch {
        // Clearing a stale rejected key is best effort; the visible failure is
        // still the provider auth problem.
      }
    }
    providerModelsError.value = options.quietMissingKey && isExpectedMissingKey(message)
      ? null
      : providerConnectionMessage(provider, message);
    if (!options.quietMissingKey || !isExpectedMissingKey(message)) {
      console.warn('Could not load provider models:', e);
    }
    return [];
  } finally {
    providerModelsLoading.value = false;
  }
}

effect(() => {
  const provider = ai.value;
  if (!provider) return;
  void loadProviderModels(provider, null, { quietMissingKey: true });
});
