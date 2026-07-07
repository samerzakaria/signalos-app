import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/preact';

// Mock the reopen transport so dialog clicks never hit IPC.
vi.mock('../services/agentEvents', () => ({
  reopenGate: vi.fn(),
}));

const agentEvents = await import('../services/agentEvents');
const {
  ReopenGateDialog,
  openReopenGateDialog,
  __resetReopenGateDialogForTests,
} = await import('./ReopenGateDialog');

const reopenGate = agentEvents.reopenGate as ReturnType<typeof vi.fn>;

beforeEach(() => {
  reopenGate.mockReset();
  __resetReopenGateDialogForTests();
});

describe('ReopenGateDialog', () => {
  it('renders nothing until opened, then shows the gate-specific dialog', () => {
    const { container } = render(<ReopenGateDialog />);
    expect(container.querySelector('[data-testid="reopen-gate-dialog"]')).toBeNull();

    act(() => openReopenGateDialog('G3'));
    expect(screen.getByTestId('reopen-gate-dialog')).toBeTruthy();
    expect(screen.getAllByText('Reopen G3').length).toBeGreaterThan(0);
  });

  it('requires a reason before invoking agent:reopen-gate', async () => {
    render(<ReopenGateDialog />);
    act(() => openReopenGateDialog('G3'));

    fireEvent.click(screen.getByTestId('reopen-confirm'));

    await waitFor(() => expect(screen.getByTestId('reopen-error')).toBeTruthy());
    expect(screen.getByTestId('reopen-error').textContent).toMatch(/reason is required/i);
    expect(reopenGate).not.toHaveBeenCalled();
  });

  it('calls reopenGate with the gate + reason and closes on success', async () => {
    reopenGate.mockResolvedValue({ status: 'ok', gate: 'G3', invalidated: ['G4'] });
    const { container } = render(<ReopenGateDialog />);
    act(() => openReopenGateDialog('G3'));

    fireEvent.input(screen.getByTestId('reopen-reason'), {
      target: { value: 'design contradicts the new brief' },
    });
    fireEvent.click(screen.getByTestId('reopen-confirm'));

    await waitFor(() => {
      expect(reopenGate).toHaveBeenCalledWith('G3', 'design contradicts the new brief');
    });
    await waitFor(() => {
      expect(container.querySelector('[data-testid="reopen-gate-dialog"]')).toBeNull();
    });
  });

  it('renders backend refusal messages inline and stays open', async () => {
    reopenGate.mockResolvedValue({
      status: 'max-reopens',
      error: 'This gate has hit its reopen budget — it cannot be reopened again.',
    });
    render(<ReopenGateDialog />);
    act(() => openReopenGateDialog('G2'));

    fireEvent.input(screen.getByTestId('reopen-reason'), { target: { value: 'try again' } });
    fireEvent.click(screen.getByTestId('reopen-confirm'));

    await waitFor(() => expect(screen.getByTestId('reopen-error')).toBeTruthy());
    expect(screen.getByTestId('reopen-error').textContent).toMatch(/reopen budget/);
    expect(screen.getByTestId('reopen-gate-dialog')).toBeTruthy();
  });

  it('surfaces transport errors (rejected IPC) inline', async () => {
    reopenGate.mockRejectedValue(new Error('Unknown command: agent:reopen-gate'));
    render(<ReopenGateDialog />);
    act(() => openReopenGateDialog('G4'));

    fireEvent.input(screen.getByTestId('reopen-reason'), { target: { value: 'rework' } });
    fireEvent.click(screen.getByTestId('reopen-confirm'));

    await waitFor(() => expect(screen.getByTestId('reopen-error')).toBeTruthy());
    expect(screen.getByTestId('reopen-error').textContent).toMatch(/Unknown command/);
  });
});
