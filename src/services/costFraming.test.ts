import { describe, it, expect } from 'vitest';
import {
  estimateHoursSaved,
  formatHoursSaved,
  parseBudgetCapUsd,
  formatUsd,
  waveValueFraming,
  MINUTES_PER_FILE,
} from './costFraming';

describe('estimateHoursSaved', () => {
  it('scales with file count using MINUTES_PER_FILE', () => {
    expect(estimateHoursSaved(0)).toBe(0);
    expect(estimateHoursSaved(12)).toBeCloseTo((12 * MINUTES_PER_FILE) / 60);
  });
  it('guards against bad input', () => {
    expect(estimateHoursSaved(-3)).toBe(0);
    expect(estimateHoursSaved(NaN)).toBe(0);
  });
});

describe('formatHoursSaved', () => {
  it('shows minutes under an hour', () => {
    expect(formatHoursSaved(0.5)).toBe('≈ 30 min');
  });
  it('shows half-hour precision under 10 hours', () => {
    expect(formatHoursSaved(1.5)).toBe('≈ 1.5 hrs');
    expect(formatHoursSaved(1)).toBe('≈ 1 hr');
  });
  it('rounds to whole hours above 10', () => {
    expect(formatHoursSaved(12.4)).toBe('≈ 12 hrs');
  });
  it('returns empty for zero/negative', () => {
    expect(formatHoursSaved(0)).toBe('');
    expect(formatHoursSaved(-1)).toBe('');
  });
});

describe('parseBudgetCapUsd', () => {
  it('parses plain and decorated numbers', () => {
    expect(parseBudgetCapUsd('50')).toBe(50);
    expect(parseBudgetCapUsd(' $1,200 ')).toBe(1200);
  });
  it('returns null for empty/invalid/non-positive', () => {
    expect(parseBudgetCapUsd('')).toBeNull();
    expect(parseBudgetCapUsd(null)).toBeNull();
    expect(parseBudgetCapUsd('abc')).toBeNull();
    expect(parseBudgetCapUsd('0')).toBeNull();
    expect(parseBudgetCapUsd('-5')).toBeNull();
  });
});

describe('formatUsd', () => {
  it('keeps extra precision for sub-cent amounts', () => {
    expect(formatUsd(0.005)).toBe('$0.0050');
  });
  it('uses two decimals otherwise', () => {
    expect(formatUsd(12.5)).toBe('$12.50');
  });
});

describe('waveValueFraming', () => {
  it('produces hours-saved and cap labels', () => {
    const f = waveValueFraming(6, '50');
    expect(f.hoursSavedLabel).toBe('≈ 2.5 hrs');
    expect(f.capLabel).toBe('of your $50.00/mo cap');
  });
  it('omits cap label when no cap set', () => {
    const f = waveValueFraming(2, '');
    expect(f.capLabel).toBe('');
    expect(f.hoursSavedLabel).not.toBe('');
  });
  it('omits hours-saved label when no files written', () => {
    const f = waveValueFraming(0, '50');
    expect(f.hoursSavedLabel).toBe('');
  });
});
