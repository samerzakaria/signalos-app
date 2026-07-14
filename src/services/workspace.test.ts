import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/preact';
import { h } from 'preact';

vi.mock('../js/ipc', () => ({
  signal: {
    run: vi.fn(),
    runAndWait: vi.fn(async () => null),
    cancelPending: vi.fn(),
  },
  gates: {
    getAll: vi.fn(async () => []),
    sign: vi.fn(),
  },
}));

import {
  chatBubbles,
  govGatesList,
  userName,
  userRole,
  workspacePath,
  type Gate,
} from '../state';
import {
  GATE0_CONSENT_TOKEN,
  approveGate0,
  canonicalGateId,
  createSignalosProject,
  initWorkspace,
  pickWorkspaceFolder,
  reconcileGate0ApprovalAffordance,
} from './workspace';
import { gates, signal } from '../js/ipc';
import { activeProjectId } from './projectPicker';
import { ChatBubbleSystem } from '../components/ChatBubbleSystem';

const runAndWait = signal.runAndWait as unknown as ReturnType<typeof vi.fn>;
const getAll = gates.getAll as unknown as ReturnType<typeof vi.fn>;

const currentGates = (): Gate[] => [
  { id: 0, name: 'Constitution', status: 'current' },
  { id: 1, name: 'Belief', status: 'locked' },
];

const signedGates = (): Gate[] => [
  { id: 0, name: 'Constitution', status: 'signed' },
  { id: 1, name: 'Belief', status: 'current' },
];

