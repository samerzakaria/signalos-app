import { describe, it, expect } from 'vitest';
import { scanChatResponse } from './chatResponseGuard';

// Milestone 2-a: chat-response guard. The five acceptance tests below mirror
// the ones called out in the milestone spec. They drive both the rule
// coverage (secret / dangerous-bash / hallucinated-path) and the contract
// shape ({ clean, redactions[] }).

describe('scanChatResponse', () => {
  it('returns the original text and no redactions for a benign message', () => {
    const result = scanChatResponse('Hello world');
    expect(result.clean).toBe('Hello world');
    expect(result.redactions).toEqual([]);
  });

  it('redacts an AWS access key shape and records 1 secret redaction', () => {
    const result = scanChatResponse("here's the key: AKIAIOSFODNN7EXAMPLE");
    expect(result.clean).not.toContain('AKIAIOSFODNN7EXAMPLE');
    expect(result.clean).toContain('[REDACTED:secret]');
    expect(result.redactions).toHaveLength(1);
    expect(result.redactions[0].kind).toBe('secret');
    expect(result.redactions[0].original).toBe('AKIAIOSFODNN7EXAMPLE');
  });

  it('redacts `rm -rf /` and records 1 dangerous-bash redaction', () => {
    const result = scanChatResponse('run: rm -rf /');
    expect(result.clean).not.toMatch(/rm\s+-rf\s+\//);
    expect(result.clean).toContain('[REDACTED:dangerous-bash]');
    expect(result.redactions).toHaveLength(1);
    expect(result.redactions[0].kind).toBe('dangerous-bash');
  });

  it('flags /etc/passwd as a hallucinated-path but keeps it in clean', () => {
    const result = scanChatResponse('look at /etc/passwd');
    // Path is kept verbatim -- the user might legitimately need to discuss it.
    expect(result.clean).toContain('/etc/passwd');
    expect(result.redactions).toHaveLength(1);
    expect(result.redactions[0].kind).toBe('hallucinated-path');
    expect(result.redactions[0].original).toBe('/etc/passwd');
  });

  it('records 2 redactions for a multi-pattern message (secret + bash)', () => {
    const result = scanChatResponse('multi-pattern: AKIAIOSFODNN7EXAMPLE and rm -rf /');
    expect(result.redactions).toHaveLength(2);
    const kinds = result.redactions.map((r) => r.kind).sort();
    expect(kinds).toEqual(['dangerous-bash', 'secret']);
    expect(result.clean).not.toContain('AKIAIOSFODNN7EXAMPLE');
    expect(result.clean).toContain('[REDACTED:secret]');
    expect(result.clean).toContain('[REDACTED:dangerous-bash]');
  });
});
