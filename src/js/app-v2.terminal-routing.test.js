import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('app-v2 terminal routing', () => {
  it('maps visible terminal chips to supported IPC commands instead of raw command strings', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/js/app-v2.js'), 'utf8');
    const start = source.indexOf('async function runTerminalCommand(cmd)');
    const end = source.indexOf('async function termExecReal(cmd)');
    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const router = source.slice(start, end);
    expect(router).toContain('lower === "help"');
    expect(router).toContain('inStarterWorkspace()');
    expect(router).toContain('You are in the starter workspace, not a product repo.');
    expect(router).toContain('lower === "signalos status"');
    expect(router).toContain('ipc.signal.runAndWait("signal-status"');
    expect(router).toContain('lower === "signalos check"');
    expect(router).toContain('ipc.signal.runAndWait("signal-release-readiness"');
    expect(router).toContain('lower === "signalos gates"');
    expect(router).toContain('ipc.signal.runAndWait("state:gates"');
    expect(router).toContain('lower === "git status"');
    expect(router).toContain('ipc.git.status()');
    expect(router).toContain('lower === "npm run dev"');
    expect(source).not.toContain('ipc.signal.runAndWait(cmd.replace');
  });
});
