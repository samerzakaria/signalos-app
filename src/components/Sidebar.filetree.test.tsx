import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/preact';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  auditList,
  fileTreeEntries,
  govGatesList,
  recentWorkspaces,
  recentlyChangedFiles,
  sbTab,
  userName,
  userRole,
  workspacePath,
} from '../state';

// Claim 11b — directory rows must expand on click and lazily fetch their own
// children via list_workspace_dir(childPath), so nested generated files are
// browsable. Mock only project.listDir; keep every other ipc export real.
const listDir = vi.fn(async (path: string) => {
  if (path === 'src') {
    return [
      { name: 'components', path: 'src/components', kind: 'dir' },
      { name: 'App.tsx', path: 'src/App.tsx', kind: 'file' },
    ];
  }
  if (path === 'src/components') {
    return [{ name: 'Button.tsx', path: 'src/components/Button.tsx', kind: 'file' }];
  }
  return [];
});

vi.mock('../js/ipc.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../js/ipc.js')>();
  return { ...actual, project: { ...actual.project, listDir } };
});

const { Sidebar } = await import('./Sidebar');

describe('Sidebar file tree — expandable directories', () => {
  beforeEach(() => {
    cleanup();
    listDir.mockClear();
    sbTab.value = 'files';
    workspacePath.value = 'C:/Products/One';
    userName.value = 'Sam';
    userRole.value = 'PO';
    recentWorkspaces.value = [];
    recentlyChangedFiles.value = new Set();
    govGatesList.value = [];
    auditList.value = [];
    fileTreeEntries.value = [
      { name: 'src', path: 'src', kind: 'dir' },
      { name: 'README.md', path: 'README.md', kind: 'file' },
    ];
  });

  // Re-query the row each time — signal-driven re-renders can replace nodes.
  const rowFor = (name: string): HTMLElement =>
    screen.getByText(name).closest('[data-testid="ftree-dir"]') as HTMLElement;

  it('expands a directory on click, lazily fetching + rendering its children', async () => {
    render(<Sidebar />);

    expect(rowFor('src')).toBeTruthy();       // directories are clickable rows
    expect(screen.queryByText('App.tsx')).toBeNull(); // collapsed initially

    fireEvent.click(rowFor('src'));

    await waitFor(() => expect(screen.getByText('App.tsx')).toBeInTheDocument());
    expect(listDir).toHaveBeenCalledWith('src');
    expect(screen.getByText('components')).toBeInTheDocument();

    // Nested directory expands too (deep browse).
    fireEvent.click(rowFor('components'));
    await waitFor(() => expect(screen.getByText('Button.tsx')).toBeInTheDocument());
    expect(listDir).toHaveBeenCalledWith('src/components');

    // Collapsing hides the children again.
    fireEvent.click(rowFor('src'));
    await waitFor(() => expect(screen.queryByText('App.tsx')).toBeNull());

    // Re-expanding serves from cache (no refetch).
    listDir.mockClear();
    fireEvent.click(rowFor('src'));
    await waitFor(() => expect(screen.getByText('App.tsx')).toBeInTheDocument());
    expect(listDir).not.toHaveBeenCalled();
  });
});
