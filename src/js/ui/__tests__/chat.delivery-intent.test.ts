/**
 * chat.delivery-intent.test.ts — Claim 8.
 *
 * `isDeliveryIntent` decides whether a plain chat message starts a governed
 * G0->G5 delivery (agent:deliver, file writes allowed) or a conversational
 * turn (agent:run, execution_context="conversation", writes refused).
 *
 * Regressions this locks:
 *   - The artifact vocabulary must cover common software nouns (module,
 *     login, endpoint, migration, …) — omitting them routed real build
 *     requests to the write-forbidden mode.
 *   - A trailing "?" alone must NOT force conversational ("Can you build me
 *     an app?" is a build request), while genuine leading-interrogative
 *     questions ("what is X?", "how do I …?") stay conversational.
 */

import { describe, it, expect, vi } from 'vitest';

// chat.js touches ipc / util / conversation / app-v2 / waveEngineClient at
// module load and in its send path. Mock them so importing the module for a
// pure classifier test never reaches Tauri.
vi.mock('../../ipc.js', () => ({
  signal: { run: vi.fn(), runAndWait: vi.fn(), cancelPending: vi.fn() },
  enforcement: {
    state: vi.fn(), precheck: vi.fn(), override: vi.fn(),
    setMode: vi.fn(), freeze: vi.fn(), unfreeze: vi.fn(),
  },
  provider: { chatStream: vi.fn(), getCost: vi.fn() },
}));
vi.mock('../../../services/waveEngineClient.ts', () => ({
  translateExternal: vi.fn(),
  tryBegin: vi.fn(async () => null),
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
vi.mock('../../util.js', () => ({ showError: vi.fn(), providerConnectionMessage: vi.fn((e: unknown) => String(e)) }));
vi.mock('@tauri-apps/api/webview', () => ({
  getCurrentWebview: () => ({ onDragDropEvent: async () => () => undefined }),
}));

const { isDeliveryIntent } = await import('../chat.js') as unknown as {
  isDeliveryIntent: (t: string) => boolean;
};

describe('isDeliveryIntent — build vs. conversation routing', () => {
  const delivery = [
    'Create an authentication module',
    'Add login',
    'Can you build me an app?',
    'build a dashboard',
    'Implement a payments endpoint',
    'Add a database migration',
    'Write a CLI script',
    'I want a todo app',
    'scaffold a REST api',
    'Create a login page',
  ];
  for (const prompt of delivery) {
    it(`routes "${prompt}" to delivery`, () => {
      expect(isDeliveryIntent(prompt)).toBe(true);
    });
  }

  const conversation = [
    'what is a closure?',
    'how do I center a div?',
    'thanks!',
    'why does my build fail',
    'explain the auth flow',
    'tell me about migrations',
    '', // empty
    '/signal-status', // slash command
  ];
  for (const prompt of conversation) {
    it(`keeps "${prompt}" conversational`, () => {
      expect(isDeliveryIntent(prompt)).toBe(false);
    });
  }

  it('treats a build request as delivery even when phrased as a question', () => {
    expect(isDeliveryIntent('Can you build me an app?')).toBe(true);
    expect(isDeliveryIntent('Could you add a login form?')).toBe(true);
  });

  it('does not force conversational purely on a trailing "?"', () => {
    // Same sentence with/without the "?" must classify the same way.
    expect(isDeliveryIntent('build a dashboard')).toBe(true);
    expect(isDeliveryIntent('build a dashboard?')).toBe(true);
  });
});
