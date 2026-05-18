export function TerminalView() {
  return (
    <>
<div className="view" data-view="terminal">
        <div className="term-wrap">
          <div className="sidecar-banner">
            <i className="ti ti-circle-check"></i>
            SignalOS Core running · Python sidecar ready · 40 commands available
          </div>
          <div className="term-bar">
            <div className="term-title"><i className="ti ti-terminal-2"></i> Terminal</div>
            <div className="term-path">~/projects/my-pizza-game</div>
            <button className="term-clear" onClick={() => window.termSubmit('clear')}><i className="ti ti-eraser"></i> Clear</button>
          </div>
          <div className="term-body" id="termBody">
            <div className="term-line t-dim">SignalOS terminal · my-pizza-game</div>
            <div className="term-line t-dim">Type a command, or tap one below. New here? Try 'help'.</div>
          </div>
          <div className="term-foot">
            <div className="term-chips">
              <span className="term-chip" onClick={(e) => window.termChip(e.currentTarget)}>help</span>
              <span className="term-chip" onClick={(e) => window.termChip(e.currentTarget)}>signalos status</span>
              <span className="term-chip" onClick={(e) => window.termChip(e.currentTarget)}>signalos check</span>
              <span className="term-chip" onClick={(e) => window.termChip(e.currentTarget)}>signalos gates</span>
              <span className="term-chip" onClick={(e) => window.termChip(e.currentTarget)}>npm run dev</span>
              <span className="term-chip" onClick={(e) => window.termChip(e.currentTarget)}>git status</span>
              <span className="term-chip" onClick={(e) => window.termChip(e.currentTarget)}>clear</span>
            </div>
            <div className="term-input-row">
              <span className="term-prompt"><span className="t-path">my-pizza-game</span> <span className="t-sym">$</span></span>
              <input id="termInput" className="term-input" spellcheck={false} autocomplete="off" placeholder="type a command…" onKeyDown={(e) => window.termKey(e)}/>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
