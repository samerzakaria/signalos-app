import { effect } from '@preact/signals';
import { workspacePath } from '../state';

// In-memory cache of governance docs read from the active workspace.
// Re-read on workspace change. Files may not exist yet (fresh project
// before slice 2 instantiates them) -- in that case the value stays null
// and the chat prompt falls back to the generic preamble.

interface GovernanceContext {
  soul: string | null;
  constitution: string | null;
  decisionDna: string | null;
  planTemplate: string | null;
}

let cache: GovernanceContext = {
  soul: null,
  constitution: null,
  decisionDna: null,
  planTemplate: null,
};

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = window.__TAURI__;
  const invoke = tauri?.core?.invoke || tauri?.invoke;
  if (!invoke) throw new Error('Tauri runtime not available');
  return invoke<T>(cmd, args);
}

async function tryRead(relativePath: string): Promise<string | null> {
  try {
    return await tauriInvoke<string>('read_workspace_file', { relative_path: relativePath });
  } catch {
    return null;
  }
}

function trim(content: string | null, maxChars: number): string | null {
  if (!content) return null;
  if (content.length <= maxChars) return content;
  return content.slice(0, maxChars) + '\n\n[...trimmed for prompt budget...]';
}

async function reload(): Promise<void> {
  if (!workspacePath.value) {
    cache = { soul: null, constitution: null, decisionDna: null, planTemplate: null };
    return;
  }
  const [soul, constitution, decisionDna, planTemplate] = await Promise.all([
    tryRead('core/governance/Governance/SOUL-DOCUMENT.md'),
    tryRead('core/governance/Governance/CONSTITUTION.md'),
    tryRead('core/governance/Governance/DECISION-DNA.md'),
    tryRead('core/governance/Templates/plan-template.md'),
  ]);
  cache = {
    soul: trim(soul, 4000),
    constitution: trim(constitution, 4000),
    decisionDna: trim(decisionDna, 2500),
    planTemplate: trim(planTemplate, 2500),
  };
}

export function getProtocolContext(): GovernanceContext {
  return cache;
}

export function buildContextBlock(): string {
  const parts: string[] = [];
  if (cache.soul) {
    parts.push('## Project Soul Document\n\n' + cache.soul);
  }
  if (cache.constitution) {
    parts.push('## Project Constitution\n\n' + cache.constitution);
  }
  if (cache.decisionDna) {
    parts.push('## Decision DNA (prior decisions)\n\n' + cache.decisionDna);
  }
  if (parts.length === 0) return '';
  return '\n\n---\n# Project context (read-only, from workspace)\n\n' + parts.join('\n\n') + '\n\n---\n';
}

// Reload whenever workspacePath becomes non-empty -- catches the
// onboarding flow's set_workspace + signal-init + governance instantiation.
effect(() => {
  const ws = workspacePath.value;
  if (ws) {
    reload().catch(() => {});
  } else {
    cache = { soul: null, constitution: null, decisionDna: null, planTemplate: null };
  }
});

// Also expose a forced refresh for callers that just wrote a governance file.
export { reload as refreshProtocolContext };
