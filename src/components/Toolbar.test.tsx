import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/preact';

// Mock ipc.js so setMode calls are under test control. Toolbar imports the
// whole module; only enforcement.setMode is exercised here.
vi.mock('../js/ipc.js', () => ({
  enforcement: {
    setMode: vi.fn(),
  },
}));

const ipc = await import('../js/ipc.js');
const { Toolbar, __resetEnforcementToggleForTests } = await import('./Toolbar');
const { enforcementRules, enfOpen, waveFrozen, tab, recentWorkspaces, workspacePath } = await import('../state');
const { mapEnforcementRules } = await import('../enforcementView');

// #13 — per-rule strict/warn/off toggles in the enforcement popover.
// Load-bearing behaviours:
//   1. Tunable rules render a three-way mode selector; clicking a mode calls
//      enforcement.setMode and flips the row optimistically.
//   2. Core invariants render a lock and NO toggle (the backend refuses to
//      disable them; the UI must not offer the dead control).
//   3. A backend refusal reverts the optimistic update and surfaces the error.

const setModeMock = ipc.enforcement.setMode as unknown as ReturnType<typeof vi.fn>;

function loadRules() {
  enforcementRules.value = mapEnforcementRules({
    modes: [
      { rule: 'gate-gating', mode: 'strict' }, // core invariant
      { rule: 'secret-block', mode: 'strict' }, // core invariant
      { rule: 'wave-freeze', mode: 'strict' }, // tunable
      { rule: 'mutation-threshold', mode: 'warn' }, // tunable
    ],
  });
}

describe('Toolbar enforcement mode toggles', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetEnforcementToggleForTests();
    loadRules();
    enfOpen.value = true;
    waveFrozen.value = false;
    tab.value = 'build';
    workspacePath.value = '';
    recentWorkspaces.value = [];
    setModeMock.mockResolvedValue(true);
  });

  it('renders a lock (and no toggle) for core-invariant rules', () => {
    render(<Toolbar />);
    expect(screen.getByTestId('rule-lock-gate-gating')).toBeInTheDocument();
    expect(screen.getByTestId('rule-lock-secret-block')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-mode-gate-gating-off')).toBeNull();
    expect(screen.queryByTestId('rule-mode-secret-block-strict')).toBeNull();
    // The lock explains itself.
    expect(screen.getByTestId('rule-lock-gate-gating')).toHaveAttribute(
      'title',
      expect.stringMatching(/core invariant/i),
    );
  });

  it('renders a three-way mode selector for tunable rules', () => {
    render(<Toolbar />);
    for (const mode of ['strict', 'warn', 'off']) {
      expect(screen.getByTestId(`rule-mode-wave-freeze-${mode}`)).toBeInTheDocument();
    }
    expect(screen.queryByTestId('rule-lock-wave-freeze')).toBeNull();
    // Active mode is reflected via aria-pressed.
    expect(screen.getByTestId('rule-mode-wave-freeze-strict')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('rule-mode-mutation-threshold-warn')).toHaveAttribute('aria-pressed', 'true');
  });

  it('clicking a mode calls setMode and optimistically updates the rule', async () => {
    render(<Toolbar />);
    fireEvent.click(screen.getByTestId('rule-mode-wave-freeze-off'));

    expect(setModeMock).toHaveBeenCalledWith('wave-freeze', 'off');
    // Optimistic flip happens synchronously.
    const rule = enforcementRules.value.find((r) => r.rule === 'wave-freeze');
    expect(rule?.mode).toBe('off');
    expect(rule?.status).toBe('warn'); // relaxed = visible soft alert
    await waitFor(() => {
      expect(screen.getByTestId('rule-mode-wave-freeze-off')).toHaveAttribute('aria-pressed', 'true');
    });
    expect(screen.queryByTestId('enf-mode-error')).toBeNull();
  });

  it('reverts the optimistic update and shows the error when the backend refuses', async () => {
    setModeMock.mockRejectedValue(
      new Error("'wave-freeze' is a core invariant and cannot be disabled; use a governed override (with a reason) instead."),
    );
    render(<Toolbar />);
    fireEvent.click(screen.getByTestId('rule-mode-wave-freeze-off'));

    await waitFor(() => {
      expect(screen.getByTestId('enf-mode-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('enf-mode-error').textContent).toMatch(/cannot be disabled/);
    // Reverted to the pre-toggle mode.
    const rule = enforcementRules.value.find((r) => r.rule === 'wave-freeze');
    expect(rule?.mode).toBe('strict');
    expect(rule?.status).toBe('ok');
    expect(screen.getByTestId('rule-mode-wave-freeze-strict')).toHaveAttribute('aria-pressed', 'true');
  });

  it('does not call setMode when clicking the already-active mode', () => {
    render(<Toolbar />);
    fireEvent.click(screen.getByTestId('rule-mode-wave-freeze-strict'));
    expect(setModeMock).not.toHaveBeenCalled();
  });
});
