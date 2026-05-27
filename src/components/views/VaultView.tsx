import { secretsList, revealedSecrets, copiedSecret, bulkImportOpen, bulkImportText, bulkImportDiff, bulkImportError, bulkImportAllowRemovals, BulkDiffResult } from '../../state';
import { secrets } from '../../js/ipc.js';
import { viewClass } from '../viewShell';

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
    <div
      className="modal-overlay open bulk-import-overlay"
      data-testid="bulk-import-modal"
      onClick={(e) => { if (e.target === e.currentTarget) closeBulk(); }}
    >
      <div className="modal bulk-import-modal" role="dialog" aria-modal="true" aria-labelledby="bulk-import-title">
        <div className="modal-head">
          <h3 id="bulk-import-title">Import .env</h3>
          <button className="ico" onClick={closeBulk} aria-label="Close"><i className="ti ti-x"></i></button>
        </div>
        <div className="modal-body bulk-import-body">
          <p className="bulk-import-copy">
            Paste a <code>.env</code> block below. SignalOS diffs it against the current file and shows what will change. Nothing is written until you confirm.
          </p>
          <textarea
            className="env-textarea"
            placeholder={'DATABASE_URL=postgres://...\nSTRIPE_SECRET_KEY=sk_test_...\nNEXT_PUBLIC_API_URL=http://localhost:3000'}
            value={text}
            onInput={(e) => { bulkImportText.value = (e.target as HTMLTextAreaElement).value; }}
          />
          <div className="bulk-import-actions">
            <button className="btn btn-soft" onClick={computeDiff}>Compute diff</button>
          </div>

          {error && (
            <div className="bulk-import-error">
              {error}
            </div>
          )}

          {diff && (
            <div className="bulk-import-diff">
              <div className="bulk-import-diff-list">
                {diff.added.map((n) => <div className="bulk-diff-row added" key={n}>+ {n}</div>)}
                {diff.changed.map((n) => <div className="bulk-diff-row changed" key={n}>~ {n}</div>)}
                {diff.unchanged.map((n) => <div className="bulk-diff-row unchanged" key={n}>= {n}</div>)}
                {diff.removed.map((n) => <div className="bulk-diff-row removed" key={n}>- {n} (will be removed)</div>)}
                {diff.added.length === 0 && diff.changed.length === 0 && diff.removed.length === 0 && (
                  <div className="bulk-diff-row unchanged">No changes.</div>
                )}
              </div>

              {diff.removed.length > 0 && (
                <label className="bulk-import-removal">
                  <input
                    type="checkbox"
                    checked={allowRemovals}
                    onChange={(e) => { bulkImportAllowRemovals.value = (e.target as HTMLInputElement).checked; }}
                  />
                  I understand that secrets will be removed.
                </label>
              )}

              <div className="bulk-import-footer">
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
<div className={viewClass('vault')} data-view="vault">
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
