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
  'wave-freeze': { name: 'Wave freeze', description: 'No AI writes while a wave is frozen.' },
  'trust-tier': { name: 'Scope limits', description: 'Agents may only write within their allowed scope.' },
  'secret-block': { name: 'Secret protection', description: 'Secrets and governance files are protected from edits.' },
  'test-first': { name: 'Test-first', description: 'Tests must exist before implementation code.' },
  'gate-compliance': { name: 'Gate order', description: 'Gate order and rules are enforced end to end.' },
};

export function mapEnforcementRules(
  enfState: BackendEnforcementState | null | undefined,
): EnfRule[] {
  const modes = enfState?.modes ?? [];
  return modes.map((m) => {
    const label = RULE_LABELS[m.rule];
    // strict = actively enforcing (ok); warn/off surface as a soft alert so a
    // relaxed rule is visible without falsely claiming the build is "blocked".
    const status = m.mode === 'strict' ? 'ok' : 'warn';
    return {
      rule: m.rule,
      name: label?.name ?? m.rule,
      description: label?.description ?? `Mode: ${m.mode}`,
      status,
    };
  });
}
