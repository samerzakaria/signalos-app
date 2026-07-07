import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Voice input (#21): recording state machine with mocked getUserMedia +
// MediaRecorder, transcript insertion (never auto-send), Esc cancel, denied
// permission, and honest backend statuses (incl. no-capable-provider).

vi.mock('../js/ipc.js', () => ({
  signal: { runAndWait: vi.fn(), run: vi.fn() },
}));

const ipc = await import('../js/ipc.js');
const {
  voiceState,
  voiceError,
  toggleVoiceInput,
  stopRecording,
  insertTranscript,
  isVoiceCaptureSupported,
  __resetVoiceInputForTests,
  MAX_RECORDING_MS,
} = await import('./voiceInput');
const { chatInputValue } = await import('../state');

const runAndWait = ipc.signal.runAndWait as ReturnType<typeof vi.fn>;

// ── Mocked capture plumbing ────────────────────────────────────────────────

class FakeTrack {
  stopped = false;
  stop() { this.stopped = true; }
}

class FakeStream {
  tracks = [new FakeTrack()];
  getTracks() { return this.tracks; }
}

let recorderInstances: FakeMediaRecorder[] = [];

class FakeMediaRecorder {
  static supported = ['audio/webm;codecs=opus', 'audio/webm'];
  static isTypeSupported(mime: string) { return FakeMediaRecorder.supported.includes(mime); }
  stream: FakeStream;
  mimeType: string;
  state = 'inactive';
  started = false;
  ondataavailable: ((e: { data?: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  constructor(stream: FakeStream, options?: { mimeType?: string }) {
    this.stream = stream;
    this.mimeType = options?.mimeType || 'audio/webm';
    recorderInstances.push(this);
  }
  start() { this.started = true; this.state = 'recording'; }
  stop() {
    this.state = 'inactive';
    // Chromium delivers the final chunk then fires stop.
    this.ondataavailable?.({ data: new Blob(['fake-opus-bytes'], { type: this.mimeType }) });
    this.onstop?.();
  }
}

let getUserMedia: ReturnType<typeof vi.fn>;
let lastStream: FakeStream;

function installCaptureMocks() {
  lastStream = new FakeStream();
  getUserMedia = vi.fn(async () => lastStream);
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia },
  });
  (globalThis as any).MediaRecorder = FakeMediaRecorder;
}

function removeCaptureMocks() {
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: undefined });
  delete (globalThis as any).MediaRecorder;
}

async function settle() {
  // FileReader + the IPC promise chain need macrotask turns. Under load
  // jsdom's FileReader can take more than a fixed couple of turns; returning
  // early leaks the pending chain into the NEXT test, where it inserts a
  // stale transcript (observed as a flaky doubled composer text / extra
  // runAndWait call). handleStop's finally block always returns voiceState
  // to 'idle', so wait for that — bounded so a genuine hang still fails.
  for (let i = 0; i < 200 && voiceState.value !== 'idle'; i++) {
    await new Promise((r) => setTimeout(r, 5));
  }
  await new Promise((r) => setTimeout(r, 0));
}

beforeEach(() => {
  vi.useRealTimers();
  runAndWait.mockReset();
  __resetVoiceInputForTests();
  chatInputValue.value = '';
  recorderInstances = [];
  installCaptureMocks();
});

afterEach(() => {
  removeCaptureMocks();
});

