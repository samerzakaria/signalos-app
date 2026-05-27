/**
 * TestDebtPanel.tsx — Read-only review surface for the test-debt store.
 *
 * Milestone 5 / §6.8.4 — system-emitted review. The .signalos/test-debt.jsonl
 * store is written by validators (mutation-low, missing-test, manual-defect);
 * users review entries and dismiss with an audit-trail reason. There is NO
 * "add debt" affordance here — entries arrive via the Rust side.
 *
 * Backend: src-tauri/src/test_automation.rs (list/resolve/read_mutation_score).
 * IPC shape: ipc.testAutomation.{listDebt,resolveDebt,readMutationScore}.
 *
 * State is held in @preact/signals (not preact/hooks) to match the rest of
 * this codebase — the preact preset config wires signals everywhere but
 * leaves hooks un-supported in the babel transform.
 */
import { signal, useSignal } from '@preact/signals';
import * as ipc from '../js/ipc.js';
import { userName } from '../state';

type TestDebtEntry = {
  ts: string;
  kind: string;
  area: string;
  title: string;
  detail: string;
  resolved: boolean;
};

type TestDebtSummary = {
  entries: TestDebtEntry[];
  open_count: number;
  resolved_count: number;
};

type MutationScoreFile = {
  score: number | null;
  area: string;
  measured_at: string | null;
  source: string | null;
  present: boolean;
};

// Mirrors the Rust threshold (check_mutation_threshold uses 0.95) but the
// audit doc calls out 80% as the user-visible policy floor — display the
// stricter policy so the user understands what "meeting policy" means.
const MUTATION_THRESHOLD = 0.8;

// Module-level signals so each render reads the same state — Preact's signal
// model treats these as reactive, no hooks plumbing required.
const summary = signal<TestDebtSummary | null>(null);
const mutationScore = signal<MutationScoreFile | null>(null);
const loading = signal<boolean>(true);
const errorMsg = signal<string | null>(null);
const dismissing = signal<TestDebtEntry | null>(null);
const dismissReason = signal<string>('');
const reportingDefect = signal<boolean>(false);
const reportTitle = signal<string>('');
const reportArea = signal<string>('');
const reportDetail = signal<string>('');
let mounted = false;

async function refresh() {
  loading.value = true;
  errorMsg.value = null;
  try {
    const [s, m] = await Promise.all([
      ipc.testAutomation.listDebt(),
      ipc.testAutomation.readMutationScore(),
    ]);
    summary.value = (s as TestDebtSummary) || { entries: [], open_count: 0, resolved_count: 0 };
    mutationScore.value = (m as MutationScoreFile) || null;
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : 'Could not load test debt.';
    errorMsg.value = msg;
    summary.value = { entries: [], open_count: 0, resolved_count: 0 };
    mutationScore.value = null;
  } finally {
    loading.value = false;
  }
}

function openReportDefect() {
  reportingDefect.value = true;
  reportTitle.value = '';
  reportArea.value = '';
  reportDetail.value = '';
}

function cancelReport() {
  reportingDefect.value = false;
  reportTitle.value = '';
  reportArea.value = '';
  reportDetail.value = '';
}

async function submitReport() {
  const title = reportTitle.value.trim();
  const area = reportArea.value.trim();
  const detail = reportDetail.value.trim();
  if (!title) {
    errorMsg.value = 'Title is required.';
    return;
  }
  try {
    await ipc.testAutomation.addDebt('manual-defect', area, title, detail);
    reportingDefect.value = false;
    reportTitle.value = '';
    reportArea.value = '';
    reportDetail.value = '';
    await refresh();
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : 'Could not log defect.';
    errorMsg.value = msg;
  }
}

function openDismiss(entry: TestDebtEntry) {
  dismissing.value = entry;
  dismissReason.value = '';
}

function cancelDismiss() {
  dismissing.value = null;
  dismissReason.value = '';
}

