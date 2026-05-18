import { currentCost, ai } from '../state';

export function Titlebar() {
  const p = ai.value;
  const names: Record<string, string> = {
    anthropic: "Claude",
    openai: "GPT-4o",
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
  const provName = names[p] || p;

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
      <span>Search projects or ask SignalOS…</span>
      <span className="kbd">⌘K</span>
    </div>
  </div>
  <div className="titlebar-right">
    <div className="cost-pill" title="Session spend so far" style={{ 'borderRadius': '7px', 'padding': '6px 10px' }}><i className="ti ti-coin"></i> <span id="costDisplay">${currentCost.value.toFixed(2)}</span></div>
    <div className="tb-divider"></div>
    <div className="live-badge" id="liveBadge" style={{ 'borderRadius': '7px' }}><span className="dot"></span> <span id="provDisplay">{provName} · live</span></div>
    <div className="tb-divider" style={{ 'margin': '0 6px 0 10px' }}></div>
    <button className="tb-btn tb-close" aria-label="Close SignalOS" onClick={() => window.openExit()} title="Close SignalOS"><i className="ti ti-x"></i></button>
  </div>
</div>
    </>
  );
}
