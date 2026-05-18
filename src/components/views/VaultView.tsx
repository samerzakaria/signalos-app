import { h } from 'preact';

export function VaultView() {
  return (
    <>
<div className="view" data-view="vault">
        <div className="page-head">
          <h1>The Vault</h1>
          <p>Every secret key, sealed tight. Not even SignalOS can read them.</p>
        </div>
        <div className="stack">
          <div className="vault-hero">
            <div className="vh-ic"><i className="ti ti-shield-lock"></i></div>
            <div className="vh-tx">
              <h2>Three secrets safely sealed</h2>
              <p>Stored in your OS keychain. Never sent to the AI. Never written into project files.</p>
            </div>
          </div>
          <div className="vstats">
            <div className="vstat"><div className="vstat-l">Secrets</div><div className="vstat-v">3</div></div>
            <div className="vstat"><div className="vstat-l">Encryption</div><div className="vstat-v g">AES-256</div></div>
            <div className="vstat"><div className="vstat-l">Last unlock</div><div className="vstat-v" style={{ 'fontSize': '16px' }}>2 min ago</div></div>
          </div>
          <div className="card">
            <div className="secrets-head">
              <h3>Project keys</h3>
              <button className="btn btn-soft" onClick={() => window.openAddSecret()}><i className="ti ti-plus"></i> Add secret</button>
            </div>
            <div className="srow">
              <div className="s-ic"><i className="ti ti-key"></i></div>
              <div className="s-info"><div className="s-nm">CLAUDE_API_KEY</div><div className="s-meta">AI brain · used 2 min ago</div></div>
              <div className="s-val" data-real="sk-ant-api03-Xa9bC2dE3fG••••">••••••••••••••••</div>
              <div className="s-act">
                <div className="ico" onClick={() => window.toggleSecret(this)} aria-label="Reveal"><i className="ti ti-eye"></i></div>
                <div className="ico" onClick={() => window.copySecret(this)} aria-label="Copy"><i className="ti ti-copy"></i></div>
              </div>
            </div>
            <div className="srow">
              <div className="s-ic"><i className="ti ti-brand-github"></i></div>
              <div className="s-info"><div className="s-nm">GITHUB_TOKEN</div><div className="s-meta">Save work online · used 1 hour ago</div></div>
              <div className="s-val" data-real="ghp_AbCdEf1234567890XyZ">••••••••••••••••</div>
              <div className="s-act">
                <div className="ico" onClick={() => window.toggleSecret(this)} aria-label="Reveal"><i className="ti ti-eye"></i></div>
                <div className="ico" onClick={() => window.copySecret(this)} aria-label="Copy"><i className="ti ti-copy"></i></div>
              </div>
            </div>
            <div className="srow">
              <div className="s-ic"><i className="ti ti-database"></i></div>
              <div className="s-info"><div className="s-nm">SUPABASE_URL</div><div className="s-meta">Save game scores · used yesterday</div></div>
              <div className="s-val" data-real="https://abcd.supabase.co">••••••••••••••••</div>
              <div className="s-act">
                <div className="ico" onClick={() => window.toggleSecret(this)} aria-label="Reveal"><i className="ti ti-eye"></i></div>
                <div className="ico" onClick={() => window.copySecret(this)} aria-label="Copy"><i className="ti ti-copy"></i></div>
              </div>
            </div>
          </div>
          <div className="vault-note">
            <i className="ti ti-eye-off"></i>
            <p><strong>What "never sent to the AI" means.</strong> When SignalOS needs a key — to save your work to GitHub, say — the request goes straight from your computer to GitHub. The AI only ever sees that a key was used, never the key itself.</p>
          </div>
        </div>
      </div>
    </>
  );
}
