import { userName, userRole, govGatesList, auditList } from '../state';

export function Sidebar() {
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
      <div className="sb-label">Active</div>
      <div className="nav active"><i className="ti ti-pizza"></i> My pizza game <span className="ct">3</span></div>
      <div style={{ 'padding': '2px 12px 7px 36px' }}><span style={{ 'fontSize': '10px', 'fontWeight': '700', 'color': 'var(--ink-3)', 'background': 'var(--surface)', 'border': '0.5px solid var(--line-2)', 'borderRadius': '4px', 'padding': '2px 6px', 'letterSpacing': '0.03em' }}>React · Vite</span></div>
      <div className="nav"><i className="ti ti-cat"></i> Cat sticker book</div>
      <div className="nav"><i className="ti ti-rocket"></i> Space quiz</div>
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
      <div className="ftree">
        <div className="ftree-item dir"><i className="ti ti-folder-open"></i> src/</div>
        <div className="ftree-item child"><i className="ti ti-file-code"></i> index.html <span className="diff-badge mod">M</span></div>
        <div className="ftree-item child"><i className="ti ti-file-code"></i> game.js <span className="diff-badge mod">M</span></div>
        <div className="ftree-item child"><i className="ti ti-file-code"></i> styles.css</div>
        <div className="ftree-item dir"><i className="ti ti-folder"></i> pizzas/</div>
        <div className="ftree-item child"><i className="ti ti-file-code"></i> recipes.js <span className="diff-badge new">N</span></div>
        <div className="ftree-item child"><i className="ti ti-file-code"></i> toppings.js <span className="diff-badge new">N</span></div>
        <div className="ftree-item dir"><i className="ti ti-folder"></i> .signalos/</div>
        <div className="ftree-item child" style={{ 'opacity': '0.5' }}><i className="ti ti-file"></i> brain.jsonl</div>
        <div className="ftree-item child" style={{ 'opacity': '0.5' }}><i className="ti ti-file"></i> AUDIT_TRAIL.jsonl</div>
        <div className="ftree-item"><i className="ti ti-file-code"></i> signalos.json</div>
        <div className="ftree-item"><i className="ti ti-file"></i> package.json</div>
      </div>
    </div>

    
    <div className="sb-panel" id="sb-gov">
      <div className="gov-wave">
        <div className="gov-wave-label">Current wave</div>
        <div className="gov-wave-name">Wave 1 · Foundation</div>
        <div className="gate-nodes">
          {govGatesList.value.length > 0 ? govGatesList.value.map((g, i) => {
            const cls = g.status === "signed" || g.signed ? "done" : g.status === "active" || g.is_current ? "active" : "locked";
            return <div key={i} className={`gate-node ${cls}`} title={g.name || "Gate " + (i + 1)}>G{i + 1}</div>;
          }) : (
            <>
              <div className="gate-node done" title="Gate 1 — Pick the idea">G1</div>
              <div className="gate-node done" title="Gate 2 — Sketch it out">G2</div>
              <div className="gate-node done" title="Gate 3 — Make the menu">G3</div>
              <div className="gate-node active" title="Gate 4 — Make the pizzas (current)">G4</div>
              <div className="gate-node locked" title="Gate 5 — Drag &amp; drop">G5</div>
              <div className="gate-node locked" title="Gate 6 — Count score">G6</div>
              <div className="gate-node locked" title="Gate 7 — Share it">G7</div>
            </>
          )}
        </div>
        <div className="gate-node-tip">Gate 4 of 7 · 3 of 5 checks passed</div>
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
