import { describe, it, expect } from 'vitest';
import { mapEnforcementRules } from './enforcementView';

describe('mapEnforcementRules (Wave 0.3)', () => {
  it('maps backend .modes into displayable rules (fixes the .rules-vs-.modes bug)', () => {
    const out = mapEnforcementRules({
      modes: [
        { rule: 'gate-gating', mode: 'strict' },
        { rule: 'wave-freeze', mode: 'warn' },
      ],
    });
    expect(out).toHaveLength(2);
    expect(out[0].name).toBe('Gate approvals'); // plain language, not the raw id
    expect(out[0].status).toBe('ok'); // strict = actively enforcing
    expect(out[1].status).toBe('warn'); // relaxed = visible soft alert
  });

  it('returns [] for empty or missing state (no crash, no phantom rules)', () => {
    expect(mapEnforcementRules(null)).toEqual([]);
    expect(mapEnforcementRules(undefined)).toEqual([]);
    expect(mapEnforcementRules({})).toEqual([]);
  });

  it('falls back to the raw rule id for an unknown rule instead of dropping it', () => {
    const out = mapEnforcementRules({ modes: [{ rule: 'new-rule', mode: 'strict' }] });
    expect(out[0].name).toBe('new-rule');
  });

  it('treats off-mode as a visible soft alert, not a silent pass', () => {
    const out = mapEnforcementRules({ modes: [{ rule: 'test-first', mode: 'off' }] });
    expect(out[0].status).toBe('warn');
  });
});
