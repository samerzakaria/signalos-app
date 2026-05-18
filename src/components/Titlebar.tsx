import { h } from 'preact';

export function Titlebar() {
  return (
    <>
<div className="titlebar" data-tauri-drag-region>
  <div className="traffic">
    <span className="tl tl-red"><i className="ti ti-x"></i></span>
    <span className="tl tl-yellow"><i className="ti ti-minus"></i></span>
    <span className="tl tl-green"><i className="ti ti-arrows-diagonal"></i></span>
  </div>
  <div className="titlebar-center">
    <div className="cmd-pill" onClick={() => window.openSearch()}>
      <i className="ti ti-search"></i>
      <span>Search projects or ask SignalOS…</span>
      <span className="kbd">⌘K</span>
    </div>
  </div>
  <div className="titlebar-right">
    <div className="cost-pill" title="Session spend · $50/mo cap" style={{ 'borderRadius': '7px', 'padding': '6px 10px' }}><i className="ti ti-coin"></i> <span id="costDisplay">—</span></div>
    <div className="tb-divider"></div>
    <div className="live-badge" id="liveBadge" style={{ 'borderRadius': '7px' }}><span className="dot"></span> <span id="provDisplay">Loading…</span></div>
    <div className="tb-divider" style={{ 'margin': '0 6px 0 10px' }}></div>
    <button className="tb-btn tb-close" aria-label="Close SignalOS" onClick={() => window.openExit()} title="Close SignalOS"><i className="ti ti-x"></i></button>
  </div>
</div>
    </>
  );
}
