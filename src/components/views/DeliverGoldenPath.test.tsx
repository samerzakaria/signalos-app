// Golden installed-app delivery path.
//
// This is the contract for the non-technical user journey after onboarding:
// projects root is already chosen, user provides only a product request, and
// SignalOS creates/selects a product repo before running the governed delivery.

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/preact';

vi.mock('../../js/ipc.js', () => ({
  onSidecarProgress: vi.fn(() => () => {}),
  signal: { runAndWait: vi.fn() },
  identity: { set: vi.fn() },
  workspace: {
    set: vi.fn(),
    ensureDefault: vi.fn(async (name: string, root: string) => `${root}/${name}`),
  },
}));

const ipc = await import('../../js/ipc.js');
const { projectsRoot, workspacePath } = await import('../../state');
const { DeliverView } = await import('./DeliverView');

function mockRunOnce(payload: unknown) {
  (ipc.signal.runAndWait as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
    JSON.stringify(payload),
  );
}

describe('Deliver golden installed-app path', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    projectsRoot.value = '';
    workspacePath.value = '';
    localStorage.clear();
    localStorage.setItem(
      'signalos.onboarding.wizard.v1',
      JSON.stringify({ projectsRoot: 'C:/SignalOS Products' }),
    );
  });

  it('turns a minimum product prompt into a governed real product repo flow', async () => {
    mockRunOnce({
      intent: {
        product_name: 'TeamOps',
        product_type: 'task-management',
        entities: ['Task', 'Team member', 'KPI'],
        primary_workflows: ['Assign tasks', 'Balance workload', 'Track KPIs'],
        ux_surfaces: ['Dashboard', 'Task board', 'Workload report'],
      },
      questions: ['Which frontend framework should be used?'],
      assumptions: ['SignalOS will choose the technical stack and design system.'],
      blueprint_id: 'task-management',
    });
    mockRunOnce({
      design: {
        ui_library: 'Accessible component system',
        ui_reason: 'Best fit for operational dashboards.',
        tokens: { color: '#2563eb', typography: 'Inter' },
        state_management: 'Governed product state',
        data_layer: 'Generated product API',
        form_handling: 'Validated forms',
      },
      profile: 'react-vite',
    });
    mockRunOnce({ preview_html: '<html><body>Team workload dashboard</body></html>' });
    mockRunOnce({
      name: 'TeamOps',
      profile: 'react-vite',
      blueprint: 'task-management',
      closure_level: 'ready',
      files_count: 24,
      workspace: { repo_root: 'C:/SignalOS Products/task-management-system' },
      how_to_run: ['npm install', 'npm run dev'],
      tests_executed: [{ name: 'unit', status: 'passed' }],
      security: { status: 'passed' },
      limitations: [],
    });

    render(<DeliverView />);

    fireEvent.input(screen.getByTestId('deliver-prompt-input'), {
      target: {
        value: "I want to do a task management system to manage my team's tasks, utilization, workload and their KPIs",
      },
    });
    fireEvent.click(screen.getByTestId('deliver-start-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-intent')).toBeInTheDocument();
    });
    expect(screen.queryByText('Which frontend framework should be used?')).not.toBeInTheDocument();
    expect(screen.getByTestId('deliver-technical-decisions')).toHaveTextContent(
      'Frontend and visual implementation choices',
    );

    fireEvent.click(screen.getByTestId('deliver-continue-btn'));
    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-design')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('deliver-approve-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-closeout')).toBeInTheDocument();
    });

    expect(ipc.workspace.ensureDefault).toHaveBeenCalledWith(
      'task-management-system',
      'C:/SignalOS Products',
    );
    expect(ipc.workspace.set).toHaveBeenCalledWith('C:/SignalOS Products/task-management-system');

    const calls = (ipc.signal.runAndWait as unknown as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls.map((call) => call[0])).toEqual([
      'deliver-intent',
      'deliver-design',
      'deliver-design-preview',
      'deliver',
    ]);
    expect(calls[1][1]).toContain('--profile');
    expect(calls[1][1]).toContain('auto');
    expect(calls[3][1]).toContain('--profile');
    expect(calls[3][1]).toContain('auto');
    expect(calls[3][1]).toContain('--repo-root');
    expect(screen.getByTestId('deliver-repo-path')).toHaveTextContent(
      'C:/SignalOS Products/task-management-system',
    );
  });

  it('lets SignalOS choose a non-UI product path instead of forcing React/Vite', async () => {
    mockRunOnce({
      intent: {
        product_name: 'Checksum Engine',
        product_type: 'custom',
        entities: ['UploadedFile', 'Checksum'],
        primary_workflows: ['Validate uploaded files'],
        ux_surfaces: [],
        stack_preferences: ['python'],
      },
      questions: ['Which frontend framework should be used?'],
      assumptions: ['SignalOS will choose the runtime and packaging path.'],
      blueprint_id: null,
    });
    mockRunOnce({
      design: {
        profile: 'generic',
        ui_library: { name: '', reason: 'Non-UI profile' },
        design_tokens: { primary_color: '#3b82f6', font_family: 'Inter', spacing_unit: 8 },
        state_management: { name: '', reason: 'Non-UI profile' },
        data_layer: { name: '', reason: 'Non-UI profile' },
        form_handling: { name: '', reason: 'Non-UI profile' },
      },
      profile: 'generic',
    });
    mockRunOnce({ preview_html: '<html><body>Should not render for generic profile</body></html>' });
    mockRunOnce({
      name: 'Checksum Engine',
      profile: 'generic',
      blueprint: null,
      closure_level: 'ready',
      files_count: 8,
      workspace: { repo_root: 'C:/SignalOS Products/Python-checksum-library' },
      how_to_run: ['python -m unittest discover -s tests'],
      tests_executed: [{ name: 'unit', status: 'passed' }],
      security: { status: 'passed' },
      limitations: [],
    });

    render(<DeliverView />);

    fireEvent.input(screen.getByTestId('deliver-prompt-input'), {
      target: {
        value: 'Build a Python checksum library for validating uploaded files',
      },
    });
    fireEvent.click(screen.getByTestId('deliver-start-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-intent')).toBeInTheDocument();
    });
    expect(screen.queryByText('Which frontend framework should be used?')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('deliver-continue-btn'));
    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-design')).toBeInTheDocument();
    });

    expect(screen.getByTestId('deliver-non-ui-plan')).toHaveTextContent('service, API, or library');
    expect(screen.queryByTestId('deliver-design-advanced')).not.toBeInTheDocument();
    expect(screen.queryByText(/React Hook Form/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId('deliver-design-preview')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('deliver-approve-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('deliver-step-closeout')).toBeInTheDocument();
    });

    expect(ipc.workspace.ensureDefault).toHaveBeenCalledWith(
      'Python-checksum-library',
      'C:/SignalOS Products',
    );

    const calls = (ipc.signal.runAndWait as unknown as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls.map((call) => call[0])).toEqual([
      'deliver-intent',
      'deliver-design',
      'deliver-design-preview',
      'deliver',
    ]);
    expect(calls[1][1]).toContain('--profile');
    expect(calls[1][1]).toContain('auto');
    expect(calls[3][1]).toContain('--profile');
    expect(calls[3][1]).toContain('auto');
    expect(screen.getByTestId('deliver-repo-path')).toHaveTextContent(
      'C:/SignalOS Products/Python-checksum-library',
    );
  });
});
