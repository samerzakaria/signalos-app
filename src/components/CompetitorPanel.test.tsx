import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/preact';

// Mock the IPC layer so Analyze never hits Tauri.
vi.mock('../js/ipc.js', () => ({
  signal: { runAndWait: vi.fn(), run: vi.fn() },
}));

const ipc = await import('../js/ipc.js');
const {
  CompetitorPanel,
  __resetCompetitorPanelForTests,
  competitorOpen,
  competitorUrls,
} = await import('./CompetitorPanel');

const runAndWait = ipc.signal.runAndWait as ReturnType<typeof vi.fn>;

function openPanel() {
  act(() => { competitorOpen.value = true; });
}

function addUrl(url: string) {
  fireEvent.input(screen.getByTestId('competitor-input'), { target: { value: url } });
  fireEvent.click(screen.getByTestId('competitor-add'));
}

beforeEach(() => {
  runAndWait.mockReset();
  __resetCompetitorPanelForTests();
});

describe('CompetitorPanel (#16)', () => {
  it('starts collapsed and expands from the toggle', () => {
    const { container } = render(<CompetitorPanel />);
    expect(container.querySelector('[data-testid="competitor-input"]')).toBeNull();
    fireEvent.click(screen.getByTestId('competitor-toggle'));
    expect(screen.getByTestId('competitor-input')).toBeTruthy();
  });

  it('adds pasted URLs as removable chips, capped at 5, deduped', async () => {
    render(<CompetitorPanel />);
    openPanel();

    addUrl('https://a.com https://b.com');
    addUrl('https://a.com'); // duplicate
    addUrl('c.com'); // scheme added
    addUrl('https://d.com, https://e.com');

    await waitFor(() => {
      expect(screen.getAllByTestId('competitor-chip')).toHaveLength(5);
    });
    expect(competitorUrls.value).toEqual([
      'https://a.com', 'https://b.com', 'https://c.com', 'https://d.com', 'https://e.com',
    ]);

    // Sixth URL is rejected with a note.
    addUrl('https://f.com');
    await waitFor(() => expect(screen.getByTestId('competitor-note')).toBeTruthy());
    expect(screen.getByTestId('competitor-note').textContent).toMatch(/Up to 5/);
    expect(screen.getAllByTestId('competitor-chip')).toHaveLength(5);

    // Chips are removable.
    fireEvent.click(screen.getByTestId('competitor-remove-https://b.com'));
    await waitFor(() => expect(screen.getAllByTestId('competitor-chip')).toHaveLength(4));
    expect(competitorUrls.value).not.toContain('https://b.com');
  });

  it('rejects non-URL input with a note', async () => {
    render(<CompetitorPanel />);
    openPanel();
    addUrl('not a url at all');
    await waitFor(() => expect(screen.getByTestId('competitor-note')).toBeTruthy());
    expect(screen.getByTestId('competitor-note').textContent).toMatch(/doesn't look like a URL/);
  });

  it('Analyze calls competitor:analyze and renders the matrix + per-URL errors', async () => {
    runAndWait.mockResolvedValue({
      status: 'ok',
      matrix: [
        { competitor: 'a.com', pricing: 'freemium', strengths: ['brand'] },
        { competitor: 'b.com', pricing: 'enterprise', strengths: ['integrations'] },
      ],
      errors: [{ url: 'https://c.com', error: 'fetch failed (404)' }],
    });
    render(<CompetitorPanel />);
    openPanel();
    addUrl('https://a.com https://b.com https://c.com');

    fireEvent.click(screen.getByTestId('competitor-analyze'));

    await waitFor(() => expect(screen.getByTestId('competitor-matrix')).toBeTruthy());
    expect(runAndWait).toHaveBeenCalledWith(
      'competitor:analyze',
      [JSON.stringify({ urls: ['https://a.com', 'https://b.com', 'https://c.com'] })],
      expect.any(Number),
    );
    // Table rendering: column headers + cell values.
    expect(screen.getByText('pricing')).toBeTruthy();
    expect(screen.getByText('freemium')).toBeTruthy();
    expect(screen.getByText('integrations')).toBeTruthy();
    // Per-URL error list.
    expect(screen.getByTestId('competitor-errors').textContent).toMatch(/https:\/\/c\.com: fetch failed \(404\)/);
  });

  it('renders a friendly note for llm-unavailable', async () => {
    runAndWait.mockResolvedValue({ status: 'llm-unavailable' });
    render(<CompetitorPanel />);
    openPanel();
    addUrl('https://a.com');

    fireEvent.click(screen.getByTestId('competitor-analyze'));

    await waitFor(() => expect(screen.getByTestId('competitor-note')).toBeTruthy());
    expect(screen.getByTestId('competitor-note').textContent).toMatch(/connected AI key/i);
  });

  it('shows a graceful error when the command is unknown', async () => {
    runAndWait.mockRejectedValue(new Error('Unknown command: competitor:analyze'));
    render(<CompetitorPanel />);
    openPanel();
    addUrl('https://a.com');

    fireEvent.click(screen.getByTestId('competitor-analyze'));

    await waitFor(() => expect(screen.getByTestId('competitor-note')).toBeTruthy());
    expect(screen.getByTestId('competitor-note').textContent).toMatch(/Analysis unavailable/);
  });

  it('renders an object-shaped matrix as a key/value list', async () => {
    runAndWait.mockResolvedValue({
      status: 'ok',
      matrix: { positioning: 'mid-market', gaps: ['offline mode', 'API'] },
    });
    render(<CompetitorPanel />);
    openPanel();
    addUrl('https://a.com');
    fireEvent.click(screen.getByTestId('competitor-analyze'));

    await waitFor(() => expect(screen.getByTestId('competitor-matrix')).toBeTruthy());
    expect(screen.getByTestId('competitor-matrix').textContent).toMatch(/positioning: mid-market/);
    expect(screen.getByTestId('competitor-matrix').textContent).toMatch(/offline mode, API/);
  });
});
