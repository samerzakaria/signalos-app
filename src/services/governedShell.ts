// governedShell.ts — governed command routing for the Build conversation
// (Phase 1.1b of the Foundry v4 plan).
//
// This preserves command-routing logic as a reusable service. The Build
// conversation and future governed-shell tool execution call
// `runGovernedCommand()` instead of reaching into app-v2.js.
//
// IMPORTANT: this is a *governed* command surface, NOT an unrestricted OS
// shell. Only an explicit allowlist of SignalOS / git / dev commands is
// routed; anything else raises so callers can surface an honest error
// (INV-4: no silent failures).
//
// IPC access is injected so the service stays testable.

import * as defaultIpc from '../js/ipc.js';

/** Minimal IPC surface this service needs. Mirrors `js/ipc.js` shape. */
export interface GovernedShellIpc {
  signal: {
    runAndWait: (cmd: string, args: string[], timeoutMs: number) => Promise<unknown>;
  };
  git: {
    status: () => Promise<{
      branch?: string;
      is_clean?: boolean;
      ahead?: number;
      behind?: number;
      last_sync?: string;
      worktrees?: unknown[];
    }>;
  };
}

export interface GovernedShellContext {
  /** Absolute workspace path, or '' when no workspace is open. */
  workspace: string;
  /** True when the workspace is the SignalOS starter (not a product repo). */
  inStarterWorkspace: boolean;
  /** IPC bridge (defaults to the installed app IPC module when omitted). */
  ipc?: GovernedShellIpc;
  /**
   * Called when a command wants to start the dev server / preview. Defaults
   * to switching to the Preview tab + calling window.previewRun(). Injected
   * for tests and for the Build conversation, which routes preview launches
   * through services/preview.ts.
   */
  startPreview?: () => Promise<string> | string;
}

/** Result type — either a single string or an array of output lines. */
export type GovernedCommandResult = string | string[];

const HELP_LINES = [
  'Supported commands:',
  '  help              show this list',
  '  signalos status   show governance/workspace status',
  '  signalos check    run release-readiness checks',
  '  signalos gates    show gate status',
  '  npm run dev       start the Preview tab dev server',
  '  git status        show branch, cleanliness, and worktrees',
  '',
  'This is a governed SignalOS command surface, not an unrestricted OS shell.',
];

function prettyJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

async function resolveIpc(ctx: GovernedShellContext): Promise<GovernedShellIpc> {
  if (ctx.ipc) return ctx.ipc;
  return defaultIpc as unknown as GovernedShellIpc;
}

async function defaultStartPreview(ctx: GovernedShellContext): Promise<string> {
  if (!ctx.workspace) {
    throw new Error('No workspace selected. Open or create a product before starting Preview.');
  }
  try {
    window.switchTab?.('preview');
  } catch {
    /* non-fatal */
  }
  if (typeof window.previewRun === 'function') {
    void window.previewRun();
    return 'Preview starting. Open the Preview tab for server status and app output.';
  }
  return 'Preview tab is available, but the dev-server runner is not loaded yet.';
}

/**
 * Route and execute a single governed command.
 *
 * Returns the command output (string or string[]). Throws on unsupported
 * commands or IPC failures so callers can render an honest error bubble.
 */
export async function runGovernedCommand(
  cmd: string,
  ctx: GovernedShellContext,
): Promise<GovernedCommandResult> {
  const normalized = cmd.trim().replace(/\s+/g, ' ');
  const lower = normalized.toLowerCase();

  if (lower === 'help') {
    return HELP_LINES;
  }

  if (
    ctx.inStarterWorkspace &&
    (lower === 'signalos status' ||
      lower === '/signal-status' ||
      lower === 'signalos check' ||
      lower === '/signal-release-readiness' ||
      lower === 'signalos gates' ||
      lower === '/state:gates')
  ) {
    return [
      'You are in the starter workspace, not a product repo.',
      'Start delivery or open a product from the Projects list, then run this command there.',
    ];
  }

  const ipc = await resolveIpc(ctx);

  if (lower === 'signalos status' || lower === '/signal-status') {
    return (await ipc.signal.runAndWait('signal-status', [], 60000)) as GovernedCommandResult;
  }

  if (lower === 'signalos check' || lower === '/signal-release-readiness') {
    return (await ipc.signal.runAndWait(
      'signal-release-readiness',
      [],
      120000,
    )) as GovernedCommandResult;
  }

  if (lower === 'signalos gates' || lower === '/state:gates') {
    const gates = await ipc.signal.runAndWait('state:gates', [], 60000);
    return prettyJson(gates);
  }

  if (lower === 'git status') {
    const git = await ipc.git.status();
    return [
      `branch: ${git.branch || '(unknown)'}`,
      `clean: ${git.is_clean ? 'yes' : 'no'}`,
      `ahead/behind: ${git.ahead || 0}/${git.behind || 0}`,
      `last sync: ${git.last_sync || '(none)'}`,
      `worktrees: ${(git.worktrees || []).length}`,
    ];
  }

  if (lower === 'npm run dev') {
    const start = ctx.startPreview ?? (() => defaultStartPreview(ctx));
    return await start();
  }

  if (normalized.startsWith('/signal-')) {
    const tokens = normalized.replace(/^\//, '').split(/\s+/).filter(Boolean);
    return (await ipc.signal.runAndWait(
      tokens[0],
      tokens.slice(1),
      120000,
    )) as GovernedCommandResult;
  }

  if (lower.startsWith('signalos ')) {
    throw new Error(`Unsupported SignalOS command: ${normalized}. Type help for supported commands.`);
  }

  throw new Error(`Unsupported command: ${normalized}. Type help for supported commands.`);
}

/**
 * Detect whether a user-typed string is a SignalOS slash command
 * (`/signal-*` or `/state:*`). Used by the unified command input (1.7) to
 * route between the governed shell and natural-language chat.
 */
export function isGovernedCommand(input: string): boolean {
  const t = input.trim();
  return t.startsWith('/signal-') || t.startsWith('/state:');
}

export { HELP_LINES };
