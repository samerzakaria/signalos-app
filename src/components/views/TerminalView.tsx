import { engineRunning, workspacePath, terminalLines, termInputValue } from '../../state';

export function TerminalView() {
  const running = engineRunning.value;
  const ws = workspacePath.value;
  const lines = terminalLines.value;
  const inputVal = termInputValue.value;

  const pathName = ws ? ws.replace(/\\/g, '/').split('/').filter(Boolean).pop() || ws : 'signalos';

  const bannerCls = running === false ? 'sidecar-banner error' : 'sidecar-banner';
  const bannerIcon = running === false ? 'ti-alert-circle' : 'ti-circle-check';
  const bannerText = running === false
    ? 'SignalOS Core not running'
    : 'SignalOS Core running · Python sidecar ready';

  return (
    <>
<div className="view" data-view="terminal">
        <div className="term-wrap">
          <div className={bannerCls}>
            <i className={`ti ${bannerIcon}`}></i>
            {' '}{bannerText}
          </div>
          <div className="term-bar">
            <div className="term-title"><i className="ti ti-terminal-2"></i> Terminal</div>
            <div className="term-path">{pathName}</div>
            <button className="term-clear" onClick={() => window.termSubmit('clear')}><i className="ti ti-eraser"></i> Clear</button>
          </div>
          <div className="term-body" id="termBody">
            {lines.map((l, i) => {
              if (l.kind === 'echo') {
                return (
                  <div className="term-line" key={i}>
                    <span className="t-path">{l.pathName || pathName}</span>{' '}
                    <span className="t-sym">$</span>{' '}
                    <span className="t-cmd">{l.text}</span>
                  </div>
                );
              }
              const cls = l.kind === 'dim' ? 'term-line t-dim'
                        : l.kind === 'error' ? 'term-line t-err'
                        : l.kind === 'loading' ? 'term-line t-dim'
                        : 'term-line';
              return <div className={cls} key={i}>{l.text}</div>;
            })}
          </div>
          <div className="term-foot">
            <div className="term-chips">
              <span className="term-chip" onClick={() => window.termChip('help')}>help</span>
              <span className="term-chip" onClick={() => window.termChip('signalos status')}>signalos status</span>
              <span className="term-chip" onClick={() => window.termChip('signalos check')}>signalos check</span>
              <span className="term-chip" onClick={() => window.termChip('signalos gates')}>signalos gates</span>
              <span className="term-chip" onClick={() => window.termChip('npm run dev')}>npm run dev</span>
              <span className="term-chip" onClick={() => window.termChip('git status')}>git status</span>
              <span className="term-chip" onClick={() => window.termChip('clear')}>clear</span>
            </div>
            <div className="term-input-row">
              <span className="term-prompt"><span className="t-path">{pathName}</span> <span className="t-sym">$</span></span>
              <input
                id="termInput"
                className="term-input"
                spellcheck={false}
                autocomplete="off"
                placeholder="type a command…"
                value={inputVal}
                onInput={(e) => { termInputValue.value = (e.target as HTMLInputElement).value; }}
                onKeyDown={(e) => window.termKey(e)}
              />
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
