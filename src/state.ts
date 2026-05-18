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
export const streamBubbles = signal<Record<string, any>>({});
export const currentGateId = signal<string | null>(null);
export const gateOpen = signal<boolean>(false);
export const enfOpen = signal<boolean>(false);
export const keyVisible = signal<boolean>(false);
export const updateChannel = signal<string>("beta");
export const workspacePath = signal<string>("");
export const termHistory = signal<string[]>([]);
export const termHistIdx = signal<number>(-1);

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
  name?: string;
  status?: string;
  signed?: boolean;
  is_current?: boolean;
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
