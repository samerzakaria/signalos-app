import {
  chatBubbles,
  govGatesList,
  workspacePath,
  userName,
  userRole,
  type ChatBubble,
  type Gate,
} from '../state';
import { refreshProtocolContext } from './protocolContext';
import { activeProjectId } from './projectPicker';
import { gates, signal } from '../js/ipc';

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = window.__TAURI__;
  const invoke = tauri?.core?.invoke || tauri?.invoke;
  if (!invoke) throw new Error('Tauri runtime not available');
  return invoke<T>(cmd, args);
}

export interface InitWorkspaceOptions {
  name?: string;
  profile?: string;
  strict?: boolean;
}

export interface CreateSignalosProjectResult {
  governance: {
    filled: string[];
    unchanged: string[];
    failed: GovernanceDocumentFailure[];
    signed: boolean;
    awaitingApproval: boolean;
    gateStateVerified: boolean;
  };
  status: unknown | null;
}

export interface GovernanceDocumentFailure {
  path: string;
  error: string;
}

export interface GovernanceInstantiationResult {
  filled: string[];
  unchanged: string[];
  failed: GovernanceDocumentFailure[];
}

export interface Gate0ApprovalOptions {
  via?: 'button' | 'chat';
  /** Exact, fail-closed consent token understood by the backend transaction. */
  consent?: typeof GATE0_CONSENT_TOKEN;
  /** Binds a rendered approval card to the workspace that produced it. */
  expectedWorkspace?: string;
  /** Binds the approval to the project namespace rendered on the card. */
  expectedProjectId?: string;
}

export interface Gate0ApprovalResult {
  signed: boolean;
  reason?: string;
  alreadySigned?: boolean;
  coalesced?: boolean;
  gates: Gate[];
}

export const GATE0_CONSENT_TOKEN = 'I approve Gate 0 as sole founder' as const;
const approvalInFlight = new Map<string, Promise<Gate0ApprovalResult>>();

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  return String(error);
}

export function canonicalGateId(value: Gate | string | number | null | undefined): string | null {
  const raw = typeof value === 'object' && value !== null
    ? (value.gate_id ?? value.id)
    : value;
  if (raw === null || raw === undefined) return null;
  const text = String(raw).trim().toUpperCase();
  if (/^G\d+$/.test(text)) return `G${Number(text.slice(1))}`;
  if (/^\d+$/.test(text)) return `G${Number(text)}`;
  return null;
}

export function isGateSigned(gate: Gate | null | undefined): boolean {
  return Boolean(gate && (gate.signed === true || String(gate.status || '').toLowerCase() === 'signed'));
}

export function findGate0(gateList: Gate[]): Gate | undefined {
  return gateList.find((gate) => canonicalGateId(gate) === 'G0');
}

export function isGate0AwaitingApproval(gateList: Gate[]): boolean {
  const gate = findGate0(gateList);
  if (!gate || isGateSigned(gate)) return false;
  const status = String(gate.status || '').toLowerCase();
  return status === 'current' || status === 'active' || gate.is_current === true;
}

function gateListFrom(value: unknown): Gate[] {
  if (Array.isArray(value)) return value as Gate[];
  if (value && typeof value === 'object' && Array.isArray((value as { gates?: unknown }).gates)) {
    return (value as { gates: Gate[] }).gates;
  }
  return [];
}

function approvalPromptText(failures: GovernanceDocumentFailure[] = []): string {
  const fillWarning = failures.length > 0
    ? `\n\n${failures.length} governance document${failures.length === 1 ? '' : 's'} could not be prepared. Review the project files and fix that before approval; strict verification will keep G0 open until every required artifact is valid.`
    : '';
  return `Gate 0 is open for your governance review. Approving records an audited sole-founder authority contract: you approve in your real PO role and explicitly assume the PE sign-off for this setup gate. Review the Soul, Constitution, Surface Inventory, and Permanently-T3 documents first.${fillWarning}\n\nClick “Approve G0 as sole founder”, or type exactly: “I approve Gate 0 as sole founder”.`;
}

