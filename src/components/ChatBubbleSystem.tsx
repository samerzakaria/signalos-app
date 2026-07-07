// ChatBubbleSystem.tsx — renders system-kind chat bubbles produced by
// the wave engine (M-W3+). Per WAVE-ENGINE-DESIGN §5 / §6 / §8.
//
// Renders three shapes based on the bubble's waveAction:
//   - 'scope-drift-prompt' → 4-way prompt (amend / new-parallel /
//                            new-folder / keep) per §6
//   - 'violation-prompt'   → 3-way prompt (fix-now / defer /
//                            override-with-log) per §8
//   - anything else        → plain info row (info icon + text)
//
// Button clicks fire the corresponding wave:* IPC command via the
// waveEngineClient service and append a follow-up bubble describing
// the outcome.

// State is held in @preact/signals (not preact/hooks) to match the rest of
// this codebase — the preact preset wires signals everywhere but the babel
// transform doesn't carry preact/hooks (see TestDebtPanel.tsx for the same
// note). Each bubble instance gets its own pair of useSignal handles.
import { useSignal } from '@preact/signals';
import type { ChatBubble } from '../state';
import {
  resolveScopeDrift,
  confirmViolation,
} from '../services/waveEngineClient';
import { reopenGate } from '../services/agentEvents';

interface Props {
  bubble: ChatBubble;
  /** Called when the user picks an option, with the produced follow-up
   *  bubble that should be appended to chatBubbles. Tests pass a spy;
   *  production wires this to setState on chatBubbles. */
  onFollowup?: (followup: ChatBubble) => void;
  /** Called to mark this bubble as resolved (disables the buttons).
   *  Tests pass a spy; production wires this to setState too. */
  onResolved?: (id: string, resolution: { choice: string; followupText?: string }) => void;
}

const SCOPE_DRIFT_OPTIONS: Array<{ key: string; label: string; sub: string }> = [
  { key: 'a', label: 'Amend Soul',        sub: 'Same project — evolve direction' },
  { key: 'b', label: 'New project here',  sub: 'Parallel in same workspace' },
  { key: 'c', label: 'New folder',        sub: 'Fresh workspace' },
  { key: 'd', label: 'Keep going',        sub: "I read it wrong — same project" },
];

const VIOLATION_OPTIONS: Array<{ key: string; choice: string; label: string; sub: string }> = [
  { key: 'a', choice: 'fix-now',           label: 'Fix now',      sub: 'Re-run after addressing the findings' },
  { key: 'b', choice: 'defer',             label: 'Defer',        sub: 'Track in backlog, ship as-is' },
  { key: 'c', choice: 'override-with-log', label: 'Override',     sub: 'Ship anyway; logged as violation' },
];

function nowId() {
  if (typeof crypto !== 'undefined' && (crypto as Crypto).randomUUID) {
    return (crypto as Crypto).randomUUID();
  }
  return String(Date.now()) + Math.random();
}

