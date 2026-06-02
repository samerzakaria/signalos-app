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

  it('sends gate verdicts to the agent verdict IPC command', async () => {
    const { mod } = await loadHarness();

    emit({ kind: 'agent-event', run_id: 'run-4', type: 'gate', gate: 'G1' });
    mod.submitGateVerdict('bubble-1', 'approve-with-conditions', 'ship only after tests');

    expect(run).toHaveBeenCalledWith('agent:verdict', [
      JSON.stringify({
        run_id: 'run-4',
        verdict: 'approve-with-conditions',
        feedback: 'ship only after tests',
      }),
    ]);
  });
});
