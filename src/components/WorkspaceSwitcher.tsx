import { signal } from '@preact/signals';
import { workspacePath, recentWorkspaces, modalOpen, type RecentWorkspace } from '../state';

/** Local open/closed state for the dropdown. */
const wsDropdownOpen = signal<boolean>(false);

/** Derive workspace display name from a file-system path (cross-platform). */
function displayName(path: string): string {
  return String(path || '')
    .replace(/\\/g, '/')
    .split('/')
    .filter(Boolean)
    .pop() || 'No project';
}

/** Format last-opened timestamp into a short human label. */
function formatLastOpened(iso: string | undefined): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHrs = Math.floor(diffMins / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    const diffDays = Math.floor(diffHrs / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}

function toggleDropdown(e: MouseEvent) {
  e.stopPropagation();
  wsDropdownOpen.value = !wsDropdownOpen.value;
}

function switchTo(entry: RecentWorkspace) {
  wsDropdownOpen.value = false;
  const target = entry.path.trim();
  if (!target || target === workspacePath.value) return;
  if (typeof window.switchWorkspace === 'function') {
    try { void window.switchWorkspace(target); } catch {}
  }
}

function openNewProject() {
  wsDropdownOpen.value = false;
  modalOpen.value = 'newProjectModal';
  try { window.openNewProject?.(); } catch {}
}

// Close dropdown when clicking anywhere outside
if (typeof document !== 'undefined') {
  document.addEventListener('click', (e) => {
    const target = e.target as HTMLElement | null;
    if (!target?.closest?.('.ws-switcher')) {
      wsDropdownOpen.value = false;
    }
  });
}

export function WorkspaceSwitcher() {
  const ws = workspacePath.value;
  const name = displayName(ws);
  const recents = recentWorkspaces.value;
  const open = wsDropdownOpen.value;

  const dropdownCls = open ? 'ws-dropdown open' : 'ws-dropdown';

  return (
    <div className="ws-switcher">
      <button
        type="button"
        className="ws-trigger"
        onClick={toggleDropdown}
        aria-expanded={open}
        aria-haspopup="listbox"
        title={ws || 'No project selected'}
      >
        <i className="ti ti-folder"></i>
        <span className="ws-name">{name}</span>
        <i className={`ti ti-chevron-${open ? 'up' : 'down'} ws-caret`}></i>
      </button>

      <div className={dropdownCls} role="listbox">
        <div className="ws-dd-head">
          <span className="ws-dd-title">Projects</span>
        </div>
        <div className="ws-dd-list">
          {recents.length > 0 ? (
            recents.map((entry) => {
              const active = entry.path === ws;
              const itemCls = active ? 'ws-dd-item active' : 'ws-dd-item';
              return (
                <button
                  type="button"
                  className={itemCls}
                  key={entry.path}
                  onClick={() => switchTo(entry)}
                  role="option"
                  aria-selected={active}
                  title={entry.path}
                >
                  <i className={`ti ${active ? 'ti-folder-check' : 'ti-folder'}`}></i>
                  <div className="ws-dd-item-info">
                    <span className="ws-dd-item-name">{entry.name || displayName(entry.path)}</span>
                    <span className="ws-dd-item-path">{entry.path}</span>
                  </div>
                  <span className="ws-dd-item-time">{formatLastOpened(entry.last_opened)}</span>
                </button>
              );
            })
          ) : (
            <div className="ws-dd-empty">No recent projects</div>
          )}
        </div>
        <div className="ws-dd-foot">
          <button type="button" className="ws-dd-new" onClick={openNewProject}>
            <i className="ti ti-plus"></i> New project
          </button>
        </div>
      </div>
    </div>
  );
}
