import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/preact';
import type { ChatBubble } from '../state';

// Mock the waveEngineClient so button clicks don't try to hit IPC.
vi.mock('../services/waveEngineClient', () => ({
  resolveScopeDrift: vi.fn(),
  confirmViolation: vi.fn(),
}));

const client = await import('../services/waveEngineClient');
const { ChatBubbleSystem } = await import('./ChatBubbleSystem');

const resolveScopeDrift = client.resolveScopeDrift as ReturnType<typeof vi.fn>;
const confirmViolation = client.confirmViolation as ReturnType<typeof vi.fn>;

beforeEach(() => {
  resolveScopeDrift.mockReset();
  confirmViolation.mockReset();
});

function plainSystemBubble(over: Partial<ChatBubble> = {}): ChatBubble {
  return {
    id: 'b1',
    kind: 'system',
    text: 'Soul (G0) isn’t signed yet — firing that agent first.',
    gate: 'G0',
    waveAction: 'fire-agent-G0',
    ...over,
  };
}

function scopeDriftBubble(over: Partial<ChatBubble> = {}): ChatBubble {
  return {
    id: 'b2',
    kind: 'system',
    text: 'This new request feels different from the signed Soul — see options.',
    waveAction: 'scope-drift-prompt',
    waveUserRequest: 'Build an enterprise dashboard for clients',
    gate: null,
    ...over,
  };
}

function violationBubble(over: Partial<ChatBubble> = {}): ChatBubble {
  return {
    id: 'b3',
    kind: 'system',
    text: 'The code-review check reported 2 findings: uses eval(); missing null check.',
    waveAction: 'violation-prompt',
    waveViolation: {
      violation_kind: 'code-review',
      findings: ['uses eval()', 'missing null check'],
      gate: 'G4',
    },
    gate: 'G4',
    ...over,
  };
}


describe('ChatBubbleSystem — plain info bubble', () => {
  it('renders the text inside an info-styled bubble', () => {
    render(<ChatBubbleSystem bubble={plainSystemBubble()} />);
    expect(screen.getByTestId('chat-bubble-system-info')).toBeTruthy();
    expect(screen.getByText(/Soul \(G0\)/)).toBeTruthy();
  });

  it('does not render any prompt buttons in the plain case', () => {
    render(<ChatBubbleSystem bubble={plainSystemBubble()} />);
    expect(screen.queryByTestId('scope-drift-option-a')).toBeNull();
    expect(screen.queryByTestId('violation-option-a')).toBeNull();
  });
});


describe('ChatBubbleSystem — scope-drift prompt (§6)', () => {
  it('renders the four scope-drift options (a/b/c/d)', () => {
    render(<ChatBubbleSystem bubble={scopeDriftBubble()} />);
    expect(screen.getByTestId('scope-drift-option-a')).toBeTruthy();
    expect(screen.getByTestId('scope-drift-option-b')).toBeTruthy();
    expect(screen.getByTestId('scope-drift-option-c')).toBeTruthy();
    expect(screen.getByTestId('scope-drift-option-d')).toBeTruthy();
    expect(screen.getByText(/Amend Soul/)).toBeTruthy();
    expect(screen.getByText(/New folder/)).toBeTruthy();
    expect(screen.getByText(/Keep going/)).toBeTruthy();
  });

  it('fires resolveScopeDrift with the user request and chosen letter', async () => {
    resolveScopeDrift.mockResolvedValue({ action: 'fire-agent-G0', mode: 'amend' });
    const onFollowup = vi.fn();
    const onResolved = vi.fn();
    render(
      <ChatBubbleSystem
        bubble={scopeDriftBubble()}
        onFollowup={onFollowup}
        onResolved={onResolved}
      />
    );

    fireEvent.click(screen.getByTestId('scope-drift-option-a'));

    await waitFor(() => {
      expect(resolveScopeDrift).toHaveBeenCalledWith(
        'Build an enterprise dashboard for clients', 'a',
      );
    });
    expect(onFollowup).toHaveBeenCalledTimes(1);
    const followup = onFollowup.mock.calls[0][0];
    expect(followup.kind).toBe('system');
    expect(followup.text).toMatch(/Amending the signed Soul/);
    expect(onResolved).toHaveBeenCalledWith('b2', expect.objectContaining({ choice: 'a' }));
  });

  it('renders specific followup text per choice', async () => {
    resolveScopeDrift.mockResolvedValue({ action: 'new-project-new-workspace' });
    const onFollowup = vi.fn();
    render(<ChatBubbleSystem bubble={scopeDriftBubble()} onFollowup={onFollowup} />);
    fireEvent.click(screen.getByTestId('scope-drift-option-c'));
    await waitFor(() => expect(onFollowup).toHaveBeenCalled());
    expect(onFollowup.mock.calls[0][0].text).toMatch(/new folder/i);
  });

  it('shows error message on IPC failure', async () => {
    resolveScopeDrift.mockRejectedValue(new Error('sidecar down'));
    render(<ChatBubbleSystem bubble={scopeDriftBubble()} />);
    fireEvent.click(screen.getByTestId('scope-drift-option-a'));
    await waitFor(() => {
      expect(screen.getByText('sidecar down')).toBeTruthy();
    });
  });

  it('disables buttons once the prompt is resolved', () => {
    render(
      <ChatBubbleSystem
        bubble={scopeDriftBubble({ waveResolved: { choice: 'a', followupText: 'Amended' } })}
      />
    );
    const btn = screen.getByTestId('scope-drift-option-a') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByText(/Choice recorded: a/)).toBeTruthy();
  });

  it('does nothing when waveUserRequest is missing', async () => {
    const bubble = scopeDriftBubble();
    delete bubble.waveUserRequest;
    render(<ChatBubbleSystem bubble={bubble} />);
    fireEvent.click(screen.getByTestId('scope-drift-option-a'));
    // Give the promise microtask a chance to settle.
    await new Promise((r) => setTimeout(r, 0));
    expect(resolveScopeDrift).not.toHaveBeenCalled();
  });
});


