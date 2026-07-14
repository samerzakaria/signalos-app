import { signal } from '@preact/signals';
import { panel } from '../../js/ipc.js';
import { viewClass } from '../viewShell';

// One question fanned out to several models independently, so you get candid,
// uncorrelated second opinions before you decide. The four defaults below are
// the panel's cross-vendor lineup; the backend accepts overrides but the view
// ships with these.
interface PanelAnswer {
  model: string;
  name: string;
  text: string;
  ok: boolean;
  error?: string | null;
}

interface PanelResult {
  answers: PanelAnswer[];
  cost_usd: number | null;
  models: string[];
  system: string;
}

const DEFAULT_MODELS: { name: string; id: string }[] = [
  { name: 'Sonnet-5', id: 'anthropic/claude-sonnet-5' },
  { name: 'GPT-5.6-Sol', id: 'openai/gpt-5.6-sol' },
  { name: 'DeepSeek-V4-Pro', id: 'deepseek/deepseek-v4-pro' },
  { name: 'Qwen3.7-Max', id: 'qwen/qwen3.7-max' },
];

// Module-scope signals survive the component's re-renders (the view re-runs on
// every signal change). Mirrors the pattern used in Sidebar.tsx.
const warQuestion = signal<string>('');
const warRunning = signal<boolean>(false);
const warError = signal<string | null>(null);
const warResult = signal<PanelResult | null>(null);

/** Reset the module-level panel state — used by tests between renders. */
export function __resetWarRoomForTests(): void {
  warQuestion.value = '';
  warRunning.value = false;
  warError.value = null;
  warResult.value = null;
}

async function consultPanel(): Promise<void> {
  const question = warQuestion.value.trim();
  if (!question || warRunning.value) return;
  warRunning.value = true;
  warError.value = null;
  try {
    const res = await panel.consult(question);
    // The envelope may arrive as the object itself or wrapped in `.data`
    // (per the locked contract) — unwrap defensively either way.
    const payload = ((res as { data?: PanelResult } | null)?.data ?? res) as PanelResult | null;
    warResult.value = payload && Array.isArray(payload.answers)
      ? payload
      : { answers: [], cost_usd: null, models: [], system: '' };
  } catch (e: unknown) {
    warError.value = e instanceof Error ? e.message : String(e);
    warResult.value = null;
  } finally {
    warRunning.value = false;
  }
}

export function WarRoomView() {
  const question = warQuestion.value;
  const running = warRunning.value;
  const error = warError.value;
  const result = warResult.value;

  const canConsult = question.trim().length > 0 && !running;
  const btnLabel = running
    ? `Consulting ${DEFAULT_MODELS.length} models…`
    : 'Consult panel';

  return (
    <>
<div className={viewClass('warroom')} data-view="warroom">
        <div className="page-head">
          <h1>War Room</h1>
          <p>Ask one question, get candid second opinions from several models independently — then decide.</p>
        </div>
        <div className="stack">
          <div className="card card-pad">
            <div className="sec-cap">The panel</div>
            <div className="chips">
              {DEFAULT_MODELS.map((m) => (
                <span className="chip" key={m.id} title={m.id} style={{ cursor: 'default' }}>{m.name}</span>
              ))}
            </div>
            <p
              className="warroom-key-hint"
              style={{ margin: '8px 0 0', fontSize: '12px', color: 'var(--ink-2)' }}
            >
              Cross-vendor via OpenRouter. Add <code>OPENROUTER_API_KEY</code> in{' '}
              <strong>Settings → Secrets</strong> to enable the panel.
            </p>
            <textarea
              className="env-textarea"
              placeholder="What decision, design, or claim do you want pressure-tested?"
              value={question}
              onInput={(e) => { warQuestion.value = (e.target as HTMLTextAreaElement).value; }}
            />
            <div style={{ marginTop: '14px' }}>
              <button
                className="btn btn-primary"
                type="button"
                onClick={() => { void consultPanel(); }}
                disabled={!canConsult}
              >
                <i className={`ti ${running ? 'ti-loader-2' : 'ti-messages'}`}></i> {btnLabel}
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
              {result.answers.map((a, i) => (
                <div
                  className="card card-pad"
                  data-testid="warroom-answer"
                  key={a.model || i}
                >
                  <div className="sec-cap" style={{ marginBottom: '10px' }}>
                    {a.name || a.model}{' '}
                    <span style={{ fontFamily: 'var(--f-mono)', textTransform: 'none', letterSpacing: 0, fontWeight: 400, color: 'var(--ink-3)' }}>{a.model}</span>
                  </div>
                  {a.ok ? (
                    <p style={{ whiteSpace: 'pre-wrap', fontSize: '13.5px', lineHeight: 1.55, margin: 0, color: 'var(--ink)' }}>{a.text}</p>
                  ) : (
                    <p
                      data-testid="warroom-answer-error"
                      style={{ whiteSpace: 'pre-wrap', fontSize: '13px', lineHeight: 1.55, margin: 0, color: 'var(--danger-deep)' }}
                    >
                      <i className="ti ti-alert-circle" style={{ verticalAlign: 'middle' }}></i> {a.error || 'This model did not return an answer.'}
                    </p>
                  )}
                </div>
              ))}
              <div
                className="sum-chip"
                data-testid="warroom-cost"
                style={{ alignSelf: 'flex-start', background: 'var(--surface-warm)', color: 'var(--ink-2)' }}
              >
                <i className="ti ti-coin"></i>
                {typeof result.cost_usd === 'number'
                  ? `Panel cost: $${result.cost_usd.toFixed(4)}`
                  : 'Panel cost: cost unavailable'}
              </div>
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
