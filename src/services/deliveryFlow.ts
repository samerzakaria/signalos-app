// deliveryFlow.ts — Build conversation delivery flow state machine
// (Phase 1.1b of the Foundry v4 plan).
//
// This preserves the delivery wizard's phase model, labels, and helper logic
// as a reusable service. The Build conversation drives the Brief → Design →
// Build → Validate → Security → Launch → Handoff flow inline, so it consumes
// these definitions instead of duplicating them.
//
// Pure logic only — no Preact, no IPC, no DOM. Fully unit-testable.

/** The wizard steps the user moves through in the Build conversation. */
export type DeliveryStep = 'prompt' | 'intent' | 'design' | 'progress' | 'closeout';

/** A single progress event emitted by the sidecar during delivery. */
export interface DeliveryProgressEvent {
  id?: string;
  phase?: string;
  substep?: string;
  state?: 'running' | 'done' | 'error' | string;
  detail?: string | null;
  ts?: number;
}

/**
 * Canonical ordered phases of a delivery. These are the internal phase names;
 * `BUSINESS_STAGES` maps them to the user-facing stage strip the plan wants
 * (Brief → Design → Build → Validate → Security → Launch → Handoff).
 */
export const PHASES = [
  'Intent',
  'Scaffold',
  'Design',
  'Acceptance',
  'Generation',
  'Validation',
  'Security',
  'Proof',
  'Deploy',
  'Closeout',
] as const;

export type Phase = (typeof PHASES)[number];

/**
 * Maps the raw phase identifiers emitted by the sidecar (which include
 * past-tense variants like "scaffolded", "validated") to a canonical
 * display label.
 */
export const PHASE_LABELS: Record<string, string> = {
  intent: 'Intent',
  scaffold: 'Scaffold',
  scaffolded: 'Scaffold',
  design: 'Design',
  acceptance: 'Acceptance',
  generated: 'Generation',
  generation: 'Generation',
  validation: 'Validation',
  validated: 'Validation',
  security: 'Security',
  proof: 'Proof',
  proved: 'Proof',
  deploy: 'Deploy',
  closeout: 'Closeout',
  closed: 'Closeout',
};

/**
 * User-facing business stages per the plan's "Build Progress" section. Each
 * maps to one or more internal phases. The Build conversation renders this
 * strip, not the gate codes.
 */
export const BUSINESS_STAGES: Array<{ label: string; phases: Phase[] }> = [
  { label: 'Brief', phases: ['Intent'] },
  { label: 'Design', phases: ['Scaffold', 'Design'] },
  { label: 'Build', phases: ['Acceptance', 'Generation'] },
  { label: 'Validate', phases: ['Validation'] },
  { label: 'Security', phases: ['Security'] },
  { label: 'Launch', phases: ['Proof', 'Deploy'] },
  { label: 'Handoff', phases: ['Closeout'] },
];

/** Normalise a raw sidecar phase string to its canonical label, or null. */
export function phaseLabel(raw: string | null | undefined): string | null {
  if (!raw) return null;
  return PHASE_LABELS[raw.toLowerCase()] ?? null;
}

/** Completion percentage given how many phases are done. */
export function deliveryPercent(completedCount: number): number {
  return Math.min(100, Math.round((completedCount / PHASES.length) * 100));
}

/** Which business stage a given canonical phase belongs to (or null). */
export function businessStageForPhase(phase: string | null | undefined): string | null {
  const label = phaseLabel(phase) ?? (phase as Phase | undefined);
  if (!label) return null;
  const stage = BUSINESS_STAGES.find((s) => s.phases.includes(label as Phase));
  return stage ? stage.label : null;
}

// ── Product-name derivation ─────────────────────────────────────────────────

/** Sanitise an arbitrary string into a filesystem-safe product folder name. */
export function safeProductName(value: string): string {
  return (
    String(value || '')
      .trim()
      // eslint-disable-next-line no-control-regex
      .replace(/[<>:"/\\|?*\x00-\x1F]/g, '-')
      .replace(/[. ]+$/g, '')
      .replace(/\s+/g, '-') || 'NewProduct'
  );
}

const STOPWORDS_RE = /^(i|we|want|need|to|do|build|create|make|an?|the|for|my|our)$/i;

/**
 * Derive a product name from an explicit name (preferred) or, failing that,
 * the first few meaningful words of the prompt.
 */
export function deriveProductName(input: { name?: string; prompt?: string }): string {
  if (input.name && input.name.trim()) return safeProductName(input.name);
  const words = String(input.prompt || '')
    .replace(/[^a-zA-Z0-9\s-]/g, ' ')
    .trim()
    .split(/\s+/)
    .filter((word) => !STOPWORDS_RE.test(word))
    .slice(0, 3);
  return safeProductName(words.join('-') || 'NewProduct');
}

/**
 * Heuristic: does a clarifying question look technical (stack/framework/infra)?
 * Used to filter agent questions so the client only sees business-language
 * questions, per the plan's UX principles.
 */
const TECHNICAL_QUESTION_RE =
  /\b(api|backend|frontend|framework|library|stack|database|dbms|sql|postgres|mysql|sqlite|docker|kubernetes|deploy|deployment|cloud|vercel|netlify|fly|render|railway|react|vite|angular|vue|svelte|zustand|jotai|redux|tanstack|swr|mantine|shadcn|tailwind|graphql|websocket|rest)\b/i;

export function isTechnicalQuestion(text: string): boolean {
  return TECHNICAL_QUESTION_RE.test(String(text || ''));
}

/**
 * Fold an incoming progress event into the running list of completed phases.
 * Returns the new completed-phase set (deduped, in canonical order) plus the
 * current phase. Mirrors the reducer used by the guided delivery flow.
 */
export function applyProgressEvent(
  prev: { completedPhases: string[]; currentPhase: string | null },
  evt: DeliveryProgressEvent,
): { completedPhases: string[]; currentPhase: string | null } {
  const label = phaseLabel(evt.phase);
  if (!label) return prev;

  let completed = prev.completedPhases;
  let current = prev.currentPhase;

  if (evt.state === 'done') {
    if (!completed.includes(label)) {
      completed = [...completed, label];
    }
    // advance current to the next not-yet-completed phase
    const next = (PHASES as readonly string[]).find((p) => !completed.includes(p));
    current = next ?? null;
  } else if (evt.state === 'running') {
    current = label;
  }

  // keep completedPhases in canonical order
  completed = (PHASES as readonly string[]).filter((p) => completed.includes(p));

  return { completedPhases: completed, currentPhase: current };
}
