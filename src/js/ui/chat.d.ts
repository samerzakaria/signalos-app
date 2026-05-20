// chat.d.ts — ambient declarations for chat.js.
//
// chat.js is the main composer module; importing it has side effects
// (registers window.sendMsg, window.composerInput, etc). The
// freeze-consolidation test imports it for those side effects.

export function loadBuild(): Promise<void>;
export function addUserBubble(text: string): void;
export function addAIBubble(text: string): void;
export function appendStreamToken(streamId: string, delta: string): void;
export function finaliseStream(streamId: string): Promise<void>;
export function showStreamError(streamId: string, msg: string): void;
