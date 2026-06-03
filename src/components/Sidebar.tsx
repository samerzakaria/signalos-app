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
} from '../state';
import { gateCode, gateUiState } from './GateTimeline';
import { TestDebtPanel } from './TestDebtPanel';
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

export function Sidebar() {
  const tree = fileTreeEntries.value;
  const flashed = recentlyChangedFiles.value;
  const ws = workspacePath.value;
  const recents = recentWorkspaces.value;

  // Probe test-debt availability whenever workspace changes
  ensureTestDebtProbed(ws);

  const switchPanel = (id: string) => {
    sbTab.value = id;
    try { window.switchSbTab?.(id); } catch {}
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
      <div className="sb-label">Tools</div>
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
          tree.map((entry) => {
            const isDir = entry.kind === 'dir';
            const cls = 'ftree-item' + (isDir ? ' dir' : '');
            const icon = isDir ? 'ti-folder' : 'ti-file-code';
            const recently = flashed.has(entry.path) || flashed.has(entry.name);
            return (
              <div className={cls} key={entry.path || entry.name}>
                <i className={`ti ${icon}`}></i> {entry.name}
                {recently ? <span className="diff-badge new">NEW</span> : null}
              </div>
            );
          })
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
