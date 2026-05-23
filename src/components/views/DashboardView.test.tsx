import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/preact';
import { DashboardView } from './DashboardView';
import {
  govGatesList,
  currentWaveSummary,
  currentGateInfo,
  gateActivities,
  gateCriteria,
} from '../../state';

// DashboardView is the user's "where are we" screen. Two pieces of
// derived state are load-bearing:
//   1. The ring percentage (signed / total)
//   2. The verdict text (ready vs held, with reasons)
// These are computed from signals; if the derivation drifts, users
// will believe the wrong thing about whether they can sign.
// window.switchTab is declared in src/global.d.ts.

describe('DashboardView', () => {
  beforeEach(() => {
    govGatesList.value = [];
    currentWaveSummary.value = null;
    currentGateInfo.value = null;
    gateActivities.value = [];
    gateCriteria.value = [];
    // The Keep-building button calls window.switchTab; stub it.
    (window as unknown as { switchTab: (t: string) => void }).switchTab = vi.fn();
  });

  it('renders the empty state when no wave is loaded', () => {
    render(<DashboardView />);
    expect(screen.getByText('No wave loaded')).toBeInTheDocument();
  });

  it('computes the ring percentage correctly from signed/total gates', () => {
    govGatesList.value = [
      { id: 'G0', name: 'Soul', status: 'signed', signed: true },
      { id: 'G1', name: 'Plan', status: 'signed', signed: true },
      { id: 'G2', name: 'Tests', status: 'active', is_current: true },
      { id: 'G3', name: 'Build', status: 'locked' },
    ];
    currentWaveSummary.value = { total_gates: 4 };
    render(<DashboardView />);
    // 2/4 signed -> 50%
    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  it('shows "All clear" verdict when activities and criteria are complete', () => {
    govGatesList.value = [{ id: 'G2', name: 'Plan', status: 'active', is_current: true }];
    gateActivities.value = [
      { name: 'Activity 1', status: 'completed' },
      { name: 'Activity 2', status: 'completed' },
    ];
    gateCriteria.value = [{ name: 'Check 1', status: 'passed' }];
    render(<DashboardView />);
    expect(screen.getByText(/All clear/)).toBeInTheDocument();
  });

  it('shows held-verdict with the count of outstanding items', () => {
    govGatesList.value = [{ id: 'G2', name: 'Plan', status: 'active', is_current: true }];
    gateActivities.value = [
      { name: 'Activity 1', status: 'completed' },
      { name: 'Activity 2', status: 'pending' },
      { name: 'Activity 3', status: 'in_progress' },
    ];
    gateCriteria.value = [
      { name: 'Check 1', status: 'passed' },
      { name: 'Check 2', status: 'pending' },
    ];
    render(<DashboardView />);
    // 2 activities and 1 check left.
    expect(screen.getByText(/2 activities to finish.*1 check to pass/)).toBeInTheDocument();
  });

  it('renders one stepper cell per gate with the right status label', () => {
    govGatesList.value = [
      { id: 'G0', name: 'Soul', status: 'signed', signed: true },
      { id: 'G1', name: 'Plan', status: 'active', is_current: true },
      { id: 'G2', name: 'Tests', status: 'locked' },
    ];
    render(<DashboardView />);
    expect(screen.getByText('Soul')).toBeInTheDocument();
    expect(screen.getByText('Plan')).toBeInTheDocument();
    expect(screen.getByText('Tests')).toBeInTheDocument();
    expect(screen.getByText('Signed')).toBeInTheDocument();
    expect(screen.getByText('Current')).toBeInTheDocument();
    expect(screen.getByText('Locked')).toBeInTheDocument();
  });

  it('treats backend status=current as the active visible gate', () => {
    govGatesList.value = [
      { id: 0, name: 'Soul', status: 'signed', signed: true },
      { id: 1, name: 'Belief', status: 'current', is_current: true },
      { id: 2, name: 'Plan', status: 'locked' },
    ];
    render(<DashboardView />);
    const current = screen.getByTestId('gate-timeline-G1');
    expect(current).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText(/G1 of 3.*Belief/)).toBeInTheDocument();
  });

  it('shows backend-limited sign, request-changes, and reject gate actions', () => {
    govGatesList.value = [{ id: 2, name: 'Plan', status: 'current', is_current: true }];
    gateActivities.value = [{ name: 'Activity 1', status: 'completed' }];
    gateCriteria.value = [{ name: 'Check 1', status: 'passed' }];
    render(<DashboardView />);

    expect(screen.getByRole('button', { name: /Sign gate/i })).toBeEnabled();
    const requestChanges = screen.getByRole('button', { name: /Request changes/i });
    const reject = screen.getByRole('button', { name: /Reject/i });
    expect(requestChanges).toBeDisabled();
    expect(requestChanges).toHaveAttribute('title', expect.stringMatching(/not exposed/i));
    expect(reject).toBeDisabled();
    expect(reject).toHaveAttribute('title', expect.stringMatching(/not exposed/i));
  });

  it('shows the active gate hero title with the gate index', () => {
    govGatesList.value = [
      { id: 'G0', name: 'Soul', status: 'signed', signed: true },
      { id: 'G1', name: 'Plan', status: 'active', is_current: true },
    ];
    currentWaveSummary.value = { total_gates: 2 };
    render(<DashboardView />);
    // Active gate is the second entry (index 1), so "Gate 2 of 2 — Plan".
    expect(screen.getByText(/G1 of 2.*Plan/)).toBeInTheDocument();
  });
});
