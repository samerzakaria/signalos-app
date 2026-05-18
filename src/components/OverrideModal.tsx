import { h } from 'preact';

export function OverrideModal() {
  return (
    <>
<div className="modal-overlay" id="overrideModal" onClick={(e) => window.closeModal('overrideModal')}>
  <div className="modal" onClick={(e) => window.event.stopPropagation()} style={{ 'width': '460px' }}>
    <div className="modal-head">
      <h3>Override a rule</h3>
      <button className="ico" onClick={(e) => window.closeModal('overrideModal')}><i className="ti ti-x"></i></button>
    </div>
    <div className="modal-body">
      <div className="override-rule">
        <i className="ti ti-alert-triangle"></i>
        <div className="override-rule-tx">
          <strong id="overrideRuleName">test-first</strong>
          <p id="overrideRuleDesc">A belief was added without a test. Overriding means you accept this risk.</p>
        </div>
      </div>
      <label className="field-label">Reason for override</label>
      <textarea className="plain-input" id="overrideReason" placeholder="Explain why this override is acceptable…" rows="3" style={{ 'resize': 'vertical', 'lineHeight': '1.5', 'marginBottom': '0' }}></textarea>
    </div>
    <div className="modal-foot">
      <button className="btn btn-ghost" onClick={(e) => window.closeModal('overrideModal')}>Cancel</button>
      <button className="btn btn-danger" onClick={(e) => window.confirmOverride()}><i className="ti ti-alert-triangle"></i> Override — I accept responsibility</button>
    </div>
  </div>
</div>
    </>
  );
}
