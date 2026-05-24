// DeliverView.tsx — Product Delivery Bridge guided flow
//
// Multi-step wizard that collects prompt, shows intent/design,
// runs delivery, and displays closeout. Calls the Python sidecar
// via IPC (same pattern as VelocityPanel).
//
// CSP rules: handlers are Preact `onClick={}` (CSP-safe). No inline
// `onclick=` / `style=` attributes in hand-written HTML.

import { useState } from 'preact/hooks';
import { signal as signalIpc } from '../../js/ipc.js';
import { workspace } from '../../js/ipc.js';

type DeliveryStep = 'prompt' | 'intent' | 'design' | 'progress' | 'closeout';

interface DeliverState {
  step: DeliveryStep;
  prompt: string;
  name: string;
  profile: string;
  mode: string;
  deploy: string;
  loading: boolean;
  error: string | null;
  intent: any | null;
  design: any | null;
  closeout: any | null;
  questions: any[] | null;
  currentPhase: string | null;
  completedPhases: string[];
}

const INITIAL_STATE: DeliverState = {
  step: 'prompt',
  prompt: '',
  name: '',
  profile: 'auto',
  mode: 'auto',
  deploy: 'none',
  loading: false,
  error: null,
  intent: null,
  design: null,
  closeout: null,
  questions: null,
  currentPhase: null,
  completedPhases: [],
};

const PHASES = [
  'Intent',
  'Scaffold',
  'Design',
  'Acceptance',
  'Generation',
  'Validation',
  'Security',
  'Proof',
  'Deploy',
  'Closeout',
];

