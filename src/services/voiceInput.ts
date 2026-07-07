// voiceInput.ts — real voice input for the composer (#21).
//
// WebView2 exposes NO SpeechRecognition API, so recognition cannot happen in
// the page. Instead: capture mic audio with getUserMedia + MediaRecorder
// (webm/opus), base64 the blob, and send it to the Python sidecar's
// `voice:transcribe` IPC command, which routes it to a transcription-capable
// provider (OpenAI whisper-1 / Groq whisper-large-v3). On success the
// transcript is INSERTED into the composer — never auto-sent; the user reads
// and sends it themselves.
//
// State machine (voiceState):
//   idle → recording   (mic click; getUserMedia + recorder.start)
//   recording → transcribing  (mic click again, or the 60s cap)
//   recording → idle   (Esc — cancel, audio discarded)
//   transcribing → idle (transcript inserted, or error surfaced)
//
// Privacy: audio lives only in memory here and in the sidecar request; it is
// never logged or persisted on either side.

import { signal } from '@preact/signals';
import * as ipc from '../js/ipc.js';
import { chatInputValue } from '../state';
import { showError } from '../js/util.js';

export type VoiceState = 'idle' | 'recording' | 'transcribing';

export const voiceState = signal<VoiceState>('idle');
export const voiceError = signal<string | null>(null);

/** Hard recording cap — matches the backend's expectation of short clips. */
export const MAX_RECORDING_MS = 60_000;
/** Frontend size guard mirroring the backend's ~10MB cap. */
export const MAX_AUDIO_BYTES = 10 * 1024 * 1024;

const TRANSCRIBE_TIMEOUT_MS = 90_000;

// Preference order for the recorder container. WebView2 (Chromium) supports
// webm/opus; the fallbacks cover other engines in dev.
const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
];

interface RecorderLike {
  mimeType?: string;
  state?: string;
  ondataavailable: ((e: { data?: Blob }) => void) | null;
  onstop: (() => void) | null;
  onerror: ((e: unknown) => void) | null;
  start(timesliceMs?: number): void;
  stop(): void;
}

interface MediaRecorderCtor {
  new (stream: MediaStream, options?: { mimeType?: string }): RecorderLike;
  isTypeSupported?: (mime: string) => boolean;
}

let recorder: RecorderLike | null = null;
let stream: MediaStream | null = null;
let chunks: Blob[] = [];
let cancelled = false;
let maxTimer: ReturnType<typeof setTimeout> | null = null;
let escListener: ((e: KeyboardEvent) => void) | null = null;

function mediaRecorderCtor(): MediaRecorderCtor | null {
  const ctor = (globalThis as { MediaRecorder?: unknown }).MediaRecorder;
  return typeof ctor === 'function' ? (ctor as unknown as MediaRecorderCtor) : null;
}

/** Truthful capability probe — WebView2 builds without mic plumbing return false. */
export function isVoiceCaptureSupported(): boolean {
  return Boolean(
    typeof navigator !== 'undefined' &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function' &&
    mediaRecorderCtor(),
  );
}

function pickMimeType(): string {
  const ctor = mediaRecorderCtor();
  const supported = ctor?.isTypeSupported;
  if (typeof supported !== 'function') return '';
  for (const mime of MIME_CANDIDATES) {
    try {
      if (supported.call(ctor, mime)) return mime;
    } catch { /* probe failure → try next */ }
  }
  return '';
}

function fail(message: string): void {
  voiceError.value = message;
  try { showError(message); } catch { /* toast is best-effort */ }
}

function releaseStream(): void {
  try {
    stream?.getTracks?.().forEach((t) => { try { t.stop(); } catch { /* already stopped */ } });
  } catch { /* defensive */ }
  stream = null;
}

function clearRecordingHooks(): void {
  if (maxTimer !== null) { clearTimeout(maxTimer); maxTimer = null; }
  if (escListener) {
    try { document.removeEventListener('keydown', escListener); } catch { /* jsdom-safe */ }
    escListener = null;
  }
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      // readAsDataURL yields "data:<mime>;base64,<payload>".
      const comma = result.indexOf(',');
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error || new Error('Could not read the recording.'));
    reader.readAsDataURL(blob);
  });
}

/** Insert the transcript into the composer. Never auto-sends. */
export function insertTranscript(text: string): void {
  const transcript = (text || '').trim();
  if (!transcript) return;
  const existing = chatInputValue.value || '';
  chatInputValue.value = existing
    ? existing.replace(/\s+$/, '') + ' ' + transcript
    : transcript;
  try { (document.getElementById('chatInput') as HTMLInputElement | null)?.focus(); } catch { /* focus is cosmetic */ }
}

function permissionDeniedMessage(): string {
  return 'Microphone access was denied. Allow microphone access for Foundry '
    + '(Windows Settings → Privacy & security → Microphone), then try again.';
}