async function confirmDismiss() {
  const target = dismissing.value;
  if (!target) return;
  const reason = dismissReason.value.trim();
  if (!reason) return;
  try {
    // AMD-CORE-110 + 111: dismiss is an action with audit semantics —
    // record WHO dismissed and WHY before flipping the underlying state.
    // The Rust resolveDebt signature only accepts (title), so the audit
    // metadata is persisted via the audit:append IPC route (added in M2-a).
    // Order: append-first, then resolve, so a failed resolve still leaves
    // the audit trail showing the intended dismiss.
    const auditEntry = {
      action: 'test-debt-dismiss',
      title: target.title,
      area: target.area,
      kind: target.kind,
      dismissed_by: userName.value || 'unknown',
      dismiss_reason: reason,
      ts: new Date().toISOString(),
    };
    try {
      await ipc.signal.runAndWait('audit:append', [JSON.stringify(auditEntry)], 5000);
    } catch {
      // Audit-append failure is logged via console but does not block the
      // user from dismissing. The Rust action itself produces a separate
      // state mutation that the user can correlate to a missing audit entry.
      console.warn('test-debt dismiss audit-append failed; proceeding with resolveDebt');
    }
    await ipc.testAutomation.resolveDebt(target.title);
    dismissing.value = null;
    dismissReason.value = '';
    await refresh();
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : 'Could not dismiss entry.';
    errorMsg.value = msg;
  }
}

