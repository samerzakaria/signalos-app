import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/preact';

// Mock ipc.js so the card's single IPC call (get_project_artifacts) is under
// test control.
vi.mock('../js/ipc.js', () => ({
  project: {
    artifacts: vi.fn(),
  },
}));

const ipc = await import('../js/ipc.js');
const { ProjectHealthCard, __resetProjectHealthForTests } = await import('./ProjectHealthCard');

const artifactsMock = ipc.project.artifacts as unknown as ReturnType<typeof vi.fn>;

// #14 — Project health card on the Dashboard. Load-bearing behaviours:
//   1. Each artifact from the payload renders with a green tick (present)
//      or grey dash (missing).
//   2. No workspace / IPC failure degrades to a "no active project" note.

function payload() {
  return {
    workspace: 'C:/projects/demo',
    initialized: true,
    artifacts: [
      { name: 'Runtime state', path: '.signalos', kind: 'folder', exists: true, detail: 'Local SignalOS runtime folder is present.' },
      { name: 'Wave plan', path: 'core/strategy/PLAN.md', kind: 'file', exists: true, detail: 'Project plan is present.' },
      { name: 'App manifest', path: 'package.json', kind: 'file', exists: false, detail: 'No dependency manifest found.' },
      { name: 'Project README', path: 'README.md', kind: 'file', exists: false, detail: 'No project README found.' },
    ],
  };
}

describe('ProjectHealthCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetProjectHealthForTests();
  });

  it('renders a tick for present artifacts and a dash for missing ones', async () => {
    artifactsMock.mockResolvedValue(payload());
    render(<ProjectHealthCard />);

    await waitFor(() => {
      expect(screen.getByTestId('project-health-list')).toBeInTheDocument();
    });

    expect(screen.getByText('Runtime state')).toBeInTheDocument();
    expect(screen.getByText('Wave plan')).toBeInTheDocument();
    expect(screen.getByText('App manifest')).toBeInTheDocument();
    expect(screen.getByText('core/strategy/PLAN.md')).toBeInTheDocument();

    expect(screen.getAllByTestId('artifact-present')).toHaveLength(2);
    expect(screen.getAllByTestId('artifact-missing')).toHaveLength(2);
    expect(screen.getByTestId('project-health-summary').textContent).toMatch(/2 of 4 artifacts present/);

    // Present rows use the check icon; missing rows the dash.
    const planRow = screen.getByTestId('project-health-core/strategy/PLAN.md');
    expect(planRow.querySelector('.ti-check')).not.toBeNull();
    const manifestRow = screen.getByTestId('project-health-package.json');
    expect(manifestRow.querySelector('.ti-minus')).not.toBeNull();
  });

  it('shows the no-active-project note when the IPC rejects (no workspace)', async () => {
    artifactsMock.mockRejectedValue(new Error('No workspace selected'));
    render(<ProjectHealthCard />);

    await waitFor(() => {
      expect(screen.getByTestId('project-health-empty')).toBeInTheDocument();
    });
    expect(screen.getByText(/No active project/)).toBeInTheDocument();
    expect(screen.queryByTestId('project-health-list')).toBeNull();
  });

  it('treats an empty artifacts payload as no active project', async () => {
    artifactsMock.mockResolvedValue({ workspace: '', initialized: false, artifacts: [] });
    render(<ProjectHealthCard />);

    await waitFor(() => {
      expect(screen.getByTestId('project-health-empty')).toBeInTheDocument();
    });
  });
});
