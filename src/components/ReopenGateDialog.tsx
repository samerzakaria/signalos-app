// ReopenGateDialog.tsx — confirm dialog for reopening a signed gate
// (GATE-REOPEN-DESIGN). Opened from the gate timeline's per-gate "Reopen"
// affordance. Collects a REQUIRED reason, then fires `agent:reopen-gate`
// via agentEvents.reopenGate (same transport as agent:verdict). Refusal
// statuses from the backend (not-signed / role-not-authorized / max-reopens /
// delivery-busy) render inline; on success the dialog closes and the
// `gate_reopened` agent event drives the system bubble + gate-rail update.
//
// Follows the existing modal-overlay pattern (OverrideModal). State lives in
// module-level signals (not preact/hooks — see MEMORY.md).

import { signal } from '@preact/signals';
import { reopenGate } from '../services/agentEvents';

export const reopenDialogGate = signal<string | null>(null);
const reasonSig = signal<string>('');
const busySig = signal<boolean>(false);
const errorSig = signal<string | null>(null);

/** Open the dialog for a gate code (e.g. "G3"). Resets prior input. */
export function openReopenGateDialog(gate: string): void {
  reasonSig.value = '';
  errorSig.value = null;
  busySig.value = false;
  reopenDialogGate.value = gate;
}

export function closeReopenGateDialog(): void {
  reopenDialogGate.value = null;
}

/** Test hook: reset the module signals between renders. */
export function __resetReopenGateDialogForTests(): void {
  reopenDialogGate.value = null;
  reasonSig.value = '';
  busySig.value = false;
  errorSig.value = null;
}

async function confirm(): Promise<void> {
  const gate = reopenDialogGate.value;
  if (!gate || busySig.value) return;
  const reason = reasonSig.value.trim();
  if (!reason) {
    errorSig.value = 'A reason is required to reopen a signed gate (it is audit-logged and threaded into the rework).';
    return;
  }
  busySig.value = true;
  errorSig.value = null;
  try {
    const res = await reopenGate(gate, reason);
    if (res.status === 'ok') {
      closeReopenGateDialog();
    } else {
      errorSig.value = res.error || `Reopen refused (${res.status}).`;
    }
  } catch (err) {
    errorSig.value = err instanceof Error ? err.message : String(err);
  } finally {
    busySig.value = false;
  }
}

export function ReopenGateDialog() {
  const gate = reopenDialogGate.value;
  if (!gate) return null;

  return (
    <div className="modal-overlay open" data-testid="reopen-gate-dialog" onClick={() => closeReopenGateDialog()}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: '460px' }}>
        <div className="modal-head">
          <h3>Reopen {gate}</h3>
          <button className="ico" onClick={() => closeReopenGateDialog()} aria-label="Close"><i className="ti ti-x"></i></button>
        </div>
        <div className="modal-body">
          <div className="override-rule">
            <i className="ti ti-lock-open"></i>
            <div className="override-rule-tx">
              <strong>{gate} loses its signature</strong>
              <p>
                Every later signed gate is invalidated too and the work resumes
                from {gate}. Your reason is audit-logged and handed to the gate
                agent verbatim.
              </p>
            </div>
          </div>
          <label className="field-label">Reason for reopening (required)</label>
          <textarea
            className="plain-input"
            data-testid="reopen-reason"
            placeholder={`Why does ${gate} need rework?`}
            rows={3}
            style={{ resize: 'vertical', lineHeight: '1.5', marginBottom: '0' }}
            value={reasonSig.value}
            onInput={(e) => { reasonSig.value = (e.target as HTMLTextAreaElement).value; }}
          ></textarea>
          {errorSig.value ? (
            <div
              data-testid="reopen-error"
              style={{ marginTop: '8px', fontSize: '12px', color: 'var(--danger-deep)', background: 'var(--danger-soft)', padding: '8px 10px', borderRadius: 'var(--r-sm)' }}
            >
              <i className="ti ti-alert-circle" style={{ verticalAlign: 'middle' }}></i>{' '}
              {errorSig.value}
            </div>
          ) : null}
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={() => closeReopenGateDialog()}>Cancel</button>
          <button
            className="btn btn-danger"
            data-testid="reopen-confirm"
            disabled={busySig.value}
            onClick={() => { void confirm(); }}
          >
            <i className="ti ti-lock-open"></i> Reopen {gate}
          </button>
        </div>
      </div>
    </div>
  );
}
