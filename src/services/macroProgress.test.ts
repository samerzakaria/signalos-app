import { describe, it, expect } from 'vitest';
import { summarizeMacro, macroLine } from './macroProgress';
import type { Gate, GateInfo, ReleaseReadinessResult } from '../state';

const GATES: Gate[] = [
  { id: 'G0', name: 'Soul', status: 'signed' },
  { id: 'G1', name: 'Belief', signed: true },
  { id: 'G2', name: 'Plan', status: 'current', is_current: true },
  { id: 'G3', name: 'Design' },
];

describe('summarizeMacro', () => {
  it('counts signed gates (status or signed flag)', () => {
    const s = summarizeMacro(GATES, null, null);
    expect(s.signed).toBe(2);
    expect(s.total).toBe(4);
  });

  it('derives current gate from gateInfo, else from is_current', () => {
    const fromFlag = summarizeMacro(GATES, null, null);
    expect(fromFlag.currentGate).toBe('G2');
    const info: GateInfo = { id: 'G3', name: 'Design' };
    const fromInfo = summarizeMacro(GATES, info, null);
    expect(fromInfo.currentGate).toBe('G3');
    expect(fromInfo.currentTitle).toBe('Design');
  });

  it('maps readiness ok/blockers to a label and pass counts', () => {
    const ready: ReleaseReadinessResult = {
      ok: true,
      checks: [{ id: 'a', severity: 'info', message: '', status: 'pass' },
               { id: 'b', severity: 'info', message: '', status: 'ok' }],
    };
    const s = summarizeMacro(GATES, null, ready);
    expect(s.readinessLabel).toBe('Ready');
    expect(s.readinessPass).toBe(2);
    expect(s.readinessTotal).toBe(2);

    const blocked: ReleaseReadinessResult = {
      blockers: [{ id: 'x', severity: 'high', message: 'nope' }],
      checks: [{ id: 'a', severity: 'info', message: '', status: 'fail' }],
    };
    expect(summarizeMacro(GATES, null, blocked).readinessLabel).toBe('Not ready');
  });

  it('is Unknown with no readiness data', () => {
    expect(summarizeMacro(GATES, null, null).readinessLabel).toBe('Unknown');
  });

  it('handles empty/garbage gate input', () => {
    const s = summarizeMacro([] as Gate[], null, null);
    expect(s.total).toBe(0);
    expect(s.signed).toBe(0);
  });
});

describe('macroLine', () => {
  it('renders a compact one-liner', () => {
    const line = macroLine(summarizeMacro(GATES, null, {
      ok: false,
      checks: [{ id: 'a', severity: 'info', message: '', status: 'pass' },
               { id: 'b', severity: 'high', message: '', status: 'fail' }],
    }));
    expect(line).toContain('2/4 gates signed');
    expect(line).toContain('G2 Plan');
    expect(line).toContain('Release: Not ready (1/2)');
  });

  it('omits unknown readiness', () => {
    expect(macroLine(summarizeMacro(GATES, null, null))).not.toContain('Release');
  });
});
