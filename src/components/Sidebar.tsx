import { signal } from '@preact/signals';
import {
  userName,
  userRole,
  govGatesList,
  auditList,
  fileTreeEntries,
  recentlyChangedFiles,
  workspacePath,
  recentWorkspaces,
  projectsRoot,
  modalOpen,
  sbTab,
  tab,
  mobileNavOpen,
  type WorkspaceEntry,
} from '../state';
import { gateCode, gateUiState } from './GateTimeline';
import { TestDebtPanel } from './TestDebtPanel';
import { ProjectPicker } from './ProjectPicker';
import { ensureProjectsLoaded, refreshProjectsPanel } from '../services/projectPicker';
import { sidebarNavClass, sidebarPanelClass, sidebarTabClass } from './viewShell';
import { project, testAutomation } from '../js/ipc.js';
import { FoundryMark } from './FoundryMark';

/** Whether the workspace has a .signalos/ directory — drives conditional rendering. */
const hasSignalosDir = signal<boolean>(false);
/** Whether there is actual test debt data to display. */
const hasTestDebtData = signal<boolean>(false);

/**
 * Probe workspace for .signalos dir and test-debt store existence.
 * Called when "gov" tab is selected and workspace changes.
 */
async function probeTestDebt(ws: string) {
  if (!ws) {
    hasSignalosDir.value = false;
    hasTestDebtData.value = false;
    return;
  }
  try {
    const entries = await project.listDir('.signalos');
    const dirExists = Array.isArray(entries) && entries.length > 0;
    hasSignalosDir.value = dirExists;
    if (dirExists) {
      const debt = await testAutomation.listDebt();
      const debtSummary = debt as { entries?: unknown[]; open_count?: number } | null;
      hasTestDebtData.value = Boolean(
        debtSummary &&
        ((debtSummary.entries && debtSummary.entries.length > 0) ||
         (debtSummary.open_count && debtSummary.open_count > 0))
      );
    } else {
      hasTestDebtData.value = false;
    }
  } catch {
    hasSignalosDir.value = false;
    hasTestDebtData.value = false;
  }
}

// Re-probe when workspace path changes
let _lastProbedWs = '';
function ensureTestDebtProbed(ws: string) {
  if (ws !== _lastProbedWs) {
    _lastProbedWs = ws;
    probeTestDebt(ws);
  }
}

// ── Lazy, expandable file tree (Claim 11b) ──────────────────────────────────
// Directory rows previously rendered as static divs with no click handler, so
// nested generated files were unbrowsable. Now a directory expands on click
// and lazily fetches its own children via list_workspace_dir(childPath); nested
// entries render indented. State lives at module scope so it survives the
// Sidebar's re-renders (the component re-runs on every signal change).
const expandedDirs = signal<Set<string>>(new Set());
const dirChildren = signal<Record<string, WorkspaceEntry[]>>({});
const loadingDirs = signal<Set<string>>(new Set());

/** Reset the browse state — call when the workspace changes so stale children
 *  from a previous project don't linger. */
function resetFileTreeBrowse() {
  expandedDirs.value = new Set();
  dirChildren.value = {};
  loadingDirs.value = new Set();
}

let _lastBrowseWs = '';
function ensureBrowseWsFresh(ws: string) {
  if (ws !== _lastBrowseWs) {
    _lastBrowseWs = ws;
    resetFileTreeBrowse();
  }
}

async function toggleDir(path: string): Promise<void> {
  const expanded = new Set(expandedDirs.value);
  if (expanded.has(path)) {
    expanded.delete(path);
    expandedDirs.value = expanded;
    return;
  }
  expanded.add(path);
  expandedDirs.value = expanded;
  // Fetch children once, then cache.
  if (path in dirChildren.value) return;
  const loading = new Set(loadingDirs.value);
  loading.add(path);
  loadingDirs.value = loading;
  let children: WorkspaceEntry[] = [];
  try {
    const res = await project.listDir(path);
    if (Array.isArray(res)) children = res as WorkspaceEntry[];
  } catch {
    children = [];
  } finally {
    dirChildren.value = { ...dirChildren.value, [path]: children };
    const done = new Set(loadingDirs.value);
    done.delete(path);
    loadingDirs.value = done;
  }
}