describe('ChatBubbleSystem — violation prompt (§8)', () => {
  it('renders the three violation options (a/b/c)', () => {
    render(<ChatBubbleSystem bubble={violationBubble()} />);
    expect(screen.getByTestId('violation-option-a')).toBeTruthy();
    expect(screen.getByTestId('violation-option-b')).toBeTruthy();
    expect(screen.getByTestId('violation-option-c')).toBeTruthy();
    expect(screen.getByText(/Fix now/)).toBeTruthy();
    expect(screen.getByText(/Defer/)).toBeTruthy();
    expect(screen.getByText(/Override/)).toBeTruthy();
  });

  it('fires confirmViolation with full choice + violation_kind on click', async () => {
    confirmViolation.mockResolvedValue({
      audit_entry: {
        action: 'violation:code-review:override-with-log',
        choice: 'override-with-log',
        evidence: 'Override',
        violation_kind: 'code-review',
        gate: 'G4',
        findings: ['uses eval()'],
      },
      system_bubble: { kind: 'sign-recorded', gate: 'G4', text: 'Override recorded.' },
    });
    const onFollowup = vi.fn();
    const onResolved = vi.fn();
    render(
      <ChatBubbleSystem
        bubble={violationBubble()}
        onFollowup={onFollowup}
        onResolved={onResolved}
      />
    );

    fireEvent.click(screen.getByTestId('violation-option-c'));

    await waitFor(() => {
      expect(confirmViolation).toHaveBeenCalledWith(expect.objectContaining({
        violation_kind: 'code-review',
        choice: 'override-with-log',
        gate: 'G4',
      }));
    });
    expect(onFollowup).toHaveBeenCalledTimes(1);
    expect(onResolved).toHaveBeenCalledWith('b3', expect.objectContaining({
      choice: 'override-with-log',
    }));
  });

  it('uses the engine bubble text as the followup when present', async () => {
    confirmViolation.mockResolvedValue({
      audit_entry: { action: 'x', choice: 'fix-now', evidence: 'Fix now', violation_kind: 'code-review', gate: 'G4', findings: [] },
      system_bubble: { kind: 'sign-recorded', gate: 'G4', text: 'Holding ship — re-running after fixes.' },
    });
    const onFollowup = vi.fn();
    render(<ChatBubbleSystem bubble={violationBubble()} onFollowup={onFollowup} />);
    fireEvent.click(screen.getByTestId('violation-option-a'));
    await waitFor(() => expect(onFollowup).toHaveBeenCalled());
    expect(onFollowup.mock.calls[0][0].text).toMatch(/Holding ship/);
  });
});
