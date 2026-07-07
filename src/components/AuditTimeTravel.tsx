// AuditTimeTravel.tsx — the audit time-travel scrubber (#15).
//
// Loads the replay timeline from the `audit:replay-timeline` IPC command
// (python/signalos_lib/audit_replay.build_timeline — one frame per audit
// entry, each carrying the cumulative `state_after`). A range slider moves
// over the frames; the panel beside it renders the selected frame's
// reconstructed state: signed gates (G0–G5 chips), the active wave, the
// frame's summary + timestamp, and the event list up to that index.
//
// Defensive by design: the backend command is being built against the
// contract {status:"ok", frames:[{index, ts, summary, entry, state_after}],
// truncated?} — missing fields are tolerated and an unknown command shows a
// graceful error line instead of breaking the History view.
//
// State lives in module-level signals (not preact/hooks — see MEMORY.md);
// the component kicks the load off on first render.

import { signal } from '@preact/signals';
import * as ipc from '../js/ipc.js';

const GATE_ORDER = ['G0', 'G1', 'G2', 'G3', 'G4', 'G5'] as const;

/** How many frames we ask the backend for. Large trails are capped here —
 *  the section then shows "showing last N". */
export const TIMELINE_LIMIT = 500;

/** How many event rows the "events so far" list renders at most. */
const EVENT_ROWS_CAP = 40;

export interface ReplayGateState {
  signed?: boolean;
  role?: string | null;
  ts?: string | null;
}

export interface ReplayStateAfter {
  index?: number;
  ts?: string | null;
  action?: string | null;
  wave?: string | null;
  gates?: Record<string, ReplayGateState>;
  events_applied?: number;
  files_touched?: number;
  overrides?: number;
}

export interface ReplayFrame {
  index?: number;
  ts?: string | null;
  summary?: string;
  entry?: Record<string, unknown>;
  state_after?: ReplayStateAfter;
}

export const timelineStatus = signal<'idle' | 'loading' | 'ready' | 'error'>('idle');
export const timelineFrames = signal<ReplayFrame[]>([]);
export const timelineTruncated = signal<boolean>(false);
export const timelineError = signal<string | null>(null);
export const timelineIndex = signal<number>(0);

/** Test hook: reset the module signals between renders. */
export function __resetAuditTimelineForTests(): void {
  timelineStatus.value = 'idle';
  timelineFrames.value = [];
  timelineTruncated.value = false;
  timelineError.value = null;
  timelineIndex.value = 0;
}

export async function loadAuditTimeline(): Promise<void> {
  if (timelineStatus.value === 'loading') return;
  timelineStatus.value = 'loading';
  timelineError.value = null;
  try {
    const raw = await ipc.signal.runAndWait(
      'audit:replay-timeline',
      [JSON.stringify({ limit: TIMELINE_LIMIT })],
      15000,
    );
    const res = (raw && typeof raw === 'object' ? raw : {}) as {
      status?: string;
      frames?: unknown;
      truncated?: boolean;
      error?: string;
    };
    if (res.status && res.status !== 'ok') {
      throw new Error(res.error || `Time travel unavailable (${res.status}).`);
    }
    const frames = Array.isArray(res.frames)
      ? (res.frames.filter((f) => f && typeof f === 'object') as ReplayFrame[])
      : [];
    timelineFrames.value = frames;
    timelineTruncated.value = Boolean(res.truncated);
    // Start at "now" — the newest frame.
    timelineIndex.value = Math.max(0, frames.length - 1);
    timelineStatus.value = 'ready';
  } catch (err) {
    timelineFrames.value = [];
    timelineStatus.value = 'error';
    timelineError.value = err instanceof Error ? err.message : String(err);
  }
}

function frameTitle(frame: ReplayFrame): string {
  return frame.summary || String(frame.entry?.action || 'event');
}

