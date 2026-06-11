import { describe, it, expect } from 'vitest';
import { GATE_ROLES, requiredRoleForGate, roleLabel } from './gateRoles';

describe('requiredRoleForGate', () => {
  it('maps each gate to its authorised role (mirrors python GATE_ROLES)', () => {
    expect(requiredRoleForGate('G0')).toBe('PE');
    expect(requiredRoleForGate('G1')).toBe('PO');
    expect(requiredRoleForGate('G2')).toBe('PO');
    expect(requiredRoleForGate('G3')).toBe('PE');
    expect(requiredRoleForGate('G4')).toBe('PE');
    expect(requiredRoleForGate('G5')).toBe('QA');
  });
  it('is case-insensitive', () => {
    expect(requiredRoleForGate('g4')).toBe('PE');
  });
  it('falls back to PO for unknown/empty gates', () => {
    expect(requiredRoleForGate('G9')).toBe('PO');
    expect(requiredRoleForGate('')).toBe('PO');
    expect(requiredRoleForGate(null)).toBe('PO');
  });
  it('covers exactly G0..G5', () => {
    expect(Object.keys(GATE_ROLES).sort()).toEqual(['G0', 'G1', 'G2', 'G3', 'G4', 'G5']);
  });
});

describe('roleLabel', () => {
  it('expands known role codes', () => {
    expect(roleLabel('PE')).toBe('Principal Engineer');
    expect(roleLabel('QA')).toBe('Quality');
  });
  it('returns the code for unknown roles and empty for blank', () => {
    expect(roleLabel('XYZ')).toBe('XYZ');
    expect(roleLabel('')).toBe('');
  });
});
