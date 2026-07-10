import { beforeEach, describe, expect, it, vi } from 'vitest';

type AgentEventPayload = Record<string, unknown>;
type Listener = (event: { payload: AgentEventPayload }) => void;

describe('agentEvents', () => {
  let listener: Listener | null;
  let run: ReturnType<typeof vi.fn>;

  async function loadHarness() {
    vi.resetModules();
    listener = null;
    run = vi.fn(async () => undefined);
    vi.doMock('../js/ipc.js', () => ({
      signal: { run },
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
    state.ai.value = 'openai';
    state.aiModel.value = 'gpt-test';
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

  it('renders streaming text live and finalizes the assistant bubble', async () => {
    const { state } = await loadHarness();

    emit({ kind: 'agent-event', run_id: 'run-1', type: 'text', text: 'hello' });

    expect(state.chatBubbles.value).toMatchObject([
      { id: 'agent-stream-run-1', kind: 'streaming', text: 'hello' },
    ]);

    emit({ kind: 'agent-event', run_id: 'run-1', type: 'text', text: ' world' });
    expect(state.chatBubbles.value[0].text).toBe('hello world');

    emit({ kind: 'agent-event', run_id: 'run-1', type: 'end_turn' });
    expect(state.chatBubbles.value[0].kind).toBe('ai');
    expect(state.busy.value).toBe(false);
  });

  it('renders tool completion and denial as visible tool bubbles', async () => {
    const { state } = await loadHarness();

    emit({ kind: 'agent-event', run_id: 'run-2', type: 'tool_done', tool: 'read_file' });
    emit({
      kind: 'agent-event',
      run_id: 'run-2',
      type: 'tool_denied',
      tool: 'write_file',
      reason: 'Permission denied: .env is forbidden',
    });

    expect(state.chatBubbles.value).toMatchObject([
      { kind: 'tool', tool: { name: 'read_file', status: 'done' } },
      {
        kind: 'tool',
        tool: {
          name: 'write_file',
          status: 'denied',
          summary: 'Permission denied: .env is forbidden',
        },
      },
    ]);
  });

  it('renders diff, gate, and preview events as their Build bubble types', async () => {
    const { state } = await loadHarness();

    emit({
      kind: 'agent-event',
      run_id: 'run-3',
      type: 'diff',
      path: 'src/App.tsx',
      before: 'old',
      after: 'new',
    });
    emit({
      kind: 'agent-event',
      run_id: 'run-3',
      type: 'gate',
      gate: 'G3',
      title: 'Design review',
      question: 'Approve this direction?',
      evidence: 'Preview rendered.',
    });
    emit({
      kind: 'agent-event',
      run_id: 'run-3',
      type: 'preview',
      srcDoc: '<main>Preview</main>',
      caption: 'Design preview',
    });

    expect(state.chatBubbles.value.map((b) => b.kind)).toEqual(['diff', 'gate', 'preview']);
    expect(state.chatBubbles.value[0].diff?.path).toBe('src/App.tsx');
    expect(state.chatBubbles.value[1].gateReview?.gate).toBe('G3');
    expect(state.chatBubbles.value[2].preview?.caption).toBe('Design preview');
    expect(state.busy.value).toBe(false);
  });

  it('renders ux_friction events as a friction bubble ahead of the gate card (#12)', async () => {
    const { state } = await loadHarness();

    // The orchestrator's _emit_preview emits ux_friction BEFORE the gate
    // checkpoint — the friction card must land before the review card.
    emit({
      kind: 'agent-event',
      run_id: 'run-ux',
      type: 'ux_friction',
      gate: 'design',
      count: 2,
      findings: [
        {
          persona: 'impatient',
          label: 'Impatient User',
          findings: [
            { severity: 'high', issue: 'No loading state.', suggestion: 'Add a spinner.' },
          ],
        },
        { persona: 'keyboard', label: 'Keyboard-only User', findings: [] },
      ],
    });
    emit({
      kind: 'agent-event',
      run_id: 'run-ux',
      type: 'gate',
      gate: 'design',
      title: 'Design review',
      question: 'Approve this direction?',
    });

    expect(state.chatBubbles.value.map((b) => b.kind)).toEqual(['friction', 'gate']);
    const friction = state.chatBubbles.value[0];
    expect(friction.uxFriction?.gate).toBe('design');
    expect(friction.uxFriction?.personas).toHaveLength(2);
    expect(friction.uxFriction?.personas[0]).toMatchObject({
      persona: 'impatient',
      label: 'Impatient User',
      findings: [{ severity: 'high', issue: 'No loading state.', suggestion: 'Add a spinner.' }],
    });
  });

  it('ignores malformed ux_friction payloads instead of crashing', async () => {
    const { state } = await loadHarness();

    emit({ kind: 'agent-event', run_id: 'run-ux2', type: 'ux_friction', gate: 'design', findings: 'not-a-list' });
    emit({ kind: 'agent-event', run_id: 'run-ux2', type: 'ux_friction', gate: 'design' });

    expect(state.chatBubbles.value).toEqual([]);
  });

  it('sends gate verdicts to the agent verdict IPC command', async () => {
    const { mod } = await loadHarness();

    emit({ kind: 'agent-event', run_id: 'run-4', type: 'gate', gate: 'G1' });
    // An unparseable bubble id falls back to the last-seen run (legacy path).
    mod.submitGateVerdict('bubble-1', 'approve-with-conditions', 'ship only after tests');

    expect(run).toHaveBeenCalledWith('agent:verdict', [
      JSON.stringify({
        run_id: 'run-4',
        verdict: 'approve-with-conditions',
        feedback: 'ship only after tests',
      }),
    ]);
  });

  it('submits a verdict against the gate bubble\'s own run, not the latest global run (Claim 10)', async () => {
    const { mod } = await loadHarness();

    // Two gate cards from two runs; run-B is the most recent, so lastRunId is
    // run-B. Submitting on run-A's card must still target run-A.
    emit({ kind: 'agent-event', run_id: 'run-A', type: 'gate', gate: 'G1' });
    emit({ kind: 'agent-event', run_id: 'run-B', type: 'gate', gate: 'G1' });

    await mod.submitGateVerdict('agent-gate-run-A-G1', 'approve', '');

    expect(run).toHaveBeenCalledWith('agent:verdict', [
      JSON.stringify({ run_id: 'run-A', verdict: 'approve', feedback: '', gate_id: 'G1' }),
    ]);
  });

  it('reports a backend refusal so the card can revert (Claim 10)', async () => {
    vi.resetModules();
    const runAndWait = vi.fn(async () => ({ status: 'build-not-verified', gate: 'G4', reason: 'stub only' }));
    vi.doMock('../js/ipc.js', () => ({ signal: { run: vi.fn(), runAndWait } }));
    (window as any).__TAURI__ = {
      event: { listen: vi.fn(async () => () => undefined) },
    };
    const mod = await import('./agentEvents');

    const res = await mod.submitGateVerdict('agent-gate-run-Z-G4', 'approve', '');

    expect(runAndWait).toHaveBeenCalledWith(
      'agent:verdict',
      [JSON.stringify({ run_id: 'run-Z', verdict: 'approve', feedback: '', gate_id: 'G4' })],
      0,
    );
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/independently verified/i);
  });

  it('treats an accepted verdict (advanced) as success', async () => {
    vi.resetModules();
    const runAndWait = vi.fn(async () => ({ status: 'advanced', gate: 'G2' }));
    vi.doMock('../js/ipc.js', () => ({ signal: { run: vi.fn(), runAndWait } }));
    (window as any).__TAURI__ = {
      event: { listen: vi.fn(async () => () => undefined) },
    };
    const mod = await import('./agentEvents');

    const res = await mod.submitGateVerdict('agent-gate-run-Q-G1', 'approve', '');
    expect(res.ok).toBe(true);
  });

  it('marks cancelled runs as resumable and sends cancel/resume IPC commands', async () => {
    const { state, mod } = await loadHarness();

    emit({ kind: 'agent-event', run_id: 'run-5', type: 'text', text: 'working' });
    mod.cancelAgentRun();

    expect(run).toHaveBeenCalledWith('agent:cancel', [
      JSON.stringify({ run_id: 'run-5' }),
    ]);

    emit({ kind: 'agent-event', run_id: 'run-5', type: 'cancelled' });

    expect(state.resumableRunId.value).toBe('run-5');
    expect(state.busy.value).toBe(false);
    expect(state.chatBubbles.value.some((b) => b.kind === 'system' && b.text === 'Agent run cancelled.')).toBe(true);

    mod.resumeAgentRun();

    expect(state.resumableRunId.value).toBe(null);
    expect(state.busy.value).toBe(true);
    expect(run).toHaveBeenCalledWith('agent:resume', [
      JSON.stringify({ run_id: 'run-5', provider: 'openai', model: 'gpt-test' }),
    ]);
  });

  it('renders provider failures as readable error bubbles', async () => {
    const { state } = await loadHarness();

    emit({
      kind: 'agent-event',
      run_id: 'run-provider',
      type: 'error',
      error: 'Provider call failed: BadRequestError: litellm.BadRequestError: AnthropicException - {"error":{"message":"Your credit balance is too low to access the Anthropic API."}}',
    });

    expect(state.chatBubbles.value[0]).toMatchObject({
      kind: 'error',
      text: 'Error: Anthropic account credit is too low. Add credits with that provider or choose another provider/model in Settings.',
    });
  });

  it('final G5 sign feeds the notification bell live, deduped on re-delivery', async () => {
    const { state } = await loadHarness();
    const notif = await import('./notifications');
    notif.__resetNotificationsForTests();

    emit({ kind: 'agent-event', run_id: 'run-g5', type: 'gate_signed', gate: 'G5', verdict: 'approve' });

    expect(notif.unreadCount.value).toBe(1);
    expect(notif.notifications.value[0]).toMatchObject({
      kind: 'delivery',
      text: 'Delivery complete — G5 signed',
    });
    // gate_signed now ALSO renders visible completion state in the transcript
    // (Claim 11a) — one system bubble, keyed per (run, gate).
    expect(state.chatBubbles.value).toMatchObject([
      { id: 'agent-gate-signed-run-g5-G5', kind: 'system', text: 'G5 signed.' },
    ]);

    // Re-delivered event (sidecar replay) must not double-notify or double-bubble…
    emit({ kind: 'agent-event', run_id: 'run-g5', type: 'gate_signed', gate: 'G5', verdict: 'approve' });
    expect(notif.unreadCount.value).toBe(1);
    expect(state.chatBubbles.value.filter((b) => b.kind === 'system')).toHaveLength(1);

    // …and the orchestrator's follow-up delivery_complete for the same run
    // is folded into the same single completion notification, and adds its own
    // visible completion bubble.
    emit({ kind: 'agent-event', run_id: 'run-g5', type: 'delivery_complete', ready: true });
    expect(notif.unreadCount.value).toBe(1);
    expect(state.chatBubbles.value.some(
      (b) => b.id === 'agent-delivery-run-g5' && /Delivery complete/.test(b.text),
    )).toBe(true);
    expect(state.busy.value).toBe(false);
  });

  it('renders system progress events into the transcript, skipping the reopen mirror (Claim 11a)', async () => {
    const { state } = await loadHarness();

    emit({ kind: 'agent-event', run_id: 'run-sys', type: 'system', text: 'Building: auth module' });
    // `message` is the alternate field some emitters use.
    emit({ kind: 'agent-event', run_id: 'run-sys', type: 'system', message: 'Getting it to pass its test.' });
    // The reopen flow emits a plain-system mirror of its structured
    // gate_reopened event — that one must be skipped to avoid a double bubble.
    emit({ kind: 'agent-event', run_id: 'run-sys', type: 'system', text: 'G3 reopened by user: rework.' });

    const systems = state.chatBubbles.value.filter((b) => b.kind === 'system');
    expect(systems.map((b) => b.text)).toEqual([
      'Building: auth module',
      'Getting it to pass its test.',
    ]);
  });
});
