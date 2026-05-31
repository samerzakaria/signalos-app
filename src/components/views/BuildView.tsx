import type { ChatBubble } from '../../state';
import { userName, chatBubbles, chatInputValue, cmdPaletteOpen } from '../../state';
// TestDebtPanel moved to sidebar tab — not rendered inline over chat
import { ChatBubbleSystem } from '../ChatBubbleSystem';
import { ProgressDetail } from '../ProgressDetail';
import { viewClass } from '../viewShell';

export function BuildView() {
  const bubbles = chatBubbles.value;
  const inputVal = chatInputValue.value;
  const paletteCls = cmdPaletteOpen.value ? 'cmd-palette open' : 'cmd-palette';
  const userAv = userName.value ? userName.value[0].toUpperCase() : '?';

  return (
    <>
<div className={viewClass('build')} data-view="build">

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
        </div>

        <div className="chat-scroll" id="chatScroll">
          <div className="chat-inner" id="chatInner">
            {bubbles.map((b) => {
              if (b.kind === 'user') {
                return (
                  <div className="msg user" key={b.id}>
                    <div className="msg-av">{userAv}</div>
                    <div>
                      <div className="bubble">{b.text}</div>
                      {b.ts && !b.historical ? <div className="msg-meta">{b.ts}</div> : null}
                    </div>
                  </div>
                );
              }
              if (b.kind === 'error') {
                return (
                  <div className="msg spark" key={b.id}>
                    <div className="msg-av"><i className="ti ti-alert-circle" style={{ 'fontSize': '17px' }}></i></div>
                    <div>
                      <div className="bubble" style={{ background: 'var(--danger-soft)', color: 'var(--danger-deep)' }}>{b.text}</div>
                    </div>
                  </div>
                );
              }
              if (b.kind === 'streaming') {
                return (
                  <div className="msg spark" key={b.id}>
                    <div className="msg-av"><i className="ti ti-sparkles" style={{ 'fontSize': '17px' }}></i></div>
                    <div>
                      <div className="bubble streaming" id={`stream-${b.id}`}>
                        <span className="stream-text">{b.text}</span><span className="stream-cursor"></span>
                      </div>
                      <div className="msg-meta">SignalOS · now</div>
                    </div>
                  </div>
                );
              }
              if (b.kind === 'plan' && b.plan) {
                const tasks = b.plan;
                const planStatus = b.planStatus || 'pending';
                const statusLabel = planStatus === 'approved' ? 'Approved · queued'
                                  : planStatus === 'running' ? 'Running'
                                  : planStatus === 'completed' ? 'Completed'
                                  : planStatus === 'failed' ? 'Failed'
                                  : planStatus === 'cancelled' ? 'Cancelled'
                                  : 'Awaiting approval';
                const statusCls = planStatus === 'pending' ? 'gate-badge'
                                : planStatus === 'failed' ? 'gate-badge'
                                : planStatus === 'cancelled' ? 'gate-badge'
                                : 'gate-badge passed';
                const costDelta = (typeof b.costBefore === 'number' && typeof b.costAfter === 'number')
                  ? Math.max(0, b.costAfter - b.costBefore)
                  : null;
                return (
                  <div className="msg spark" key={b.id}>
                    <div className="msg-av"><i className="ti ti-sparkles" style={{ 'fontSize': '17px' }}></i></div>
                    <div style={{ flex: 1 }}>
                      <div className="card" style={{ marginBottom: '6px', padding: '14px 16px' }}>
                        <div className="gate-head" style={{ marginBottom: '12px' }}>
                          <div className="gate-ic"><i className="ti ti-list-check"></i></div>
                          <div className="gate-tx">
                            <h3 style={{ margin: 0 }}>Plan · Wave 1</h3>
                            <p style={{ margin: '2px 0 0', fontSize: '12.5px' }}>{tasks.length} task{tasks.length !== 1 ? 's' : ''} · review and approve to start the build wave.</p>
                          </div>
                          <div className={statusCls}>{statusLabel}</div>
                        </div>
                        <div className="acts">
                          {tasks.map((t, i) => {
                            const cls = t.status === 'completed' ? 'act done'
                                      : t.status === 'in_progress' ? 'act ongoing'
                                      : t.status === 'failed' ? 'act'
                                      : 'act pending';
                            const ic = t.status === 'completed' ? 'ti-check'
                                     : t.status === 'in_progress' ? 'ti-loader-2'
                                     : t.status === 'failed' ? 'ti-x'
                                     : '';
                            const pillLabel = t.status === 'completed' ? 'Done'
                                            : t.status === 'in_progress' ? 'Running'
                                            : t.status === 'failed' ? 'Failed'
                                            : 'Pending';
                            return (
                              <div className={cls} key={t.id || i}>
                                <div className="act-ic">{ic ? <i className={`ti ${ic}`}></i> : null}</div>
                                <div className="act-name" style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                                  <span>{t.title}</span>
                                  {t.files && t.files.length > 0 ? (
                                    <span style={{ fontSize: '11px', color: 'var(--ink-3)', fontFamily: 'var(--f-mono)' }}>{t.files.join(' · ')}</span>
                                  ) : null}
                                </div>
                                <div className="act-pill" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                  <span>{pillLabel}</span>
                                  {t.status === 'failed' ? (
                                    <button
                                      className="btn btn-soft"
                                      style={{ fontSize: '10.5px', padding: '3px 8px' }}
                                      onClick={() => window.retryTask(b.id, t.id)}
                                      title="Re-dispatch this task"
                                    >
                                      <i className="ti ti-refresh"></i> Retry
                                    </button>
                                  ) : null}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                        {planStatus === 'pending' ? (
                          <div className="verdict ready" style={{ marginTop: '14px' }}>
                            <div className="verdict-ic"><i className="ti ti-circle-check"></i></div>
                            <div className="verdict-tx">Approve to sign Gate 2 and dispatch the parallel orchestrator.</div>
                            <button className="btn btn-primary" onClick={() => window.approvePlan(b.id)}>Approve &amp; run <i className="ti ti-player-play"></i></button>
                          </div>
                        ) : null}
                        {planStatus === 'running' ? (
                          <div className="verdict held" style={{ marginTop: '14px' }}>
                            <div className="verdict-ic"><i className="ti ti-loader-2" style={{ animation: 'spin 1s linear infinite' }}></i></div>
                            <div className="verdict-tx">Orchestrator dispatching tasks across worktrees…</div>
                            <button className="btn btn-soft" style={{ fontSize: '12px', padding: '6px 11px' }} onClick={() => window.cancelWave(b.id)}><i className="ti ti-player-stop"></i> Cancel wave</button>
                          </div>
                        ) : null}
                        {planStatus === 'cancelled' ? (
                          <div className="verdict held" style={{ marginTop: '14px' }}>
                            <div className="verdict-ic"><i className="ti ti-ban"></i></div>
                            <div className="verdict-tx">Wave cancelled. In-flight tasks may still finish in the background.</div>
                          </div>
                        ) : null}
                        {costDelta !== null && (planStatus === 'completed' || planStatus === 'failed' || planStatus === 'cancelled') ? (
                          <div style={{ marginTop: '10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 12px', background: 'var(--surface-warm)', borderRadius: 'var(--r-sm)', fontSize: '12px', color: 'var(--ink-2)' }}>
                            <span><i className="ti ti-coin" style={{ verticalAlign: 'middle' }}></i> Wave spend</span>
                            <span style={{ fontFamily: 'var(--f-mono)', fontWeight: 600, color: 'var(--ink)' }}>${costDelta.toFixed(4)}</span>
                          </div>
                        ) : null}
                        {(planStatus === 'completed' || planStatus === 'failed' || planStatus === 'cancelled')
                          && b.preWaveSha && !b.rolledBack ? (
                          <div style={{ marginTop: '10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 12px', borderRadius: 'var(--r-sm)', fontSize: '12px' }}>
                            <span style={{ color: 'var(--ink-3)' }}>
                              <i className="ti ti-arrow-back-up" style={{ verticalAlign: 'middle' }}></i>
                              {' '}Don't like the result? Restore the workspace to before this wave.
                            </span>
                            <button
                              className="btn btn-soft"
                              style={{ fontSize: '11.5px', padding: '5px 10px' }}
                              onClick={() => window.rollbackWave(b.id)}
                              title={`git reset --hard ${b.preWaveSha.slice(0, 8)} + delete wave files`}
                            >
                              <i className="ti ti-arrow-back-up"></i> Rollback wave
                            </button>
                          </div>
                        ) : null}
                        {b.rolledBack ? (
                          <div className="verdict held" style={{ marginTop: '10px' }}>
                            <div className="verdict-ic"><i className="ti ti-arrow-back-up"></i></div>
                            <div className="verdict-tx">Wave rolled back. Workspace restored to pre-approval state.</div>
                          </div>
                        ) : null}
                      </div>
                      <div className="msg-meta">SignalOS · plan</div>
                    </div>
                  </div>
                );
              }
              if (b.kind === 'progress') {
                const pct = b.progress && b.progress.total > 0 ? Math.round((b.progress.current / b.progress.total) * 100) : 0;
                return (
                  <div className="msg spark" key={b.id}>
                    <div className="msg-av"><i className="ti ti-activity"></i></div>
                    <div style={{ flex: 1 }}>
                      <div className="bubble" style={{ background: 'var(--surface-warm)' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                          <span style={{ fontWeight: 600 }}>{b.progress?.label || b.text}</span>
                          <span style={{ fontSize: '11px', color: 'var(--ink-3)', fontFamily: 'var(--f-mono)' }}>{b.progress?.current ?? 0} / {b.progress?.total ?? 0}</span>
                        </div>
                        <div style={{ height: '4px', background: 'var(--line)', borderRadius: '2px', overflow: 'hidden' }}>
                          <div style={{ height: '100%', width: `${pct}%`, background: 'var(--accent)', transition: 'width 0.3s var(--ease)' }}></div>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              }
              if (b.kind === 'system') {
                return (
                  <ChatBubbleSystem
                    key={b.id}
                    bubble={b}
                    onFollowup={(followup) => {
                      chatBubbles.value = [...chatBubbles.value, followup];
                    }}
                    onResolved={(id, resolution) => {
                      chatBubbles.value = chatBubbles.value.map((cb: ChatBubble) =>
                        cb.id === id ? { ...cb, waveResolved: resolution } : cb
                      );
                    }}
                  />
                );
              }
              // ai
              return (
                <div className="msg spark" key={b.id}>
                  <div className="msg-av"><i className="ti ti-sparkles" style={{ 'fontSize': '17px' }}></i></div>
                  <div>
                    <div className="bubble">{b.text}</div>
                    {b.ts && !b.historical ? <div className="msg-meta">SignalOS · {b.ts}</div> : <div className="msg-meta">SignalOS</div>}
                  </div>
                </div>
              );
            })}
            <ProgressDetail />
          </div>
        </div>

        {/* TestDebtPanel renders in the sidebar tab, not floating over chat */}

        <div className="chat-foot">
          <div className="chat-foot-inner">

            <div className={paletteCls} id="cmdPalette">
              <div className="cmd-palette-head">Commands</div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-status')}>
                <div className="cmd-item-ic"><i className="ti ti-activity"></i></div>
                <span className="cmd-item-name">/signal-status</span>
                <span className="cmd-item-desc">Force refresh status</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-review')}>
                <div className="cmd-item-ic"><i className="ti ti-eye"></i></div>
                <span className="cmd-item-name">/signal-review</span>
                <span className="cmd-item-desc">Regenerate code review</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-design')}>
                <div className="cmd-item-ic"><i className="ti ti-palette"></i></div>
                <span className="cmd-item-name">/signal-design</span>
                <span className="cmd-item-desc">Regenerate design</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-debrief')}>
                <div className="cmd-item-ic"><i className="ti ti-report"></i></div>
                <span className="cmd-item-name">/signal-debrief</span>
                <span className="cmd-item-desc">Regenerate wave retrospective</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-ship')}>
                <div className="cmd-item-ic"><i className="ti ti-rocket"></i></div>
                <span className="cmd-item-name">/signal-ship</span>
                <span className="cmd-item-desc">Skip G5 sign and ship now (logged violation)</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-brain')}>
                <div className="cmd-item-ic"><i className="ti ti-brain"></i></div>
                <span className="cmd-item-name">/signal-brain</span>
                <span className="cmd-item-desc">Show notes</span>
              </div>
            </div>
            <div className="composer">
              <input
                id="chatInput"
                placeholder="Tell SignalOS anything, or type / for commands…"
                value={inputVal}
                onInput={(e) => { chatInputValue.value = (e.target as HTMLInputElement).value; window.composerInput(e); }}
                onKeyDown={(e) => window.composerKey(e)}
              />
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
