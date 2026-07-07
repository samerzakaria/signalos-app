import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/preact';
import type { ChatBubble } from '../state';

// Scope-drift option (e) — GATE-REOPEN-DESIGN #5.

vi.mock('../services/waveEngineClient', () => ({
  resolveScopeDrift: vi.fn(),
  confirmViolation: vi.fn(),
}));
vi.mock('../services/agentEvents', () => ({
  reopenGate: vi.fn(),
}));

const client = await import('../services/waveEngineClient');
const agentEvents = await import('../services/agentEvents');
const { ChatBubbleSystem } = await import('./ChatBubbleSystem');

const resolveScopeDrift = client.resolveScopeDrift as ReturnType<typeof vi.fn>;
const reopenGate = agentEvents.reopenGate as ReturnType<typeof vi.fn>;

function driftBubbleWithConflict(over: Partial<ChatBubble> = {}): ChatBubble {
  return {
    id: 'drift-1',
    kind: 'system',
    text: 'This request contradicts the signed Design (G3) — see options.',
    waveAction: 'scope-drift-prompt',
    waveUserRequest: 'Actually make it a mobile-first dark UI instead',
    waveDrift: {
      recommended_action: 'reopen-gate',
      conflicting_gate: 'G3',
      conflicting_summary: 'Desktop-first light design',
    },
    gate: null,
    ...over,
  };
}

beforeEach(() => {
  resolveScopeDrift.mockReset();
  reopenGate.mockReset();
});

describe('ChatBubbleSystem — scope-drift option (e) reopen', () => {
  it('renders a 5th option (e) when the drift names a conflicting signed gate', () => {
    render(<ChatBubbleSystem bubble={driftBubbleWithConflict()} />);
    expect(screen.getByTestId('scope-drift-option-e')).toBeTruthy();
    expect(screen.getByText(/Reopen G3 and rework from there/)).toBeTruthy();
  });

  it('does not render option (e) for a plain drift prompt', () => {
    const bubble = driftBubbleWithConflict();
    delete bubble.waveDrift;
    render(<ChatBubbleSystem bubble={bubble} />);
    expect(screen.getByTestId('scope-drift-option-a')).toBeTruthy();
    expect(screen.queryByTestId('scope-drift-option-e')).toBeNull();
  });

  it('choice e resolves the drift then fires agent:reopen-gate with the engine result', async () => {
    resolveScopeDrift.mockResolvedValue({
      action: 'reopen-gate',
      gate: 'G3',
      reason: 'Actually make it a mobile-first dark UI instead',
    });
    reopenGate.mockResolvedValue({ status: 'ok', gate: 'G3', invalidated: ['G4', 'G5'] });
    const onFollowup = vi.fn();
    const onResolved = vi.fn();
    render(
      <ChatBubbleSystem
        bubble={driftBubbleWithConflict()}
        onFollowup={onFollowup}
        onResolved={onResolved}
      />,
    );

    fireEvent.click(screen.getByTestId('scope-drift-option-e'));

    await waitFor(() => {
      expect(resolveScopeDrift).toHaveBeenCalledWith(
        'Actually make it a mobile-first dark UI instead', 'e',
      );
    });
    await waitFor(() => {
      expect(reopenGate).toHaveBeenCalledWith(
        'G3', 'Actually make it a mobile-first dark UI instead',
      );
    });
    expect(onFollowup).toHaveBeenCalledTimes(1);
    expect(onFollowup.mock.calls[0][0].text).toMatch(/Reopening G3/);
    expect(onResolved).toHaveBeenCalledWith('drift-1', expect.objectContaining({ choice: 'e' }));
  });

  it('shows the reopen refusal inline and does not resolve the prompt', async () => {
    resolveScopeDrift.mockResolvedValue({ action: 'reopen-gate', gate: 'G3', reason: 'rework' });
    reopenGate.mockResolvedValue({
      status: 'role-not-authorized',
      error: 'Your role is not authorized to reopen this gate.',
    });
    const onResolved = vi.fn();
    render(<ChatBubbleSystem bubble={driftBubbleWithConflict()} onResolved={onResolved} />);

    fireEvent.click(screen.getByTestId('scope-drift-option-e'));

    await waitFor(() => {
      expect(screen.getByText(/not authorized to reopen/)).toBeTruthy();
    });
    expect(onResolved).not.toHaveBeenCalled();
  });
});
