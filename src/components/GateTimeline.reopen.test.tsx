import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/preact';
import { GateTimeline } from './GateTimeline';
import type { Gate } from '../state';

const gates: Gate[] = [
  { id: 'G2', gate_id: 'G2', name: 'Plan', status: 'signed', signed: true },
  { id: 'G3', gate_id: 'G3', name: 'Design', status: 'signed', signed: true },
  { id: 'G4', gate_id: 'G4', name: 'Build', status: 'current', is_current: true },
  { id: 'G5', gate_id: 'G5', name: 'Quality', status: 'locked' },
];

describe('GateTimeline — reopen affordance', () => {
  it('shows a reopen button only on signed gates when onReopen is provided', () => {
    const onReopen = vi.fn();
    render(<GateTimeline gates={gates} onReopen={onReopen} />);

    expect(screen.getByTestId('gate-reopen-G2')).toBeTruthy();
    expect(screen.getByTestId('gate-reopen-G3')).toBeTruthy();
    expect(screen.queryByTestId('gate-reopen-G4')).toBeNull();
    expect(screen.queryByTestId('gate-reopen-G5')).toBeNull();

    fireEvent.click(screen.getByTestId('gate-reopen-G3'));
    expect(onReopen).toHaveBeenCalledWith('G3');
  });

  it('shows no reopen buttons when onReopen is absent (no active/resumable run)', () => {
    render(<GateTimeline gates={gates} />);
    expect(screen.queryByTestId('gate-reopen-G2')).toBeNull();
    expect(screen.queryByTestId('gate-reopen-G3')).toBeNull();
  });
});
