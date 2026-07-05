/**
 * chat.workspace-guard.test.ts — #53.
 *
 * Foundry builds inside a product *project* (workspace). Onboarding only sets
 * the projects root; the active workspace stays (none) until a project is
 * created/opened. A first delivery — or a reinstall that skipped onboarding via
 * persisted WebView2 state — otherwise fired a build that died in the Rust
 * precheck with a cryptic "Agent run failed: Build precheck failed: No
 * workspace selected", dead-ending the journey.
 *
 * The guard: a delivery intent with no active workspace must NOT fire a build.
 * It posts a plain-language system bubble and opens New Project instead. A
 * delivery WITH a workspace proceeds normally. A "no workspace selected" error
 * bubbling up from the sidecar despite the guard is caught and re-routed too.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  chatBubbles,
  chatInputValue,
  busy,
  ai,
  aiModel,
  workspacePath,
} from '../../../state';

const runAndWait = vi.fn();
const enforcementPrecheck = vi.fn();

vi.mock('../../ipc.js', () => ({
  signal: {
    run: vi.fn(),
    runAndWait,
    cancelPending: vi.fn(),
  },
  enforcement: {
    state: vi.fn(),
    precheck: enforcementPrecheck,
    override: vi.fn(),
    setMode: vi.fn(),
    freeze: vi.fn(),
    unfreeze: vi.fn(),
  },
  provider: {
    chatStream: vi.fn(),
    getCost: vi.fn(),
  },
}));

vi.mock('../../app-v2.js', () => ({
  loadEnforcement: vi.fn(async () => undefined),
  updateCostDisplay: vi.fn(),
}));

vi.mock('../../conversation.js', () => ({
  activeBuildId: vi.fn(async () => 'build-test'),
  appendTurn: vi.fn(async () => undefined),
  loadHistory: vi.fn(async () => []),
}));

vi.mock('../../util.js', () => ({
  providerConnectionMessage: vi.fn((error: unknown) => error instanceof Error ? error.message : String(error)),
  showError: vi.fn(),
}));

await import('../chat.js');

const send = () => (window as unknown as { sendMsg: () => Promise<void> }).sendMsg();

describe('#53 build-with-no-workspace guard', () => {
  let openNewProject: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    runAndWait.mockReset();
    enforcementPrecheck.mockReset();
    chatBubbles.value = [];
    chatInputValue.value = '';
    busy.value = false;
    ai.value = 'anthropic';
    aiModel.value = 'claude-sonnet-4-5';
    workspacePath.value = '';
    openNewProject = vi.fn();
    (window as unknown as { openNewProject: () => void }).openNewProject = openNewProject;
  });

  it('a delivery with NO active workspace guides to New Project instead of firing a build', async () => {
    chatInputValue.value = 'build a task manager app';
    await send();

    // No build was dispatched to the sidecar.
    expect(runAndWait).not.toHaveBeenCalled();
    // The precheck was never even reached (it would have thrown "No workspace").
    expect(enforcementPrecheck).not.toHaveBeenCalled();

    // A plain-language system bubble points the user at project creation.
    const guide = chatBubbles.value.find((b) => b.kind === 'system' && /no project open yet/i.test(b.text || ''));
    expect(guide).toBeTruthy();
    // No raw "No workspace selected" internals leaked as an error bubble.
    expect(chatBubbles.value.some((b) => b.kind === 'error')).toBe(false);

    // New Project was opened for the user, and the composer is not left locked.
    expect(openNewProject).toHaveBeenCalledTimes(1);
    expect(busy.value).toBe(false);
  });

  it('a delivery WITH an active workspace proceeds to the governed build', async () => {
    workspacePath.value = 'C:/Users/foundry/Foundry Projects/task-manager';
    enforcementPrecheck.mockResolvedValueOnce({ allowed: true });
    runAndWait.mockResolvedValueOnce(undefined);

    chatInputValue.value = 'build a task manager app';
    await send();

    // The build entrypoint precheck ran and the delivery command dispatched.
    expect(enforcementPrecheck).toHaveBeenCalledTimes(1);
    expect(runAndWait).toHaveBeenCalledTimes(1);
    expect(runAndWait.mock.calls[0][0]).toBe('agent:deliver');
    // The guide bubble must NOT appear when a workspace is active.
    expect(chatBubbles.value.some((b) => /no project open yet/i.test(b.text || ''))).toBe(false);
    expect(openNewProject).not.toHaveBeenCalled();
  });

  it('a "no workspace selected" error surfacing from the sidecar is caught and re-routed', async () => {
    // state says a workspace is active but the Rust/sidecar side disagrees
    // (divergence) — the delivery throws. The safety net must guide, not leak.
    workspacePath.value = 'C:/Users/foundry/Foundry Projects/task-manager';
    enforcementPrecheck.mockResolvedValueOnce({ allowed: true });
    runAndWait.mockRejectedValueOnce(new Error('No workspace selected'));

    chatInputValue.value = 'build a task manager app';
    await send();

    const guide = chatBubbles.value.find((b) => b.kind === 'system' && /no project open yet/i.test(b.text || ''));
    expect(guide).toBeTruthy();
    expect(chatBubbles.value.some((b) => b.kind === 'error')).toBe(false);
    expect(openNewProject).toHaveBeenCalledTimes(1);
    expect(busy.value).toBe(false);
  });
});
