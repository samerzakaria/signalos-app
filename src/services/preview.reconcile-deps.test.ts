import { describe, it, expect, beforeEach, vi } from 'vitest';
import { previewRun } from './preview';
import { workspacePath, previewStack, previewStatus, previewKey } from '../state';

// Claim 11c — the auto-deps repair reads .signalos/missing-deps.json and
// package.json before the preview's npm install. The Rust command binds
// `relative_path`; the old code passed `path`, so every read rejected and the
// repair silently no-oped. Lock the correct arg name.

describe('preview auto-deps reconcile — read_workspace_file arg name', () => {
  let invoke: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    workspacePath.value = 'C:/ws';
    previewStack.value = 'vite';
    previewStatus.value = 'idle';
    previewKey.value = '';
    invoke = vi.fn(async (cmd: string, args: Record<string, unknown>) => {
      if (cmd === 'read_workspace_file' && args?.relative_path === '.signalos/missing-deps.json') {
        return JSON.stringify(['lodash']);
      }
      if (cmd === 'read_workspace_file' && args?.relative_path === 'package.json') {
        return JSON.stringify({ dependencies: {} });
      }
      if (cmd === 'start_preview') return { key: 'k1' };
      return null;
    });
    (window as unknown as { __TAURI__: unknown }).__TAURI__ = {
      core: { invoke },
      event: { listen: vi.fn(async () => () => undefined) },
    };
  });

  it('reads missing-deps.json and package.json via relative_path (never path)', async () => {
    await previewRun();

    expect(invoke).toHaveBeenCalledWith('read_workspace_file', { relative_path: '.signalos/missing-deps.json' });
    expect(invoke).toHaveBeenCalledWith('read_workspace_file', { relative_path: 'package.json' });

    const legacyCall = invoke.mock.calls.find(
      ([cmd, args]) => cmd === 'read_workspace_file' && args && Object.prototype.hasOwnProperty.call(args, 'path'),
    );
    expect(legacyCall).toBeUndefined();

    // Repair actually happened: lodash was undeclared, so package.json is
    // rewritten with it before install.
    const write = invoke.mock.calls.find(([cmd]) => cmd === 'write_workspace_files');
    expect(write).toBeTruthy();
    const files = (write![1] as { files: { path: string; content: string }[] }).files;
    expect(files[0].path).toBe('package.json');
    expect(files[0].content).toContain('lodash');
  });
});
