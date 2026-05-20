import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  gateActivities,
  gateCriteria,
  govGatesList,
  currentGateInfo,
  currentWaveSummary,
} from '../../../state';

// This test locks the M3 → dashboard.js → DashboardView vocabulary contract.
//
// status.py::build_status_json emits criterion status as 'passing|failing|pending'.
// DashboardView renders pills only for 'passed|failed|checking|waiting'.
// dashboard.js::loadDashboard translates at the boundary. If either side
// of the contract drifts, this test fails loudly instead of silently
// rendering the wrong pill.
//
// We don't mount the full DashboardView here — DashboardView.test.tsx
// already covers its rendering paths with the post-translation vocabulary.
// What's missing was a test that the translation itself happens. That's
// what this file pins.

vi.mock('../../ipc.js', () => ({
  wave: { get: vi.fn() },
  gates: { getAll: vi.fn() },
  provider: { getCost: vi.fn() },
}));

vi.mock('../../app-v2.js', () => ({
  loadEnforcement: vi.fn(async () => {}),
  updateCostDisplay: vi.fn(),
}));

// Import after mocks so the mocked modules are picked up.
const { loadDashboard } = await import('../dashboard.js');
const ipc = await import('../../ipc.js');

describe('loadDashboard: M3 vocabulary translation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    govGatesList.value = [];
    currentWaveSummary.value = null;
    currentGateInfo.value = null;
    gateActivities.value = [];
    gateCriteria.value = [];
  });

  it('translates passing → passed (renders as the green "Passed" pill in DashboardView)', async () => {
    (ipc.wave.get as ReturnType<typeof vi.fn>).mockResolvedValue({ number: 1, total_gates: 6 });
    (ipc.provider.getCost as ReturnType<typeof vi.fn>).mockResolvedValue(null);
    (ipc.gates.getAll as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: 2,
        name: 'Plan',
        status: 'current',
        activities: [],
        criteria: [{ name: 'writing-plans', status: 'passing', description: 'plans valid' }],
      },
    ]);
    await loadDashboard();
    expect(gateCriteria.value).toHaveLength(1);
    expect(gateCriteria.value[0].status).toBe('passed');
    expect(gateCriteria.value[0].name).toBe('writing-plans');
  });

  it('translates failing → failed (renders as red "Needs a fix" pill)', async () => {
    (ipc.wave.get as ReturnType<typeof vi.fn>).mockResolvedValue({ number: 1, total_gates: 6 });
    (ipc.provider.getCost as ReturnType<typeof vi.fn>).mockResolvedValue(null);
    (ipc.gates.getAll as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: 4,
        name: 'Build',
        status: 'current',
        activities: [],
        criteria: [{ name: 'test-generation', status: 'failing', description: 'tests missing' }],
      },
    ]);
    await loadDashboard();
    expect(gateCriteria.value[0].status).toBe('failed');
  });

  it('translates pending → waiting (renders as grey "Waiting" pill)', async () => {
    (ipc.wave.get as ReturnType<typeof vi.fn>).mockResolvedValue({ number: 1, total_gates: 6 });
    (ipc.provider.getCost as ReturnType<typeof vi.fn>).mockResolvedValue(null);
    (ipc.gates.getAll as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: 5,
        name: 'Ship',
        status: 'current',
        activities: [],
        criteria: [{ name: 'security-audit', status: 'pending', description: 'awaiting evidence' }],
      },
    ]);
    await loadDashboard();
    expect(gateCriteria.value[0].status).toBe('waiting');
  });

  it('handles a mix of all three M3 statuses in one gate', async () => {
    (ipc.wave.get as ReturnType<typeof vi.fn>).mockResolvedValue({ number: 1, total_gates: 6 });
    (ipc.provider.getCost as ReturnType<typeof vi.fn>).mockResolvedValue(null);
    (ipc.gates.getAll as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: 3,
        name: 'Design',
        status: 'current',
        activities: [],
        criteria: [
          { name: 'design', status: 'passing' },
          { name: 'security-audit', status: 'failing' },
          { name: 'comprehensive-code-review', status: 'pending' },
        ],
      },
    ]);
    await loadDashboard();
    const statuses = gateCriteria.value.map((c) => c.status);
    expect(statuses).toEqual(['passed', 'failed', 'waiting']);
  });

  it('preserves the criterion name and description through the translation', async () => {
    (ipc.wave.get as ReturnType<typeof vi.fn>).mockResolvedValue({ number: 1, total_gates: 6 });
    (ipc.provider.getCost as ReturnType<typeof vi.fn>).mockResolvedValue(null);
    (ipc.gates.getAll as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: 2,
        name: 'Plan',
        status: 'current',
        activities: [],
        criteria: [
          {
            name: 'writing-plans',
            description: 'PLAN.tasks.yaml schema valid',
            status: 'passing',
            evidence: '.signalos/skill-validation/W1/writing-plans.json',
          },
        ],
      },
    ]);
    await loadDashboard();
    const c = gateCriteria.value[0];
    expect(c.name).toBe('writing-plans');
    expect(c.description).toBe('PLAN.tasks.yaml schema valid');
    expect(c.evidence).toBe('.signalos/skill-validation/W1/writing-plans.json');
  });

  it('forwards activities array untouched (no translation needed — already in DashboardView vocabulary)', async () => {
    (ipc.wave.get as ReturnType<typeof vi.fn>).mockResolvedValue({ number: 1, total_gates: 6 });
    (ipc.provider.getCost as ReturnType<typeof vi.fn>).mockResolvedValue(null);
    (ipc.gates.getAll as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: 4,
        name: 'Build',
        status: 'current',
        activities: [
          { name: 't1', title: 't1', status: 'completed' },
          { name: 't2', title: 't2', status: 'in_progress' },
          { name: 't3', title: 't3', status: 'pending' },
        ],
        criteria: [],
      },
    ]);
    await loadDashboard();
    expect(gateActivities.value.map((a) => a.status)).toEqual([
      'completed',
      'in_progress',
      'pending',
    ]);
  });
});
