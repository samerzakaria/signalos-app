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

  it('does not create a starter workspace during onboarding', () => {
    const body = finishOnboardingBody();

    expect(body).not.toContain('ipc.workspace.ensureDefault("SignalOS Workspace"');
    expect(body).not.toContain('ipc.signal.runAndWait("signal-init"');
    expect(body).not.toContain('ipc.identity.set');
    expect(body).toContain('await ipc.workspace.clear()');
    expect(body).toContain('identity: { name, role }');
    expect(body).toContain('Product workspaces are created later by Deliver/New Project.');
  });

  it('does not fail onboarding when provider validation rejects the API key', () => {
    const body = finishOnboardingBody();

    expect(body).not.toContain('Provider connection failed');
    expect(body).not.toContain('Ollama connection failed');
    expect(body).toContain('providerWarning');
    expect(body).toContain('showWarning(providerWarning)');
    expect(body).toContain('tested: providerReady');
  });

  it('stores the typed API key before provider model fetch so retry can use keychain', () => {
    const body = finishOnboardingBody();
    const providerBlockStart = body.indexOf('if (apiKey)');
    const providerBlockEnd = body.indexOf('} else if (state.ai === "ollama")');
    const providerBlock = body.slice(providerBlockStart, providerBlockEnd);

    const storeKey = providerBlock.indexOf('ipc.keychain.store(state.ai, apiKey)');
    const fetchModels = providerBlock.indexOf('refreshCurrentProviderModels(apiKey)');
    const testProvider = providerBlock.indexOf('ipc.provider.test(state.ai, apiKey, state.aiModel)');
    const deleteRejectedKey = providerBlock.indexOf('ipc.keychain.delete(state.ai)');

    expect(storeKey).toBeGreaterThanOrEqual(0);
    expect(fetchModels).toBeGreaterThanOrEqual(0);
    expect(fetchModels).toBeGreaterThan(storeKey);
    expect(testProvider).toBeGreaterThan(fetchModels);
    expect(deleteRejectedKey).toBeGreaterThan(testProvider);
  });
});
