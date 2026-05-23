import {
  userName,
  govGatesList,
  currentWaveSummary,
  currentGateInfo,
  gateActivities,
  gateCriteria,
  releaseReadiness,
  signFormOpen,
} from '../../state';
import { refreshReleaseReadiness } from '../../services/releaseReadiness';
import {
  GateTimeline,
  activeGateIndex,
  gateCode,
  isGateSigned,
} from '../GateTimeline';
import { VelocityPanel } from './VelocityPanel';

export function DashboardView() {
  const gates = govGatesList.value;
  const wave = currentWaveSummary.value;
  const gate = currentGateInfo.value;
  const activities = gateActivities.value;
  const criteria = gateCriteria.value;
  const readiness = releaseReadiness.value;
  const readinessResult = readiness.result;

  const total = wave?.total_gates ?? (gates.length || 0);
  const signedCount = gates.filter(isGateSigned).length;
  const pct = total > 0 ? Math.round((signedCount / total) * 100) : 0;
  const dashOffset = 276.46 * (1 - pct / 100);

  const activeIdx = activeGateIndex(gates);
  const activeGate = activeIdx >= 0 ? gates[activeIdx] : null;
  const heroTitle = activeGate
    ? `${gateCode(activeGate, activeIdx)} of ${total || gates.length} — ${activeGate.name || 'Current gate'}`
    : gates.length === 0
      ? 'No wave loaded'
      : 'All gates signed';

  const actDone = activities.filter((a) => a.status === 'completed').length;
  const actOngoing = activities.filter((a) => a.status === 'in_progress').length;
  const actPending = activities.filter((a) => !a.status || a.status === 'pending').length;
  const critPassed = criteria.filter((c) => c.status === 'passed').length;

  const ready = activities.length > 0 && actDone === activities.length && critPassed === criteria.length;
  const verdictCls = ready ? 'verdict ready' : 'verdict held';
  const verdictIcon = ready ? 'ti-circle-check' : 'ti-lock';
  const aLeft = activities.length - actDone;
  const cLeft = criteria.length - critPassed;
  let verdictText: string;
  if (activities.length === 0 && criteria.length === 0) {
    verdictText = 'No active gate. Approve a plan to start a wave.';
  } else if (ready) {
    verdictText = 'All clear — sign the gate to advance.';
  } else {
    const parts: string[] = [];
    if (aLeft > 0) parts.push(`${aLeft} activit${aLeft > 1 ? 'ies' : 'y'} to finish`);
    if (cLeft > 0) parts.push(`${cLeft} check${cLeft > 1 ? 's' : ''} to pass`);
    verdictText = 'Gate held' + (parts.length ? ' — ' + parts.join(' and ') + '.' : '.');
  }

  const heroSub = activities.length === 0 && criteria.length === 0
    ? `${signedCount} of ${total} gates signed.`
    : `${actDone} of ${activities.length} activities done · ${critPassed} of ${criteria.length} checks passed.`;

  const readinessOk = Boolean(readinessResult?.ok ?? readinessResult?.pass);
  const readinessStatus = readiness.loading
    ? 'Checking'
    : readiness.error
      ? 'Error'
      : readinessResult
        ? readinessOk ? 'Ready' : 'Blocked'
        : 'Not checked';
  const readinessClass = readiness.loading
    ? 'release-card checking'
    : readiness.error || (readinessResult && !readinessOk)
      ? 'release-card blocked'
      : readinessOk
        ? 'release-card ready'
        : 'release-card idle';
  const readinessIcon = readiness.loading
    ? 'ti-loader-2'
    : readiness.error || (readinessResult && !readinessOk)
      ? 'ti-alert-triangle'
      : readinessOk
        ? 'ti-circle-check'
        : 'ti-shield-check';
  const readinessBlockers = readinessResult?.blockers || [];
  const readinessEvidence = readinessResult?.evidence || [];
  const readinessNext = readiness.error
    ? readiness.error
    : readinessResult?.next_action || 'Run readiness when release evidence has been captured.';
  const publishRelationship = readinessResult?.publish_relationship || 'blocked';

  return (
    <>
<div className="view active" data-view="dashboard">
        <div className="page-head">
          <h1>Where we are</h1>
          <p>SignalOS breaks each build into gates. A gate opens only when its work is done, its checks pass, and you sign it.</p>
        </div>
        <div className="stack">
          <div className="hero">
            <div className="ring-box">
              <svg width="104" height="104" viewBox="0 0 104 104">
                <circle cx="52" cy="52" r="44" stroke="var(--accent-soft)" strokeWidth="8" fill="none"/>
                <circle id="ring" cx="52" cy="52" r="44" stroke="var(--accent)" strokeWidth="8" fill="none" strokeLinecap="round" strokeDasharray="276.46" strokeDashoffset={dashOffset}/>
              </svg>
              <div className="ring-tx">
                <span className="ring-pct" id="ringPct">{pct}%</span>
                <span className="ring-lbl">done</span>
              </div>
            </div>
            <div className="hero-tx">
              <div className="eyebrow">Right now</div>
              <h2>{heroTitle}</h2>
              <p id="heroSub">{heroSub}</p>
            </div>
            <button className="btn btn-primary" onClick={() => window.switchTab('build')}>Keep building <i className="ti ti-arrow-right"></i></button>
          </div>

          {gates.length > 0 ? (
            <div className="card card-pad">
              <div className="sec-cap">Gates</div>
              <GateTimeline gates={gates} />
            </div>
          ) : null}

          <div className={readinessClass}>
            <div className="release-head">
              <div className="release-ic"><i className={`ti ${readinessIcon}`}></i></div>
              <div className="release-title">
                <div className="sec-cap">Release readiness</div>
                <h3>{readinessStatus}</h3>
                <p>{readinessNext}</p>
              </div>
              <button className="btn btn-soft btn-compact" type="button" onClick={() => refreshReleaseReadiness()} disabled={readiness.loading}>
                <i className={`ti ${readiness.loading ? 'ti-loader-2' : 'ti-refresh'}`}></i> Check
              </button>
            </div>
            <div className="release-meta">
              <span><i className="ti ti-package"></i> {publishRelationship}</span>
              <span><i className="ti ti-link"></i> {readinessEvidence.length} evidence link{readinessEvidence.length === 1 ? '' : 's'}</span>
              <span><i className="ti ti-clock"></i> {readinessResult?.generated_at || 'No run yet'}</span>
            </div>
            {readinessBlockers.length > 0 ? (
              <div className="release-blockers">
                {readinessBlockers.slice(0, 3).map((blocker) => (
                  <div className="release-blocker" key={blocker.id}>
                    <i className="ti ti-circle-x"></i>
                    <span>{blocker.message}</span>
                  </div>
                ))}
              </div>
            ) : readinessOk ? (
              <div className="release-clear">
                <i className="ti ti-circle-check"></i>
                <span>All release checks passed.</span>
              </div>
            ) : null}
          </div>

          <VelocityPanel />

          {activeGate || activities.length > 0 || criteria.length > 0 ? (
          <div className="card" id="gateCard">
            <div className="gate-head">
              <div className="gate-ic"><i className="ti ti-flame"></i></div>
              <div className="gate-tx">
                <h3>{activeGate ? `${gateCode(activeGate, activeIdx)} — ${activeGate.name || ''}` : (gate?.name || 'Current gate')}</h3>
                <p>{gate?.description || 'Tap activities to move them along, tap a check to run it. Sign the gate when everything is green.'}</p>
              </div>
              <div className="gate-badge" id="gateBadge">
                {activeGate && (activeGate.status === 'signed' || activeGate.signed)
                  ? <><i className="ti ti-check"></i> Signed</>
                  : <><span className="dot"></span> Current gate</>}
              </div>
            </div>
            <div className="subsec-head">
              <div className="lbl"><i className="ti ti-checklist"></i> Activities — the work</div>
            </div>
            <div className="act-sum">
              <span className="sum-chip sum-done"><i className="ti ti-circle-check"></i><b id="cDone">{actDone}</b> done</span>
              <span className="sum-chip sum-ongoing"><i className="ti ti-loader-2"></i><b id="cOngoing">{actOngoing}</b> ongoing</span>
              <span className="sum-chip sum-pending"><i className="ti ti-circle"></i><b id="cPending">{actPending}</b> pending</span>
            </div>
            <div className="acts" id="acts">
              {activities.length === 0 ? (
                <div style={{ padding: '14px', fontSize: '12.5px', color: 'var(--ink-3)' }}>
                  No activities yet. Approve a plan in the Build tab to populate this gate.
                </div>
              ) : activities.map((a, i) => {
                const status = a.status || 'pending';
                const cls = status === 'completed' ? 'act done'
                          : status === 'in_progress' ? 'act ongoing'
                          : 'act pending';
                const ic = status === 'completed' ? 'ti-check'
                         : status === 'in_progress' ? 'ti-loader-2'
                         : '';
                const pill = status === 'completed' ? 'Done'
                           : status === 'in_progress' ? <><span className="pdot"></span>Ongoing</>
                           : 'Pending';
                return (
                  <div className={cls} key={i}>
                    <div className="act-ic">{ic ? <i className={`ti ${ic}`}></i> : null}</div>
                    <div className="act-name">{a.name}</div>
                    <div className="act-pill">{pill}</div>
                  </div>
                );
              })}
            </div>
            <div className="subsec-div"></div>
            <div className="subsec-head">
              <div className="lbl"><i className="ti ti-shield-check"></i> Gate criteria — the checks</div>
              <div className="meta"><b id="cCrit">{critPassed}</b> of {criteria.length} passed</div>
            </div>
            <div className="crits" id="crits">
              {criteria.length === 0 ? (
                <div style={{ padding: '14px', fontSize: '12.5px', color: 'var(--ink-3)' }}>
                  No criteria defined for this gate yet.
                </div>
              ) : criteria.map((c, i) => {
                const status = c.status || 'waiting';
                const cls = status === 'passed' ? 'crit passed'
                          : status === 'failed' ? 'crit failed'
                          : status === 'checking' ? 'crit checking'
                          : 'crit waiting';
                const ic = status === 'passed' ? 'ti-shield-check'
                         : status === 'failed' ? 'ti-shield-x'
                         : status === 'checking' ? 'ti-loader-2'
                         : 'ti-shield';
                const pill = status === 'passed' ? 'Passed'
                           : status === 'failed' ? 'Needs a fix'
                           : status === 'checking' ? <><span className="pdot"></span>Checking</>
                           : 'Waiting';
                return (
                  <div className={cls} key={i}>
                    <div className="crit-ic"><i className={`ti ${ic}`}></i></div>
                    <div className="crit-name">{c.name}</div>
                    <div className="crit-pill">{pill}</div>
                  </div>
                );
              })}
            </div>
            <div className={verdictCls} id="verdict">
              <div className="verdict-ic"><i className={`ti ${verdictIcon}`}></i></div>
              <div className="verdict-tx" id="verdictTx">{verdictText}</div>
              <div className="gate-actions">
                <button className="btn btn-primary" id="openBtn" onClick={() => { signFormOpen.value = true; }} disabled={!ready}>Sign gate <i className="ti ti-pencil"></i></button>
                <button className="btn btn-soft" type="button" disabled title="Request-changes verdict is not exposed by the current gate-sign backend.">Request changes <i className="ti ti-message-x"></i></button>
                <button className="btn btn-danger" type="button" disabled title="Reject verdict is not exposed by the current gate-sign backend.">Reject <i className="ti ti-circle-x"></i></button>
              </div>
              {signFormOpen.value ? (
                <div className="sign-form" id="signForm">
                  <span className="sign-label"><i className="ti ti-user-check"></i> Sign as:</span>
                  <input className="sign-input" id="signName" placeholder="Your name" defaultValue={userName.value}/>
                  <select className="sign-select" id="signRole">
                    <option value="PO">PO — Product Owner</option>
                    <option value="PE">PE — Principal Engineer</option>
                    <option value="QA">QA — Quality</option>
                    <option value="DevOps">DevOps</option>
                  </select>
                  <button className="btn btn-primary btn-compact" onClick={() => window.openGate()}>Confirm <i className="ti ti-check"></i></button>
                </div>
              ) : null}
            </div>
          </div>
          ) : (
            <div className="card" style={{ padding: '24px', textAlign: 'center', color: 'var(--ink-3)' }}>
              <p style={{ fontSize: '13.5px', margin: 0 }}>
                No active gate. Open the Build tab and ask SignalOS to build something — your plan will sign Gate 2 and start the wave.
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
