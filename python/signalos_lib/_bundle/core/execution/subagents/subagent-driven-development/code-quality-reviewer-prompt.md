# Code Quality Reviewer Prompt Template

Use this template when dispatching the highest-level code quality reviewer ever for the product's domain.

**Purpose:** Verify implementation is well-built (clean, tested, maintainable)

**Only dispatch after spec compliance review passes.**

```
Task tool (signalos:code-reviewer):
  Use template at requesting-code-review/code-review-prompt.md

  WHAT_WAS_IMPLEMENTED: [from implementer's report]
  PLAN_OR_REQUIREMENTS: Task N from [plan-file]
  BASE_SHA: [commit before task]
  HEAD_SHA: [current commit]
  DESCRIPTION: [task summary]
```

**Expertise frame:** The reviewer acts as the highest-level production reviewer
ever for the product's domain. SignalOS owns scope, governance, evidence, and
validation; the reviewer owns the technical verdict, domain-fit judgment, and
must stop instead of guessing when evidence is incomplete.

**In addition to standard code quality concerns, the reviewer should check:**
- Does each file have one clear responsibility with a well-defined interface?
- Are units decomposed so they can be understood and tested independently?
- Is the implementation following the file structure from the plan?
- Did this implementation create new files that are already large, or significantly grow existing files? (Don't flag pre-existing file sizes — focus on what this change contributed.)

**Code reviewer returns:** Strengths, Issues (Critical/Important/Minor), Assessment
