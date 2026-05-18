import { h } from 'preact';

export function Onboarding() {
  return (
    <>
<div id="onboarding" className="stage active">
  <div className="ob-panel">
    <div className="ob-brand">
      <div className="ob-brand-mark">
        <svg width="20" height="20" viewBox="0 0 32 32" fill="none"><circle cx="16" cy="16" r="3.7" fill="currentColor"/><path d="M20.24 9.22 A8 8 0 0 1 20.24 22.78" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"/><path d="M11.76 9.22 A8 8 0 0 0 11.76 22.78" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"/><path d="M22.89 4.98 A13 13 0 0 1 22.89 27.02" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"/><path d="M9.11 4.98 A13 13 0 0 0 9.11 27.02" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"/></svg>
      </div>
      <span className="ob-brand-name">SignalOS</span>
    </div>
    <div className="ob-art">
      <svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
        <circle cx="100" cy="100" r="86" fill="none" stroke="rgba(243,241,236,0.09)" strokeWidth="1" strokeDasharray="2 7"/>
        <circle cx="100" cy="14" r="3" fill="#F3F1EC" opacity="0.55"/>
        <circle cx="173" cy="129" r="4" fill="#BC6A47"/>
        <circle cx="39" cy="151" r="3.2" fill="#BC6A47" opacity="0.75"/>
        <g className="sig-ring" style={{ 'animationDelay': '1.1s' }}>
          <path d="M133.91 45.73 A64 64 0 0 1 133.91 154.27" stroke="#F3F1EC" strokeWidth="3.4" strokeLinecap="round" fill="none"/>
          <path d="M66.09 45.73 A64 64 0 0 0 66.09 154.27" stroke="#F3F1EC" strokeWidth="3.4" strokeLinecap="round" fill="none"/>
        </g>
        <g className="sig-ring" style={{ 'animationDelay': '0.55s' }}>
          <path d="M123.32 62.69 A44 44 0 0 1 123.32 137.31" stroke="#F3F1EC" strokeWidth="3.4" strokeLinecap="round" fill="none"/>
          <path d="M76.68 62.69 A44 44 0 0 0 76.68 137.31" stroke="#F3F1EC" strokeWidth="3.4" strokeLinecap="round" fill="none"/>
        </g>
        <g className="sig-ring" style={{ 'animationDelay': '0s' }}>
          <path d="M113.78 77.95 A26 26 0 0 1 113.78 122.05" stroke="#F3F1EC" strokeWidth="3.4" strokeLinecap="round" fill="none"/>
          <path d="M86.22 77.95 A26 26 0 0 0 86.22 122.05" stroke="#F3F1EC" strokeWidth="3.4" strokeLinecap="round" fill="none"/>
        </g>
        <circle cx="100" cy="100" r="11" fill="#F3F1EC"/>
      </svg>
    </div>
    <div className="ob-foot">
      <div className="ob-dots">
        <div className="pd active" id="pd-1"></div>
        <div className="pd" id="pd-2"></div>
        <div className="pd" id="pd-3"></div>
      </div>
      <p className="ob-tag" id="obTag">Every great thing starts with a spark.</p>
    </div>
  </div>

  <div className="ob-form">
    
    <div className="ob-step active" data-step="1">
      <div className="ob-kicker">Step 1 — Welcome</div>
      <h1>Hi, I'm SignalOS.<br/>Let's make<br/><em>something real.</em></h1>
      <p className="ob-sub">Tell me what you want to build. I plan it, gate it, and build it with you — step by step. Setup takes under a minute.</p>
      <div className="ob-body">
        <div className="feat-list">
          <div className="feat">
            <div className="feat-ic a"><i className="ti ti-message-circle-2"></i></div>
            <div className="feat-tx"><strong>Just talk</strong><p>No code, no menus. Plain words — I do the rest.</p></div>
          </div>
          <div className="feat">
            <div className="feat-ic b"><i className="ti ti-gate"></i></div>
            <div className="feat-tx"><strong>Gated progress</strong><p>Every build is broken into signed gates. Nothing ships unreviewed.</p></div>
          </div>
          <div className="feat">
            <div className="feat-ic c"><i className="ti ti-shield-lock"></i></div>
            <div className="feat-tx"><strong>Stay private</strong><p>Your keys live in your OS keychain — never in a file, never in the cloud.</p></div>
          </div>
        </div>
      </div>
      <div className="ob-actions">
        <button className="btn btn-primary" onClick={() => window.nextStep()}>Begin <i className="ti ti-arrow-right"></i></button>
      </div>
    </div>

    
    <div className="ob-step" data-step="2">
      <div className="ob-kicker">Step 2 — Brain &amp; Budget</div>
      <h1>Which AI, and<br/>how <em>much?</em></h1>
      <p className="ob-sub">Pick your model, add its key, and set a monthly spend cap. You can change all of this in Settings any time.</p>
      <div className="ob-body">
        <div className="prov-label">Popular</div>
        <div className="prov-grid" id="provGrid">
          <div className="prov-card sel" data-ai="anthropic" data-model="claude-sonnet-4-6" data-key-label="Anthropic API key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--clay-soft)', 'color': 'var(--clay-deep)' }}><i className="ti ti-asterisk"></i></div><div className="prov-tx"><div className="prov-nm">Claude</div><div className="prov-ds">Anthropic · sonnet-4-6</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className="prov-card" data-ai="openai" data-model="gpt-4o-mini" data-key-label="OpenAI API key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--success-soft)', 'color': 'var(--success-deep)' }}><i className="ti ti-circle-dot"></i></div><div className="prov-tx"><div className="prov-nm">OpenAI</div><div className="prov-ds">OpenAI · gpt-4o-mini</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className="prov-card" data-ai="gemini" data-model="gemini-2.5-flash" data-key-label="Gemini API key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--info-soft)', 'color': 'var(--info-deep)' }}><i className="ti ti-diamond"></i></div><div className="prov-tx"><div className="prov-nm">Gemini</div><div className="prov-ds">Google · 2.5 flash</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className="prov-card" data-ai="qwen" data-model="qwen-plus" data-key-label="Qwen API key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--amber-soft)', 'color': 'var(--amber-deep)' }}><i className="ti ti-sparkles"></i></div><div className="prov-tx"><div className="prov-nm">Qwen</div><div className="prov-ds">Alibaba · qwen-plus</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className="prov-card" data-ai="ollama" data-model="" data-key-label="Model name (e.g. llama3)" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--surface-deep)', 'color': 'var(--ink-2)' }}><i className="ti ti-cpu"></i></div><div className="prov-tx"><div className="prov-nm">Ollama</div><div className="prov-ds">Local · no key needed</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
        </div>
        <button className="prov-more-btn" id="provMoreBtn" onClick={() => window.toggleMoreProvs()}><i className="ti ti-chevron-down"></i> 7 more providers</button>
        <div className="prov-grid" id="provMore" style={{ 'display': 'none' }}>
          <div className="prov-card" data-ai="openrouter" data-model="qwen/qwen-plus" data-key-label="OpenRouter API key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--accent-soft)', 'color': 'var(--accent)' }}><i className="ti ti-route"></i></div><div className="prov-tx"><div className="prov-nm">OpenRouter</div><div className="prov-ds">Multi-model gateway</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className="prov-card" data-ai="deepseek" data-model="deepseek-chat" data-key-label="DeepSeek API key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--info-soft)', 'color': 'var(--info-deep)' }}><i className="ti ti-search"></i></div><div className="prov-tx"><div className="prov-nm">DeepSeek</div><div className="prov-ds">deepseek-chat</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className="prov-card" data-ai="mistral" data-model="mistral-large-latest" data-key-label="Mistral API key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--clay-soft)', 'color': 'var(--clay-deep)' }}><i className="ti ti-wind"></i></div><div className="prov-tx"><div className="prov-nm">Mistral</div><div className="prov-ds">mistral-large</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className="prov-card" data-ai="groq" data-model="llama-3.3-70b-versatile" data-key-label="Groq API key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--success-soft)', 'color': 'var(--success-deep)' }}><i className="ti ti-bolt"></i></div><div className="prov-tx"><div className="prov-nm">Groq</div><div className="prov-ds">llama-3.3-70b · fast</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className="prov-card" data-ai="cerebras" data-model="llama-4-scout-17b-16e-instruct" data-key-label="Cerebras API key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--amber-soft)', 'color': 'var(--amber-deep)' }}><i className="ti ti-brain"></i></div><div className="prov-tx"><div className="prov-nm">Cerebras</div><div className="prov-ds">llama-4 scout</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className="prov-card" data-ai="together" data-model="meta-llama/Llama-3.3-70B-Instruct-Turbo" data-key-label="Together AI key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--accent-soft)', 'color': 'var(--accent-deep)' }}><i className="ti ti-topology-star"></i></div><div className="prov-tx"><div className="prov-nm">Together AI</div><div className="prov-ds">Llama-3.3-70B turbo</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className="prov-card" data-ai="xai" data-model="grok-4" data-key-label="xAI API key" onClick={() => window.selectProv(this)}><div className="prov-ic" style={{ 'background': 'var(--surface-deep)', 'color': 'var(--ink)' }}><i className="ti ti-x"></i></div><div className="prov-tx"><div className="prov-nm">xAI</div><div className="prov-ds">Grok 4</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
        </div>
        <div className="ob-divider"></div>
        <label className="field-label" id="keyLabel">Claude API key</label>
        <div className="key-wrap">
          <input type="password" className="key-input" id="apiKey" placeholder="sk-ant-api03-…"/>
          <button className="key-tog" onClick={() => window.toggleKey()} id="keyTog" aria-label="Show or hide key"><i className="ti ti-eye"></i></button>
        </div>
        <label className="field-label">Monthly spend cap <span style={{ 'fontWeight': '400', 'color': 'var(--ink-3)' }}>(optional)</span></label>
        <div className="budget-wrap">
          <span className="budget-prefix">$</span>
          <input type="number" className="budget-input" id="budgetInput" placeholder="50" min="0" step="5"/>
        </div>
        <div className="hint"><i className="ti ti-info-circle"></i> No key yet? <a href="#">Get one in under a minute</a></div>
      </div>
      <div className="ob-actions">
        <button className="btn btn-ghost" onClick={() => window.prevStep()}><i className="ti ti-arrow-left"></i> Back</button>
        <button className="btn btn-primary" onClick={() => window.nextStep()} style={{ 'marginLeft': 'auto' }}>Continue <i className="ti ti-arrow-right"></i></button>
      </div>
    </div>

    
    <div className="ob-step" data-step="3">
      <div className="ob-kicker">Step 3 — Identity</div>
      <h1>Last thing —<br/><em>who are you?</em></h1>
      <p className="ob-sub">Your name and role are recorded each time you sign a gate. This is what makes the audit trail honest.</p>
      <div className="ob-body">
        <div className="field-row">
          <div>
            <label className="field-label">Your name</label>
            <input type="text" className="plain-input" id="identName" placeholder="Samer" value="Samer"/>
          </div>
          <div>
            <label className="field-label">Your role</label>
            <select className="select-input" id="identRole">
              <option value="PO">PO — Product Owner</option>
              <option value="PE">PE — Principal Engineer</option>
              <option value="QA">QA — Quality</option>
              <option value="DevOps">DevOps</option>
            </select>
          </div>
        </div>
        <div className="callout success">
          <i className="ti ti-shield-check"></i>
          <p><strong>Your identity stays on this device.</strong> It's written to your local audit trail when you sign gates — never sent to the AI, never stored in the cloud.</p>
        </div>
        <div className="callout">
          <i className="ti ti-key"></i>
          <p><strong>API key sealed.</strong> Stored in your OS keychain — the same vault your Mac uses for its own passwords.</p>
        </div>
      </div>
      <div className="ob-actions">
        <button className="btn btn-ghost" onClick={() => window.prevStep()}><i className="ti ti-arrow-left"></i> Back</button>
        <button className="btn btn-primary" onClick={() => window.finishOnboarding()} style={{ 'marginLeft': 'auto' }}>Seal &amp; start <i className="ti ti-sparkles"></i></button>
      </div>
    </div>
  </div>
</div>
    </>
  );
}
