import { signal, computed } from '@preact/signals';
import { useEffect } from 'preact/hooks';

// ── Types ───────────────────────────────────────────────────────────────────

type SubstepState = 'pending' | 'running' | 'done' | 'error';

interface ProgressSession {
  contract: [string, string[]][] | null;
  states: Record<string, SubstepState>; // "phase:substep" → state
  activePhase: string;
  started: number;
  lastDetail: string;
  finished: boolean;
}

interface SidecarProgressEvent {
  id?: string;
  kind?: string;
  phase?: string;
  substep?: string;
  state?: string;
  detail?: string | null;
  ts?: number;
}

// ── State ───────────────────────────────────────────────────────────────────

const sessions = signal<Map<string, ProgressSession>>(new Map());
const tick = signal(0); // forces elapsed time re-render

const STATE_ICON: Record<SubstepState, string> = {
  pending: '\u25CB', // ○
  running: '\u25B6', // ▶
  done:    '\u2713', // ✓
  error:   '\u2715', // ✕
};

const PHASE_TITLES: Record<string, string> = {
  prepare: 'Prepare',
  plan:    'Plan',
  build:   'Build',
  review:  'Review',
  write:   'Write',
  read:    'Read',
  render:  'Render',
};

function phaseTitle(id: string): string {
  return PHASE_TITLES[id] || id.charAt(0).toUpperCase() + id.slice(1);
}

