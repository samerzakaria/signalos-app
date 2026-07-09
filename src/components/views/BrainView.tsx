import { brainList, brainFilter, brainSearch } from '../../state';
import { viewClass } from '../viewShell';

export function BrainView() {
  const query = brainSearch.value.trim().toLowerCase();
  const entries = query
    ? brainList.value.filter((e) => {
        const title = (e.title || e.text || '').toLowerCase();
        const body = (e.body || e.text || '').toLowerCase();
        return title.includes(query) || body.includes(query);
      })
    : brainList.value;
  const filter = brainFilter.value;
  const cls = (f: string) => filter === f ? 'brain-type active' : 'brain-type';

  return (
    <>
<div className={viewClass('brain')} data-view="brain">
        <div className="page-head">
          <h1>Brain</h1>
          <p>Notes, decisions, and artifacts saved across your project.</p>
        </div>
        <div className="stack">
          <div style={{ 'display': 'flex', 'alignItems': 'center', 'gap': '12px', 'marginBottom': '4px' }}>
            <div className="sb-search" style={{ 'flex': '1', 'margin': '0' }}>
              <i className="ti ti-search"></i>
              <input placeholder="Search notes and decisions…" value={brainSearch.value} onInput={(e) => { brainSearch.value = (e.target as HTMLInputElement).value; }}/>
            </div>
            <button className="btn btn-soft" onClick={() => window.addBrainEntry()}><i className="ti ti-plus"></i> Add note</button>
          </div>
          <div className="brain-type-seg">
            <div className={cls('all')} onClick={() => window.filterBrain(null, 'all')}>All</div>
            <div className={cls('note')} onClick={() => window.filterBrain(null, 'note')}><i className="ti ti-notes" style={{ 'fontSize': '13px' }}></i> Notes</div>
            <div className={cls('decision')} onClick={() => window.filterBrain(null, 'decision')}><i className="ti ti-scale" style={{ 'fontSize': '13px' }}></i> Decisions</div>
            <div className={cls('artifact')} onClick={() => window.filterBrain(null, 'artifact')}><i className="ti ti-file-code" style={{ 'fontSize': '13px' }}></i> Artifacts</div>
            <div className={cls('qa')} onClick={() => window.filterBrain(null, 'qa')}><i className="ti ti-help-circle" style={{ 'fontSize': '13px' }}></i> Q&amp;A</div>
          </div>
          <div className="card">
            
            {entries.length === 0 ? (
              <div style={{ padding: '24px', textAlign: 'center', color: 'var(--ink-3)' }}>
                No brain entries yet. Use /signal-brain in the Build tab to add notes.
              </div>
            ) : (
              entries.map((e, idx) => {
                const type = (e.entry_type || e.type || "note").toLowerCase();
                const typeMap: Record<string, {cls: string, icon: string, label: string}> = {
                  note: { cls: "note", icon: "ti-notes", label: "Note" },
                  decision: { cls: "decision", icon: "ti-scale", label: "Decision" },
                  artifact: { cls: "artifact", icon: "ti-file-code", label: "Artifact" },
                  qa: { cls: "qa", icon: "ti-help-circle", label: "Q&A" },
                };
                const t = typeMap[type] || typeMap.note;
                
                return (
                  <div className="brain-row" key={idx}>
                    <div className={`brain-type-ic ${t.cls}`}><i className={`ti ${t.icon}`}></i></div>
                    <div className="brain-tx">
                      <div className="brain-title">{e.title || e.text?.slice(0, 80) || ""}</div>
                      <div className="brain-body">{e.body || e.text || ""}</div>
                      <div className="brain-meta">
                        <span>{e.ts || e.created_at || "Just now"}</span>
                        <span className="brain-tag">{t.label}</span>
                      </div>
                    </div>
                  </div>
                );
              })
            )}

          </div>
        </div>
      </div>
    </>
  );
}
