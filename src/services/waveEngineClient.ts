// waveEngineClient.ts — typed wrappers around the wave:* IPC handlers.
//
// Per WAVE-ENGINE-DESIGN §3.1 + §5 + §8. Each method maps to one IPC
// command in signalos_ipc_server.py. The chat layer uses this to:
//   - call wave:begin before a user message hits the LLM stream
//   - render system bubbles ahead of the LLM reply (re-route, sign-
//     recorded, scope-drift, complete)
//   - resolve scope-drift / violation prompts via 4-way / 3-way choices
//
// All methods are best-effort: if the sidecar isn't running or the
// command times out, they reject with the IPC error. Callers should
// wrap in try/catch and fall back to "no engine context" so the chat
// flow keeps working when the engine is unavailable.

// @ts-expect-error — ../js/ipc.js is plain JS with no .d.ts; runtime shape
// is `{ signal: { runAndWait(command, args, timeoutMs) } }`.
import * as ipc from '../js/ipc.js';

export type GateId = 'G0' | 'G1' | 'G2' | 'G3' | 'G4' | 'G5';

export interface SystemBubble {
  kind: 'reroute' | 'sign-recorded' | 'scope-drift' | 'complete' | string;
  gate?: GateId | null;
  text: string;
  detail?: string;
}

export interface AgentInfo {
  gate: GateId;
  filename: string;
  path: string | null;
  exists: boolean;
  content: string;
}

export interface Inspection {
  project_id: string;
  gates: Record<GateId, boolean>;
  artifacts: Record<GateId, {
    path: string | null;
    exists: boolean;
    signed: boolean;
    snippet: string;
  }>;
  next_gate: GateId | null;
  all_signed: boolean;
}

export interface DriftVerdict {
  drifted: boolean;
  confidence: number;
  method: 'no-soul' | 'heuristic' | 'llm-judged' | 'ambiguous';
  current_soul_summary: string;
  new_request_summary: string;
  signals: string[];
  recommended_action: 'keep' | 'amend' | 'new-project' | 'ambiguous';
}

export interface BeginResult {
  action: string;
  current_gate: GateId | null;
  inspection: Inspection;
  drift: DriftVerdict | null;
  agent: AgentInfo | null;
  system_bubble: SystemBubble;
}

export interface ReplyResult {
  action: string;
  current_gate?: GateId | null;
  signed_gate?: GateId;
  evidence?: string;
  system_bubble?: SystemBubble;
  auto_signed?: boolean;
  classification?: { kind: string; matched_phrase: string | null; raw: string };
  error?: string;
}

export interface TranslationResult {
  translation: {
    supported: boolean;
    format: string;
    text: string;
    install_hint?: string;
    error?: string;
    source_path?: string;
    source_url?: string;
    truncated?: boolean;
    page_count?: number;
    paragraph_count?: number;
    figma_file_key?: string | null;
    note?: string;
  };
  gate: GateId | null;
  system_bubble: SystemBubble;
}

export interface ViolationPromptResult {
  prompt: {
    category: string;
    violation_kind: string;
    gate: GateId | null;
    findings: string[];
    options: string[];
    text: string;
    prompt_id: string;
  };
  system_bubble: SystemBubble;
}

export interface ViolationConfirmResult {
  audit_entry?: {
    action: string;
    violation_kind: string;
    gate: GateId | null;
    choice: string;
    evidence: string;
    findings: string[];
  };
  system_bubble?: SystemBubble;
  action?: string;
  error?: string;
}

const DEFAULT_TIMEOUT_MS = 8000;

async function call<T>(command: string, args: unknown[]): Promise<T> {
  const result = await ipc.signal.runAndWait(command, args, DEFAULT_TIMEOUT_MS);
  return result as T;
}

/** WAVE-ENGINE-DESIGN §3.1 ENTRY→INSPECT→DECIDE→DISPATCH. Engine reconstructs from disk. */
export function begin(userRequest: string): Promise<BeginResult> {
  return call<BeginResult>('wave:begin', [userRequest]);
}

/** §8 auto-sign on affirmation; refine/question/ambiguous routed accordingly. */
export function reply(userReply: string, currentGate: GateId): Promise<ReplyResult> {
  return call<ReplyResult>('wave:reply', [userReply, currentGate]);
}

/** §6 4-way scope-drift resolution (a/b/c/d → amend/new-parallel/new-folder/keep). */
export function resolveScopeDrift(
  userRequest: string,
  choice: 'a' | 'b' | 'c' | 'd' | string,
): Promise<{ action: string; mode?: string; current_gate?: GateId | null }> {
  return call('wave:scope-drift-resolve', [userRequest, choice]);
}

/** §7 translator-mode for non-SignalOS artifacts (md/figma/pdf/docx/url). */
export function translateExternal(
  artifactPathOrUrl: string,
  gate?: GateId,
): Promise<TranslationResult> {
  const args: unknown[] = gate ? [artifactPathOrUrl, gate] : [artifactPathOrUrl];
  return call<TranslationResult>('wave:translate-external', args);
}

/** §8 3-way violation prompt builder (fix-now / defer / override-with-log). */
export function requestViolation(payload: {
  violation_kind: string;
  findings?: string[];
  gate?: GateId;
}): Promise<ViolationPromptResult> {
  return call<ViolationPromptResult>('wave:violation-request', [JSON.stringify(payload)]);
}

/** §8 record the user's violation choice (writes to AUDIT_TRAIL.jsonl). */
export function confirmViolation(payload: {
  violation_kind: string;
  choice: 'a' | 'b' | 'c' | 'fix-now' | 'defer' | 'override-with-log';
  user_reply: string;
  gate?: GateId;
  findings?: string[];
}): Promise<ViolationConfirmResult> {
  return call<ViolationConfirmResult>('wave:violation-confirm', [JSON.stringify(payload)]);
}

/** §2 G5 ship-gate handoff to M4 auto-commit. */
export function g5Handoff(
  waveId: string,
  summary: Record<string, unknown> = {},
): Promise<{
  commit_outcome: { status: string; reason?: string; files_count?: number; message?: string };
  system_bubble: SystemBubble;
}> {
  return call('wave:g5-handoff', [waveId, JSON.stringify(summary)]);
}

/**
 * Best-effort begin() that swallows IPC errors. Used by the chat layer
 * to surface a system bubble before the LLM stream without blocking
 * the user's flow when the sidecar is down.
 *
 * Returns null on any failure (sidecar unavailable, timeout, malformed
 * response). Callers should fall back to plain LLM stream.
 */
export async function tryBegin(userRequest: string): Promise<BeginResult | null> {
  try {
    return await begin(userRequest);
  } catch (err) {
    if (typeof console !== 'undefined' && console.warn) {
      console.warn('[waveEngine] tryBegin failed:', err);
    }
    return null;
  }
}
