import { secretsList, revealedSecrets, copiedSecret, bulkImportOpen, bulkImportText, bulkImportDiff, bulkImportError, bulkImportAllowRemovals, BulkDiffResult } from '../../state';
import { secrets } from '../../js/ipc.js';

function BulkImportModal() {
  const open = bulkImportOpen.value;
  if (!open) return null;

  const text = bulkImportText.value;
  const diff = bulkImportDiff.value;
  const error = bulkImportError.value;
  const allowRemovals = bulkImportAllowRemovals.value;

  const closeBulk = () => {
    bulkImportOpen.value = false;
    bulkImportText.value = '';
    bulkImportDiff.value = null;
    bulkImportError.value = null;
    bulkImportAllowRemovals.value = false;
  };

  const computeDiff = async () => {
    bulkImportError.value = null;
    bulkImportDiff.value = null;
    try {
      const result = await secrets.applyDiff('.env.local', text, false) as BulkDiffResult;
      if (result.applied) {
        closeBulk();
        return;
      }
      bulkImportDiff.value = result;
    } catch (e: unknown) {
      bulkImportError.value = (e as Error)?.message || String(e);
    }
  };

  const applyBulk = async () => {
    bulkImportError.value = null;
    try {
      const result = await secrets.applyDiff('.env.local', text, allowRemovals) as BulkDiffResult;
      if (!result.applied && result.removed && result.removed.length > 0 && !allowRemovals) {
        bulkImportError.value = `${result.removed.length} secrets would be removed. Check the box to confirm.`;
        return;
      }
      if (result.applied) {
        closeBulk();
      } else {
        // Force apply with removals allowed
        const final = await secrets.applyDiff('.env.local', text, true) as BulkDiffResult;
        if (final.applied) {
          closeBulk();
        }
      }
    } catch (e: unknown) {
      bulkImportError.value = (e as Error)?.message || String(e);
    }
  };

  return (
    <div className="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget) closeBulk(); }}>
      <div className="modal-box" style={{ maxWidth: '560px' }}>
        <div className="modal-head">
          <h3>Import .env</h3>
          <button className="ico" onClick={closeBulk} aria-label="Close"><i className="ti ti-x"></i></button>
        </div>
        <div className="modal-body" style={{ padding: '16px' }}>
          <p style={{ fontSize: '13px', color: 'var(--ink-3)', marginBottom: '12px' }}>
            Paste a <code>.env</code> block below. SignalOS diffs it against the current file and shows what will change. Nothing is written until you confirm.
          </p>
          <textarea
            className="env-textarea"
            style={{ width: '100%', minHeight: '140px', fontFamily: 'var(--mono)', fontSize: '12px', padding: '10px', borderRadius: '6px', border: '1px solid var(--border)', background: 'var(--bg-2)', color: 'var(--ink-1)', resize: 'vertical' }}
            placeholder={'DATABASE_URL=postgres://...\nSTRIPE_SECRET_KEY=sk_test_...\nNEXT_PUBLIC_API_URL=http://localhost:3000'}
            value={text}
            onInput={(e) => { bulkImportText.value = (e.target as HTMLTextAreaElement).value; }}
          />
          <div style={{ marginTop: '10px', display: 'flex', gap: '8px' }}>
            <button className="btn btn-soft" onClick={computeDiff}>Compute diff</button>
          </div>

          {error && (
            <div style={{ marginTop: '10px', padding: '8px 12px', background: 'var(--red-bg, #ffeaea)', color: 'var(--red, #c00)', borderRadius: '6px', fontSize: '12px' }}>
              {error}
            </div>
          )}

          {diff && (
            <div style={{ marginTop: '12px' }}>
              <div style={{ display: 'grid', gap: '4px', fontSize: '12px', fontFamily: 'var(--mono)' }}>
                {diff.added.map((n) => <div key={n} style={{ color: 'var(--green, #2a7)' }}>+ {n}</div>)}
                {diff.changed.map((n) => <div key={n} style={{ color: 'var(--amber, #c80)' }}>~ {n}</div>)}
                {diff.unchanged.map((n) => <div key={n} style={{ color: 'var(--ink-3)' }}>= {n}</div>)}
                {diff.removed.map((n) => <div key={n} style={{ color: 'var(--red, #c00)' }}>- {n} (will be removed)</div>)}
                {diff.added.length === 0 && diff.changed.length === 0 && diff.removed.length === 0 && (
                  <div style={{ color: 'var(--ink-3)' }}>No changes.</div>
                )}
              </div>

              {diff.removed.length > 0 && (
                <label style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '10px', fontSize: '12px', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={allowRemovals}
                    onChange={(e) => { bulkImportAllowRemovals.value = (e.target as HTMLInputElement).checked; }}
                  />
                  I understand that secrets will be removed.
                </label>
              )}

              <div style={{ marginTop: '12px', display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                <button className="btn btn-ghost" onClick={closeBulk}>Cancel</button>
                <button className="btn btn-primary" onClick={applyBulk}>Apply changes</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function VaultView() {
  const list = secretsList.value;
  const revealed = revealedSecrets.value;
  const copied = copiedSecret.value;
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
              <div style={{ display: 'flex', gap: '8px' }}>
                <button className="btn btn-soft" onClick={() => { bulkImportOpen.value = true; }}><i className="ti ti-file-import"></i> Import .env</button>
                <button className="btn btn-soft" onClick={() => window.openAddSecret()}><i className="ti ti-plus"></i> Add secret</button>
              </div>
            </div>

            {list.length === 0 ? (
              <div style={{ padding: '24px', textAlign: 'center', color: 'var(--ink-3)', fontSize: '13px' }}>
                No secrets yet. Click Add secret to store your first key.
              </div>
            ) : (
              list.map((s, i) => {
                const name = s.name || s.key || "";
                const isRevealed = name in revealed;
                const displayValue = isRevealed ? revealed[name] : "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";
                const eyeIcon = isRevealed ? 'ti-eye-off' : 'ti-eye';
                const copyIcon = copied === name ? 'ti-check' : 'ti-copy';
                return (
                  <div className="srow" data-secret-name={name} key={i}>
                    <div className="s-ic"><i className="ti ti-key"></i></div>
                    <div className="s-info"><div className="s-nm">{name}</div><div className="s-meta">{s.file || ".env.local"}</div></div>
                    <div className="s-val">{displayValue}</div>
                    <div className="s-act">
                      <div className="ico" onClick={() => window.toggleSecret(name)} aria-label="Reveal"><i className={`ti ${eyeIcon}`}></i></div>
                      <div className="ico" onClick={() => window.copySecret(name)} aria-label="Copy"><i className={`ti ${copyIcon}`}></i></div>
                      <div className="ico" onClick={() => window.deleteSecret(name)} aria-label="Delete"><i className="ti ti-trash"></i></div>
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
      <BulkImportModal />
    </>
  );
}
