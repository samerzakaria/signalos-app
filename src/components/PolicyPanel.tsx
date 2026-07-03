// Founder policy controls (Wave 1.11) -- the plain-language settings surface.
// The founder edits POLICY here (sign-off level, research depth, budget cap,
// standards profile) -- never gate structure. No workflow-graph editor by
// design: rewiring gates would let a non-technical founder break the
// governance that is the product's value.
import { useSignal } from '@preact/signals';
import { useEffect } from 'preact/hooks';
import * as ipc from '../js/ipc.js';
import {
  DEFAULT_POLICY, GATE_MODE_OPTIONS, RESEARCH_DEPTH_OPTIONS,
  normalizePolicy, validatePolicyForm, type FounderPolicy,
} from '../policyView';

export function PolicyPanel() {
  const policy = useSignal<FounderPolicy>(DEFAULT_POLICY);
  const status = useSignal<'idle' | 'loading' | 'saving' | 'saved' | 'error'>('idle');
  const errorMsg = useSignal<string>('');

  useEffect(() => {
    status.value = 'loading';
    ipc.policy
      .get()
      .then((raw: unknown) => {
        policy.value = normalizePolicy(raw);
        status.value = 'idle';
      })
      .catch((e: unknown) => {
        errorMsg.value = e instanceof Error ? e.message : String(e);
        status.value = 'error';
      });
  }, []);

  const save = () => {
    const problems = validatePolicyForm(policy.value);
    if (problems.length) {
      errorMsg.value = problems.join(' ');
      status.value = 'error';
      return;
    }
    status.value = 'saving';
    ipc.policy
      .set(policy.value)
      .then((raw: unknown) => {
        policy.value = normalizePolicy(raw);
        status.value = 'saved';
      })
      .catch((e: unknown) => {
        errorMsg.value = e instanceof Error ? e.message : String(e);
        status.value = 'error';
      });
  };

  return (
    <div className="settings-section" data-testid="policy-panel">
      <h3>How Foundry works for you</h3>
      <p className="hint">These are your choices. Foundry never lets an agent skip a required decision, no matter what you set here.</p>

      <label>
        Sign-off level
        <select
          value={policy.value.gate_mode}
          onChange={(e) => { policy.value = { ...policy.value, gate_mode: (e.target as HTMLSelectElement).value }; }}
        >
          {GATE_MODE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </label>

      <label>
        Research depth
        <select
          value={policy.value.research_depth}
          onChange={(e) => { policy.value = { ...policy.value, research_depth: (e.target as HTMLSelectElement).value }; }}
        >
          {RESEARCH_DEPTH_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </label>

      <label>
        Budget cap (USD, 0 = no cap)
        <input
          type="number"
          min={0}
          step={1}
          value={policy.value.budget_cap_usd}
          onChange={(e) => {
            const n = Number((e.target as HTMLInputElement).value);
            policy.value = { ...policy.value, budget_cap_usd: Number.isFinite(n) ? n : 0 };
          }}
        />
      </label>

      <label>
        Standards profile
        <input
          type="text"
          value={policy.value.standards_profile}
          onChange={(e) => { policy.value = { ...policy.value, standards_profile: (e.target as HTMLInputElement).value }; }}
        />
      </label>

      <button type="button" onClick={save} disabled={status.value === 'saving' || status.value === 'loading'}>
        {status.value === 'saving' ? 'Saving…' : 'Save'}
      </button>
      {status.value === 'saved' && <span className="status-ok">Saved</span>}
      {status.value === 'error' && <span className="status-error">{errorMsg.value}</span>}
    </div>
  );
}
