import { signal } from '@preact/signals';

export interface Identity {
  name: string;
  role: string;
}

export interface Provider {
  provider: string;
  model: string;
}

export const tab = signal<string>("dashboard");
export const sbTab = signal<string>("projects");
export const ai = signal<string>("anthropic");
export const aiModel = signal<string>("claude-sonnet-4-6");
export const userName = signal<string>("");
export const userRole = signal<string>("");
export const waveFrozen = signal<boolean>(false);
export const busy = signal<boolean>(false);
export const currentGateId = signal<string | null>(null);
export const gateOpen = signal<boolean>(false);
export const enfOpen = signal<boolean>(false);
export const keyVisible = signal<boolean>(false);
export const updateChannel = signal<string>("beta");
export const workspacePath = signal<string>("");
export interface RecentWorkspace {
  path: string;
  name: string;
  last_opened?: string;
  exists?: boolean;
  is_directory?: boolean;
  initialized?: boolean;
  profile_id?: string | null;
}
export const recentWorkspaces = signal<RecentWorkspace[]>([]);
export const selectedProductProfile = signal<string>('generic');
export const productProfiles = signal([
  { id: 'generic', name: 'Generic Product Repo' },
  { id: 'react-vite', name: 'React + Vite' },
]);
export const termHistory = signal<string[]>([]);
export const termHistIdx = signal<number>(-1);
export const modalOpen = signal<string | null>(null);

export interface EnfRule {
  name?: string;
  rule?: string;
  description?: string;
  desc?: string;
  status?: string;
}
export const enforcementRules = signal<EnfRule[]>([]);

export const previewDevice = signal<string>('desktop');
export const previewUrl = signal<string>('');
export const previewStatus = signal<'idle' | 'starting' | 'installing' | 'running' | 'stopped' | 'error'>('idle');
export const previewKey = signal<string>('');
export const previewStack = signal<string>('');

export const monthlyCap = signal<number | null>(null);
export const engineRunning = signal<boolean | null>(null);
export const engineTestState = signal<'idle' | 'testing' | 'ok' | 'failed'>('idle');
export const engineRestartState = signal<'idle' | 'restarting'>('idle');

export interface UpdateCheck {
  checking: boolean;
  visible: boolean;
  hasUpdate: boolean;
  message: string;
}
export const updateCheck = signal<UpdateCheck>({
  checking: false,
  visible: false,
  hasUpdate: false,
  message: 'Up to date',
});

export const brainFilter = signal<string>('all');
export const revealedSecrets = signal<Record<string, string>>({});
export const copiedSecret = signal<string | null>(null);

// Bulk .env import modal state
export interface BulkDiffResult {
  added: string[];
  changed: string[];
  unchanged: string[];
  removed: string[];
  applied: boolean;
}
export const bulkImportOpen = signal<boolean>(false);
export const bulkImportText = signal<string>('');
export const bulkImportDiff = signal<BulkDiffResult | null>(null);
export const bulkImportError = signal<string | null>(null);
export const bulkImportAllowRemovals = signal<boolean>(false);

export interface TermLine {
  kind: 'output' | 'echo' | 'error' | 'loading' | 'dim';
  text: string;
  pathName?: string;
}
export const terminalLines = signal<TermLine[]>([
  { kind: 'dim', text: 'SignalOS terminal' },
  { kind: 'dim', text: "Type a command, or tap one below. New here? Try 'help'." },
]);
export const termInputValue = signal<string>('');

export const obStep = signal<number>(1);
export const provMoreOpen = signal<boolean>(false);
export const keyLabel = signal<string>('Anthropic API key');
export const apiKeyInput = signal<string>('');
export const budgetInputValue = signal<string>('');

export interface PlanTask {
  id: string;
  title: string;
  description?: string;
  files?: string[];
  tier?: string;
  effort_days?: number;
  status?: string;
  skills?: string[];
  /** Populated by retryTask: the reason the previous attempt failed.
   *  When set, the orchestrator prepends a "## Previous attempt failed
   *  with:" section to the task prompt so the LLM can avoid the same
   *  failure mode. */
  previous_failure?: string;
}

export interface ChatBubble {
  id: string;
  kind: 'user' | 'ai' | 'streaming' | 'error' | 'plan' | 'progress' | 'system';
  text: string;
  ts?: string;
  historical?: boolean;
  plan?: PlanTask[];
  planStatus?: 'pending' | 'approved' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress?: { current: number; total: number; label?: string };
  // Cost accounting captured around the orchestrate call (session_usd delta).
  costBefore?: number;
  costAfter?: number;
  // When the user clicks Cancel, this flips true. Orchestrator events with
  // this bubble's wave are ignored from that point on; the running Python
  // task batch completes but the UI stops responding to it.
  cancelled?: boolean;
  // "Undo Wave" plumbing: the pre-wave HEAD SHA captured by
  // /signal-checkpoint when approvePlan kicks off. Used by
  // rollbackWave() to restore the workspace. Absent means the wave
  // ran before checkpointing was wired (rollback unavailable).
  preWaveSha?: string;
  // Cumulative list of files the wave wrote, collected from per-task
  // progress events as they arrive. The rollback path passes this to
  // /signal-rollback --files so the sidecar deletes the right set.
  filesWritten?: string[];
  // True once a rollback completed; the plan card hides the Rollback
  // button after this so a double-click can't re-issue the destructive
  // command.
  rolledBack?: boolean;

