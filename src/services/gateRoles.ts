// gateRoles.ts — which role is authorised to sign each gate.
//
// Mirrors python/signalos_lib/product/gate_orchestrator.py GATE_ROLES. A solo
// founder wears every hat, so instead of making them role-play by switching a
// global role dropdown before each gate, we sign as the role the gate itself
// requires. The accountable human (their name) is still recorded in the audit
// trail; this only removes the manual dropdown dance.

export const GATE_ROLES: Record<string, string> = {
  G0: 'PE',
  G1: 'PO',
  G2: 'PO',
  G3: 'PE',
  G4: 'PE',
  G5: 'QA',
};

export const ROLE_LABELS: Record<string, string> = {
  PO: 'Product Owner',
  PE: 'Principal Engineer',
  QA: 'Quality',
  DevOps: 'DevOps',
};

/** The role authorised to sign a gate. Falls back to PO for unknown gates. */
export function requiredRoleForGate(gate: string | null | undefined): string {
  const key = (gate || '').toUpperCase();
  return GATE_ROLES[key] || 'PO';
}

/** Human-friendly label for a role code (e.g. "PE" → "Principal Engineer"). */
export function roleLabel(role: string | null | undefined): string {
  if (!role) return '';
  return ROLE_LABELS[role] || role;
}
