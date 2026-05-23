import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';
import { GateTimeline, activeGateIndex, gateCode, gateStatusLabel, gateUiState } from './GateTimeline';

describe('GateTimeline release scenarios', () => {
  it('normalizes backend gate ids and states for G0-G5 rendering', () => {
    const gates = [
      { id: 0, name: 'Soul', status: 'signed', signed: true },
      { id: '1', name: 'Belief', status: 'current', is_current: true },
      { gate_id: 'g2', name: 'Plan', status: 'locked' },
    ];

    expect(gateCode(gates[0], 0)).toBe('G0');
    expect(gateCode(gates[1], 1)).toBe('G1');
    expect(gateCode(gates[2], 2)).toBe('G2');
    expect(gateUiState(gates[0])).toBe('signed');
    expect(gateUiState(gates[1])).toBe('current');
    expect(gateUiState(gates[2])).toBe('locked');
    expect(gateStatusLabel(gates[2])).toBe('Locked');
    expect(activeGateIndex(gates)).toBe(1);
  });

  it('renders signed, current, and locked gates with stable test ids', () => {
    render(
      <GateTimeline
        gates={[
          { id: 'G0', name: 'Soul', status: 'signed', signed: true },
          { id: 'G1', name: 'Belief', status: 'current', is_current: true },
          { id: 'G2', name: 'Plan', status: 'locked' },
        ]}
      />,
    );

    expect(screen.getByTestId('gate-timeline-G0')).toHaveTextContent('Signed');
    expect(screen.getByTestId('gate-timeline-G1')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByTestId('gate-timeline-G1')).toHaveTextContent('Current');
    expect(screen.getByTestId('gate-timeline-G2')).toHaveTextContent('Locked');
  });
});
