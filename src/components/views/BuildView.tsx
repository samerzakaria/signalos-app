import type { ChatBubble } from '../../state';
import { userName, chatBubbles, chatInputValue, cmdPaletteOpen, busy, resumableRunId, budgetInputValue, govGatesList, currentGateInfo, releaseReadiness } from '../../state';
import { summarizeMacro, macroLine } from '../../services/macroProgress';
import { waveValueFraming } from '../../services/costFraming';
import { requiredRoleForGate, roleLabel } from '../../services/gateRoles';
// TestDebtPanel moved to sidebar tab — not rendered inline over chat
import { ChatBubbleSystem } from '../ChatBubbleSystem';
import { ProgressDetail } from '../ProgressDetail';
import { Markdown, CodeBlock } from '../markdown';
import { ToolCallBubble } from '../ToolCallBubble';
import { FileDiffBubble } from '../FileDiffBubble';
import { GateReviewCard, type GateReviewSubmission } from '../GateReviewCard';
import { submitGateVerdict } from '../../services/agentEvents';
import { UxFrictionCard } from '../UxFrictionCard';
import { ChatPreviewBubble } from '../ChatPreviewBubble';
import { isGovernedCommand } from '../../services/governedShell';
import { BUSINESS_STAGES } from '../../services/deliveryFlow';
import { voiceState } from '../../services/voiceInput';
import { CompetitorPanel } from '../CompetitorPanel';
import { viewClass } from '../viewShell';

