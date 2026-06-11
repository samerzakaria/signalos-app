import { describe, it, expect } from 'vitest';
import {
  VISUAL_EDIT_MESSAGE,
  parseSelectMessage,
  describeTarget,
  buildEditInstruction,
  PICKER_SNIPPET,
} from './visualEdit';

describe('parseSelectMessage', () => {
  it('accepts a well-formed select message', () => {
    const t = parseSelectMessage({
      type: VISUAL_EDIT_MESSAGE,
      target: { selector: 'button.cta', tag: 'BUTTON', text: 'Buy now', classes: ['cta'] },
    });
    expect(t).toEqual({ selector: 'button.cta', tag: 'button', text: 'Buy now', classes: ['cta'] });
  });
  it('rejects wrong type / missing selector / non-objects', () => {
    expect(parseSelectMessage(null)).toBeNull();
    expect(parseSelectMessage({ type: 'other', target: { selector: 'x' } })).toBeNull();
    expect(parseSelectMessage({ type: VISUAL_EDIT_MESSAGE, target: {} })).toBeNull();
    expect(parseSelectMessage('string')).toBeNull();
  });
  it('clamps overly long selector/text', () => {
    const t = parseSelectMessage({
      type: VISUAL_EDIT_MESSAGE,
      target: { selector: 'a'.repeat(1000), text: 'b'.repeat(1000) },
    })!;
    expect(t.selector.length).toBe(400);
    expect((t.text || '').length).toBe(120);
  });
});

describe('describeTarget', () => {
  it('reads as plain language with tag, text, selector', () => {
    const d = describeTarget({ selector: 'button.cta', tag: 'button', text: 'Buy now' });
    expect(d).toContain('<button>');
    expect(d).toContain('"Buy now"');
    expect(d).toContain('button.cta');
  });
});

describe('buildEditInstruction', () => {
  it('produces a scoped instruction', () => {
    const instr = buildEditInstruction(
      { selector: 'button.cta', tag: 'button', text: 'Buy now' },
      'make this red',
    );
    expect(instr).toContain('make this red');
    expect(instr).toContain('button.cta');
    expect(instr.toLowerCase()).toContain('only this element');
  });
  it('returns empty for a blank phrase', () => {
    expect(buildEditInstruction({ selector: 'x', tag: 'div' }, '   ')).toBe('');
  });
});

describe('PICKER_SNIPPET', () => {
  it('is a self-guarding IIFE that posts the select message', () => {
    expect(PICKER_SNIPPET).toContain('__foundryPicker');
    expect(PICKER_SNIPPET).toContain('foundry:select');
    expect(PICKER_SNIPPET).toContain('parent.postMessage');
  });
});
