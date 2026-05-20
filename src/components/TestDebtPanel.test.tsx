import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/preact';
import { userName } from '../state';

// Mock ipc.js so the panel's network calls are entirely under test
// control. The panel calls ipc.testAutomation.listDebt() and
// ipc.testAutomation.readMutationScore() on mount, plus resolveDebt()
// on dismiss confirm.
vi.mock('../js/ipc.js', () => ({
  testAutomation: {
    listDebt: vi.fn(),
    resolveDebt: vi.fn(),
    readMutationScore: vi.fn(),
  },
  signal: {
    runAndWait: vi.fn(async () => 'ok'),
  },
}));

const ipc = await import('../js/ipc.js');
const { TestDebtPanel, __resetTestDebtPanelForTests } = await import('./TestDebtPanel');

function emptySummary() {
  return { entries: [], open_count: 0, resolved_count: 0 };
}

function entry(overrides: Partial<{
  ts: string; kind: string; area: string; title: string; detail: string; resolved: boolean;
}> = {}) {
  return {
    ts: '2026-05-20T10:00:00Z',
    kind: 'missing-test',
    area: 'src/foo.ts',
    title: 'Missing test for foo()',
    detail: 'No assertions cover the empty-list branch.',
    resolved: false,
    ...overrides,
  };
}

function mockListDebt(value: ReturnType<typeof emptySummary>) {
  (ipc.testAutomation.listDebt as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(value);
}

function mockMutationScore(value: { score: number | null; area: string; present: boolean; measured_at?: string | null; source?: string | null }) {
  (ipc.testAutomation.readMutationScore as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
    measured_at: null,
    source: null,
    ...value,
  });
}

describe('TestDebtPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetTestDebtPanelForTests();
    userName.value = 'Samer';
    mockListDebt(emptySummary());
    mockMutationScore({ score: 0.87, area: 'workspace', present: true });
    (ipc.testAutomation.resolveDebt as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(true);
  });

  it('renders empty state when the test-debt list is empty', async () => {
    render(<TestDebtPanel />);
    await waitFor(() => {
      expect(screen.getByTestId('td-empty')).toBeInTheDocument();
    });
    expect(
      screen.getByText(/No test debt — your test coverage is meeting policy/i),
    ).toBeInTheDocument();
    // The Dismiss button only renders for entries; empty state has none.
    expect(screen.queryByTestId('td-dismiss-btn')).toBeNull();
  });

  it('renders one card per entry when the list has 3 entries', async () => {
    mockListDebt({
      entries: [
        entry({ title: 'First debt', area: 'src/a.ts' }),
        entry({ title: 'Second debt', area: 'src/b.ts', kind: 'mutation-low' }),
        entry({ title: 'Third debt', area: 'src/c.ts', kind: 'manual-defect' }),
      ],
      open_count: 3,
      resolved_count: 0,
    });

    render(<TestDebtPanel />);

    await waitFor(() => {
      expect(screen.getAllByTestId('td-entry')).toHaveLength(3);
    });
    expect(screen.getByText('First debt')).toBeInTheDocument();
    expect(screen.getByText('Second debt')).toBeInTheDocument();
    expect(screen.getByText('Third debt')).toBeInTheDocument();
    // Each non-resolved entry exposes a Dismiss button.
    expect(screen.getAllByTestId('td-dismiss-btn')).toHaveLength(3);
  });

  it('clicking Dismiss opens a confirm modal; submitting calls resolveDebt with the reason', async () => {
    mockListDebt({
      entries: [entry({ title: 'Stale belief test', area: 'src/auth.ts' })],
      open_count: 1,
      resolved_count: 0,
    });

    render(<TestDebtPanel />);

    // Wait until the entry's Dismiss button is on screen.
    const dismissBtn = await screen.findByTestId('td-dismiss-btn');
    fireEvent.click(dismissBtn);

    // Modal opens with reason textarea.
    const modal = await screen.findByTestId('td-dismiss-modal');
    expect(modal).toBeInTheDocument();
    const reasonField = screen.getByTestId('td-dismiss-reason') as HTMLTextAreaElement;
    expect(reasonField).toBeInTheDocument();

    // Confirm is disabled while the reason is empty (audit-trail requires
    // a stated reason).
    const confirmBtn = screen.getByTestId('td-dismiss-confirm') as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);

    // Type a reason, confirm.
    fireEvent.input(reasonField, { target: { value: 'Replaced by integration test in PR #142' } });
    expect((screen.getByTestId('td-dismiss-confirm') as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByTestId('td-dismiss-confirm'));

    await waitFor(() => {
      expect(ipc.testAutomation.resolveDebt).toHaveBeenCalledTimes(1);
    });
    // The Rust signature accepts (title) only — audit metadata is persisted
    // via the audit:append IPC route (added in M2-a), not piggybacked on
    // resolveDebt. Verify both calls happen and the audit payload carries
    // the dismiss reason + identity.
    expect(ipc.testAutomation.resolveDebt).toHaveBeenCalledWith('Stale belief test');
    expect(ipc.signal.runAndWait).toHaveBeenCalledTimes(1);
    const [auditCmd, auditArgs] = (ipc.signal.runAndWait as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(auditCmd).toBe('audit:append');
    const payload = JSON.parse(auditArgs[0]);
    expect(payload).toMatchObject({
      action: 'test-debt-dismiss',
      title: 'Stale belief test',
      dismissed_by: 'Samer',
      dismiss_reason: 'Replaced by integration test in PR #142',
    });
  });

  it('displays the mutation score at the top with threshold comparison', async () => {
    mockListDebt(emptySummary());
    mockMutationScore({ score: 0.87, area: 'workspace', present: true });

    render(<TestDebtPanel />);

    const banner = await screen.findByTestId('td-mutation-banner');
    expect(banner).toBeInTheDocument();
    expect(banner.textContent || '').toMatch(/Mutation score:\s*87%/);
    expect(banner.textContent || '').toMatch(/threshold:\s*80%/);
  });
});
