import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/preact';

vi.mock('../js/ipc.js', () => ({
  policy: {
    get: vi.fn(),
    set: vi.fn(),
  },
}));

const ipc = await import('../js/ipc.js');
const { PolicyPanel } = await import('./PolicyPanel');

describe('PolicyPanel (Wave 1.11)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the saved policy on mount and shows it in the form', async () => {
    (ipc.policy.get as any).mockResolvedValueOnce({
      gate_mode: 'fast-lane', research_depth: 'deep',
      budget_cap_usd: 30, standards_profile: 'strict', allowed_deploy_targets: [],
    });
    render(<PolicyPanel />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('30')).toBeTruthy();
    });
    expect(ipc.policy.get).toHaveBeenCalled();
  });

  it('saves the edited policy and shows a confirmation', async () => {
    (ipc.policy.get as any).mockResolvedValueOnce({
      gate_mode: 'standard', research_depth: 'standard',
      budget_cap_usd: 0, standards_profile: 'default', allowed_deploy_targets: [],
    });
    (ipc.policy.set as any).mockResolvedValueOnce({
      gate_mode: 'strict', research_depth: 'standard',
      budget_cap_usd: 0, standards_profile: 'default', allowed_deploy_targets: [],
    });
    render(<PolicyPanel />);
    await waitFor(() => expect(ipc.policy.get).toHaveBeenCalled());

    const select = screen.getByDisplayValue('Sign off on the decisions that matter') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'strict' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(ipc.policy.set).toHaveBeenCalledWith(
        expect.objectContaining({ gate_mode: 'strict' }),
      );
      expect(screen.getByText('Saved')).toBeTruthy();
    });
  });

  it('shows a plain-language error and does NOT call the backend on an invalid form', async () => {
    (ipc.policy.get as any).mockResolvedValueOnce({
      gate_mode: 'standard', research_depth: 'standard',
      budget_cap_usd: 0, standards_profile: 'default', allowed_deploy_targets: [],
    });
    render(<PolicyPanel />);
    await waitFor(() => expect(ipc.policy.get).toHaveBeenCalled());

    const budgetInput = screen.getByLabelText(/Budget cap/i) as HTMLInputElement;
    fireEvent.change(budgetInput, { target: { value: '-5' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/cannot be negative/i)).toBeTruthy();
    });
    expect(ipc.policy.set).not.toHaveBeenCalled();
  });
});
