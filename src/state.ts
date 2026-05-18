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
