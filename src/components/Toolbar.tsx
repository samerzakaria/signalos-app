export function Toolbar() {
  return (
    <>
<header className="toolbar">
      <div className="crumb">
        <i className="ti ti-pizza"></i>
        <strong>My pizza game</strong>
        <i className="ti ti-chevron-right"></i>
        <span id="viewName">Dashboard</span>
      </div>
      <div className="seg">
        <div className="seg-i" data-tab="build" onClick={() => window.switchTab('build')}><i className="ti ti-message-circle-2"></i> Build</div>
        <div className="seg-i" data-tab="preview" onClick={() => window.switchTab('preview')}><i className="ti ti-device-desktop"></i> Preview</div>
        <div className="seg-i" data-tab="terminal" onClick={() => window.switchTab('terminal')}><i className="ti ti-terminal-2"></i> Terminal</div>
        <div className="seg-i active" data-tab="dashboard" onClick={() => window.switchTab('dashboard')}><i className="ti ti-layout-dashboard"></i> Dashboard</div>
      </div>
      <div className="toolbar-right">
        
        <div className="enf-pill warn" id="enfPill" onClick={() => window.toggleEnfPopover()}>
          <i className="ti ti-shield-half"></i> 1 warning
          
          <div className="enf-popover" id="enfPopover">
            <div className="enf-pop-head">
              <div className="enf-pop-title">Enforcement · Wave 1</div>
              <div className="enf-pop-sub">5 rules active · 1 warning</div>
            </div>
            <div id="enfRules"><div className="rule-row">
              <div className="rule-ic ok"><i className="ti ti-check"></i></div>
              <div className="rule-tx"><div className="rule-name">Gate-gating</div><div className="rule-desc">Build blocked until gates are signed</div></div>
            </div>
            <div className="rule-row">
              <div className="rule-ic ok"><i className="ti ti-check"></i></div>
              <div className="rule-tx"><div className="rule-name">Plan-gating</div><div className="rule-desc">No file writes without a plan</div></div>
            </div>
            <div className="rule-row">
              <div className="rule-ic ok"><i className="ti ti-check"></i></div>
              <div className="rule-tx"><div className="rule-name">Secret-block</div><div className="rule-desc">Secrets never sent to AI</div></div>
            </div>
            <div className="rule-row">
              <div className="rule-ic warn"><i className="ti ti-alert-triangle"></i></div>
              <div className="rule-tx"><div className="rule-name">Test-first</div><div className="rule-desc">1 belief missing a test — tap to override</div></div>
            </div>
            <div className="rule-row">
              <div className="rule-ic ok"><i className="ti ti-check"></i></div>
              <div className="rule-tx"><div className="rule-name">Zero manual regression</div><div className="rule-desc">All defects have automated tests</div></div>
            </div>
            </div>
            <div className="frozen-banner" id="frozenBanner">
              <i className="ti ti-snowflake"></i>
              <span>Wave is frozen — no AI writes allowed</span>
              <button className="btn btn-soft" style={{ 'fontSize': '11px', 'padding': '5px 10px', 'flexShrink': '0' }} onClick={() => window.unfreezeWave()}>Unfreeze</button>
            </div>
            <div className="enf-pop-foot">
              <button className="btn btn-soft" id="freezeBtn" style={{ 'fontSize': '12px', 'padding': '8px 13px' }} onClick={() => window.freezeWave()}><i className="ti ti-snowflake"></i> Freeze wave</button>
              <button className="btn btn-danger" style={{ 'fontSize': '12px', 'padding': '8px 13px', 'marginLeft': 'auto' }} onClick={() => window.openOverride()}><i className="ti ti-alert-triangle"></i> Override</button>
            </div>
          </div>
        </div>
        
        <div className="ico" style={{ 'position': 'relative' }} onClick={() => window.showNotifications()} aria-label="Notifications">
          <i className="ti ti-bell"></i>
          <span className="badge"></span>
        </div>
        
        <div className="ico" onClick={() => window.shareProject()} aria-label="Share"><i className="ti ti-share-3"></i></div>
      </div>
    </header>
    </>
  );
}
