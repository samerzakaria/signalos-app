/**
 * chat.freeze-consolidation.test.ts — Milestone 2-b / AMD-CORE-107.
 *
 * Documents the JS half of the freeze-state dual-write contract.
 *
 * The user can freeze a wave either by clicking the Toolbar's Freeze
 * button (Rust mutex) or by typing `/signal-freeze` in chat (Python
 * record). Historically these were independent: a chat freeze didn't
 * flip the Rust mutex so the Toolbar still showed "not frozen".
 *
 * AMD-CORE-107 mandates one source of truth. The chosen design:
 * `chat.js` calls BOTH `ipc.signal.runAndWait` (preserves the durable
 * Python audit record) AND `ipc.enforcement.freeze` (flips the Rust
 * mutex that the UI reads). This test asserts both calls happen.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  chatBubbles,
  chatInputValue,
  busy,
  cmdPaletteOpen,
} from '../../../state';

// Mock ipc.js BEFORE importing chat.js so the imports resolve to our
// spies. Path is relative to this test file: src/js/ui/__tests__/ ->
// src/js/ipc.js is two levels up.
const runAndWait = vi.fn();
const enforcementFreeze = vi.fn();
const enforcementUnfreeze = vi.fn();
const providerChatStream = vi.fn();
const providerGetCost = vi.fn();

vi.mock('../../ipc.js', () => ({
  signal: {
    run: vi.fn(),
    runAndWait,
    cancelPending: vi.fn(),
  },
  enforcement: {
    state: vi.fn(),
    precheck: vi.fn(),
    override: vi.fn(),
    setMode: vi.fn(),
    freeze: enforcementFreeze,
    unfreeze: enforcementUnfreeze,
  },
  provider: {
    chatStream: providerChatStream,
    getCost: providerGetCost,
  },
}));

// Avoid pulling in app-v2.js + conversation.js heavyweight side effects.
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
  showError: vi.fn(),
}));

// Import AFTER mocks so chat.js binds to the mocked modules.
// Using a top-level await with dynamic import keeps vi.mock hoisting safe.
const chatModule = await import('../chat.js');

describe('chat /signal-freeze dual-write (AMD-CORE-107)', () => {
  beforeEach(() => {
    runAndWait.mockReset();
    enforcementFreeze.mockReset();
    enforcementUnfreeze.mockReset();
    providerChatStream.mockReset();
    providerGetCost.mockReset();

    chatBubbles.value = [];
    chatInputValue.value = '';
    busy.value = false;
    cmdPaletteOpen.value = false;
  });

  it('typing /signal-freeze calls BOTH the Python CLI and the Rust enforcement IPC', async () => {
    runAndWait.mockResolvedValueOnce('frozen freeze-001: src');
    enforcementFreeze.mockResolvedValueOnce(undefined);

    chatInputValue.value = '/signal-freeze src --wave W14';
    await (window as unknown as { sendMsg: () => Promise<void> }).sendMsg();

    // Python CLI must be invoked with the parsed command + args.
    expect(runAndWait).toHaveBeenCalledTimes(1);
    expect(runAndWait).toHaveBeenCalledWith(
      'signal-freeze',
      ['src', '--wave', 'W14'],
      60000,
    );

    // Rust mutex flip must also be invoked — this is the consolidation point.
    expect(enforcementFreeze).toHaveBeenCalledTimes(1);
    expect(enforcementUnfreeze).not.toHaveBeenCalled();
  });

  it('typing /signal-unfreeze calls BOTH the Python CLI and ipc.enforcement.unfreeze', async () => {
    runAndWait.mockResolvedValueOnce('unfrozen src');
    enforcementUnfreeze.mockResolvedValueOnce(undefined);

    chatInputValue.value = '/signal-unfreeze src';
    await (window as unknown as { sendMsg: () => Promise<void> }).sendMsg();

    expect(runAndWait).toHaveBeenCalledTimes(1);
    expect(runAndWait).toHaveBeenCalledWith(
      'signal-unfreeze',
      ['src'],
      60000,
    );
    expect(enforcementUnfreeze).toHaveBeenCalledTimes(1);
    expect(enforcementFreeze).not.toHaveBeenCalled();
  });

  it('a Rust IPC failure after a successful CLI run does NOT raise to the user', async () => {
    // The CLI succeeded — the durable audit record is written. The Rust
    // mutex flip then fails (e.g. transient IPC glitch). The user's
    // chat flow must not break: we log a warning and continue.
    runAndWait.mockResolvedValueOnce('frozen freeze-002: src');
    enforcementFreeze.mockRejectedValueOnce(new Error('IPC offline'));
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    chatInputValue.value = '/signal-freeze src --wave W14';
    await (window as unknown as { sendMsg: () => Promise<void> }).sendMsg();

    expect(runAndWait).toHaveBeenCalledTimes(1);
    expect(enforcementFreeze).toHaveBeenCalledTimes(1);

    // No error bubble should have been pushed onto the chat.
    const errorBubbles = chatBubbles.value.filter((b) => b.kind === 'error');
    expect(errorBubbles).toHaveLength(0);

    // The warning is the only audit trail of the Rust failure.
    expect(warnSpy).toHaveBeenCalled();
    const calls = warnSpy.mock.calls.map((c) => String(c[0]));
    expect(calls.some((s) => s.includes('freeze-consolidation'))).toBe(true);
    warnSpy.mockRestore();
  });

  it('other slash commands do NOT trigger the enforcement IPC', async () => {
    // Sanity check: we only flip the Rust mutex for signal-freeze /
    // signal-unfreeze. A /signal-status should leave the mutex alone.
    runAndWait.mockResolvedValueOnce('ok');

    chatInputValue.value = '/signal-status';
    await (window as unknown as { sendMsg: () => Promise<void> }).sendMsg();

    expect(runAndWait).toHaveBeenCalledTimes(1);
    expect(enforcementFreeze).not.toHaveBeenCalled();
    expect(enforcementUnfreeze).not.toHaveBeenCalled();
  });

  it('routes plain natural-language messages through the governed agent loop', async () => {
    runAndWait.mockResolvedValueOnce({ run_id: 'agent-1', status: 'completed' });

    chatInputValue.value = 'build a task management system';
    await (window as unknown as { sendMsg: () => Promise<void> }).sendMsg();

    expect(runAndWait).toHaveBeenCalledTimes(1);
    expect(runAndWait).toHaveBeenCalledWith(
      'agent:run',
      [JSON.stringify({ prompt: 'build a task management system' })],
      600000,
    );
    expect(enforcementFreeze).not.toHaveBeenCalled();
    expect(enforcementUnfreeze).not.toHaveBeenCalled();
  });
});

// Reference the module so unused-import lints don't drop it. (The
// import is the load-bearing side-effect: it registers window.sendMsg.)
void chatModule;
