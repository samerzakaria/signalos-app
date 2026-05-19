import { previewUrl, previewStatus, previewKey, previewStack, workspacePath } from '../state';

interface PreviewEvent {
  key?: string;
  kind?: string; // "port" | "status" | "exit" | "error" | "stdout" | "stderr"
  message?: string;
}

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = window.__TAURI__;
  const invoke = tauri?.core?.invoke || tauri?.invoke;
  if (!invoke) throw new Error('Tauri runtime not available');
  return invoke<T>(cmd, args);
}

/**
 * Read .signalos/missing-deps.json (written by orchestrator's auto-deps
 * scan) and merge any entries into package.json's `dependencies` before
 * the preview's npm install runs. The LLM frequently emits code that
 * imports a package without remembering to add it; this catches it.
 *
 * Returns the list of deps we added so the caller can surface them.
 */
async function reconcileMissingDeps(): Promise<string[]> {
  let raw: string;
  try {
    raw = await tauriInvoke<string>('read_workspace_file', {
      path: '.signalos/missing-deps.json',
    });
  } catch {
    return []; // file doesn't exist -> nothing to reconcile
  }
  let missing: string[];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    missing = parsed.filter((s) => typeof s === 'string' && s.length > 0);
  } catch {
    return [];
  }
  if (missing.length === 0) return [];

  let pkgRaw: string;
  try {
    pkgRaw = await tauriInvoke<string>('read_workspace_file', { path: 'package.json' });
  } catch {
    return []; // no package.json -> not a Node workspace; skip silently
  }
  let pkg: Record<string, unknown>;
  try {
    pkg = JSON.parse(pkgRaw);
  } catch {
    return [];
  }
  const declared = new Set<string>([
    ...Object.keys((pkg.dependencies as Record<string, string>) || {}),
    ...Object.keys((pkg.devDependencies as Record<string, string>) || {}),
  ]);
  const toAdd = missing.filter((m) => !declared.has(m));
  if (toAdd.length === 0) return [];
  const deps = { ...(pkg.dependencies as Record<string, string> | undefined) };
  for (const name of toAdd) deps[name] = 'latest';
  pkg.dependencies = deps;
  await tauriInvoke('write_workspace_files', {
    files: [{ path: 'package.json', content: JSON.stringify(pkg, null, 2) + '\n' }],
    overwrite: true,
  });
  return toAdd;
}

export async function previewRun(): Promise<void> {
  const ws = workspacePath.value;
  if (!ws) {
    previewStatus.value = 'error';
    return;
  }
  previewStatus.value = 'starting';
  // Reconcile missing deps before npm install so the preview doesn't
  // fail on a fresh package.json that's missing imports the LLM added.
  try {
    const added = await reconcileMissingDeps();
    if (added.length > 0) {
      console.info('[preview] auto-deps: added missing packages to package.json:', added);
    }
  } catch (e) {
    console.warn('[preview] auto-deps reconcile failed (non-fatal):', e);
  }
  try {
    const result = await tauriInvoke<{ key?: string }>('start_preview', {
      stack: previewStack.value || 'react-vite',
      workspace: ws,
    });
    if (result?.key) previewKey.value = result.key;
  } catch (e) {
    console.warn('preview start failed:', e);
    previewStatus.value = 'error';
  }
}

export async function previewStop(): Promise<void> {
  const key = previewKey.value;
  if (!key) {
    previewStatus.value = 'stopped';
    return;
  }
  try {
    await tauriInvoke('stop_preview', { key });
  } catch (e) {
    console.warn('preview stop failed:', e);
  }
  previewStatus.value = 'stopped';
  previewUrl.value = '';
}

export function previewReload(): void {
  // The iframe is keyed off previewUrl; touching it forces a re-mount.
  const u = previewUrl.value;
  if (!u) return;
  previewUrl.value = '';
  // Microtask re-set so React re-renders without the iframe even if the
  // URL is identical.
  queueMicrotask(() => { previewUrl.value = u; });
}

function subscribe(): void {
  const tauri = window.__TAURI__ as unknown as {
    event?: { listen?: (event: string, cb: (e: { payload: PreviewEvent }) => void) => Promise<() => void> };
  } | undefined;
  const listen = tauri?.event?.listen;
  if (typeof listen !== 'function') return;
  listen('preview:event', (e) => {
    const evt = e.payload || {};
    const k = previewKey.value;
    if (evt.key && k && evt.key !== k) return;

    if (evt.kind === 'port' && typeof evt.message === 'string' && evt.message.startsWith('http://')) {
      previewUrl.value = evt.message;
      previewStatus.value = 'running';
      return;
    }
    if (evt.kind === 'status' && typeof evt.message === 'string') {
      const m = evt.message.toLowerCase();
      if (previewStatus.value === 'running') return; // don't regress out of running
      if (m.includes('install')) previewStatus.value = 'installing';
      else if (m.includes('start')) previewStatus.value = 'starting';
      else if (m.includes('stopping')) previewStatus.value = 'stopped';
      return;
    }
    if (evt.kind === 'exit') {
      const m = String(evt.message || '');
      if (/stopped by user/i.test(m)) {
        previewStatus.value = 'stopped';
      } else {
        const codeMatch = m.match(/code\s+(-?\d+)/i);
        const code = codeMatch ? Number(codeMatch[1]) : -1;
        previewStatus.value = code === 0 ? 'stopped' : 'error';
      }
      previewUrl.value = '';
      return;
    }
    if (evt.kind === 'error') {
      previewStatus.value = 'error';
    }
  }).catch(() => {});
}

window.previewRun = previewRun;
window.previewStop = previewStop;
window.previewReload = previewReload;

subscribe();
