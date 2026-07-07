// CompetitorPanel.tsx — "Competitors (optional)" input for the brief step
// (#16). Hosted in the Build conversation (the brief/intent flow lives in
// the chat, not a separate wizard view), as a compact collapsed strip under
// the business-stage strip so it never crowds the composer.
//
// The founder pastes up to 5 competitor URLs (chips UI) and clicks Analyze;
// we call the `competitor:analyze` IPC command and render the returned
// matrix compactly plus any per-URL errors. `llm-unavailable` gets a
// friendly "connect an AI key" note. The analysis itself is stored
// backend-side — this panel only displays it.
//
// State lives in module-level signals (not preact/hooks — see MEMORY.md).

import { signal } from '@preact/signals';
import * as ipc from '../js/ipc.js';

export const MAX_COMPETITOR_URLS = 5;

export interface CompetitorUrlError {
  url: string;
  error: string;
}

export const competitorOpen = signal<boolean>(false);
export const competitorUrls = signal<string[]>([]);
export const competitorInput = signal<string>('');
export const competitorBusy = signal<boolean>(false);
export const competitorMatrix = signal<unknown>(null);
export const competitorErrors = signal<CompetitorUrlError[]>([]);
export const competitorNote = signal<string | null>(null);

/** Test hook: reset the module signals between renders. */
export function __resetCompetitorPanelForTests(): void {
  competitorOpen.value = false;
  competitorUrls.value = [];
  competitorInput.value = '';
  competitorBusy.value = false;
  competitorMatrix.value = null;
  competitorErrors.value = [];
  competitorNote.value = null;
}

function looksLikeUrl(value: string): boolean {
  return /^https?:\/\/\S+$/i.test(value) || /^[\w-]+(\.[\w-]+)+(\/\S*)?$/i.test(value);
}

/** Add one or more pasted URLs (comma/whitespace separated). Returns a
 *  message when something was rejected, null when all went in. */
export function addCompetitorUrls(raw: string): string | null {
  const parts = String(raw || '')
    .split(/[\s,]+/)
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.length === 0) return null;
  let rejected: string | null = null;
  const next = [...competitorUrls.value];
  for (const part of parts) {
    if (!looksLikeUrl(part)) {
      rejected = `"${part}" doesn't look like a URL.`;
      continue;
    }
    const url = /^https?:\/\//i.test(part) ? part : `https://${part}`;
    if (next.includes(url)) continue;
    if (next.length >= MAX_COMPETITOR_URLS) {
      rejected = `Up to ${MAX_COMPETITOR_URLS} competitor URLs.`;
      break;
    }
    next.push(url);
  }
  competitorUrls.value = next;
  return rejected;
}

export function removeCompetitorUrl(url: string): void {
  competitorUrls.value = competitorUrls.value.filter((u) => u !== url);
}

export async function analyzeCompetitors(): Promise<void> {
  const urls = competitorUrls.value;
  if (urls.length === 0 || competitorBusy.value) return;
  competitorBusy.value = true;
  competitorNote.value = null;
  competitorMatrix.value = null;
  competitorErrors.value = [];
  try {
    const raw = await ipc.signal.runAndWait(
      'competitor:analyze',
      [JSON.stringify({ urls })],
      180000,
    );
    const res = (raw && typeof raw === 'object' ? raw : {}) as {
      status?: string;
      matrix?: unknown;
      errors?: unknown;
      error?: string;
    };
    // Per-URL errors can ride alongside either status.
    if (Array.isArray(res.errors)) {
      competitorErrors.value = (res.errors as unknown[])
        .filter((e): e is Record<string, unknown> => !!e && typeof e === 'object')
        .map((e) => ({
          url: typeof e.url === 'string' ? e.url : '',
          error: typeof e.error === 'string' ? e.error : 'Failed to analyze.',
        }));
    }
    if (res.status === 'llm-unavailable') {
      competitorNote.value =
        'Competitor analysis needs a connected AI key. Add a provider key in Settings, then try again.';
    } else if (res.status === 'ok' || res.matrix != null) {
      competitorMatrix.value = res.matrix ?? null;
      if (res.matrix == null && competitorErrors.value.length === 0) {
        competitorNote.value = 'No analysis returned.';
      }
    } else {
      competitorNote.value = res.error || `Analysis failed${res.status ? ` (${res.status})` : ''}.`;
    }
  } catch (err) {
    competitorNote.value =
      'Analysis unavailable: ' + (err instanceof Error ? err.message : String(err));
  } finally {
    competitorBusy.value = false;
  }
}

