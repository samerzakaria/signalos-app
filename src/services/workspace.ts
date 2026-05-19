import { workspacePath, userName, userRole } from '../state';
import { refreshProtocolContext } from './protocolContext';

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = window.__TAURI__;
  const invoke = tauri?.core?.invoke || tauri?.invoke;
  if (!invoke) throw new Error('Tauri runtime not available');
  return invoke<T>(cmd, args);
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

export async function initWorkspace(path: string): Promise<void> {
  await tauriInvoke('set_workspace', { path });
  // Run signalos init --mode keep to scaffold .signalos/ non-destructively.
  // Errors are logged but not rethrown -- a failed init shouldn't block boot.
  try {
    await tauriInvoke('run_signal_command', { command: 'signal-init', args: ['--mode', 'keep'] });
  } catch (e) {
    console.warn('signal-init failed:', e);
  }
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
 * DECISION-DNA.md with the user's project name + identity, then sign Gate 0
 * (setup gate) so the audit trail records the first checkpoint. Idempotent:
 * if a doc has no placeholders left we skip rewriting it.
 */
export async function instantiateGovernanceAndSignG0(): Promise<{
  filled: string[];
  signed: boolean;
}> {
  const ws = workspacePath.value;
  if (!ws) return { filled: [], signed: false };

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

  // Sign Gate 0 -- setup checkpoint. Best-effort; an audit failure shouldn't
  // block onboarding.
  let signed = false;
  try {
    await tauriInvoke('run_signal_command', {
      command: 'signal-sign',
      args: ['G0', '--signer', sub.user, '--role', sub.role, '--verdict', 'pass'],
    });
    signed = true;
  } catch (e) {
    console.warn('Gate 0 sign failed:', e);
  }

  return { filled, signed };
}

window.pickWorkspaceFolder = pickWorkspaceFolder;
window.instantiateGovernanceAndSignG0 = instantiateGovernanceAndSignG0;