describe('workspace factory and Gate 0 approval seam', () => {
  let invoke: ReturnType<typeof vi.fn>;
  let mkdir: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    workspacePath.value = '';
    userName.value = 'User';
    userRole.value = 'PO';
    chatBubbles.value = [];
    govGatesList.value = [];
    activeProjectId.value = 'default';
    runAndWait.mockReset();
    runAndWait.mockResolvedValue(null);
    getAll.mockReset();
    getAll.mockResolvedValue(currentGates());
    mkdir = vi.fn(async () => undefined);
    invoke = vi.fn(async (cmd: string, args?: Record<string, unknown>) => {
      if (cmd === 'read_workspace_file') {
        const path = String(args?.relative_path || '');
        if (path.endsWith('plan-template.md')) return '# Plan';
        return '# {Product Name}\nOwner: {PO}\nDate: [DATE]';
      }
      if (cmd === 'get_workspace_status') return { status: 'initialized' };
      return null;
    });
    window.__TAURI__ = {
      core: { invoke },
      fs: { mkdir },
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('creates, reports document preparation honestly, and recognizes backend numeric G0', async () => {
    const result = await createSignalosProject('C:/Products/Task App', 'Task App', 'react-vite');

    expect(mkdir).toHaveBeenCalledWith('C:/Products/Task App', { recursive: true });
    expect(invoke).toHaveBeenCalledWith('set_workspace', { path: 'C:/Products/Task App' });
    expect(runAndWait).toHaveBeenCalledWith(
      'signal-init',
      ['--mode', 'keep', '--name', 'Task App', '--profile', 'react-vite'],
      expect.any(Number),
    );
    expect(runAndWait.mock.calls.some((call) => call[0] === 'gate0:approve')).toBe(false);
    expect(runAndWait.mock.calls.some((call) => call[0] === 'signal-sign')).toBe(false);
    expect(result.governance).toMatchObject({
      signed: false,
      awaitingApproval: true,
      gateStateVerified: true,
      failed: [],
    });
    expect(result.governance.filled).toHaveLength(3);
    expect(govGatesList.value[0]).toMatchObject({ id: 0, status: 'current' });
  });

  it('distinguishes missing documents from unchanged documents instead of claiming both were filled', async () => {
    invoke.mockImplementation(async (cmd: string, args?: Record<string, unknown>) => {
      if (cmd === 'read_workspace_file') {
        const path = String(args?.relative_path || '');
        if (path.endsWith('DECISION-DNA.md')) throw new Error('missing from scaffold');
        return '# Already prepared';
      }
      if (cmd === 'get_workspace_status') return { status: 'initialized' };
      return null;
    });

    const result = await createSignalosProject('C:/Products/Task App', 'Task App');

    expect(result.governance.filled).toEqual([]);
    expect(result.governance.unchanged).toHaveLength(2);
    expect(result.governance.failed).toEqual([
      expect.objectContaining({ path: expect.stringContaining('DECISION-DNA.md'), error: 'missing from scaffold' }),
    ]);
  });

  it('uses one backend transaction and accepts only its fresh strictly-signed G0 projection', async () => {
    workspacePath.value = 'C:/Products/Task App';
    govGatesList.value = currentGates();
    reconcileGate0ApprovalAffordance(currentGates());
    runAndWait.mockImplementation(async (command: string) => {
      if (command === 'gate0:approve') return { signed: true, gates: signedGates() };
      return null;
    });

    const result = await approveGate0({
      via: 'chat',
      consent: GATE0_CONSENT_TOKEN,
      expectedWorkspace: 'C:/Products/Task App',
      expectedProjectId: 'default',
    });

    const approvalCalls = runAndWait.mock.calls.filter((call) => call[0] === 'gate0:approve');
    expect(approvalCalls).toHaveLength(1);
    expect(JSON.parse(approvalCalls[0][1][0])).toEqual({
      consent: GATE0_CONSENT_TOKEN,
      via: 'chat',
      expected_workspace: 'C:/Products/Task App',
      expected_project_id: 'default',
    });
    expect(runAndWait.mock.calls.some((call) => call[0] === 'signal-sign')).toBe(false);
    expect(runAndWait.mock.calls.some((call) => call[0] === 'audit:append')).toBe(false);
    expect(result.signed).toBe(true);
    expect(govGatesList.value[0]).toMatchObject({ id: 0, status: 'signed' });
    expect(chatBubbles.value.find((bubble) => bubble.waveAction === 'gate-approval')?.waveResolved)
      .toMatchObject({ choice: 'approve' });
  });

  it('rejects a success flag when the returned strict projection still has numeric G0 open', async () => {
    workspacePath.value = 'C:/Products/Task App';
    runAndWait.mockResolvedValue({ signed: true, gates: currentGates() });

    const result = await approveGate0({
      consent: GATE0_CONSENT_TOKEN,
      expectedWorkspace: 'C:/Products/Task App',
      expectedProjectId: 'default',
    });

    expect(result.signed).toBe(false);
    expect(result.reason).toMatch(/did not contain a fresh strictly signed/i);
    expect(govGatesList.value[0]).toMatchObject({ id: 0, status: 'current' });
  });

  it('fails closed without exact consent or when a stale card targets another workspace', async () => {
    workspacePath.value = 'C:/Products/Task App';

    const missingConsent = await approveGate0({ via: 'button', expectedProjectId: 'default' });
    const staleCard = await approveGate0({
      via: 'button',
      consent: GATE0_CONSENT_TOKEN,
      expectedWorkspace: 'C:/Products/Other App',
      expectedProjectId: 'default',
    });

    expect(missingConsent.signed).toBe(false);
    expect(missingConsent.reason).toMatch(/exact/i);
    expect(staleCard.signed).toBe(false);
    expect(staleCard.reason).toMatch(/different workspace/i);
    expect(runAndWait).not.toHaveBeenCalled();
  });

  it('coalesces concurrent button/chat approval into one backend mutation', async () => {
    workspacePath.value = 'C:/Products/Task App';
    let resolveApproval: ((value: unknown) => void) | undefined;
    runAndWait.mockImplementation((command: string) => {
      if (command !== 'gate0:approve') return Promise.resolve(null);
      return new Promise((resolve) => { resolveApproval = resolve; });
    });
    const options = {
      consent: GATE0_CONSENT_TOKEN,
      expectedWorkspace: 'C:/Products/Task App',
      expectedProjectId: 'default',
    } as const;

    const first = approveGate0({ ...options, via: 'button' });
    const invalidConcurrent = await approveGate0({ via: 'chat', expectedProjectId: 'default' });
    const second = approveGate0({ ...options, via: 'chat' });
    expect(runAndWait.mock.calls.filter((call) => call[0] === 'gate0:approve')).toHaveLength(1);
    expect(invalidConcurrent).toMatchObject({ signed: false, reason: expect.stringMatching(/exact/i) });
    resolveApproval?.({ signed: true, gates: signedGates() });

    const firstResult = await first;
    const secondResult = await second;
    expect(firstResult.signed).toBe(true);
    expect(firstResult.coalesced).toBeUndefined();
    expect(secondResult).toMatchObject({ signed: true, coalesced: true });
  });

  it('does not project an in-flight approval response into a newly selected project', async () => {
    workspacePath.value = 'C:/Products/Task App';
    activeProjectId.value = 'alpha';
    govGatesList.value = currentGates();
    let resolveApproval: ((value: unknown) => void) | undefined;
    runAndWait.mockImplementation((command: string) => {
      if (command !== 'gate0:approve') return Promise.resolve(null);
      return new Promise((resolve) => { resolveApproval = resolve; });
    });

    const pending = approveGate0({
      via: 'button',
      consent: GATE0_CONSENT_TOKEN,
      expectedWorkspace: 'C:/Products/Task App',
      expectedProjectId: 'alpha',
    });
    activeProjectId.value = 'beta';
    resolveApproval?.({ signed: true, gates: signedGates() });

    const result = await pending;
    expect(result).toMatchObject({
      signed: false,
      gates: [],
      reason: expect.stringMatching(/previous workspace or project/i),
    });
    expect(govGatesList.value).toEqual(currentGates());
  });

  it('reconstructs one workspace-bound card after reload and resolves it from strict backend state', () => {
    workspacePath.value = 'C:/Products/Task App';
    chatBubbles.value = [];

    reconcileGate0ApprovalAffordance(currentGates(), {
      documentFailures: [{ path: 'DECISION-DNA.md', error: 'missing' }],
    });
    reconcileGate0ApprovalAffordance(currentGates());

    const cards = chatBubbles.value.filter((bubble) => bubble.waveAction === 'gate-approval');
    expect(cards).toHaveLength(1);
    expect(cards[0]).toMatchObject({
      approvalWorkspace: 'C:/Products/Task App',
      approvalProjectId: 'default',
      gate: 'G0',
    });
    expect(cards[0].text).toContain(GATE0_CONSENT_TOKEN);
    expect(cards[0].text).toMatch(/1 governance document could not be prepared/i);

    reconcileGate0ApprovalAffordance(signedGates());
    expect(chatBubbles.value[0].waveResolved).toMatchObject({ choice: 'approve' });
  });

  it('rebinds G0 to a switched project and refuses a click from the stale project card', async () => {
    workspacePath.value = 'C:/Products/Task App';
    activeProjectId.value = 'alpha';
    reconcileGate0ApprovalAffordance(currentGates());

    const staleCard = chatBubbles.value.find((bubble) => bubble.waveAction === 'gate-approval');
    expect(staleCard).toMatchObject({
      approvalWorkspace: 'C:/Products/Task App',
      approvalProjectId: 'alpha',
    });
    expect(staleCard?.waveResolved).toBeUndefined();

    // A project switch does not change the workspace path. The old card must
    // still be retired, and exactly one new card must be actionable for beta.
    activeProjectId.value = 'beta';
    reconcileGate0ApprovalAffordance(currentGates());

    const cards = chatBubbles.value.filter((bubble) => bubble.waveAction === 'gate-approval');
    expect(cards).toHaveLength(2);
    expect(cards.filter((bubble) => !bubble.waveResolved)).toEqual([
      expect.objectContaining({
        approvalWorkspace: 'C:/Products/Task App',
        approvalProjectId: 'beta',
      }),
    ]);
    expect(cards.find((bubble) => bubble.approvalProjectId === 'alpha')?.waveResolved)
      .toMatchObject({ choice: 'workspace-changed' });

    // Simulate a stale rendered frame whose old Alpha button remains visible.
    // The service re-checks the live project binding before joining/mutating a
    // transaction, so this click is refused without touching the backend.
    window.approveGate0 = approveGate0;
    render(h(ChatBubbleSystem, { bubble: staleCard! }));
    fireEvent.click(screen.getByTestId('approve-gate0'));
    await waitFor(() => {
      expect(screen.getByText(/different project/i)).toBeInTheDocument();
    });
    expect(runAndWait).not.toHaveBeenCalled();
  });

  it('normalizes numeric, numeric-string, and prefixed gate IDs', () => {
    expect(canonicalGateId(0)).toBe('G0');
    expect(canonicalGateId('0')).toBe('G0');
    expect(canonicalGateId('g0')).toBe('G0');
    expect(canonicalGateId({ gate_id: 'G0' })).toBe('G0');
  });

  it('switches between product repos by setting the active workspace each time', async () => {
    await initWorkspace('C:/Products/One');
    await initWorkspace('C:/Products/Two');

    expect(invoke).toHaveBeenCalledWith('set_workspace', { path: 'C:/Products/One' });
    expect(invoke).toHaveBeenCalledWith('set_workspace', { path: 'C:/Products/Two' });
    expect(workspacePath.value).toBe('C:/Products/Two');
  });

  it('uses the Tauri folder dialog for workspace picking when available', async () => {
    const open = vi.fn(async () => 'C:/Products/Picked');
    window.__TAURI__ = { core: { invoke }, fs: { mkdir }, dialog: { open } };

    await pickWorkspaceFolder();

    expect(open).toHaveBeenCalledWith({
      directory: true,
      multiple: false,
      title: 'Choose project folder',
    });
    expect(workspacePath.value).toBe('C:/Products/Picked');
  });

  it('rethrows init failures when strict mode is enabled', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    runAndWait.mockImplementation(async (command: string) => {
      if (command === 'signal-init') throw new Error('init failed');
      return null;
    });

    await expect(initWorkspace('C:/Products/Bad App', { strict: true })).rejects.toThrow('init failed');
    expect(workspacePath.value).toBe('C:/Products/Bad App');
  });
});
