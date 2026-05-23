import { fireEvent, render, screen } from '@testing-library/preact';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  productProfiles,
  recentWorkspaces,
  selectedProductProfile,
  workspacePath,
} from '../../state';
import { SettingsView } from './SettingsView';

describe('SettingsView workspace controls', () => {
  beforeEach(() => {
    workspacePath.value = 'C:/Products/One';
    selectedProductProfile.value = 'generic';
    productProfiles.value = [
      { id: 'generic', name: 'Generic Product Repo' },
      { id: 'react-vite', name: 'React + Vite' },
    ];
    recentWorkspaces.value = [
      {
        path: 'C:/Products/One',
        name: 'One',
        initialized: true,
        exists: true,
        is_directory: true,
        profile_id: 'generic',
      },
      {
        path: 'C:/Products/Two',
        name: 'Two',
        initialized: true,
        exists: true,
        is_directory: true,
        profile_id: 'react-vite',
      },
    ];
    window.switchWorkspace = vi.fn();
    window.changeStack = vi.fn();
  });

  it('renders recent product switcher and switches inactive products', () => {
    render(<SettingsView />);

    fireEvent.click(screen.getByRole('button', { name: /two/i }));

    expect(window.switchWorkspace).toHaveBeenCalledWith('C:/Products/Two');
  });

  it('updates selected product profile for profile-aware preview defaults', () => {
    render(<SettingsView />);

    fireEvent.input(screen.getByDisplayValue('Generic Product Repo'), {
      target: { value: 'react-vite' },
    });
    fireEvent.change(screen.getByDisplayValue('React + Vite'));

    expect(selectedProductProfile.value).toBe('react-vite');
    expect(window.changeStack).toHaveBeenCalled();
  });
});