describe('recording state machine', () => {
  it('idle → recording on first toggle, with a visible recording state', async () => {
    await toggleVoiceInput();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(voiceState.value).toBe('recording');
    expect(recorderInstances).toHaveLength(1);
    expect(recorderInstances[0].started).toBe(true);
    expect(recorderInstances[0].mimeType).toBe('audio/webm;codecs=opus');
  });

  it('recording → transcribing → idle on second toggle; transcript inserted, never auto-sent', async () => {
    runAndWait.mockResolvedValue({ status: 'ok', text: 'build me a landing page' });
    const sendMsg = vi.fn();
    (window as any).sendMsg = sendMsg;

    await toggleVoiceInput();
    await toggleVoiceInput(); // click again to stop
    expect(voiceState.value).toBe('transcribing');
    await settle();

    expect(runAndWait).toHaveBeenCalledTimes(1);
    const [command, args, timeout] = runAndWait.mock.calls[0];
    expect(command).toBe('voice:transcribe');
    const payload = JSON.parse(args[0]);
    expect(typeof payload.audio_b64).toBe('string');
    expect(payload.audio_b64.length).toBeGreaterThan(0);
    expect(payload.mime).toContain('audio/webm');
    expect(timeout).toEqual(expect.any(Number));

    expect(chatInputValue.value).toBe('build me a landing page');
    expect(sendMsg).not.toHaveBeenCalled(); // inserted, NOT sent
    expect(voiceState.value).toBe('idle');
    expect(voiceError.value).toBeNull();
    expect(lastStream.tracks[0].stopped).toBe(true); // mic released
    delete (window as any).sendMsg;
  });

  it('appends to existing composer text with a separating space', async () => {
    chatInputValue.value = 'please ';
    runAndWait.mockResolvedValue({ status: 'ok', text: 'add dark mode' });
    await toggleVoiceInput();
    await toggleVoiceInput();
    await settle();
    expect(chatInputValue.value).toBe('please add dark mode');
  });

  it('Esc cancels the recording: audio discarded, no IPC call', async () => {
    await toggleVoiceInput();
    expect(voiceState.value).toBe('recording');

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await settle();

    expect(voiceState.value).toBe('idle');
    expect(runAndWait).not.toHaveBeenCalled();
    expect(lastStream.tracks[0].stopped).toBe(true);
  });

  it('auto-stops at the 60s cap', async () => {
    vi.useFakeTimers();
    runAndWait.mockResolvedValue({ status: 'ok', text: 'capped' });
    await toggleVoiceInput();
    expect(voiceState.value).toBe('recording');
    vi.advanceTimersByTime(MAX_RECORDING_MS + 1);
    expect(voiceState.value).toBe('transcribing');
    // Flush the FileReader/IPC chain (jsdom's FileReader schedules through
    // the mocked timers) before switching back to real time.
    await vi.runAllTimersAsync();
    vi.useRealTimers();
    await settle();
    expect(chatInputValue.value).toBe('capped');
  });

  it('toggle during transcription is ignored (no second recording)', async () => {
    let resolveIpc: (v: unknown) => void = () => {};
    runAndWait.mockReturnValue(new Promise((r) => { resolveIpc = r; }));
    await toggleVoiceInput();
    await toggleVoiceInput(); // stop → transcribing (IPC pending)
    await new Promise((r) => setTimeout(r, 0));
    expect(voiceState.value).toBe('transcribing');

    await toggleVoiceInput(); // ignored
    expect(getUserMedia).toHaveBeenCalledTimes(1);

    resolveIpc({ status: 'ok', text: 'done' });
    await settle();
    expect(voiceState.value).toBe('idle');
  });
});

describe('permission and capability failures', () => {
  it('mic permission denied → clear message, state stays idle', async () => {
    const denied = new DOMException('Permission denied', 'NotAllowedError');
    getUserMedia.mockRejectedValue(denied);

    await toggleVoiceInput();

    expect(voiceState.value).toBe('idle');
    expect(voiceError.value).toContain('Microphone access was denied');
    expect(runAndWait).not.toHaveBeenCalled();
  });

  it('no microphone device → honest message', async () => {
    getUserMedia.mockRejectedValue(new DOMException('none', 'NotFoundError'));
    await toggleVoiceInput();
    expect(voiceError.value).toContain('No microphone was found');
  });

  it('runtime without capture APIs degrades truthfully', async () => {
    removeCaptureMocks();
    expect(isVoiceCaptureSupported()).toBe(false);
    await toggleVoiceInput();
    expect(voiceState.value).toBe('idle');
    expect(voiceError.value).toContain("Voice capture isn't available in this app runtime");
    expect(runAndWait).not.toHaveBeenCalled();
  });
});

describe('backend statuses', () => {
  async function recordAndStop() {
    await toggleVoiceInput();
    await toggleVoiceInput();
    await settle();
  }

  it('no-capable-provider is rendered honestly', async () => {
    runAndWait.mockResolvedValue({
      status: 'no-capable-provider',
      error: 'Voice transcription needs an OpenAI or Groq key.',
    });
    await recordAndStop();
    expect(voiceError.value).toBe('Voice transcription needs an OpenAI or Groq key.');
    expect(chatInputValue.value).toBe('');
    expect(voiceState.value).toBe('idle');
  });

  it('provider-error passes the backend message through', async () => {
    runAndWait.mockResolvedValue({
      status: 'provider-error',
      error: 'openai transcription failed (HTTP 429): Rate limit reached',
    });
    await recordAndStop();
    expect(voiceError.value).toContain('HTTP 429');
  });

  it('transport rejection is surfaced, state returns to idle', async () => {
    runAndWait.mockRejectedValue(new Error('engine stopped'));
    await recordAndStop();
    expect(voiceError.value).toContain('engine stopped');
    expect(voiceState.value).toBe('idle');
  });

  it('ok without text is treated as a failure, composer untouched', async () => {
    runAndWait.mockResolvedValue({ status: 'ok', text: '   ' });
    await recordAndStop();
    expect(chatInputValue.value).toBe('');
    expect(voiceError.value).toBeTruthy();
  });
});

describe('insertTranscript / stopRecording edge cases', () => {
  it('insertTranscript ignores empty text', () => {
    chatInputValue.value = 'keep me';
    insertTranscript('   ');
    expect(chatInputValue.value).toBe('keep me');
  });

  it('stopRecording is a no-op when idle', () => {
    expect(() => stopRecording(false)).not.toThrow();
    expect(voiceState.value).toBe('idle');
  });
});
