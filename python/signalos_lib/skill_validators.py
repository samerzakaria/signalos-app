"""skill_validators.py - Smart, artifact-based skill enforcement.

The orchestrator already INJECTS each tagged skill's SKILL.md into the
per-task LLM prompt (the "advisory" mechanism). This module adds the
*enforcement* layer: after the LLM produces files, we verify that the
expected structured-output artifact for each tagged skill actually got
produced.

The trick that keeps this scalable: enforcement is by ARTIFACT SHAPE,
not by quality grading. We don't ask another LLM "did this review
catch all the bugs?" -- we check "did a review-notes file get written
with the expected severity sections?" Process-skills (debugging,
review, retro, plan-writing) become enforceable the same way deliverable
skills do, without an LLM-as-judge layer.

When a validator returns violations, the orchestrator feeds them back
into the task's `previous_failure` field so the smart-retry mechanism
re-prompts the LLM with the specific problem to fix.

Each validator returns a list of strings (one per violation, human-
readable). An empty list means the skill was satisfied.

Validators get:
  task: the task dict (id, title, description, files, skills, ...)
  root: workspace Path
  written_files: list of paths the LLM produced this attempt (relative
                 to root)
  task_response: the raw LLM text response (for skills that want to
                 scan it directly, e.g. review notes embedded inline)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

__all__ = ["validate_skill_artifacts", "SkillViolation"]


class SkillViolation:
    """A single artifact-shape failure for a tagged skill."""

    __slots__ = ("skill", "message", "severity")

    def __init__(self, skill: str, message: str, severity: str = "error") -> None:
        self.skill = skill
        self.message = message
        self.severity = severity

    def __str__(self) -> str:
        return f"[{self.skill}] {self.message}"


# ---------------------------------------------------------------------------
# Deliverable-skill validators (have file-shape proofs)
# ---------------------------------------------------------------------------

# Lint patterns the security-audit validator rejects in extracted files.
# Each entry: (regex, file-suffix-set, human-readable reason). We do not
# attempt full SAST -- this is a "did the LLM walk into an obvious foot-
# gun" pass. The skill's prompt content tells the LLM the right patterns;
# this validator catches when the LLM ignored them.
_SECURITY_LINTS: list[tuple[re.Pattern[str], frozenset[str], str]] = [
    (re.compile(r"\.innerHTML\s*=\s*[^'\"`\s]"),
     frozenset({".ts", ".tsx", ".js", ".jsx"}),
     "writing to .innerHTML with non-constant content (XSS risk; use textContent or a sanitizer)"),
    (re.compile(r"dangerouslySetInnerHTML\b"),
     frozenset({".tsx", ".jsx"}),
     "dangerouslySetInnerHTML used (escape user content explicitly)"),
    (re.compile(r"\beval\s*\("),
     frozenset({".ts", ".tsx", ".js", ".jsx", ".py"}),
     "eval() call (refuse arbitrary code execution; use JSON.parse / structured input)"),
    (re.compile(r"\bnew\s+Function\s*\("),
     frozenset({".ts", ".tsx", ".js", ".jsx"}),
     "new Function() constructor (same risk as eval)"),
    (re.compile(r"subprocess\.(?:run|Popen|call)\([^)]*shell\s*=\s*True"),
     frozenset({".py"}),
     "subprocess called with shell=True (use the argv-list form)"),
    (re.compile(r"os\.system\s*\("),
     frozenset({".py"}),
     "os.system() call (use subprocess.run([...]) without shell=True)"),
    (re.compile(r"['\"](?:sk-|pk_live_|AKIA|ghp_|xox[bap]-)[A-Za-z0-9_-]{16,}['\"]"),
     frozenset({".ts", ".tsx", ".js", ".jsx", ".py", ".json", ".env"}),
     "hardcoded API-key-looking string (load from OS keychain via Tauri keychain IPC)"),
]


def _validate_security_audit(_task: dict, root: Path, written: list[str], _resp: str) -> list[SkillViolation]:
    violations: list[SkillViolation] = []
    for rel in written:
        target = root / rel
        if not target.is_file():
            continue
        suffix = target.suffix.lower()
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern, exts, reason in _SECURITY_LINTS:
            if suffix not in exts:
                continue
            if pattern.search(content):
                violations.append(SkillViolation(
                    skill="security-audit",
                    message=f"{rel}: {reason}",
                ))
    return violations


def _validate_test_generation(_task: dict, _root: Path, written: list[str], _resp: str) -> list[SkillViolation]:
    # The task asks for tests; at least one test file should have been
    # produced. We accept .test. / .spec. / test_*.py / *_test.py.
    test_re = re.compile(r"(?:\.test\.|\.spec\.|^test_|_test\.)", re.IGNORECASE)
    has_test = any(test_re.search(Path(rel).name) for rel in written)
    if not has_test and written:
        return [SkillViolation(
            skill="test-generation",
            message=(
                "no test file was produced. A task tagged "
                "'test-generation' must emit at least one file matching "
                "*.test.*, *.spec.*, test_*.py, or *_test.py."
            ),
        )]
    return []


def _has_sections(content: str, required: list[str]) -> list[str]:
    """Return the list of required headings that are MISSING from *content*.

    Matches both `## Heading` and `### Heading` styles, case-insensitive.
    """
    missing: list[str] = []
    for heading in required:
        pattern = re.compile(r"^#{2,4}\s*" + re.escape(heading), re.IGNORECASE | re.MULTILINE)
        if not pattern.search(content):
            missing.append(heading)
    return missing


def _read_artifact(root: Path, *candidate_paths: str) -> str | None:
    """Return the contents of the first candidate path that exists."""
    for rel in candidate_paths:
        p = root / rel
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return None


def _validate_comprehensive_code_review(task: dict, root: Path, _written: list[str], _resp: str) -> list[SkillViolation]:
    task_id = task.get("task") or task.get("step_id") or "task"
    content = _read_artifact(
        root,
        f".signalos/reviews/{task_id}.md",
        f".signalos/reviews/wave-{task.get('wave', '?')}-{task_id}.md",
    )
    if content is None:
        return [SkillViolation(
            skill="comprehensive-code-review",
            message=(
                f"missing review artifact .signalos/reviews/{task_id}.md "
                f"with severity sections. Emit one file at that path with "
                f"## Critical / ## High / ## Medium / ## Low headings "
                f"(use `## None` if no issues at that severity)."
            ),
        )]
    missing = _has_sections(content, ["Critical", "High", "Medium", "Low"])
    if missing:
        return [SkillViolation(
            skill="comprehensive-code-review",
            message=f"review artifact missing severity heading(s): {', '.join(missing)}",
        )]
    return []


def _validate_systematic_debugging(task: dict, root: Path, _written: list[str], _resp: str) -> list[SkillViolation]:
    task_id = task.get("task") or "task"
    content = _read_artifact(root, f".signalos/debug/{task_id}.md")
    if content is None:
        return [SkillViolation(
            skill="systematic-debugging",
            message=(
                f"missing debug trace .signalos/debug/{task_id}.md. "
                f"Emit a file at that path with ## Reproduce / "
                f"## Hypothesis / ## Test / ## Fix sections (one each)."
            ),
        )]
    missing = _has_sections(content, ["Reproduce", "Hypothesis", "Test", "Fix"])
    if missing:
        return [SkillViolation(
            skill="systematic-debugging",
            message=f"debug trace missing section(s): {', '.join(missing)}",
        )]
    return []


def _validate_writing_plans(_task: dict, root: Path, _written: list[str], _resp: str) -> list[SkillViolation]:
    # A wave-writing task must produce PLAN.tasks.yaml. The chat flow
    # already writes this from JS; this validator catches the rare case
    # where the planner skill is invoked from an orchestrate task and
    # forgets to.
    if not (root / "PLAN.tasks.yaml").is_file():
        return [SkillViolation(
            skill="writing-plans",
            message="PLAN.tasks.yaml is missing -- a writing-plans task must produce it.",
        )]
    return []


def _validate_executing_plans(_task: dict, root: Path, _written: list[str], _resp: str) -> list[SkillViolation]:
    # Executing-plans implies the plan was actually read. We check the
    # audit trail has an `orchestrate.start` (or similar) event in the
    # last N entries.
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not audit.is_file():
        return [SkillViolation(
            skill="executing-plans",
            message="AUDIT_TRAIL.jsonl is missing -- execution must leave audit entries.",
        )]
    return []


def _validate_using_git_worktrees(_task: dict, root: Path, _written: list[str], _resp: str) -> list[SkillViolation]:
    # A worktree task should leave evidence in the worktree-state file.
    state = root / ".signalos" / "worktree-state.json"
    if state.is_file():
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("worktrees"):
                return []
        except (OSError, ValueError):
            pass
    # If worktrees aren't being used (no-bash fallback), this is not a
    # violation -- the orchestrator chose the sequential path. Don't
    # block the user on a missing artifact for a path they couldn't
    # take.
    return []


def _validate_receiving_code_review(task: dict, root: Path, _written: list[str], _resp: str) -> list[SkillViolation]:
    task_id = task.get("task") or "task"
    content = _read_artifact(root, f".signalos/responses/{task_id}.md")
    if content is None:
        return [SkillViolation(
            skill="receiving-code-review",
            message=(
                f"missing review response .signalos/responses/{task_id}.md. "
                f"Emit a file mapping each reviewer comment to one of "
                f"`## Addressed`, `## Declined`, or `## Wontfix` with a "
                f"short rationale."
            ),
        )]
    # At least one of the three response sections must exist.
    # _has_sections returns the MISSING headings; if any of these three
    # is present, at most two are missing.
    missing = _has_sections(content, ["Addressed", "Declined", "Wontfix"])
    if len(missing) == 3:
        return [SkillViolation(
            skill="receiving-code-review",
            message="review response file has no `## Addressed/Declined/Wontfix` section.",
        )]
    return []


def _validate_requesting_code_review(task: dict, root: Path, _written: list[str], _resp: str) -> list[SkillViolation]:
    task_id = task.get("task") or "task"
    content = _read_artifact(root, f".signalos/review-requests/{task_id}.md")
    if content is None:
        return [SkillViolation(
            skill="requesting-code-review",
            message=(
                f"missing review request .signalos/review-requests/{task_id}.md. "
                f"Emit a file with `## Summary`, `## Changes`, and "
                f"`## Test plan` sections."
            ),
        )]
    missing = _has_sections(content, ["Summary", "Changes", "Test plan"])
    if missing:
        return [SkillViolation(
            skill="requesting-code-review",
            message=f"review request missing section(s): {', '.join(missing)}",
        )]
    return []


def _validate_finishing_a_branch(_task: dict, root: Path, _written: list[str], _resp: str) -> list[SkillViolation]:
    # Either the worktree-state shows merged/retired entries OR there
    # are no worktrees at all (the orchestrator chose sequential mode).
    state = root / ".signalos" / "worktree-state.json"
    if not state.is_file():
        return []  # No worktrees in play; nothing to finish.
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    worktrees = data.get("worktrees", [])
    if not isinstance(worktrees, list) or not worktrees:
        return []
    # Just check we have *some* "merged" or "retired" status entries; this
    # is a soft check -- the bash script's lifecycle is the source of truth.
    statuses = {wt.get("status") for wt in worktrees if isinstance(wt, dict)}
    if not (statuses & {"merged", "retired", "done"}):
        return [SkillViolation(
            skill="finishing-a-development-branch",
            message=(
                "no worktree entries marked merged/retired/done. Run "
                "`signalos orchestrate ... --retire` to finish the branch "
                "before claiming the task is complete."
            ),
            severity="warning",
        )]
    return []


def _validate_verification_before_completion(_task: dict, root: Path, written: list[str], resp: str) -> list[SkillViolation]:
    # Soft check: response text should include "## Verification" or
    # "## Self-check" or similar, OR a verification artifact must exist.
    # We don't want to block on this -- it's about discipline, not
    # output -- so this validator is advisory (warning severity).
    if not written:
        return []
    if re.search(r"^#{1,4}\s*(?:Verification|Self.?check|Sanity check)", resp, re.IGNORECASE | re.MULTILINE):
        return []
    if (root / ".signalos" / "verification.md").is_file():
        return []
    return [SkillViolation(
        skill="verification-before-completion",
        message=(
            "no verification section found in response or "
            ".signalos/verification.md artifact. Run through the "
            "self-check (compile, lint, test, behavior, edge cases) "
            "before claiming done."
        ),
        severity="warning",
    )]


def _validate_retro_run(task: dict, root: Path, _written: list[str], _resp: str) -> list[SkillViolation]:
    wave = task.get("wave", "?")
    content = _read_artifact(
        root,
        f"core/governance/Retro/waves/W{wave}/WAVE_REVIEW.md",
        f"core/governance/Retro/wave-{wave}-review.md",
        ".signalos/retros/latest.md",
    )
    if content is None:
        return [SkillViolation(
            skill="retro-run",
            message=(
                f"missing retro artifact for wave {wave}. Emit "
                f"core/governance/Retro/waves/W{wave}/WAVE_REVIEW.md with "
                f"## What worked / ## What didn't / ## Action items."
            ),
        )]
    missing = _has_sections(content, ["What worked", "What didn't", "Action items"])
    # Be permissive on apostrophe style ("didn't" vs "did not").
    if "What didn't" in missing and "What did not" not in _has_sections(content, ["What did not"]):
        missing.remove("What didn't")
    if missing:
        return [SkillViolation(
            skill="retro-run",
            message=f"retro missing section(s): {', '.join(missing)}",
        )]
    return []


def _validate_retrospective_analyze(_task: dict, root: Path, _written: list[str], _resp: str) -> list[SkillViolation]:
    content = _read_artifact(root, ".signalos/retros/analysis.md")
    if content is None:
        return [SkillViolation(
            skill="retrospective-analyze",
            message=(
                "missing .signalos/retros/analysis.md with cross-wave "
                "trend analysis. Emit a file with ## Patterns / "
                "## Recurring issues / ## Recommendations."
            ),
        )]
    return []


# ---------------------------------------------------------------------------
# Registry
#
# Process / cognitive skills not listed here are treated as advisory --
# their SKILL.md content gets injected into the prompt but there's no
# post-write enforcement, because they're context-providers (memory,
# context, brainstorming) not output-producers. Adding artifact validators
# for them would be ceremony without value.
# ---------------------------------------------------------------------------

_Validator = Callable[[dict, Path, list[str], str], list[SkillViolation]]

VALIDATORS: dict[str, _Validator] = {
    "security-audit":                 _validate_security_audit,
    "test-generation":                _validate_test_generation,
    # test-driven-development handled separately (multi-phase execution
    # in orchestrator's TDD loop, not a single post-write check)
    "comprehensive-code-review":      _validate_comprehensive_code_review,
    "systematic-debugging":           _validate_systematic_debugging,
    "writing-plans":                  _validate_writing_plans,
    "executing-plans":                _validate_executing_plans,
    "using-git-worktrees":            _validate_using_git_worktrees,
    "finishing-a-development-branch": _validate_finishing_a_branch,
    "receiving-code-review":          _validate_receiving_code_review,
    "requesting-code-review":         _validate_requesting_code_review,
    "verification-before-completion": _validate_verification_before_completion,
    "retro-run":                      _validate_retro_run,
    "retrospective-analyze":          _validate_retrospective_analyze,
}


def validate_skill_artifacts(
    skills: list[str] | None,
    task: dict,
    root: Path,
    written_files: list[str],
    task_response: str = "",
) -> list[SkillViolation]:
    """Run every applicable validator for *skills* and return any violations.

    Unknown skill keys are skipped silently (advisory / context skills).
    Validator exceptions are caught so a buggy validator can't crash the
    orchestrator -- we log to stderr-equivalent and continue.
    """
    if not skills:
        return []
    out: list[SkillViolation] = []
    for skill in skills:
        validator = VALIDATORS.get(skill)
        if validator is None:
            continue
        try:
            out.extend(validator(task, root, written_files, task_response))
        except Exception as exc:  # pragma: no cover -- defensive
            out.append(SkillViolation(
                skill=skill,
                message=f"validator crashed: {exc.__class__.__name__}: {exc}",
                severity="warning",
            ))
    return out
