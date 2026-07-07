// projectPicker.ts — frontend for the multi-project registry (#19).
//
// The Python sidecar owns .signalos/projects.json (signalos_lib/projects.py);
// this service wraps the three IPC commands and holds the picker's signals:
//
//   project:list   {}               → {status:"ok", active, projects:[{id,name,created_at}]}
//   project:create {name}           → {status:"ok", project, active} | {status:"delivery-active", runs}
//   project:switch {project_id}     → {status:"ok", active}          | {status:"delivery-active", runs}
//
// These manage project NAMESPACES inside the active workspace — they do not
// touch the workspace-folder flow (workspace.ts / recentWorkspaces).
//
// After a successful switch/create the per-project surfaces (wave state,
// gates, conversation) are stale: refreshAfterProjectChange() clears the chat
// (so loadBuild re-hydrates from the new namespace — see the #50 comment in
// src/js/ui/chat.js) and re-runs the current tab's loader exactly the way tab
// navigation does (window.switchTab).

import { effect, signal } from '@preact/signals';
import * as ipc from '../js/ipc.js';
import { chatBubbles, sbTab, tab } from '../state';

export interface ProjectEntry {
  id: string;
  name: string;
  created_at?: string;
}

export const projectList = signal<ProjectEntry[]>([]);
export const activeProjectId = signal<string>('default');
export const projectPickerError = signal<string | null>(null);
export const projectPickerBusy = signal<boolean>(false);
export const newProjectName = signal<string>('');

// Inline refusal text for {status:"delivery-active"} (create/switch refuse
// while a governed delivery is running — switching would split its state
// across two namespaces).
export const DELIVERY_ACTIVE_MESSAGE =
  'A delivery is running — finish or stop the running delivery first.';

const IPC_TIMEOUT_MS = 15000;

interface ProjectIpcResponse {
  status?: string;
  error?: string;
  active?: string;
  projects?: unknown;
  project?: unknown;
}

function asResponse(raw: unknown): ProjectIpcResponse {
  return raw && typeof raw === 'object' ? (raw as ProjectIpcResponse) : {};
}

function normalizeProject(raw: unknown): ProjectEntry | null {
  if (!raw || typeof raw !== 'object') return null;
  const p = raw as Record<string, unknown>;
  const id = typeof p.id === 'string' ? p.id : '';
  if (!id) return null;
  return {
    id,
    name: typeof p.name === 'string' && p.name ? p.name : id,
    created_at: typeof p.created_at === 'string' ? p.created_at : undefined,
  };
}

function normalizeProjects(raw: unknown): ProjectEntry[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map(normalizeProject)
    .filter((p): p is ProjectEntry => p !== null);
}

function failureMessage(res: ProjectIpcResponse, verb: string): string {
  if (res.status === 'delivery-active') return DELIVERY_ACTIVE_MESSAGE;
  return res.error || `Could not ${verb} (${res.status || 'no response'}).`;
}

/** Fetch the registry. Errors land in projectPickerError, never thrown. */
export async function loadProjects(): Promise<void> {
  try {
    const res = asResponse(
      await ipc.signal.runAndWait('project:list', [JSON.stringify({})], IPC_TIMEOUT_MS),
    );
    if (res.status === 'ok') {
      projectList.value = normalizeProjects(res.projects);
      activeProjectId.value =
        typeof res.active === 'string' && res.active ? res.active : 'default';
      projectPickerError.value = null;
    } else {
      projectPickerError.value = failureMessage(res, 'load projects');
    }
  } catch (e) {
    projectPickerError.value = e instanceof Error ? e.message : String(e);
  }
}

/**
 * Refresh the per-project surfaces after the active namespace changed:
 * clear the conversation (Build re-hydrates it from the new project on next
 * load) and re-run the current view's loader the way switchTab does.
 */
export function refreshAfterProjectChange(): void {
  chatBubbles.value = [];
  try {
    void window.switchTab?.(tab.value);
  } catch {
    /* legacy global unavailable in tests/dev — signals already updated */
  }
}

