import {
  userName,
  userRole,
  ai,
  aiModel,
  obStep,
  provMoreOpen,
  keyLabel,
  keyVisible,
  apiKeyInput,
  budgetInputValue,
  projectsRoot,
  providerModels,
  providerModelsError,
  providerModelsLoading,
} from '../state';
import { loadProviderModels } from '../services/providerModels';
import { FoundryMark } from './FoundryMark';
import { stageClass } from './viewShell';

const OB_TAGS = [
  <>Every great thing starts with a spark.</>,
  <>The right brain,<br/>the right budget.</>,
  <>Your name on every gate.<br/>That's accountability.</>,
];

export function Onboarding() {
  const step = obStep.value;
  const stepCls = (n: number) => step === n ? 'ob-step active' : 'ob-step';
  const dotCls = (n: number) => n <= step ? 'pd active' : 'pd';
  const provider = ai.value;
  const provCardCls = (p: string) => provider === p ? 'prov-card sel' : 'prov-card';
  const moreDisplay = provMoreOpen.value ? 'grid' : 'none';
  const moreBtnCls = provMoreOpen.value ? 'prov-more-btn open' : 'prov-more-btn';
  const moreBtnIcon = provMoreOpen.value ? 'ti-chevron-up' : 'ti-chevron-down';
  const moreBtnLabel = provMoreOpen.value ? 'Show fewer' : '7 more providers';
  const keyVis = keyVisible.value;
  const keyTogIcon = keyVis ? 'ti-eye-off' : 'ti-eye';
  const models = providerModels.value;
  const selectedModel = aiModel.value;
  const modelSelectValue = models.some((model) => model.id === selectedModel) ? selectedModel : '';
  const providerNeedsKey = provider !== 'ollama';
  const modelHelp = providerModelsLoading.value
    ? 'Fetching models from the selected provider...'
    : providerModelsError.value
    ? providerModelsError.value
    : models.length > 0
    ? `${models.length} models available from ${provider}.`
    : providerNeedsKey
    ? 'Fetch models with the saved key, or paste a new key here first.'
    : 'Fetch local models from Ollama.';

  const chooseProvider = (providerId: string, label: string) => {
    window.selectProv(providerId, '', label);
    providerModels.value = [];
    providerModelsError.value = null;
    void loadProviderModels(providerId, apiKeyInput.value.trim() || null, { quietMissingKey: true });
  };

  const fetchModels = async () => {
    // Persist the typed key to keychain BEFORE fetching, so the backend's
    // `fetch_provider_models` resolves it the same way Settings does (via
    // keychain lookup). Passing the raw value worked unevenly; the Settings
    // "Replace key" flow stores-then-fetches and is the reference path.
    const key = apiKeyInput.value.trim();
    if (key && provider !== 'ollama') {
      try {
        const tauri = window.__TAURI__;
        const invoke = tauri?.core?.invoke || tauri?.invoke;
        if (invoke) await invoke('store_api_key', { provider, key });
      } catch {
        // Best-effort persist — keychain unavailable shouldn't block fetch.
      }
    }
    void loadProviderModels(provider, null);
  };

  const browseProjectsRoot = async () => {
    const tauri = window.__TAURI__;
    const dialog = tauri?.dialog;
    if (!dialog?.open) {
      const fallback = window.prompt('Projects root folder path');
      if (fallback) projectsRoot.value = fallback;
      return;
    }
    const result = await dialog.open({
      directory: true,
      multiple: false,
      title: 'Choose projects root',
    });
    const path = Array.isArray(result) ? result[0] : result;
    if (path && typeof path === 'string') {
      projectsRoot.value = path;
    }
  };

  return (
    <>
<div id="onboarding" className={stageClass('onboarding')}>
  <div className="ob-panel">
    <div className="ob-brand">
      <div className="ob-brand-mark">
        <FoundryMark size={24} />
      </div>
      <span className="ob-brand-name">Foundry <small>by SignalOS</small></span>
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
        <div className={dotCls(1)} id="pd-1"></div>
        <div className={dotCls(2)} id="pd-2"></div>
        <div className={dotCls(3)} id="pd-3"></div>
      </div>
      <p className="ob-tag" id="obTag">{OB_TAGS[step - 1]}</p>
    </div>
  </div>

  <div className="ob-form">

    <div className={stepCls(1)} data-step="1">
      <div className="ob-kicker">Step 1 — Welcome</div>
      <h1>Hi, I'm Foundry.<br/>Let's make<br/><em>something real.</em></h1>
      <p className="ob-sub">Tell me what you want to build. I plan it, gate it, and build it with you through Foundry's governance. Setup takes under a minute.</p>
      <div className="ob-body">
        <div className="feat-list">
          <div className="feat">
            <div className="feat-ic a"><i className="ti ti-message-circle-2"></i></div>
            <div className="feat-tx"><strong>Just talk</strong><p>No code, no menus. Plain words — I do the rest.</p></div>
          </div>
          <div className="feat">
            <div className="feat-ic b"><i className="ti ti-lock-check"></i></div>
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


    <div className={stepCls(2)} data-step="2">
      <div className="ob-kicker">Step 2 — Brain &amp; Budget</div>
      <h1>Which AI, and<br/>how <em>much?</em></h1>
      <p className="ob-sub">Pick your model, add its key, and set a monthly spend cap. You can change all of this in Settings any time.</p>
      <div className="ob-body">
        <div className="prov-label">Popular</div>
        <div className="prov-grid" id="provGrid">
          <div className={provCardCls('anthropic')} data-ai="anthropic" data-key-label="Anthropic API key" onClick={() => chooseProvider('anthropic', 'Anthropic API key')}><div className="prov-ic" style={{ 'background': 'var(--clay-soft)', 'color': 'var(--clay-deep)' }}><i className="ti ti-asterisk"></i></div><div className="prov-tx"><div className="prov-nm">Claude</div><div className="prov-ds">Anthropic</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className={provCardCls('openai')} data-ai="openai" data-key-label="OpenAI API key" onClick={() => chooseProvider('openai', 'OpenAI API key')}><div className="prov-ic" style={{ 'background': 'var(--success-soft)', 'color': 'var(--success-deep)' }}><i className="ti ti-circle-dot"></i></div><div className="prov-tx"><div className="prov-nm">OpenAI</div><div className="prov-ds">OpenAI</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className={provCardCls('gemini')} data-ai="gemini" data-key-label="Gemini API key" onClick={() => chooseProvider('gemini', 'Gemini API key')}><div className="prov-ic" style={{ 'background': 'var(--info-soft)', 'color': 'var(--info-deep)' }}><i className="ti ti-diamond"></i></div><div className="prov-tx"><div className="prov-nm">Gemini</div><div className="prov-ds">Google</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className={provCardCls('qwen')} data-ai="qwen" data-key-label="Qwen API key" onClick={() => chooseProvider('qwen', 'Qwen API key')}><div className="prov-ic" style={{ 'background': 'var(--amber-soft)', 'color': 'var(--amber-deep)' }}><i className="ti ti-sparkles"></i></div><div className="prov-tx"><div className="prov-nm">Qwen</div><div className="prov-ds">Alibaba</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className={provCardCls('ollama')} data-ai="ollama" data-key-label="Ollama does not need an API key" onClick={() => chooseProvider('ollama', 'Ollama does not need an API key')}><div className="prov-ic" style={{ 'background': 'var(--surface-deep)', 'color': 'var(--ink-2)' }}><i className="ti ti-cpu"></i></div><div className="prov-tx"><div className="prov-nm">Ollama</div><div className="prov-ds">Local</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
        </div>
        <button className={moreBtnCls} id="provMoreBtn" onClick={() => window.toggleMoreProvs()}><i className={`ti ${moreBtnIcon}`}></i> {moreBtnLabel}</button>
        <div className="prov-grid" id="provMore" style={{ display: moreDisplay }}>
          <div className={provCardCls('openrouter')} data-ai="openrouter" data-key-label="OpenRouter API key" onClick={() => chooseProvider('openrouter', 'OpenRouter API key')}><div className="prov-ic" style={{ 'background': 'var(--accent-soft)', 'color': 'var(--accent)' }}><i className="ti ti-route"></i></div><div className="prov-tx"><div className="prov-nm">OpenRouter</div><div className="prov-ds">Multi-model gateway</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className={provCardCls('deepseek')} data-ai="deepseek" data-key-label="DeepSeek API key" onClick={() => chooseProvider('deepseek', 'DeepSeek API key')}><div className="prov-ic" style={{ 'background': 'var(--info-soft)', 'color': 'var(--info-deep)' }}><i className="ti ti-search"></i></div><div className="prov-tx"><div className="prov-nm">DeepSeek</div><div className="prov-ds">DeepSeek</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className={provCardCls('mistral')} data-ai="mistral" data-key-label="Mistral API key" onClick={() => chooseProvider('mistral', 'Mistral API key')}><div className="prov-ic" style={{ 'background': 'var(--clay-soft)', 'color': 'var(--clay-deep)' }}><i className="ti ti-wind"></i></div><div className="prov-tx"><div className="prov-nm">Mistral</div><div className="prov-ds">Mistral</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className={provCardCls('groq')} data-ai="groq" data-key-label="Groq API key" onClick={() => chooseProvider('groq', 'Groq API key')}><div className="prov-ic" style={{ 'background': 'var(--success-soft)', 'color': 'var(--success-deep)' }}><i className="ti ti-bolt"></i></div><div className="prov-tx"><div className="prov-nm">Groq</div><div className="prov-ds">Groq</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className={provCardCls('cerebras')} data-ai="cerebras" data-key-label="Cerebras API key" onClick={() => chooseProvider('cerebras', 'Cerebras API key')}><div className="prov-ic" style={{ 'background': 'var(--amber-soft)', 'color': 'var(--amber-deep)' }}><i className="ti ti-brain"></i></div><div className="prov-tx"><div className="prov-nm">Cerebras</div><div className="prov-ds">Cerebras</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className={provCardCls('together')} data-ai="together" data-key-label="Together AI key" onClick={() => chooseProvider('together', 'Together AI key')}><div className="prov-ic" style={{ 'background': 'var(--accent-soft)', 'color': 'var(--accent-deep)' }}><i className="ti ti-topology-star"></i></div><div className="prov-tx"><div className="prov-nm">Together AI</div><div className="prov-ds">Together</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
          <div className={provCardCls('xai')} data-ai="xai" data-key-label="xAI API key" onClick={() => chooseProvider('xai', 'xAI API key')}><div className="prov-ic" style={{ 'background': 'var(--surface-deep)', 'color': 'var(--ink)' }}><i className="ti ti-x"></i></div><div className="prov-tx"><div className="prov-nm">xAI</div><div className="prov-ds">xAI</div></div><div className="ai-rd"><i className="ti ti-check"></i></div></div>
        </div>
        <div className="ob-divider"></div>
        <label className="field-label" id="keyLabel">{keyLabel.value}</label>
        <div className="key-wrap">
          <input
            type={keyVis ? 'text' : 'password'}
            className="key-input"
            id="apiKey"
            placeholder="sk-ant-api03-…"
            value={apiKeyInput.value}
            onInput={(e) => { apiKeyInput.value = (e.target as HTMLInputElement).value; }}
          />
          <button className="key-tog" onClick={() => window.toggleKey()} id="keyTog" aria-label="Show or hide key"><i className={`ti ${keyTogIcon}`}></i></button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px', marginBottom: '7px' }}>
          <label className="field-label" htmlFor="ob-model" style={{ marginBottom: 0 }}>Model</label>
          <button className="btn btn-soft btn-compact" type="button" onClick={fetchModels} disabled={providerModelsLoading.value}>
            <i className={`ti ${providerModelsLoading.value ? 'ti-loader-2' : 'ti-refresh'}`} style={providerModelsLoading.value ? { animation: 'spin 1s linear infinite' } : undefined}></i>
            Fetch models
          </button>
        </div>
        <select
          id="ob-model"
          className="select-input"
          value={modelSelectValue}
          disabled={models.length === 0}
          onInput={(e) => { aiModel.value = (e.target as HTMLSelectElement).value; }}
          style={{ marginBottom: '6px' }}
        >
          {models.length === 0 ? (
            <option value="">{providerModelsLoading.value ? 'Loading models...' : 'No models loaded'}</option>
          ) : (
            models.map((model) => <option key={model.id} value={model.id}>{model.name || model.id}</option>)
          )}
        </select>
        <div className="hint" style={{ marginBottom: '12px' }}><i className="ti ti-info-circle"></i> {modelHelp}</div>
        <label className="field-label">Monthly spend cap <span style={{ 'fontWeight': '400', 'color': 'var(--ink-3)' }}>(optional)</span></label>
        <div className="budget-wrap">
          <span className="budget-prefix">$</span>
          <input
            type="number"
            className="budget-input"
            id="budgetInput"
            placeholder="50"
            min="0"
            step="5"
            value={budgetInputValue.value}
            onInput={(e) => { budgetInputValue.value = (e.target as HTMLInputElement).value; }}
          />
        </div>
        <div className="hint"><i className="ti ti-info-circle"></i> No key yet? <a href="#">Get one in under a minute</a></div>
      </div>
      <div className="ob-actions">
        <button className="btn btn-ghost" onClick={() => window.prevStep()}><i className="ti ti-arrow-left"></i> Back</button>
        <button className="btn btn-primary" onClick={() => window.nextStep()} style={{ 'marginLeft': 'auto' }}>Continue <i className="ti ti-arrow-right"></i></button>
      </div>
    </div>


    <div className={stepCls(3)} data-step="3">
      <div className="ob-kicker">Step 3 — Identity</div>
      <h1>Last thing —<br/><em>who are you?</em></h1>
      <p className="ob-sub">Your name and role are recorded each time you sign a gate. This is what makes the audit trail honest.</p>
      <div className="ob-body">
        <div className="field-row">
          <div>
            <label className="field-label">Your name</label>
            <input
              type="text"
              className="plain-input"
              id="identName"
              placeholder="Your name"
              value={userName.value}
              onInput={(e) => { userName.value = (e.target as HTMLInputElement).value; }}
            />
          </div>
          <div>
            <label className="field-label">Your role</label>
            <select
              className="select-input"
              id="identRole"
              value={userRole.value || 'PO'}
              onInput={(e) => { userRole.value = (e.target as HTMLSelectElement).value; }}
            >
              <option value="PO">PO — Product Owner</option>
              <option value="PE">PE — Principal Engineer</option>
              <option value="QA">QA — Quality</option>
              <option value="DevOps">DevOps</option>
            </select>
          </div>
        </div>
        <label className="field-label" style={{ marginTop: '12px' }}>Projects root</label>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'stretch' }}>
          <input
            type="text"
            className="plain-input"
            id="identFolder"
            placeholder="C:\\Users\\you\\Foundry Projects"
            value={projectsRoot.value}
            onInput={(e) => { projectsRoot.value = (e.target as HTMLInputElement).value; }}
            style={{ flex: 1, fontFamily: 'var(--f-mono)', fontSize: '12px' }}
          />
          <button className="btn btn-soft" onClick={browseProjectsRoot} style={{ flexShrink: 0 }}><i className="ti ti-folder-open"></i> Browse</button>
        </div>
        <div className="hint" style={{ marginTop: '6px' }}>
          <i className="ti ti-info-circle"></i> Foundry will create one folder per product inside this root.
        </div>
        <div className="callout success" style={{ marginTop: '14px' }}>
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
