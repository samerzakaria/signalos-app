// Founder policy controls (Wave 1.11) -- plain-language mapping between the
// backend FounderPolicy shape and the settings UI. No workflow-graph editor:
// the founder edits policy fields only, never gate structure.

export interface FounderPolicy {
  gate_mode: string;
  research_depth: string;
  budget_cap_usd: number;
  standards_profile: string;
  allowed_deploy_targets: string[];
}

export const DEFAULT_POLICY: FounderPolicy = {
  gate_mode: 'standard',
  research_depth: 'standard',
  budget_cap_usd: 0,
  standards_profile: 'default',
  allowed_deploy_targets: [],
};

// Mirrors signalos_lib/product/policy.py's GATE_MODE_LABELS -- plain language,
// never internal codes, in the one surface a non-technical founder reads.
export const GATE_MODE_OPTIONS: { value: string; label: string }[] = [
  { value: 'strict', label: 'Sign off on everything (most control)' },
  { value: 'standard', label: 'Sign off on the decisions that matter' },
  { value: 'fast-lane', label: 'Sign off only on the essential decisions' },
];

export const RESEARCH_DEPTH_OPTIONS: { value: string; label: string }[] = [
  { value: 'light', label: 'Light — fast, fewer sources' },
  { value: 'standard', label: 'Standard' },
  { value: 'deep', label: 'Deep — slower, more thorough' },
];

export function normalizePolicy(raw: unknown): FounderPolicy {
  if (!raw || typeof raw !== 'object') return { ...DEFAULT_POLICY };
  const r = raw as Record<string, unknown>;
  return {
    gate_mode: typeof r.gate_mode === 'string' ? r.gate_mode : DEFAULT_POLICY.gate_mode,
    research_depth: typeof r.research_depth === 'string' ? r.research_depth : DEFAULT_POLICY.research_depth,
    budget_cap_usd: typeof r.budget_cap_usd === 'number' ? r.budget_cap_usd : DEFAULT_POLICY.budget_cap_usd,
    standards_profile: typeof r.standards_profile === 'string' ? r.standards_profile : DEFAULT_POLICY.standards_profile,
    allowed_deploy_targets: Array.isArray(r.allowed_deploy_targets)
      ? r.allowed_deploy_targets.filter((t): t is string => typeof t === 'string')
      : [],
  };
}

export function validatePolicyForm(policy: FounderPolicy): string[] {
  const problems: string[] = [];
  if (!GATE_MODE_OPTIONS.some((o) => o.value === policy.gate_mode)) {
    problems.push('Pick a valid sign-off level.');
  }
  if (!RESEARCH_DEPTH_OPTIONS.some((o) => o.value === policy.research_depth)) {
    problems.push('Pick a valid research depth.');
  }
  if (!(policy.budget_cap_usd >= 0)) {
    problems.push('Budget cap cannot be negative.');
  }
  return problems;
}
