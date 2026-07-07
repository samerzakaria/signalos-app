// util.d.ts — ambient declarations for the plain-JS util helpers (util.js),
// following the codebase convention (see ipc.d.ts): TS callers that import
// from a *.js module need a colocated .d.ts.

export function esc(s: unknown): string;
export function showError(msg: string): void;
export function showWarning(msg: string): void;
export function errorMessage(error: unknown, fallback?: string): string;
export function providerConnectionMessage(error: unknown, provider?: string): string;
export function isProviderAuthFailure(error: unknown): boolean;
export function formatTs(ts: unknown): string;
