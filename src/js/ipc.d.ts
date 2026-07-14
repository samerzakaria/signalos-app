// ipc.d.ts — ambient declarations for the plain-JS IPC layer (ipc.js).
//
// ipc.js is the Tauri-invoke wrapper. Test files cast each method to
// vi.fn() before calling, so the strict return types here are deliberately
// loose (Promise<unknown>) — the contract that matters is "the function
// exists and takes these args." Tighten individual return types when a
// TS caller starts depending on the actual shape.

// runAndWait passes args verbatim to the Python sidecar; the sidecar
// json-serializes them. Most callers pass strings, but some pass
// pre-serialized JSON strings or nested arrays — keep this loose.
export type SignalArgs = readonly unknown[];

export const signal: {
  run(command: string, args?: SignalArgs): Promise<unknown>;
  runAndWait(
    command: string,
    args?: SignalArgs,
    timeoutMs?: number,
    onId?: ((id: string) => void) | null,
  ): Promise<unknown>;
  cancelPending(message?: string): void;
};

export const engine: {
  ping(): Promise<unknown>;
  status(): Promise<unknown>;
  restart(): Promise<unknown>;
};

export const workspace: {
  set(path: string): Promise<unknown>;
  ensureDefault(name?: string, projectsRoot?: string): Promise<unknown>;
  clear(): Promise<unknown>;
  get(): Promise<unknown>;
  status(): Promise<unknown>;
  validate(target: string): Promise<unknown>;
  startWatch(): Promise<unknown>;
};

export const project: {
  artifacts(): Promise<unknown>;
  openPath(relativePath: string): Promise<unknown>;
  exportFile(kind: string, filename: string, content: string): Promise<unknown>;
  writeFiles(files: unknown, overwrite?: boolean): Promise<unknown>;
  previewFiles(files: unknown): Promise<unknown>;
  readFile(relativePath: string): Promise<unknown>;
  listDir(relativePath?: string): Promise<unknown>;
};

export const secrets: {
  upsert(name: string, value: string, filename?: string): Promise<unknown>;
  list(filename?: string): Promise<unknown>;
  reveal(name: string, filename?: string): Promise<unknown>;
  delete(name: string, filename?: string): Promise<unknown>;
  applyDiff(filename: string, envText: string, allowRemovals: boolean): Promise<unknown>;
};

export const updater: { check(channel?: string): Promise<unknown> };

export const wave: { get(): Promise<unknown> };

export const git: { status(): Promise<unknown> };

export const gates: {
  getAll(): Promise<unknown>;
  sign(gateId: number, signer: string): Promise<unknown>;
};

export const brain: {
  search(query: string): Promise<unknown>;
  add(text: string, entryType: string): Promise<unknown>;
};

export const audit: { list(limit?: number): Promise<unknown> };

export const security: { secrets(): Promise<unknown> };

export const policy: {
  get(): Promise<unknown>;
  set(policyObj: unknown): Promise<unknown>;
};

export const attachments: { analyze(files: unknown): Promise<unknown> };

export interface PanelConsultOptions {
  mode?: 'council' | 'independent';
  advisers?: string[] | string;
  models?: string[] | string;
  chair?: string;
  verifier?: string;
  red_team?: string;
  jury?: string[] | string;
  critique_rounds?: 0 | 1 | 2;
  max_workers?: number;
  request_timeout_seconds?: number;
  deadline_seconds?: number;
  system?: string;
  config?: Record<string, unknown>;
}

export const panel: {
  consult(question: string, opts?: PanelConsultOptions): Promise<unknown>;
};

export const identity: {
  set(name: string, role: string): Promise<unknown>;
  get(): Promise<unknown>;
  canSignGate(gateId: number): Promise<unknown>;
};

export const testAutomation: {
  listDebt(): Promise<unknown>;
  addDebt(kind: string, area: string, title: string, detail: string): Promise<unknown>;
  resolveDebt(title: string): Promise<unknown>;
  checkMutation(score: number, area: string): Promise<unknown>;
  checkTestFirst(testRefs: unknown): Promise<unknown>;
  readMutationScore(): Promise<unknown>;
};

export const enforcement: {
  state(): Promise<unknown>;
  precheck(stack: unknown, options?: Record<string, unknown>): Promise<unknown>;
  override(rule: string, reason: string, context?: unknown): Promise<unknown>;
  setMode(rule: string, mode: string): Promise<unknown>;
  freeze(): Promise<unknown>;
  unfreeze(): Promise<unknown>;
};

export const preview: {
  probeNode(): Promise<unknown>;
  start(stack: unknown, workspace: unknown): Promise<unknown>;
  stop(key: string): Promise<unknown>;
  list(): Promise<unknown>;
  get(key: string): Promise<unknown>;
};

export const provider: {
  list(): Promise<unknown>;
  getActive(): Promise<unknown>;
  setActive(p: string): Promise<unknown>;
  setModel(p: string, model: string): Promise<unknown>;
  setPricing(p: string, i: number, o: number): Promise<unknown>;
  getCost(): Promise<unknown>;
  recordTokens(i: number, o: number): Promise<unknown>;
  resetSession(): Promise<unknown>;
  setBudget(usd: number): Promise<unknown>;
  fetchModels(p: string, apiKey?: string): Promise<unknown>;
  test(p: string, apiKey?: string, model?: string): Promise<unknown>;
  chat(p: string, model: string | null, message: string): Promise<unknown>;
  chatStream(streamId: string, p: string, model: string | null, message: string): Promise<unknown>;
};

export const keychain: {
  store(p: string, key: string): Promise<unknown>;
  has(p: string): Promise<unknown>;
  delete(p: string): Promise<unknown>;
};

export function onWorkspaceChange(cb: (payload: unknown) => void): () => void;
export function onSidecarResponse(cb: (payload: unknown) => void): () => void;
export function onSidecarLog(cb: (payload: unknown) => void): () => void;
export function onSidecarProgress(cb: (payload: unknown) => void): () => void;
export function onPreviewEvent(cb: (payload: unknown) => void): () => void;
export function onChatToken(streamId: string, cb: (payload: unknown) => void): () => void;
export function invokeProgressContract(name: string): Promise<unknown>;
