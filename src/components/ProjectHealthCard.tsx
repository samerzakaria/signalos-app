/**
 * ProjectHealthCard.tsx — compact "Project health" card for the Dashboard (#14).
 *
 * Lists each project artifact reported by the Rust `get_project_artifacts`
 * IPC (ipc.project.artifacts() → { workspace, initialized, artifacts:
 * [{name, path, kind, exists, detail}] }) with a green tick when present or
 * a grey dash when missing. Loads once on dashboard mount; when no workspace
 * is active the IPC rejects ("No workspace selected") and the card degrades
 * to a quiet "no active project" note instead of crashing.
 *
 * State is held in @preact/signals (not preact/hooks) to match the rest of
 * this codebase — see MEMORY.md / TestDebtPanel.tsx for the documented
 * pattern (module-level signals + a mounted guard + a test reset).
 */
import { signal, useSignal } from '@preact/signals';
import * as ipc from '../js/ipc.js';

export type ProjectArtifact = {
  name: string;
  path: string;
  kind: string;
  exists: boolean;
  detail: string;
};

export type ProjectArtifactsPayload = {
  workspace: string;
  initialized: boolean;
  artifacts: ProjectArtifact[];
};

const artifacts = signal<ProjectArtifact[]>([]);
const loading = signal<boolean>(true);
const noProject = signal<boolean>(false);

async function refresh(): Promise<void> {
  loading.value = true;
  try {
    const payload = (await ipc.project.artifacts()) as Partial<ProjectArtifactsPayload> | null;
    const list = Array.isArray(payload?.artifacts) ? payload!.artifacts! : [];
    artifacts.value = list;
    noProject.value = list.length === 0;
  } catch {
    // "No workspace selected" (or a broken shell) — degrade gracefully.
    artifacts.value = [];
    noProject.value = true;
  } finally {
    loading.value = false;
  }
}

export function ProjectHealthCard() {
  // Per-instance signal so the load fires once per mount (same signals-only
  // pattern as TestDebtPanel — no preact/hooks in this codebase).
  const initOnce = useSignal(false);
  if (!initOnce.value) {
    initOnce.value = true;
    void refresh();
  }

  const list = artifacts.value;
  const presentCount = list.filter((a) => a.exists).length;

  return (
    <div className="card card-pad" data-testid="project-health-card">
      <div className="sec-cap">Project health</div>
      {loading.value && list.length === 0 && !noProject.value ? (
        <div data-testid="project-health-loading" style={{ fontSize: '12px', color: 'var(--ink-3)', padding: '8px 0' }}>
          <i className="ti ti-loader-2"></i> Checking project files…
        </div>
      ) : noProject.value ? (
        <div
          data-testid="project-health-empty"
          style={{ padding: '12px', textAlign: 'center', color: 'var(--ink-3)', fontSize: '12.5px', border: '1px dashed var(--line)', borderRadius: 'var(--r-sm)' }}
        >
          No active project — open or create a project to see its health.
        </div>
      ) : (
        <>
          <div style={{ fontSize: '11.5px', color: 'var(--ink-3)', marginBottom: '8px' }} data-testid="project-health-summary">
            {presentCount} of {list.length} artifacts present
          </div>
          <div style={{ display: 'flex', flexDirection: 'column' }} data-testid="project-health-list">
            {list.map((a) => (
              <div
                key={a.path}
                data-testid={`project-health-${a.path}`}
                title={a.detail}
                style={{ display: 'flex', alignItems: 'center', gap: '9px', padding: '6px 2px', borderBottom: '0.5px solid var(--line)', fontSize: '12.5px' }}
              >
                <i
                  className={`ti ${a.exists ? 'ti-check' : 'ti-minus'}`}
                  data-testid={a.exists ? 'artifact-present' : 'artifact-missing'}
                  style={{ flexShrink: 0, fontSize: '14px', color: a.exists ? 'var(--success-deep)' : 'var(--ink-3)' }}
                ></i>
                <span style={{ fontWeight: 500 }}>{a.name}</span>
                <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--ink-3)', fontFamily: 'var(--f-mono)', wordBreak: 'break-all', textAlign: 'right' }}>
                  {a.path}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// Exposed for tests: forces a fresh load on the next mount.
export function __resetProjectHealthForTests(): void {
  artifacts.value = [];
  loading.value = true;
  noProject.value = false;
}
