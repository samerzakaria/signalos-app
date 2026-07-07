import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/preact';

// Mock the IPC layer so the scrubber never hits Tauri.
vi.mock('../js/ipc.js', () => ({
  signal: { runAndWait: vi.fn(), run: vi.fn() },
}));

const ipc = await import('../js/ipc.js');
const {
  AuditTimeTravel,
  __resetAuditTimelineForTests,
} = await import('./AuditTimeTravel');

const runAndWait = ipc.signal.runAndWait as ReturnType<typeof vi.fn>;

function frame(index: number, over: Record<string, unknown> = {}) {
  return {
    index,
    ts: `2026-07-0${index + 1}T10:00:00Z`,
    summary: `event-${index}`,
    entry: { action: `event-${index}` },
    state_after: {
      index,
      wave: index >= 1 ? 'W01' : null,
      gates: {
        G0: { signed: index >= 0, role: 'PO', ts: '2026-07-01' },
        G1: { signed: index >= 1, role: null, ts: null },
        G2: { signed: false, role: null, ts: null },
        G3: { signed: false, role: null, ts: null },
        G4: { signed: false, role: null, ts: null },
        G5: { signed: false, role: null, ts: null },
      },
      events_applied: index + 1,
      files_touched: 0,
      overrides: 0,
    },
    ...over,
  };
}

beforeEach(() => {
  runAndWait.mockReset();
  __resetAuditTimelineForTests();
});

describe('AuditTimeTravel — scrubber (#15)', () => {
  it('loads the timeline, renders the slider and the newest frame state', async () => {
    runAndWait.mockResolvedValue({
      status: 'ok',
      frames: [frame(0), frame(1), frame(2)],
    });

    render(<AuditTimeTravel />);

    await waitFor(() => expect(screen.getByTestId('time-travel')).toBeTruthy());
    expect(runAndWait).toHaveBeenCalledWith(
      'audit:replay-timeline',
      [JSON.stringify({ limit: 500 })],
      expect.any(Number),
    );

    // Starts at the newest frame (index 2).
    expect(screen.getByTestId('time-travel-position').textContent).toBe('3 / 3');
    expect(screen.getByTestId('time-travel-summary').textContent).toBe('event-2');
    // Signed-gate chips reflect state_after: G0 + G1 signed, rest not.
    expect(screen.getByTestId('time-travel-gate-G0').getAttribute('data-signed')).toBe('true');
    expect(screen.getByTestId('time-travel-gate-G1').getAttribute('data-signed')).toBe('true');
    expect(screen.getByTestId('time-travel-gate-G2').getAttribute('data-signed')).toBe('false');
    expect(screen.getByTestId('time-travel-wave').textContent).toMatch(/Active wave: W01/);
    // Event list renders every frame up to the index, current highlighted.
    expect(screen.getByTestId('time-travel-current-event').textContent).toMatch(/event-2/);
  });

  it('moving the slider updates the state panel and the event list', async () => {
    runAndWait.mockResolvedValue({
      status: 'ok',
      frames: [frame(0), frame(1), frame(2)],
    });
    render(<AuditTimeTravel />);
    await waitFor(() => expect(screen.getByTestId('time-travel-slider')).toBeTruthy());

    fireEvent.input(screen.getByTestId('time-travel-slider'), { target: { value: '0' } });

    await waitFor(() => {
      expect(screen.getByTestId('time-travel-position').textContent).toBe('1 / 3');
    });
    expect(screen.getByTestId('time-travel-summary').textContent).toBe('event-0');
    // At frame 0 only G0 is signed and no wave is active yet.
    expect(screen.getByTestId('time-travel-gate-G1').getAttribute('data-signed')).toBe('false');
    expect(screen.getByTestId('time-travel-wave').textContent).toMatch(/No active wave/);
    expect(screen.getByTestId('time-travel-current-event').textContent).toMatch(/event-0/);
  });

  it('shows "showing last N" when the backend truncated the trail', async () => {
    runAndWait.mockResolvedValue({
      status: 'ok',
      truncated: true,
      frames: [frame(0), frame(1)],
    });
    render(<AuditTimeTravel />);
    await waitFor(() => expect(screen.getByTestId('time-travel')).toBeTruthy());
    expect(screen.getByText(/showing last 2 events/)).toBeTruthy();
  });

  it('hides the section entirely for an empty trail', async () => {
    runAndWait.mockResolvedValue({ status: 'ok', frames: [] });
    const { container } = render(<AuditTimeTravel />);
    // Let the load settle, then confirm nothing rendered.
    await waitFor(() => expect(runAndWait).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(container.querySelector('[data-testid="time-travel"]')).toBeNull();
    expect(container.querySelector('[data-testid="time-travel-error"]')).toBeNull();
  });

  it('renders a graceful error state when the command is unknown', async () => {
    runAndWait.mockRejectedValue(new Error('Unknown command: audit:replay-timeline'));
    render(<AuditTimeTravel />);
    await waitFor(() => expect(screen.getByTestId('time-travel-error')).toBeTruthy());
    expect(screen.getByText(/Unknown command: audit:replay-timeline/)).toBeTruthy();
  });

  it('tolerates a refusal-status payload with no frames', async () => {
    runAndWait.mockResolvedValue({ status: 'error', error: 'trail unreadable' });
    render(<AuditTimeTravel />);
    await waitFor(() => expect(screen.getByTestId('time-travel-error')).toBeTruthy());
    expect(screen.getByText(/trail unreadable/)).toBeTruthy();
  });
});
