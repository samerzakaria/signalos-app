// Vitest setup: jest-dom matchers + stubs for window-level Tauri/IPC
// helpers the views reach for. Without these stubs, JSDOM `onClick`
// handlers that call `window.previewReload()` etc. would blow up at
// render time and obscure the actual rendering assertion.

import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/preact';

afterEach(() => {
  cleanup();
});

// Tauri-only window helpers used by view onClick handlers. Tests don't
// invoke them by default; they're stubs to prevent ReferenceError when
// jsdom renders the components.
const w = window as unknown as Record<string, unknown>;
const noop = vi.fn();
const noops = [
  'switchDevice',
  'previewReload',
  'previewStop',
  'previewRun',
  'openExternal',
  'approvePlan',
  'cancelWave',
  'retryTask',
  'showSignForm',
  'ensureWorkspaceFolder',
  'initWorkspace',
  'createSignalosProject',
  'pickWorkspaceFolder',
  'instantiateGovernance',
  'approveGate0',
  'openCommandPalette',
  'closeCommandPalette',
];
for (const name of noops) {
  if (w[name] === undefined) w[name] = noop;
}

// Tauri IPC namespace — most views import services that read this
// lazily; the bare presence prevents undefined-property crashes.
if (w.__TAURI__ === undefined) {
  w.__TAURI__ = {
    invoke: vi.fn(async () => null),
    event: { listen: vi.fn(async () => () => undefined), emit: vi.fn() },
    dialog: { open: vi.fn(), save: vi.fn() },
  };
}
