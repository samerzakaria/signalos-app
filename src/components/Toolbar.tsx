import { enforcementRules, enfOpen, tab, waveFrozen, mobileNavOpen } from '../state';
import { topTabClass } from './viewShell';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';

export function Toolbar() {
  const rules = enforcementRules.value;
  const warns = rules.filter((r) => r.status === 'warn').length;
  const errors = rules.filter((r) => r.status === 'blocked' || r.status === 'error').length;

  let pillCls = 'enf-pill ok';
  let pillIcon = 'ti-shield-check';
  let pillText = 'All clear';
  if (errors > 0) {
    pillCls = 'enf-pill blocked';
    pillIcon = 'ti-shield-off';
    pillText = `${errors} blocked`;
  } else if (warns > 0) {
    pillCls = 'enf-pill warn';
    pillIcon = 'ti-shield-half';
    pillText = `${warns} warning${warns > 1 ? 's' : ''}`;
  }

  const popoverCls = enfOpen.value ? 'enf-popover open' : 'enf-popover';
  const bannerCls = waveFrozen.value ? 'frozen-banner visible' : 'frozen-banner';
  const freezeLabel = waveFrozen.value ? 'Unfreeze wave' : 'Freeze wave';
  const freezeIcon = waveFrozen.value ? 'ti-sun' : 'ti-snowflake';
  const freezeHandler = waveFrozen.value ? () => window.unfreezeWave() : () => window.freezeWave();
  const activeTab = tab.value;
  const viewNames: Record<string, string> = {
    build: 'Build',
    preview: 'Preview',
    dashboard: 'Evidence',
    vault: 'Vault',
    settings: 'Settings',
    help: 'Help',
    history: 'History',
    brain: 'Brain',
  };

  const popSub = rules.length === 0
    ? 'No rules loaded'
    : `${rules.length} rule${rules.length > 1 ? 's' : ''} active${warns > 0 ? ` · ${warns} warning${warns > 1 ? 's' : ''}` : ''}${errors > 0 ? ` · ${errors} blocked` : ''}`;

  return (
    <>
<header className="toolbar">
      <button type="button" className="mobile-nav-toggle" aria-label="Toggle navigation" onClick={() => { mobileNavOpen.value = !mobileNavOpen.value; }}><i className="ti ti-menu-2"></i></button>
      <div className="crumb">
        <WorkspaceSwitcher />
        <i className="ti ti-chevron-right"></i>
        <span id="viewName">{viewNames[activeTab] || activeTab}</span>
      </div>
      <div className="seg">
        <div className={topTabClass('build')} data-tab="build" onClick={() => window.switchTab('build')}><i className="ti ti-message-circle-2"></i> Build</div>
        <div className={topTabClass('preview')} data-tab="preview" onClick={() => window.switchTab('preview')}><i className="ti ti-device-desktop"></i> Preview</div>
        <div className={topTabClass('dashboard')} data-tab="dashboard" onClick={() => window.switchTab('dashboard')}><i className="ti ti-clipboard-check"></i> Evidence</div>
      </div>
      <div className="toolbar-right">

        <div className={pillCls} id="enfPill" onClick={() => window.toggleEnfPopover()}>
          <i className={`ti ${pillIcon}`}></i> {pillText}

          <div className={popoverCls} id="enfPopover">
            <div className="enf-pop-head">
              <div className="enf-pop-title">Enforcement</div>
              <div className="enf-pop-sub">{popSub}</div>
            </div>
            <div id="enfRules">
              {rules.map((r, i) => {
                const ok = r.status === 'ok' || r.status === 'pass';
                const icCls = ok ? 'ok' : 'warn';
                const icIcon = ok ? 'ti-check' : 'ti-alert-triangle';
                return (
                  <div className="rule-row" key={i}>
                    <div className={`rule-ic ${icCls}`}><i className={`ti ${icIcon}`}></i></div>
                    <div className="rule-tx">
                      <div className="rule-name">{r.name || r.rule || ''}</div>
                      <div className="rule-desc">{r.description || r.desc || ''}</div>
                    </div>
                  </div>
                );
              })}
            </div>
            <div className={bannerCls} id="frozenBanner">
              <i className="ti ti-snowflake"></i>
              <span>Wave is frozen — no AI writes allowed</span>
              <button className="btn btn-soft" style={{ 'fontSize': '11px', 'padding': '5px 10px', 'flexShrink': '0' }} onClick={() => window.unfreezeWave()}>Unfreeze</button>
            </div>
            <div className="enf-pop-foot">
              <button className="btn btn-soft" id="freezeBtn" style={{ 'fontSize': '12px', 'padding': '8px 13px' }} onClick={freezeHandler}><i className={`ti ${freezeIcon}`}></i> {freezeLabel}</button>
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
