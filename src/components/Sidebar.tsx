import { userName, userRole, govGatesList, auditList, fileTreeEntries, recentlyChangedFiles, workspacePath } from '../state';

export function Sidebar() {
  const tree = fileTreeEntries.value;
  const flashed = recentlyChangedFiles.value;
  const ws = workspacePath.value;
  return (
    <>
<aside className="sidebar">
    <div className="sb-head">
      <div className="sb-mark">
        <svg width="18" height="18" viewBox="0 0 32 32" fill="none"><circle cx="16" cy="16" r="3.7" fill="currentColor"/><path d="M20.24 9.22 A8 8 0 0 1 20.24 22.78" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"/><path d="M11.76 9.22 A8 8 0 0 0 11.76 22.78" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"/><path d="M22.89 4.98 A13 13 0 0 1 22.89 27.02" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"/><path d="M9.11 4.98 A13 13 0 0 0 9.11 27.02" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"/></svg>
      </div>
      <span className="sb-name">SignalOS <span className="pro">PRO</span></span>
    </div>

    
    <div className="sb-tabs">
      <div className="sb-tab active" onClick={() => window.switchSbTab('projects')}>Projects</div>
      <div className="sb-tab" onClick={() => window.switchSbTab('files')}>Files</div>
      <div className="sb-tab" onClick={() => window.switchSbTab('gov')}>Gov</div>
    </div>

    
    <div className="sb-panel active" id="sb-projects">
      <div className="nav accent" onClick={() => window.openNewProject()}><i className="ti ti-plus"></i> New project</div>
      <div className="sb-label">Tools</div>
      <div className="nav" onClick={() => window.switchTab('vault')}><i className="ti ti-shield-lock"></i> Vault</div>
      <div className="nav" onClick={() => window.switchTab('brain')}><i className="ti ti-brain"></i> Brain</div>
      <div className="nav" onClick={() => window.switchTab('history')}><i className="ti ti-history"></i> History</div>
      <div className="sb-label">Account</div>
      <div className="nav" onClick={() => window.switchTab('settings')}><i className="ti ti-settings"></i> Settings</div>
      <div className="nav" onClick={() => window.switchTab('help')}><i className="ti ti-help-circle"></i> Help</div>
      <div className="sb-user" style={{ 'marginTop': '8px' }}>
        <div className="sb-av" id="sbAvatar">{userName.value ? userName.value[0].toUpperCase() : 'D'}</div>
        <div className="sb-ui">
          <div className="sb-un" id="sbUserName">{userName.value || 'Developer'}</div>
          <div className="sb-us" id="sbUserRole">{userRole.value || 'Local account'}</div>
        </div>
      </div>
    </div>

    
    <div className="sb-panel" id="sb-files">
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

    
    <div className="sb-panel" id="sb-gov">
      <div className="gov-wave">
        <div className="gov-wave-label">Current wave</div>
        <div className="gov-wave-name">{govGatesList.value.length > 0 ? 'Active' : 'No wave loaded'}</div>
        <div className="gate-nodes">
          {govGatesList.value.map((g, i) => {
            const cls = g.status === "signed" || g.signed ? "done" : g.status === "active" || g.is_current ? "active" : "locked";
            return <div key={i} className={`gate-node ${cls}`} title={g.name || "Gate " + (i + 1)}>G{i + 1}</div>;
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

    </div>
  </aside>
    </>
  );
}
