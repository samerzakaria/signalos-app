// UxFrictionCard.tsx — the UX-friction gate review surface (#12).
//
// Renders the 5-persona friction report emitted by the Python gate
// orchestrator ({"type":"ux_friction", "gate", "findings"} — see
// gate_orchestrator._emit_preview + ux_friction.heuristic_findings). Each
// persona lists its findings with a plain-language severity (high / medium /
// low). The card is purely informational — no verdict buttons — and appears
// before/alongside the design-gate GateReviewCard so the human signing the
// gate sees the friction findings first.
//
// State via @preact/signals (useSignal) per this codebase's convention —
// see MEMORY.md / TestDebtPanel.tsx.

import { useSignal } from '@preact/signals';
import type { UxFrictionPersona } from '../state';

export interface UxFrictionCardProps {
  /** Gate code the report was generated for, e.g. "design" / "G3". */
  gate: string;
  /** One entry per persona, each with its (possibly empty) findings. */
  personas: UxFrictionPersona[];
}

/** The card starts collapsed when the report is long (many findings). */
const COLLAPSE_THRESHOLD = 5;

const SEVERITY_STYLES: Record<string, { background: string; color: string }> = {
  high: { background: 'var(--danger-soft)', color: 'var(--danger-deep)' },
  medium: { background: 'var(--amber-soft)', color: 'var(--amber-deep)' },
  low: { background: 'var(--surface-warm)', color: 'var(--ink-2)' },
};

const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

function severityStyle(severity: string) {
  return SEVERITY_STYLES[severity] || SEVERITY_STYLES.medium;
}

const PERSONA_ICONS: Record<string, string> = {
  impatient: 'ti-clock-bolt',
  colorblind: 'ti-color-swatch',
  first_time: 'ti-user-plus',
  mobile: 'ti-device-mobile',
  keyboard: 'ti-keyboard',
};

export function UxFrictionCard({ gate, personas }: UxFrictionCardProps) {
  const totalFindings = personas.reduce((n, p) => n + p.findings.length, 0);
  const highCount = personas.reduce(
    (n, p) => n + p.findings.filter((f) => f.severity === 'high').length,
    0,
  );
  const collapsed = useSignal(totalFindings > COLLAPSE_THRESHOLD);

  const summary = totalFindings === 0
    ? 'No friction found across the personas.'
    : `${totalFindings} finding${totalFindings === 1 ? '' : 's'}${highCount > 0 ? ` · ${highCount} high` : ''} across ${personas.length} persona${personas.length === 1 ? '' : 's'}`;

  return (
    <div className="msg spark" data-testid="ux-friction-card">
      <div className="msg-av"><i className="ti ti-user-search" style={{ fontSize: '17px' }}></i></div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="card gate-review" data-gate={gate}>
          <div className="gate-head" style={{ marginBottom: '10px' }}>
            <div className="gate-ic"><i className="ti ti-accessible"></i></div>
            <div className="gate-tx">
              <h3 style={{ margin: 0 }}>UX friction report</h3>
              <p style={{ margin: '2px 0 0', fontSize: '12.5px' }} data-testid="ux-friction-summary">
                {summary}
              </p>
            </div>
            <button
              type="button"
              className="btn btn-soft"
              style={{ fontSize: '11px', padding: '4px 10px', flexShrink: 0 }}
              data-testid="ux-friction-toggle"
              aria-expanded={!collapsed.value}
              onClick={() => { collapsed.value = !collapsed.value; }}
            >
              <i className={`ti ${collapsed.value ? 'ti-chevron-down' : 'ti-chevron-up'}`}></i>{' '}
              {collapsed.value ? 'Show details' : 'Hide details'}
            </button>
          </div>

          {!collapsed.value ? (
            <div data-testid="ux-friction-body" style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {personas.map((p) => (
                <div key={p.persona || p.label} data-testid={`ux-friction-persona-${p.persona || 'unknown'}`}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12.5px', fontWeight: 600, marginBottom: '4px' }}>
                    <i className={`ti ${PERSONA_ICONS[p.persona] || 'ti-user'}`} style={{ color: 'var(--ink-2)' }}></i>
                    <span>{p.label}</span>
                    {p.findings.length === 0 ? (
                      <span style={{ fontSize: '11px', fontWeight: 400, color: 'var(--success-deep)' }}>
                        <i className="ti ti-check" style={{ verticalAlign: 'middle' }}></i> no friction
                      </span>
                    ) : null}
                  </div>
                  {p.findings.length > 0 ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      {[...p.findings]
                        .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 1) - (SEVERITY_ORDER[b.severity] ?? 1))
                        .map((f, i) => {
                          const sev = severityStyle(f.severity);
                          return (
                            <div
                              key={i}
                              data-testid="ux-friction-finding"
                              style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', padding: '7px 10px', background: 'var(--surface-warm)', borderRadius: 'var(--r-sm)' }}
                            >
                              <span
                                data-testid="ux-friction-severity"
                                data-severity={f.severity}
                                style={{
                                  flexShrink: 0,
                                  fontSize: '10px',
                                  fontWeight: f.severity === 'high' ? 700 : 600,
                                  textTransform: 'uppercase',
                                  letterSpacing: '0.04em',
                                  padding: '2px 7px',
                                  borderRadius: '99px',
                                  marginTop: '1px',
                                  ...sev,
                                }}
                              >
                                {f.severity}
                              </span>
                              <span style={{ fontSize: '12.5px', minWidth: 0 }}>
                                <span>{f.issue}</span>
                                {f.suggestion ? (
                                  <span style={{ display: 'block', fontSize: '11.5px', color: 'var(--ink-3)', marginTop: '2px' }}>
                                    {f.suggestion}
                                  </span>
                                ) : null}
                              </span>
                            </div>
                          );
                        })}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </div>
        <div className="msg-meta">Foundry · UX review</div>
      </div>
    </div>
  );
}
