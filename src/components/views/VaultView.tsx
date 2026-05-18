import { secretsList } from '../../state';

export function VaultView() {
  const list = secretsList.value;
  const n = list.length;
  const heroTx = n === 0 ? "No secrets stored yet" : n === 1 ? "One secret safely sealed" : n + " secrets safely sealed";

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
              <h2>{heroTx}</h2>
              <p>Stored in your OS keychain. Never sent to the AI. Never written into project files.</p>
            </div>
          </div>
          <div className="vstats">
            <div className="vstat"><div className="vstat-l">Secrets</div><div className="vstat-v">{n}</div></div>
            <div className="vstat"><div className="vstat-l">Encryption</div><div className="vstat-v g">AES-256</div></div>
            <div className="vstat"><div className="vstat-l">Last unlock</div><div className="vstat-v" style={{ 'fontSize': '16px' }}>Just now</div></div>
          </div>
          <div className="card">
            <div className="secrets-head">
              <h3>Project keys</h3>
              <button className="btn btn-soft" onClick={() => window.openAddSecret()}><i className="ti ti-plus"></i> Add secret</button>
            </div>
            
            {list.length === 0 ? (
              <div style={{ padding: '24px', textAlign: 'center', color: 'var(--ink-3)', fontSize: '13px' }}>
                No secrets yet. Click Add secret to store your first key.
              </div>
            ) : (
              list.map((s, i) => {
                const name = s.name || s.key || "";
                return (
                  <div className="srow" data-secret-name={name} key={i}>
                    <div className="s-ic"><i className="ti ti-key"></i></div>
                    <div className="s-info"><div className="s-nm">{name}</div><div className="s-meta">{s.file || ".env.local"}</div></div>
                    <div className="s-val">••••••••••••••••</div>
                    <div className="s-act">
                      <div className="ico" onClick={(e) => window.toggleSecret(e.currentTarget)} aria-label="Reveal"><i className="ti ti-eye"></i></div>
                      <div className="ico" onClick={(e) => window.copySecret(e.currentTarget)} aria-label="Copy"><i className="ti ti-copy"></i></div>
                      <div className="ico" onClick={(e) => window.deleteSecret(e.currentTarget)} aria-label="Delete"><i className="ti ti-trash"></i></div>
                    </div>
                  </div>
                );
              })
            )}

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
