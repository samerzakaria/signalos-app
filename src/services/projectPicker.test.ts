import { describe, it, expect, vi, beforeEach } from 'vitest';

// Project picker (#19): list/active/switch/create + the delivery-active
// refusal, all over mocked IPC.

vi.mock('../js/ipc.js', () => ({
  signal: { runAndWait: vi.fn(), run: vi.fn() },
}));

const ipc = await import('../js/ipc.js');
const {
  projectList,
  activeProjectId,
  projectPickerError,
  projectPickerBusy,
  newProjectName,
  loadProjects,
  switchProject,
  createProject,
  ensureProjectsLoaded,
  refreshProjectsPanel,
  refreshAfterProjectChange,
  DELIVERY_ACTIVE_MESSAGE,
  __resetProjectPickerForTests,
} = await import('./projectPicker');
const { chatBubbles, sbTab, tab } = await import('../state');

const runAndWait = ipc.signal.runAndWait as ReturnType<typeof vi.fn>;

beforeEach(() => {
  runAndWait.mockReset();
  __resetProjectPickerForTests();
  chatBubbles.value = [];
  tab.value = 'dashboard';
  sbTab.value = 'projects'; // the app's default sidebar panel
  delete (window as any).switchTab;
});

const REGISTRY = {
  status: 'ok',
  active: 'default',
  projects: [
    { id: 'default', name: 'Default', created_at: '2026-07-01T00:00:00Z' },
    { id: 'mobile-app', name: 'Mobile app', created_at: '2026-07-02T00:00:00Z' },
  ],
};

describe('loadProjects', () => {
  it('populates the list and the active project', async () => {
    runAndWait.mockResolvedValue(REGISTRY);
    await loadProjects();
    expect(runAndWait).toHaveBeenCalledWith('project:list', [JSON.stringify({})], expect.any(Number));
    expect(projectList.value.map((p) => p.id)).toEqual(['default', 'mobile-app']);
    expect(activeProjectId.value).toBe('default');
    expect(projectPickerError.value).toBeNull();
  });

  it('drops malformed entries and tolerates a missing active', async () => {
    runAndWait.mockResolvedValue({
      status: 'ok',
      projects: [{ id: 'x' }, { name: 'no-id' }, null, 42],
    });
    await loadProjects();
    expect(projectList.value).toEqual([{ id: 'x', name: 'x', created_at: undefined }]);
    expect(activeProjectId.value).toBe('default');
  });

  it('surfaces transport failures as the picker error', async () => {
    runAndWait.mockRejectedValue(new Error('engine down'));
    await loadProjects();
    expect(projectPickerError.value).toBe('engine down');
  });
});

describe('switchProject', () => {
  beforeEach(() => {
    projectList.value = REGISTRY.projects;
    activeProjectId.value = 'default';
  });

  it('switches, updates the active id, and refreshes the current view', async () => {
    const switchTab = vi.fn();
    (window as any).switchTab = switchTab;
    tab.value = 'build';
    chatBubbles.value = [{ id: 'b1', kind: 'ai', text: 'old project chat' } as any];
    runAndWait.mockResolvedValue({ status: 'ok', active: 'mobile-app' });

    const okResult = await switchProject('mobile-app');

    expect(okResult).toBe(true);
    expect(runAndWait).toHaveBeenCalledWith(
      'project:switch',
      [JSON.stringify({ project_id: 'mobile-app' })],
      expect.any(Number),
    );
    expect(activeProjectId.value).toBe('mobile-app');
    // Stale per-project surfaces are refreshed the way tab loads do.
    expect(chatBubbles.value).toEqual([]);
    expect(switchTab).toHaveBeenCalledWith('build');
    expect(projectPickerError.value).toBeNull();
  });

  it('delivery-active refusal shows the inline message and keeps the active id', async () => {
    runAndWait.mockResolvedValue({ status: 'delivery-active', runs: ['run-1'] });
    const okResult = await switchProject('mobile-app');
    expect(okResult).toBe(false);
    expect(activeProjectId.value).toBe('default');
    expect(projectPickerError.value).toBe(DELIVERY_ACTIVE_MESSAGE);
    expect(projectPickerError.value).toContain('finish or stop the running delivery first');
  });

  it('switching to the already-active project is a no-op', async () => {
    const okResult = await switchProject('default');
    expect(okResult).toBe(true);
    expect(runAndWait).not.toHaveBeenCalled();
  });

  it('backend error status surfaces the backend message', async () => {
    runAndWait.mockResolvedValue({ status: 'error', error: 'unknown project id: nope' });
    await switchProject('nope');
    expect(projectPickerError.value).toBe('unknown project id: nope');
  });

  it('clears busy even when the transport rejects', async () => {
    runAndWait.mockRejectedValue(new Error('timeout'));
    await switchProject('mobile-app');
    expect(projectPickerBusy.value).toBe(false);
    expect(projectPickerError.value).toBe('timeout');
  });
});

