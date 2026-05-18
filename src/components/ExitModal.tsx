import { h } from 'preact';

export function ExitModal() {
  return (
    <>
<div className="modal-overlay" id="exitModal" onClick={(e) => window.closeModal('exitModal')}>
  <div className="modal" onClick={(e) => window.event.stopPropagation()} style={{ 'width': '400px' }}>
    <div className="modal-head">
      <h3>Exit SignalOS?</h3>
      <button className="ico" onClick={(e) => window.closeModal('exitModal')}><i className="ti ti-x"></i></button>
    </div>
    <div className="modal-body">
      <p style={{ 'fontSize': '13.5px', 'color': 'var(--ink-2)', 'lineHeight': '1.65' }}>Your brain notes and audit trail are saved continuously. Clicking <strong style={{ 'color': 'var(--ink)' }}>Save &amp; exit</strong> flushes any in-progress buffers before closing.</p>
      <div className="exit-status" id="exitStatus" style={{ 'display': 'none' }}>
        <i className="ti ti-loader-2" style={{ 'animation': 'spin 1s linear infinite' }}></i>
        <span id="exitStatusTx">Saving project state…</span>
      </div>
    </div>
    <div className="modal-foot">
      <button className="btn btn-ghost" onClick={(e) => window.closeModal('exitModal')} id="exitCancelBtn">Cancel</button>
      <button className="btn btn-ghost" style={{ 'color': 'var(--ink-2)' }} onClick={(e) => window.exitApp(false)} id="exitRawBtn">Exit without saving</button>
      <button className="btn btn-primary" onClick={(e) => window.exitApp(true)} id="exitSaveBtn"><i className="ti ti-device-floppy"></i> Save &amp; exit</button>
    </div>
  </div>
</div>


<script src="./js/csp-bootstrap.js"></script>
<script type="module" src="./js/app-v2.js"></script>
    </>
  );
}
