import { releaseReadiness, workspacePath, type ReleaseReadinessResult } from '../state';

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = window.__TAURI__;
  const invoke = tauri?.core?.invoke || tauri?.invoke;
  if (!invoke) throw new Error('Tauri runtime not available');
  return invoke<T>(cmd, args);
}

export async function refreshReleaseReadiness(): Promise<void> {
  if (!workspacePath.value) {
    releaseReadiness.value = { loading: false, error: null, result: null };
    return;
  }

  releaseReadiness.value = {
    loading: true,
    error: null,
    result: releaseReadiness.value.result,
  };

  try {
    const output = await tauriInvoke<string>('run_signal_command', {
      command: 'signal-release-readiness',
      args: ['--json'],
    });
    const parsed = JSON.parse(output) as ReleaseReadinessResult;
    releaseReadiness.value = { loading: false, error: null, result: parsed };
  } catch (e) {
    releaseReadiness.value = {
      loading: false,
      error: e instanceof Error ? e.message : String(e),
      result: releaseReadiness.value.result,
    };
  }
}
