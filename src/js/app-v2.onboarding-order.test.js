import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('app-v2 onboarding setup order', () => {
  function finishOnboardingBody() {
    const source = readFileSync(resolve(process.cwd(), 'src/js/app-v2.js'), 'utf8');
    const start = source.indexOf('async function finishOnboarding()');
    const end = source.indexOf('window.finishOnboarding = finishOnboarding;');
    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    return source.slice(start, end);
  }

  it('creates and initializes the workspace before writing workspace-scoped identity', () => {
    const body = finishOnboardingBody();
    const ensureWorkspace = body.indexOf('ipc.workspace.ensureDefault');
    const initWorkspace = body.indexOf('ipc.signal.runAndWait("signal-init"');
    const setIdentity = body.indexOf('ipc.identity.set');

    expect(ensureWorkspace).toBeGreaterThanOrEqual(0);
    expect(initWorkspace).toBeGreaterThan(ensureWorkspace);
    expect(setIdentity).toBeGreaterThan(initWorkspace);
  });

  it('does not fail onboarding when provider validation rejects the API key', () => {
    const body = finishOnboardingBody();

    expect(body).not.toContain('Provider connection failed');
    expect(body).not.toContain('Ollama connection failed');
    expect(body).toContain('providerWarning');
    expect(body).toContain('showWarning(providerWarning)');
    expect(body).toContain('tested: providerReady');
  });

  it('validates provider access before storing the typed API key', () => {
    const body = finishOnboardingBody();
    const providerBlockStart = body.indexOf('if (apiKey)');
    const providerBlockEnd = body.indexOf('} else if (state.ai === "ollama")');
    const providerBlock = body.slice(providerBlockStart, providerBlockEnd);

    const fetchModels = providerBlock.indexOf('refreshCurrentProviderModels(apiKey)');
    const testProvider = providerBlock.indexOf('ipc.provider.test(state.ai, apiKey, state.aiModel)');
    const storeKey = providerBlock.indexOf('ipc.keychain.store(state.ai, apiKey)');

    expect(fetchModels).toBeGreaterThanOrEqual(0);
    expect(testProvider).toBeGreaterThan(fetchModels);
    expect(storeKey).toBeGreaterThan(testProvider);
  });
});