function cellText(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(cellText).filter(Boolean).join(', ');
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Render the matrix defensively: array-of-objects → table; object →
 *  key/value list; string → paragraph; anything else → JSON. */
function MatrixView({ matrix }: { matrix: unknown }) {
  if (matrix == null) return null;

  if (Array.isArray(matrix) && matrix.length > 0 && matrix.every((r) => r && typeof r === 'object' && !Array.isArray(r))) {
    const rows = matrix as Record<string, unknown>[];
    const columns: string[] = [];
    for (const row of rows) {
      for (const key of Object.keys(row)) {
        if (!columns.includes(key)) columns.push(key);
        if (columns.length >= 8) break;
      }
    }
    return (
      <div style={{ overflowX: 'auto' }} data-testid="competitor-matrix">
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c} style={{ textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid var(--line)', color: 'var(--ink-3)', fontWeight: 600 }}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {columns.map((c) => (
                  <td key={c} style={{ padding: '4px 8px', borderBottom: '1px solid var(--line)', verticalAlign: 'top' }}>
                    {cellText(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (typeof matrix === 'object' && !Array.isArray(matrix)) {
    const entries = Object.entries(matrix as Record<string, unknown>);
    return (
      <div data-testid="competitor-matrix" style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '12px' }}>
        {entries.map(([key, value]) => (
          <div key={key}>
            <span style={{ fontWeight: 600 }}>{key}: </span>
            <span style={{ color: 'var(--ink-2)' }}>{cellText(value)}</span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div data-testid="competitor-matrix" style={{ fontSize: '12px', whiteSpace: 'pre-wrap', color: 'var(--ink-2)' }}>
      {cellText(matrix)}
    </div>
  );
}

export function CompetitorPanel() {
  const open = competitorOpen.value;
  const urls = competitorUrls.value;
  const busy = competitorBusy.value;

  return (
    <div data-testid="competitor-panel" style={{ padding: '2px 14px' }}>
      <button
        type="button"
        className="btn btn-soft"
        data-testid="competitor-toggle"
        style={{ fontSize: '11.5px', padding: '4px 10px' }}
        onClick={() => { competitorOpen.value = !open; }}
        aria-expanded={open}
      >
        <i className={`ti ${open ? 'ti-chevron-down' : 'ti-chevron-right'}`}></i>{' '}
        Competitors (optional){urls.length > 0 ? ` · ${urls.length}` : ''}
      </button>

      {open ? (
        <div className="card" style={{ marginTop: '6px', padding: '12px 14px' }}>
          <div style={{ fontSize: '12px', color: 'var(--ink-3)', marginBottom: '8px' }}>
            Paste up to {MAX_COMPETITOR_URLS} competitor URLs — Foundry compares them so your brief
            can position against what already exists.
          </div>
          <div style={{ display: 'flex', gap: '6px', marginBottom: urls.length > 0 ? '8px' : 0 }}>
            <input
              className="plain-input"
              data-testid="competitor-input"
              placeholder="https://competitor.com"
              value={competitorInput.value}
              style={{ flex: 1, marginBottom: 0 }}
              onInput={(e) => { competitorInput.value = (e.target as HTMLInputElement).value; }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  const rejected = addCompetitorUrls(competitorInput.value);
                  competitorNote.value = rejected;
                  if (!rejected) competitorInput.value = '';
                }
              }}
            />
            <button
              type="button"
              className="btn btn-soft"
              data-testid="competitor-add"
              style={{ fontSize: '12px', padding: '6px 11px' }}
              disabled={urls.length >= MAX_COMPETITOR_URLS}
              onClick={() => {
                const rejected = addCompetitorUrls(competitorInput.value);
                competitorNote.value = rejected;
                if (!rejected) competitorInput.value = '';
              }}
            >
              <i className="ti ti-plus"></i> Add
            </button>
            <button
              type="button"
              className="btn btn-primary"
              data-testid="competitor-analyze"
              style={{ fontSize: '12px', padding: '6px 11px' }}
              disabled={urls.length === 0 || busy}
              onClick={() => { void analyzeCompetitors(); }}
            >
              {busy
                ? <><i className="ti ti-loader-2" style={{ animation: 'spin 1s linear infinite' }}></i> Analyzing…</>
                : <><i className="ti ti-radar-2"></i> Analyze</>}
            </button>
          </div>

          {urls.length > 0 ? (
            <div data-testid="competitor-chips" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginBottom: '4px' }}>
              {urls.map((url) => (
                <span
                  key={url}
                  className="sum-chip"
                  data-testid="competitor-chip"
                  style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', maxWidth: '260px' }}
                >
                  <i className="ti ti-world"></i>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{url}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${url}`}
                    data-testid={`competitor-remove-${url}`}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'var(--ink-3)' }}
                    onClick={() => removeCompetitorUrl(url)}
                  >
                    <i className="ti ti-x"></i>
                  </button>
                </span>
              ))}
            </div>
          ) : null}

          {competitorNote.value ? (
            <div
              data-testid="competitor-note"
              style={{ marginTop: '6px', fontSize: '12px', color: 'var(--ink-2)', background: 'var(--surface-warm)', padding: '8px 10px', borderRadius: 'var(--r-sm)' }}
            >
              <i className="ti ti-info-circle" style={{ verticalAlign: 'middle' }}></i>{' '}
              {competitorNote.value}
            </div>
          ) : null}

          {competitorMatrix.value != null ? (
            <div style={{ marginTop: '8px' }}>
              <div className="sec-cap" style={{ marginBottom: '4px' }}>Competitor matrix</div>
              <MatrixView matrix={competitorMatrix.value} />
            </div>
          ) : null}

          {competitorErrors.value.length > 0 ? (
            <div data-testid="competitor-errors" style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
              {competitorErrors.value.map((e, i) => (
                <div key={i} style={{ fontSize: '11.5px', color: 'var(--danger-deep)' }}>
                  <i className="ti ti-alert-circle" style={{ verticalAlign: 'middle' }}></i>{' '}
                  {e.url ? `${e.url}: ` : ''}{e.error}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