function FileTreeRows({ entries, depth, flashed }: {
  entries: WorkspaceEntry[];
  depth: number;
  flashed: Set<string>;
}) {
  return (
    <>
      {entries.map((entry) => {
        const isDir = entry.kind === 'dir';
        const isOpen = isDir && expandedDirs.value.has(entry.path);
        const isLoading = isDir && loadingDirs.value.has(entry.path);
        const children = dirChildren.value[entry.path];
        const cls = 'ftree-item' + (isDir ? ' dir' : '');
        const icon = isDir ? (isOpen ? 'ti-folder-open' : 'ti-folder') : 'ti-file-code';
        const recently = flashed.has(entry.path) || flashed.has(entry.name);
        const childIndent = { paddingLeft: `${8 + (depth + 1) * 14}px` };
        return (
          <div key={entry.path || entry.name}>
            <div
              className={cls}
              style={{ paddingLeft: `${8 + depth * 14}px`, cursor: isDir ? 'pointer' : 'default' }}
              onClick={isDir ? () => { void toggleDir(entry.path); } : undefined}
              role={isDir ? 'button' : undefined}
              aria-expanded={isDir ? isOpen : undefined}
              data-testid={isDir ? 'ftree-dir' : 'ftree-file'}
              data-path={entry.path}
            >
              {isDir ? (
                <i className={`ti ${isOpen ? 'ti-chevron-down' : 'ti-chevron-right'}`} style={{ fontSize: '11px' }}></i>
              ) : null}
              <i className={`ti ${icon}`}></i> {entry.name}
              {recently ? <span className="diff-badge new">NEW</span> : null}
            </div>
            {isOpen ? (
              isLoading && children === undefined ? (
                <div className="ftree-item" style={{ ...childIndent, color: 'var(--ink-3)', fontSize: '11px' }}>Loading…</div>
              ) : children && children.length > 0 ? (
                <FileTreeRows entries={children} depth={depth + 1} flashed={flashed} />
              ) : children ? (
                <div className="ftree-item" style={{ ...childIndent, color: 'var(--ink-3)', fontSize: '11px' }}>(empty)</div>
              ) : null
            ) : null}
          </div>
        );
      })}
    </>
  );
}

