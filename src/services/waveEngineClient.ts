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

import * as ipc from '../js/ipc.js';
import {
  currentGateId,
  currentGateInfo,
  currentWaveSummary,
  govGatesList,
  type Gate,
  type GateInfo,
} from '../state';

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
const GATE_SEQUENCE: GateId[] = ['G0', 'G1', 'G2', 'G3', 'G4', 'G5'];
const GATE_NAMES: Record<GateId, string> = {
  G0: 'Soul',
  G1: 'Belief',
  G2: 'Plan',
  G3: 'Design',
  G4: 'Build',
  G5: 'Quality',
};

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
    const result = await begin(userRequest);
    publishBeginResult(result);
    return result;
  } catch (err) {
    if (typeof console !== 'undefined' && console.warn) {
      console.warn('[waveEngine] tryBegin failed:', err);
    }
    return null;
  }
}

export function publishBeginResult(result: BeginResult): void {
  const current = result.current_gate || result.inspection?.next_gate || result.system_bubble?.gate || null;
  if (result.inspection) {
    const gates = GATE_SEQUENCE.map((gateId): Gate => {
      const artifact = result.inspection.artifacts?.[gateId];
      const signed = Boolean(result.inspection.gates?.[gateId]);
      return {
        id: gateId,
        gate_id: gateId,
        name: GATE_NAMES[gateId],
        status: signed ? 'signed' : current === gateId ? 'current' : 'locked',
        signed,
        is_current: current === gateId && !signed,
        criteria: artifact
          ? [{
              name: artifact.path || `${gateId} required artifact`,
              status: artifact.exists && artifact.signed ? 'passed' : 'waiting',
              evidence: artifact.path || undefined,
            }]
          : [],
      };
    });
    govGatesList.value = gates;
    const currentGate = gates.find((gate) => gate.is_current) || null;
    currentGateInfo.value = currentGate
      ? beginGateInfo(currentGate, result.system_bubble?.text)
      : null;
    currentGateId.value = currentGate?.id ? String(currentGate.id) : current;
    currentWaveSummary.value = {
      current_gate_name: currentGate?.name || '',
      total_gates: gates.length,
    };
    return;
  }

  if (current) {
    currentGateId.value = current;
    currentGateInfo.value = {
      id: current,
      name: GATE_NAMES[current],
      status: 'current',
      is_current: true,
      description: result.system_bubble?.text,
    };
  }
}

function beginGateInfo(gate: Gate, description?: string): GateInfo {
  return {
    id: gate.id,
    name: gate.name,
    status: gate.status,
    signed: gate.signed,
    is_current: gate.is_current,
    description,
    activities: gate.activities,
    criteria: gate.criteria,
  };
}
