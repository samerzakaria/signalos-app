// shareExport.ts — the Share action (#18). Replaces the app-v2.js
// shareProject stub: calls the `share:export` IPC command; on success shows
// a toast (file-toast pattern, matching showFileWriteToast) with the export
// path and an "Open folder" action; on error shows the backend's message.
//
// Contract (backend in flight): "share:export" {} →
//   {status:"ok", path, files[]} | {status:"error", error}
// Coded defensively — missing fields are tolerated and an unknown command
// shows a graceful error toast.

import * as ipc from '../js/ipc.js';

export interface ShareExportOutcome {
  status: 'ok' | 'error';
  path?: string;
  files?: string[];
  error?: string;
}

const TOAST_ID = 'shareExportToast';

/** Open the export folder. Tries the workspace-scoped open-path IPC
 *  (open_workspace_path) first; falls back to the OS shell for absolute
 *  paths outside the workspace sandbox. */
export function openExportFolder(path: string): void {
  if (!path) return;
  Promise.resolve()
    .then(() => ipc.project.openPath(path))
    .catch(() => {
      try {
        return window.__TAURI__?.shell?.open?.(path);
      } catch {
        return undefined;
      }
    })
    .catch(() => { /* best-effort — nothing else to try */ });
}

function removeExistingToast(): void {
  const existing = document.getElementById(TOAST_ID);
  if (existing) existing.remove();
}

function showShareToast(outcome: ShareExportOutcome): void {
  if (typeof document === 'undefined') return;
  removeExistingToast();

  const toast = document.createElement('div');
  toast.className = 'file-toast';
  toast.id = TOAST_ID;
  toast.setAttribute('data-status', outcome.status);

  const icon = document.createElement('i');
  icon.className = outcome.status === 'ok'
    ? 'ti ti-share-3 file-toast-ic'
    : 'ti ti-alert-triangle file-toast-ic';
  toast.appendChild(icon);

  const tx = document.createElement('div');
  tx.className = 'file-toast-tx';
  if (outcome.status === 'ok') {
    const count = outcome.files?.length ?? 0;
    const title = document.createElement('strong');
    title.textContent = count > 0
      ? `Share package exported (${count} file${count === 1 ? '' : 's'})`
      : 'Share package exported';
    const pathLine = document.createElement('div');
    pathLine.className = 'share-toast-path';
    pathLine.style.fontSize = '11.5px';
    pathLine.textContent = outcome.path || '';
    tx.appendChild(title);
    tx.appendChild(pathLine);
    if (outcome.path) {
      const openBtn = document.createElement('button');
      openBtn.className = 'btn btn-soft';
      openBtn.id = 'shareExportOpenBtn';
      openBtn.style.cssText = 'font-size:11.5px;padding:4px 9px;margin-top:6px;';
      openBtn.innerHTML = '<i class="ti ti-folder-open"></i> Open folder';
      openBtn.addEventListener('click', () => {
        openExportFolder(outcome.path || '');
        toast.remove();
      });
      tx.appendChild(openBtn);
    }
  } else {
    const title = document.createElement('strong');
    title.textContent = 'Share export failed';
    const msg = document.createElement('div');
    msg.style.fontSize = '11.5px';
    msg.textContent = outcome.error || 'The engine could not build the share package.';
    tx.appendChild(title);
    tx.appendChild(msg);
  }
  toast.appendChild(tx);

  const close = document.createElement('div');
  close.className = 'file-toast-close';
  close.innerHTML = '<i class="ti ti-x"></i>';
  close.addEventListener('click', () => toast.remove());
  toast.appendChild(close);

  document.body.appendChild(toast);
  // Success auto-dismisses after 12s (long enough to click Open folder);
  // errors stay until dismissed.
  if (outcome.status === 'ok') {
    setTimeout(() => { if (toast.parentElement) toast.remove(); }, 12000);
  }
}

/**
 * Run the share export and surface the outcome as a toast. Never rejects —
 * app-v2.js binds this straight onto window.shareProject.
 */
export async function runShareExport(): Promise<ShareExportOutcome> {
  let outcome: ShareExportOutcome;
  try {
    const raw = await ipc.signal.runAndWait('share:export', [], 60000);
    const res = (raw && typeof raw === 'object' ? raw : {}) as {
      status?: string;
      path?: string;
      files?: unknown;
      error?: string;
    };
    if (res.status === 'ok' && typeof res.path === 'string' && res.path) {
      outcome = {
        status: 'ok',
        path: res.path,
        files: Array.isArray(res.files)
          ? (res.files as unknown[]).filter((f): f is string => typeof f === 'string')
          : [],
      };
    } else {
      outcome = {
        status: 'error',
        error: res.error || (res.status && res.status !== 'ok'
          ? `Share export failed (${res.status}).`
          : 'Share export returned no path.'),
      };
    }
  } catch (err) {
    outcome = {
      status: 'error',
      error: err instanceof Error ? err.message : String(err),
    };
  }
  showShareToast(outcome);
  return outcome;
}
