import { cleanup, fireEvent, render, screen } from '@testing-library/preact';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  appVisible,
  auditList,
  fileTreeEntries,
  govGatesList,
  modalOpen,
  onboardingVisible,
  recentWorkspaces,
  recentlyChangedFiles,
  sbTab,
  tab,
  userName,
  userRole,
  workspacePath,
} from '../state';
import { Sidebar } from './Sidebar';

describe('Sidebar navigation', () => {
  beforeEach(() => {
    cleanup();
    appVisible.value = true;
    onboardingVisible.value = false;
    tab.value = 'dashboard';
    sbTab.value = 'projects';
    modalOpen.value = null;
    workspacePath.value = 'C:/Products/One';
    userName.value = 'Samer';
    userRole.value = 'PO';
    recentWorkspaces.value = [];
    fileTreeEntries.value = [];
    recentlyChangedFiles.value = new Set();
    govGatesList.value = [];
    auditList.value = [];
    delete (window as any).switchTab;
    delete (window as any).switchSbTab;
    delete (window as any).openNewProject;
    delete (window as any).switchWorkspace;
  });

  it('opens tool views even when legacy global navigation is unavailable', () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByText('Vault'));

    expect(tab.value).toBe('vault');
  });

  it('opens the new project modal directly from the Projects panel', () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByTestId('sidebar-new-project'));

    expect(modalOpen.value).toBe('newProjectModal');
  });

  it('renders recent projects as reachable buttons', () => {
    recentWorkspaces.value = [
      { name: 'One', path: 'C:/Products/One', exists: true, initialized: true },
      { name: 'Two', path: 'C:/Products/Two', exists: true, initialized: true },
    ];
    render(<Sidebar />);

    fireEvent.click(screen.getByText('Two'));

    expect(workspacePath.value).toBe('C:/Products/Two');
    expect(tab.value).toBe('dashboard');
  });

  it('uses the real workspace switch handler when it is registered', () => {
    const switchWorkspace = vi.fn();
    (window as any).switchWorkspace = switchWorkspace;
    recentWorkspaces.value = [
      { name: 'Two', path: 'C:/Products/Two', exists: true, initialized: true },
    ];
    render(<Sidebar />);

    fireEvent.click(screen.getByText('Two'));

    expect(switchWorkspace).toHaveBeenCalledWith('C:/Products/Two');
  });
});