/** Keep exactly one workspace-bound approval card in sync with backend gate truth. */
export function reconcileGate0ApprovalAffordance(
  gateList: Gate[] = govGatesList.value,
  options: {
    workspace?: string;
    projectId?: string;
    documentFailures?: GovernanceDocumentFailure[];
  } = {},
): void {
  const ws = String(options.workspace ?? workspacePath.value ?? '').trim();
  const projectId = String(options.projectId ?? activeProjectId.value ?? '').trim();
  if (!ws) return;
  if (!projectId) return;
  const awaiting = isGate0AwaitingApproval(gateList);
  const signed = isGateSigned(findGate0(gateList));
  const bubbles = chatBubbles.value.map((bubble) => {
    if (
      bubble.waveAction === 'gate-approval'
      && (
        (bubble.approvalWorkspace && bubble.approvalWorkspace !== ws)
        || (bubble.approvalProjectId && bubble.approvalProjectId !== projectId)
      )
      && !bubble.waveResolved
    ) {
      return {
        ...bubble,
        waveResolved: {
          choice: 'workspace-changed',
          followupText: 'This Gate 0 approval belongs to another workspace or project and is no longer actionable.',
        },
      };
    }
    return bubble;
  });
  const matchesWorkspace = (bubble: ChatBubble) =>
    bubble.waveAction === 'gate-approval'
    && (!bubble.approvalWorkspace || bubble.approvalWorkspace === ws)
    && (!bubble.approvalProjectId || bubble.approvalProjectId === projectId);
  const matching = bubbles.filter(matchesWorkspace);

  if (signed) {
    chatBubbles.value = bubbles.map((bubble) => matchesWorkspace(bubble)
      ? {
          ...bubble,
          approvalWorkspace: ws,
          approvalProjectId: projectId,
          waveResolved: bubble.waveResolved || {
            choice: 'approve',
            followupText: 'Gate 0 is strictly signed and verified by the backend.',
          },
        }
      : bubble);
    return;
  }

  if (!awaiting) {
    // A stale card must never remain actionable when backend truth says G0 is
    // locked, missing, or otherwise not the current approval checkpoint.
    chatBubbles.value = bubbles.map((bubble) => matchesWorkspace(bubble) && !bubble.waveResolved
      ? {
          ...bubble,
          approvalWorkspace: ws,
          approvalProjectId: projectId,
          waveResolved: {
            choice: 'unavailable',
            followupText: 'Gate 0 is not currently available for approval.',
          },
        }
      : bubble);
    return;
  }

  const first = matching[0];
  const card: ChatBubble = {
    ...(first || {}),
    id: first?.id || `gate0-approval:${encodeURIComponent(ws)}:${encodeURIComponent(projectId)}`,
    kind: 'system',
    gate: 'G0',
    waveAction: 'gate-approval',
    approvalWorkspace: ws,
    approvalProjectId: projectId,
    text: first?.approvalWorkspace === ws
      && first?.approvalProjectId === projectId
      && options.documentFailures === undefined
      ? first.text
      : approvalPromptText(options.documentFailures),
  };
  delete card.waveResolved;
  let inserted = false;
  chatBubbles.value = bubbles.flatMap((bubble) => {
    if (!matchesWorkspace(bubble)) return [bubble];
    if (inserted) return [];
    inserted = true;
    return [card];
  });
  if (!inserted) chatBubbles.value = [...chatBubbles.value, card];
}

export async function refreshGovernanceGates(
  options: { reconcileApproval?: boolean; documentFailures?: GovernanceDocumentFailure[] } = {},
): Promise<Gate[]> {
  const gateList = gateListFrom(await gates.getAll());
  govGatesList.value = gateList;
  if (options.reconcileApproval !== false) {
    reconcileGate0ApprovalAffordance(gateList, { documentFailures: options.documentFailures });
  }
  return gateList;
}

export async function pickWorkspaceFolder(): Promise<void> {
  const tauri = window.__TAURI__;
  const dialog = tauri?.dialog;
  if (!dialog?.open) {
    const fallback = window.prompt('Project folder path');
    if (fallback) workspacePath.value = fallback;
    return;
  }
  const result = await dialog.open({
    directory: true,
    multiple: false,
    title: 'Choose project folder',
  });
  const path = Array.isArray(result) ? result[0] : result;
  if (path && typeof path === 'string') {
    workspacePath.value = path;
  }
}

