import { signal } from '@preact/signals';
import { enforcementRules, enfOpen, tab, waveFrozen, mobileNavOpen, type EnfRule } from '../state';
import { statusForMode } from '../enforcementView';
import * as ipc from '../js/ipc.js';
import { topTabClass } from './viewShell';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';
import { NotificationsPopover } from './NotificationsPopover';
import { unreadCount, toggleNotifications } from '../services/notifications';

// #13 per-rule strict/warn/off toggles. Mode changes go through
// enforcement.setMode (Rust set_rule_mode). The update is optimistic; if the
// backend refuses (unknown rule, or an attempt to relax a core invariant),
// the row reverts and the refusal is shown in the popover.
const RULE_MODES = ['strict', 'warn', 'off'] as const;
const enfModeError = signal<string | null>(null);

export async function setRuleMode(rule: string, mode: string): Promise<void> {
  const prev = enforcementRules.value;
  const current = prev.find((r) => r.rule === rule);
  if (!current || current.mode === mode) return;
  enfModeError.value = null;
  // Optimistic: flip the row immediately so the toggle feels instant.
  enforcementRules.value = prev.map((r) =>
    r.rule === rule ? { ...r, mode, status: statusForMode(mode) } : r,
  );
  try {
    await ipc.enforcement.setMode(rule, mode);
  } catch (e: unknown) {
    // Backend refused (e.g. core invariant) — revert and surface the reason.
    enforcementRules.value = prev;
    enfModeError.value = e instanceof Error ? e.message : String(e);
  }
}

// Exposed for tests: clears the module-level error between renders.
export function __resetEnforcementToggleForTests(): void {
  enfModeError.value = null;
}

function RuleModeControl({ rule }: { rule: EnfRule }) {
  const id = rule.rule || '';
  if (rule.core) {
    return (
      <div
        className="rule-mode-lock"
        data-testid={`rule-lock-${id}`}
        title="Core invariant — cannot be relaxed. Use a governed override (with a reason) instead."
        style={{ marginLeft: 'auto', flexShrink: 0, color: 'var(--ink-3)', fontSize: '13px' }}
      >
        <i className="ti ti-lock"></i>
      </div>
    );
  }
  const active = rule.mode || 'strict';
  return (
    <div
      className="rule-mode-toggle"
      role="group"
      aria-label={`${rule.name || id} mode`}
      style={{ marginLeft: 'auto', flexShrink: 0, display: 'flex', gap: '2px', background: 'var(--surface-warm)', borderRadius: '99px', padding: '2px' }}
      onClick={(e) => e.stopPropagation()}
    >
      {RULE_MODES.map((m) => (
        <button
          key={m}
          type="button"
          data-testid={`rule-mode-${id}-${m}`}
          aria-pressed={active === m}
          title={`Set ${rule.name || id} to ${m}`}
          onClick={() => { void setRuleMode(id, m); }}
          style={{
            fontSize: '10px',
            fontWeight: active === m ? 700 : 500,
            padding: '2px 7px',
            borderRadius: '99px',
            background: active === m ? 'var(--surface)' : 'transparent',
            color: active === m ? 'var(--ink)' : 'var(--ink-3)',
            boxShadow: active === m ? 'var(--sh-sm)' : 'none',
          }}
        >
          {m}
        </button>
      ))}
    </div>
  );
}

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
    warroom: 'War Room',
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
                  <div className="rule-row" key={r.rule || i}>
                    <div className={`rule-ic ${icCls}`}><i className={`ti ${icIcon}`}></i></div>
                    <div className="rule-tx" style={{ minWidth: 0 }}>
                      <div className="rule-name">{r.name || r.rule || ''}</div>
                      <div className="rule-desc">{r.description || r.desc || ''}</div>
                    </div>
                    <RuleModeControl rule={r} />
                  </div>
                );
              })}
            </div>
            {enfModeError.value ? (
              <div
                data-testid="enf-mode-error"
                style={{ padding: '8px 16px', fontSize: '11.5px', background: 'var(--danger-soft)', color: 'var(--danger-deep)' }}
              >
                <i className="ti ti-alert-circle" style={{ verticalAlign: 'middle' }}></i>{' '}
                {enfModeError.value}
              </div>
            ) : null}
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

        <div
          className="ico"
          id="notifBell"
          style={{ 'position': 'relative' }}
          onClick={() => {
            // The legacy global (app-v2.js) reconciles the feed against the
            // audit trail on open; fall back to the service directly when it
            // isn't registered (tests / early boot).
            if (typeof window.showNotifications === 'function') window.showNotifications();
            else void toggleNotifications();
          }}
          aria-label="Notifications"
          data-testid="notif-bell"
        >
          <i className="ti ti-bell"></i>
          {unreadCount.value > 0 ? (
            <span className="badge" data-testid="notif-badge" title={`${unreadCount.value} unread`}></span>
          ) : null}
          <NotificationsPopover />
        </div>

        <div className="ico" onClick={() => window.shareProject()} aria-label="Share"><i className="ti ti-share-3"></i></div>
      </div>
    </header>
    </>
  );
}
