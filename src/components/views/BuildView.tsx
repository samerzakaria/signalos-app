import { h } from 'preact';

export function BuildView() {
  return (
    <>
<div className="view" data-view="build">
        
        <div className="phase-strip">
          <div className="phase-node">
            <div className="phase-dot done"></div>
            <span className="phase-label done">Plan</span>
          </div>
          <div className="phase-conn done"></div>
          <div className="phase-node">
            <div className="phase-dot active"></div>
            <span className="phase-label active">Build</span>
          </div>
          <div className="phase-conn"></div>
          <div className="phase-node">
            <div className="phase-dot"></div>
            <span className="phase-label">Check</span>
          </div>
          <div className="phase-conn"></div>
          <div className="phase-node">
            <div className="phase-dot"></div>
            <span className="phase-label">Ship</span>
          </div>
          <div className="phase-stack">
            <i className="ti ti-brand-javascript"></i>
            <span className="stack-pill">React · Vite</span>
          </div>
        </div>

        <div className="chat-scroll" id="chatScroll">
          <div className="chat-inner" id="chatInner">
            <div className="msg spark">
              <div className="msg-av"><i className="ti ti-sparkles" style={{ 'fontSize': '17px' }}></i></div>
              <div>
                <div className="bubble">Hi Samer! What do you want to build today? You can tell me anything — a game, a story, an app for someone.</div>
                <div className="msg-meta">SignalOS · 10:42</div>
              </div>
            </div>
            <div className="msg user">
              <div className="msg-av">S</div>
              <div>
                <div className="bubble">i want a pizza game where you make pizzas and bring them to people!!</div>
                <div className="msg-meta">10:42</div>
              </div>
            </div>
            <div className="msg spark">
              <div className="msg-av"><i className="ti ti-sparkles" style={{ 'fontSize': '17px' }}></i></div>
              <div>
                <div className="bubble">Great idea — I've broken this into seven gates. We're on gate four now: making the pizzas. Shall I add green peppers to the toppings list?</div>
                <div className="msg-meta">SignalOS · 10:43</div>
              </div>
            </div>
            <div className="msg user">
              <div className="msg-av">S</div>
              <div>
                <div className="bubble">yes! and pineapple please!!</div>
                <div className="msg-meta">10:43</div>
              </div>
            </div>
            <div className="msg spark">
              <div className="msg-av"><i className="ti ti-sparkles" style={{ 'fontSize': '17px' }}></i></div>
              <div>
                <div className="bubble">Done — pineapple and green peppers added. Open the Preview tab to see them on the menu. Should the pineapple pizza be worth more points, or the same?</div>
                <div className="msg-meta">SignalOS · just now</div>
              </div>
            </div>
          </div>
        </div>

        <div className="chat-foot">
          <div className="chat-foot-inner">
            
            <div className="cmd-palette" id="cmdPalette">
              <div className="cmd-palette-head">Commands</div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-status')}>
                <div className="cmd-item-ic"><i className="ti ti-activity"></i></div>
                <span className="cmd-item-name">/signal-status</span>
                <span className="cmd-item-desc">Show gate state</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-build')}>
                <div className="cmd-item-ic"><i className="ti ti-hammer"></i></div>
                <span className="cmd-item-name">/signal-build</span>
                <span className="cmd-item-desc">Run build pipeline</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-review')}>
                <div className="cmd-item-ic"><i className="ti ti-eye"></i></div>
                <span className="cmd-item-name">/signal-review</span>
                <span className="cmd-item-desc">Code review</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-design')}>
                <div className="cmd-item-ic"><i className="ti ti-palette"></i></div>
                <span className="cmd-item-name">/signal-design</span>
                <span className="cmd-item-desc">Design notes</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-debrief')}>
                <div className="cmd-item-ic"><i className="ti ti-report"></i></div>
                <span className="cmd-item-name">/signal-debrief</span>
                <span className="cmd-item-desc">Wave retrospective</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-ship')}>
                <div className="cmd-item-ic"><i className="ti ti-rocket"></i></div>
                <span className="cmd-item-name">/signal-ship</span>
                <span className="cmd-item-desc">Ship checklist</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-freeze')}>
                <div className="cmd-item-ic"><i className="ti ti-snowflake"></i></div>
                <span className="cmd-item-name">/signal-freeze</span>
                <span className="cmd-item-desc">Freeze wave</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-brain')}>
                <div className="cmd-item-ic"><i className="ti ti-brain"></i></div>
                <span className="cmd-item-name">/signal-brain</span>
                <span className="cmd-item-desc">Show notes</span>
              </div>
            </div>
            <div className="chips">
              <div className="chip" onClick={() => window.sendChip(this)}>Same points please</div>
              <div className="chip" onClick={() => window.sendChip(this)}>Make it worth 10 points</div>
              <div className="chip" onClick={() => window.sendChip(this)}>Show me the pizza first</div>
              <div className="chip" onClick={() => window.sendChip(this)}>Can we add olives too?</div>
            </div>
            <div className="composer">
              <input id="chatInput" placeholder="Tell SignalOS anything, or type / for commands…" onKeyDown={(e) => window.composerKey(event)} onInput={(e) => window.composerInput(event)}/>
              <button className="cmp-btn" onClick={() => window.attachFile()} aria-label="Attach file"><i className="ti ti-paperclip"></i></button>
              <button className="cmp-btn" onClick={() => window.voiceInput()} aria-label="Voice input"><i className="ti ti-microphone"></i></button>
              <button className="cmp-btn cmp-send" onClick={() => window.sendMsg()} aria-label="Send"><i className="ti ti-arrow-up"></i></button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
