import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/preact';
import { chatBubbles, userName, type ChatBubble } from '../../state';

// Claim 10 (front-end): the gate card must not be optimistic-and-stuck. On a
// backend refusal the card reverts to un-resolved (controls re-enabled) and a
// visible error is surfaced; on success it stays resolved. The verdict
// submission is mocked here — its routing/awaited behaviour is covered in
// agentEvents.test.ts.

const submitGateVerdict = vi.fn();
vi.mock('../../services/agentEvents', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/agentEvents')>();
  return { ...actual, submitGateVerdict: (...args: unknown[]) => submitGateVerdict(...args) };
});

// Import AFTER the mock is registered.
const { BuildView } = await import('./BuildView');

function gateBubble(id: string): ChatBubble {
  return {
    id,
    kind: 'gate',
    text: '',
    gateReview: {
      gate: 'G4',
      title: 'Build review',
      question: 'Approve this build?',
      resolvedVerdict: null,
    },
  };
}

function resolvedAttr(container: Element): string | null {
  return container.querySelector('.gate-review')?.getAttribute('data-resolved') ?? null;
}

describe('BuildView gate verdict — optimistic-with-revert', () => {
  beforeEach(() => {
    chatBubbles.value = [];
    userName.value = 'Tester';
    submitGateVerdict.mockReset();
  });

  it('reverts the card and surfaces an error when the backend refuses', async () => {
    submitGateVerdict.mockResolvedValue({ ok: false, error: 'Build not verified.' });
    chatBubbles.value = [gateBubble('agent-gate-run-x-G4')];
    const { container } = render(<BuildView />);

    fireEvent.click(screen.getByTestId('verdict-approve'));
    fireEvent.click(screen.getByTestId('verdict-submit'));

    // Optimistically resolved the instant the user submits.
    expect(resolvedAttr(container)).toBe('approve');
    expect(submitGateVerdict).toHaveBeenCalledWith('agent-gate-run-x-G4', 'approve', '');

    // After the refusal comes back, the card reverts to un-resolved so the
    // user can retry, and an error bubble explains why.
    await waitFor(() => expect(resolvedAttr(container)).toBe(''));
    expect(screen.getByText(/Build not verified\./)).toBeInTheDocument();
    expect(screen.getByText(/still open/i)).toBeInTheDocument();
    // The verdict controls are interactive again (submit button re-appears).
    expect(screen.getByTestId('verdict-submit')).toBeInTheDocument();
  });

  it('keeps the card resolved when the backend accepts the verdict', async () => {
    submitGateVerdict.mockResolvedValue({ ok: true });
    chatBubbles.value = [gateBubble('agent-gate-run-y-G4')];
    const { container } = render(<BuildView />);

    fireEvent.click(screen.getByTestId('verdict-approve'));
    fireEvent.click(screen.getByTestId('verdict-submit'));

    // Give the resolved promise a chance to run; it must NOT revert.
    await new Promise((r) => setTimeout(r, 0));
    expect(resolvedAttr(container)).toBe('approve');
    expect(screen.queryByTestId('verdict-submit')).toBeNull();
  });
});
