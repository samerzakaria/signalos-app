import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/preact';
import { tab } from '../../state';

// The panel IPC is mocked so the view is exercised end-to-end (click ->
// ipc.panel.consult -> answers render) without a live sidecar.
vi.mock('../../js/ipc.js', () => ({
  panel: {
    consult: vi.fn(),
  },
}));

const ipc = await import('../../js/ipc.js');
const { WarRoomView, __resetWarRoomForTests } = await import('./WarRoomView');

const consult = ipc.panel.consult as unknown as ReturnType<typeof vi.fn>;

describe('WarRoomView', () => {
  beforeEach(() => {
    cleanup();
    tab.value = 'warroom';
    __resetWarRoomForTests();
    vi.clearAllMocks();
  });

  it('renders the four default panel models as chips', () => {
    render(<WarRoomView />);
    expect(screen.getByText('Sonnet-5')).toBeInTheDocument();
    expect(screen.getByText('GPT-5.6-Sol')).toBeInTheDocument();
    expect(screen.getByText('DeepSeek-V4-Pro')).toBeInTheDocument();
    expect(screen.getByText('Qwen3.7-Max')).toBeInTheDocument();
  });

  it('disables the Consult panel button when the question is empty', () => {
    render(<WarRoomView />);
    expect(screen.getByRole('button', { name: /Consult panel/i })).toBeDisabled();
  });

  it('calls ipc.panel.consult with the question, renders each answer card and the cost line', async () => {
    consult.mockResolvedValueOnce({
      answers: [
        { model: 'anthropic/claude-sonnet-5', name: 'Sonnet-5', text: 'Ship it Friday.', ok: true },
        { model: 'openai/gpt-5.6-sol', name: 'GPT-5.6-Sol', text: 'Wait a week.', ok: true },
      ],
      cost_usd: 0.0123,
      models: ['anthropic/claude-sonnet-5', 'openai/gpt-5.6-sol'],
      system: '',
    });

    render(<WarRoomView />);
    fireEvent.input(screen.getByPlaceholderText(/pressure-tested/i), {
      target: { value: 'Should we launch Friday?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Consult panel/i }));

    await waitFor(() => {
      expect(screen.getByText('Ship it Friday.')).toBeInTheDocument();
    });
    expect(consult).toHaveBeenCalledWith('Should we launch Friday?');
    expect(screen.getByText('Wait a week.')).toBeInTheDocument();
    expect(screen.getAllByTestId('warroom-answer')).toHaveLength(2);
    expect(screen.getByText(/Panel cost: \$0\.0123/)).toBeInTheDocument();
  });

  it('renders a failed model\'s error instead of its text, and "cost unavailable" when cost is null', async () => {
    consult.mockResolvedValueOnce({
      answers: [
        { model: 'qwen/qwen3.7-max', name: 'Qwen3.7-Max', text: '', ok: false, error: 'provider rate limited' },
      ],
      cost_usd: null,
      models: ['qwen/qwen3.7-max'],
      system: '',
    });

    render(<WarRoomView />);
    fireEvent.input(screen.getByPlaceholderText(/pressure-tested/i), {
      target: { value: 'Any question' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Consult panel/i }));

    await waitFor(() => {
      expect(screen.getByTestId('warroom-answer-error')).toHaveTextContent('provider rate limited');
    });
    expect(screen.getByText(/cost unavailable/i)).toBeInTheDocument();
  });

  it('surfaces a thrown error from the panel call', async () => {
    consult.mockRejectedValueOnce(new Error('sidecar exploded'));

    render(<WarRoomView />);
    fireEvent.input(screen.getByPlaceholderText(/pressure-tested/i), {
      target: { value: 'Any question' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Consult panel/i }));

    await waitFor(() => {
      expect(screen.getByTestId('warroom-error')).toHaveTextContent('sidecar exploded');
    });
  });
});
