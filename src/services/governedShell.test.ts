import { describe, expect, it, vi } from 'vitest';
import {
  runGovernedCommand,
  isGovernedCommand,
  splitGovernedCommand,
  type GovernedShellIpc,
} from './governedShell';

function fakeIpc(overrides: Partial<GovernedShellIpc> = {}): GovernedShellIpc {
  return {
    signal: { runAndWait: vi.fn(async (cmd: string) => [`ran ${cmd}`]) },
    git: { status: vi.fn(async () => ({ branch: 'main', is_clean: true, ahead: 0, behind: 0 })) },
    ...overrides,
  };
}

describe('isGovernedCommand', () => {
  it('detects any slash or signalos command as governed', () => {
    expect(isGovernedCommand('/signal-status')).toBe(true);
    expect(isGovernedCommand('/validate-gate')).toBe(true);
    expect(isGovernedCommand('  /state:gates ')).toBe(true);
    expect(isGovernedCommand('signalos validate-gate --gate 5')).toBe(true);
    expect(isGovernedCommand('build me an app')).toBe(false);
  });
});

describe('splitGovernedCommand', () => {
  it('preserves quoted Windows paths', () => {
    expect(splitGovernedCommand('signalos cost --ledger "C:\\tmp\\ai usage.jsonl"')).toEqual([
      'signalos',
      'cost',
      '--ledger',
      'C:\\tmp\\ai usage.jsonl',
    ]);
  });
});

describe('runGovernedCommand', () => {
  it('returns help lines', async () => {
    const out = await runGovernedCommand('help', { workspace: '/w', inStarterWorkspace: false, ipc: fakeIpc() });
    expect(Array.isArray(out)).toBe(true);
    expect((out as string[])[0]).toMatch(/Supported commands/);
  });

  it('routes /signal-status through the signal IPC', async () => {
    const ipc = fakeIpc();
    const out = await runGovernedCommand('/signal-status', { workspace: '/w', inStarterWorkspace: false, ipc });
    expect(ipc.signal.runAndWait).toHaveBeenCalledWith('signal-status', [], 60000);
    expect((out as string[])[0]).toBe('ran signal-status');
  });

  it('routes arbitrary slash commands through the signal IPC', async () => {
    const ipc = fakeIpc();
    await runGovernedCommand('/bundle list --category commands', {
      workspace: '/w',
      inStarterWorkspace: false,
      ipc,
    });

    expect(ipc.signal.runAndWait).toHaveBeenCalledWith(
      'bundle',
      ['list', '--category', 'commands'],
      120000,
    );
  });

  it('preserves quoted args for arbitrary slash commands', async () => {
    const ipc = fakeIpc();
    await runGovernedCommand('/signal-discovery --name "Jane Doe"', {
      workspace: '/w',
      inStarterWorkspace: false,
      ipc,
    });

    expect(ipc.signal.runAndWait).toHaveBeenCalledWith(
      'signal-discovery',
      ['--name', 'Jane Doe'],
      120000,
    );
  });

  it('routes arbitrary signalos commands through the signal IPC', async () => {
    const ipc = fakeIpc();
    await runGovernedCommand('signalos validate-gate --gate 5 --json', {
      workspace: '/w',
      inStarterWorkspace: false,
      ipc,
    });

    expect(ipc.signal.runAndWait).toHaveBeenCalledWith(
      'validate-gate',
      ['--gate', '5', '--json'],
      120000,
    );
  });

  it('guards governance commands in the starter workspace', async () => {
    const out = await runGovernedCommand('signalos status', { workspace: '/w', inStarterWorkspace: true, ipc: fakeIpc() });
    expect((out as string[])[0]).toMatch(/starter workspace/);
  });

  it('formats git status', async () => {
    const out = await runGovernedCommand('git status', { workspace: '/w', inStarterWorkspace: false, ipc: fakeIpc() });
    expect((out as string[])[0]).toBe('branch: main');
  });

  it('delegates npm run dev to startPreview', async () => {
    const out = await runGovernedCommand('npm run dev', {
      workspace: '/w',
      inStarterWorkspace: false,
      ipc: fakeIpc(),
      startPreview: () => 'PREVIEW',
    });
    expect(out).toBe('PREVIEW');
  });

  it('throws on unsupported commands (no silent failure)', async () => {
    await expect(
      runGovernedCommand('rm -rf /', { workspace: '/w', inStarterWorkspace: false, ipc: fakeIpc() }),
    ).rejects.toThrow(/Unsupported command/);
  });
});
