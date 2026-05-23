export {};

type TauriInvoke = <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

interface TauriDialog {
  open?: (opts?: {
    directory?: boolean;
    multiple?: boolean;
    title?: string;
    defaultPath?: string;
  }) => Promise<string | string[] | null>;
}

interface TauriFs {
  mkdir?: (path: string, opts?: { recursive?: boolean }) => Promise<void>;
}

declare global {
  interface Window {
    __TAURI__?: {
      core?: { invoke?: TauriInvoke };
      invoke?: TauriInvoke;
      dialog?: TauriDialog;
      fs?: TauriFs;
      shell?: { open?: (url: string) => Promise<void> };
    };
    pickWorkspaceFolder: () => Promise<void>;
    ensureWorkspaceFolder: (path: string) => Promise<void>;
    initWorkspace: (path: string, options?: { name?: string; profile?: string; strict?: boolean }) => Promise<void>;
    createSignalosProject: (
      path: string,
      name: string,
      profile?: string,
    ) => Promise<{ governance: { filled: string[]; signed: boolean }; status: unknown | null }>;
    instantiateGovernanceAndSignG0: () => Promise<{ filled: string[]; signed: boolean }>;
    approvePlan: (bubbleId: string) => Promise<void>;
    cancelWave: (bubbleId: string) => void;
    retryTask: (bubbleId: string, taskId: string) => Promise<void>;
    rollbackWave: (bubbleId: string) => Promise<void>;
    showSignForm: () => void;
    previewRun: () => Promise<void>;
    previewStop: () => Promise<void>;
    previewReload: () => void;
    addBrainEntry: () => void;
    attachFile: () => void;
    changeModel: () => void;
    changeProvider: () => void;
    changeStack: () => void;
    checkForUpdates: () => void;
    closeAddSecret: (e: MouseEvent) => void;
    closeModal: (id: string) => void;
    closeNewProject: (e: MouseEvent) => void;
    composerInput: (e: Event) => void;
    composerKey: (e: KeyboardEvent) => void;
    confirmOverride: () => void;
    copySecret: (name: string) => void;
    createProject: () => Promise<void> | void;
    cycleActivity: (el: EventTarget | null) => void;
    deleteSecret: (name: string) => void;
    exitApp: (save: boolean) => void;
    exportHandoff: (btn?: EventTarget | null) => void;
    exportReport: (btn?: EventTarget | null) => void;
    filterBrain: (_el: EventTarget | null, type: string) => void;
    finishOnboarding: () => void;
    forgetWorkspace: () => void;
    freezeWave: () => void;
    nextStep: () => void;
    openAddSecret: () => void;
    openExit: () => void;
    openExternal: () => void;
    openFile: (path: string) => void;
    openGate: () => void;
    openModal: (id: string) => void;
    openNewProject: () => void;
    switchWorkspace: (path: string) => Promise<void>;
    openOverride: () => void;
    openSearch: () => void;
    prevStep: () => void;
    refreshPreview: () => void;
    replaceApiKey: () => void;
    resetSessionCost: () => void;
    restartEngine: () => void;
    runCheck: (el: EventTarget | null) => void;
    runCmd: (cmd: string) => void;
    saveBudget: () => void;
    saveIdentity: () => void;
    saveSecret: () => void;
    selectAI: (ai: string) => void;
    selectProv: (provider: string, model: string, keyLabel: string) => void;
    sendChip: (text: string) => void;
    sendMsg: () => void;
    shareProject: () => void;
    showFileWriteToast: (files: unknown) => void;
    showNotifications: () => void;
    showSignForm: () => void;
    switchDevice: (mode: string) => void;
    switchSbTab: (tab: string) => void;
    switchTab: (tab: string) => void;
    termChip: (cmd: string) => void;
    termKey: (e: KeyboardEvent) => void;
    termSubmit: (val?: string) => void;
    testEngine: () => void;
    toggleEnfPopover: () => void;
    toggleKey: () => void;
    toggleMoreProvs: () => void;
    toggleSecret: (name: string) => void;
    unfreezeWave: () => void;
    voiceInput: () => void;
  }
}