export function Sidebar() {
  const tree = fileTreeEntries.value;
  const flashed = recentlyChangedFiles.value;
  const ws = workspacePath.value;
  const recents = recentWorkspaces.value;

  // Probe test-debt availability whenever workspace changes
  ensureTestDebtProbed(ws);
  // Load the project-namespace registry (#19) whenever workspace changes
  ensureProjectsLoaded(ws);
  // Drop cached file-tree expansion state when the workspace changes.
  ensureBrowseWsFresh(ws);

  const switchPanel = (id: string) => {
    // Panel-open freshness: switching TO the projects panel is observed by
    // the projectPicker service's sbTab effect, but re-clicking the already-
    // active Projects tab doesn't change the signal — refresh explicitly so
    // a project created outside the app (e.g. via the CLI) shows up.
    const reClick = id === sbTab.value;
    sbTab.value = id;
    try { window.switchSbTab?.(id); } catch {}
    if (id === 'projects' && reClick) refreshProjectsPanel();
  };

  const navigate = (id: string) => {
    tab.value = id;
    try { void window.switchTab?.(id); } catch {}
  };

  const openProjectModal = () => {
    modalOpen.value = 'newProjectModal';
    try { window.openNewProject?.(); } catch {}
  };

  const openWorkspace = (path: string) => {
    const target = path.trim();
    if (!target) return;
    if (target === ws) {
      navigate('dashboard');
      return;
    }
    if (typeof window.switchWorkspace === 'function') {
      try { void window.switchWorkspace(target); } catch {}
      return;
    }
    workspacePath.value = target;
    navigate('dashboard');
  };

  return (
    <>
<aside className={"sidebar" + (mobileNavOpen.value ? " mobile-open" : "")}>
    <div className="sb-head">
      <div className="sb-mark">
        <FoundryMark size={22} />
      </div>
      <span className="sb-name">Foundry <span className="pro">by SignalOS</span></span>
    </div>

    
    <div className="sb-tabs">
      <button type="button" className={sidebarTabClass('projects')} onClick={() => switchPanel('projects')}>Projects</button>
      <button type="button" className={sidebarTabClass('files')} onClick={() => switchPanel('files')}>Files</button>
      <button type="button" className={sidebarTabClass('gov')} onClick={() => switchPanel('gov')}>Gov</button>
    </div>

    
    <div className={sidebarPanelClass('projects')} id="sb-projects">
      <button type="button" className="nav accent" onClick={openProjectModal} data-testid="sidebar-new-project"><i className="ti ti-plus"></i> New project</button>
      {projectsRoot.value ? (
        <div
          className="sb-projects-root"
          title={projectsRoot.value}
          data-testid="sidebar-projects-root"
          style={{ padding:'6px 12px', fontSize:'11px', color:'var(--ink-3)', display:'flex', alignItems:'center', gap:'6px', overflow:'hidden' }}
        >
          <i className="ti ti-folder-cog" style={{ flexShrink:0 }}></i>
          <span style={{ fontFamily:'var(--f-mono)', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis', direction:'rtl' }}>{projectsRoot.value}</span>
        </div>
      ) : null}
      <div className="sb-label">Projects</div>
      {recents.length > 0 ? (
        recents.map((project) => {
          const active = project.path === ws;
          const missing = project.exists === false;
          const cls = active ? 'nav active' : 'nav';
          return (
            <button
              type="button"
              className={cls}
              key={project.path}
              title={project.path}
              onClick={() => openWorkspace(project.path)}
              disabled={missing}
              data-testid="sidebar-recent-project"
            >
              <i className={`ti ${missing ? 'ti-alert-triangle' : active ? 'ti-folder-check' : 'ti-folder'}`}></i>
              <span className="nav-text">{project.name || project.path}</span>
              {missing ? <span className="nav-tag">Missing</span> : null}
            </button>
          );
        })
      ) : (
        <div className="sb-empty">No projects yet.</div>
      )}
      {ws ? <ProjectPicker /> : null}
      <div className="sb-label">Tools</div>
      <button type="button" className={sidebarNavClass('warroom')} data-tab="warroom" onClick={() => navigate('warroom')}><i className="ti ti-messages"></i> War Room</button>
      <button type="button" className={sidebarNavClass('vault')} data-tab="vault" onClick={() => navigate('vault')}><i className="ti ti-shield-lock"></i> Vault</button>
      <button type="button" className={sidebarNavClass('brain')} data-tab="brain" onClick={() => navigate('brain')}><i className="ti ti-brain"></i> Brain</button>
      <button type="button" className={sidebarNavClass('history')} data-tab="history" onClick={() => navigate('history')}><i className="ti ti-history"></i> History</button>
      <div className="sb-label">Account</div>
      <button type="button" className={sidebarNavClass('settings')} data-tab="settings" onClick={() => navigate('settings')}><i className="ti ti-settings"></i> Settings</button>
      <button type="button" className={sidebarNavClass('help')} data-tab="help" onClick={() => navigate('help')}><i className="ti ti-help-circle"></i> Help</button>
      <div className="sb-user" style={{ 'marginTop': '8px' }}>
        <div className="sb-av" id="sbAvatar">{userName.value ? userName.value[0].toUpperCase() : 'D'}</div>
        <div className="sb-ui">
          <div className="sb-un" id="sbUserName">{userName.value || 'Developer'}</div>
          <div className="sb-us" id="sbUserRole">{userRole.value || 'Local account'}</div>
        </div>
      </div>
    </div>

    
    <div className={sidebarPanelClass('files')} id="sb-files">
      <div className="sb-search">
        <i className="ti ti-search"></i>
        <input placeholder="Filter files…"/>
      </div>
      <div className="ftree" id="leftFileTree">
        {!ws ? (
          <div style={{ padding: '16px 12px', fontSize: '12px', color: 'var(--ink-3)' }}>
            No workspace open.
          </div>
        ) : tree.length === 0 ? (
          <div style={{ padding: '16px 12px', fontSize: '12px', color: 'var(--ink-3)' }}>
            Workspace empty. Build something to populate it.
          </div>
        ) : (
          <FileTreeRows entries={tree} depth={0} flashed={flashed} />
        )}
      </div>
    </div>

    
    <div className={sidebarPanelClass('gov')} id="sb-gov">
      <div className="gov-wave">
        <div className="gov-wave-label">Current wave</div>
        <div className="gov-wave-name">{govGatesList.value.length > 0 ? 'Active' : 'No wave loaded'}</div>
        <div className="gate-nodes">
          {govGatesList.value.map((g, i) => {
            const state = gateUiState(g);
            const cls = state === "signed" ? "done" : state === "current" ? "active" : "locked";
            const code = gateCode(g, i);
            return <div key={code} className={`gate-node ${cls}`} title={g.name || code}>{code}</div>;
          })}
        </div>
        {govGatesList.value.length > 0 && (() => {
          const signed = govGatesList.value.filter(g => g.status === "signed" || g.signed).length;
          const total = govGatesList.value.length;
          return <div className="gate-node-tip">{signed} of {total} gates signed</div>;
        })()}
      </div>
      <div className="sb-label">Recent audit</div>
      
      {auditList.value.length > 0 ? auditList.value.map((entry, idx) => {
        const dot = entry.action?.includes("sign") ? "sign" : entry.action?.includes("build") ? "build" : entry.action?.includes("override") ? "override" : "build";
        return (
          <div className="audit-row" key={idx}>
            <div className={`audit-dot ${dot}`}></div>
            <div className="audit-tx">
              <div className="audit-action">{entry.action}</div>
              <div className="audit-meta">{entry.ts || entry.timestamp}</div>
            </div>
          </div>
        )
      }) : (
        <div className="audit-row">
          <div className="audit-dot build"></div>
          <div className="audit-tx">
            <div className="audit-action">No audit entries</div>
            <div className="audit-meta">System awaiting data</div>
          </div>
        </div>
      )}

      {hasSignalosDir.value && hasTestDebtData.value ? <TestDebtPanel /> : null}

    </div>
  </aside>
    </>
  );
}
