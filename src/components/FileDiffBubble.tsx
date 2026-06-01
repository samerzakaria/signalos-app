// FileDiffBubble.tsx — inline green/red diff for a file edit (Phase 1.4).
// Shows a compact hunk by default with a collapsible full-file view. Drives
// test T14 ("file diff shown in chat").
//
// Preact only. Pure presentational — the diff lines are computed by a small
// dependency-free LCS line differ so we never need a runtime diff library.

import { useSignal } from '@preact/signals';

export interface FileDiffBubbleProps {
  /** Path of the file that changed. */
  path: string;
  /** Previous file content (empty string for a new file). */
  before?: string;
  /** New file content. */
  after?: string;
  /**
   * Pre-computed diff lines, if the caller already has them (e.g. from the
   * agent loop). When omitted, the diff is computed from before/after.
   */
  lines?: DiffLine[];
  /** Start collapsed (compact) — defaults to true. */
  collapsed?: boolean;
}

export interface DiffLine {
  kind: 'add' | 'del' | 'ctx';
  text: string;
}

/** Minimal LCS-based line diff. Good enough for review display; not a merge. */
export function computeDiff(before: string, after: string): DiffLine[] {
  const a = before === '' ? [] : before.replace(/\n$/, '').split('\n');
  const b = after === '' ? [] : after.replace(/\n$/, '').split('\n');
  const n = a.length;
  const m = b.length;
  // LCS DP table
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: 'ctx', text: a[i] });
      i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: 'del', text: a[i] });
      i++;
    } else {
      out.push({ kind: 'add', text: b[j] });
      j++;
    }
  }
  while (i < n) { out.push({ kind: 'del', text: a[i++] }); }
  while (j < m) { out.push({ kind: 'add', text: b[j++] }); }
  return out;
}

function summarize(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const l of lines) {
    if (l.kind === 'add') added++;
    else if (l.kind === 'del') removed++;
  }
  return { added, removed };
}

export function FileDiffBubble({ path, before, after, lines, collapsed }: FileDiffBubbleProps) {
  const open = useSignal(collapsed === false);
  const diff = lines ?? computeDiff(before ?? '', after ?? '');
  const { added, removed } = summarize(diff);
  // Compact view: only changed lines (+ a little context is omitted for brevity).
  const changed = diff.filter((l) => l.kind !== 'ctx');
  const shown = open.value ? diff : changed.slice(0, 12);
  const hiddenCount = open.value ? 0 : Math.max(0, changed.length - shown.length);

  return (
    <div className="msg spark" data-testid="file-diff-bubble">
      <div className="msg-av"><i className="ti ti-git-commit" style={{ fontSize: '17px' }}></i></div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="file-diff" data-path={path}>
          <div className="file-diff-head">
            <i className="ti ti-file-diff"></i>
            <span className="file-diff-path">{path}</span>
            <span className="file-diff-stat">
              <span className="file-diff-add">+{added}</span>
              <span className="file-diff-del">-{removed}</span>
            </span>
            <button
              type="button"
              className="file-diff-toggle"
              data-testid="file-diff-toggle"
              onClick={() => { open.value = !open.value; }}
              aria-expanded={open.value}
            >
              {open.value ? 'Collapse' : 'Full file'}
            </button>
          </div>
          <pre className="file-diff-body">
            {shown.map((l, idx) => (
              <div className={`file-diff-line ${l.kind}`} key={idx}>
                <span className="file-diff-gutter">{l.kind === 'add' ? '+' : l.kind === 'del' ? '-' : ' '}</span>
                <span className="file-diff-text">{l.text}</span>
              </div>
            ))}
            {hiddenCount > 0 ? (
              <div className="file-diff-more">… {hiddenCount} more changed line{hiddenCount === 1 ? '' : 's'}</div>
            ) : null}
          </pre>
        </div>
      </div>
    </div>
  );
}
