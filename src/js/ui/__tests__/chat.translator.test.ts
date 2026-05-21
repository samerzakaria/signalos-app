/**
 * chat.translator.test.ts — WAVE-ENGINE-DESIGN §7 translator-mode UI hook.
 *
 * `attachExternalDoc()` is bound to window.attachFile (the composer's
 * paperclip button). It prompts for a path or URL, calls
 * waveEngineClient.translateExternal, and renders the engine's bubble
 * + the translated body as an AI bubble.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { chatBubbles, chatInputValue, busy, cmdPaletteOpen } from '../../../state';

// Mock ipc.js so chat.js's other paths (signal, enforcement, provider)
// don't try to hit Tauri during this test.
vi.mock('../../ipc.js', () => ({
  signal: { run: vi.fn(), runAndWait: vi.fn(), cancelPending: vi.fn() },
  enforcement: {
    state: vi.fn(), precheck: vi.fn(), override: vi.fn(),
    setMode: vi.fn(), freeze: vi.fn(), unfreeze: vi.fn(),
  },
  provider: { chatStream: vi.fn(), getCost: vi.fn() },
}));

// Mock waveEngineClient so the translator IPC call is fully under
// test control.
const translateExternal = vi.fn();
const tryBegin = vi.fn(async () => null);
vi.mock('../../../services/waveEngineClient.ts', () => ({
  translateExternal,
  tryBegin,
}));

// Mock the Tauri dialog plugin so tests can control its return value
// (selected path / null on cancel / throw to force prompt fallback).
const dialogOpen = vi.fn();
vi.mock('@tauri-apps/plugin-dialog', () => ({ open: dialogOpen }));

// Mock the Tauri webview API for drag-drop registration + manual trigger.
let dragDropHandler: ((event: { payload: { type: string; paths?: string[] } }) => unknown) | null = null;
vi.mock('@tauri-apps/api/webview', () => ({
  getCurrentWebview: () => ({
    onDragDropEvent: async (cb: (event: { payload: { type: string; paths?: string[] } }) => unknown) => {
      dragDropHandler = cb;
      return () => { dragDropHandler = null; };
    },
  }),
}));

// Console silence — chat.js logs "[translator] Tauri dialog unavailable…"
// on every fallback. The mocked dialog avoids that, but keep this here
// for when a test forces an exception path.
beforeEach(() => {
  vi.spyOn(console, 'debug').mockImplementation(() => {});
});

vi.mock('../../app-v2.js', () => ({
  loadEnforcement: vi.fn(async () => undefined),
  updateCostDisplay: vi.fn(),
}));
vi.mock('../../conversation.js', () => ({
  activeBuildId: vi.fn(async () => 'build-test'),
  appendTurn: vi.fn(async () => undefined),
  loadHistory: vi.fn(async () => []),
}));
vi.mock('../../util.js', () => ({ showError: vi.fn() }));

const chatModule = await import('../chat.js');

const { attachExternalDoc } = chatModule as unknown as {
  attachExternalDoc: () => Promise<void>;
};

function setPrompt(answer: string | null): void {
  (window as unknown as { prompt: (s: string) => string | null }).prompt =
    vi.fn(() => answer);
}

beforeEach(() => {
  translateExternal.mockReset();
  // Default: dialog throws so the existing prompt-based tests still
  // exercise the fallback. Tests that want to drive the dialog
  // success/cancel path override before calling attachExternalDoc.
  dialogOpen.mockReset();
  dialogOpen.mockRejectedValue(new Error('dialog unavailable (test default)'));
  chatBubbles.value = [];
  chatInputValue.value = '';
  busy.value = false;
  cmdPaletteOpen.value = false;
});

describe('attachExternalDoc — translator-mode UI hook', () => {
  it('no-ops when the user dismisses the prompt', async () => {
    setPrompt(null);
    await attachExternalDoc();
    expect(translateExternal).not.toHaveBeenCalled();
    expect(chatBubbles.value).toHaveLength(0);
  });

  it('no-ops on empty / whitespace input', async () => {
    setPrompt('    ');
    await attachExternalDoc();
    expect(translateExternal).not.toHaveBeenCalled();
    expect(chatBubbles.value).toHaveLength(0);
  });

  it('appends a user bubble naming the artifact then calls translateExternal', async () => {
    setPrompt('docs/brief.md');
    translateExternal.mockResolvedValue({
      translation: { supported: true, format: 'markdown', text: 'Belief body.' },
      gate: 'G1',
      system_bubble: { kind: 'reroute', gate: 'G1', text: 'Translating markdown' },
    });
    await attachExternalDoc();

    expect(translateExternal).toHaveBeenCalledWith('docs/brief.md');
    const kinds = chatBubbles.value.map((b) => b.kind);
    expect(kinds).toContain('user');     // "[Translator-mode] docs/brief.md"
    expect(kinds).toContain('system');   // engine bubble
    expect(kinds).toContain('ai');       // translated body as AI bubble
  });

  it('renders the translated body in an AI bubble (trimmed when oversized)', async () => {
    setPrompt('docs/brief.md');
    const longBody = 'x'.repeat(10000);
    translateExternal.mockResolvedValue({
      translation: { supported: true, format: 'markdown', text: longBody },
      gate: null,
      system_bubble: { kind: 'reroute', text: 'Translating markdown' },
    });
    await attachExternalDoc();
    const ai = chatBubbles.value.find((b) => b.kind === 'ai');
    expect(ai).toBeDefined();
    // Trimmed to 4000 + marker.
    expect(ai!.text.length).toBeLessThan(longBody.length);
    expect(ai!.text).toContain('trimmed');
  });

  it('records a Figma URL as a reference (no body text expected)', async () => {
    setPrompt('https://www.figma.com/design/ABC/X');
    translateExternal.mockResolvedValue({
      translation: {
        supported: true,
        format: 'figma-url',
        text: '',
        source_url: 'https://www.figma.com/design/ABC/X',
        figma_file_key: 'ABC',
      },
      gate: 'G3',
      system_bubble: { kind: 'reroute', gate: 'G3', text: 'Recording Figma ref' },
    });
    await attachExternalDoc();
    const ai = chatBubbles.value.find((b) => b.kind === 'ai');
    expect(ai!.text).toMatch(/Recorded figma-url reference/);
    expect(ai!.text).toContain('https://www.figma.com/design/ABC/X');
  });

  it('surfaces an error bubble when the artifact format is unsupported', async () => {
    setPrompt('mystery.unknown');
    translateExternal.mockResolvedValue({
      translation: {
        supported: false,
        format: 'unknown',
        text: '',
        error: "Could not detect format for: 'mystery.unknown'",
      },
      gate: null,
      system_bubble: { kind: 'reroute', text: 'Cannot translate' },
    });
    await attachExternalDoc();
    const errBubble = chatBubbles.value.find((b) => b.kind === 'error');
    expect(errBubble).toBeDefined();
    expect(errBubble!.text).toMatch(/could not ingest unknown/i);
  });

  it('surfaces an error bubble when the IPC call rejects', async () => {
    setPrompt('docs/brief.md');
    translateExternal.mockRejectedValue(new Error('sidecar down'));
    await attachExternalDoc();
    const errBubble = chatBubbles.value.find((b) => b.kind === 'error');
    expect(errBubble).toBeDefined();
    expect(errBubble!.text).toMatch(/Translator-mode failed/);
    expect(errBubble!.text).toMatch(/sidecar down/);
  });

  it('shows install hint when the optional dep is missing', async () => {
    setPrompt('docs/brief.pdf');
    translateExternal.mockResolvedValue({
      translation: {
        supported: false,
        format: 'pdf',
        text: '',
        install_hint: 'pip install pypdf',
      },
      gate: null,
      system_bubble: { kind: 'reroute', text: 'Cannot translate' },
    });
    await attachExternalDoc();
    const errBubble = chatBubbles.value.find((b) => b.kind === 'error');
    expect(errBubble!.text).toContain('Install hint: pip install pypdf');
  });

  it('registers itself on window.attachFile so the paperclip button fires it', () => {
    expect(
      (window as unknown as { attachFile: () => Promise<void> }).attachFile,
    ).toBe(attachExternalDoc);
  });
});


// --------------------------------------------------------------------------
// Tauri-dialog success path (skips window.prompt entirely)
// --------------------------------------------------------------------------

describe('attachExternalDoc — Tauri dialog path', () => {
  it('uses the dialog-returned path without falling back to prompt', async () => {
    setPrompt('SHOULD-NOT-BE-USED');
    dialogOpen.mockReset();
    dialogOpen.mockResolvedValue('/Users/me/brief.md');
    translateExternal.mockResolvedValue({
      translation: { supported: true, format: 'markdown', text: 'body' },
      gate: 'G1',
      system_bubble: { kind: 'reroute', gate: 'G1', text: 'Translating markdown' },
    });
    await attachExternalDoc();
    expect(translateExternal).toHaveBeenCalledWith('/Users/me/brief.md');
  });

  it('respects dialog cancel (null return) by not running translator', async () => {
    setPrompt('SHOULD-NOT-BE-USED');
    dialogOpen.mockReset();
    dialogOpen.mockResolvedValue(null);   // user dismissed the dialog
    await attachExternalDoc();
    expect(translateExternal).not.toHaveBeenCalled();
  });

  it('falls through to prompt() when the dialog plugin throws', async () => {
    setPrompt('docs/typed-by-hand.md');
    dialogOpen.mockReset();
    dialogOpen.mockRejectedValue(new Error('plugin runtime error'));
    translateExternal.mockResolvedValue({
      translation: { supported: true, format: 'markdown', text: 'body' },
      gate: null,
      system_bubble: { kind: 'reroute', text: 'Translating markdown' },
    });
    await attachExternalDoc();
    expect(translateExternal).toHaveBeenCalledWith('docs/typed-by-hand.md');
  });
});


// --------------------------------------------------------------------------
// Tauri webview drag-drop path (handler registered at module load)
// --------------------------------------------------------------------------

describe('attachExternalDoc — Tauri drag-drop path', () => {
  it('registers a drag-drop handler at module load', () => {
    expect(dragDropHandler).not.toBeNull();
  });

  it('routes a drop event through the translator pipeline', async () => {
    translateExternal.mockResolvedValue({
      translation: { supported: true, format: 'pdf', text: 'pdf body' },
      gate: 'G1',
      system_bubble: { kind: 'reroute', gate: 'G1', text: 'Translating pdf' },
    });
    await dragDropHandler!({
      payload: { type: 'drop', paths: ['/Users/me/brief.pdf'] },
    });
    expect(translateExternal).toHaveBeenCalledWith('/Users/me/brief.pdf');
    // user-bubble surfaces what was dropped (provenance).
    expect(chatBubbles.value.some((b) => b.text.includes('/Users/me/brief.pdf'))).toBe(true);
  });

  it('takes the first path when multiple files are dropped', async () => {
    translateExternal.mockResolvedValue({
      translation: { supported: true, format: 'markdown', text: 'body' },
      gate: null,
      system_bubble: { kind: 'reroute', text: 'Translating markdown' },
    });
    await dragDropHandler!({
      payload: { type: 'drop', paths: ['/Users/me/a.md', '/Users/me/b.md', '/Users/me/c.pdf'] },
    });
    expect(translateExternal).toHaveBeenCalledTimes(1);
    expect(translateExternal).toHaveBeenCalledWith('/Users/me/a.md');
  });

  it('ignores drag-over (type !== "drop")', async () => {
    await dragDropHandler!({ payload: { type: 'over' } });
    expect(translateExternal).not.toHaveBeenCalled();
  });

  it('ignores empty path list', async () => {
    await dragDropHandler!({ payload: { type: 'drop', paths: [] } });
    expect(translateExternal).not.toHaveBeenCalled();
  });
});