async function startRecording(): Promise<void> {
  voiceError.value = null;
  if (!isVoiceCaptureSupported()) {
    // Honest degradation: the runtime genuinely has no capture API.
    fail("Voice capture isn't available in this app runtime (the embedded "
      + 'WebView exposes no microphone API). Type your message instead.');
    return;
  }

  let mediaStream: MediaStream;
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    const name = (e as { name?: string } | null)?.name || '';
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError' || name === 'SecurityError') {
      fail(permissionDeniedMessage());
    } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
      fail('No microphone was found on this device.');
    } else {
      fail('Could not start the microphone: ' + (e instanceof Error ? e.message : String(e)));
    }
    return;
  }

  const Ctor = mediaRecorderCtor();
  if (!Ctor) {
    releaseStreamOf(mediaStream);
    fail("Voice capture isn't available in this app runtime (MediaRecorder missing).");
    return;
  }

  const mime = pickMimeType();
  let rec: RecorderLike;
  try {
    rec = mime ? new Ctor(mediaStream, { mimeType: mime }) : new Ctor(mediaStream);
  } catch (e) {
    releaseStreamOf(mediaStream);
    fail('Could not start the recorder: ' + (e instanceof Error ? e.message : String(e)));
    return;
  }

  stream = mediaStream;
  recorder = rec;
  chunks = [];
  cancelled = false;

  rec.ondataavailable = (e) => {
    if (e && e.data && e.data.size > 0) chunks.push(e.data);
  };
  rec.onerror = (e) => {
    const detail = (e as { error?: { message?: string } } | null)?.error?.message;
    clearRecordingHooks();
    releaseStream();
    recorder = null;
    voiceState.value = 'idle';
    fail('Recording failed' + (detail ? ': ' + detail : '.'));
  };
  rec.onstop = () => { void handleStop(); };

  try {
    rec.start();
  } catch (e) {
    releaseStream();
    recorder = null;
    fail('Could not start recording: ' + (e instanceof Error ? e.message : String(e)));
    return;
  }

  voiceState.value = 'recording';
  maxTimer = setTimeout(() => stopRecording(false), MAX_RECORDING_MS);
  escListener = (e: KeyboardEvent) => {
    if (e.key === 'Escape') stopRecording(true);
  };
  try { document.addEventListener('keydown', escListener); } catch { /* jsdom-safe */ }
}

function releaseStreamOf(s: MediaStream): void {
  try { s.getTracks?.().forEach((t) => { try { t.stop(); } catch { /* noop */ } }); } catch { /* noop */ }
}

/** Stop the active recording. cancel=true discards the audio (Esc). */
export function stopRecording(cancel: boolean): void {
  if (voiceState.value !== 'recording' || !recorder) return;
  cancelled = cancel;
  clearRecordingHooks();
  voiceState.value = cancel ? 'idle' : 'transcribing';
  try {
    recorder.stop(); // onstop → handleStop()
  } catch {
    releaseStream();
    recorder = null;
    voiceState.value = 'idle';
  }
}

async function handleStop(): Promise<void> {
  const recordedChunks = chunks;
  const wasCancelled = cancelled;
  const mime = recorder?.mimeType || pickMimeType() || 'audio/webm';
  chunks = [];
  releaseStream();
  recorder = null;

  if (wasCancelled) {
    voiceState.value = 'idle';
    return;
  }

  try {
    const blob = new Blob(recordedChunks, { type: mime });
    if (!blob.size) {
      fail('Nothing was recorded — check that your microphone is working.');
      return;
    }
    if (blob.size > MAX_AUDIO_BYTES) {
      fail('The recording is too large to transcribe (over 10 MB). Record a shorter clip.');
      return;
    }
    const audioB64 = await blobToBase64(blob);
    const raw = await ipc.signal.runAndWait(
      'voice:transcribe',
      [JSON.stringify({ audio_b64: audioB64, mime })],
      TRANSCRIBE_TIMEOUT_MS,
    );
    const res = (raw && typeof raw === 'object' ? raw : {}) as {
      status?: string;
      text?: string;
      error?: string;
    };
    if (res.status === 'ok' && typeof res.text === 'string' && res.text.trim()) {
      insertTranscript(res.text);
      voiceError.value = null;
    } else {
      // Render the backend's message honestly (incl. no-capable-provider).
      fail(res.error || `Transcription failed (${res.status || 'no response'}).`);
    }
  } catch (e) {
    fail('Transcription failed: ' + (e instanceof Error ? e.message : String(e)));
  } finally {
    voiceState.value = 'idle';
  }
}

/**
 * The mic-button entry point (bound to window.voiceInput by app-v2.js).
 * Click to record, click again to stop + transcribe; Esc cancels.
 */
export async function toggleVoiceInput(): Promise<void> {
  if (voiceState.value === 'recording') {
    stopRecording(false);
    return;
  }
  if (voiceState.value === 'transcribing') return; // in flight — ignore
  await startRecording();
}

/** Test seam: reset module state between tests. */
export function __resetVoiceInputForTests(): void {
  clearRecordingHooks();
  releaseStream();
  recorder = null;
  chunks = [];
  cancelled = false;
  voiceState.value = 'idle';
  voiceError.value = null;
}
