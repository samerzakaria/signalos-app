// Maps the backend enforcement state into the shape the Toolbar renders.
//
// Wave 0.3: the backend `get_enforcement_state` returns `{ modes: [{rule, mode}] }`,
// but the UI was reading `enfState.rules` (wrong field) and expected `{status,
// description}` per rule (wrong shape) -- so enforcement always showed "No rules
// loaded", even for the rules that really enforce. This is the single mapping
// point that fixes both, and translates rule ids to plain language (no internal
// jargon in the founder-facing surface).
import type { EnfRule } from './state';

export interface BackendRuleStatus {
  rule: string;
  mode: string; // "strict" | "warn" | "off"
}

export interface BackendEnforcementState {
  modes?: BackendRuleStatus[];
  wave_frozen?: boolean;
}

// Plain-language labels for the stable governance rule ids (enforcement.rs
// ALL_RULES). Unknown ids fall back to the raw id rather than vanishing.
const RULE_LABELS: Record<string, { name: string; description: string }> = {
  'gate-gating': { name: 'Gate approvals', description: 'Build stays blocked until the required gates are signed.' },
  'plan-gating': { name: 'Plan approvals', description: 'Waves only run against an approved plan.' },
  'trust-tier': { name: 'Scope limits', description: 'Agents may only write within their allowed scope.' },
  'audit-append': { name: 'Audit trail', description: 'Every governed action is appended to the audit trail.' },
  'secret-block': { name: 'Secret protection', description: 'Secrets and governance files are protected from edits.' },
  'role-sign': { name: 'Role signatures', description: 'Gates are signed by someone holding the required role.' },
  'stack-contract': { name: 'Stack contract', description: 'Generated code stays within the declared tech stack.' },
  'wave-freeze': { name: 'Wave freeze', description: 'No AI writes while a wave is frozen.' },
  'test-first': { name: 'Test-first', description: 'Tests must exist before implementation code.' },
  'gate-compliance': { name: 'Gate order', description: 'Gate order and rules are enforced end to end.' },
  'zero-manual-regression': { name: 'Regression tests', description: 'Manually-found defects must gain an automated test.' },
  'mutation-threshold': { name: 'Mutation score', description: 'Test suites must meet the mutation-score floor.' },
};

// Mirrors enforcement.rs CORE_INVARIANTS (Wave 0.3): the backend's
// `set_rule_mode` refuses to disable these — relaxing one requires the
// governed override path (reason + audit), never a silent toggle. The UI
// renders them locked. get_enforcement_state does not expose a per-rule
// core flag, so this list is the single frontend source of truth.
export const CORE_INVARIANT_RULES: ReadonlySet<string> = new Set([
  'gate-gating',
  'plan-gating',
  'trust-tier',
  'audit-append',
  'secret-block',
  'role-sign',
  'test-first',
  'gate-compliance',
]);

export function isCoreInvariant(rule: string): boolean {
  return CORE_INVARIANT_RULES.has(rule);
}

// strict = actively enforcing (ok); warn/off surface as a soft alert so a
// relaxed rule is visible without falsely claiming the build is "blocked".
export function statusForMode(mode: string): string {
  return mode === 'strict' ? 'ok' : 'warn';
}

export function mapEnforcementRules(
  enfState: BackendEnforcementState | null | undefined,
): EnfRule[] {
  const modes = enfState?.modes ?? [];
  return modes.map((m) => {
    const label = RULE_LABELS[m.rule];
    return {
      rule: m.rule,
      name: label?.name ?? m.rule,
      description: label?.description ?? `Mode: ${m.mode}`,
      status: statusForMode(m.mode),
      mode: m.mode,
      core: isCoreInvariant(m.rule),
    };
  });
}
