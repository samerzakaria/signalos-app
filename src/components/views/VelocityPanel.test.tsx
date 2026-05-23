// VelocityPanel.test.tsx — Phase 13 dashboard sidebar widget tests.
//
// Covers the two load-bearing render paths so the dashboard never
// crashes on a brand-new workspace:
//   1. Empty state — sidecar returns zero values, panel shows the
//      "No velocity data yet" hint instead of NaN / blank.
//   2. Populated state — sidecar returns burndown + sessions/day +
//      ETA, panel renders each row with the right label.
//
// We mock the ipc.js boundary, not the Tauri runtime — same pattern
// as TestDebtPanel.test.tsx.

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/preact';

vi.mock('../../js/ipc.js', () => ({
  signal: {
    runAndWait: vi.fn(),
  },
}));

const ipc = await import('../../js/ipc.js');
const { VelocityPanel } = await import('./VelocityPanel');

function mockRun(payload: unknown) {
  (ipc.signal.runAndWait as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
    JSON.stringify(payload),
  );
}

function mockRunError(message: string) {
  (ipc.signal.runAndWait as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
    new Error(message),
  );
}

describe('VelocityPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the "No velocity data yet" empty state when sidecar returns zero values', async () => {
    mockRun({
      sessions_per_day: 0,
      scope_card_burndown: [],
      eta_days: null,
      last_session_at: null,
      window_days: 14,
      generated_at: '2026-05-23T12:00:00Z',
    });

    render(<VelocityPanel />);

    await waitFor(() => {
      expect(screen.getByTestId('velocity-empty')).toBeInTheDocument();
    });
    expect(
      screen.getByText(/No velocity data yet/i),
    ).toBeInTheDocument();
    // Populated body must not render in the empty path.
    expect(screen.queryByTestId('velocity-body')).toBeNull();
  });

  it('renders sessions/day, ETA, and per-wave burndown when sidecar returns metrics', async () => {
    mockRun({
      sessions_per_day: 1.25,
      scope_card_burndown: [
        { wave: '1', total: 4, completed: 1 },
        { wave: '2', total: 6, completed: 3 },
      ],
      eta_days: 4.5,
      last_session_at: '2026-05-22T18:30:00Z',
      window_days: 14,
      generated_at: '2026-05-23T12:00:00Z',
    });

    render(<VelocityPanel />);

    const body = await screen.findByTestId('velocity-body');
    expect(body).toBeInTheDocument();

    expect(screen.getByTestId('velocity-sessions-per-day').textContent).toBe('1.25');
    expect(screen.getByTestId('velocity-eta').textContent || '').toMatch(/4\.5 days/);

    // Burndown rows render per wave with the right counts.
    const wave1 = screen.getByTestId('velocity-burndown-wave-1');
    const wave2 = screen.getByTestId('velocity-burndown-wave-2');
    expect(wave1.textContent || '').toMatch(/Wave 1/);
    expect(wave1.textContent || '').toMatch(/1 \/ 4/);
    expect(wave2.textContent || '').toMatch(/Wave 2/);
    expect(wave2.textContent || '').toMatch(/3 \/ 6/);

    // Empty-state hint must not render in the populated path.
    expect(screen.queryByTestId('velocity-empty')).toBeNull();
  });

  it('renders the error state when the sidecar call rejects (no crash)', async () => {
    mockRunError('Sidecar timed out');

    render(<VelocityPanel />);

    const err = await screen.findByTestId('velocity-error');
    expect(err).toBeInTheDocument();
    expect(err.textContent || '').toMatch(/Sidecar timed out/);
    expect(screen.queryByTestId('velocity-body')).toBeNull();
  });
});
