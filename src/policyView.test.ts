import { describe, it, expect } from 'vitest';
import { normalizePolicy, validatePolicyForm, DEFAULT_POLICY, GATE_MODE_OPTIONS } from './policyView';

describe('normalizePolicy (Wave 1.11)', () => {
  it('maps a well-formed backend payload through', () => {
    const p = normalizePolicy({
      gate_mode: 'fast-lane', research_depth: 'deep',
      budget_cap_usd: 12.5, standards_profile: 'strict', allowed_deploy_targets: ['web'],
    });
    expect(p.gate_mode).toBe('fast-lane');
    expect(p.budget_cap_usd).toBe(12.5);
    expect(p.allowed_deploy_targets).toEqual(['web']);
  });

  it('falls back to defaults for null/garbage input (never crashes the settings panel)', () => {
    expect(normalizePolicy(null)).toEqual(DEFAULT_POLICY);
    expect(normalizePolicy(undefined)).toEqual(DEFAULT_POLICY);
    expect(normalizePolicy('nonsense')).toEqual(DEFAULT_POLICY);
  });

  it('drops non-string entries from allowed_deploy_targets rather than crashing', () => {
    const p = normalizePolicy({ allowed_deploy_targets: ['web', 42, null] });
    expect(p.allowed_deploy_targets).toEqual(['web']);
  });
});

describe('validatePolicyForm', () => {
  it('accepts the default policy', () => {
    expect(validatePolicyForm(DEFAULT_POLICY)).toEqual([]);
  });

  it('flags an unknown gate mode', () => {
    const problems = validatePolicyForm({ ...DEFAULT_POLICY, gate_mode: 'bogus' });
    expect(problems.length).toBeGreaterThan(0);
  });

  it('flags a negative budget cap', () => {
    const problems = validatePolicyForm({ ...DEFAULT_POLICY, budget_cap_usd: -5 });
    expect(problems.length).toBeGreaterThan(0);
  });
});

describe('GATE_MODE_OPTIONS', () => {
  it('never exposes internal jargon -- only plain-language labels', () => {
    for (const opt of GATE_MODE_OPTIONS) {
      expect(opt.label).not.toMatch(/gate|invariant|enforcement/i);
    }
  });
});
