import { effect } from '@preact/signals';
import { workspacePath, fileTreeEntries, recentlyChangedFiles, type WorkspaceEntry } from '../state';

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = window.__TAURI__;
  const invoke = tauri?.core?.invoke || tauri?.invoke;
  if (!invoke) throw new Error('Tauri runtime not available');
  return invoke<T>(cmd, args);
}

export async function refreshFileTree(): Promise<void> {
  try {
    const entries = await tauriInvoke<WorkspaceEntry[]>('list_workspace_dir', { relative_path: '.' });
    fileTreeEntries.value = Array.isArray(entries) ? entries : [];
  } catch (e) {
    // No workspace set, permission error, etc -- keep tree empty.
    fileTreeEntries.value = [];
  }
}

interface WorkspaceChangePayload {
  paths?: string[];
  changed?: string[];
  files?: string[];
}

function flashRecentlyChanged(paths: string[]): void {
  if (!paths || !paths.length) return;
  const next = new Set(recentlyChangedFiles.value);
  for (const p of paths) next.add(p);
  recentlyChangedFiles.value = next;
  // Decay the highlight after 6 seconds.
  setTimeout(() => {
    const after = new Set(recentlyChangedFiles.value);
    for (const p of paths) after.delete(p);
    recentlyChangedFiles.value = after;
  }, 6000);
}

function subscribe(): void {
  const tauri = window.__TAURI__ as unknown as {
    event?: { listen?: (event: string, cb: (e: { payload: WorkspaceChangePayload }) => void) => Promise<() => void> };
  } | undefined;
  const listen = tauri?.event?.listen;
  if (typeof listen !== 'function') return;
  listen('workspace:changed', (e) => {
    const paths = e.payload?.paths || e.payload?.changed || e.payload?.files || [];
    flashRecentlyChanged(paths);
    refreshFileTree().catch(() => {});
  }).catch(() => {});
}

// Refresh whenever workspacePath becomes non-empty (i.e. after onboarding or
// the user picks a project folder in Settings -> Workspace).
effect(() => {
  const ws = workspacePath.value;
  if (ws) {
    refreshFileTree().catch(() => {});
  } else {
    fileTreeEntries.value = [];
  }
});

subscribe();
