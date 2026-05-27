import { viewClass } from '../viewShell';

export function HelpView() {
  return (
    <>
<div className={viewClass('help')} data-view="help">
        <div className="page-head">
          <h1>Help &amp; Reference</h1>
          <p>Quick-start guides, slash commands, and the gate model explained.</p>
        </div>
        <div className="stack">

          <div className="card card-pad">
            <h3 style={{ 'fontFamily': 'var(--f-display)', 'fontWeight': '500', 'fontSize': '17px', 'marginBottom': '14px' }}>Quick start</h3>
            <div style={{ 'display': 'grid', 'gridTemplateColumns': '1fr 1fr', 'gap': '14px' }}>
              <div className="next-c">
                <div style={{ 'fontSize': '22px', 'marginBottom': '7px' }}>🚀</div>
                <div className="next-t">Start a new project</div>
                <div className="next-d">Open Build, pick a folder, tell SignalOS what you want to make — it handles the rest.</div>
              </div>
              <div className="next-c">
                <div style={{ 'fontSize': '22px', 'marginBottom': '7px' }}>🔐</div>
                <div className="next-t">Add your API key</div>
                <div className="next-d">Vault → Add secret. Keys live in your OS keychain — never sent anywhere raw.</div>
              </div>
              <div className="next-c">
                <div style={{ 'fontSize': '22px', 'marginBottom': '7px' }}>🏛️</div>
                <div className="next-t">Understand gates</div>
                <div className="next-d">Gates G0–G7 checkpoint each wave. Sign with your role to advance the phase.</div>
              </div>
              <div className="next-c">
                <div style={{ 'fontSize': '22px', 'marginBottom': '7px' }}>🧠</div>
                <div className="next-t">Check the Brain</div>
                <div className="next-d">Every decision, QA note, and artifact the AI captures lives in the Brain view.</div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="secrets-head"><h3>Slash commands</h3></div>
            <div className="srow">
              <div className="s-ic" style={{ 'background': 'var(--accent-softer)', 'color': 'var(--accent)' }}><i className="ti ti-hammer"></i></div>
              <div className="s-info"><div className="s-nm">/signal-build</div><div className="s-meta">Run the full build pipeline — lint, test, bundle</div></div>
              <div className="s-val" style={{ 'fontSize': '11px', 'color': 'var(--ink-3)', 'whiteSpace': 'nowrap' }}>Build phase</div>
            </div>
            <div className="srow">
              <div className="s-ic" style={{ 'background': 'var(--accent-softer)', 'color': 'var(--accent)' }}><i className="ti ti-eye-check"></i></div>
              <div className="s-info"><div className="s-nm">/signal-review</div><div className="s-meta">AI code review — security, style, logic gaps</div></div>
              <div className="s-val" style={{ 'fontSize': '11px', 'color': 'var(--ink-3)', 'whiteSpace': 'nowrap' }}>Any phase</div>
            </div>
            <div className="srow">
              <div className="s-ic" style={{ 'background': 'var(--accent-softer)', 'color': 'var(--accent)' }}><i className="ti ti-palette"></i></div>
              <div className="s-info"><div className="s-nm">/signal-design</div><div className="s-meta">Generate or refine UI from a description or sketch</div></div>
              <div className="s-val" style={{ 'fontSize': '11px', 'color': 'var(--ink-3)', 'whiteSpace': 'nowrap' }}>Build phase</div>
            </div>
            <div className="srow">
              <div className="s-ic" style={{ 'background': 'var(--accent-softer)', 'color': 'var(--accent)' }}><i className="ti ti-clipboard-check"></i></div>
              <div className="s-info"><div className="s-nm">/signal-debrief</div><div className="s-meta">Summarise what was built this wave for the Brain</div></div>
              <div className="s-val" style={{ 'fontSize': '11px', 'color': 'var(--ink-3)', 'whiteSpace': 'nowrap' }}>Check phase</div>
            </div>
            <div className="srow">
              <div className="s-ic" style={{ 'background': 'var(--success-soft)', 'color': 'var(--success)' }}><i className="ti ti-git-branch"></i></div>
              <div className="s-info"><div className="s-nm">/signal-ship</div><div className="s-meta">Tag, push, and record the release in AUDIT_TRAIL</div></div>
              <div className="s-val" style={{ 'fontSize': '11px', 'color': 'var(--ink-3)', 'whiteSpace': 'nowrap' }}>Ship phase</div>
            </div>
            <div className="srow">
              <div className="s-ic" style={{ 'background': 'var(--amber-soft)', 'color': 'var(--amber-deep)' }}><i className="ti ti-shield-check"></i></div>
              <div className="s-info"><div className="s-nm">/signal-gate</div><div className="s-meta">Open the gate sign form for the current checkpoint</div></div>
              <div className="s-val" style={{ 'fontSize': '11px', 'color': 'var(--ink-3)', 'whiteSpace': 'nowrap' }}>Gate checkpoints</div>
            </div>
          </div>

          <div className="card card-pad">
            <h3 style={{ 'fontFamily': 'var(--f-display)', 'fontWeight': '500', 'fontSize': '17px', 'marginBottom': '10px' }}>Gate &amp; wave model</h3>
            <p style={{ 'fontSize': '13px', 'color': 'var(--ink-2)', 'lineHeight': '1.65', 'marginBottom': '14px' }}>Work moves in <strong style={{ 'color': 'var(--ink)' }}>waves</strong> through four phases — Plan → Build → Check → Ship — guarded by eight gates (G0–G7). Each gate is signed by the role responsible at that checkpoint.</p>
            <div className="vstats" style={{ 'gridTemplateColumns': 'repeat(4,1fr)' }}>
              <div className="vstat">
                <div className="vstat-l">G0 – G3</div>
                <div className="vstat-v" style={{ 'fontSize': '16px' }}>PO</div>
                <div style={{ 'fontSize': '11px', 'color': 'var(--ink-3)', 'marginTop': '3px' }}>Product Owner</div>
              </div>
              <div className="vstat">
                <div className="vstat-l">G3 – G4</div>
                <div className="vstat-v" style={{ 'fontSize': '16px' }}>PE</div>
                <div style={{ 'fontSize': '11px', 'color': 'var(--ink-3)', 'marginTop': '3px' }}>Principal Engineer</div>
              </div>
              <div className="vstat">
                <div className="vstat-l">G4 – G5</div>
                <div className="vstat-v" style={{ 'fontSize': '16px' }}>QA</div>
                <div style={{ 'fontSize': '11px', 'color': 'var(--ink-3)', 'marginTop': '3px' }}>Quality</div>
              </div>
              <div className="vstat">
                <div className="vstat-l">Deploy</div>
                <div className="vstat-v" style={{ 'fontSize': '16px' }}>DevOps</div>
                <div style={{ 'fontSize': '11px', 'color': 'var(--ink-3)', 'marginTop': '3px' }}>Deploy gates</div>
              </div>
            </div>
          </div>

          <div className="vault-note" style={{ 'background': 'var(--accent-softer)', 'borderRadius': 'var(--r)' }}>
            <i className="ti ti-info-circle" style={{ 'color': 'var(--accent)' }}></i>
            <p style={{ 'color': 'var(--accent-deep)' }}><strong>Terminal help.</strong> Type <code style={{ 'background': 'rgba(79,70,199,0.1)', 'padding': '1px 6px', 'borderRadius': '3px', 'fontFamily': 'var(--f-mono)', 'fontSize': '11.5px' }}>help</code> in the Terminal tab to see all available shell commands. Past Q&amp;A entries in the Brain also capture what SignalOS has already figured out for your project.</p>
          </div>

        </div>
      </div>
    </>
  );
}
