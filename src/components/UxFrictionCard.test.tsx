import { render, screen, fireEvent } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';
import { UxFrictionCard } from './UxFrictionCard';
import type { UxFrictionPersona } from '../state';

// UxFrictionCard is the #12 review surface: the 5-persona friction report the
// gate orchestrator emits at the design gate. It is informational — the human
// reads it, then signs (or not) on the adjacent GateReviewCard. Load-bearing:
//   1. Every persona is rendered with its findings and severity weight.
//   2. No verdict buttons — signing stays on the GateReviewCard.
//   3. Long reports start collapsed but stay reachable.

function personas(overrides: Partial<Record<string, UxFrictionPersona>> = {}): UxFrictionPersona[] {
  const base: UxFrictionPersona[] = [
    {
      persona: 'impatient',
      label: 'Impatient User',
      findings: [
        { severity: 'high', issue: 'No loading state during async work.', suggestion: 'Show a spinner.' },
      ],
    },
    {
      persona: 'colorblind',
      label: 'Colorblind User',
      findings: [
        { severity: 'medium', issue: 'State conveyed by colour alone.' },
      ],
    },
    { persona: 'first_time', label: 'First-time User', findings: [] },
    {
      persona: 'mobile',
      label: 'Mobile User',
      findings: [{ severity: 'low', issue: 'Fixed pixel widths.' }],
    },
    { persona: 'keyboard', label: 'Keyboard-only User', findings: [] },
  ];
  return base.map((p) => overrides[p.persona] ?? p);
}

describe('UxFrictionCard', () => {
  it('renders each persona with its findings and severity badges', () => {
    render(<UxFrictionCard gate="design" personas={personas()} />);

    expect(screen.getByTestId('ux-friction-card')).toBeInTheDocument();
    expect(screen.getByText('Impatient User')).toBeInTheDocument();
    expect(screen.getByText('Colorblind User')).toBeInTheDocument();
    expect(screen.getByText('Mobile User')).toBeInTheDocument();
    expect(screen.getByText('No loading state during async work.')).toBeInTheDocument();
    expect(screen.getByText('Show a spinner.')).toBeInTheDocument();

    const severities = screen.getAllByTestId('ux-friction-severity');
    expect(severities.map((s) => s.getAttribute('data-severity'))).toEqual(
      expect.arrayContaining(['high', 'medium', 'low']),
    );
    // Summary counts total findings and calls out highs.
    expect(screen.getByTestId('ux-friction-summary').textContent).toMatch(/3 findings · 1 high/);
  });

  it('marks personas without findings as friction-free', () => {
    render(<UxFrictionCard gate="design" personas={personas()} />);
    const clean = screen.getByTestId('ux-friction-persona-first_time');
    expect(clean.textContent).toMatch(/no friction/);
  });

  it('is informational — renders no verdict buttons or submit control', () => {
    render(<UxFrictionCard gate="design" personas={personas()} />);
    expect(screen.queryByTestId('verdict-approve')).toBeNull();
    expect(screen.queryByTestId('verdict-submit')).toBeNull();
  });

  it('starts expanded for short reports and collapses on toggle', () => {
    render(<UxFrictionCard gate="design" personas={personas()} />);
    expect(screen.getByTestId('ux-friction-body')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('ux-friction-toggle'));
    expect(screen.queryByTestId('ux-friction-body')).toBeNull();
  });

  it('starts collapsed when the report is long, and expands on toggle', () => {
    const many: UxFrictionPersona[] = [
      {
        persona: 'impatient',
        label: 'Impatient User',
        findings: Array.from({ length: 6 }, (_, i) => ({
          severity: 'medium',
          issue: `Finding ${i + 1}`,
        })),
      },
    ];
    render(<UxFrictionCard gate="design" personas={many} />);
    expect(screen.queryByTestId('ux-friction-body')).toBeNull();
    // The summary is still visible while collapsed.
    expect(screen.getByTestId('ux-friction-summary').textContent).toMatch(/6 findings/);
    fireEvent.click(screen.getByTestId('ux-friction-toggle'));
    expect(screen.getByTestId('ux-friction-body')).toBeInTheDocument();
    expect(screen.getByText('Finding 1')).toBeInTheDocument();
  });

  it('sorts findings high before medium before low within a persona', () => {
    const mixed: UxFrictionPersona[] = [
      {
        persona: 'keyboard',
        label: 'Keyboard-only User',
        findings: [
          { severity: 'low', issue: 'Low issue' },
          { severity: 'high', issue: 'High issue' },
          { severity: 'medium', issue: 'Medium issue' },
        ],
      },
    ];
    render(<UxFrictionCard gate="design" personas={mixed} />);
    const rows = screen.getAllByTestId('ux-friction-finding');
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('High issue'),
      expect.stringContaining('Medium issue'),
      expect.stringContaining('Low issue'),
    ]);
  });
});
