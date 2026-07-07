import { describe, it, expect, vi, beforeEach } from 'vitest';

// Share export (#18): success toast with path + Open folder, error toast.

vi.mock('../js/ipc.js', () => ({
  signal: { runAndWait: vi.fn(), run: vi.fn() },
  project: { openPath: vi.fn() },
}));

const ipc = await import('../js/ipc.js');
const { runShareExport, openExportFolder } = await import('./shareExport');

const runAndWait = ipc.signal.runAndWait as ReturnType<typeof vi.fn>;
const openPath = ipc.project.openPath as ReturnType<typeof vi.fn>;

beforeEach(() => {
  runAndWait.mockReset();
  openPath.mockReset();
  document.body.innerHTML = '';
  delete (window as any).__TAURI__;
});

describe('runShareExport', () => {
  it('success: shows a toast with the export path and an Open folder action', async () => {
    runAndWait.mockResolvedValue({
      status: 'ok',
      path: '.signalos/exports/share-2026-07-07',
      files: ['report.md', 'evidence.json'],
    });
    openPath.mockResolvedValue(undefined);

    const outcome = await runShareExport();

    expect(runAndWait).toHaveBeenCalledWith('share:export', [], expect.any(Number));
    expect(outcome).toMatchObject({
      status: 'ok',
      path: '.signalos/exports/share-2026-07-07',
      files: ['report.md', 'evidence.json'],
    });

    const toast = document.getElementById('shareExportToast');
    expect(toast).toBeTruthy();
    expect(toast!.getAttribute('data-status')).toBe('ok');
    expect(toast!.textContent).toContain('.signalos/exports/share-2026-07-07');
    expect(toast!.textContent).toContain('2 files');

    // "Open folder" opens the export path via the workspace open-path IPC.
    const openBtn = document.getElementById('shareExportOpenBtn') as HTMLButtonElement;
    expect(openBtn).toBeTruthy();
    openBtn.click();
    await new Promise((r) => setTimeout(r, 0)); // open-path call is async
    expect(openPath).toHaveBeenCalledWith('.signalos/exports/share-2026-07-07');
    // Toast dismissed after opening.
    expect(document.getElementById('shareExportToast')).toBeNull();
  });

  it('backend error status: shows the backend message', async () => {
    runAndWait.mockResolvedValue({ status: 'error', error: 'Nothing to export yet — sign G0 first.' });

    const outcome = await runShareExport();

    expect(outcome.status).toBe('error');
    const toast = document.getElementById('shareExportToast');
    expect(toast).toBeTruthy();
    expect(toast!.getAttribute('data-status')).toBe('error');
    expect(toast!.textContent).toContain('Nothing to export yet — sign G0 first.');
  });

  it('unknown command / transport failure: shows a graceful error, never throws', async () => {
    runAndWait.mockRejectedValue(new Error('Unknown command: share:export'));

    const outcome = await runShareExport();

    expect(outcome).toMatchObject({ status: 'error', error: 'Unknown command: share:export' });
    expect(document.getElementById('shareExportToast')!.textContent)
      .toContain('Unknown command: share:export');
  });

  it('tolerates an ok payload with no path (treated as error)', async () => {
    runAndWait.mockResolvedValue({ status: 'ok' });
    const outcome = await runShareExport();
    expect(outcome.status).toBe('error');
    expect(document.getElementById('shareExportToast')!.textContent).toContain('no path');
  });

  it('replaces a previous toast instead of stacking', async () => {
    runAndWait.mockResolvedValue({ status: 'ok', path: '/tmp/x', files: [] });
    await runShareExport();
    await runShareExport();
    expect(document.querySelectorAll('#shareExportToast')).toHaveLength(1);
  });
});

describe('openExportFolder', () => {
  it('falls back to the OS shell when the workspace open-path IPC refuses', async () => {
    const shellOpen = vi.fn(async () => undefined);
    (window as any).__TAURI__ = { shell: { open: shellOpen } };
    openPath.mockRejectedValue(new Error('outside workspace'));

    openExportFolder('C:\\Users\\x\\exports');
    await new Promise((r) => setTimeout(r, 0));

    expect(openPath).toHaveBeenCalledWith('C:\\Users\\x\\exports');
    expect(shellOpen).toHaveBeenCalledWith('C:\\Users\\x\\exports');
  });
});
