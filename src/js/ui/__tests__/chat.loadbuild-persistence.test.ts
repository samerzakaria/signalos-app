/**
 * #50 regression: loadBuild() runs on EVERY switch to the Build tab
 * (switchTab's loaders). It must NOT clobber a live or already-loaded
 * conversation -- unconditionally overwriting chatBubbles wiped a mid-flight
 * chat back to just the welcome message whenever the user navigated away and
 * came back ("new chat without history, even mid-something").
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { chatBubbles } from '../../../state';

const { activeBuildId, loadConvHistory } = vi.hoisted(() => ({
  activeBuildId: vi.fn(async () => 'build-1'),
  loadConvHistory: vi.fn(async () => [] as unknown[]),
}));
vi.mock('../../conversation.js', () => ({
  activeBuildId,
  appendTurn: vi.fn(),
  loadHistory: loadConvHistory,
}));
vi.mock('../../app-v2.js', () => ({
  loadEnforcement: vi.fn(async () => {}),
  updateCostDisplay: vi.fn(),
}));
// Keep chat.js's other module-load imports inert.
vi.mock('../../ipc.js', () => ({
  enforcement: { precheck: vi.fn(), state: vi.fn() },
  signal: { run: vi.fn(), runAndWait: vi.fn() },
  provider: {},
}));

import { loadBuild } from '../chat.js';

describe('loadBuild — conversation persistence across tab switches (#50)', () => {
  beforeEach(() => {
    chatBubbles.value = [];
    activeBuildId.mockClear();
    loadConvHistory.mockClear();
  });

  it('hydrates welcome + persisted history when the chat is EMPTY (first visit)', async () => {
    loadConvHistory.mockResolvedValueOnce([{ user_idea: 'a task app', ai_summary: 'scoped it' }]);
    await loadBuild();
    const texts = chatBubbles.value.map((b) => b.text);
    expect(texts.some((t) => /what do you want to build/i.test(t))).toBe(true);
    expect(texts).toContain('a task app');
    expect(texts).toContain('scoped it');
  });

  it('does NOT clobber a LIVE conversation on re-entry (the tab-switch wipe)', async () => {
    chatBubbles.value = [
      { id: 'welcome', kind: 'ai', text: 'Hi!', historical: true },
      { id: 'u1', kind: 'user', text: 'build me a game' },
      { id: 'a1', kind: 'streaming', text: 'working on it…' },
    ];
    await loadBuild();
    // history is NOT reloaded, and the in-flight chat is preserved verbatim.
    expect(loadConvHistory).not.toHaveBeenCalled();
    const texts = chatBubbles.value.map((b) => b.text);
    expect(texts).toContain('build me a game');
    expect(texts).toContain('working on it…');
    expect(chatBubbles.value.length).toBe(3);
  });
});
