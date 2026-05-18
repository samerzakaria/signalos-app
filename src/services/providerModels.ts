import { effect } from '@preact/signals';
import { ai, providerModels, type ProviderModel } from '../state';

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = window.__TAURI__;
  const invoke = tauri?.core?.invoke || tauri?.invoke;
  if (!invoke) throw new Error('Tauri runtime not available');
  return invoke<T>(cmd, args);
}

effect(() => {
  const provider = ai.value;
  if (!provider) return;
  tauriInvoke<ProviderModel[]>('fetch_provider_models', { provider, api_key: null })
    .then((models) => {
      providerModels.value = models || [];
    })
    .catch((e) => {
      console.warn('Could not load provider models:', e);
      providerModels.value = [];
    });
});
