import { signal } from '@preact/signals';
import { panel } from '../../js/ipc.js';
import { viewClass } from '../viewShell';

interface PanelAnswer {
  candidate_id?: string;
  model: string;
  name: string;
  text: string;
  ok: boolean;
  error?: string | null;
  revised?: boolean;
}

interface PanelDecision {
  decision_state: 'verified_consensus' | 'provisional_majority' | 'unresolved_escalate';
  selected_candidate_id?: string | null;
  recommendation: string;
  rationale: string;
  consensus?: string[];
  disagreements?: string[];
  dissent_summary?: string;
  response_to_dissent?: string;
  conditions_to_reconsider?: string[];
  next_actions?: string[];
  confidence?: number | null;
  fallback?: boolean;
  engine_state_adjustment?: string;
}

interface PanelDissent {
  status: 'available' | 'unavailable' | 'not_run';
  name?: string;
  model?: string;
  thesis?: string;
  counter_recommendation?: string;
  evidence?: string[];
  failure_modes?: string[];
  conditions_that_make_it_right?: string[];
  error?: string;
}

interface PanelResult {
  schema_version?: string;
  protocol_version?: string;
  status?: 'complete' | 'degraded' | 'failed';
  answers: PanelAnswer[];
  decision?: PanelDecision | null;
  decision_state?: PanelDecision['decision_state'] | null;
  dissent?: PanelDissent;
  warnings?: string[];
  failures?: Array<{ stage: string; model: string; error: string }>;
  cost_usd: number | null;
  cost?: { source?: string; warning?: string | null };
  models: string[];
  system: string;
}

const COUNCIL_ROLES: Array<{ role: string; name: string; id: string }> = [
  { role: 'Adviser', name: 'Sonnet 5', id: 'anthropic/claude-sonnet-5' },
  { role: 'Adviser', name: 'DeepSeek V4 Pro', id: 'deepseek/deepseek-v4-pro' },
  { role: 'Adviser', name: 'Qwen3.7 Max', id: 'qwen/qwen3.7-max' },
  { role: 'Verifier · Juror', name: 'Fable 5', id: 'anthropic/claude-fable-5' },
  { role: 'Red team · Juror', name: 'Grok 4.5', id: 'x-ai/grok-4.5' },
  { role: 'Chair · Juror', name: 'GPT-5.6 Sol Pro', id: 'openai/gpt-5.6-sol-pro' },
];

const warQuestion = signal<string>('');
const warRunning = signal<boolean>(false);
const warError = signal<string | null>(null);
const warResult = signal<PanelResult | null>(null);

export function __resetWarRoomForTests(): void {
  warQuestion.value = '';
  warRunning.value = false;
  warError.value = null;
  warResult.value = null;
}

function readableState(state: PanelDecision['decision_state']): string {
  if (state === 'verified_consensus') return 'Verified consensus';
  if (state === 'provisional_majority') return 'Provisional majority';
  return 'Unresolved — escalate';
}

async function consultPanel(): Promise<void> {
  const question = warQuestion.value.trim();
  if (!question || warRunning.value) return;
  warRunning.value = true;
  warError.value = null;
  try {
    const res = await panel.consult(question, { mode: 'council' });
    const payload = ((res as { data?: PanelResult } | null)?.data ?? res) as PanelResult | null;
    warResult.value = payload && Array.isArray(payload.answers)
      ? { ...payload, status: payload.status ?? 'complete' }
      : { answers: [], cost_usd: null, models: [], system: '', status: 'failed' };
  } catch (error: unknown) {
    warError.value = error instanceof Error ? error.message : String(error);
    warResult.value = null;
  } finally {
    warRunning.value = false;
  }
}

