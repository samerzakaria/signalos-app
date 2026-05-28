// DeliverView.test.tsx - Product Delivery Bridge guided flow tests.
//
// Covers the load-bearing render paths:
//   1. Renders prompt input step initially
//   2. Submit button triggers delivery (intent extraction)
//   3. Shows loading state during delivery
//   4. Shows intent step with entities on success
//   5. Shows error state on failure (no crash)
//   6. Profile selector has correct options
//   7. Shows closeout on delivery success

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/preact';

vi.mock('../../js/ipc.js', () => ({
  onSidecarProgress: vi.fn(() => () => {}),
  signal: { runAndWait: vi.fn() },
  workspace: { set: vi.fn() },
}));

const ipc = await import('../../js/ipc.js');
const { DeliverView } = await import('./DeliverView');

function mockRunOnce(payload: unknown) {
  (ipc.signal.runAndWait as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
    JSON.stringify(payload),
  );
}

function mockRunRaw(raw: string) {
  (ipc.signal.runAndWait as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(raw);
}

function mockRunError(message: string) {
  (ipc.signal.runAndWait as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
    new Error(message),
  );
}

describe('DeliverView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (window as any).switchTab = vi.fn();
  });

  it('renders the prompt input step initially', () => {
    render(<DeliverView />);
    expect(screen.getByTestId('deliver-step-prompt')).toBeInTheDocument();
    expect(screen.getByTestId('deliver-prompt-input')).toBeInTheDocument();
    expect(screen.getByTestId('deliver-name-input')).toBeInTheDocument();
    expect(screen.getByTestId('deliver-start-btn')).toBeInTheDocument();
  });

  it('start button is disabled when prompt is empty', () => {
    render(<DeliverView />);
    const btn = screen.getByTestId('deliver-start-btn');
    expect(btn).toBeDisabled();
  });

  it('submit triggers intent extraction and shows intent step', async () => {
    mockRunOnce({
      entities: ['Recipe', 'Tag'],
      workflows: ['Add recipe', 'Search recipes'],
      surfaces: ['List view', 'Detail view'],
      questions: ['Should recipes support images?'],
      assumptions: ['Single user, no auth needed'],
    });

    render(<DeliverView />);

    const textarea = screen.getByTestId('deliver-prompt-input');
    fireEvent.input(textarea, { target: { value: 'A recipe manager app' } });

    const btn = screen.getByTestId('deliver-start-btn');
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-intent')).toBeInTheDocument();
    });

    expect(screen.getByText('Recipe')).toBeInTheDocument();
    expect(screen.getByText('Tag')).toBeInTheDocument();
    expect(screen.getByText('Add recipe')).toBeInTheDocument();
    expect(screen.getByText('Should recipes support images?')).toBeInTheDocument();
  });

  it('shows error state when sidecar call fails (no crash)', async () => {
    mockRunError('Sidecar not responding');

    render(<DeliverView />);

    const textarea = screen.getByTestId('deliver-prompt-input');
    fireEvent.input(textarea, { target: { value: 'Build a todo app' } });

    fireEvent.click(screen.getByTestId('deliver-start-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('deliver-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('deliver-error').textContent).toMatch(/Sidecar not responding/);
    // Falls back to prompt step
    expect(screen.getByTestId('deliver-step-prompt')).toBeInTheDocument();
  });

  it('profile selector has auto, react-vite, and generic options', () => {
    render(<DeliverView />);
    const select = screen.getByTestId('deliver-profile-select') as HTMLSelectElement;
    const options = Array.from(select.querySelectorAll('option'));
    const values = options.map((o) => o.value);
    expect(select.value).toBe('react-vite');
    expect(values).toContain('auto');
    expect(values).toContain('react-vite');
    expect(values).toContain('generic');
  });

  it('parses JSON even when a backend command prints setup logs first', async () => {
    mockRunRaw('SignalOS setup complete\n{"entities":["Task"],"workflows":["Add task"],"surfaces":[],"questions":[],"assumptions":[]}');

    render(<DeliverView />);

    fireEvent.input(screen.getByTestId('deliver-prompt-input'), {
      target: { value: 'Build a task app' },
    });
    fireEvent.click(screen.getByTestId('deliver-start-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-intent')).toBeInTheDocument();
    });
    expect(screen.getByText('Task')).toBeInTheDocument();
  });

  it('mode selector has auto, greenfield, and adopt options', () => {
    render(<DeliverView />);
    const select = screen.getByTestId('deliver-mode-select') as HTMLSelectElement;
    const options = Array.from(select.querySelectorAll('option'));
    const values = options.map((o) => o.value);
    expect(values).toContain('auto');
    expect(values).toContain('greenfield');
    expect(values).toContain('adopt');
  });

  it('shows closeout step with product details after full delivery', async () => {
    // Mock intent step
    mockRunOnce({
      entities: ['Task'],
      workflows: ['Add task'],
      surfaces: ['Board view'],
      questions: [],
      assumptions: [],
    });

    // Mock design step
    mockRunOnce({
      ui_library: 'Tailwind',
      ui_reason: 'Fast iteration',
      tokens: { color: 'blue-500', typography: 'Inter' },
      state_management: 'Zustand',
      data_layer: 'Local storage',
      form_handling: 'Native forms',
    });

    // Mock design preview (returns HTML for iframe)
    mockRunOnce({ preview_html: '<html><body>Preview</body></html>' });

    // Mock full delivery
    mockRunOnce({
      name: 'my-kanban',
      closure_level: 'full',
      files_count: 12,
      workspace: { repo_root: '/tmp/my-kanban' },
      how_to_run: ['cd /tmp/my-kanban', 'npm install', 'npm run dev'],
      limitations: ['No dark mode yet'],
      security: { status: 'All checks passed' },
    });

    render(<DeliverView />);

    // Fill prompt and start
    fireEvent.input(screen.getByTestId('deliver-prompt-input'), { target: { value: 'A kanban board' } });
    fireEvent.input(screen.getByTestId('deliver-name-input'), { target: { value: 'my-kanban' } });
    fireEvent.click(screen.getByTestId('deliver-start-btn'));

    // Wait for intent step
    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-intent')).toBeInTheDocument();
    });

    // Continue to design
    fireEvent.click(screen.getByTestId('deliver-continue-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-design')).toBeInTheDocument();
    });

    // Approve design
    fireEvent.click(screen.getByTestId('deliver-approve-btn'));

    // Wait for closeout
    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-closeout')).toBeInTheDocument();
    }, { timeout: 5000 });

    const deliverCalls = (ipc.signal.runAndWait as unknown as ReturnType<typeof vi.fn>).mock.calls
      .filter((call) => String(call[0]).startsWith('deliver'));
    expect(deliverCalls.map((call) => [call[0], call[2]])).toEqual([
      ['deliver-intent', 0],
      ['deliver-design', 0],
      ['deliver-design-preview', 0],
      ['deliver', 0],
    ]);
    expect(screen.getByText('my-kanban')).toBeInTheDocument();
    expect(screen.getByTestId('deliver-closure').textContent).toBe('full');
    expect(screen.getByText('12 files')).toBeInTheDocument();
    expect(screen.getByText('No dark mode yet')).toBeInTheDocument();
    expect(screen.getByTestId('deliver-open-btn')).toBeInTheDocument();
  });

  it('does not tell users to leave Deliver for Terminal on a sidecar timeout', async () => {
    mockRunOnce({
      entities: ['Task'],
      workflows: ['Add task'],
      surfaces: ['Board view'],
      questions: [],
      assumptions: [],
    });
    mockRunOnce({
      ui_library: 'Tailwind',
      tokens: { color: '#2563eb', typography: 'Inter' },
      state_management: 'Zustand',
      data_layer: 'Local storage',
      form_handling: 'Native forms',
    });
    mockRunOnce({ preview_html: '<html><body>Preview</body></html>' });
    mockRunError('Timed out waiting for run_signal_command');

    render(<DeliverView />);

    fireEvent.input(screen.getByTestId('deliver-prompt-input'), {
      target: { value: 'Build a todo product' },
    });
    fireEvent.click(screen.getByTestId('deliver-start-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-intent')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('deliver-continue-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-design')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('deliver-approve-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('deliver-error')).toBeInTheDocument();
    });

    const text = screen.getByTestId('deliver-error').textContent ?? '';
    expect(text).toContain('SignalOS stopped receiving a response');
    expect(text).not.toMatch(/Terminal|retry from this screen if it stops updating/i);
  });
});
