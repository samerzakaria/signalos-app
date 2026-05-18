export function NewProjectModal() {
  return (
    <>
<div className="modal-overlay" id="newProjectModal" onClick={(e) => window.closeNewProject(e)}>
  <div className="modal proj-modal" onClick={(e) => e.stopPropagation()}>
    <div className="modal-head">
      <h3>New project</h3>
      <button className="ico" onClick={() => window.closeModal('newProjectModal')}><i className="ti ti-x"></i></button>
    </div>
    <div className="modal-body">
      <label className="field-label">Project name</label>
      <input type="text" className="plain-input" placeholder="My awesome app" id="newProjName" style={{ 'marginBottom': '14px' }}/>
      <label className="field-label">Folder path</label>
      <div style={{ 'display': 'flex', 'gap': '8px', 'marginBottom': '8px' }}>
        <input type="text" className="plain-input" placeholder="~/projects/my-awesome-app" id="newProjPath" style={{ 'fontFamily': 'var(--f-mono)', 'fontSize': '12px' }}/>
        <button className="btn btn-soft" style={{ 'padding': '10px 13px', 'flexShrink': '0' }}><i className="ti ti-folder-open"></i></button>
      </div>
      <div className="hint"><i className="ti ti-info-circle"></i> SignalOS will create a <code style={{ 'fontFamily': 'var(--f-mono)', 'fontSize': '11px' }}>.signalos/</code> folder inside it</div>
    </div>
    <div className="modal-foot">
      <button className="btn btn-ghost" onClick={() => window.closeModal('newProjectModal')}>Cancel</button>
      <button className="btn btn-primary" onClick={() => window.createProject()}>Create project <i className="ti ti-arrow-right"></i></button>
    </div>
  </div>
</div>
    </>
  );
}
