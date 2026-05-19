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
export const previewStack = signal<string>('react-vite');

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
  status?: string;
}
export interface GateCriterion {
  name: string;
  status?: string;
}
export interface GateInfo {
  id?: string;
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
  id?: string;
  gate_id?: string;
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