function TextList({ items }: { items?: string[] }) {
  if (!items?.length) return null;
  return (
    <ul style={{ margin: '8px 0 0', paddingLeft: '20px' }}>
      {items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
    </ul>
  );
}

export function WarRoomView() {
  const question = warQuestion.value;
  const running = warRunning.value;
  const error = warError.value;
  const result = warResult.value;
  const decision = result?.decision ?? null;
  const dissent = result?.dissent;
  const canConsult = question.trim().length > 0 && !running;

  return (
    <div className={viewClass('warroom')} data-view="warroom">
      <div className="page-head">
        <h1>War Room</h1>
        <p>Independent advice, bounded challenge, a blind jury, and a decision that keeps its minority report.</p>
      </div>

      <div className="stack">
        <div className="card card-pad">
          <div className="sec-cap">Governed council</div>
          <div className="chips" aria-label="Council roles">
            {COUNCIL_ROLES.map((member) => (
              <span className="chip" key={member.id} title={member.id} style={{ cursor: 'default' }}>
                {member.name} · {member.role}
              </span>
            ))}
          </div>
          <p style={{ color: 'var(--ink-3)', fontSize: '12.5px', margin: '12px 0' }}>
            The first opinions are sealed. Review is anonymous, critique is bounded, and agreement is never forced.
          </p>
          <p className="warroom-key-hint" style={{ color: 'var(--ink-2)', fontSize: '12px', margin: '0 0 12px' }}>
            Cross-vendor via OpenRouter. Add <code>OPENROUTER_API_KEY</code> in{' '}
            <strong>Settings → Secrets</strong> to enable the council.
          </p>
          <textarea
            className="env-textarea"
            aria-label="Decision or question"
            placeholder="What decision, design, or claim do you want pressure-tested? Include the evidence and constraints the council needs."
            value={question}
            onInput={(event) => { warQuestion.value = (event.target as HTMLTextAreaElement).value; }}
          />
          <div style={{ marginTop: '14px' }}>
            <button
              className="btn btn-primary"
              type="button"
              onClick={() => { void consultPanel(); }}
              disabled={!canConsult}
            >
              <i className={`ti ${running ? 'ti-loader-2' : 'ti-messages'}`}></i>{' '}
              {running ? 'Running governed council…' : 'Consult council'}
            </button>
          </div>
        </div>

        {error ? (
          <div
            className="card card-pad"
            data-testid="warroom-error"
            style={{ background: 'var(--danger-soft)', color: 'var(--danger-deep)' }}
          >
            <i className="ti ti-alert-triangle" style={{ verticalAlign: 'middle' }}></i> {error}
          </div>
        ) : null}

        {result ? (
          <>
            <div className="card card-pad" data-testid="warroom-status">
              <div className="sec-cap">Council status</div>
              <p style={{ margin: '8px 0 0', color: result.status === 'failed' ? 'var(--danger-deep)' : 'var(--ink)' }}>
                {result.status === 'complete' ? 'Complete' : result.status === 'degraded' ? 'Degraded — review warnings' : 'Failed — no defensible council decision'}
                {result.protocol_version ? ` · ${result.protocol_version}` : ''}
              </p>
            </div>

            {decision ? (
              <div className="card card-pad" data-testid="warroom-decision">
                <div className="sec-cap">Decision</div>
                <h2 style={{ fontSize: '18px', margin: '10px 0 6px' }}>
                  {readableState(decision.decision_state)}
                </h2>
                <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, margin: '0 0 12px' }}>
                  {decision.recommendation}
                </p>
                <p style={{ whiteSpace: 'pre-wrap', color: 'var(--ink-2)', lineHeight: 1.55, margin: 0 }}>
                  {decision.rationale}
                </p>
                {decision.engine_state_adjustment ? (
                  <p style={{ color: 'var(--danger-deep)', marginTop: '10px' }}>{decision.engine_state_adjustment}</p>
                ) : null}
                {decision.fallback ? (
                  <p style={{ color: 'var(--danger-deep)', marginTop: '10px' }}>This is a deterministic jury fallback; chair validation failed.</p>
                ) : null}
                {decision.disagreements?.length ? (
                  <div style={{ marginTop: '14px' }}>
                    <div className="sec-cap">Unresolved disagreements</div>
                    <TextList items={decision.disagreements} />
                  </div>
                ) : null}
                {decision.response_to_dissent ? (
                  <div style={{ marginTop: '14px' }}>
                    <div className="sec-cap">Chair response to dissent</div>
                    <p style={{ whiteSpace: 'pre-wrap', margin: '8px 0 0' }}>{decision.response_to_dissent}</p>
                  </div>
                ) : null}
                {decision.next_actions?.length ? (
                  <div style={{ marginTop: '14px' }}>
                    <div className="sec-cap">Next actions</div>
                    <TextList items={decision.next_actions} />
                  </div>
                ) : null}
                {decision.conditions_to_reconsider?.length ? (
                  <div style={{ marginTop: '14px' }}>
                    <div className="sec-cap">Reconsider when</div>
                    <TextList items={decision.conditions_to_reconsider} />
                  </div>
                ) : null}
              </div>
            ) : null}

            {dissent && dissent.status !== 'not_run' ? (
              <div className="card card-pad" data-testid="warroom-dissent">
                <div className="sec-cap">Minority report · {dissent.name || 'Independent red team'}</div>
                {dissent.status === 'available' ? (
                  <>
                    <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.55, margin: '10px 0 0' }}>
                      {dissent.counter_recommendation || dissent.thesis}
                    </p>
                    <TextList items={dissent.failure_modes} />
                  </>
                ) : (
                  <p style={{ color: 'var(--danger-deep)', margin: '10px 0 0' }}>
                    Dissent unavailable: {dissent.error || 'the red-team call failed'}
                  </p>
                )}
              </div>
            ) : null}

            {result.warnings?.length ? (
              <div className="card card-pad" data-testid="warroom-warnings">
                <div className="sec-cap">Warnings</div>
                <TextList items={result.warnings} />
              </div>
            ) : null}

            {result.failures?.length ? (
              <div className="card card-pad" data-testid="warroom-failures">
                <div className="sec-cap">Failed roles and calls</div>
                <TextList
                  items={result.failures.map((failure) => (
                    `${failure.stage} · ${failure.model}: ${failure.error}`
                  ))}
                />
              </div>
            ) : null}

            <details className="card card-pad" open={!decision} data-testid="warroom-opinions">
              <summary style={{ cursor: 'pointer', fontWeight: 650 }}>
                Independent adviser record ({result.answers.length})
              </summary>
              <div className="stack" style={{ marginTop: '12px' }}>
                {result.answers.map((answer, index) => (
                  <div data-testid="warroom-answer" key={answer.model || index}>
                    <div className="sec-cap" style={{ marginBottom: '8px' }}>
                      {answer.candidate_id ? `${answer.candidate_id} · ` : ''}{answer.name || answer.model}
                      {answer.revised ? ' · revised' : ''}
                    </div>
                    {answer.ok ? (
                      <p style={{ whiteSpace: 'pre-wrap', fontSize: '13.5px', lineHeight: 1.55, margin: 0, color: 'var(--ink)' }}>{answer.text}</p>
                    ) : (
                      <p data-testid="warroom-answer-error" style={{ color: 'var(--danger-deep)', margin: 0 }}>
                        <i className="ti ti-alert-circle" style={{ verticalAlign: 'middle' }}></i>{' '}
                        {answer.error || 'This adviser did not return an answer.'}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </details>

            <div style={{ alignSelf: 'flex-start' }}>
              <div
                className="sum-chip"
                data-testid="warroom-cost"
                style={{ background: 'var(--surface-warm)', color: 'var(--ink-2)' }}
              >
                <i className="ti ti-coin"></i>
                {typeof result.cost_usd === 'number'
                  ? `${result.cost?.source === 'partial_per_response_usage' ? 'Known cost subtotal' : 'Council cost'}: $${result.cost_usd.toFixed(4)}${result.cost?.source ? ` · ${result.cost.source}` : ''}`
                  : 'Council cost: unavailable'}
              </div>
              {result.cost?.warning ? (
                <p data-testid="warroom-cost-warning" style={{ color: 'var(--ink-3)', fontSize: '12px', margin: '6px 0 0' }}>
                  {result.cost.warning}
                </p>
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