describe('createProject', () => {
  it('creates, appends, marks the new project active, and clears the input', async () => {
    newProjectName.value = 'Web store';
    runAndWait
      .mockResolvedValueOnce({
        status: 'ok',
        project: { id: 'web-store', name: 'Web store', created_at: '2026-07-07T00:00:00Z' },
        active: 'web-store',
      })
      // background re-sync (project:list)
      .mockResolvedValueOnce({
        status: 'ok',
        active: 'web-store',
        projects: [
          { id: 'default', name: 'Default' },
          { id: 'web-store', name: 'Web store' },
        ],
      });

    const okResult = await createProject('Web store');
    expect(okResult).toBe(true);
    expect(runAndWait).toHaveBeenNthCalledWith(
      1,
      'project:create',
      [JSON.stringify({ name: 'Web store' })],
      expect.any(Number),
    );
    expect(activeProjectId.value).toBe('web-store');
    expect(projectList.value.some((p) => p.id === 'web-store')).toBe(true);
    expect(newProjectName.value).toBe('');
    await Promise.resolve(); // let the background list re-sync settle
    expect(runAndWait).toHaveBeenCalledWith('project:list', [JSON.stringify({})], expect.any(Number));
  });

  it('refuses an empty name without calling IPC', async () => {
    const okResult = await createProject('   ');
    expect(okResult).toBe(false);
    expect(runAndWait).not.toHaveBeenCalled();
    expect(projectPickerError.value).toBe('Enter a project name.');
  });

  it('delivery-active refusal shows the inline message', async () => {
    runAndWait.mockResolvedValue({ status: 'delivery-active', runs: ['run-9'] });
    const okResult = await createProject('New thing');
    expect(okResult).toBe(false);
    expect(projectPickerError.value).toBe(DELIVERY_ACTIVE_MESSAGE);
  });

  it('backend error status (e.g. reserved name) surfaces the backend message', async () => {
    runAndWait.mockResolvedValue({ status: 'error', error: '"default" is reserved' });
    const okResult = await createProject('default');
    expect(okResult).toBe(false);
    expect(projectPickerError.value).toBe('"default" is reserved');
  });
});

describe('ensureProjectsLoaded', () => {
  it('loads once per workspace and clears when the workspace closes', async () => {
    runAndWait.mockResolvedValue(REGISTRY);
    ensureProjectsLoaded('C:/Products/One');
    ensureProjectsLoaded('C:/Products/One'); // same ws — no second call
    await Promise.resolve();
    expect(runAndWait).toHaveBeenCalledTimes(1);

    ensureProjectsLoaded('');
    expect(projectList.value).toEqual([]);
    expect(activeProjectId.value).toBe('default');
  });
});

describe('panel-open freshness', () => {
  const listCalls = () =>
    runAndWait.mock.calls.filter((c) => c[0] === 'project:list').length;

  it('re-invokes project:list every time the Projects panel opens', async () => {
    runAndWait.mockResolvedValue(REGISTRY);
    sbTab.value = 'files'; // start away from the panel
    ensureProjectsLoaded('C:/Products/One'); // workspace load (1)
    await Promise.resolve();
    expect(listCalls()).toBe(1);

    sbTab.value = 'projects'; // panel opens (2)
    sbTab.value = 'files'; // panel closes — no fetch
    sbTab.value = 'projects'; // opens again (3)
    await Promise.resolve();
    expect(listCalls()).toBe(3);
  });

  it('re-clicking the active Projects tab refreshes via refreshProjectsPanel', async () => {
    runAndWait.mockResolvedValue(REGISTRY);
    ensureProjectsLoaded('C:/Products/One'); // (1)
    refreshProjectsPanel(); // Sidebar's re-click path (2)
    await Promise.resolve();
    expect(listCalls()).toBe(2);
  });

  it('stays quiet while no workspace is open', () => {
    sbTab.value = 'files';
    sbTab.value = 'projects';
    refreshProjectsPanel();
    expect(runAndWait).not.toHaveBeenCalled();
  });
});

describe('refreshAfterProjectChange', () => {
  it('is safe when the legacy switchTab global is absent', () => {
    chatBubbles.value = [{ id: 'x', kind: 'ai', text: 'y' } as any];
    expect(() => refreshAfterProjectChange()).not.toThrow();
    expect(chatBubbles.value).toEqual([]);
  });
});