export async function ensureWorkspaceFolder(path: string): Promise<void> {
  const target = path.trim();
  if (!target) throw new Error('Folder path is required');

  const tauri = window.__TAURI__;
  const fsApi = tauri?.fs;
  if (fsApi?.mkdir) {
    await fsApi.mkdir(target, { recursive: true });
    return;
  }

  try {
    await tauriInvoke('plugin:fs|mkdir', {
      path: target,
      options: { recursive: true },
    });
  } catch (e) {
    // Older shells may not expose the plugin through global Tauri. In that
    // case set_workspace below remains the source of truth for existing dirs.
    console.warn('workspace folder creation not available through Tauri fs:', e);
  }
}

export async function initWorkspace(path: string, options: InitWorkspaceOptions = {}): Promise<void> {
  await tauriInvoke('set_workspace', { path });
  workspacePath.value = path;

  // Run signalos init --mode keep to scaffold .signalos/ non-destructively.
  // Existing onboarding keeps this best-effort; new project creation passes
  // strict=true so setup failures stay visible to the user.
  const args = ['--mode', 'keep'];
  const name = options.name?.trim();
  if (name) args.push('--name', name);
  const profile = options.profile?.trim();
  if (profile) args.push('--profile', profile);

  try {
    // FIX 1 (Claim 3a): AWAIT real sidecar completion. `run_signal_command`
    // is fire-and-forget -- the Rust side returns a request id at ENQUEUE, so
    // the old code reported the workspace "initialized" before the sidecar had
    // done anything, and a sidecar-side failure was never surfaced. Use the
    // awaited transport the chat path already uses (ipc.signal.runAndWait):
    // it resolves only when the engine has actually finished init and rejects
    // if it failed, so strict callers see the real error.
    await signal.runAndWait('signal-init', args, 120000);
  } catch (e) {
    console.warn('signal-init failed:', e);
    if (options.strict) throw e;
  }
}

export async function createSignalosProject(
  path: string,
  name: string,
  profile = 'generic',
): Promise<CreateSignalosProjectResult> {
  const target = path.trim();
  const productName = name.trim();
  if (!productName || !target) {
    throw new Error('Name and folder path are required');
  }

  await ensureWorkspaceFolder(target);
  await initWorkspace(target, { name: productName, profile, strict: true });
  await tauriInvoke('set_identity', {
    name: (userName.value || 'User').trim() || 'User',
    role: userRole.value || 'PO',
  });
  // C1: fill the governance docs but do NOT auto-sign G0. Signing G0 is the
  // founder's explicit act -- a real review of Soul / Constitution /
  // Decision-DNA, done by clicking Approve or affirming in chat, never a silent
  // rubber-stamp at scaffold time. The New Project flow surfaces G0 as awaiting
  // the founder's approval.
  const filledDocs = await instantiateGovernance();
  let gateStateVerified = false;
  let signed = false;
  let awaitingApproval = true;
  try {
    const gateList = await refreshGovernanceGates({ reconcileApproval: false });
    const gate0 = findGate0(gateList);
    if (gate0) {
      gateStateVerified = true;
      signed = isGateSigned(gate0);
      awaitingApproval = isGate0AwaitingApproval(gateList);
    }
  } catch (error) {
    // Project creation succeeded, but do not pretend the gate projection was
    // verified. The next boot/build load retries and reconstructs the card.
    console.warn('could not verify Gate 0 after project creation:', error);
  }
  const governance = {
    ...filledDocs,
    signed,
    awaitingApproval,
    gateStateVerified,
  };
  const status = await tauriInvoke('get_workspace_status').catch(() => null);

  return { governance, status };
}

function projectNameFrom(path: string): string {
  if (!path) return 'My Project';
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
  return parts[parts.length - 1] || 'My Project';
}

async function readWorkspaceFile(relPath: string): Promise<string> {
  return tauriInvoke<string>('read_workspace_file', { relative_path: relPath });
}

async function writeWorkspaceFile(relPath: string, content: string): Promise<void> {
  await tauriInvoke('write_workspace_files', {
    files: [{ path: relPath, content }],
    overwrite: true,
  });
}

interface Substitutions {
  projectName: string;
  date: string;
  user: string;
  role: string;
}

