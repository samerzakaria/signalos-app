import { currentCost, ai, aiModel } from '../state';

// Provider brand labels — a FALLBACK only, used when no concrete model is
// selected. Never a stand-in for the real model: hardcoding "GPT-4o" here made
// the badge lie ("GPT-4o") no matter which model the user actually picked.
const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Claude",
  openai: "OpenAI",
  gemini: "Gemini",
  ollama: "Ollama",
  openrouter: "OpenRouter",
  deepseek: "DeepSeek",
  mistral: "Mistral",
  groq: "Groq",
  cerebras: "Cerebras",
  together: "Together AI",
  xai: "xAI",
  qwen: "Qwen",
};

// Show the ACTUAL selected model, lightly cleaned (drop a trailing -YYYYMMDD
// snapshot suffix that model ids often carry). Falls back to the provider brand
// only when no model is selected yet.
function modelDisplay(provider: string, model: string): string {
  const m = (model || '').trim().replace(/-\d{8}$/, '');
  if (m) return m;
  return PROVIDER_LABELS[provider] || provider || 'No model';
}

export function Titlebar() {
  const p = ai.value;
  const provName = modelDisplay(p, aiModel.value);

  return (
    <>
<div className="titlebar" data-tauri-drag-region>
  <div className="traffic">
    <span className="tl tl-red"><i className="ti ti-x"></i></span>
    <span className="tl tl-yellow"><i className="ti ti-minus"></i></span>
    <span className="tl tl-green"><i className="ti ti-arrows-diagonal"></i></span>
  </div>
  <div className="titlebar-center">
    <div className="cmd-pill" onClick={() => window.openSearch()}>
      <i className="ti ti-search"></i>
      <span>Search projects or ask Foundry...</span>
      <span className="kbd">⌘K</span>
    </div>
  </div>
  <div className="titlebar-right">
    <div className="cost-pill" title="Session spend so far" style={{ 'borderRadius': '7px', 'padding': '6px 10px' }}><i className="ti ti-coin"></i> <span id="costDisplay">${currentCost.value.toFixed(2)}</span></div>
    <div className="tb-divider"></div>
    <div className="live-badge" id="liveBadge" style={{ 'borderRadius': '7px' }}><span className="dot"></span> <span id="provDisplay">{provName} · live</span></div>
    <div className="tb-divider" style={{ 'margin': '0 6px 0 10px' }}></div>
    <button className="tb-btn tb-close" aria-label="Close Foundry" onClick={() => window.openExit()} title="Close Foundry"><i className="ti ti-x"></i></button>
  </div>
</div>
    </>
  );
}