  // ── Wave-engine system bubble metadata (M-W3+ / chat hook) ────────────
  // For kind === 'system' bubbles produced by the wave engine. Used by
  // ChatBubbleSystem to render interactive prompts (4-way scope-drift,
  // 3-way violation) instead of a plain info row.
  gate?: 'G0' | 'G1' | 'G2' | 'G3' | 'G4' | 'G5' | null;
  /** Engine action that produced this bubble — drives interactive UI:
   *  'scope-drift-prompt' → 4-way (amend / new-parallel / new-folder / keep)
   *  'violation-prompt'   → 3-way (fix-now / defer / override-with-log)
   *  anything else        → plain info row */
  waveAction?: string;
  /** Original user request — needed to re-fire wave:scope-drift-resolve. */
  waveUserRequest?: string;
  /** Violation prompt payload — needed to re-fire wave:violation-confirm. */
  waveViolation?: {
    violation_kind: string;
    findings: string[];
    gate?: 'G0' | 'G1' | 'G2' | 'G3' | 'G4' | 'G5' | null;
  };
  /** Set after a prompt button is clicked so the buttons disable + the
   *  bubble shows the chosen action. */
  waveResolved?: { choice: string; followupText?: string };
}
export const chatBubbles = signal<ChatBubble[]>([]);
export const chatInputValue = signal<string>('');
export const cmdPaletteOpen = signal<boolean>(false);

export const currentWave = signal<string>('1');

export interface WorkspaceEntry {
  name: string;
  path: string;
  kind: string; // "dir" | "file"
  bytes?: number;
  modified_ms?: number;
}
export const fileTreeEntries = signal<WorkspaceEntry[]>([]);
export const recentlyChangedFiles = signal<Set<string>>(new Set());

export interface GateActivity {
  name: string;
  title?: string;
  status?: string;
}
export interface GateCriterion {
  name: string;
  status?: string;
  // M3 (gate emissions) — the IPC payload from build_status_json's
  // `gate_details` array includes these per-criterion fields. They're
  // optional in the type because older payloads may omit them.
  description?: string;
  evidence?: string;
}
export interface GateInfo {
  id?: string | number;
  name?: string;
  description?: string;
  status?: string;
  signed?: boolean;
  is_current?: boolean;
  activities?: GateActivity[];
  criteria?: GateCriterion[];
}
export interface WaveSummary {
  current_gate_name?: string;
  name?: string;
  number?: number;
  total_gates?: number;
}
export const currentWaveSummary = signal<WaveSummary | null>(null);
export const gateActivities = signal<GateActivity[]>([]);
export const gateCriteria = signal<GateCriterion[]>([]);
export const currentGateInfo = signal<GateInfo | null>(null);
export const signFormOpen = signal<boolean>(false);

export interface ReleaseReadinessCheck {
  id: string;
  status?: string;
  severity: string;
  message: string;
  evidence?: string[];
}
export interface ReleaseReadinessResult {
  schema_version?: string;
  ok?: boolean;
  pass?: boolean;
  status?: string;
  checks?: ReleaseReadinessCheck[];
  blockers?: ReleaseReadinessCheck[];
  evidence?: string[];
  evidence_path?: string | null;
  next_action?: string;
  publish_relationship?: string;
  generated_at?: string;
}
export interface ReleaseReadinessState {
  loading: boolean;
  error: string | null;
  result: ReleaseReadinessResult | null;
}
export const releaseReadiness = signal<ReleaseReadinessState>({
  loading: false,
  error: null,
  result: null,
});

export interface Secret {
  name?: string;
  key?: string;
  file?: string;
  value?: string;
}

export interface BrainEntry {
  entry_type?: string;
  type?: string;
  title?: string;
  text?: string;
  body?: string;
  ts?: string;
  created_at?: string;
}

export interface Gate {
  id?: string | number;
  gate_id?: string | number;
  name?: string;
  status?: string;
  signed?: boolean;
  is_current?: boolean;
  activities?: GateActivity[];
  criteria?: GateCriterion[];
}

export interface AuditEntry {
  action?: string;
  ts?: string;
  timestamp?: string;
}

export const secretsList = signal<Secret[]>([]);
export const brainList = signal<BrainEntry[]>([]);
export const govGatesList = signal<Gate[]>([]);
export const auditList = signal<AuditEntry[]>([]);
export const currentCost = signal<number>(0);

export interface ProviderModel {
  id: string;
  name: string;
}
export const providerModels = signal<ProviderModel[]>([]);
