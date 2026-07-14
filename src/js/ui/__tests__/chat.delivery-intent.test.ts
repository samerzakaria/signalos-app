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

import { beforeEach, describe, it, expect, vi } from 'vitest';

// chat.js touches ipc / util / conversation / app-v2 / waveEngineClient at
// module load and in its send path. Mock them so importing the module for a
// pure classifier test never reaches Tauri.
vi.mock('../../ipc.js', () => ({
  signal: { run: vi.fn(), runAndWait: vi.fn(), cancelPending: vi.fn() },
  gates: { getAll: vi.fn(async () => []), sign: vi.fn() },
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

import {
  busy,
  chatBubbles,
  chatInputValue,
  govGatesList,
  workspacePath,
} from '../../../state';
import { signal, gates } from '../../ipc.js';

const { isDeliveryIntent, isApprovalIntent, loadBuild } = await import('../chat.js') as unknown as {
  isDeliveryIntent: (t: string, ctx?: { hasProduct?: boolean }) => boolean;
  isApprovalIntent: (t: string) => boolean;
  loadBuild: () => Promise<void>;
};

const runAndWait = signal.runAndWait as unknown as ReturnType<typeof vi.fn>;
const getAll = gates.getAll as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  workspacePath.value = '';
  govGatesList.value = [];
  chatBubbles.value = [];
  chatInputValue.value = '';
  busy.value = false;
  runAndWait.mockReset();
  runAndWait.mockResolvedValue(null);
  getAll.mockReset();
  getAll.mockResolvedValue([]);
});

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

describe('isDeliveryIntent — imperative edit follow-ups against an existing product', () => {
  // The hole: imperative edits with no artifact noun ("fix it", "refactor this",
  // "undo that", "make it dark mode") and change verbs missing from the action
  // list ("remove"/"delete") all fell through to conversational agent:run, where
  // writes are refused -- so the user's change was silently dropped.
  const imperativeEdits = [
    'fix it',
    'remove the login page',
    'delete the login page',
    'refactor this',
    'make it dark mode',
    'undo that',
    'rename the component',
    'change the header color',
    'revert the last change',
    'simplify this function',
  ];
  for (const prompt of imperativeEdits) {
    it(`routes "${prompt}" to the build path WHEN a product exists`, () => {
      expect(isDeliveryIntent(prompt, { hasProduct: true })).toBe(true);
    });
    it(`leaves "${prompt}" conversational when NO product exists yet`, () => {
      // Nothing to change yet -> not a silent drop; stays a normal turn.
      expect(isDeliveryIntent(prompt, { hasProduct: false })).toBe(false);
      expect(isDeliveryIntent(prompt)).toBe(false); // no context == no product
    });
  }

  it('keeps genuine questions conversational even with a product open', () => {
    expect(isDeliveryIntent('how does the auth flow work?', { hasProduct: true })).toBe(false);
    expect(isDeliveryIntent('what is a closure?', { hasProduct: true })).toBe(false);
    expect(isDeliveryIntent('why does removing login break the build?', { hasProduct: true })).toBe(false);
  });

  it('still routes explicit build requests to delivery regardless of context', () => {
    expect(isDeliveryIntent('build a dashboard', { hasProduct: false })).toBe(true);
    expect(isDeliveryIntent('Create a login page', { hasProduct: true })).toBe(true);
  });
});

describe('isApprovalIntent — founder approving Gate 0 in chat (C1)', () => {
  // This exact sentence is both approval and a dual-seat authority contract.
  // Broad positive sentiment is intentionally not enough.
  const approvals = [
    'I approve Gate 0 as sole founder',
  ];
  for (const prompt of approvals) {
    it(`treats "${prompt}" as an approval`, () => {
      expect(isApprovalIntent(prompt)).toBe(true);
    });
  }

  const notApprovals = [
    '', // empty
    '/approve', // slash command, not a chat approval
    'approve',
    'I agree',
    'looks good',
    'approve gate 0 as sole founder',
    'APPROVE G0 AS SOLO FOUNDER',
    'I approve G0 as the sole founder.',
    ' I approve Gate 0 as sole founder',
    'I approve Gate 0 as sole founder ',
    'I approve Gate 0 as sole founder\n',
    'not approve Gate 0 as sole founder',
    'I do not approve Gate 0 as sole founder',
    'Can I approve Gate 0 as sole founder?',
    'Could I approve Gate 0 as sole founder',
    'Maybe approve Gate 0 as sole founder',
    'I might approve Gate 0 as sole founder',
    'how do I approve gate 0?', // a question
    'why do I need to approve this?',
    // A long build request that happens to contain "approve" must NOT sign the gate.
    'build me an approvals dashboard where a manager can approve expense reports and sign them',
    'add an approve button to the settings page',
  ];
  for (const prompt of notApprovals) {
    it(`does NOT treat "${prompt}" as an approval`, () => {
      expect(isApprovalIntent(prompt)).toBe(false);
    });
  }
});

describe('Gate 0 frontend seam', () => {
  const current = [
    { id: 0, name: 'Constitution', status: 'current' },
    { id: 1, name: 'Belief', status: 'locked' },
  ];
  const signed = [
    { id: 0, name: 'Constitution', status: 'signed' },
    { id: 1, name: 'Belief', status: 'current' },
  ];

  it('routes exact chat consent through gate0:approve and refreshes numeric G0 UI state', async () => {
    workspacePath.value = 'C:/Products/Task App';
    govGatesList.value = current;
    chatBubbles.value = [{
      id: 'g0-card',
      kind: 'system',
      text: 'Review G0',
      gate: 'G0',
      waveAction: 'gate-approval',
      approvalWorkspace: 'C:/Products/Task App',
      approvalProjectId: 'default',
    }];
    chatInputValue.value = 'I approve Gate 0 as sole founder';
    runAndWait.mockImplementation(async (command: string) => {
      if (command === 'gate0:approve') return { signed: true, gates: signed };
      return null;
    });

    await window.sendMsg();

    const calls = runAndWait.mock.calls.filter((call) => call[0] === 'gate0:approve');
    expect(calls).toHaveLength(1);
    expect(JSON.parse(calls[0][1][0])).toMatchObject({
      consent: 'I approve Gate 0 as sole founder',
      via: 'chat',
      expected_workspace: 'C:/Products/Task App',
      expected_project_id: 'default',
    });
    expect(govGatesList.value[0]).toMatchObject({ id: 0, status: 'signed' });
    expect(chatBubbles.value.find((bubble) => bubble.id === 'g0-card')?.waveResolved)
      .toMatchObject({ choice: 'approve' });
    expect(chatBubbles.value.some((bubble) => bubble.waveAction === 'gate-approved')).toBe(true);
    expect(busy.value).toBe(false);
  });

  it('reconstructs the approval card on Build reload and never duplicates it', async () => {
    workspacePath.value = 'C:/Products/Task App';
    getAll.mockResolvedValue(current);

    await loadBuild();
    await loadBuild();

    expect(chatBubbles.value.filter((bubble) => bubble.waveAction === 'gate-approval')).toHaveLength(1);
    expect(chatBubbles.value.find((bubble) => bubble.waveAction === 'gate-approval'))
      .toMatchObject({
        approvalWorkspace: 'C:/Products/Task App',
        approvalProjectId: 'default',
      });

    getAll.mockResolvedValue(signed);
    await loadBuild();
    expect(chatBubbles.value.find((bubble) => bubble.waveAction === 'gate-approval')?.waveResolved)
      .toMatchObject({ choice: 'approve' });
  });
});
