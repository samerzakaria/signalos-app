import { describe, expect, it } from 'vitest';
import {
  PHASES,
  phaseLabel,
  deliveryPercent,
  businessStageForPhase,
  safeProductName,
  deriveProductName,
  isTechnicalQuestion,
  applyProgressEvent,
} from './deliveryFlow';

describe('deliveryFlow', () => {
  it('normalises raw phase identifiers (incl. past tense) to labels', () => {
    expect(phaseLabel('scaffolded')).toBe('Scaffold');
    expect(phaseLabel('validated')).toBe('Validation');
    expect(phaseLabel('bogus')).toBeNull();
  });

  it('computes completion percentage', () => {
    expect(deliveryPercent(0)).toBe(0);
    expect(deliveryPercent(PHASES.length)).toBe(100);
    expect(deliveryPercent(PHASES.length * 2)).toBe(100);
  });

  it('maps phases to business stages', () => {
    expect(businessStageForPhase('intent')).toBe('Brief');
    expect(businessStageForPhase('validated')).toBe('Validate');
    expect(businessStageForPhase('closeout')).toBe('Handoff');
  });

  it('derives a safe product name from a prompt', () => {
    expect(deriveProductName({ prompt: 'I want to build a task management system' }))
      .toBe('task-management-system');
    expect(safeProductName('My App')).toBe('My-App');
    expect(deriveProductName({ name: '   ' })).toBe('NewProduct');
  });

  it('flags technical vs business questions', () => {
    expect(isTechnicalQuestion('Which framework, React or Vue?')).toBe(true);
    expect(isTechnicalQuestion('Who are the users?')).toBe(false);
  });

  it('folds progress events into completed phases and advances current', () => {
    let s = { completedPhases: [] as string[], currentPhase: null as string | null };
    s = applyProgressEvent(s, { phase: 'intent', state: 'done' });
    expect(s.completedPhases).toEqual(['Intent']);
    expect(s.currentPhase).toBe('Scaffold');
    s = applyProgressEvent(s, { phase: 'design', state: 'running' });
    expect(s.currentPhase).toBe('Design');
  });
});
