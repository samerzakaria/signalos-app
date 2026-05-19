import { modalOpen } from '../state';

export function AddSecretModal() {
  const cls = modalOpen.value === 'addSecretModal' ? 'modal-overlay open' : 'modal-overlay';
  return (
    <>
<div className={cls} id="addSecretModal" onClick={(e) => window.closeAddSecret(e)}>
  <div className="modal" onClick={(e) => e.stopPropagation()}>
    <div className="modal-head">
      <h3>Add a secret</h3>
      <button className="ico" onClick={() => window.closeModal('addSecretModal')}><i className="ti ti-x"></i></button>
    </div>
    <div className="modal-body">
      <label className="field-label">Secret name</label>
      <input type="text" className="plain-input" id="newSecretName" placeholder="MY_SECRET_KEY" style={{ 'marginBottom': '14px', 'fontFamily': 'var(--f-mono)' }}/>
      <label className="field-label">Value</label>
      <div className="key-wrap" style={{ 'marginBottom': '14px' }}>
        <input type="password" className="key-input" id="newSecretValue" placeholder="paste value here…"/>
        <button className="key-tog"><i className="ti ti-eye"></i></button>
      </div>
      <label className="field-label">Store in</label>
      <select className="select-input" id="newSecretFile" style={{ 'marginBottom': '0' }}>
        <option value=".env.local">.env.local (recommended)</option>
        <option value=".env">.env</option>
        <option value=".env.development">.env.development</option>
        <option value=".env.production">.env.production</option>
      </select>
    </div>
    <div className="modal-foot">
      <button className="btn btn-ghost" onClick={() => window.closeModal('addSecretModal')}>Cancel</button>
      <button className="btn btn-primary" onClick={() => window.saveSecret()}>Seal secret <i className="ti ti-shield-check"></i></button>
    </div>
  </div>
</div>
    </>
  );
}
