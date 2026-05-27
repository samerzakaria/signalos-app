import { modalOpen, productProfiles, projectsRoot, selectedProductProfile, workspacePath } from '../state';

async function browseNewProjectFolder() {
  const previousWorkspace = workspacePath.value;
  await window.pickWorkspaceFolder();
  const selectedPath = workspacePath.value;
  const input = document.getElementById('newProjPath') as HTMLInputElement | null;
  if (input && selectedPath) input.value = selectedPath;
  workspacePath.value = previousWorkspace;
}

export function NewProjectModal() {
  const cls = modalOpen.value === 'newProjectModal' ? 'modal-overlay open' : 'modal-overlay';
  return (
    <>
<div className={cls} id="newProjectModal" onClick={(e) => window.closeNewProject(e)}>
  <div className="modal proj-modal" onClick={(e) => e.stopPropagation()}>
    <div className="modal-head">
      <h3>New project</h3>
      <button className="ico" onClick={() => window.closeModal('newProjectModal')}><i className="ti ti-x"></i></button>
    </div>
    <div className="modal-body">
      <label className="field-label" htmlFor="newProjName">Project name</label>
      <input type="text" className="plain-input" placeholder="My awesome app" id="newProjName" style={{ 'marginBottom': '14px' }}/>
      <label className="field-label" htmlFor="newProjPath">Folder path <span style={{ 'fontWeight': '400', 'color': 'var(--ink-3)' }}>(optional)</span></label>
      <div style={{ 'display': 'flex', 'gap': '8px', 'marginBottom': '8px' }}>
        <input type="text" className="plain-input" placeholder={projectsRoot.value ? `${projectsRoot.value}\\<project-name>` : '~/projects/my-awesome-app'} id="newProjPath" style={{ 'fontFamily': 'var(--f-mono)', 'fontSize': '12px' }}/>
        <button className="btn btn-soft" onClick={() => browseNewProjectFolder()} title="Browse project folder" aria-label="Browse project folder" style={{ 'padding': '10px 13px', 'flexShrink': '0' }}><i className="ti ti-folder-open"></i></button>
      </div>
      <label className="field-label" htmlFor="newProjProfile">Product profile</label>
      <select className="select-input" id="newProjProfile" value={selectedProductProfile.value} onInput={(e) => { selectedProductProfile.value = (e.target as HTMLSelectElement).value; }} style={{ 'width': '100%', 'marginBottom': '8px' }}>
        {productProfiles.value.map((profile) => (
          <option key={profile.id} value={profile.id}>{profile.name}</option>
        ))}
      </select>
      <div className="hint"><i className="ti ti-info-circle"></i> Leave blank to create it under your projects root. SignalOS initializes each product folder separately.</div>
      <div className="hint" id="newProjStatus" role="status" aria-live="polite" style={{ 'marginTop': '10px' }}></div>
    </div>
    <div className="modal-foot">
      <button className="btn btn-ghost" onClick={() => window.closeModal('newProjectModal')}>Cancel</button>
      <button className="btn btn-primary" id="createProjectBtn" onClick={() => window.createProject()}>Create project <i className="ti ti-arrow-right"></i></button>
    </div>
  </div>
</div>
    </>
  );
}