/** Switch the active project. Resolves true on success. */
export async function switchProject(projectId: string): Promise<boolean> {
  const target = (projectId || '').trim();
  if (!target || projectPickerBusy.value) return false;
  if (target === activeProjectId.value) return true;
  projectPickerBusy.value = true;
  projectPickerError.value = null;
  try {
    const res = asResponse(
      await ipc.signal.runAndWait(
        'project:switch',
        [JSON.stringify({ project_id: target })],
        IPC_TIMEOUT_MS,
      ),
    );
    if (res.status === 'ok') {
      activeProjectId.value =
        typeof res.active === 'string' && res.active ? res.active : target;
      refreshAfterProjectChange();
      return true;
    }
    projectPickerError.value = failureMessage(res, 'switch project');
    return false;
  } catch (e) {
    projectPickerError.value = e instanceof Error ? e.message : String(e);
    return false;
  } finally {
    projectPickerBusy.value = false;
  }
}

/** Create a project (the backend also switches to it). Resolves true on success. */
export async function createProject(name: string): Promise<boolean> {
  const trimmed = (name || '').trim();
  if (projectPickerBusy.value) return false;
  if (!trimmed) {
    projectPickerError.value = 'Enter a project name.';
    return false;
  }
  projectPickerBusy.value = true;
  projectPickerError.value = null;
  try {
    const res = asResponse(
      await ipc.signal.runAndWait(
        'project:create',
        [JSON.stringify({ name: trimmed })],
        IPC_TIMEOUT_MS,
      ),
    );
    if (res.status === 'ok') {
      const created = normalizeProject(res.project);
      if (created && !projectList.value.some((p) => p.id === created.id)) {
        projectList.value = [...projectList.value, created];
      }
      activeProjectId.value =
        typeof res.active === 'string' && res.active
          ? res.active
          : created?.id || activeProjectId.value;
      newProjectName.value = '';
      // Creating switches the active project server-side — same refresh.
      refreshAfterProjectChange();
      // Re-sync the full registry in the background (id slugging/suffixing
      // is decided server-side).
      void loadProjects();
      return true;
    }
    projectPickerError.value = failureMessage(res, 'create project');
    return false;
  } catch (e) {
    projectPickerError.value = e instanceof Error ? e.message : String(e);
    return false;
  } finally {
    projectPickerBusy.value = false;
  }
}

// Load the registry once per workspace (mirrors Sidebar's test-debt probe).
let lastLoadedWorkspace: string | null = null;

export function ensureProjectsLoaded(workspace: string): void {
  if (workspace === lastLoadedWorkspace) return;
  lastLoadedWorkspace = workspace;
  if (!workspace) {
    projectList.value = [];
    activeProjectId.value = 'default';
    projectPickerError.value = null;
    return;
  }
  void loadProjects();
}

// Panel-open freshness: the registry can change outside the app while it is
// open (e.g. `signalos project create` from the CLI), so the once-per-
// workspace load above goes stale. Re-fetch every time the sidebar's Projects
// panel becomes the active panel.

/** Refresh the registry because the Projects panel was (re)opened. No-op
 *  until a workspace has been loaded — ensureProjectsLoaded owns the initial
 *  load and the no-workspace reset. */
export function refreshProjectsPanel(): void {
  if (!lastLoadedWorkspace) return;
  void loadProjects();
}

// Every panel-activation path writes the sbTab signal — the preact Sidebar's
// switchPanel and the legacy app-v2.js switchSbTab (through the state.js
// proxy) — so one signal effect covers all of them. Fires only on the
// files/gov → projects transition, not on unrelated re-renders; a re-click on
// the already-active Projects tab can't change the signal, so Sidebar calls
// refreshProjectsPanel() directly for that case.
let lastSbPanel: string | null = null;
effect(() => {
  const panel = sbTab.value;
  const opened = panel === 'projects' && lastSbPanel !== 'projects';
  lastSbPanel = panel;
  if (opened) refreshProjectsPanel();
});

/** Test seam: reset module state between tests. */
export function __resetProjectPickerForTests(): void {
  lastLoadedWorkspace = null;
  projectList.value = [];
  activeProjectId.value = 'default';
  projectPickerError.value = null;
  projectPickerBusy.value = false;
  newProjectName.value = '';
}
