import { h } from 'preact';

export function HistoryView() {
  return (
    <>
<div className="view" data-view="history">
        <div className="page-head">
          <h1>History</h1>
          <p>Every build run, gate signing, and audit event for this project.</p>
        </div>
        <div className="stack">
          <div className="vstats">
            <div className="vstat"><div className="vstat-l">Build runs</div><div className="vstat-v">12</div></div>
            <div className="vstat"><div className="vstat-l">Gates signed</div><div className="vstat-v g">3</div></div>
            <div className="vstat"><div className="vstat-l">Open defects</div><div className="vstat-v" style={{ 'color': 'var(--success)' }}>0</div></div>
          </div>
          <div className="card">
            <div className="secrets-head">
              <h3>Build &amp; audit log</h3>
              <div style={{ 'display': 'flex', 'gap': '8px' }}>
                <button className="btn btn-soft" style={{ 'fontSize': '12px', 'padding': '8px 13px' }} onClick={() => window.exportHandoff(this)}><i className="ti ti-download"></i> Export handoff</button>
                <button className="btn btn-soft" style={{ 'fontSize': '12px', 'padding': '8px 13px' }} onClick={() => window.exportReport(this)}><i className="ti ti-file-report"></i> Issue report</button>
              </div>
            </div>
            <div className="history-item">
              <div className="history-ic sign"><i className="ti ti-pencil"></i></div>
              <div className="history-tx">
                <div className="history-title">Gate 3 signed — Make the menu</div>
                <div className="history-meta">Samer · PO · today at 11:22</div>
              </div>
              <span className="history-badge done">Signed</span>
            </div>
            <div className="history-item">
              <div className="history-ic build"><i className="ti ti-hammer"></i></div>
              <div className="history-tx">
                <div className="history-title">/signal-build · Gate 4 · recipes.js + toppings.js written</div>
                <div className="history-meta">2 files · 187 tokens · $0.004 · today at 10:58</div>
              </div>
              <span className="history-badge done">Done</span>
            </div>
            <div className="history-item">
              <div className="history-ic build"><i className="ti ti-hammer"></i></div>
              <div className="history-tx">
                <div className="history-title">/signal-build · Gate 4 · game.js updated</div>
                <div className="history-meta">1 file · 312 tokens · $0.006 · today at 10:43</div>
              </div>
              <span className="history-badge done">Done</span>
            </div>
            <div className="history-item">
              <div className="history-ic sign"><i className="ti ti-pencil"></i></div>
              <div className="history-tx">
                <div className="history-title">Gate 2 signed — Sketch it out</div>
                <div className="history-meta">Samer · PO · yesterday at 16:04</div>
              </div>
              <span className="history-badge done">Signed</span>
            </div>
            <div className="history-item">
              <div className="history-ic freeze"><i className="ti ti-alert-triangle"></i></div>
              <div className="history-tx">
                <div className="history-title">Rule override — test-first</div>
                <div className="history-meta">Reason: "Design phase only, no beliefs yet" · yesterday at 15:48</div>
              </div>
              <span className="history-badge" style={{ 'background': 'var(--amber-soft)', 'color': 'var(--amber-deep)' }}>Override</span>
            </div>
            <div className="history-item">
              <div className="history-ic build"><i className="ti ti-hammer"></i></div>
              <div className="history-tx">
                <div className="history-title">/signal-build · Gate 2 · index.html + styles.css written</div>
                <div className="history-meta">2 files · 544 tokens · $0.011 · yesterday at 15:30</div>
              </div>
              <span className="history-badge done">Done</span>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