function applySubstitutions(template: string, sub: Substitutions): string {
  // Soul / Constitution / Decision DNA templates use bare-brace placeholders
  // like {Product Name}. Fill the common ones; leave unknown placeholders as-is
  // so the user can hand-edit them when they want to.
  return template
    .replace(/\{Product Name\}/g, sub.projectName)
    .replace(/\{PRODUCT NAME\}/g, sub.projectName)
    .replace(/\{Project Name\}/gi, sub.projectName)
    .replace(/\[DATE\]/g, sub.date)
    .replace(/\{date\}/g, sub.date)
    .replace(/\{Date\}/g, sub.date)
    .replace(/\{PO\}/g, sub.user)
    .replace(/\{Owner\}/gi, sub.user)
    .replace(/\{role\}/gi, sub.role);
}

/**
 * Fill placeholders in Governance/SOUL-DOCUMENT.md, CONSTITUTION.md, and
 * DECISION-DNA.md with the user's project name + identity. Idempotent: a doc
 * with no placeholders left is skipped.
 *
 * C1: this deliberately does NOT sign Gate 0. Signing G0 asserts the founder
 * has reviewed the governance agreement (Soul / Constitution / Decision-DNA);
 * auto-signing it at scaffold time signs as the founder without a real review.
 * The founder approves G0 explicitly via {@link approveGate0} -- by clicking
 * Approve or affirming in chat.
 */
export async function instantiateGovernance(): Promise<GovernanceInstantiationResult> {
  const ws = workspacePath.value;
  if (!ws) return {
    filled: [],
    unchanged: [],
    failed: [{ path: '(workspace)', error: 'No workspace is selected.' }],
  };

  const sub: Substitutions = {
    projectName: projectNameFrom(ws),
    date: new Date().toISOString().slice(0, 10),
    user: (userName.value || 'User').trim(),
    role: userRole.value || 'PO',
  };

  const targets = [
    'core/governance/Governance/SOUL-DOCUMENT.md',
    'core/governance/Governance/CONSTITUTION.md',
    'core/governance/Governance/DECISION-DNA.md',
  ];

  const filled: string[] = [];
  const unchanged: string[] = [];
  const failed: GovernanceDocumentFailure[] = [];
  for (const rel of targets) {
    try {
      const original = await readWorkspaceFile(rel);
      // A document with no canonical placeholders is already prepared; report
      // that honestly instead of treating a failed read and an unchanged file
      // as the same silent no-op.
      const placeholderHits = original.match(/\{Product Name\}|\[DATE\]|\{PO\}/g);
      if (!placeholderHits || placeholderHits.length === 0) {
        unchanged.push(rel);
        continue;
      }
      const next = applySubstitutions(original, sub);
      if (next === original) {
        unchanged.push(rel);
        continue;
      }
      await writeWorkspaceFile(rel, next);
      filled.push(rel);
    } catch (error) {
      failed.push({ path: rel, error: errorMessage(error) });
    }
  }

  // Refresh the cached protocol context so the next chat message sees the
  // filled-in soul / constitution.
  try {
    await refreshProtocolContext();
  } catch (error) {
    console.warn('could not refresh governance context:', error);
  }

  return { filled, unchanged, failed };
}

/**
 * Sign Gate 0 -- the setup checkpoint -- as the founder's EXPLICIT approval of
 * the governance agreement. Called only from a real human action: the New
 * Project approval button, or the exact sole-founder consent sentence in chat.
 * Never auto-invoked at scaffold time.
 *
 * The frontend never selects or injects signing roles. One awaited
 * `gate0:approve` backend transaction authenticates the workspace identity,
 * records the authority contract, signs idempotently, strictly validates G0,
 * and returns the fresh gate projection. This function accepts success only
 * when both the transaction verdict and returned strict projection agree.
 *
 * @param opts.via provenance of the approval ("button" | "chat"), for logging.
 */
