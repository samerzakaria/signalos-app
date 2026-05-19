<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# PR Checklist — 
# Complete before creating any PR. Delivery Agent must fill every field.
# Leave no field blank. "N/A" is acceptable only with a reason.

## Ticket reference
- Backlog item ID: _______________
- Wave: _______________
- Branch: _______________

## TDD compliance
- [ ] All acceptance criteria from `wave-{N}-acceptance-criteria.md` have a corresponding test
- [ ] Tests were written BEFORE implementation (RED → GREEN → REFACTOR)
- [ ] All tests pass in CI: _______________  (paste CI link or result)
- [ ] Test coverage delta: +___% (no regressions)

## Spec compliance
- [ ] Implementation matches `governance/plans/wave-{N}-plan.md`
- [ ] No scope added beyond the signed Expectation Map
- [ ] DEFER comments added for any deferred ideas (not deleted, not built)

## Code quality
- [ ] No linting errors
- [ ] No TypeScript / type errors (if applicable)
- [ ] No hardcoded credentials or secrets
- [ ] Security surface reviewed (auth, input validation, data exposure)

## Documentation
- [ ] Inline docs updated for any changed public API
- [ ] Decision DNA updated if an architectural choice was made: _______________
- [ ] Prompt Library Gotchas updated if AI made a project-specific mistake: _______________

## Integration readiness
- [ ] No conflicts with other open worktrees
- [ ] DB migrations tested dry-run (if schema changed)
- [ ] Environment variables documented in Soul Document (if new ones added)

## Sign-off
AI agent: _______________
Date: _______________
Gate tier applied (T1/T2/T3): _______________
