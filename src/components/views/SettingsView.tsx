export function SettingsView() {
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
                <input type="text" className="plain-input" id="settingsName" placeholder="Your name" style={{ 'width': '180px', 'border': '1px solid var(--line-2)', 'borderRadius': 'var(--r-sm)', 'padding': '8px 11px', 'fontSize': '13px', 'background': 'var(--surface)' }} onChange={() => window.saveIdentity()}/>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Your role</strong><span>Used when signing gates</span></div>
                <select className="select-input" id="settingsRole" style={{ 'width': 'auto', 'padding': '8px 28px 8px 12px' }} onChange={() => window.saveIdentity()}>
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
                <span className="settings-path" id="settingsWorkspacePath">~/projects/my-pizza-game</span>
              </div>
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
                <select className="select-input" id="settingsProvider" style={{ 'width': 'auto', 'padding': '8px 28px 8px 12px' }} onChange={() => window.changeProvider()}>
                  <option value="anthropic" selected>Claude (Anthropic)</option>
                  <option value="openai">GPT-4o (OpenAI)</option>
                  <option value="gemini">Gemini (Google)</option>
                  <option value="ollama">Ollama (local)</option>
                </select>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Model</strong><span>Specific version to use</span></div>
                <select className="select-input" id="settingsModel" style={{ 'width': 'auto', 'padding': '8px 28px 8px 12px' }} onChange={() => window.changeModel()}>
                  <option value="claude-sonnet-4-6" selected>claude-sonnet-4-6</option>
                  <option value="claude-opus-4-6">claude-opus-4-6</option>
                  <option value="claude-haiku-4-5">claude-haiku-4-5</option>
                </select>
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
                  <input type="number" className="budget-input" id="settingsBudget" value="50" style={{ 'width': '80px', 'borderRadius': '0 var(--r-sm) var(--r-sm) 0' }} onChange={() => window.saveBudget()}/>
                </div>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Session spend</strong><span>This session only</span></div>
                <span className="settings-path" id="settingsSessionSpend" style={{ 'fontSize': '13px', 'fontWeight': '600', 'color': 'var(--ink)' }}>—</span>
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
                <div className="live-badge" id="engineStatusBadge" style={{ 'fontSize': '11px' }}><span className="dot"></span> Running</div>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Node.js</strong><span>v20.11.0 detected on PATH</span></div>
                <span className="settings-path">v20.11.0</span>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Restart engine</strong><span>If commands feel slow or unresponsive</span></div>
                <div style={{ 'display': 'flex', 'gap': '8px' }}>
                  <button className="btn btn-soft" id="testEngineBtn" style={{ 'fontSize': '12.5px', 'padding': '8px 14px' }} onClick={() => window.testEngine()}><i className="ti ti-activity"></i> Test</button>
                  <button className="btn btn-soft" id="restartEngineBtn" style={{ 'fontSize': '12.5px', 'padding': '8px 14px' }} onClick={() => window.restartEngine()}><i className="ti ti-refresh"></i> Restart</button>
                </div>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="settings-section" style={{ 'padding': '0' }}>
              <div className="secrets-head"><h3>Updates</h3></div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Channel</strong><span>Which release track to follow</span></div>
                <select className="select-input" id="updateChannel" style={{ 'width': 'auto', 'padding': '8px 28px 8px 12px' }}>
                  <option value="stable">Stable</option>
                  <option value="beta" selected>Beta</option>
                </select>
              </div>
              <div className="settings-row">
                <div className="settings-row-tx"><strong>Version</strong><span>SignalOS 1.4.2-beta</span></div>
<div style={{ 'display': 'flex', 'alignItems': 'center', 'gap': '10px' }}>
                  <button className="btn btn-soft" id="updateBtn" style={{ 'fontSize': '12.5px', 'padding': '8px 14px' }} onClick={() => window.checkForUpdates()}><i className="ti ti-cloud-download"></i> Check for updates</button>
                  <div className="update-result" id="updateResult"><i className="ti ti-circle-check" style={{ 'color': 'var(--success)' }}></i><span id="updateResultTx">Up to date</span></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
