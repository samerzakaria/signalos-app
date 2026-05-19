import { modalOpen } from '../state';

export function ExitModal() {
  const cls = modalOpen.value === 'exitModal' ? 'modal-overlay open' : 'modal-overlay';
  return (
    <>
<div className={cls} id="exitModal" onClick={() => window.closeModal('exitModal')}>
  <div className="modal" onClick={(e) => e.stopPropagation()} style={{ 'width': '400px' }}>
    <div className="modal-head">
      <h3>Exit SignalOS?</h3>
      <button className="ico" onClick={() => window.closeModal('exitModal')}><i className="ti ti-x"></i></button>
    </div>
    <div className="modal-body">
      <p style={{ 'fontSize': '13.5px', 'color': 'var(--ink-2)', 'lineHeight': '1.65' }}>Your brain notes and audit trail are saved continuously. Clicking <strong style={{ 'color': 'var(--ink)' }}>Save &amp; exit</strong> flushes any in-progress buffers before closing.</p>
      <div className="exit-status" id="exitStatus" style={{ 'display': 'none' }}>
        <i className="ti ti-loader-2" style={{ 'animation': 'spin 1s linear infinite' }}></i>
        <span id="exitStatusTx">Saving project state…</span>
      </div>
    </div>
    <div className="modal-foot">
      <button className="btn btn-ghost" onClick={() => window.closeModal('exitModal')} id="exitCancelBtn">Cancel</button>
      <button className="btn btn-ghost" style={{ 'color': 'var(--ink-2)' }} onClick={() => window.exitApp(false)} id="exitRawBtn">Exit without saving</button>
      <button className="btn btn-primary" onClick={() => window.exitApp(true)} id="exitSaveBtn"><i className="ti ti-device-floppy"></i> Save &amp; exit</button>
    </div>
  </div>
</div>
    </>
  );
}
