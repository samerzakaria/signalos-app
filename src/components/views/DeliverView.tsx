// DeliverView.tsx - Product Delivery Bridge guided flow
//
// Multi-step wizard that collects prompt, shows intent/design,
// runs delivery, and displays closeout. Calls the Python sidecar
// via IPC (same pattern as VelocityPanel).
//
// CSP rules: handlers are Preact `onClick={}` (CSP-safe). No inline
// `onclick=` / `style=` attributes in hand-written HTML.

import { useEffect, useState } from 'preact/hooks';
import { onSidecarProgress, signal as signalIpc } from '../../js/ipc.js';
import { workspace } from '../../js/ipc.js';
import { projectsRoot, workspacePath } from '../../state';
import { viewClass } from '../viewShell';

type DeliveryStep = 'prompt' | 'intent' | 'design' | 'progress' | 'closeout';

interface DeliveryProgressEvent {
  id?: string;
  phase?: string;
  substep?: string;
  state?: 'running' | 'done' | 'error' | string;
  detail?: string | null;
  ts?: number;
}

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
  designPreviewHtml: string | null;
  closeout: any | null;
  questions: any[] | null;
  repoRoot: string | null;
  currentPhase: string | null;
  completedPhases: string[];
  runId: string | null;
  progressEvents: DeliveryProgressEvent[];
  startedAt: number | null;
}

const INITIAL_STATE: DeliverState = {
  step: 'prompt',
  prompt: '',
  name: '',
  profile: 'react-vite',
  mode: 'auto',
  deploy: 'none',
  loading: false,
  error: null,
  intent: null,
  design: null,
  designPreviewHtml: null,
  closeout: null,
  questions: null,
  repoRoot: null,
  currentPhase: null,
  completedPhases: [],
  runId: null,
  progressEvents: [],
  startedAt: null,
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

const PHASE_LABELS: Record<string, string> = {
  intent: 'Intent',
  scaffold: 'Scaffold',
  scaffolded: 'Scaffold',
  design: 'Design',
  acceptance: 'Acceptance',
  generated: 'Generation',
  generation: 'Generation',
  validation: 'Validation',
  validated: 'Validation',
  security: 'Security',
  proof: 'Proof',
  proved: 'Proof',
  deploy: 'Deploy',
  closeout: 'Closeout',
  closed: 'Closeout',
};

const DELIVERY_COMMAND_TIMEOUT_MS = 0;
const INTERNAL_WORKSPACE_NAME = 'SignalOS Workspace';
const WIZARD_STORAGE_KEY = 'signalos.onboarding.wizard.v1';
const TECHNICAL_QUESTION_RE = /\b(api|backend|frontend|framework|library|stack|database|dbms|sql|postgres|mysql|sqlite|docker|kubernetes|deploy|deployment|cloud|vercel|netlify|fly|render|railway|react|vite|angular|vue|svelte|zustand|jotai|redux|tanstack|swr|mantine|shadcn|tailwind|graphql|websocket|rest)\b/i;

const readStoredProjectsRoot = (): string => {
  try {
    const saved = JSON.parse(localStorage.getItem(WIZARD_STORAGE_KEY) || 'null');
    return String(saved?.projectsRoot || '').trim();
  } catch {
    return '';
  }
};

const resolveProjectsRoot = (): string => {
  const root = (projectsRoot.value || '').trim() || readStoredProjectsRoot();
  if (root && projectsRoot.value !== root) projectsRoot.value = root;
  return root;
};

const safeProductName = (value: string): string => {
  return String(value || '')
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '-')
    .replace(/[. ]+$/g, '')
    .replace(/\s+/g, '-')
    || 'NewProduct';
};

const deriveProductName = (state: DeliverState): string => {
  if (state.name.trim()) return safeProductName(state.name);
  const words = state.prompt
    .replace(/[^a-zA-Z0-9\s-]/g, ' ')
    .trim()
    .split(/\s+/)
    .filter((word) => !/^(i|we|want|need|to|build|create|make|an?|the|for|my|our)$/i.test(word))
    .slice(0, 3);
  return safeProductName(words.join('-') || 'NewProduct');
};

