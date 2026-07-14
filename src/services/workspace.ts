import { workspacePath, userName, userRole } from '../state';
import { refreshProtocolContext } from './protocolContext';
import { signal } from '../js/ipc';

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
    signed: boolean;
    // C1: G0 is not auto-signed at creation; it awaits the founder's explicit
    // approval (Approve button or a chat approval).
    awaitingApproval?: boolean;
  };
  status: unknown | null;
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
  const governance = { filled: filledDocs.filled, signed: false, awaitingApproval: true };
  const status = await tauriInvoke('get_workspace_status').catch(() => null);

  return { governance, status };
}

function projectNameFrom(path: string): string {
  if (!path) return 'My Project';
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
  return parts[parts.length - 1] || 'My Project';
}

async function readWorkspaceFile(relPath: string): Promise<string | null> {
  try {
    return await tauriInvoke<string>('read_workspace_file', { relative_path: relPath });
  } catch {
    return null;
  }
}

async function writeWorkspaceFile(relPath: string, content: string): Promise<boolean> {
  try {
    await tauriInvoke('write_workspace_files', {
      files: [{ path: relPath, content }],
      overwrite: true,
    });
    return true;
  } catch (e) {
    console.warn(`could not write ${relPath}:`, e);
    return false;
  }
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
export async function instantiateGovernance(): Promise<{ filled: string[] }> {
  const ws = workspacePath.value;
  if (!ws) return { filled: [] };

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
  for (const rel of targets) {
    const original = await readWorkspaceFile(rel);
    if (!original) continue;
    // Skip files that have already been hand-filled (no canonical placeholders left).
    const placeholderHits = original.match(/\{Product Name\}|\[DATE\]|\{PO\}/g);
    if (!placeholderHits || placeholderHits.length === 0) continue;
    const next = applySubstitutions(original, sub);
    if (next !== original) {
      const ok = await writeWorkspaceFile(rel, next);
      if (ok) filled.push(rel);
    }
  }

  // Refresh the cached protocol context so the next chat message sees the
  // filled-in soul / constitution.
  await refreshProtocolContext();

  return { filled };
}

/**
 * Sign Gate 0 -- the setup checkpoint -- as the founder's EXPLICIT approval of
 * the governance agreement. Called only from a real human action: the New
 * Project "Approve Gate 0" button, or an approval phrase in chat
 * ("approve" / "accepted" / "signed" / "I agree"). Never auto-invoked at
 * scaffold time (C1).
 *
 * AWAITs the real sidecar sign so `signed` reflects what the engine actually
 * did. G0's manifest spans two roles -- Soul + Constitution require PO + PE,
 * Surface Inventory + Permanently-T3 require PE -- and in the solo-founder
 * setup the founder holds both seats, so we sign once per required role and
 * report `signed` only when EVERY awaited sign resolves ok. Valid CLI verdicts
 * are APPROVED / APPROVED-WITH-CONDITIONS / WAIVED.
 *
 * @param opts.via provenance of the approval ("button" | "chat"), for logging.
 */
export async function approveGate0(
  opts: { via?: string } = {},
): Promise<{ signed: boolean }> {
  const ws = workspacePath.value;
  if (!ws) return { signed: false };
  const signer = (userName.value || 'User').trim() || 'User';
  let signed = false;
  try {
    for (const role of ['PO', 'PE']) {
      await signal.runAndWait(
        'signal-sign',
        ['G0', '--signer', signer, '--role', role, '--verdict', 'APPROVED'],
        60000,
      );
    }
    signed = true;
    if (opts.via) console.info(`Gate 0 approved by ${signer} (via ${opts.via}).`);
  } catch (e) {
    // Surface the real failure: `signed` stays false so the caller can tell the
    // founder the approval did not record.
    console.warn('Gate 0 sign failed:', e);
  }

  return { signed };
}

window.pickWorkspaceFolder = pickWorkspaceFolder;
window.ensureWorkspaceFolder = ensureWorkspaceFolder;
window.initWorkspace = initWorkspace;
window.createSignalosProject = createSignalosProject;
window.instantiateGovernance = instantiateGovernance;
window.approveGate0 = approveGate0;
