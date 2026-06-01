import { render, screen, fireEvent } from '@testing-library/preact';
import { describe, expect, it, vi } from 'vitest';
import { GateReviewCard, GATE_VERDICTS } from './GateReviewCard';

describe('GateReviewCard', () => {
  it('renders all five verdict buttons', () => {
    render(<GateReviewCard gate="G3" title="Design direction" question="Does this look right?" />);
    expect(GATE_VERDICTS).toHaveLength(5);
    for (const v of GATE_VERDICTS) {
      expect(screen.getByTestId(`verdict-${v.verdict}`)).toBeTruthy();
    }
  });

  it('approve submits immediately without feedback', () => {
    const onVerdict = vi.fn();
    render(<GateReviewCard gate="G0" title="Scope" question="ok?" onVerdict={onVerdict} />);
    fireEvent.click(screen.getByTestId('verdict-approve'));
    fireEvent.click(screen.getByTestId('verdict-submit'));
    expect(onVerdict).toHaveBeenCalledWith({ verdict: 'approve', feedback: '' });
  });

  it('request-changes requires feedback before submitting', () => {
    const onVerdict = vi.fn();
    render(<GateReviewCard gate="G2" title="Plan" question="ok?" onVerdict={onVerdict} />);
    fireEvent.click(screen.getByTestId('verdict-request-changes'));
    fireEvent.click(screen.getByTestId('verdict-submit'));
    expect(onVerdict).not.toHaveBeenCalled();
    expect(screen.getByTestId('verdict-error')).toBeTruthy();
    fireEvent.input(screen.getByTestId('verdict-feedback'), { target: { value: 'add tests' } });
    fireEvent.click(screen.getByTestId('verdict-submit'));
    expect(onVerdict).toHaveBeenCalledWith({ verdict: 'request-changes', feedback: 'add tests' });
  });

  it('waive requires a justification (INV-1)', () => {
    const onVerdict = vi.fn();
    render(<GateReviewCard gate="G5" title="Launch" question="ship?" onVerdict={onVerdict} />);
    fireEvent.click(screen.getByTestId('verdict-waive'));
    fireEvent.click(screen.getByTestId('verdict-submit'));
    expect(onVerdict).not.toHaveBeenCalled();
    fireEvent.input(screen.getByTestId('verdict-feedback'), { target: { value: 'risk accepted' } });
    fireEvent.click(screen.getByTestId('verdict-submit'));
    expect(onVerdict).toHaveBeenCalledWith({ verdict: 'waive', feedback: 'risk accepted' });
  });

  it('disables interaction once resolved', () => {
    render(<GateReviewCard gate="G0" title="Scope" question="ok?" resolved="approve" />);
    expect(screen.getByTestId('verdict-approve')).toBeDisabled();
    expect(screen.queryByTestId('verdict-submit')).toBeNull();
  });
});