const isTechnicalQuestion = (question: string): boolean => {
  return TECHNICAL_QUESTION_RE.test(question);
};

const technicalDecisionLabel = (question: string): string => {
  if (/\b(database|dbms|sql|postgres|mysql|sqlite|data source|data comes from)\b/i.test(question)) {
    return 'Data storage and API shape';
  }
  if (/\b(deploy|deployment|cloud|docker|vercel|netlify|fly|render|railway)\b/i.test(question)) {
    return 'Packaging and deployment path';
  }
  if (/\b(ui|frontend|framework|library|react|vite|angular|vue|svelte|mantine|shadcn|tailwind)\b/i.test(question)) {
    return 'Frontend and visual implementation choices';
  }
  return 'Technical implementation choice';
};

const resolveDeliveryWorkspace = async (state: DeliverState): Promise<string> => {
  if (state.repoRoot) {
    await workspace.set(state.repoRoot);
    workspacePath.value = state.repoRoot;
    return state.repoRoot;
  }

  if (state.mode === 'adopt' && workspacePath.value.trim()) {
    return workspacePath.value.trim();
  }

  const root = resolveProjectsRoot();
  if (!root) {
    throw new Error('Choose a projects root folder in onboarding or Settings before starting delivery.');
  }

  const productName = deriveProductName(state);
  if (productName === INTERNAL_WORKSPACE_NAME) {
    throw new Error('Choose a product name. SignalOS Workspace is only the starter workspace, not a product.');
  }

  const repoRoot = await workspace.ensureDefault(productName, root);
  const repoPath = String(repoRoot || '').trim();
  if (!repoPath) throw new Error('SignalOS could not create the product workspace.');
  workspacePath.value = repoPath;
  return repoPath;
};

const phaseLabel = (phase: unknown): string => {
  const raw = String(phase ?? '').trim();
  if (!raw) return '';
  const key = raw.toLowerCase();
  return PHASE_LABELS[key] ?? raw.charAt(0).toUpperCase() + raw.slice(1);
};

const friendlyError = (message: string): string => {
  if (/timed out waiting for run_signal_command/i.test(message)) {
    return 'SignalOS stopped receiving a response from Core before this step finished. Restart the engine and retry from this screen.';
  }
  if (/No workspace selected/i.test(message)) {
    return 'No product workspace is selected. Finish onboarding or open a project before running this action.';
  }
  if (/undefined/i.test(message)) {
    return 'Setup failed without a useful backend message. Please retry after selecting or creating a workspace.';
  }
  return message;
};

const parseIpcJson = (raw: unknown): any => {
  const text = typeof raw === 'string' ? raw : JSON.stringify(raw);
  try {
    return JSON.parse(text);
  } catch (firstError) {
    const start = text.indexOf('{');
    const end = text.lastIndexOf('}');
    if (start >= 0 && end > start) {
      try {
        return JSON.parse(text.slice(start, end + 1));
      } catch {
        // Fall through to the original parse error; it points to the real payload.
      }
    }
    throw firstError;
  }
};

const textItems = (items: unknown): string[] => {
  if (!Array.isArray(items)) return [];
  return items
    .map((item) => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object') {
        const record = item as Record<string, unknown>;
        return String(record.question ?? record.reason ?? record.assumed_value ?? record.field ?? '');
      }
      return String(item ?? '');
    })
    .filter(Boolean);
};

const normalizeIntentPayload = (payload: any): any => {
  const intent = payload?.intent ?? payload ?? {};
  return {
    ...intent,
    primary_workflows: intent.primary_workflows ?? intent.workflows ?? [],
    ux_surfaces: intent.ux_surfaces ?? intent.surfaces ?? [],
    workflows: intent.workflows ?? intent.primary_workflows ?? [],
    surfaces: intent.surfaces ?? intent.ux_surfaces ?? [],
    assumptions: textItems(payload?.assumptions ?? intent.assumptions ?? []),
  };
};

