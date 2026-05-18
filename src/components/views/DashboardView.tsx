import { h } from 'preact';

export function DashboardView() {
  return (
    <>
<div className="view active" data-view="dashboard">
        <div className="page-head">
          <h1>Where we are</h1>
          <p>SignalOS broke this build into seven gates. A gate opens only when its work is done, its checks pass, and you sign it.</p>
        </div>
        <div className="stack">
          <div className="hero">
            <div className="ring-box">
              <svg width="104" height="104" viewBox="0 0 104 104">
                <circle cx="52" cy="52" r="44" stroke="var(--accent-soft)" strokeWidth="8" fill="none"/>
                <circle id="ring" cx="52" cy="52" r="44" stroke="var(--accent)" strokeWidth="8" fill="none" strokeLinecap="round" strokeDasharray="276.46" strokeDashoffset="138"/>
              </svg>
              <div className="ring-tx">
                <span className="ring-pct" id="ringPct">50%</span>
                <span className="ring-lbl">done</span>
              </div>
            </div>
            <div className="hero-tx">
              <div className="eyebrow">Right now</div>
              <h2>Gate 4 of 7 — Making the pizzas</h2>
              <p id="heroSub">2 of 5 activities done · 3 of 5 checks passed.</p>
            </div>
            <button className="btn btn-primary" onClick={() => window.switchTab('build')}>Keep building <i className="ti ti-arrow-right"></i></button>
          </div>

          <div className="card card-pad">
            <div className="sec-cap">The seven gates</div>
            <div className="stepper">
              <div className="scell done"><div className="scirc"><i className="ti ti-check"></i></div><div className="slbl">Pick the idea</div><div className="sstatus">Signed</div><div className="conn"></div></div>
              <div className="scell done"><div className="scirc"><i className="ti ti-check"></i></div><div className="slbl">Sketch it out</div><div className="sstatus">Signed</div><div className="conn"></div></div>
              <div className="scell done"><div className="scirc"><i className="ti ti-check"></i></div><div className="slbl">Make the menu</div><div className="sstatus">Signed</div><div className="conn"></div></div>
              <div className="scell active"><div className="scirc">4</div><div className="slbl">Make the pizzas</div><div className="sstatus">Current</div><div className="conn"></div></div>
              <div className="scell"><div className="scirc"><i className="ti ti-lock"></i></div><div className="slbl">Drag &amp; drop</div><div className="sstatus">Locked</div><div className="conn"></div></div>
              <div className="scell"><div className="scirc"><i className="ti ti-lock"></i></div><div className="slbl">Count score</div><div className="sstatus">Locked</div><div className="conn"></div></div>
              <div className="scell"><div className="scirc"><i className="ti ti-lock"></i></div><div className="slbl">Share it</div><div className="sstatus">Locked</div></div>
            </div>
          </div>

          <div className="card" id="gateCard">
            <div className="gate-head">
              <div className="gate-ic"><i className="ti ti-flame"></i></div>
              <div className="gate-tx">
                <h3>Gate 4 — Making the pizzas</h3>
                <p>Tap activities to move them along, tap a check to run it. Sign the gate when everything is green.</p>
              </div>
              <div className="gate-badge" id="gateBadge"><span className="dot"></span> Current gate</div>
            </div>
            <div className="subsec-head">
              <div className="lbl"><i className="ti ti-checklist"></i> Activities — the work</div>
            </div>
            <div className="act-sum">
              <span className="sum-chip sum-done"><i className="ti ti-circle-check"></i><b id="cDone">2</b> done</span>
              <span className="sum-chip sum-ongoing"><i className="ti ti-loader-2"></i><b id="cOngoing">2</b> ongoing</span>
              <span className="sum-chip sum-pending"><i className="ti ti-circle"></i><b id="cPending">1</b> pending</span>
            </div>
            <div className="acts" id="acts">
              <div className="act done" onClick={() => window.cycleActivity(this)}><div className="act-ic"><i className="ti ti-check"></i></div><div className="act-name">Draw the pizza base</div><div className="act-pill">Done</div></div>
              <div className="act done" onClick={() => window.cycleActivity(this)}><div className="act-ic"><i className="ti ti-check"></i></div><div className="act-name">Add six toppings — cheese, pepperoni, mushroom…</div><div className="act-pill">Done</div></div>
              <div className="act ongoing" onClick={() => window.cycleActivity(this)}><div className="act-ic"><i className="ti ti-loader-2"></i></div><div className="act-name">Make three pizza recipes</div><div className="act-pill"><span className="pdot"></span>Ongoing</div></div>
              <div className="act ongoing" onClick={() => window.cycleActivity(this)}><div className="act-ic"><i className="ti ti-loader-2"></i></div><div className="act-name">Make pizzas appear on a tray</div><div className="act-pill"><span className="pdot"></span>Ongoing</div></div>
              <div className="act pending" onClick={() => window.cycleActivity(this)}><div className="act-ic"></div><div className="act-name">Test if the tray fills up correctly</div><div className="act-pill">Pending</div></div>
            </div>
            <div className="subsec-div"></div>
            <div className="subsec-head">
              <div className="lbl"><i className="ti ti-shield-check"></i> Gate criteria — the checks</div>
              <div className="meta"><b id="cCrit">3</b> of 5 passed</div>
            </div>
            <div className="crits" id="crits">
              <div className="crit passed" onClick={() => window.runCheck(this)}><div className="crit-ic"><i className="ti ti-shield-check"></i></div><div className="crit-name">The game runs with no errors</div><div className="crit-pill">Passed</div></div>
              <div className="crit passed" onClick={() => window.runCheck(this)}><div className="crit-ic"><i className="ti ti-shield-check"></i></div><div className="crit-name">Every pizza shows a picture</div><div className="crit-pill">Passed</div></div>
              <div className="crit passed" onClick={() => window.runCheck(this)}><div className="crit-ic"><i className="ti ti-shield-check"></i></div><div className="crit-name">Safe for kids — content was checked</div><div className="crit-pill">Passed</div></div>
              <div className="crit checking" onClick={() => window.runCheck(this)}><div className="crit-ic"><i className="ti ti-loader-2"></i></div><div className="crit-name">Works on phone, tablet and computer</div><div className="crit-pill"><span className="pdot"></span>Checking</div></div>
              <div className="crit waiting" onClick={() => window.runCheck(this)}><div className="crit-ic"><i className="ti ti-shield"></i></div><div className="crit-name">Your work is backed up safely</div><div className="crit-pill">Waiting</div></div>
            </div>
            <div className="verdict held" id="verdict">
              <div className="verdict-ic"><i className="ti ti-lock"></i></div>
              <div className="verdict-tx" id="verdictTx">Gate held — 3 activities to finish and 2 checks to pass.</div>
              <button className="btn btn-primary" id="openBtn" onClick={() => window.showSignForm()} disabled>Sign gate <i className="ti ti-pencil"></i></button>
              <div className="sign-form" id="signForm" style={{ 'display': 'none' }}>
                <span className="sign-label"><i className="ti ti-user-check"></i> Sign as:</span>
                <input className="sign-input" id="signName" placeholder="Your name" value="Samer"/>
                <select className="sign-select" id="signRole">
                  <option value="PO">PO — Product Owner</option>
                  <option value="PE">PE — Principal Engineer</option>
                  <option value="QA">QA — Quality</option>
                  <option value="DevOps">DevOps</option>
                </select>
                <button className="btn btn-primary" style={{ 'padding': '8px 14px', 'fontSize': '12.5px' }} onClick={() => window.openGate()}>Confirm <i className="ti ti-check"></i></button>
              </div>
            </div>
          </div>

          <div className="next-grid">
            <div className="next-c">
              <div className="eyebrow">Next gate · 5</div>
              <h4><i className="ti ti-hand-grab"></i> Drag and drop the pizzas</h4>
              <p>Make pizzas you can pick up with your finger and place onto plates.</p>
            </div>
            <div className="next-c">
              <div className="eyebrow">Then · gate 6</div>
              <h4><i className="ti ti-trophy"></i> Count score points</h4>
              <p>Show how many pizzas were delivered to the right person.</p>
            </div>
            <div className="next-c">
              <div className="eyebrow">Test debt</div>
              <h4><i className="ti ti-bug" style={{ 'color': 'var(--amber)' }}></i> 0 open defects <span className="ct-sm">✓ clear</span></h4>
              <p>No manual defects logged. Zero regression enforced.</p>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
