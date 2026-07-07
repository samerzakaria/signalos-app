// ProjectPicker.tsx — the sidebar project-namespace picker (#19).
//
// Lives inside the Projects panel, BELOW the workspace-folder list: workspaces
// are folders on disk, while these entries are project namespaces WITHIN the
// active workspace (registry: .signalos/projects.json). Switching retargets
// which project's wave/gate state the app reads and writes.

import {
  projectList,
  activeProjectId,
  projectPickerError,
  projectPickerBusy,
  newProjectName,
  switchProject,
  createProject,
} from '../services/projectPicker';

export function ProjectPicker() {
  const items = projectList.value;
  const active = activeProjectId.value;
  const busy = projectPickerBusy.value;
  const error = projectPickerError.value;

  const submitCreate = () => {
    void createProject(newProjectName.value);
  };

  return (
    <div data-testid="project-picker">
      <div className="sb-label">Project spaces</div>
      <div className="sb-projects-note" data-testid="project-picker-note">
        Spaces keep separate efforts apart inside this workspace — the folder
        stays the same.
      </div>
      {items.length > 0 ? (
        items.map((p) => {
          const isActive = p.id === active;
          return (
            <button
              type="button"
              key={p.id}
              className={isActive ? 'nav active' : 'nav'}
              title={isActive ? `${p.name} (active)` : `Switch to ${p.name}`}
              disabled={busy}
              onClick={() => { void switchProject(p.id); }}
              data-testid="project-picker-item"
              data-project-id={p.id}
            >
              <i className={`ti ${isActive ? 'ti-folder-check' : 'ti-folder'}`}></i>
              <span className="nav-text">{p.name}</span>
              {isActive ? <span className="nav-tag active-tag">Active</span> : null}
            </button>
          );
        })
      ) : (
        <div className="sb-empty">One space (default). Add another below.</div>
      )}
      <div className="sb-project-new">
        <input
          type="text"
          placeholder="+ New project space…"
          value={newProjectName.value}
          disabled={busy}
          data-testid="project-picker-new-input"
          onInput={(e) => { newProjectName.value = (e.target as HTMLInputElement).value; }}
          onKeyDown={(e) => { if (e.key === 'Enter') submitCreate(); }}
        />
        <button
          type="button"
          className="btn btn-soft"
          disabled={busy || !newProjectName.value.trim()}
          data-testid="project-picker-create"
          onClick={submitCreate}
          title="Create a project space in this workspace"
        >
          <i className="ti ti-plus"></i>
        </button>
      </div>
      {error ? (
        <div className="sb-project-error" data-testid="project-picker-error">
          <i className="ti ti-alert-circle"></i> {error}
        </div>
      ) : null}
    </div>
  );
}