export function ChatBubbleSystem({ bubble, onFollowup, onResolved }: Props) {
  const busy = useSignal(false);
  const errorMsg = useSignal<string | null>(null);

  const isScopeDrift = bubble.waveAction === 'scope-drift-prompt';
  const isViolation = bubble.waveAction === 'violation-prompt';
  const resolved = bubble.waveResolved;

  async function handleScopeDrift(choice: string) {
    if (!bubble.waveUserRequest || busy.value) return;
    busy.value = true;
    errorMsg.value = null;
    try {
      const result = await resolveScopeDrift(bubble.waveUserRequest, choice);
      let followupText = scopeDriftFollowupText(choice, result);
      // Option (e): the engine hands back {action:"reopen-gate", gate, reason}
      // and the frontend fires agent:reopen-gate — the engine never rewrites
      // signatures itself (GATE-REOPEN-DESIGN #5).
      if (result.action === 'reopen-gate' && result.gate) {
        const reopened = await reopenGate(
          result.gate,
          result.reason || bubble.waveUserRequest,
        );
        if (reopened.status !== 'ok') {
          errorMsg.value = reopened.error || `Reopen refused (${reopened.status}).`;
          return;
        }
        followupText = `Reopening ${result.gate} — later signed gates are invalidated and rework starts from there.`;
      }
      const followup: ChatBubble = {
        id: nowId(),
        kind: 'system',
        text: followupText,
        gate: (result.current_gate as ChatBubble['gate']) || (result.gate as ChatBubble['gate']) || null,
        waveAction: result.action,
      };
      onFollowup?.(followup);
      onResolved?.(bubble.id, { choice, followupText: followup.text });
    } catch (err) {
      errorMsg.value = messageOf(err);
    } finally {
      busy.value = false;
    }
  }

  async function handleViolation(option: typeof VIOLATION_OPTIONS[number]) {
    if (!bubble.waveViolation || busy.value) return;
    busy.value = true;
    errorMsg.value = null;
    try {
      const result = await confirmViolation({
        violation_kind: bubble.waveViolation.violation_kind,
        choice: option.choice as 'fix-now' | 'defer' | 'override-with-log',
        user_reply: option.label,
        gate: bubble.waveViolation.gate || undefined,
        findings: bubble.waveViolation.findings,
      });
      const followup: ChatBubble = {
        id: nowId(),
        kind: 'system',
        text: result.system_bubble?.text || `Recorded ${option.choice}.`,
        gate: bubble.waveViolation.gate || null,
        waveAction: 'violation-recorded',
      };
      onFollowup?.(followup);
      onResolved?.(bubble.id, { choice: option.choice, followupText: followup.text });
    } catch (err) {
      errorMsg.value = messageOf(err);
    } finally {
      busy.value = false;
    }
  }

  if (isScopeDrift) {
    // GATE-REOPEN-DESIGN #5: when the drift verdict names a conflicting
    // signed gate (recommended_action "reopen-gate"), offer a 5th option (e).
    const drift = bubble.waveDrift;
    const reopenTarget = drift && (drift.recommended_action === 'reopen-gate' || drift.conflicting_gate)
      ? (drift.conflicting_gate || null)
      : null;
    const options = reopenTarget
      ? [
          ...SCOPE_DRIFT_OPTIONS,
          {
            key: 'e',
            label: `Reopen ${reopenTarget} and rework from there`,
            sub: 'Later signed gates are invalidated (audit-logged)',
          },
        ]
      : SCOPE_DRIFT_OPTIONS;
    return (
      <div className="msg spark" data-testid="chat-bubble-system-scope-drift">
        <div className="msg-av"><i className="ti ti-git-fork"></i></div>
        <div>
          <div
            className="bubble"
            style={{ background: 'var(--surface-warm)', color: 'var(--ink-1)' }}
          >
            <div style={{ marginBottom: 8, fontSize: '12.5px' }}>{bubble.text}</div>
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
              }}
            >
              {options.map((opt) => (
                <button
                  key={opt.key}
                  type="button"
                  className="cmp-btn"
                  data-testid={`scope-drift-option-${opt.key}`}
                  disabled={busy.value || !!resolved}
                  onClick={() => handleScopeDrift(opt.key)}
                  style={{
                    textAlign: 'left',
                    padding: '8px 10px',
                    border: '1px solid var(--line)',
                    borderRadius: 6,
                    cursor: busy.value || resolved ? 'not-allowed' : 'pointer',
                    opacity: resolved && resolved.choice !== opt.key ? 0.45 : 1,
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: '12.5px' }}>({opt.key}) {opt.label}</div>
                  <div style={{ fontSize: '11px', color: 'var(--ink-3)' }}>{opt.sub}</div>
                </button>
              ))}
            </div>
            {resolved ? (
              <div style={{ marginTop: 8, fontSize: '11px', color: 'var(--ink-3)' }}>
                Choice recorded: {resolved.choice}.
                {resolved.followupText ? ` ${resolved.followupText}` : ''}
              </div>
            ) : null}
            {errorMsg.value ? (
              <div style={{ marginTop: 8, fontSize: '11px', color: 'var(--danger)' }}>
                {errorMsg.value}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  if (isViolation) {
    return (
      <div className="msg spark" data-testid="chat-bubble-system-violation">
        <div className="msg-av"><i className="ti ti-alert-triangle"></i></div>
        <div>
          <div
            className="bubble"
            style={{ background: 'var(--warning-soft)', color: 'var(--warning-deep)' }}
          >
            <div style={{ marginBottom: 8, fontSize: '12.5px', whiteSpace: 'pre-wrap' }}>{bubble.text}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {VIOLATION_OPTIONS.map((opt) => (
                <button
                  key={opt.key}
                  type="button"
                  className="cmp-btn"
                  data-testid={`violation-option-${opt.key}`}
                  disabled={busy.value || !!resolved}
                  onClick={() => handleViolation(opt)}
                  style={{
                    textAlign: 'left',
                    padding: '8px 10px',
                    border: '1px solid var(--line)',
                    borderRadius: 6,
                    cursor: busy.value || resolved ? 'not-allowed' : 'pointer',
                    opacity: resolved && resolved.choice !== opt.choice ? 0.45 : 1,
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: '12.5px' }}>({opt.key}) {opt.label}</div>
                  <div style={{ fontSize: '11px', color: 'var(--ink-3)' }}>{opt.sub}</div>
                </button>
              ))}
            </div>
            {resolved ? (
              <div style={{ marginTop: 8, fontSize: '11px', color: 'var(--ink-3)' }}>
                Choice recorded: {resolved.choice}.
                {resolved.followupText ? ` ${resolved.followupText}` : ''}
              </div>
            ) : null}
            {errorMsg.value ? (
              <div style={{ marginTop: 8, fontSize: '11px', color: 'var(--danger)' }}>
                {errorMsg.value}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  // Default: plain info bubble — what BuildView rendered pre-component.
  return (
    <div className="msg spark" data-testid="chat-bubble-system-info">
      <div className="msg-av"><i className="ti ti-info-circle"></i></div>
      <div>
        <div
          className="bubble"
          style={{ background: 'var(--info-soft)', color: 'var(--info-deep)', fontSize: '12.5px' }}
        >
          {bubble.text}
        </div>
      </div>
    </div>
  );
}

function scopeDriftFollowupText(choice: string, result: { action?: string; mode?: string; gate?: string | null }): string {
  if (result.action === 'reopen-gate') {
    return `Reopening ${result.gate || 'the conflicting gate'} — rework starts from there.`;
  }
  if (result.action === 'fire-agent-G0' && result.mode === 'amend') {
    return 'Amending the signed Soul — firing the onboarding agent in amend mode.';
  }
  if (result.action === 'new-project-same-workspace') {
    return 'Starting a new project in this workspace.';
  }
  if (result.action === 'new-project-new-workspace') {
    return 'Pick a new folder to scaffold the next project.';
  }
  if (result.action === 'treat-as-refinement') {
    return 'Treating your message as a refinement of the existing project.';
  }
  if (result.action === 'no-longer-drifted') {
    return 'Scope-drift no longer detected — proceeding normally.';
  }
  return `Scope-drift choice ${choice} applied.`;
}

function messageOf(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === 'string') return err;
  return String(err);
}
