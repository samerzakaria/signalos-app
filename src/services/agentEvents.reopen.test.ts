import { beforeEach, describe, expect, it, vi } from 'vitest';

// Gate-reopen surface (GATE-REOPEN-DESIGN): the `gate_reopened` agent event
// and the `agent:reopen-gate` transport (reopenGate).

type AgentEventPayload = Record<string, unknown>;
type Listener = (event: { payload: AgentEventPayload }) => void;

describe('agentEvents — gate reopen', () => {
  let listener: Listener | null;
  let run: ReturnType<typeof vi.fn>;
  let runAndWait: ReturnType<typeof vi.fn>;

  async function loadHarness() {
    vi.resetModules();
    listener = null;
    run = vi.fn(async () => undefined);
    runAndWait = vi.fn(async () => ({ status: 'ok' }));
    vi.doMock('../js/ipc.js', () => ({
      signal: { run, runAndWait },
    }));
    (window as any).__TAURI__ = {
      event: {
        listen: vi.fn(async (_event: string, cb: Listener) => {
          listener = cb;
          return () => undefined;
        }),
      },
    };

    const state = await import('../state');
    state.chatBubbles.value = [];
    state.busy.value = true;
    state.resumableRunId.value = null;
    state.govGatesList.value = [
      { id: 'G2', gate_id: 'G2', name: 'Plan', status: 'signed', signed: true },
      { id: 'G3', gate_id: 'G3', name: 'Design', status: 'signed', signed: true },
      { id: 'G4', gate_id: 'G4', name: 'Build', status: 'signed', signed: true },
      { id: 'G5', gate_id: 'G5', name: 'Quality', status: 'current', is_current: true },
    ];
    const mod = await import('./agentEvents');
    return { state, mod };
  }

  function emit(payload: AgentEventPayload) {
    expect(listener).toBeTruthy();
    listener!({ payload });
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    delete (window as any).__TAURI__;
  });

  it('renders gate_reopened as one system bubble naming the invalidated gates', async () => {
    const { state } = await loadHarness();

    emit({
      kind: 'agent-event',
      run_id: 'run-9',
      type: 'gate_reopened',
      gate: 'G3',
      invalidated: ['G4', 'G5'],
      reason: 'design contradicts the new brief',
      by: 'Samer',
      role: 'PO',
      reopen_count: 1,
    });

    const systems = state.chatBubbles.value.filter((b) => b.kind === 'system');
    expect(systems).toHaveLength(1);
    expect(systems[0].text).toBe(
      'G3 reopened by Samer: design contradicts the new brief. Also invalidated: G4, G5.',
    );

    // Re-delivery of the same event upserts (no double bubble).
    emit({
      kind: 'agent-event',
      run_id: 'run-9',
      type: 'gate_reopened',
      gate: 'G3',
      invalidated: ['G4', 'G5'],
      reason: 'design contradicts the new brief',
      by: 'Samer',
      reopen_count: 1,
    });
    expect(state.chatBubbles.value.filter((b) => b.kind === 'system')).toHaveLength(1);
    expect(state.busy.value).toBe(false);
  });

  it('cascades the un-sign into govGatesList: reopened gate current, later gates unsigned', async () => {
    const { state } = await loadHarness();

    emit({
      kind: 'agent-event',
      run_id: 'run-9',
      type: 'gate_reopened',
      gate: 'G3',
      invalidated: ['G4'],
      reason: 'rework',
    });

    const byId = Object.fromEntries(state.govGatesList.value.map((g) => [g.id, g]));
    expect(byId.G2).toMatchObject({ signed: true, status: 'signed' });
    expect(byId.G3).toMatchObject({ signed: false, status: 'current', is_current: true });
    expect(byId.G4).toMatchObject({ signed: false, status: 'locked' });
  });

  it('tolerates a minimal gate_reopened payload (missing fields)', async () => {
    const { state } = await loadHarness();
    emit({ kind: 'agent-event', run_id: 'run-9', type: 'gate_reopened' });
    const systems = state.chatBubbles.value.filter((b) => b.kind === 'system');
    expect(systems).toHaveLength(1);
    expect(systems[0].text).toBe('Gate reopened by user.');
  });

  it('reopenGate sends agent:reopen-gate with run_id, gate and reason', async () => {
    const { mod } = await loadHarness();
    // Seed lastRunId via an event.
    emit({ kind: 'agent-event', run_id: 'run-7', type: 'text', text: 'hi' });

    const res = await mod.reopenGate('G3', 'design is stale');

    expect(runAndWait).toHaveBeenCalledWith(
      'agent:reopen-gate',
      [JSON.stringify({ run_id: 'run-7', gate: 'G3', reason: 'design is stale' })],
      expect.any(Number),
    );
    expect(res.status).toBe('ok');
  });

  it('reopenGate maps refusal statuses to inline-ready messages', async () => {
    const { mod } = await loadHarness();
    emit({ kind: 'agent-event', run_id: 'run-7', type: 'text', text: 'hi' });

    for (const status of ['role-not-authorized', 'max-reopens', 'delivery-busy', 'not-signed']) {
      runAndWait.mockResolvedValueOnce({ status });
      const res = await mod.reopenGate('G3', 'why');
      expect(res.status).toBe(status);
      expect(res.error).toBeTruthy();
    }

    // Backend-provided message wins over the local fallback.
    runAndWait.mockResolvedValueOnce({ status: 'max-reopens', message: 'Budget of 3 exhausted for G3.' });
    const res = await mod.reopenGate('G3', 'why');
    expect(res.error).toBe('Budget of 3 exhausted for G3.');
  });

  it('reopenGate refuses locally when no run exists', async () => {
    const { mod } = await loadHarness();
    const res = await mod.reopenGate('G3', 'why');
    expect(res.status).toBe('no-run');
    expect(runAndWait).not.toHaveBeenCalled();
  });
});
