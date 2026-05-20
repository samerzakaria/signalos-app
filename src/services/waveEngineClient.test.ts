import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the ipc module the client calls into. vi.mock hoists, so the
// fake must live at the top level of the factory.
vi.mock('../js/ipc.js', () => ({
  signal: {
    runAndWait: vi.fn(),
  },
}));

import {
  begin,
  reply,
  resolveScopeDrift,
  translateExternal,
  requestViolation,
  confirmViolation,
  g5Handoff,
  tryBegin,
} from './waveEngineClient';
import * as ipc from '../js/ipc.js';

const runAndWait = ipc.signal.runAndWait as ReturnType<typeof vi.fn>;

describe('waveEngineClient', () => {
  beforeEach(() => {
    runAndWait.mockReset();
  });

  describe('begin', () => {
    it('passes wave:begin with the user request', async () => {
      runAndWait.mockResolvedValue({
        action: 'fire-agent-G0',
        current_gate: 'G0',
        system_bubble: { kind: 'reroute', gate: 'G0', text: 'fire G0' },
      });
      const result = await begin('Build a todo app');
      expect(runAndWait).toHaveBeenCalledWith('wave:begin', ['Build a todo app'], 8000);
      expect(result.action).toBe('fire-agent-G0');
      expect(result.current_gate).toBe('G0');
    });
  });

  describe('reply', () => {
    it('passes both user_reply and current_gate', async () => {
      runAndWait.mockResolvedValue({
        action: 'fire-agent-G1',
        signed_gate: 'G0',
        auto_signed: true,
      });
      const result = await reply('yes', 'G0');
      expect(runAndWait).toHaveBeenCalledWith('wave:reply', ['yes', 'G0'], 8000);
      expect(result.auto_signed).toBe(true);
      expect(result.signed_gate).toBe('G0');
    });
  });

  describe('resolveScopeDrift', () => {
    it('passes the user request and the 4-way choice', async () => {
      runAndWait.mockResolvedValue({ action: 'fire-agent-G0', mode: 'amend' });
      const result = await resolveScopeDrift('different request', 'a');
      expect(runAndWait).toHaveBeenCalledWith(
        'wave:scope-drift-resolve', ['different request', 'a'], 8000,
      );
      expect(result.action).toBe('fire-agent-G0');
      expect(result.mode).toBe('amend');
    });
  });

  describe('translateExternal', () => {
    it('omits gate when not provided', async () => {
      runAndWait.mockResolvedValue({
        translation: { supported: true, format: 'markdown', text: 'body' },
        gate: null,
        system_bubble: { kind: 'reroute', text: 'translating' },
      });
      const result = await translateExternal('belief.md');
      expect(runAndWait).toHaveBeenCalledWith(
        'wave:translate-external', ['belief.md'], 8000,
      );
      expect(result.translation.supported).toBe(true);
    });

    it('passes gate when provided', async () => {
      runAndWait.mockResolvedValue({
        translation: { supported: true, format: 'markdown', text: 'body' },
        gate: 'G1',
        system_bubble: { kind: 'reroute', text: 'translating' },
      });
      await translateExternal('belief.md', 'G1');
      expect(runAndWait).toHaveBeenCalledWith(
        'wave:translate-external', ['belief.md', 'G1'], 8000,
      );
    });
  });

  describe('requestViolation', () => {
    it('JSON-stringifies the payload', async () => {
      runAndWait.mockResolvedValue({
        prompt: {
          category: 'D:override-with-audit',
          violation_kind: 'code-review',
          gate: 'G4',
          findings: ['x'],
          options: ['fix-now', 'defer', 'override-with-log'],
          text: '...',
          prompt_id: 'violation:code-review',
        },
        system_bubble: { kind: 'reroute', text: 'violation prompt' },
      });
      await requestViolation({
        violation_kind: 'code-review',
        findings: ['x'],
        gate: 'G4',
      });
      const [cmd, args] = runAndWait.mock.calls[0];
      expect(cmd).toBe('wave:violation-request');
      const parsed = JSON.parse(args[0] as string);
      expect(parsed.violation_kind).toBe('code-review');
      expect(parsed.gate).toBe('G4');
      expect(parsed.findings).toEqual(['x']);
    });
  });

  describe('confirmViolation', () => {
    it('JSON-stringifies the payload including choice + user_reply', async () => {
      runAndWait.mockResolvedValue({
        audit_entry: {
          action: 'violation:code-review:override-with-log',
          violation_kind: 'code-review',
          gate: 'G4',
          choice: 'override-with-log',
          evidence: 'risk accepted',
          findings: [],
        },
        system_bubble: { kind: 'sign-recorded', text: 'Override recorded' },
      });
      await confirmViolation({
        violation_kind: 'code-review',
        choice: 'c',
        user_reply: 'risk accepted',
        gate: 'G4',
      });
      const [cmd, args] = runAndWait.mock.calls[0];
      expect(cmd).toBe('wave:violation-confirm');
      const parsed = JSON.parse(args[0] as string);
      expect(parsed.choice).toBe('c');
      expect(parsed.user_reply).toBe('risk accepted');
      expect(parsed.gate).toBe('G4');
    });
  });

  describe('g5Handoff', () => {
    it('passes wave_id and summary-as-JSON', async () => {
      runAndWait.mockResolvedValue({
        commit_outcome: { status: 'skipped', reason: 'clean-tree' },
        system_bubble: { kind: 'complete', gate: 'G5', text: 'wave complete' },
      });
      await g5Handoff('W7.1', { tasks: [], completed: 0 });
      const [cmd, args] = runAndWait.mock.calls[0];
      expect(cmd).toBe('wave:g5-handoff');
      expect(args[0]).toBe('W7.1');
      expect(JSON.parse(args[1] as string)).toEqual({ tasks: [], completed: 0 });
    });
  });

  describe('tryBegin', () => {
    it('returns the begin result on success', async () => {
      runAndWait.mockResolvedValue({
        action: 'fire-agent-G0',
        current_gate: 'G0',
        system_bubble: { kind: 'reroute', text: 'fire G0' },
      });
      const result = await tryBegin('build a todo');
      expect(result).not.toBeNull();
      expect(result?.action).toBe('fire-agent-G0');
    });

    it('returns null when the IPC call rejects', async () => {
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      runAndWait.mockRejectedValue(new Error('sidecar down'));
      const result = await tryBegin('build a todo');
      expect(result).toBeNull();
      warnSpy.mockRestore();
    });
  });
});
