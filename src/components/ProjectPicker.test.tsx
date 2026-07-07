import { cleanup, fireEvent, render, screen } from '@testing-library/preact';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Component surface of the project-namespace picker (#19): active marking,
// switch-on-click, inline create, inline error rendering.

vi.mock('../js/ipc.js', () => ({
  signal: { runAndWait: vi.fn(), run: vi.fn() },
}));

const ipc = await import('../js/ipc.js');
const {
  projectList,
  activeProjectId,
  projectPickerError,
  newProjectName,
  __resetProjectPickerForTests,
} = await import('../services/projectPicker');
const { ProjectPicker } = await import('./ProjectPicker');

const runAndWait = ipc.signal.runAndWait as ReturnType<typeof vi.fn>;

beforeEach(() => {
  cleanup();
  runAndWait.mockReset();
  __resetProjectPickerForTests();
  projectList.value = [
    { id: 'default', name: 'Default' },
    { id: 'mobile-app', name: 'Mobile app' },
  ];
  activeProjectId.value = 'default';
});

describe('ProjectPicker', () => {
  it('lists projects with the active one marked', () => {
    render(<ProjectPicker />);
    const items = screen.getAllByTestId('project-picker-item');
    expect(items).toHaveLength(2);
    expect(items[0].className).toContain('active');
    expect(items[0].textContent).toContain('Active');
    expect(items[1].className).not.toContain('active');
    // One-line explainer distinguishing spaces from workspace folders.
    expect(screen.getByTestId('project-picker-note').textContent).toContain('inside this workspace');
  });

  it('switches on click', async () => {
    runAndWait.mockResolvedValue({ status: 'ok', active: 'mobile-app' });
    render(<ProjectPicker />);
    fireEvent.click(screen.getAllByTestId('project-picker-item')[1]);
    await Promise.resolve();
    expect(runAndWait).toHaveBeenCalledWith(
      'project:switch',
      [JSON.stringify({ project_id: 'mobile-app' })],
      expect.any(Number),
    );
  });

  it('creates from the inline input (button and Enter key)', async () => {
    runAndWait.mockResolvedValue({
      status: 'ok',
      project: { id: 'store', name: 'Store' },
      active: 'store',
    });
    render(<ProjectPicker />);
    const input = screen.getByTestId('project-picker-new-input') as HTMLInputElement;
    fireEvent.input(input, { target: { value: 'Store' } });
    expect(newProjectName.value).toBe('Store');
    fireEvent.keyDown(input, { key: 'Enter' });
    await Promise.resolve();
    expect(runAndWait).toHaveBeenCalledWith(
      'project:create',
      [JSON.stringify({ name: 'Store' })],
      expect.any(Number),
    );
  });

  it('disables the create button while the name is empty', () => {
    render(<ProjectPicker />);
    const btn = screen.getByTestId('project-picker-create') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('renders the delivery-active refusal inline', () => {
    projectPickerError.value = 'A delivery is running — finish or stop the running delivery first.';
    render(<ProjectPicker />);
    expect(screen.getByTestId('project-picker-error').textContent)
      .toContain('finish or stop the running delivery first');
  });
});