export function AuditTimeTravel() {
  // Kick the load off on first render. `loadAuditTimeline` flips the status
  // synchronously, so re-renders can't double-fire the request.
  if (timelineStatus.value === 'idle') {
    void loadAuditTimeline();
  }

  const status = timelineStatus.value;
  const frames = timelineFrames.value;

  if (status === 'error') {
    return (
      <div className="card" data-testid="time-travel-error">
        <div className="secrets-head">
          <h3>Time travel</h3>
        </div>
        <div style={{ padding: '14px', fontSize: '12.5px', color: 'var(--ink-3)' }}>
          <i className="ti ti-alert-circle" style={{ verticalAlign: 'middle' }}></i>{' '}
          Time travel is unavailable: {timelineError.value || 'the engine did not respond.'}
        </div>
      </div>
    );
  }

  // Empty trail (or still loading) → hide the section entirely.
  if (frames.length === 0) return null;

  const idx = Math.max(0, Math.min(timelineIndex.value, frames.length - 1));
  const frame = frames[idx] || {};
  const state = frame.state_after || {};
  const gates = state.gates || {};
  const wave = state.wave;

  const eventsUpTo = frames.slice(0, idx + 1);
  const hiddenRows = Math.max(0, eventsUpTo.length - EVENT_ROWS_CAP);
  const visibleRows = eventsUpTo.slice(-EVENT_ROWS_CAP);

  return (
    <div className="card" data-testid="time-travel">
      <div className="secrets-head">
        <h3>Time travel</h3>
        <div style={{ fontSize: '11.5px', color: 'var(--ink-3)' }}>
          {(timelineTruncated.value || frames.length >= TIMELINE_LIMIT)
            ? `showing last ${frames.length} events`
            : `${frames.length} event${frames.length === 1 ? '' : 's'}`}
        </div>
      </div>

      <div style={{ padding: '4px 14px 10px' }}>
        <input
          type="range"
          data-testid="time-travel-slider"
          min={0}
          max={frames.length - 1}
          step={1}
          value={idx}
          style={{ width: '100%' }}
          aria-label="Audit timeline position"
          onInput={(e) => {
            const v = Number((e.target as HTMLInputElement).value);
            timelineIndex.value = Number.isFinite(v) ? v : 0;
          }}
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10.5px', color: 'var(--ink-3)', fontFamily: 'var(--f-mono)' }}>
          <span>1</span>
          <span data-testid="time-travel-position">{idx + 1} / {frames.length}</span>
          <span>{frames.length}</span>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '14px', padding: '0 14px 14px' }}>
        {/* State panel: what was true immediately after this entry. */}
        <div data-testid="time-travel-state" style={{ flex: '1 1 240px', minWidth: '220px' }}>
          <div className="sec-cap" style={{ marginBottom: '6px' }}>State after this event</div>
          <div style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--ink-1)' }} data-testid="time-travel-summary">
            {frameTitle(frame)}
          </div>
          <div style={{ fontSize: '11px', color: 'var(--ink-3)', marginBottom: '8px' }}>
            {frame.ts || state.ts || 'no timestamp'}
          </div>
          <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', marginBottom: '8px' }}>
            {GATE_ORDER.map((g) => {
              const signed = Boolean(gates[g]?.signed);
              return (
                <span
                  key={g}
                  data-testid={`time-travel-gate-${g}`}
                  data-signed={signed ? 'true' : 'false'}
                  className={signed ? 'history-badge done' : 'history-badge'}
                  title={signed
                    ? `${g} signed${gates[g]?.role ? ` by ${gates[g]?.role}` : ''}${gates[g]?.ts ? ` at ${gates[g]?.ts}` : ''}`
                    : `${g} not signed at this point`}
                  style={signed ? undefined : { opacity: 0.45 }}
                >
                  {signed ? <i className="ti ti-check"></i> : null} {g}
                </span>
              );
            })}
          </div>
          <div style={{ fontSize: '12px', color: 'var(--ink-2)', display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span data-testid="time-travel-wave">
              <i className="ti ti-wave-sine" style={{ verticalAlign: 'middle' }}></i>{' '}
              {wave ? `Active wave: ${wave}` : 'No active wave'}
            </span>
            <span>
              <i className="ti ti-list-details" style={{ verticalAlign: 'middle' }}></i>{' '}
              {state.events_applied ?? idx + 1} events applied
              {typeof state.overrides === 'number' && state.overrides > 0 ? ` · ${state.overrides} override${state.overrides === 1 ? '' : 's'}` : ''}
            </span>
          </div>
        </div>

        {/* Event list up to (and highlighting) the selected index. */}
        <div data-testid="time-travel-events" style={{ flex: '1 1 280px', minWidth: '240px' }}>
          <div className="sec-cap" style={{ marginBottom: '6px' }}>Events up to here</div>
          {hiddenRows > 0 ? (
            <div style={{ fontSize: '11px', color: 'var(--ink-3)', padding: '2px 0 6px' }}>
              … {hiddenRows} earlier event{hiddenRows === 1 ? '' : 's'} not shown
            </div>
          ) : null}
          {visibleRows.map((f, i) => {
            const realIndex = idx + 1 - visibleRows.length + i;
            const isCurrent = realIndex === idx;
            return (
              <div
                className="history-item"
                key={realIndex}
                data-testid={isCurrent ? 'time-travel-current-event' : undefined}
                style={isCurrent
                  ? { background: 'var(--accent-soft)', borderRadius: 'var(--r-sm)' }
                  : { opacity: 0.75 }}
              >
                <div className="history-ic build"><i className={`ti ${isCurrent ? 'ti-player-play' : 'ti-point'}`}></i></div>
                <div className="history-tx">
                  <div className="history-title" style={{ fontSize: '12px' }}>{frameTitle(f)}</div>
                  <div className="history-meta">{f.ts || ''}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
