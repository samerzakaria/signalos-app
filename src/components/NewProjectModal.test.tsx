import { fireEvent, render, screen, waitFor } from '@testing-library/preact';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NewProjectModal } from './NewProjectModal';
import { modalOpen, selectedProductProfile, workspacePath } from '../state';

describe('NewProjectModal', () => {
  beforeEach(() => {
    modalOpen.value = 'newProjectModal';
    workspacePath.value = '';
    selectedProductProfile.value = 'generic';
  });

  it('uses the existing workspace picker for Browse and fills the path input', async () => {
    workspacePath.value = 'C:/current/product';
    window.pickWorkspaceFolder = vi.fn(async () => {
      workspacePath.value = 'C:/picked/new-product';
    });

    render(<NewProjectModal />);

    fireEvent.click(screen.getByLabelText('Browse project folder'));

    const pathInput = document.getElementById('newProjPath') as HTMLInputElement;
    await waitFor(() => expect(pathInput.value).toBe('C:/picked/new-product'));
    expect(window.pickWorkspaceFolder).toHaveBeenCalledTimes(1);
    expect(workspacePath.value).toBe('C:/current/product');
  });

  it('delegates creation to the app-level factory handler', () => {
    window.createProject = vi.fn();

    render(<NewProjectModal />);

    fireEvent.click(screen.getByRole('button', { name: /create project/i }));

    expect(window.createProject).toHaveBeenCalledTimes(1);
  });

  it('shows the product profile selector', () => {
    render(<NewProjectModal />);

    const profile = screen.getByLabelText('Product profile') as HTMLSelectElement;
    fireEvent.input(profile, { target: { value: 'react-vite' } });

    expect(selectedProductProfile.value).toBe('react-vite');
  });
});
