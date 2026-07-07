import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/preact';
import { BuildView } from './BuildView';
import {
  chatBubbles,
  chatInputValue,
  cmdPaletteOpen,
  userName,
  type PlanTask,
  type ChatBubble,
} from '../../state';

// BuildView is the chat surface and the plan-card landing. Three
// load-bearing behaviours:
//   1. User / AI / streaming / error / plan bubbles each render with
//      their distinguishing element so the user can tell them apart.
//   2. The plan card status -> CTA mapping (pending->Approve,
//      running->Cancel) drives the build flow.
//   3. The Approve button actually invokes window.approvePlan with the
//      bubble id (otherwise approving silently does nothing).
// window.{approvePlan, cancelWave, retryTask} are declared in
// src/global.d.ts; we just stub them with vi.fn() per test.

function makeTask(overrides: Partial<PlanTask> = {}): PlanTask {
  return {
    id: 'task-001',
    title: 'Implement feature',
    files: ['src/foo.ts'],
    tier: 'T2',
    effort_days: 0.5,
    status: 'pending',
    ...overrides,
  };
}

function makeBubble(overrides: Partial<ChatBubble> = {}): ChatBubble {
  return {
    id: 'bub-1',
    kind: 'ai',
    text: '',
    ...overrides,
  };
}

