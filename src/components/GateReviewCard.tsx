// GateReviewCard.tsx — the gate review surface (Phase 1.5). Renders ALL FIVE
// verdicts the governance layer supports (plan §"5 verdicts" / gate_review.py):
//
//   approve                 → sign + advance
//   approve-with-conditions → sign + log conditions (feedback optional/encouraged)
//   request-changes         → bounded rework (max 3), feedback REQUIRED
//   reject                  → bounded restart (max 2), feedback REQUIRED
//   waive                   → skip with justification, feedback REQUIRED (INV-1)
//
// The card collects the verdict + justification and hands it to `onVerdict`.
// Wiring to the paused agent loop (`agent:verdict` IPC) happens in Phase 3;
// this component is provider-agnostic and unit-testable.
//
// Preact only.

import { useSignal } from '@preact/signals';

export type GateVerdict =
  | 'approve'
  | 'approve-with-conditions'
  | 'request-changes'
  | 'reject'
  | 'waive';

export interface GateReviewSubmission {
  verdict: GateVerdict;
  feedback: string;
}

export interface GateReviewCardProps {
  /** Gate code, e.g. "G3". */
  gate: string;
  /** User-facing specialist / gate title, e.g. "Design direction". */
  title: string;
  /** Plain-language question shown to the user. */
  question: string;
  /** Optional evidence / summary children rendered above the verdict buttons. */
  children?: preact.ComponentChildren;
  /** Called with the chosen verdict + feedback. */
  onVerdict?: (submission: GateReviewSubmission) => void;
  /** Disables interaction once a verdict has been submitted. */
  resolved?: GateVerdict | null;
  /** Human-friendly capacity the signature is recorded under, e.g. "Principal Engineer".
   *  When set, the card shows "Signing as …" so the user need not pick a role. */
  signingAs?: string;
}

interface VerdictDef {
  verdict: GateVerdict;
  label: string;
  icon: string;
  cls: string;
  /** Whether feedback/justification is mandatory for this verdict. */
  requiresFeedback: boolean;
  /** Placeholder hint for the feedback field. */
  hint: string;
}

const VERDICTS: VerdictDef[] = [
  { verdict: 'approve', label: 'Approve', icon: 'ti-circle-check', cls: 'gv-approve', requiresFeedback: false, hint: '' },
  { verdict: 'approve-with-conditions', label: 'Approve with Conditions', icon: 'ti-checkup-list', cls: 'gv-conditions', requiresFeedback: false, hint: 'List the conditions to log alongside the signature…' },
  { verdict: 'request-changes', label: 'Request Changes', icon: 'ti-arrow-back-up', cls: 'gv-changes', requiresFeedback: true, hint: 'What needs to change? (required)' },
  { verdict: 'reject', label: 'Reject', icon: 'ti-ban', cls: 'gv-reject', requiresFeedback: true, hint: 'Why is this rejected? (required)' },
  { verdict: 'waive', label: 'Waive', icon: 'ti-shield-x', cls: 'gv-waive', requiresFeedback: true, hint: 'Justification for waiving this gate (required, audit-logged)' },
];

/** Verdicts that surface a feedback textarea (conditions or required reason). */
const FEEDBACK_VERDICTS = new Set<GateVerdict>([
  'approve-with-conditions',
  'request-changes',
  'reject',
  'waive',
]);

export function GateReviewCard({ gate, title, question, children, onVerdict, resolved, signingAs }: GateReviewCardProps) {
  const selected = useSignal<GateVerdict | null>(null);
  const feedback = useSignal('');
  const error = useSignal<string | null>(null);

  const isResolved = !!resolved;
  const active = selected.value;
  const showFeedback = active != null && FEEDBACK_VERDICTS.has(active);

  const submit = () => {
    if (!active) return;
    const def = VERDICTS.find((v) => v.verdict === active)!;
    const text = feedback.value.trim();
    if (def.requiresFeedback && !text) {
      error.value = 'A justification is required for this verdict.';
      return;
    }
    error.value = null;
    onVerdict?.({ verdict: active, feedback: text });
  };

  return (
    <div className="msg spark" data-testid="gate-review-card">
      <div className="msg-av"><i className="ti ti-gavel" style={{ fontSize: '17px' }}></i></div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="card gate-review" data-gate={gate} data-resolved={resolved || ''}>
          <div className="gate-head" style={{ marginBottom: '10px' }}>
            <div className="gate-ic"><i className="ti ti-shield-check"></i></div>
            <div className="gate-tx">
              <h3 style={{ margin: 0 }}>{title}</h3>
              <p style={{ margin: '2px 0 0', fontSize: '12.5px' }}>{gate} review</p>
            </div>
            {isResolved ? <div className="gate-badge passed">{resolved}</div> : null}
          </div>

          <div className="gate-review-q">{question}</div>

          {children ? <div className="gate-review-evidence">{children}</div> : null}

          <div className="gate-review-verdicts" role="group" aria-label="Verdict">
            {VERDICTS.map((v) => (
              <button
                key={v.verdict}
                type="button"
                className={`gate-verdict-btn ${v.cls}${active === v.verdict ? ' selected' : ''}`}
                data-testid={`verdict-${v.verdict}`}
                data-verdict={v.verdict}
                disabled={isResolved}
                aria-pressed={active === v.verdict}
                onClick={() => { selected.value = v.verdict; error.value = null; }}
              >
                <i className={`ti ${v.icon}`}></i> {v.label}
              </button>
            ))}
          </div>

          {showFeedback && !isResolved ? (
            <div className="gate-review-feedback">
              <textarea
                className="gate-review-textarea"
                data-testid="verdict-feedback"
                placeholder={VERDICTS.find((v) => v.verdict === active)?.hint || 'Add a note…'}
                value={feedback.value}
                onInput={(e) => { feedback.value = (e.target as HTMLTextAreaElement).value; }}
                rows={3}
              />
            </div>
          ) : null}

          {error.value ? <div className="gate-review-error" data-testid="verdict-error">{error.value}</div> : null}

          {!isResolved ? (
            <div className="gate-review-actions">
              {signingAs ? (
                <span className="gate-signing-as" data-testid="gate-signing-as" style={{ fontSize: '12px', color: 'var(--ink-3)' }}>
                  <i className="ti ti-id-badge-2" style={{ verticalAlign: 'middle' }}></i> Signing as {signingAs}
                </span>
              ) : null}
              <button
                type="button"
                className="btn btn-primary"
                data-testid="verdict-submit"
                disabled={!active}
                onClick={submit}
              >
                Submit verdict <i className="ti ti-arrow-right"></i>
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export { VERDICTS as GATE_VERDICTS, FEEDBACK_VERDICTS };
