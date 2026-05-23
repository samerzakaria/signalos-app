// VelocityPanel.tsx — Phase 13 dashboard sidebar widget.
//
// Surfaces wave-velocity metrics derived from .signalos/AUDIT_TRAIL.jsonl
// and existing autoplan tasks. No new persistence. The panel calls
// `get_velocity_metrics` (Rust IPC) which dispatches the Python sidecar
// command `signal-velocity --json` and returns a JSON string.
//
// Empty-state contract: if the sidecar returns an empty trail / no
// autoplan waves, the panel renders a "No velocity data yet" message
// rather than crashing or displaying NaN / undefined.
//
// CSP rules: handlers are Preact `onClick={}` (CSP-safe). No inline
// `onclick=` / `style=` attributes in hand-written HTML.

import { useEffect, useState } from 'preact/hooks';
import { signal as signalIpc } from '../../js/ipc.js';

export interface VelocityBurndownRow {
  wave: string;
  total: number;
  completed: number;
}

export interface VelocityMetrics {
  sessions_per_day: number;
  scope_card_burndown: VelocityBurndownRow[];
  eta_days: number | null;
  last_session_at: string | null;
  window_days: number;
  generated_at: string;
}

interface VelocityState {
  loading: boolean;
  error: string | null;
  metrics: VelocityMetrics | null;
}

const INITIAL_STATE: VelocityState = { loading: true, error: null, metrics: null };

async function fetchVelocityMetrics(): Promise<VelocityMetrics> {
  // The Rust IPC dispatches `signal-velocity --json` and returns the
  // sidecar's stdout as a string. `runAndWait` waits on the matching
  // `sidecar:response` event and resolves with the raw payload.
  //
  // We use the existing `signal.runAndWait` plumbing (proven path —
  // releaseReadiness.ts uses the same shape) rather than introducing
  // a parallel `invoke('get_velocity_metrics')` request-id round-trip.
  // The Rust side maps both to the same sidecar transport.
  const raw = await signalIpc.runAndWait('signal-velocity', ['--json'], 8000);
  if (raw == null) {
    throw new Error('Empty response from signal-velocity');
  }
  const text = typeof raw === 'string' ? raw : JSON.stringify(raw);
  const parsed = JSON.parse(text) as VelocityMetrics;
  return parsed;
}

function isEmptyMetrics(m: VelocityMetrics): boolean {
  return (
    (m.scope_card_burndown?.length ?? 0) === 0
    && (m.sessions_per_day ?? 0) === 0
    && !m.last_session_at
  );
}

function formatLastSession(iso: string | null): string {
  if (!iso) return 'never';
  // Audit timestamps are ISO-8601 UTC. Show a readable local-time-ish
  // string without depending on heavyweight date libs.
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function formatEta(eta: number | null): string {
  if (eta == null) return 'insufficient data';
  if (eta < 1) return '< 1 day';
  if (eta === 1) return '1 day';
  return `${eta} days`;
}

export function VelocityPanel() {
  const [state, setState] = useState<VelocityState>(INITIAL_STATE);

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, metrics: state.metrics });
    fetchVelocityMetrics()
      .then((metrics) => {
        if (cancelled) return;
        setState({ loading: false, error: null, metrics });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setState({ loading: false, error: message, metrics: null });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRefresh = () => {
    setState({ loading: true, error: null, metrics: state.metrics });
    fetchVelocityMetrics()
      .then((metrics) => setState({ loading: false, error: null, metrics }))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        setState({ loading: false, error: message, metrics: null });
      });
  };

  return (
    <div className="card card-pad" data-testid="velocity-panel">
      <div className="sec-cap">Wave velocity</div>
      <div className="velocity-head">
        <h3>Sessions and ETA</h3>
        <button
          className="btn btn-soft btn-compact"
          type="button"
          onClick={handleRefresh}
          disabled={state.loading}
          data-testid="velocity-refresh"
        >
          <i className={`ti ${state.loading ? 'ti-loader-2' : 'ti-refresh'}`}></i> Refresh
        </button>
      </div>

      {state.error ? (
        <div className="velocity-error" data-testid="velocity-error">
          <i className="ti ti-alert-triangle"></i>{' '}
          Could not load velocity: {state.error}
        </div>
      ) : null}

      {!state.error && state.metrics && isEmptyMetrics(state.metrics) ? (
        <div className="velocity-empty" data-testid="velocity-empty">
          No velocity data yet. Velocity appears once you complete a
          session or have a wave in progress.
        </div>
      ) : null}

      {!state.error && state.metrics && !isEmptyMetrics(state.metrics) ? (
        <div className="velocity-body" data-testid="velocity-body">
          <div className="velocity-row">
            <span className="velocity-label">Sessions / day</span>
            <span className="velocity-value" data-testid="velocity-sessions-per-day">
              {state.metrics.sessions_per_day.toFixed(2)}
            </span>
            <span className="velocity-meta">
              over last {state.metrics.window_days} day(s)
            </span>
          </div>
          <div className="velocity-row">
            <span className="velocity-label">Last session</span>
            <span className="velocity-value" data-testid="velocity-last-session">
              {formatLastSession(state.metrics.last_session_at)}
            </span>
          </div>
          <div className="velocity-row">
            <span className="velocity-label">ETA (remaining)</span>
            <span className="velocity-value" data-testid="velocity-eta">
              {formatEta(state.metrics.eta_days)}
            </span>
          </div>
          <div className="velocity-burndown" data-testid="velocity-burndown">
            <div className="velocity-burndown-head">Scope-card burndown</div>
            {state.metrics.scope_card_burndown.length === 0 ? (
              <div className="velocity-burndown-empty">No waves in progress yet.</div>
            ) : (
              <ul className="velocity-burndown-list">
                {state.metrics.scope_card_burndown.map((row) => {
                  const pct = row.total > 0
                    ? Math.round((row.completed / row.total) * 100)
                    : 0;
                  return (
                    <li
                      className="velocity-burndown-row"
                      key={row.wave}
                      data-testid={`velocity-burndown-wave-${row.wave}`}
                    >
                      <span className="velocity-wave-label">Wave {row.wave}</span>
                      <span className="velocity-wave-count">
                        {row.completed} / {row.total}
                      </span>
                      <span className="velocity-wave-pct">{pct}%</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      ) : null}

      {!state.error && !state.metrics && state.loading ? (
        <div className="velocity-loading" data-testid="velocity-loading">
          <i className="ti ti-loader-2"></i> Reading velocity signal...
        </div>
      ) : null}
    </div>
  );
}