describe('BuildView chat bubbles', () => {
  beforeEach(() => {
    chatBubbles.value = [];
    chatInputValue.value = '';
    cmdPaletteOpen.value = false;
    userName.value = 'Test User';
  });

  it('renders a user bubble with the message text and user avatar initial', () => {
    chatBubbles.value = [makeBubble({ id: 'u1', kind: 'user', text: 'build me a todo app' })];
    render(<BuildView />);
    expect(screen.getByText('build me a todo app')).toBeInTheDocument();
    expect(screen.getByText('T')).toBeInTheDocument(); // "Test User" -> "T"
  });

  it('renders a streaming bubble with the stream-cursor element', () => {
    chatBubbles.value = [makeBubble({ id: 's1', kind: 'streaming', text: 'thinking…' })];
    const { container } = render(<BuildView />);
    expect(screen.getByText('thinking…')).toBeInTheDocument();
    expect(container.querySelector('.stream-cursor')).not.toBeNull();
  });

  it('renders an error bubble with the danger style hook', () => {
    chatBubbles.value = [makeBubble({ id: 'e1', kind: 'error', text: 'network down' })];
    const { container } = render(<BuildView />);
    expect(screen.getByText('network down')).toBeInTheDocument();
    // The error bubble has the alert-circle icon as the avatar marker.
    expect(container.querySelector('.ti-alert-circle')).not.toBeNull();
  });

  it('renders tool call bubbles inside the Build conversation', () => {
    chatBubbles.value = [
      makeBubble({
        id: 'tool-1',
        kind: 'tool',
        tool: {
          name: 'read_file',
          target: 'package.json',
          status: 'running',
          summary: 'Reading package.json',
        },
      }),
    ];

    render(<BuildView />);

    const bubble = screen.getByTestId('tool-call-bubble');
    expect(bubble.querySelector('.tool-call')?.getAttribute('data-tool')).toBe('read_file');
    expect(screen.getByText('Reading')).toBeInTheDocument();
    expect(screen.getByText('package.json')).toBeInTheDocument();
    expect(screen.getByText(/Reading package\.json/i)).toBeInTheDocument();
  });

  it('renders file diff bubbles inside the Build conversation', () => {
    chatBubbles.value = [
      makeBubble({
        id: 'diff-1',
        kind: 'diff',
        diff: {
          path: 'src/App.tsx',
          before: 'const title = "Old";',
          after: 'const title = "New";',
        },
      }),
    ];

    const { container } = render(<BuildView />);

    expect(screen.getByText('src/App.tsx')).toBeInTheDocument();
    expect(screen.getByTestId('file-diff-bubble')).toBeInTheDocument();
    expect(container.querySelector('.file-diff-line.add')).not.toBeNull();
    expect(container.querySelector('.file-diff-line.del')).not.toBeNull();
  });

  it('renders opened markdown files as readable documents', () => {
    chatBubbles.value = [
      makeBubble({
        id: 'file-md',
        kind: 'file',
        file: {
          path: 'docs/constitution.md',
          content: '# Product Constitution\n\n**Signed by:** PO',
          markdown: true,
        },
      }),
    ];

    const { container } = render(<BuildView />);

    expect(screen.getByTestId('file-viewer-bubble')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Product Constitution' })).toBeInTheDocument();
    expect(screen.getByText('document')).toBeInTheDocument();
    expect(container.querySelector('.file-viewer-code')).toBeNull();
  });

  it('renders opened non-markdown files as code', () => {
    chatBubbles.value = [
      makeBubble({
        id: 'file-code',
        kind: 'file',
        file: {
          path: 'src/App.tsx',
          content: 'const title = "SignalOS";',
        },
      }),
    ];

    const { container } = render(<BuildView />);

    expect(screen.getByTestId('file-viewer-bubble')).toBeInTheDocument();
    expect(container.querySelector('.file-viewer-code pre code')?.textContent)
      .toContain('const title = "SignalOS";');
  });

  it('renders markdown code blocks inside AI bubbles', () => {
    chatBubbles.value = [
      makeBubble({
        id: 'ai-code',
        kind: 'ai',
        text: '```ts\nconst answer = 42;\n```',
      }),
    ];

    const { container } = render(<BuildView />);

    const code = container.querySelector('pre code');
    expect(code).not.toBeNull();
    expect(code?.textContent).toContain('const answer = 42;');
    expect(screen.getByRole('button', { name: /copy code/i })).toBeInTheDocument();
  });

  it('renders the UX friction card before the gate review card (#12)', () => {
    chatBubbles.value = [
      makeBubble({
        id: 'fr-1',
        kind: 'friction',
        uxFriction: {
          gate: 'design',
          personas: [
            {
              persona: 'impatient',
              label: 'Impatient User',
              findings: [{ severity: 'high', issue: 'No loading state shown.' }],
            },
          ],
        },
      }),
      makeBubble({
        id: 'g-1',
        kind: 'gate',
        gateReview: {
          gate: 'design',
          title: 'Design review',
          question: 'Approve this direction?',
          resolvedVerdict: null,
        },
      }),
    ];
    const { container } = render(<BuildView />);
    const friction = screen.getByTestId('ux-friction-card');
    const gate = screen.getByTestId('gate-review-card');
    expect(friction).toBeInTheDocument();
    expect(gate).toBeInTheDocument();
    expect(screen.getByText('No loading state shown.')).toBeInTheDocument();
    // The friction report precedes the gate card so the human sees it
    // before signing.
    const all = Array.from(container.querySelectorAll('[data-testid="ux-friction-card"],[data-testid="gate-review-card"]'));
    expect(all[0]).toBe(friction);
    expect(all[1]).toBe(gate);
  });

  it('shows the command palette when slash mode is open', () => {
    cmdPaletteOpen.value = true;

    render(<BuildView />);

    expect(screen.getByText('Commands')).toBeInTheDocument();
    expect(screen.getByText('/signal-status')).toBeInTheDocument();
  });
});

describe('BuildView plan card status -> CTA', () => {
  beforeEach(() => {
    chatBubbles.value = [];
    userName.value = 'Test User';
    window.approvePlan = vi.fn();
    window.cancelWave = vi.fn();
    window.retryTask = vi.fn();
  });

  it('shows "Awaiting approval" and an Approve button for a pending plan', () => {
    chatBubbles.value = [
      makeBubble({
        id: 'p1',
        kind: 'plan',
        plan: [makeTask()],
        planStatus: 'pending',
      }),
    ];
    render(<BuildView />);
    expect(screen.getByText('Awaiting approval')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Approve.*run/i })).toBeInTheDocument();
  });

  it('clicking Approve invokes window.approvePlan with the bubble id', () => {
    chatBubbles.value = [
      makeBubble({ id: 'p-approve', kind: 'plan', plan: [makeTask()], planStatus: 'pending' }),
    ];
    render(<BuildView />);
    fireEvent.click(screen.getByRole('button', { name: /Approve.*run/i }));
    expect(window.approvePlan).toHaveBeenCalledWith('p-approve');
  });

  it('shows "Running" and a Cancel wave button for a running plan', () => {
    chatBubbles.value = [
      makeBubble({ id: 'p2', kind: 'plan', plan: [makeTask()], planStatus: 'running' }),
    ];
    render(<BuildView />);
    expect(screen.getByText('Running')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Cancel wave/i })).toBeInTheDocument();
  });

  it('shows a Retry button only for failed tasks, not for pending/running/done', () => {
    chatBubbles.value = [
      makeBubble({
        id: 'p3',
        kind: 'plan',
        planStatus: 'running',
        plan: [
          makeTask({ id: 't1', title: 'Pending task', status: 'pending' }),
          makeTask({ id: 't2', title: 'Done task', status: 'completed' }),
          makeTask({ id: 't3', title: 'Failed task', status: 'failed' }),
        ],
      }),
    ];
    render(<BuildView />);
    const retries = screen.getAllByRole('button', { name: /Retry/i });
    expect(retries).toHaveLength(1);
    fireEvent.click(retries[0]);
    expect(window.retryTask).toHaveBeenCalledWith('p3', 't3');
  });

  it('renders the per-task file list under the task title', () => {
    chatBubbles.value = [
      makeBubble({
        id: 'p4',
        kind: 'plan',
        planStatus: 'pending',
        plan: [makeTask({ id: 'tf', title: 'Edit files', files: ['src/a.ts', 'src/b.ts'] })],
      }),
    ];
    render(<BuildView />);
    expect(screen.getByText('src/a.ts · src/b.ts')).toBeInTheDocument();
  });

  it('shows the cost delta when a wave is completed and cost data is present', () => {
    chatBubbles.value = [
      makeBubble({
        id: 'p5',
        kind: 'plan',
        planStatus: 'completed',
        plan: [makeTask({ status: 'completed' })],
        costBefore: 0.5,
        costAfter: 0.5234,
      }),
    ];
    render(<BuildView />);
    expect(screen.getByText('$0.0234')).toBeInTheDocument();
  });
});
