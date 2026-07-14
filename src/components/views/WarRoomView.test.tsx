import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/preact';
import { tab } from '../../state';

vi.mock('../../js/ipc.js', () => ({
  panel: { consult: vi.fn() },
}));

const ipc = await import('../../js/ipc.js');
const { WarRoomView, __resetWarRoomForTests } = await import('./WarRoomView');
const consult = ipc.panel.consult as unknown as ReturnType<typeof vi.fn>;

function completeResult() {
  return {
    schema_version: 'panel-run/1',
    protocol_version: 'council/1.0',
    status: 'complete',
    answers: [
      {
        candidate_id: 'A01',
        model: 'anthropic/claude-sonnet-5',
        name: 'Claude Sonnet 5',
        text: 'Proceed after rollback validation.',
        ok: true,
        revised: true,
      },
    ],
    decision: {
      decision_state: 'provisional_majority',
      selected_candidate_id: 'A01',
      recommendation: 'Run a bounded pilot before release.',
      rationale: 'The jury favored the pilot while retaining the red-team concern.',
      next_actions: ['Run the pilot', 'Capture rollback evidence'],
      conditions_to_reconsider: ['Rollback fails'],
    },
    dissent: {
      status: 'available',
      name: 'Grok 4.5',
      counter_recommendation: 'Delay until independent rollback evidence exists.',
      failure_modes: ['Shared evidence could hide a common blind spot'],
    },
    warnings: [],
    failures: [],
    cost_usd: 1.2345,
    cost: { source: 'per_response_usage' },
    models: [],
    system: '',
  };
}

describe('WarRoomView', () => {
  beforeEach(() => {
    cleanup();
    tab.value = 'warroom';
    __resetWarRoomForTests();
    vi.clearAllMocks();
  });

  it('shows every default council role including Qwen in the primary council', () => {
    render(<WarRoomView />);
    expect(screen.getByText('Sonnet 5 · Adviser')).toBeInTheDocument();
    expect(screen.getByText('DeepSeek V4 Pro · Adviser')).toBeInTheDocument();
    expect(screen.getByText('Qwen3.7 Max · Adviser')).toBeInTheDocument();
    expect(screen.getByText('Fable 5 · Verifier · Juror')).toBeInTheDocument();
    expect(screen.getByText('Grok 4.5 · Red team · Juror')).toBeInTheDocument();
    expect(screen.getByText('GPT-5.6 Sol Pro · Chair · Juror')).toBeInTheDocument();
  });

  it('disables the council button when the case is empty', () => {
    render(<WarRoomView />);
    expect(screen.getByRole('button', { name: /Consult council/i })).toBeDisabled();
  });

  it('requests council mode and renders decision, dissent, adviser record, and cost', async () => {
    consult.mockResolvedValueOnce(completeResult());
    render(<WarRoomView />);
    fireEvent.input(screen.getByLabelText('Decision or question'), {
      target: { value: 'Should we launch Friday?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Consult council/i }));

    await waitFor(() => {
      expect(screen.getByText('Run a bounded pilot before release.')).toBeInTheDocument();
    });
    expect(consult).toHaveBeenCalledWith('Should we launch Friday?', { mode: 'council' });
    expect(screen.getByText('Provisional majority')).toBeInTheDocument();
    expect(screen.getByText('Delay until independent rollback evidence exists.')).toBeInTheDocument();
    expect(screen.getByText('Proceed after rollback validation.')).toBeInTheDocument();
    expect(screen.getByText(/Council cost: \$1\.2345 · per_response_usage/)).toBeInTheDocument();
    expect(screen.getByTestId('warroom-status')).toHaveTextContent('Complete');
  });

  it('renders degraded warnings, an adviser failure, unavailable dissent, and unknown cost', async () => {
    consult.mockResolvedValueOnce({
      protocol_version: 'council/1.0',
      status: 'degraded',
      answers: [
        {
          model: 'qwen/qwen3.7-max',
          name: 'Qwen3.7 Max',
          text: '',
          ok: false,
          error: 'provider rate limited',
        },
      ],
      decision: null,
      dissent: { status: 'unavailable', error: 'red-team provider unavailable' },
      warnings: ['Jury quorum not met'],
      failures: [
        { stage: 'jury', model: 'openai/gpt-5.6-sol-pro', error: 'provider timed out' },
      ],
      cost_usd: null,
      models: [],
      system: '',
    });
    render(<WarRoomView />);
    fireEvent.input(screen.getByLabelText('Decision or question'), {
      target: { value: 'Any question' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Consult council/i }));

    await waitFor(() => {
      expect(screen.getByTestId('warroom-answer-error')).toHaveTextContent('provider rate limited');
    });
    expect(screen.getByTestId('warroom-status')).toHaveTextContent('Degraded');
    expect(screen.getByTestId('warroom-warnings')).toHaveTextContent('Jury quorum not met');
    expect(screen.getByTestId('warroom-failures')).toHaveTextContent('jury');
    expect(screen.getByTestId('warroom-failures')).toHaveTextContent('provider timed out');
    expect(screen.getByTestId('warroom-dissent')).toHaveTextContent('red-team provider unavailable');
    expect(screen.getByText(/Council cost: unavailable/i)).toBeInTheDocument();
  });

  it('treats a legacy successful payload as complete and labels partial cost honestly', async () => {
    consult.mockResolvedValueOnce({
      answers: [
        {
          model: 'qwen/qwen3.7-max',
          name: 'Qwen3.7 Max',
          text: 'Use a bounded rollout.',
          ok: true,
        },
      ],
      cost_usd: 0.25,
      cost: {
        source: 'partial_per_response_usage',
        warning: 'Some provider usage costs were unavailable.',
      },
      models: ['qwen/qwen3.7-max'],
      system: '',
    });
    render(<WarRoomView />);
    fireEvent.input(screen.getByLabelText('Decision or question'), {
      target: { value: 'Legacy payload' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Consult council/i }));

    await waitFor(() => {
      expect(screen.getByTestId('warroom-status')).toHaveTextContent('Complete');
    });
    expect(screen.getByTestId('warroom-cost')).toHaveTextContent('Known cost subtotal: $0.2500');
    expect(screen.getByTestId('warroom-cost-warning')).toHaveTextContent(
      'Some provider usage costs were unavailable.',
    );
  });

  it('accepts the locked IPC data envelope as well as a direct result', async () => {
    consult.mockResolvedValueOnce({ data: completeResult() });
    render(<WarRoomView />);
    fireEvent.input(screen.getByLabelText('Decision or question'), {
      target: { value: 'Envelope case' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Consult council/i }));
    await waitFor(() => {
      expect(screen.getByTestId('warroom-decision')).toHaveTextContent('bounded pilot');
    });
  });

  it('surfaces a transport error', async () => {
    consult.mockRejectedValueOnce(new Error('sidecar unavailable'));
    render(<WarRoomView />);
    fireEvent.input(screen.getByLabelText('Decision or question'), {
      target: { value: 'Any question' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Consult council/i }));
    await waitFor(() => {
      expect(screen.getByTestId('warroom-error')).toHaveTextContent('sidecar unavailable');
    });
  });
});