const normalizeDesignPayload = (payload: any): any => {
  const design = payload?.design ?? payload ?? {};
  const tokens = design.tokens ?? {
    color: design.design_tokens?.primary_color,
    typography: design.design_tokens?.font_family,
    spacing: design.design_tokens?.spacing_unit ? `${design.design_tokens.spacing_unit}px` : design.design_tokens?.spacing,
  };
  return {
    ...design,
    ui_library: typeof design.ui_library === 'object' ? design.ui_library?.name : design.ui_library,
    ui_reason: typeof design.ui_library === 'object' ? design.ui_library?.reason : design.ui_reason,
    tokens,
    state_management: typeof design.state_management === 'object' ? design.state_management?.name : design.state_management,
    data_layer: typeof design.data_layer === 'object' ? design.data_layer?.name : design.data_layer,
    form_handling: typeof design.form_handling === 'object' ? design.form_handling?.name : design.form_handling,
    dependencies: payload?.dependencies ?? design.dependencies ?? design.additional_deps ?? {},
  };
};

export function DeliverView() {
  const [state, setState] = useState<DeliverState>(INITIAL_STATE);

  useEffect(() => {
    const maybeUnlisten = onSidecarProgress((payload: unknown) => {
      const event = payload as DeliveryProgressEvent;
      setState((prev) => {
        if (!event || !event.id || event.id !== prev.runId) return prev;

        const label = phaseLabel(event.phase);
        const completed = new Set(prev.completedPhases);
        if (label && event.state === 'done') completed.add(label);

        return {
          ...prev,
          currentPhase: label && event.state !== 'done' ? label : prev.currentPhase,
          completedPhases: Array.from(completed),
          progressEvents: [...prev.progressEvents, event].slice(-80),
        };
      });
    }) as unknown as (() => void) | Promise<() => void>;

    return () => {
      if (typeof maybeUnlisten === 'function') {
        maybeUnlisten();
      } else if (maybeUnlisten && typeof (maybeUnlisten as Promise<() => void>).then === 'function') {
        (maybeUnlisten as Promise<() => void>).then((unlisten) => unlisten?.()).catch(() => {});
      }
    };
  }, []);

  const updateState = (patch: Partial<DeliverState>) => {
    setState((prev) => ({ ...prev, ...patch }));
  };

  const handleStartDelivery = async () => {
    if (!state.prompt.trim()) return;

    updateState({
      loading: true,
      error: null,
      step: 'intent',
      currentPhase: 'Intent',
      completedPhases: [],
      progressEvents: [],
      runId: null,
      startedAt: Date.now(),
    });

    try {
      const repoRoot = await resolveDeliveryWorkspace(state);
      updateState({ repoRoot });

      // Step 1: Get intent preview
      const intentRaw = await signalIpc.runAndWait('deliver-intent', [
        '--prompt', state.prompt,
        '--name', deriveProductName(state),
        '--json',
      ], DELIVERY_COMMAND_TIMEOUT_MS);

      const payload = parseIpcJson(intentRaw);
      const intent = normalizeIntentPayload(payload);

      updateState({
        loading: false,
        intent,
        questions: textItems(payload.questions ?? intent.questions ?? []),
        completedPhases: ['Intent'],
        currentPhase: null,
      });
    } catch (err: unknown) {
      const message = friendlyError(err instanceof Error ? err.message : String(err));
      updateState({ loading: false, error: message, step: 'prompt' });
    }
  };

  const handleContinueToDesign = async () => {
    updateState({ loading: true, error: null, step: 'design', currentPhase: 'Design', startedAt: Date.now() });

    try {
      const repoRoot = await resolveDeliveryWorkspace(state);
      updateState({ repoRoot });

      const designRaw = await signalIpc.runAndWait('deliver-design', [
        '--prompt', state.prompt,
        '--name', deriveProductName(state),
        '--profile', state.profile,
        '--json',
      ], DELIVERY_COMMAND_TIMEOUT_MS);

      const design = normalizeDesignPayload(parseIpcJson(designRaw));

      // Fetch design preview HTML
      let designPreviewHtml: string | null = null;
      try {
        const previewRaw = await signalIpc.runAndWait('deliver-design-preview', [
          '--prompt', state.prompt,
          '--name', deriveProductName(state),
          '--profile', state.profile,
          '--repo-root', repoRoot,
          '--json',
        ], DELIVERY_COMMAND_TIMEOUT_MS);
        const previewPayload = parseIpcJson(previewRaw);
        const html = previewPayload?.preview_html;
        if (html) {
          designPreviewHtml = String(html);
        }
      } catch (_previewErr) {
        // Preview is optional — design step still works without it
      }

      updateState({
        loading: false,
        design,
        designPreviewHtml,
        completedPhases: ['Intent', 'Scaffold', 'Design'],
        currentPhase: null,
      });
    } catch (err: unknown) {
      const message = friendlyError(err instanceof Error ? err.message : String(err));
      updateState({ loading: false, error: message });
    }
  };

  const handleApproveDesign = async () => {
    updateState({
      loading: true,
      error: null,
      step: 'progress',
      currentPhase: 'Acceptance',
      progressEvents: [],
      runId: null,
      startedAt: Date.now(),
    });

    try {
      const repoRoot = await resolveDeliveryWorkspace(state);
      updateState({ repoRoot });

      const raw = await signalIpc.runAndWait('deliver', [
        '--prompt', state.prompt,
        '--name', deriveProductName(state),
        '--repo-root', repoRoot,
        '--profile', state.profile,
        '--mode', state.mode,
        '--deploy', state.deploy,
        '--json',
      ], DELIVERY_COMMAND_TIMEOUT_MS, (id: string) => {
        updateState({
          runId: id,
          currentPhase: 'Intent',
          progressEvents: [{
            id,
            phase: 'intent',
            substep: 'queued',
            state: 'running',
            detail: 'Delivery request accepted by SignalOS Core.',
            ts: Date.now(),
          }],
        });
      });

      const closeout = parseIpcJson(raw);

      updateState({
        loading: false,
        closeout,
        step: 'closeout',
        completedPhases: [...PHASES],
        currentPhase: null,
      });
    } catch (err: unknown) {
      const message = friendlyError(err instanceof Error ? err.message : String(err));
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

  const renderEventText = (event: DeliveryProgressEvent): string => {
    const phase = phaseLabel(event.phase) || 'Delivery';
    const substep = event.substep ? String(event.substep).replace(/_/g, ' ') : '';
    const detail = event.detail ? ` - ${event.detail}` : '';
    return `${phase}${substep ? ` / ${substep}` : ''}${detail}`;
  };

  const renderPromptStep = () => (
    <div className="deliver-step" data-testid="deliver-step-prompt">
      <div className="deliver-step-head">
        <h2>New product delivery</h2>
        <p>Describe the product in your own words. SignalOS will choose the technical setup, explain the plan, build it, validate it, and package it.</p>
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

      <details className="deliver-advanced" data-testid="deliver-advanced-options">
        <summary><i className="ti ti-adjustments"></i> Advanced options</summary>
        <div className="deliver-row">
          <div className="deliver-field">
            <label className="deliver-label">App profile</label>
            <select
              className="deliver-select"
              value={state.profile}
              onChange={(e) => updateState({ profile: (e.target as HTMLSelectElement).value })}
              data-testid="deliver-profile-select"
            >
              <option value="react-vite">React + Vite</option>
              <option value="auto">Auto-detect</option>
              <option value="generic">Generic</option>
            </select>
          </div>

          <div className="deliver-field">
            <label className="deliver-label">Project mode</label>
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
            <label className="deliver-label">Deployment</label>
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
      </details>

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

  const renderIntentStep = () => {
    const userQuestions = (state.questions || []).filter((q: string) => !isTechnicalQuestion(q));
    const technicalDecisions = Array.from(new Set(
      (state.questions || [])
        .filter((q: string) => isTechnicalQuestion(q))
        .map((q: string) => technicalDecisionLabel(q)),
    ));

    return (
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

      {state.intent?.primary_workflows && state.intent.primary_workflows.length > 0 ? (
        <div className="deliver-section">
          <h3><i className="ti ti-arrows-split"></i> Workflows</h3>
          <ul className="deliver-list">
            {state.intent.primary_workflows.map((w: string, i: number) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {state.intent?.ux_surfaces && state.intent.ux_surfaces.length > 0 ? (
        <div className="deliver-section">
          <h3><i className="ti ti-layout"></i> Surfaces</h3>
          <ul className="deliver-list">
            {state.intent.ux_surfaces.map((s: string, i: number) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {userQuestions.length > 0 ? (
        <div className="deliver-section">
          <h3><i className="ti ti-help-circle"></i> Questions for you</h3>
          <ul className="deliver-list">
            {userQuestions.map((q: string, i: number) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {technicalDecisions.length > 0 ? (
        <div className="deliver-section deliver-decision-note" data-testid="deliver-technical-decisions">
          <h3><i className="ti ti-sparkles"></i> Decisions SignalOS will handle</h3>
          <p className="deliver-meta">These are implementation choices. SignalOS will choose sensible defaults and show the result for approval.</p>
          <ul className="deliver-list">
            {technicalDecisions.map((decision, i) => (
              <li key={i}>{decision}</li>
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
  };

  const handleDesignOverride = (field: string, value: string) => {
    if (!state.design) return;
    const updated = { ...state.design, [field]: value };
    updateState({ design: updated });
  };

  const renderDesignStep = () => (
    <div className="deliver-step" data-testid="deliver-step-design">
      <div className="deliver-step-head">
        <h2>Build plan selected</h2>
        <p>Review the product experience SignalOS will build. Technical choices are handled for you unless you open Advanced controls.</p>
      </div>

      <div className="deliver-section">
        <h3><i className="ti ti-layout-dashboard"></i> Product experience</h3>
        <div className="deliver-decision-grid">
          <div>
            <strong>Interface foundation</strong>
            <p>{state.design?.ui_library || 'Accessible component system'} selected for fast, consistent screens.</p>
          </div>
          <div>
            <strong>Data behavior</strong>
            <p>{state.design?.data_layer || 'Local product state'} with {state.design?.state_management || 'simple state management'}.</p>
          </div>
          <div>
            <strong>Forms and input</strong>
            <p>{state.design?.form_handling || 'Clear form handling'} for validation and user feedback.</p>
          </div>
        </div>
        {state.design?.ui_reason ? <div className="deliver-meta">{state.design.ui_reason}</div> : null}
      </div>

      {state.design?.tokens ? (
        <div className="deliver-section">
          <h3><i className="ti ti-color-swatch"></i> Design tokens</h3>
          <div className="deliver-tokens">
            {state.design.tokens.color ? (
              <div className="deliver-token-row">
                <span className="deliver-token-label">Primary color</span>
                <input
                  type="color"
                  className="deliver-color-input"
                  value={state.design.tokens.color}
                  onInput={(e) => {
                    const tokens = { ...state.design.tokens, color: (e.target as HTMLInputElement).value };
                    updateState({ design: { ...state.design, tokens } });
                  }}
                />
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

      <details className="deliver-advanced" data-testid="deliver-design-advanced">
        <summary><i className="ti ti-adjustments"></i> Advanced technical controls</summary>

      <div className="deliver-section">
        <h3><i className="ti ti-palette"></i> UI library</h3>
        <select
          className="deliver-select"
          value={state.design?.ui_library || ''}
          onChange={(e) => handleDesignOverride('ui_library', (e.target as HTMLSelectElement).value)}
          data-testid="deliver-design-ui-select"
        >
          <option value="@mantine/core">Mantine (forms, tables, dates)</option>
          <option value="shadcn/ui">shadcn/ui (composable, lightweight)</option>
        </select>
      </div>

      <div className="deliver-section">
        <h3><i className="ti ti-database"></i> State management</h3>
        <select
          className="deliver-select"
          value={state.design?.state_management || 'zustand'}
          onChange={(e) => handleDesignOverride('state_management', (e.target as HTMLSelectElement).value)}
          data-testid="deliver-design-state-select"
        >
          <option value="zustand">Zustand (minimal, scalable)</option>
          <option value="jotai">Jotai (atomic, fine-grained)</option>
          <option value="redux-toolkit">Redux Toolkit (structured, middleware)</option>
        </select>
      </div>

      <div className="deliver-section">
        <h3><i className="ti ti-server"></i> Data layer</h3>
        <select
          className="deliver-select"
          value={state.design?.data_layer || 'local'}
          onChange={(e) => handleDesignOverride('data_layer', (e.target as HTMLSelectElement).value)}
          data-testid="deliver-design-data-select"
        >
          <option value="@tanstack/react-query">TanStack Query (API caching)</option>
          <option value="local">Local state only</option>
          <option value="swr">SWR (stale-while-revalidate)</option>
        </select>
      </div>

      <div className="deliver-section">
        <h3><i className="ti ti-forms"></i> Form handling</h3>
        <select
          className="deliver-select"
          value={state.design?.form_handling || 'native'}
          onChange={(e) => handleDesignOverride('form_handling', (e.target as HTMLSelectElement).value)}
          data-testid="deliver-design-form-select"
        >
          <option value="react-hook-form">React Hook Form + Zod</option>
          <option value="native">Native controlled inputs</option>
          <option value="formik">Formik</option>
        </select>
      </div>

      </details>

      {state.designPreviewHtml ? (
        <div className="deliver-section">
          <h3><i className="ti ti-eye"></i> Visual preview</h3>
          <p className="deliver-meta">This is how your product will look with the selected design.</p>
          <div className="deliver-preview-frame" data-testid="deliver-design-preview">
            <iframe
              srcDoc={state.designPreviewHtml}
              sandbox=""
              title="Design preview"
              style={{ width: '100%', height: '480px', border: '1px solid var(--border, #dee2e6)', borderRadius: '8px', background: '#fff' }}
            />
          </div>
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

  const renderProgressStep = () => {
    const completedCount = state.completedPhases.length;
    const pct = Math.min(100, Math.round((completedCount / PHASES.length) * 100));
    const latestEvents = state.progressEvents.slice(-8).reverse();

    return (
      <div className="deliver-step deliver-step-wide" data-testid="deliver-step-progress">
        <div className="deliver-progress-hero">
          <div>
            <div className="deliver-kicker">Product delivery</div>
            <h2>{state.error ? 'Delivery needs attention' : 'Building your product'}</h2>
            <p>
              {state.error
                ? 'SignalOS stopped before closeout. Review the failure and retry from a clear workspace state.'
                : 'SignalOS is scaffolding, generating, validating, and packaging the product. This screen updates as each phase reports evidence.'}
            </p>
            {state.runId ? <code className="deliver-run-id">{state.runId}</code> : null}
          </div>
          <div className="deliver-progress-meter" aria-label={`${pct}% complete`}>
            <span>{pct}%</span>
            <div><i style={{ width: `${pct}%` }}></i></div>
          </div>
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

        <div className="deliver-section deliver-live-log" data-testid="deliver-progress-log">
          <h3><i className="ti ti-activity"></i> Live evidence</h3>
          {latestEvents.length ? (
            <ol>
              {latestEvents.map((event, i) => (
                <li key={`${event.ts ?? i}-${event.phase}-${event.substep}`} className={`deliver-event ${event.state || 'running'}`}>
                  <span>{event.state || 'running'}</span>
                  <p>{renderEventText(event)}</p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="deliver-meta">Waiting for SignalOS Core to report the first phase.</p>
          )}
        </div>

        {state.error ? (
          <div className="deliver-error" data-testid="deliver-error">
            <i className="ti ti-alert-triangle"></i>
            <div>
              <strong>{state.error}</strong>
              <p>Review the live evidence above, then start over after fixing the reported blocker.</p>
              <div className="deliver-actions">
                <button className="btn btn-soft" onClick={handleReset}>
                  <i className="ti ti-arrow-left"></i> Start over
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    );
  };

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
              <span className="deliver-meta" data-testid="deliver-repo-path"> - {c.workspace.repo_root}</span>
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
    <div className={viewClass('deliver')} data-view="deliver" data-testid="deliver-view">
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
