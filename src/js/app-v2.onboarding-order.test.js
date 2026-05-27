import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('app-v2 onboarding setup order', () => {
  it('creates and initializes the workspace before writing workspace-scoped identity', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/js/app-v2.js'), 'utf8');
    const start = source.indexOf('async function finishOnboarding()');
    const end = source.indexOf('window.finishOnboarding = finishOnboarding;');
    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const body = source.slice(start, end);
    const ensureWorkspace = body.indexOf('ipc.workspace.ensureDefault');
    const initWorkspace = body.indexOf('ipc.signal.runAndWait("signal-init"');
    const setIdentity = body.indexOf('ipc.identity.set');

    expect(ensureWorkspace).toBeGreaterThanOrEqual(0);
    expect(initWorkspace).toBeGreaterThan(ensureWorkspace);
    expect(setIdentity).toBeGreaterThan(initWorkspace);
  });
});