function substepTitle(id: string): string {
  return id.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatElapsed(ms: number): string {
  const sec = Math.max(1, Math.floor(ms / 1000));
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

// ── Public API: start/end a progress session ────────────────────────────────

export function startProgressSession(reqId: string, contractName?: string): void {
  const session: ProgressSession = {
    contract: null,
    states: {},
    activePhase: '',
    started: Date.now(),
    lastDetail: '',
    finished: false,
  };

  // Try to fetch contract from sidecar
  if (contractName) {
    const tauri = (window as any).__TAURI__;
    const invoke = tauri?.core?.invoke || tauri?.invoke;
    if (typeof invoke === 'function') {
      invoke('run_signal_command', { command: 'phase:contract', args: [contractName] })
        .then((result: any) => {
          if (result?.phases) {
            session.contract = result.phases;
            session.activePhase = result.phases[0]?.[0] || '';
            sessions.value = new Map(sessions.value).set(reqId, session);
          }
        })
        .catch(() => { /* contract unavailable, render without it */ });
    }
  }

  const next = new Map(sessions.value);
  next.set(reqId, session);
  sessions.value = next;
}

export function endProgressSession(reqId: string, ok = true): void {
  const s = sessions.value.get(reqId);
  if (!s) return;
  // Mark all running substeps
  if (s.contract) {
    for (const [phase, substeps] of s.contract) {
      for (const sub of substeps) {
        const key = `${phase}:${sub}`;
        if (s.states[key] === 'running') {
          s.states[key] = ok ? 'done' : 'error';
        }
      }
    }
  }
  s.finished = true;
  sessions.value = new Map(sessions.value);
  // Clean up after 4s
  setTimeout(() => {
    const next = new Map(sessions.value);
    next.delete(reqId);
    sessions.value = next;
  }, 4000);
}

// ── Listener (attaches once) ────────────────────────────────────────────────

let listenerAttached = false;

function ensureListener(): void {
  if (listenerAttached) return;
  listenerAttached = true;

  const tauri = (window as any).__TAURI__ as {
    event?: { listen?: (event: string, cb: (e: { payload: SidecarProgressEvent }) => void) => Promise<() => void> };
  } | undefined;
  const listen = tauri?.event?.listen;
  if (typeof listen !== 'function') return;

  listen('sidecar:progress', (e) => {
    const p = e.payload;
    if (!p || !p.id) return;

    const s = sessions.value.get(p.id);
    if (!s || s.finished) return;

    const phase = p.phase || '';
    const substep = p.substep || '_';
    const state = (p.state || 'running') as SubstepState;

    s.states[`${phase}:${substep}`] = state;
    if (state === 'running') s.activePhase = phase;
    s.lastDetail = p.detail || s.lastDetail;

    // If no contract, auto-build one from events
    if (!s.contract) {
      const seenPhases = new Map<string, string[]>();
      for (const key of Object.keys(s.states)) {
        const [ph, sub] = key.split(':');
        if (!seenPhases.has(ph)) seenPhases.set(ph, []);
        const subs = seenPhases.get(ph)!;
        if (!subs.includes(sub)) subs.push(sub);
      }
      s.contract = Array.from(seenPhases.entries());
    } else {
      // Add newly seen substeps to existing contract phases
      const existing = s.contract.find(([ph]) => ph === phase);
      if (existing && !existing[1].includes(substep)) {
        existing[1].push(substep);
      } else if (!existing) {
        s.contract.push([phase, [substep]]);
      }
    }

    sessions.value = new Map(sessions.value);
  }).catch(() => {});
}

// ── Computed active sessions ────────────────────────────────────────────────

const activeSessions = computed(() => {
  // Reference tick to re-render on timer
  void tick.value;
  const result: [string, ProgressSession][] = [];
  for (const [id, s] of sessions.value) {
    result.push([id, s]);
  }
  return result;
});

// ── Component ───────────────────────────────────────────────────────────────

export function ProgressDetail() {
  useEffect(() => {
    ensureListener();
    // Elapsed timer: tick every second while sessions exist
    const interval = setInterval(() => {
      if (sessions.value.size > 0) {
        tick.value = tick.value + 1;
      }
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const active = activeSessions.value;
  if (active.length === 0) return null;

  return (
    <>
      {active.map(([reqId, session]) => (
        <ProgressCard key={reqId} session={session} />
      ))}
    </>
  );
}

function ProgressCard({ session }: { session: ProgressSession }) {
  const elapsed = formatElapsed(Date.now() - session.started);
  const contract = session.contract;

  if (!contract || contract.length === 0) {
    // No contract yet — show minimal spinner
    return (
      <div className="card" style={{ marginBottom: '8px', padding: '12px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <i className="ti ti-loader-2" style={{ animation: 'spin 1s linear infinite' }}></i>
          <span>Working... ({elapsed})</span>
        </div>
        {session.lastDetail ? (
          <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--ink-3)' }}>
            {session.lastDetail}
          </div>
        ) : null}
      </div>
    );
  }

  // Calculate totals
  const totalSubsteps = contract.reduce((a, [, subs]) => a + subs.length, 0);
  const doneCount = Object.values(session.states).filter((v) => v === 'done').length;
  const pct = totalSubsteps > 0 ? Math.round((doneCount / totalSubsteps) * 100) : 0;

  return (
    <div className="card" style={{ marginBottom: '8px', padding: '12px 16px' }}>
      {/* Progress bar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--ink-2)' }}>
          {doneCount} / {totalSubsteps} steps
        </span>
        <span style={{ fontSize: '11px', color: 'var(--ink-3)', fontFamily: 'var(--f-mono)' }}>
          {elapsed}
        </span>
      </div>
      <div style={{ height: '4px', background: 'var(--line)', borderRadius: '2px', overflow: 'hidden', marginBottom: '12px' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: 'var(--accent)', transition: 'width 0.3s var(--ease)' }}></div>
      </div>

      {/* Phase rows */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {contract.map(([phase, substeps]) => {
          const allDone = substeps.every((sub) => session.states[`${phase}:${sub}`] === 'done');
          const anyRunning = substeps.some((sub) => session.states[`${phase}:${sub}`] === 'running');
          const anyError = substeps.some((sub) => session.states[`${phase}:${sub}`] === 'error');
          const phaseState: SubstepState = anyError ? 'error' : allDone ? 'done' : anyRunning ? 'running' : 'pending';

          return (
            <div key={phase} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '4px 10px' }}>
              <span style={{
                fontWeight: 600,
                fontSize: '12.5px',
                minWidth: '72px',
                color: phaseState === 'running' ? 'var(--accent)' : phaseState === 'done' ? 'var(--success)' : phaseState === 'error' ? 'var(--danger)' : 'var(--ink-3)',
              }}>
                {STATE_ICON[phaseState]} {phaseTitle(phase)}
              </span>
              {substeps.map((sub) => {
                const st = (session.states[`${phase}:${sub}`] || 'pending') as SubstepState;
                return (
                  <span
                    key={sub}
                    style={{
                      fontSize: '11.5px',
                      fontFamily: 'var(--f-mono)',
                      color: st === 'running' ? 'var(--accent)' : st === 'done' ? 'var(--success)' : st === 'error' ? 'var(--danger)' : 'var(--ink-4)',
                    }}
                  >
                    {STATE_ICON[st]} {substepTitle(sub)}
                  </span>
                );
              })}
            </div>
          );
        })}
      </div>

      {/* Detail message */}
      {session.lastDetail ? (
        <div style={{ marginTop: '10px', fontSize: '12px', color: 'var(--ink-3)', borderTop: '1px solid var(--line)', paddingTop: '8px' }}>
          {session.lastDetail}
        </div>
      ) : null}
    </div>
  );
}