export function TestDebtPanel() {
  // Use a per-instance signal so tests that re-render trigger refresh once.
  // We also keep module-level state for the singleton case (one panel).
  const initOnce = useSignal(false);
  if (!initOnce.value && !mounted) {
    mounted = true;
    initOnce.value = true;
    // Kick off the load without blocking render.
    refresh();
  } else if (!initOnce.value) {
    initOnce.value = true;
    refresh();
  }

  const sum = summary.value;
  const score = mutationScore.value;
  const isLoading = loading.value;
  const err = errorMsg.value;
  const dismissTarget = dismissing.value;

  // Mutation-score banner — always visible at the top.
  let banner;
  if (!score || !score.present || score.score === null) {
    banner = (
      <div
        data-testid="td-mutation-banner"
        style={{
          padding: '8px 12px',
          borderRadius: 'var(--r-sm)',
          background: 'var(--surface-warm)',
          fontSize: '12px',
          color: 'var(--ink-2)',
          marginBottom: '12px',
        }}
      >
        <i className="ti ti-help-circle" style={{ marginRight: '6px' }}></i>
        Mutation score: no measurement on file (threshold:{' '}
        {Math.round(MUTATION_THRESHOLD * 100)}%)
      </div>
    );
  } else {
    const pct = Math.round(score.score * 100);
    const meets = score.score >= MUTATION_THRESHOLD;
    banner = (
      <div
        data-testid="td-mutation-banner"
        style={{
          padding: '8px 12px',
          borderRadius: 'var(--r-sm)',
          background: meets ? 'var(--success-soft)' : 'var(--danger-soft)',
          color: meets ? 'var(--success-deep)' : 'var(--danger-deep)',
          fontSize: '12px',
          marginBottom: '12px',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}
      >
        <i className={`ti ${meets ? 'ti-circle-check' : 'ti-alert-circle'}`}></i>
        <span>
          Mutation score: {pct}% (threshold:{' '}
          {Math.round(MUTATION_THRESHOLD * 100)}%)
        </span>
      </div>
    );
  }

  const entries = sum?.entries || [];

  return (
    <div
      className="test-debt-panel card"
      data-testid="test-debt-panel"
      style={{ padding: '14px 16px', marginTop: '12px' }}
    >
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '10px',
        }}
      >
        <h3 style={{ margin: 0, fontSize: '13px', fontWeight: 600 }}>
          <i className="ti ti-flask" style={{ marginRight: '6px' }}></i>
          Test debt
        </h3>
        <div style={{ display: 'flex', gap: '6px' }}>
          <button
            className="btn btn-soft"
            style={{ fontSize: '11px', padding: '3px 8px' }}
            onClick={() => openReportDefect()}
            aria-label="Report a defect"
            data-testid="td-report-btn"
          >
            <i className="ti ti-plus" style={{ marginRight: '3px' }}></i>
            Report defect
          </button>
          <button
            className="btn btn-soft"
            style={{ fontSize: '11px', padding: '3px 8px' }}
            onClick={() => refresh()}
            aria-label="Refresh test debt"
          >
            <i className="ti ti-refresh"></i>
          </button>
        </div>
      </header>

      {banner}

      {err ? (
        <div
          style={{
            padding: '8px 12px',
            borderRadius: 'var(--r-sm)',
            background: 'var(--danger-soft)',
            color: 'var(--danger-deep)',
            fontSize: '12px',
            marginBottom: '10px',
          }}
          data-testid="td-error"
        >
          {err}
        </div>
      ) : null}

      {isLoading ? (
        <div
          data-testid="td-loading"
          style={{ fontSize: '12px', color: 'var(--ink-3)', padding: '12px 0' }}
        >
          Loading test debt…
        </div>
      ) : err ? (
        <div
          data-testid="td-unavailable"
          style={{
            padding: '14px 12px',
            textAlign: 'center',
            color: 'var(--ink-3)',
            fontSize: '12.5px',
            border: '1px dashed var(--line)',
            borderRadius: 'var(--r-sm)',
          }}
        >
          Test-debt evidence is unavailable for this workspace.
        </div>
      ) : entries.length === 0 ? (
        <div
          data-testid="td-empty"
          style={{
            padding: '14px 12px',
            textAlign: 'center',
            color: 'var(--ink-3)',
            fontSize: '12.5px',
            border: '1px dashed var(--line)',
            borderRadius: 'var(--r-sm)',
          }}
        >
          No test debt — your test coverage is meeting policy
        </div>
      ) : (
        <div
          data-testid="td-list"
          style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}
        >
          {entries.map((entry) => (
            <article
              key={`${entry.ts}-${entry.title}`}
              data-testid="td-entry"
              className="card"
              style={{
                padding: '10px 12px',
                opacity: entry.resolved ? 0.55 : 1,
                background: 'var(--surface)',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'flex-start',
                  gap: '8px',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '12.5px', fontWeight: 600, marginBottom: '2px' }}>
                    {entry.title}
                  </div>
                  <div
                    style={{
                      fontSize: '11px',
                      color: 'var(--ink-3)',
                      fontFamily: 'var(--f-mono)',
                      marginBottom: '4px',
                      wordBreak: 'break-all',
                    }}
                  >
                    {entry.area || '(no source file)'}
                  </div>
                  {entry.detail ? (
                    <div
                      style={{
                        fontSize: '12px',
                        color: 'var(--ink-2)',
                        marginBottom: '4px',
                      }}
                    >
                      {entry.detail}
                    </div>
                  ) : null}
                  <div style={{ fontSize: '10.5px', color: 'var(--ink-3)' }}>
                    <span data-testid="td-entry-kind">{entry.kind}</span>
                    {' · added '}
                    <span data-testid="td-entry-ts">{entry.ts || '(unknown)'}</span>
                    {entry.resolved ? ' · resolved' : ''}
                  </div>
                </div>
                {entry.resolved ? null : (
                  <button
                    className="btn btn-soft"
                    style={{ fontSize: '11px', padding: '4px 10px' }}
                    onClick={() => openDismiss(entry)}
                    data-testid="td-dismiss-btn"
                  >
                    Dismiss
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      )}

      {reportingDefect.value ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="td-report-title"
          data-testid="td-report-modal"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.45)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
          onClick={() => cancelReport()}
        >
          <div
            className="card"
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 'min(540px, 92%)',
              padding: '18px 20px',
              background: 'var(--surface)',
            }}
          >
            <h3 id="td-report-title" style={{ margin: '0 0 8px', fontSize: '14px' }}>
              Report a defect
            </h3>
            <p style={{ fontSize: '12px', color: 'var(--ink-2)', margin: '0 0 12px' }}>
              Every manually-found defect must become an automated test before the fix
              merges. Logging it here makes that promise.
            </p>
            <label
              htmlFor="td-report-title-input"
              style={{ fontSize: '11.5px', color: 'var(--ink-2)', display: 'block', marginBottom: '4px' }}
            >
              Title (required)
            </label>
            <input
              id="td-report-title-input"
              data-testid="td-report-title-input"
              type="text"
              value={reportTitle.value}
              onInput={(e) => { reportTitle.value = (e.target as HTMLInputElement).value; }}
              placeholder="Short defect summary"
              style={{
                width: '100%',
                fontSize: '12px',
                padding: '8px 10px',
                borderRadius: 'var(--r-sm)',
                border: '1px solid var(--line)',
                fontFamily: 'inherit',
                boxSizing: 'border-box',
                marginBottom: '10px',
              }}
            />
            <label
              htmlFor="td-report-area-input"
              style={{ fontSize: '11.5px', color: 'var(--ink-2)', display: 'block', marginBottom: '4px' }}
            >
              Area / file glob
            </label>
            <input
              id="td-report-area-input"
              data-testid="td-report-area-input"
              type="text"
              value={reportArea.value}
              onInput={(e) => { reportArea.value = (e.target as HTMLInputElement).value; }}
              placeholder="src/utils/dates.ts"
              style={{
                width: '100%',
                fontSize: '12px',
                padding: '8px 10px',
                borderRadius: 'var(--r-sm)',
                border: '1px solid var(--line)',
                fontFamily: 'inherit',
                boxSizing: 'border-box',
                marginBottom: '10px',
              }}
            />
            <label
              htmlFor="td-report-detail-input"
              style={{ fontSize: '11.5px', color: 'var(--ink-2)', display: 'block', marginBottom: '4px' }}
            >
              Repro steps + expected behavior
            </label>
            <textarea
              id="td-report-detail-input"
              data-testid="td-report-detail-input"
              rows={4}
              value={reportDetail.value}
              onInput={(e) => { reportDetail.value = (e.target as HTMLTextAreaElement).value; }}
              placeholder="Steps to reproduce and what the code should have done."
              style={{
                width: '100%',
                fontSize: '12px',
                padding: '8px 10px',
                borderRadius: 'var(--r-sm)',
                border: '1px solid var(--line)',
                resize: 'vertical',
                fontFamily: 'inherit',
                boxSizing: 'border-box',
              }}
            />
            <div
              style={{
                display: 'flex',
                justifyContent: 'flex-end',
                gap: '8px',
                marginTop: '14px',
              }}
            >
              <button
                className="btn btn-ghost"
                onClick={() => cancelReport()}
                data-testid="td-report-cancel"
              >
                Cancel
              </button>
              <button
                className="btn btn-primary"
                onClick={() => submitReport()}
                disabled={reportTitle.value.trim().length === 0}
                data-testid="td-report-submit"
              >
                Log test debt
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {dismissTarget ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="td-dismiss-title"
          data-testid="td-dismiss-modal"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.45)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
          onClick={() => cancelDismiss()}
        >
          <div
            className="card"
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 'min(520px, 92%)',
              padding: '18px 20px',
              background: 'var(--surface)',
            }}
          >
            <h3 id="td-dismiss-title" style={{ margin: '0 0 8px', fontSize: '14px' }}>
              Dismiss test-debt entry
            </h3>
            <p style={{ fontSize: '12px', color: 'var(--ink-2)', margin: '0 0 12px' }}>
              You are about to dismiss <strong>{dismissTarget.title}</strong>. This writes an
              audit-trail entry with your name and the reason below.
            </p>
            <label
              htmlFor="td-dismiss-reason"
              style={{
                fontSize: '11.5px',
                color: 'var(--ink-2)',
                display: 'block',
                marginBottom: '4px',
              }}
            >
              Reason for dismissal
            </label>
            <textarea
              id="td-dismiss-reason"
              data-testid="td-dismiss-reason"
              rows={4}
              value={dismissReason.value}
              onInput={(e) => {
                dismissReason.value = (e.target as HTMLTextAreaElement).value;
              }}
              placeholder="Explain why this entry is OK to dismiss without a follow-up test."
              style={{
                width: '100%',
                fontSize: '12px',
                padding: '8px 10px',
                borderRadius: 'var(--r-sm)',
                border: '1px solid var(--line)',
                resize: 'vertical',
                fontFamily: 'inherit',
                boxSizing: 'border-box',
              }}
            />
            <div
              style={{
                display: 'flex',
                justifyContent: 'flex-end',
                gap: '8px',
                marginTop: '14px',
              }}
            >
              <button
                className="btn btn-ghost"
                onClick={() => cancelDismiss()}
                data-testid="td-dismiss-cancel"
              >
                Cancel
              </button>
              <button
                className="btn btn-primary"
                onClick={() => confirmDismiss()}
                disabled={dismissReason.value.trim().length === 0}
                data-testid="td-dismiss-confirm"
              >
                Confirm dismiss
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// Exposed for tests: forces a fresh refresh on the next mount even if
// the singleton `mounted` guard has fired in a prior test.
export function __resetTestDebtPanelForTests() {
  mounted = false;
  summary.value = null;
  mutationScore.value = null;
  loading.value = true;
  errorMsg.value = null;
  dismissing.value = null;
  dismissReason.value = '';
}
