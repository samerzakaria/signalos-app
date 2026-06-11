// macroProgress.ts — condense the Dashboard's macro state into a one-line
// summary so it can ride along inside the Build view. This lets a user keep
// chatting/executing while still seeing gate progress and release readiness,
// instead of toggling to the Dashboard tab for the big picture.
//
// Pure and unit-tested; the BuildView strip renders this.

import type { Gate, GateInfo, ReleaseReadinessResult } from '../state';

export type ReadinessLabel = 'Ready' | 'Not ready' | 'Unknown';

export interface MacroSummary {
  signed: number;
  total: number;
  currentGate: string;
  currentTitle: string;
  readinessLabel: ReadinessLabel;
  readinessPass: number;
  readinessTotal: number;
}

function gateIsSigned(g: Gate): boolean {
  return g.status === 'signed' || g.signed === true;
}

function isPass(status: string | undefined): boolean {
  const s = (status || '').toLowerCase();
  return s === 'pass' || s === 'ok' || s === 'passed';
}

export function summarizeMacro(
  gates: Gate[],
  gateInfo: GateInfo | null,
  readiness: ReleaseReadinessResult | null,
): MacroSummary {
  const list = Array.isArray(gates) ? gates : [];
  const total = list.length;
  const signed = list.filter(gateIsSigned).length;

  let currentGate = '';
  let currentTitle = '';
  const cur = gateInfo ?? list.find((g) => g.is_current) ?? null;
  if (cur) {
    currentGate = cur.id != null ? String(cur.id) : '';
    currentTitle = cur.name ?? '';
  }

  let readinessLabel: ReadinessLabel = 'Unknown';
  let readinessPass = 0;
  let readinessTotal = 0;
  if (readiness) {
    const checks = readiness.checks ?? [];
    readinessTotal = checks.length;
    readinessPass = checks.filter((c) => isPass(c.status)).length;
    const hasBlockers = !!(readiness.blockers && readiness.blockers.length);
    if (readiness.ok === true || readiness.pass === true) readinessLabel = 'Ready';
    else if (readiness.ok === false || readiness.pass === false || hasBlockers) readinessLabel = 'Not ready';
  }

  return { signed, total, currentGate, currentTitle, readinessLabel, readinessPass, readinessTotal };
}

/** One-line label, e.g. "2/6 gates · G3 Design · Release: Not ready (3/5)". */
export function macroLine(s: MacroSummary): string {
  const parts: string[] = [];
  if (s.total) parts.push(`${s.signed}/${s.total} gates signed`);
  if (s.currentGate || s.currentTitle) {
    parts.push(`${s.currentGate}${s.currentTitle ? ' ' + s.currentTitle : ''}`.trim());
  }
  if (s.readinessLabel !== 'Unknown') {
    const counts = s.readinessTotal ? ` (${s.readinessPass}/${s.readinessTotal})` : '';
    parts.push(`Release: ${s.readinessLabel}${counts}`);
  }
  return parts.join(' · ');
}