export function DeliverView() {
  const [state, setState] = useState<DeliverState>(INITIAL_STATE);

  const updateState = (patch: Partial<DeliverState>) => {
    setState((prev) => ({ ...prev, ...patch }));
  };

  const handleStartDelivery = async () => {
    if (!state.prompt.trim()) return;

    updateState({ loading: true, error: null, step: 'intent', currentPhase: 'Intent', completedPhases: [] });

    try {
      // Step 1: Get intent preview
      const intentRaw = await signalIpc.runAndWait('deliver-intent', [
        '--prompt', state.prompt,
        '--name', state.name || 'untitled',
        '--json',
      ], 30000);

      const intentText = typeof intentRaw === 'string' ? intentRaw : JSON.stringify(intentRaw);
      const intent = JSON.parse(intentText);

      updateState({
        loading: false,
        intent,
        questions: intent.questions || [],
        completedPhases: ['Intent'],
        currentPhase: null,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      updateState({ loading: false, error: message, step: 'prompt' });
    }
  };

  const handleContinueToDesign = async () => {
    updateState({ loading: true, error: null, step: 'design', currentPhase: 'Design' });

    try {
      const designRaw = await signalIpc.runAndWait('deliver-design', [
        '--prompt', state.prompt,
        '--name', state.name || 'untitled',
        '--profile', state.profile,
        '--json',
      ], 30000);

      const designText = typeof designRaw === 'string' ? designRaw : JSON.stringify(designRaw);
      const design = JSON.parse(designText);

      updateState({
        loading: false,
        design,
        completedPhases: ['Intent', 'Scaffold', 'Design'],
        currentPhase: null,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      updateState({ loading: false, error: message });
    }
  };

  const handleApproveDesign = async () => {
    updateState({ loading: true, error: null, step: 'progress', currentPhase: 'Acceptance' });

    try {
      const raw = await signalIpc.runAndWait('deliver', [
        '--prompt', state.prompt,
        '--name', state.name || 'untitled',
        '--profile', state.profile,
        '--mode', state.mode,
        '--deploy', state.deploy,
        '--json',
      ], 300000); // 5 minute timeout for full delivery

      const closeoutText = typeof raw === 'string' ? raw : JSON.stringify(raw);
      const closeout = JSON.parse(closeoutText);

      updateState({
        loading: false,
        closeout,
        step: 'closeout',
        completedPhases: [...PHASES],
        currentPhase: null,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      updateState({ loading: false, error: message });
    }
  };

  const handleOpenProduct = async () => {
    if (state.closeout?.workspace?.repo_root) {
      try {
        await workspace.set(state.closeout.workspace.repo_root);
        window.switchTab('dashboard');
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        updateState({ error: message });
      }
    }
  };

  const handleReset = () => {
    setState(INITIAL_STATE);
  };

  const handleBackToPrompt = () => {
    updateState({ step: 'prompt', error: null });
  };

  // -- Render helpers --

  const renderPhaseStrip = () => {
    return (
      <div className="phase-strip" data-testid="deliver-phase-strip">
        {PHASES.map((phase, i) => {
          const done = state.completedPhases.includes(phase);
          const active = state.currentPhase === phase;
          const dotCls = done ? 'phase-dot done' : active ? 'phase-dot active' : 'phase-dot';
          const labelCls = done ? 'phase-label done' : active ? 'phase-label active' : 'phase-label';
          return (
            <span key={phase} className="deliver-phase-item">
              {i > 0 ? <span className={done ? 'phase-conn done' : 'phase-conn'}></span> : null}
              <span className="phase-node">
                <span className={dotCls}></span>
                <span className={labelCls}>{phase}</span>
              </span>
            </span>
          );
        })}
      </div>
    );
  };

  const renderPromptStep = () => (
    <div className="deliver-step" data-testid="deliver-step-prompt">
      <div className="deliver-step-head">
        <h2>New product delivery</h2>
        <p>Describe what you want to build. SignalOS will extract intent, choose a design system, generate code, validate it, and produce a ready-to-run product.</p>
      </div>

      <div className="deliver-field">
        <label className="deliver-label">What are you building?</label>
        <textarea
          className="deliver-textarea"
          placeholder="A recipe manager that lets me save, tag, and search my favorite recipes..."
          value={state.prompt}
          onInput={(e) => updateState({ prompt: (e.target as HTMLTextAreaElement).value })}
          rows={4}
          data-testid="deliver-prompt-input"
        />
      </div>

      <div className="deliver-field">
        <label className="deliver-label">Product name</label>
        <input
          className="deliver-input"
          type="text"
          placeholder="my-recipe-app"
          value={state.name}
          onInput={(e) => updateState({ name: (e.target as HTMLInputElement).value })}
          data-testid="deliver-name-input"
        />
      </div>

      <div className="deliver-row">
        <div className="deliver-field">
          <label className="deliver-label">Profile</label>
          <select
            className="deliver-select"
            value={state.profile}
            onChange={(e) => updateState({ profile: (e.target as HTMLSelectElement).value })}
            data-testid="deliver-profile-select"
          >
            <option value="auto">Auto-detect</option>
            <option value="react-vite">React + Vite</option>
            <option value="generic">Generic</option>
          </select>
        </div>

        <div className="deliver-field">
          <label className="deliver-label">Mode</label>
          <select
            className="deliver-select"
            value={state.mode}
            onChange={(e) => updateState({ mode: (e.target as HTMLSelectElement).value })}
            data-testid="deliver-mode-select"
          >
            <option value="auto">Auto</option>
            <option value="greenfield">Greenfield</option>
            <option value="adopt">Adopt existing</option>
          </select>
        </div>

        <div className="deliver-field">
          <label className="deliver-label">Deploy</label>
          <select
            className="deliver-select"
            value={state.deploy}
            onChange={(e) => updateState({ deploy: (e.target as HTMLSelectElement).value })}
            data-testid="deliver-deploy-select"
          >
            <option value="none">No deploy</option>
            <option value="prepare">Prepare only</option>
            <option value="live">Live deploy</option>
          </select>
        </div>
      </div>

      {state.error ? (
        <div className="deliver-error" data-testid="deliver-error">
          <i className="ti ti-alert-triangle"></i> {state.error}
        </div>
      ) : null}

      <button
        className="btn btn-primary"
        onClick={handleStartDelivery}
        disabled={state.loading || !state.prompt.trim()}
        data-testid="deliver-start-btn"
      >
        {state.loading ? (
          <><i className="ti ti-loader-2"></i> Analyzing...</>
        ) : (
          <>Start delivery <i className="ti ti-arrow-right"></i></>
        )}
      </button>
    </div>
  );

  const renderIntentStep = () => (
    <div className="deliver-step" data-testid="deliver-step-intent">
      <div className="deliver-step-head">
        <h2>Intent extracted</h2>
        <p>Here is what we understood from your description. Review and continue, or go back to edit.</p>
      </div>

      {state.intent?.entities && state.intent.entities.length > 0 ? (
        <div className="deliver-section">
          <h3><i className="ti ti-database"></i> Entities</h3>
          <ul className="deliver-list">
            {state.intent.entities.map((e: string, i: number) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {state.intent?.workflows && state.intent.workflows.length > 0 ? (
        <div className="deliver-section">
          <h3><i className="ti ti-arrows-split"></i> Workflows</h3>
          <ul className="deliver-list">
            {state.intent.workflows.map((w: string, i: number) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {state.intent?.surfaces && state.intent.surfaces.length > 0 ? (
        <div className="deliver-section">
          <h3><i className="ti ti-layout"></i> Surfaces</h3>
          <ul className="deliver-list">
            {state.intent.surfaces.map((s: string, i: number) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {state.questions && state.questions.length > 0 ? (
        <div className="deliver-section">
          <h3><i className="ti ti-help-circle"></i> Questions</h3>
          <ul className="deliver-list">
            {state.questions.map((q: string, i: number) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {state.intent?.assumptions && state.intent.assumptions.length > 0 ? (
        <div className="deliver-section">
          <h3><i className="ti ti-bulb"></i> Assumptions</h3>
          <ul className="deliver-list">
            {state.intent.assumptions.map((a: string, i: number) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {state.error ? (
        <div className="deliver-error" data-testid="deliver-error">
          <i className="ti ti-alert-triangle"></i> {state.error}
        </div>
      ) : null}

      <div className="deliver-actions">
        <button className="btn btn-soft" onClick={handleBackToPrompt} data-testid="deliver-edit-btn">
          <i className="ti ti-arrow-left"></i> Edit prompt
        </button>
        <button
          className="btn btn-primary"
          onClick={handleContinueToDesign}
          disabled={state.loading}
          data-testid="deliver-continue-btn"
        >
          {state.loading ? (
            <><i className="ti ti-loader-2"></i> Loading design...</>
          ) : (
            <>Continue <i className="ti ti-arrow-right"></i></>
          )}
        </button>
      </div>
    </div>
  );

  const renderDesignStep = () => (
    <div className="deliver-step" data-testid="deliver-step-design">
      <div className="deliver-step-head">
        <h2>Design selected</h2>
        <p>Review the design decisions below. Approve to begin building your product.</p>
      </div>

      {state.design?.ui_library ? (
        <div className="deliver-section">
          <h3><i className="ti ti-palette"></i> UI library</h3>
          <div className="deliver-detail">
            <strong>{state.design.ui_library}</strong>
            {state.design.ui_reason ? <span className="deliver-meta"> — {state.design.ui_reason}</span> : null}
          </div>
        </div>
      ) : null}

      {state.design?.tokens ? (
        <div className="deliver-section">
          <h3><i className="ti ti-color-swatch"></i> Design tokens</h3>
          <div className="deliver-tokens">
            {state.design.tokens.color ? (
              <div className="deliver-token-row">
                <span className="deliver-token-label">Color</span>
                <span className="deliver-token-value">{state.design.tokens.color}</span>
              </div>
            ) : null}
            {state.design.tokens.typography ? (
              <div className="deliver-token-row">
                <span className="deliver-token-label">Typography</span>
                <span className="deliver-token-value">{state.design.tokens.typography}</span>
              </div>
            ) : null}
            {state.design.tokens.spacing ? (
              <div className="deliver-token-row">
                <span className="deliver-token-label">Spacing</span>
                <span className="deliver-token-value">{state.design.tokens.spacing}</span>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {state.design?.state_management ? (
        <div className="deliver-section">
          <h3><i className="ti ti-database"></i> State management</h3>
          <div className="deliver-detail">{state.design.state_management}</div>
        </div>
      ) : null}

      {state.design?.data_layer ? (
        <div className="deliver-section">
          <h3><i className="ti ti-server"></i> Data layer</h3>
          <div className="deliver-detail">{state.design.data_layer}</div>
        </div>
      ) : null}

      {state.design?.form_handling ? (
        <div className="deliver-section">
          <h3><i className="ti ti-forms"></i> Form handling</h3>
          <div className="deliver-detail">{state.design.form_handling}</div>
        </div>
      ) : null}

      {state.error ? (
        <div className="deliver-error" data-testid="deliver-error">
          <i className="ti ti-alert-triangle"></i> {state.error}
        </div>
      ) : null}

      <div className="deliver-actions">
        <button className="btn btn-soft" onClick={handleBackToPrompt} data-testid="deliver-back-btn">
          <i className="ti ti-arrow-left"></i> Start over
        </button>
        <button
          className="btn btn-primary"
          onClick={handleApproveDesign}
          disabled={state.loading}
          data-testid="deliver-approve-btn"
        >
          {state.loading ? (
            <><i className="ti ti-loader-2"></i> Building...</>
          ) : (
            <>Approve and build <i className="ti ti-rocket"></i></>
          )}
        </button>
      </div>
    </div>
  );

  const renderProgressStep = () => (
    <div className="deliver-step" data-testid="deliver-step-progress">
      <div className="deliver-step-head">
        <h2>Building your product</h2>
        <p>SignalOS is generating, validating, and packaging your product. This may take a few minutes.</p>
      </div>

      <div className="deliver-phases">
        {PHASES.map((phase) => {
          const done = state.completedPhases.includes(phase);
          const active = state.currentPhase === phase;
          let icon = 'ti-circle';
          let cls = 'deliver-phase';
          if (done) {
            icon = 'ti-circle-check';
            cls = 'deliver-phase done';
          } else if (active) {
            icon = 'ti-loader-2';
            cls = 'deliver-phase active';
          }
          return (
            <div className={cls} key={phase} data-testid={`deliver-phase-${phase.toLowerCase()}`}>
              <i className={`ti ${icon}`}></i>
              <span>{phase}</span>
            </div>
          );
        })}
      </div>

      {state.error ? (
        <div className="deliver-error" data-testid="deliver-error">
          <i className="ti ti-alert-triangle"></i> {state.error}
          <div className="deliver-actions" style={{ marginTop: '12px' }}>
            <button className="btn btn-soft" onClick={handleReset}>
              <i className="ti ti-arrow-left"></i> Start over
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );

  const renderCloseoutStep = () => {
    const c = state.closeout || {};
    const closureLevel = c.closure_level || c.closure || 'unknown';
    const closureCls = closureLevel === 'full' ? 'deliver-badge deliver-badge-success'
                     : closureLevel === 'partial' ? 'deliver-badge deliver-badge-warn'
                     : 'deliver-badge';

    return (
      <div className="deliver-step" data-testid="deliver-step-closeout">
        <div className="deliver-step-head">
          <h2>Ready to run</h2>
          <p>Your product has been built and validated. Here is the summary.</p>
        </div>

        <div className="deliver-section">
          <h3><i className="ti ti-package"></i> Product</h3>
          <div className="deliver-detail">
            <strong>{c.name || state.name || 'Product'}</strong>
            {c.workspace?.repo_root ? (
              <span className="deliver-meta" data-testid="deliver-repo-path"> — {c.workspace.repo_root}</span>
            ) : null}
          </div>
        </div>

        {c.files_count != null ? (
          <div className="deliver-section">
            <h3><i className="ti ti-files"></i> Generated files</h3>
            <div className="deliver-detail">{c.files_count} file{c.files_count !== 1 ? 's' : ''}</div>
          </div>
        ) : null}

        <div className="deliver-section">
          <h3><i className="ti ti-certificate"></i> Closure</h3>
          <div className="deliver-detail">
            <span className={closureCls} data-testid="deliver-closure">{closureLevel}</span>
          </div>
        </div>

        {c.how_to_run && c.how_to_run.length > 0 ? (
          <div className="deliver-section">
            <h3><i className="ti ti-player-play"></i> How to run</h3>
            <ol className="deliver-steps-list">
              {c.how_to_run.map((step: string, i: number) => (
                <li key={i}><code>{step}</code></li>
              ))}
            </ol>
          </div>
        ) : null}

        {c.limitations && c.limitations.length > 0 ? (
          <div className="deliver-section">
            <h3><i className="ti ti-alert-circle"></i> Known limitations</h3>
            <ul className="deliver-list">
              {c.limitations.map((lim: string, i: number) => (
                <li key={i}>{lim}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {c.security ? (
          <div className="deliver-section">
            <h3><i className="ti ti-shield-check"></i> Security</h3>
            <div className="deliver-detail">{c.security.status || 'Checked'}</div>
          </div>
        ) : null}

        {state.error ? (
          <div className="deliver-error" data-testid="deliver-error">
            <i className="ti ti-alert-triangle"></i> {state.error}
          </div>
        ) : null}

        <div className="deliver-actions">
          <button className="btn btn-soft" onClick={handleReset} data-testid="deliver-new-btn">
            <i className="ti ti-plus"></i> New delivery
          </button>
          {c.workspace?.repo_root ? (
            <button
              className="btn btn-primary"
              onClick={handleOpenProduct}
              data-testid="deliver-open-btn"
            >
              Open product <i className="ti ti-arrow-right"></i>
            </button>
          ) : null}
        </div>
      </div>
    );
  };

  return (
    <div className="view" data-view="deliver" data-testid="deliver-view">
      {renderPhaseStrip()}
      <div className="deliver-content">
        {state.step === 'prompt' ? renderPromptStep() : null}
        {state.step === 'intent' ? (state.loading ? renderProgressStep() : renderIntentStep()) : null}
        {state.step === 'design' ? (state.loading ? renderProgressStep() : renderDesignStep()) : null}
        {state.step === 'progress' ? renderProgressStep() : null}
        {state.step === 'closeout' ? renderCloseoutStep() : null}
      </div>
    </div>
  );
}
