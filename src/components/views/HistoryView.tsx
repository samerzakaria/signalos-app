import { auditList } from '../../state';
import { viewClass } from '../viewShell';
import { AuditTimeTravel } from '../AuditTimeTravel';

export function HistoryView() {
  const entries = auditList.value;

  return (
    <>
<div className={viewClass('history')} data-view="history">
        <div className="page-head">
          <h1>History</h1>
          <p>Every build run, gate signing, and audit event for this project.</p>
        </div>
        <div className="stack">
          <div className="card">
            <div className="secrets-head">
              <h3>Build &amp; audit log</h3>
              <div style={{ 'display': 'flex', 'gap': '8px' }}>
                <button className="btn btn-soft" style={{ 'fontSize': '12px', 'padding': '8px 13px' }} onClick={(e) => window.exportHandoff(e.currentTarget)}><i className="ti ti-download"></i> Export handoff</button>
                <button className="btn btn-soft" style={{ 'fontSize': '12px', 'padding': '8px 13px' }} onClick={(e) => window.exportReport(e.currentTarget)}><i className="ti ti-file-report"></i> Issue report</button>
              </div>
            </div>
            {entries.length === 0 ? (
              <div style={{ padding: '24px', textAlign: 'center', color: 'var(--ink-3)', fontSize: '13px' }}>
                No history yet. Audit events appear here as you build and sign gates.
              </div>
            ) : (
              entries.map((entry, idx) => {
                const action = entry.action || '';
                const lower = action.toLowerCase();
                const kind = lower.includes('sign') ? 'sign'
                           : lower.includes('override') ? 'freeze'
                           : 'build';
                const icon = kind === 'sign' ? 'ti-pencil'
                           : kind === 'freeze' ? 'ti-alert-triangle'
                           : 'ti-hammer';
                const badgeLabel = kind === 'sign' ? 'Signed'
                                 : kind === 'freeze' ? 'Override'
                                 : 'Done';
                const badgeStyle = kind === 'freeze'
                  ? { background: 'var(--amber-soft)', color: 'var(--amber-deep)' }
                  : undefined;
                const badgeClass = kind === 'freeze' ? 'history-badge' : 'history-badge done';
                const ts = entry.ts || entry.timestamp || '';
                return (
                  <div className="history-item" key={idx}>
                    <div className={`history-ic ${kind}`}><i className={`ti ${icon}`}></i></div>
                    <div className="history-tx">
                      <div className="history-title">{action}</div>
                      <div className="history-meta">{ts}</div>
                    </div>
                    <span className={badgeClass} style={badgeStyle}>{badgeLabel}</span>
                  </div>
                );
              })
            )}
          </div>
          <AuditTimeTravel />
        </div>
      </div>
    </>
  );
}
