import type { Gate } from '../state';

export type GateUiState = 'signed' | 'current' | 'locked';

export function gateCode(gate: Gate, index: number): string {
  const raw = gate.gate_id ?? gate.id;
  if (typeof raw === 'string') {
    const trimmed = raw.trim();
    if (/^G\d+$/i.test(trimmed)) return trimmed.toUpperCase();
    if (/^\d+$/.test(trimmed)) return `G${trimmed}`;
  }
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return `G${raw}`;
  }
  return `G${index}`;
}

export function isGateSigned(gate: Gate): boolean {
  return gate.status === 'signed' || gate.signed === true;
}

export function isGateCurrent(gate: Gate): boolean {
  return gate.status === 'current' || gate.status === 'active' || gate.is_current === true;
}

export function gateUiState(gate: Gate): GateUiState {
  if (isGateSigned(gate)) return 'signed';
  if (isGateCurrent(gate)) return 'current';
  return 'locked';
}

export function gateStatusLabel(gate: Gate): string {
  const state = gateUiState(gate);
  if (state === 'signed') return 'Signed';
  if (state === 'current') return 'Current';
  return 'Locked';
}

export function activeGateIndex(gates: Gate[]): number {
  return gates.findIndex(isGateCurrent);
}

interface GateTimelineProps {
  gates: Gate[];
  testId?: string;
}

export function GateTimeline({ gates, testId = 'gate-timeline' }: GateTimelineProps) {
  return (
    <div className="stepper" data-testid={testId}>
      {gates.map((gate, index) => {
        const code = gateCode(gate, index);
        const state = gateUiState(gate);
        const cls = state === 'signed' ? 'scell done' : state === 'current' ? 'scell active' : 'scell';
        const marker = state === 'signed'
          ? <i className="ti ti-check"></i>
          : state === 'current'
            ? <>{code}</>
            : <i className="ti ti-lock"></i>;
        const isLast = index === gates.length - 1;
        return (
          <div
            className={cls}
            key={code}
            data-testid={`gate-timeline-${code}`}
            aria-current={state === 'current' ? 'step' : undefined}
          >
            <div className="scirc">{marker}</div>
            <div className="slbl">{gate.name || code}</div>
            <div className="sstatus">{gateStatusLabel(gate)}</div>
            {!isLast ? <div className="conn"></div> : null}
          </div>
        );
      })}
    </div>
  );
}