function newBubbleId(): string {
  return (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : String(Date.now()) + Math.random();
}

function fileLanguage(path: string, fallback?: string): string {
  if (fallback) return fallback;
  const ext = path.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, string> = {
    ts: 'ts',
    tsx: 'tsx',
    js: 'js',
    jsx: 'jsx',
    json: 'json',
    css: 'css',
    html: 'html',
    md: 'markdown',
    markdown: 'markdown',
    py: 'py',
    rs: 'rust',
    toml: 'toml',
    yaml: 'yaml',
    yml: 'yaml',
  };
  return map[ext] || 'text';
}

function isMarkdownFile(path: string, markdown?: boolean): boolean {
  if (typeof markdown === 'boolean') return markdown;
  return /\.(md|markdown)$/i.test(path);
}

function FileViewerBubble({ bubble }: { bubble: ChatBubble }) {
  const file = bubble.file;
  if (!file) return null;
  const lang = fileLanguage(file.path, file.language);
  const renderMarkdown = isMarkdownFile(file.path, file.markdown);
  return (
    <div className="msg spark" data-testid="file-viewer-bubble">
      <div className="msg-av"><i className="ti ti-file-text" style={{ fontSize: '17px' }}></i></div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="file-viewer" data-path={file.path}>
          <div className="file-viewer-head">
            <i className="ti ti-file"></i>
            <span className="file-viewer-path">{file.path}</span>
            <span className="file-viewer-kind">{renderMarkdown ? 'document' : lang}</span>
          </div>
          <div className={renderMarkdown ? 'file-viewer-doc' : 'file-viewer-code'}>
            {renderMarkdown ? <Markdown text={file.content} /> : <CodeBlock code={file.content} lang={lang} />}
          </div>
          {file.truncated ? (
            <div className="file-viewer-more">
              Showing first 400 of {file.totalLines || 'many'} lines.
            </div>
          ) : null}
        </div>
        <div className="msg-meta">File</div>
      </div>
    </div>
  );
}

export function BuildView() {
  const bubbles = chatBubbles.value;
  const inputVal = chatInputValue.value;
  const paletteCls = cmdPaletteOpen.value ? 'cmd-palette open' : 'cmd-palette';
  const userAv = userName.value ? userName.value[0].toUpperCase() : '?';
  // 1.7 unified input: auto-detect /signal-* commands vs natural language so we
  // can hint the user (and, in Phase 3, route to the governed shell).
  const inputIsCommand = isGovernedCommand(inputVal);
  // 1.9 streaming / tool-use indicator: the agent is "working" while a
  // streaming bubble is present or a tool call is running, or busy is set.
  const agentWorking =
    busy.value ||
    bubbles.some(
      (b) => b.kind === 'streaming' || (b.kind === 'tool' && b.tool?.status === 'running'),
    );

  // Business-stage strip (plan: Brief → Design → Build → Validate → Security →
  // Launch → Handoff). Active stage is supplied by the agent loop in Phase 3;
  // until then we surface the first stage as active so the strip is meaningful.
  const activeStage = bubbles.find((b) => b.kind === 'gate' && b.gateReview)?.gateReview?.gate;

  return (
    <>
<div className={viewClass('build')} data-view="build">

        <div className="phase-strip" data-testid="business-stage-strip">
          {BUSINESS_STAGES.flatMap((stage, i) => {
            const isLast = i === BUSINESS_STAGES.length - 1;
            const activeCls = activeStage ? '' : i === 0 ? ' active' : '';
            const node = (
              <div className="phase-node" key={stage.label}>
                <div className={`phase-dot${activeCls}`}></div>
                <span className={`phase-label${activeCls}`}>{stage.label}</span>
              </div>
            );
            return isLast
              ? [node]
              : [node, <div className="phase-conn" key={`conn-${stage.label}`}></div>];
          })}
        </div>

        {(() => {
          const line = macroLine(summarizeMacro(govGatesList.value, currentGateInfo.value, releaseReadiness.value.result));
          return line ? (
            <div
              className="macro-strip"
              data-testid="macro-progress"
              title="Macro progress — also on the Dashboard tab"
              style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '4px 14px', fontSize: '11.5px', color: 'var(--ink-3)' }}
            >
              <i className="ti ti-gauge" style={{ verticalAlign: 'middle' }}></i>
              <span>{line}</span>
            </div>
          ) : null;
        })()}

        {/* #16 — competitor URLs for the brief step (analysis stored backend-side). */}
        <CompetitorPanel />

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
                        <span className="stream-text"><Markdown text={b.text} /></span><span className="stream-cursor"></span>
                      </div>
                      <div className="msg-meta">Foundry · now</div>
                    </div>
                  </div>
                );
              }
              // 1.3 tool call bubble
              if (b.kind === 'tool' && b.tool) {
                return (
                  <ToolCallBubble
                    key={b.id}
                    tool={b.tool.name}
                    target={b.tool.target}
                    status={b.tool.status}
                    summary={b.tool.summary}
                    detail={b.tool.detail}
                  />
                );
              }
              // 1.4 file diff bubble
              if (b.kind === 'file' && b.file) {
                return <FileViewerBubble key={b.id} bubble={b} />;
              }
              if (b.kind === 'diff' && b.diff) {
                return (
                  <FileDiffBubble
                    key={b.id}
                    path={b.diff.path}
                    before={b.diff.before}
                    after={b.diff.after}
                  />
                );
              }
              // #12 UX-friction gate review card — informational, lands before
              // the design-gate review card (event order from the orchestrator).
              if (b.kind === 'friction' && b.uxFriction) {
                return (
                  <UxFrictionCard
                    key={b.id}
                    gate={b.uxFriction.gate}
                    personas={b.uxFriction.personas}
                  />
                );
              }
              // 1.5 gate review card
              if (b.kind === 'gate' && b.gateReview) {
                const gr = b.gateReview;
                return (
                  <GateReviewCard
                    key={b.id}
                    gate={gr.gate}
                    title={gr.title}
                    question={gr.question}
                    resolved={gr.resolvedVerdict ?? null}
                    signingAs={roleLabel(requiredRoleForGate(gr.gate))}
                    onVerdict={(submission: GateReviewSubmission) => {
                      const setVerdict = (v: GateReviewSubmission['verdict'] | null) => {
                        chatBubbles.value = chatBubbles.value.map((cb: ChatBubble) =>
                          cb.id === b.id && cb.gateReview
                            ? { ...cb, gateReview: { ...cb.gateReview, resolvedVerdict: v } }
                            : cb,
                        );
                      };
                      // Optimistically lock the card so a second click can't
                      // double-submit while the verdict is in flight — then
                      // revert on a backend refusal so the user can retry
                      // (Claim 10: the old code marked resolved unconditionally
                      // and fire-and-forgot, so a rejection could never undo it,
                      // leaving the card permanently locked).
                      setVerdict(submission.verdict);
                      Promise.resolve(submitGateVerdict(b.id, submission.verdict, submission.feedback))
                        .then((res) => {
                          if (res && res.ok === false) {
                            setVerdict(null);
                            chatBubbles.value = [...chatBubbles.value, {
                              id: newBubbleId(),
                              kind: 'error',
                              text: `Gate verdict not accepted: ${res.error || 'unknown error'} — the gate is still open, adjust and submit again.`,
                            }];
                          }
                        })
                        .catch((err: unknown) => {
                          setVerdict(null);
                          chatBubbles.value = [...chatBubbles.value, {
                            id: newBubbleId(),
                            kind: 'error',
                            text: `Gate verdict failed to send: ${err instanceof Error ? err.message : String(err)} — the gate is still open, try again.`,
                          }];
                        });
                    }}
                  >
                    {b.text ? <Markdown text={b.text} /> : null}
                  </GateReviewCard>
                );
              }
              // 1.6 inline design preview
              if (b.kind === 'preview' && b.preview) {
                return (
                  <ChatPreviewBubble
                    key={b.id}
                    srcDoc={b.preview.srcDoc}
                    url={b.preview.url}
                    caption={b.preview.caption}
                  />
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
                          (() => {
                            const framing = waveValueFraming((b.filesWritten || []).length, budgetInputValue.value);
                            return (
                              <div style={{ marginTop: '10px', padding: '8px 12px', background: 'var(--surface-warm)', borderRadius: 'var(--r-sm)', fontSize: '12px', color: 'var(--ink-2)' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                  <span><i className="ti ti-coin" style={{ verticalAlign: 'middle' }}></i> Wave spend</span>
                                  <span style={{ fontFamily: 'var(--f-mono)', fontWeight: 600, color: 'var(--ink)' }}>
                                    ${costDelta.toFixed(4)}{framing.capLabel ? <span style={{ fontWeight: 400, color: 'var(--ink-3)' }}> {framing.capLabel}</span> : null}
                                  </span>
                                </div>
                                {framing.hoursSavedLabel ? (
                                  <div style={{ marginTop: '4px', color: 'var(--ink-3)' }}>
                                    <i className="ti ti-clock-check" style={{ verticalAlign: 'middle' }}></i> {framing.hoursSavedLabel} of developer time saved <span style={{ opacity: 0.7 }}>(estimate)</span>
                                  </div>
                                ) : null}
                              </div>
                            );
                          })()
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
                              title="Restore the workspace to the saved pre-wave snapshot"
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
                      <div className="msg-meta">Foundry · plan</div>
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
              // ai — 1.2: render markdown (headings, code blocks, lists, links)
              return (
                <div className="msg spark" key={b.id}>
                  <div className="msg-av"><i className="ti ti-sparkles" style={{ 'fontSize': '17px' }}></i></div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="bubble"><Markdown text={b.text} /></div>
                    {b.ts && !b.historical ? <div className="msg-meta">Foundry · {b.ts}</div> : <div className="msg-meta">Foundry</div>}
                  </div>
                </div>
              );
            })}
            {agentWorking ? (
              <div className="msg spark" data-testid="agent-working-indicator">
                <div className="msg-av"><i className="ti ti-sparkles" style={{ 'fontSize': '17px' }}></i></div>
                <div>
                  <div className="bubble agent-working">
                    <span className="typing-dot"></span><span className="typing-dot"></span><span className="typing-dot"></span>
                    <span className="agent-working-label">working…</span>
                    <button type="button" className="agent-stop-btn" onClick={() => window.cancelAgentRun?.()}>Stop</button>
                  </div>
                </div>
              </div>
            ) : null}
            {resumableRunId.value ? (
              <div className="msg spark" data-testid="agent-resume">
                <div className="msg-av"><i className="ti ti-refresh" style={{ 'fontSize': '15px' }}></i></div>
                <div>
                  <div className="bubble">
                    <button type="button" className="agent-resume-btn" onClick={() => window.resumeAgentRun?.()}>Resume run</button>
                  </div>
                </div>
              </div>
            ) : null}
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
                <span className="cmd-item-desc">Record quality review</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-wave-review')}>
                <div className="cmd-item-ic"><i className="ti ti-chart-dots"></i></div>
                <span className="cmd-item-name">/signal-wave-review</span>
                <span className="cmd-item-desc">Record wave signal review</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-debrief')}>
                <div className="cmd-item-ic"><i className="ti ti-report"></i></div>
                <span className="cmd-item-name">/signal-debrief</span>
                <span className="cmd-item-desc">Record wave retrospective</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-brain')}>
                <div className="cmd-item-ic"><i className="ti ti-brain"></i></div>
                <span className="cmd-item-name">/signal-brain</span>
                <span className="cmd-item-desc">Show notes</span>
              </div>

              <div className="cmd-palette-head" style={{ marginTop: '8px' }}>Security</div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-cso threats list')}>
                <div className="cmd-item-ic"><i className="ti ti-shield-lock"></i></div>
                <span className="cmd-item-name">/signal-cso threats list</span>
                <span className="cmd-item-desc">Show threat records</span>
              </div>

              <div className="cmd-palette-head" style={{ marginTop: '8px' }}>Planning</div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-autoplan list --wave W01')}>
                <div className="cmd-item-ic"><i className="ti ti-list-check"></i></div>
                <span className="cmd-item-name">/signal-autoplan list --wave W01</span>
                <span className="cmd-item-desc">Show wave plan tasks</span>
              </div>

              <div className="cmd-palette-head" style={{ marginTop: '8px' }}>Wave Control</div>
              <div className="cmd-item" onClick={() => window.runCmd('/test all')}>
                <div className="cmd-item-ic"><i className="ti ti-shield-check"></i></div>
                <span className="cmd-item-name">/test all</span>
                <span className="cmd-item-desc">Run test automation</span>
              </div>

              <div className="cmd-palette-head" style={{ marginTop: '8px' }}>Quality</div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-careful status')}>
                <div className="cmd-item-ic"><i className="ti ti-alert-triangle"></i></div>
                <span className="cmd-item-name">/signal-careful status</span>
                <span className="cmd-item-desc">Show careful mode</span>
              </div>

              <div className="cmd-palette-head" style={{ marginTop: '8px' }}>Context</div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-context-restore list')}>
                <div className="cmd-item-ic"><i className="ti ti-restore"></i></div>
                <span className="cmd-item-name">/signal-context-restore list</span>
                <span className="cmd-item-desc">Show context checkpoints</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-investigate list --wave all')}>
                <div className="cmd-item-ic"><i className="ti ti-search"></i></div>
                <span className="cmd-item-name">/signal-investigate list --wave all</span>
                <span className="cmd-item-desc">Show investigations</span>
              </div>
              <div className="cmd-item" onClick={() => window.runCmd('/signal-learn review')}>
                <div className="cmd-item-ic"><i className="ti ti-book"></i></div>
                <span className="cmd-item-name">/signal-learn review</span>
                <span className="cmd-item-desc">Review learning memory</span>
              </div>
            </div>
            <div className={`composer${inputIsCommand ? ' composer-command' : ''}`}>
              {inputIsCommand ? (
                <span className="composer-mode-badge" data-testid="composer-mode-command" title="Detected a governed command">
                  <i className="ti ti-terminal-2"></i> cmd
                </span>
              ) : null}
              <input
                id="chatInput"
                placeholder="Tell Foundry anything, or type / for commands…"
                value={inputVal}
                onInput={(e) => { chatInputValue.value = (e.target as HTMLInputElement).value; window.composerInput(e); }}
                onKeyDown={(e) => window.composerKey(e)}
              />
              <button className="cmp-btn" onClick={() => window.attachFile()} aria-label="Attach file"><i className="ti ti-paperclip"></i></button>
              <button
                className={
                  'cmp-btn' +
                  (voiceState.value === 'recording' ? ' cmp-rec' : '') +
                  (voiceState.value === 'transcribing' ? ' cmp-transcribing' : '')
                }
                onClick={() => window.voiceInput()}
                aria-label={
                  voiceState.value === 'recording'
                    ? 'Stop recording'
                    : voiceState.value === 'transcribing'
                      ? 'Transcribing…'
                      : 'Voice input'
                }
                title={
                  voiceState.value === 'recording'
                    ? 'Recording — click again to stop, Esc to cancel'
                    : voiceState.value === 'transcribing'
                      ? 'Transcribing…'
                      : 'Voice input'
                }
                data-testid="voice-input-btn"
                data-voice-state={voiceState.value}
              >
                <i
                  className={`ti ${
                    voiceState.value === 'recording'
                      ? 'ti-player-stop-filled'
                      : voiceState.value === 'transcribing'
                        ? 'ti-loader-2'
                        : 'ti-microphone'
                  }`}
                ></i>
              </button>
              <button className="cmp-btn cmp-send" onClick={() => window.sendMsg()} aria-label="Send"><i className="ti ti-arrow-up"></i></button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