async function performGate0Approval(
  ws: string,
  opts: Gate0ApprovalOptions,
): Promise<Gate0ApprovalResult> {
  let gateList: Gate[] = [];
  const projectId = String(activeProjectId.value || '').trim();
  if (opts.expectedWorkspace && opts.expectedWorkspace !== ws) {
    return { signed: false, reason: 'This approval belongs to a different workspace.', gates: gateList };
  }
  if (!projectId || opts.expectedProjectId !== projectId) {
    return { signed: false, reason: 'This approval belongs to a different project.', gates: gateList };
  }
  if (opts.consent !== GATE0_CONSENT_TOKEN) {
    return {
      signed: false,
      reason: 'Gate 0 requires the exact sole-founder authority consent statement.',
      gates: gateList,
    };
  }

  try {
    const raw = await signal.runAndWait(
      'gate0:approve',
      [JSON.stringify({
        consent: GATE0_CONSENT_TOKEN,
        via: opts.via || 'button',
        expected_workspace: ws,
        expected_project_id: projectId,
      })],
      60000,
    );
    const response = raw && typeof raw === 'object'
      ? raw as {
          signed?: unknown;
          already_signed?: unknown;
          alreadySigned?: unknown;
          reason?: unknown;
          gates?: unknown;
        }
      : {};
    gateList = gateListFrom(response.gates);

    // The backend transaction is bound to the captured workspace/project, but
    // the user can switch context while that awaited transaction is running.
    // Never project the old project's gates into the newly active view (or
    // append a misleading success there). Reopening the original project will
    // fetch its durable signed state from the backend.
    const stillCurrent = String(workspacePath.value || '').trim() === ws
      && String(activeProjectId.value || '').trim() === projectId;
    if (!stillCurrent) {
      return {
        signed: false,
        reason: 'Gate 0 approval finished for the previous workspace or project after you switched. The current view was not changed; reopen that project to verify its gate state.',
        gates: [],
      };
    }
    govGatesList.value = gateList;
    reconcileGate0ApprovalAffordance(gateList, { projectId });

    const strictGate0Signed = isGateSigned(findGate0(gateList));
    if (response.signed === true && strictGate0Signed) {
      return {
        signed: true,
        alreadySigned: response.already_signed === true || response.alreadySigned === true,
        gates: gateList,
      };
    }
    return {
      signed: false,
      reason: String(
        response.reason
        || (response.signed === true
          ? 'The approval response did not contain a fresh strictly signed Gate 0 state.'
          : 'The backend refused Gate 0 approval.'),
      ),
      gates: gateList,
    };
  } catch (error) {
    return {
      signed: false,
      reason: `Gate 0 approval failed: ${errorMessage(error)}`,
      gates: gateList,
    };
  }
}

export async function approveGate0(
  opts: Gate0ApprovalOptions = {},
): Promise<Gate0ApprovalResult> {
  const ws = String(workspacePath.value || '').trim();
  if (!ws) return { signed: false, reason: 'No workspace is selected.', gates: [] };
  // Validate every caller before it may join an existing transaction. A
  // concurrent generic call must not inherit another action's valid consent.
  if (opts.expectedWorkspace && opts.expectedWorkspace !== ws) {
    return { signed: false, reason: 'This approval belongs to a different workspace.', gates: [] };
  }
  const projectId = String(activeProjectId.value || '').trim();
  if (!projectId || opts.expectedProjectId !== projectId) {
    return { signed: false, reason: 'This approval belongs to a different project.', gates: [] };
  }
  if (opts.consent !== GATE0_CONSENT_TOKEN) {
    return {
      signed: false,
      reason: 'Gate 0 requires the exact sole-founder authority consent statement.',
      gates: [],
    };
  }
  const approvalKey = `${ws}\u0000${projectId}`;
  const active = approvalInFlight.get(approvalKey);
  if (active) {
    const result = await active;
    return { ...result, coalesced: true };
  }
  const operation = performGate0Approval(ws, opts);
  approvalInFlight.set(approvalKey, operation);
  try {
    return await operation;
  } finally {
    if (approvalInFlight.get(approvalKey) === operation) approvalInFlight.delete(approvalKey);
  }
}

window.pickWorkspaceFolder = pickWorkspaceFolder;
window.ensureWorkspaceFolder = ensureWorkspaceFolder;
window.initWorkspace = initWorkspace;
window.createSignalosProject = createSignalosProject;
window.instantiateGovernance = instantiateGovernance;
window.approveGate0 = approveGate0;
