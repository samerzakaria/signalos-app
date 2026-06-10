// costFraming.ts — translate raw LLM spend into terms a non-technical founder
// relates to. The precise dollar figure stays visible for transparency; these
// helpers add a plain-language framing on top.
//
// "Hours saved" is an ESTIMATE, not a measurement. It is derived from the
// volume of accepted work (files written in the wave) using a single, openly
// documented per-file figure, and is always labelled as an estimate in the UI.
// Honest-by-default: we never imply the number is exact.

// Conservative equivalent-human-effort per accepted file: writing, wiring, and
// reviewing a file of generated code. Deliberately on the low side so the
// estimate under-promises. Tune in one place.
export const MINUTES_PER_FILE = 25;

/** Estimated developer-hours saved for a wave, from its accepted file count. */
export function estimateHoursSaved(fileCount: number): number {
  if (!Number.isFinite(fileCount) || fileCount <= 0) return 0;
  return (fileCount * MINUTES_PER_FILE) / 60;
}

/** Human-friendly duration: "≈ 40 min", "≈ 1.5 hrs", "≈ 3 hrs". */
export function formatHoursSaved(hours: number): string {
  if (!Number.isFinite(hours) || hours <= 0) return '';
  if (hours < 1) {
    const mins = Math.round(hours * 60);
    return `≈ ${mins} min`;
  }
  // One decimal under 10 hours, whole numbers above.
  const rounded = hours < 10 ? Math.round(hours * 2) / 2 : Math.round(hours);
  const label = rounded === 1 ? 'hr' : 'hrs';
  return `≈ ${rounded} ${label}`;
}

/** Parse the optional monthly spend cap entered at onboarding. */
export function parseBudgetCapUsd(raw: string | null | undefined): number | null {
  if (raw == null) return null;
  const trimmed = String(raw).trim();
  if (!trimmed) return null;
  const value = Number(trimmed.replace(/[$,\s]/g, ''));
  if (!Number.isFinite(value) || value <= 0) return null;
  return value;
}

/** Format a USD amount: cents-precision under $1, two decimals otherwise. */
export function formatUsd(usd: number): string {
  if (!Number.isFinite(usd)) return '$0.00';
  if (usd > 0 && usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

export interface WaveValueFraming {
  /** Estimated dev time saved, e.g. "≈ 1.5 hrs" (empty when not estimable). */
  hoursSavedLabel: string;
  /** Cap context, e.g. "of your $50/mo cap" (empty when no cap set). */
  capLabel: string;
}

/** Build the founder-friendly framing shown beside a wave's raw spend. */
export function waveValueFraming(
  fileCount: number,
  budgetCapRaw: string | null | undefined,
): WaveValueFraming {
  const hoursSavedLabel = formatHoursSaved(estimateHoursSaved(fileCount));
  const cap = parseBudgetCapUsd(budgetCapRaw);
  const capLabel = cap !== null ? `of your ${formatUsd(cap)}/mo cap` : '';
  return { hoursSavedLabel, capLabel };
}
