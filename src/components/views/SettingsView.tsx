import {
  userName, userRole, ai, aiModel, providerModels, providerModelsError,
  providerModelsLoading, currentCost,
  workspacePath, monthlyCap, engineRunning, engineTestState,
  engineRestartState, updateCheck, updateChannel,
  productProfiles, recentWorkspaces, selectedProductProfile,
} from '../../state';
import { loadProviderModels } from '../../services/providerModels';

const PROVIDERS = [
  { id: 'anthropic', label: 'Claude (Anthropic)' },
  { id: 'openai', label: 'OpenAI' },
  { id: 'gemini', label: 'Gemini (Google)' },
  { id: 'qwen', label: 'Qwen' },
  { id: 'ollama', label: 'Ollama (local)' },
  { id: 'openrouter', label: 'OpenRouter' },
  { id: 'deepseek', label: 'DeepSeek' },
  { id: 'mistral', label: 'Mistral' },
  { id: 'groq', label: 'Groq' },
  { id: 'cerebras', label: 'Cerebras' },
  { id: 'together', label: 'Together AI' },
  { id: 'xai', label: 'xAI' },
];

export function SettingsView() {
  const models = providerModels.value;
  const selectedModel = aiModel.value;
  const modelSelectValue = models.some((model) => model.id === selectedModel) ? selectedModel : '';
  const provider = ai.value || 'anthropic';
  const role = userRole.value || 'PO';
  const cap = monthlyCap.value;
  const spend = currentCost.value;
  const running = engineRunning.value;
  const testSt = engineTestState.value;
  const restartSt = engineRestartState.value;
  const upd = updateCheck.value;
  const channel = updateChannel.value || 'beta';
  const recents = recentWorkspaces.value;
  const profiles = productProfiles.value;
  const selectedProfile = selectedProductProfile.value || 'generic';

  const engBadgeContent = running === false
    ? <><span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--danger)', display: 'inline-block' }}></span> Stopped</>
    : <><span className="dot"></span> Running</>;

  const testBtn = testSt === 'testing'
    ? <><i className="ti ti-loader-2" style={{ animation: 'spin 1s linear infinite' }}></i> Testing…</>
    : testSt === 'ok'
    ? <><i className="ti ti-circle-check" style={{ color: 'var(--success)' }}></i> OK</>
    : testSt === 'failed'
    ? <><i className="ti ti-alert-circle" style={{ color: 'var(--danger)' }}></i> Failed</>
    : <><i className="ti ti-activity"></i> Test</>;

  const restartBtn = restartSt === 'restarting'
    ? <><i className="ti ti-loader-2" style={{ animation: 'spin 1s linear infinite' }}></i> Restarting…</>
    : <><i className="ti ti-refresh"></i> Restart</>;

  const updateBtnContent = upd.checking
    ? <><i className="ti ti-loader-2" style={{ animation: 'spin 1s linear infinite' }}></i> Checking…</>
    : <><i className="ti ti-cloud-download"></i> Check for updates</>;

  const updateResultCls = upd.visible ? 'update-result visible' : 'update-result';
  const updateResultIcon = upd.hasUpdate ? 'ti ti-cloud-download' : 'ti ti-circle-check';
  const updateResultColor = upd.hasUpdate ? 'var(--accent)' : 'var(--success)';

  return (
    <>
<div className="view" data-view="settings">
        <div className="page-head">
          <h1>Settings</h1>
          <p>Workspace, AI, spend cap, and engine diagnostics.</p>
        </div>
        <div className="stack" style={{ 'maxWidth': '680px' }}>
          <div className="card">
            <div className="settings-section" style={{ 'padding': '0' }}>
              <div className="secrets-head"><h3>Identity</h3></div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Your name</strong><span>Recorded on every gate you sign</span></div>
                <input type="text" className="plain-input" id="settingsName" placeholder="Your name" value={userName.value} onInput={(e) => { userName.value = (e.target as HTMLInputElement).value; }} onChange={() => window.saveIdentity()} style={{ 'width': '180px', 'border': '1px solid var(--line-2)', 'borderRadius': 'var(--r-sm)', 'padding': '8px 11px', 'fontSize': '13px', 'background': 'var(--surface)' }}/>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Your role</strong><span>Used when signing gates</span></div>
                <select className="select-input" id="settingsRole" value={role} onInput={(e) => { userRole.value = (e.target as HTMLSelectElement).value; }} onChange={() => window.saveIdentity()} style={{ 'width': 'auto', 'padding': '8px 28px 8px 12px' }}>
                  <option value="PO">PO — Product Owner</option>
                  <option value="PE">PE — Principal Engineer</option>
                  <option value="QA">QA — Quality</option>
                  <option value="DevOps">DevOps</option>
                </select>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="settings-section" style={{ 'padding': '0' }}>
              <div className="secrets-head"><h3>Workspace</h3></div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Active folder</strong><span>The project SignalOS is working in</span></div>
                <span className="settings-path" id="settingsWorkspacePath">{workspacePath.value || '(none)'}</span>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Product profile</strong><span>Controls init validation and preview defaults</span></div>
                <select className="select-input" id="settingsProductProfile" value={selectedProfile} onInput={(e) => { selectedProductProfile.value = (e.target as HTMLSelectElement).value; }} onChange={() => window.changeStack()} style={{ 'width': 'auto', 'padding': '8px 28px 8px 12px' }}>
                  {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                </select>
              </div>
              {recents.length > 0 ? (
                <div className="settings-row recent-workspaces-row">
                  <div className="settings-row-tx"><strong>Recent products</strong><span>Switch without leaving the app</span></div>
                  <div className="recent-workspaces" id="settingsRecentWorkspaces">
                    {recents.map((ws) => {
                      const active = ws.path === workspacePath.value;
                      const cls = active ? 'recent-workspace active' : 'recent-workspace';
                      const icon = ws.exists === false ? 'ti-alert-triangle' : ws.initialized ? 'ti-folder-check' : 'ti-folder';
                      return (
                        <button key={ws.path} className={cls} onClick={() => window.switchWorkspace(ws.path)} disabled={active || ws.exists === false} title={ws.path}>
                          <i className={`ti ${icon}`}></i>
                          <span>{ws.name || ws.path}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Forget this folder</strong><span>Removes it from SignalOS — files stay on your computer</span></div>
                <button className="btn btn-soft" style={{ 'fontSize': '12.5px', 'padding': '8px 14px' }} onClick={() => window.forgetWorkspace()}><i className="ti ti-trash"></i> Forget</button>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="settings-section" style={{ 'padding': '0' }}>
              <div className="secrets-head"><h3>AI connection</h3></div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Provider</strong><span>Current AI brain</span></div>
                <select className="select-input" id="settingsProvider" value={provider} onInput={(e) => { ai.value = (e.target as HTMLSelectElement).value; }} onChange={() => window.changeProvider()} style={{ 'width': 'auto', 'padding': '8px 28px 8px 12px' }}>
                  {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Model</strong><span>Specific version to use</span></div>
                <select className="select-input" id="settingsModel" style={{ 'width': 'auto', 'padding': '8px 28px 8px 12px' }} value={modelSelectValue} disabled={models.length === 0} onInput={(e) => { aiModel.value = (e.target as HTMLSelectElement).value; }} onChange={() => window.changeModel()}>
                  {models.length === 0 ? (
                    <option value="">{providerModelsLoading.value ? 'Loading models...' : 'No models loaded'}</option>
                  ) : (
                    models.map((m) => <option key={m.id} value={m.id}>{m.name || m.id}</option>)
                  )}
                </select>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Refresh models</strong><span>{providerModelsError.value || (models.length > 0 ? `${models.length} available` : 'Fetch from the selected provider')}</span></div>
                <button className="btn btn-soft" style={{ 'fontSize': '12.5px', 'padding': '8px 14px' }} onClick={() => { void loadProviderModels(provider, null, { persistSelection: true }); }} disabled={providerModelsLoading.value}>
                  <i className={`ti ${providerModelsLoading.value ? 'ti-loader-2' : 'ti-refresh'}`} style={providerModelsLoading.value ? { animation: 'spin 1s linear infinite' } : undefined}></i> Fetch models
                </button>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>API key</strong><span>Stored in OS keychain</span></div>
                <button className="btn btn-soft" style={{ 'fontSize': '12.5px', 'padding': '8px 14px' }} onClick={() => window.replaceApiKey()}><i className="ti ti-key"></i> Replace key</button>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="settings-section" style={{ 'padding': '0' }}>
              <div className="secrets-head"><h3>Budget</h3></div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Monthly cap</strong><span>SignalOS stops calling the AI when you hit this</span></div>
                <div className="budget-wrap" style={{ 'width': 'auto', 'margin': '0' }}>
                  <span className="budget-prefix">$</span>
                  <input type="number" className="budget-input" id="settingsBudget" placeholder="50" min="0" step="5" value={cap ?? ''} onInput={(e) => { const v = (e.target as HTMLInputElement).value; monthlyCap.value = v === '' ? null : parseFloat(v); }} onChange={() => window.saveBudget()} style={{ 'width': '80px', 'borderRadius': '0 var(--r-sm) var(--r-sm) 0' }}/>
                </div>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Session spend</strong><span>This session only</span></div>
                <span className="settings-path" id="settingsSessionSpend" style={{ 'fontSize': '13px', 'fontWeight': '600', 'color': 'var(--ink)' }}>${spend.toFixed(4)}</span>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Reset session</strong><span>Clear this session's cost counter</span></div>
                <button className="btn btn-soft" style={{ 'fontSize': '12.5px', 'padding': '8px 14px' }} onClick={() => window.resetSessionCost()}><i className="ti ti-refresh"></i> Reset</button>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="settings-section" style={{ 'padding': '0' }}>
              <div className="secrets-head"><h3>Engine diagnostics</h3></div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>SignalOS Core</strong><span>Python sidecar · 40 commands</span></div>
                <div className="live-badge" id="engineStatusBadge" style={{ 'fontSize': '11px' }}>{engBadgeContent}</div>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Restart engine</strong><span>If commands feel slow or unresponsive</span></div>
                <div style={{ 'display': 'flex', 'gap': '8px' }}>
                  <button className="btn btn-soft" id="testEngineBtn" style={{ 'fontSize': '12.5px', 'padding': '8px 14px' }} onClick={() => window.testEngine()}>{testBtn}</button>
                  <button className="btn btn-soft" id="restartEngineBtn" style={{ 'fontSize': '12.5px', 'padding': '8px 14px' }} onClick={() => window.restartEngine()}>{restartBtn}</button>
                </div>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="settings-section" style={{ 'padding': '0' }}>
              <div className="secrets-head"><h3>Updates</h3></div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Channel</strong><span>Which release track to follow</span></div>
                <select className="select-input" id="updateChannel" value={channel} onInput={(e) => { updateChannel.value = (e.target as HTMLSelectElement).value; }} style={{ 'width': 'auto', 'padding': '8px 28px 8px 12px' }}>
                  <option value="stable">Stable</option>
                  <option value="beta">Beta</option>
                </select>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Version</strong><span>SignalOS 1.1.1</span></div>
                <div style={{ 'display': 'flex', 'alignItems': 'center', 'gap': '10px' }}>
                  <button className="btn btn-soft" id="updateBtn" disabled={upd.checking} style={{ 'fontSize': '12.5px', 'padding': '8px 14px' }} onClick={() => window.checkForUpdates()}>{updateBtnContent}</button>
                  <div className={updateResultCls} id="updateResult"><i className={updateResultIcon} style={{ 'color': updateResultColor }}></i><span id="updateResultTx">{upd.message}</span></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
