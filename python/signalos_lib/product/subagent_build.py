# signalos_lib/product/subagent_build.py
# Subagent-Driven Build (Gate 4) -- assembles the BUNDLED skills we already
# ship and credit (SignalOS bundles Superpowers' TDD + subagent-driven
# development; the flow also matches GitHub Spec Kit's tests-first,
# task-by-task implement phase).
#
# The build EXECUTES THE SIGNED PLAN, test-first and iteratively:
#
#   decompose the signed plan -> TASKS (each with its plan-authored test)
#   per task:  implementer subagent makes THAT task's test pass
#              -> objective per-task gate (run just that test) -> bounded fixer
#   integration: full real build + whole suite -> batched bounded fixer
#   review:    independent spec + code-quality reviewers on the GREEN product
#   evidence:  BUILD_EVIDENCE with the real numbers
#
# Nothing here hardcodes workspace layout, stack, or budgets:
#   - Artifacts (Plan / Acceptance Criteria / Design Note / Build Evidence) are
#     resolved through artifacts.resolve_gate_artifacts by manifest LABEL, so
#     non-default project namespaces resolve to where the loop actually wrote.
#   - Stack specifics (build/test commands, single-test command, source dir,
#     prompt gotchas) come from the stack adapter registry (stacks.py); optional
#     adapter extensions are consumed via getattr with graceful fallbacks.
#   - Budgets come from budgets.py resolvers (SIGNALOS_BUILD_* env overrides).
#
# Reviewers run on the orchestrator's INDEPENDENT critic_adapter when one is
# configured (a genuinely different model / vendor -- not double-biased
# self-review). The final hard wall is unchanged: GateOrchestrator._verify_
# g4_build runs the real build+test on disk and refuses to sign a stub, so the
# reviewers here improve quality but never replace the objective gate.
#
# Everything is injectable through `run_agent` / `build_check` so the
# orchestration is unit testable with fakes, no live LLM.

from __future__ import annotations

__all__ = [
    "Task",
    "decompose_plan_tasks",
    "decompose_tasks",
    "parse_verdict",
    "parse_implementer_status",
    "run_subagent_driven_build",
]

import re
import subprocess
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Optional

from ..artifacts import resolve_gate_artifacts, resolve_workspace_path
from .agent_loop import AgentLoop, LoopResult
from .budgets import (
    resolve_build_fixer_error_batch,
    resolve_build_implementer_tool_budget,
    resolve_build_max_tasks,
    resolve_build_reviewer_tool_budget,
    resolve_build_task_fix_cycles,
    resolve_repair_cycle_budget,
)

# A single subagent turn: (role, adapter, system_prompt, user_message) -> report
RunAgent = Callable[[str, Any, str, str], str]
# (repo_root, only_test=None) -> (is_green, real_error_text). The OBJECTIVE
# gate. only_test=None runs the FULL build+suite (integration); a path runs just
# that one plan test (the per-task green gate, so errors never pile up).
BuildCheck = Callable[..., "tuple[bool, str]"]

# Verdict token the reviewer must end its report with. Parsed fail-closed but
# the loop is budget-bounded, and the objective build gate is the real wall.
_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)", re.I)
_STATUS_RE = re.compile(r"\b(DONE_WITH_CONCERNS|NEEDS_CONTEXT|BLOCKED|DONE)\b")

# Per bundled-doc char cap so a call's system prompt stays bounded (prompt
# shaping, not an execution budget -- the execution budgets live in budgets.py).
_PROMPT_CAP = 6000

# Common source-code suffixes for "files already in the project" listings.
# The source DIRECTORY comes from the stack adapter (resolve_targets); this
# suffix set only filters obvious non-code (assets, lockfiles) out of listings.
_CODE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".vue", ".py", ".go", ".rs",
                  ".cs", ".java", ".kt", ".dart", ".rb", ".php")


# ---------------------------------------------------------------------------
# Stack context -- everything stack-specific, resolved ONCE per build from the
# adapter registry so prompts and gates never hardcode a stack.
# ---------------------------------------------------------------------------

@dataclass
class _StackContext:
    profile: str = "generic"
    source_dir: str = "src"
    build_cmds: list = field(default_factory=list)
    test_cmds: list = field(default_factory=list)
    gotchas: str = ""
    # (repo_root, test_path) -> argv for ONE test file, or None if the adapter
    # has no single-test runner (per-task gate then falls back to full checks).
    test_file_command: Optional[Callable[[Path, str], list]] = None

    @property
    def build_and_test_hint(self) -> str:
        cmds = [*self.build_cmds, *self.test_cmds]
        return " && ".join(cmds) if cmds else "the project's build and test commands"


def _resolve_stack(repo_root: Path) -> _StackContext:
    """Resolve the stack context from the adapter registry. Degrades to a
    permissive generic context on any failure (the objective gate still rules)."""
    ctx = _StackContext()
    try:
        from .stacks import detect_profile, get_adapter
        ctx.profile = detect_profile(repo_root)
        adapter = get_adapter(ctx.profile)
    except Exception:
        return ctx
    try:
        targets = adapter.resolve_targets(repo_root)
        ctx.source_dir = str(targets.get("source") or "src").strip() or "src"
    except Exception:
        pass
    try:
        plan = adapter.validation_plan(repo_root)
        ctx.build_cmds = list(plan.get("build", []) or [])
        ctx.test_cmds = list(plan.get("test", []) or [])
    except Exception:
        pass
    # Optional adapter extensions -- absent on most adapters; degrade cleanly.
    gotchas_fn = getattr(adapter, "prompt_gotchas", None)
    if callable(gotchas_fn):
        try:
            ctx.gotchas = str(gotchas_fn(repo_root) or "")
        except Exception:
            ctx.gotchas = ""
    tfc = getattr(adapter, "test_file_command", None)
    if callable(tfc):
        ctx.test_file_command = tfc
    return ctx


# ---------------------------------------------------------------------------
# Artifact resolution (assemble, don't hardcode paths)
# ---------------------------------------------------------------------------

def _artifact_by_label(repo_root: Path, project_id: str,
                       *labels: str) -> "dict[str, Any]":
    """Resolve gate artifacts by their manifest LABEL (never a hardcoded path):
    {label: ResolvedGateArtifact}. Labels come from gate_artifacts.json ("Plan",
    "Acceptance Criteria", "Design Note", "Expectation Map", "Build Evidence"),
    and .path is already project-namespaced + escape-checked by the resolver, so
    non-default project namespaces resolve to exactly where the loop wrote them.
    Resolution failures degrade to an empty map (callers fall back)."""
    want = set(labels)
    out: dict[str, Any] = {}
    try:
        for art in resolve_gate_artifacts(repo_root, None, project_id=project_id):
            if art.label in want and art.label not in out:
                out[art.label] = art
    except Exception:
        return {}
    return out


def _workspace_path(repo_root: Path, rel_path: str, project_id: str) -> Optional[Path]:
    """Project-namespaced, escape-checked resolution of a single rel_path (e.g.
    a plan test skeleton under the plan's tests directory). None if invalid."""
    try:
        return resolve_workspace_path(repo_root, rel_path, project_id)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Bundled skill loading (assemble, don't rebuild)
# ---------------------------------------------------------------------------

def _bundle_execution_dir() -> Path:
    """On-disk path to `_bundle/core/execution` (installed or source tree),
    mirroring agent_loader._bundle_agents_dir."""
    try:
        ref = resources.files("signalos_lib").joinpath(
            "_bundle", "core", "execution",
        )
        return Path(str(ref))
    except (ModuleNotFoundError, AttributeError):
        here = Path(__file__).resolve().parent.parent
        return here / "_bundle" / "core" / "execution"


_BUNDLE_FILES = {
    "implementer": ("subagents", "subagent-driven-development", "implementer-prompt.md"),
    "spec_reviewer": ("subagents", "subagent-driven-development", "spec-reviewer-prompt.md"),
    "code_reviewer": ("subagents", "subagent-driven-development", "code-quality-reviewer-prompt.md"),
    "tdd": ("build", "test-driven-development", "SKILL.md"),
}

_bundle_cache: dict[str, str] = {}


def _load_bundled(key: str) -> str:
    """Load a bundled skill/prompt doc by key, cached. Missing file -> "" so a
    partial bundle degrades gracefully instead of crashing G4."""
    if key in _bundle_cache:
        return _bundle_cache[key]
    parts = _BUNDLE_FILES.get(key)
    text = ""
    if parts:
        try:
            path = _bundle_execution_dir().joinpath(*parts)
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    _bundle_cache[key] = text
    return text


def _strip_template_wrapper(raw: str) -> str:
    """The bundled *-prompt.md files wrap their body in a `Task tool (...):`
    header + a fenced block with placeholders. Keep the behavioral guidance,
    drop the dispatch-mechanism scaffolding so it reads as instructions to the
    subagent (which SignalOS dispatches in Python, not via a Task tool)."""
    lines = raw.splitlines()
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("```"):
            continue
        if s.startswith("Task tool") or s.startswith("description:") or s == "prompt: |":
            continue
        if s.startswith("Use this template") or s.startswith("Use template at"):
            continue
        out.append(ln)
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# Task decomposition -- the "smaller pieces" the build needs (Waves stay the
# outer unit; a Wave's build decomposes into task-level vertical slices).
#
# Fallback chain (never silent): structured plan tasks (headings + Files/Test
# fields) -> acceptance headings -> acceptance criterion lines -> one
# whole-product task. Which path was taken is reported via Task fields (a plan
# task carries .test) and the run summary.
# ---------------------------------------------------------------------------

@dataclass
class Task:
    id: str
    name: str
    text: str
    extra: list[str] = field(default_factory=list)  # merged tail criteria, if any
    files: list[str] = field(default_factory=list)  # target source files (from plan)
    test: str = ""                                    # plan-authored test path (from plan)


def _clean_cell(cell: str) -> str:
    return re.sub(r"\s+", " ", cell.replace("`", "").strip())


# A plan task heading, e.g. "### T1.1 — Setup Zustand store". The plan artifact
# authors one failing acceptance test per task (test-first / TDD-at-plan-time)
# and records its path under `**Test:**` plus the target `**Files:**`. The
# build EXECUTES that plan: implement the product until each task's authored
# test passes -- it must NOT write parallel tests of its own.
_PLAN_TASK_RE = re.compile(r"^#{2,4}\s+(T\d+(?:\.\d+)?)\b\s*[—:-]?\s*(.*)$")
# Matches both `**Files:**` (colon inside the bold, as the plan authors it) and
# `**Files**:` (colon after).
_PLAN_FIELD_RE = re.compile(r"^\*\*(Files|Test)(?::\*\*|\*\*:)\s*(.+)$", re.I)
_BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")


def _plan_text(repo_root: Path, project_id: str) -> str:
    """The signed plan's markdown, resolved via the artifact manifest."""
    art = _artifact_by_label(repo_root, project_id, "Plan").get("Plan")
    try:
        if art is not None and art.path.is_file():
            return art.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def decompose_plan_tasks(repo_root: Path, project_id: str = "default") -> list[Task]:
    """Parse the signed plan artifact into ordered build tasks, each carrying
    its target source files and its plan-authored acceptance TEST path. Empty
    list when there is no such structured plan (caller falls back to acceptance
    decomposition)."""
    md = _plan_text(repo_root, project_id)
    if not md:
        return []
    lines = md.splitlines()
    heads = [(i, m) for i, ln in enumerate(lines)
             if (m := _PLAN_TASK_RE.match(ln.strip()))]
    if len(heads) < 2:
        return []
    tasks: list[Task] = []
    for k, (i, m) in enumerate(heads):
        tid, title = m.group(1), _clean_cell(m.group(2))
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        block = lines[i + 1:end]
        files: list[str] = []
        test = ""
        for bl in block:
            fm = _PLAN_FIELD_RE.match(bl.strip())
            if not fm:
                continue
            paths = _BACKTICK_PATH_RE.findall(fm.group(2)) or [fm.group(2).strip()]
            if fm.group(1).lower() == "files":
                files = [p.strip() for p in paths if p.strip()]
            else:
                test = paths[0].strip() if paths else ""
        text = "\n".join(block).strip()
        name = (f"{tid} — {title}" if title else tid)[:70]
        tasks.append(Task(id=tid, name=name, text=text, files=files, test=test))
    return tasks


# An acceptance-criterion / task HEADING, e.g. "### AC-1: Store Implementation",
# "## AC2", "### Acceptance 3", "#### T1.2: Persistence". When >=2 are present
# the build decomposes per HEADING (one AC = one vertical slice, with its
# checkboxes/evidence as the task body) rather than per individual checkbox,
# which would over-fragment one AC into many micro-tasks.
_AC_HEADING_RE = re.compile(
    r"^#{2,4}\s+((?:AC|T)[-_ ]?\d+(?:\.\d+)?\b.*|Acceptance\s+(?:Row\s+)?\d+.*)$",
    re.I,
)


def _tasks_from_headings(md: str) -> list[tuple[str, str]]:
    """(name, text) per acceptance heading, text = the heading's block (its
    checkboxes + evidence). Empty when fewer than two headings are found."""
    lines = md.splitlines()
    heads = [i for i, ln in enumerate(lines) if _AC_HEADING_RE.match(ln.strip())]
    if len(heads) < 2:
        return []
    out: list[tuple[str, str]] = []
    for k, i in enumerate(heads):
        name = _clean_cell(re.sub(r"^#{2,4}\s+", "", lines[i].strip()))
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        body = "\n".join(lines[i + 1:end]).strip()
        text = (name + ("\n" + body if body else "")).strip()
        out.append((name[:70], text))
    return out


def _criteria_from_markdown(md: str) -> list[str]:
    """Extract acceptance-criterion lines from the acceptance artifact. Handles
    the common shapes an authoring agent emits: a table, checkbox/bullet lists,
    or numbered lists. Returns criteria strings in document order."""
    criteria: list[str] = []

    # 1. Markdown table rows: skip the header row and the `---|---` separator,
    # take the widest meaningful (non-id, non-status) cell as the criterion.
    table_rows = [ln for ln in md.splitlines() if ln.count("|") >= 2]
    real_rows = [ln for ln in table_rows if not re.match(r"^\s*\|?[\s:|-]+\|?\s*$", ln)]
    if len(real_rows) >= 2:
        for ln in real_rows[1:]:  # drop header
            cells = [_clean_cell(c) for c in ln.strip().strip("|").split("|")]
            cells = [c for c in cells if c]
            if not cells:
                continue
            # Prefer the longest cell that isn't a bare id/status token.
            body = max(
                (c for c in cells if not re.match(r"^(AC[-_ ]?\d+|[A-Z]{2,4}\d*|✅|❌|done|todo|pass|fail)$", c, re.I)),
                key=len, default="",
            )
            if len(body) >= 8:
                criteria.append(body)
        if criteria:
            return criteria

    # 2. Bullet / checkbox / numbered lists.
    for ln in md.splitlines():
        m = re.match(r"^\s*(?:[-*]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)(.+)$", ln)
        if m:
            body = _clean_cell(m.group(1))
            body = re.sub(r"^(AC[-_ ]?\d+\s*[:.\-]\s*)", "", body, flags=re.I)
            if len(body) >= 8:
                criteria.append(body)
    return criteria


def decompose_tasks(repo_root: Path, prompt: str,
                    project_id: str = "default") -> list[Task]:
    """Decompose the signed work into ordered build TASKS. Prefers the plan's
    own task list (each task already carrying its target files and its
    plan-authored acceptance test -- the build EXECUTES that plan test-first).
    Falls back to acceptance-criteria headings/criterion lines, then to a
    single whole-product task, so the path never breaks."""
    max_tasks = resolve_build_max_tasks()
    plan_tasks = decompose_plan_tasks(repo_root, project_id)
    if plan_tasks:
        return plan_tasks[:max_tasks] if len(plan_tasks) > max_tasks else plan_tasks

    md = ""
    art = _artifact_by_label(repo_root, project_id, "Acceptance Criteria").get(
        "Acceptance Criteria")
    try:
        if art is not None and art.path.is_file():
            md = art.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        md = ""

    # Prefer per-heading tasks (one AC = one slice); else per-criterion lines.
    pairs = _tasks_from_headings(md)
    if not pairs:
        seen: set[str] = set()
        pairs = [(c[:70], c) for c in _criteria_from_markdown(md)
                 if not (c.lower() in seen or seen.add(c.lower()))]

    if not pairs:
        return [Task(id="T1", name="Implement the product",
                     text=(prompt or "Implement the product to satisfy the signed acceptance criteria."))]

    tasks: list[Task] = []
    head = pairs[:max_tasks]
    tail = [t for _, t in pairs[max_tasks:]]
    for i, (name, text) in enumerate(head, 1):
        tasks.append(Task(id=f"T{i}", name=name, text=text))
    if tail:
        # Never silently drop criteria: fold the tail into the last task.
        tasks[-1].extra = tail
    return tasks


# ---------------------------------------------------------------------------
# Verdict / status parsing
# ---------------------------------------------------------------------------

def parse_verdict(text: str) -> str:
    """PASS or FAIL from a reviewer report. Prefers the explicit `VERDICT:`
    token the orchestration asks for; falls back to the bundled prompts' native
    ✅/❌ convention; defaults FAIL (fail-closed -- the reviewer must clearly
    approve). The per-task loop is budget-bounded, so a stubborn FAIL cannot
    spin forever."""
    if not text:
        return "FAIL"
    m = _VERDICT_RE.search(text)
    if m:
        return m.group(1).upper()
    low = text.lower()
    if "❌" in text or "issues found" in low or "not compliant" in low:
        return "FAIL"
    if "✅" in text or "spec compliant" in low or re.search(r"\bapproved\b", low):
        return "PASS"
    return "FAIL"


def parse_implementer_status(text: str) -> str:
    """DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT from an implementer
    report; defaults DONE when the model omitted an explicit status."""
    if not text:
        return "DONE"
    m = _STATUS_RE.search(text)
    return m.group(1) if m else "DONE"


# ---------------------------------------------------------------------------
# Prompt assembly (stack-agnostic: specifics come from _StackContext)
# ---------------------------------------------------------------------------

def _implementer_system_prompt(governance_frame: str, stack: _StackContext) -> str:
    impl = _strip_template_wrapper(_load_bundled("implementer"))[:_PROMPT_CAP]
    tdd = _load_bundled("tdd")[:_PROMPT_CAP]
    parts = [
        "You are the implementer subagent in a SignalOS-governed build. You "
        "have real tools (read_file, write_file, edit_file, run_command, "
        "search_files, list_directory). You implement ONE task at a time as a "
        "vertical slice and DRIVE IT TO GREEN before reporting: make the task's "
        "acceptance test pass with real implementation (INCLUDING creating any "
        "module the test imports), run the project's build/test commands via "
        "run_command, and iterate until green. Never stop at red and never "
        "leave a test whose implementation module is missing.",
        "",
        "## Test-Driven Development (bundled skill -- follow it)",
        tdd,
        "",
        "## Implementer discipline (bundled skill -- follow it)",
        impl,
    ]
    if governance_frame.strip():
        parts += ["", "## Governance frame (binding forbidden rules)", governance_frame[:_PROMPT_CAP]]
    return "\n".join(parts)


def _reviewer_system_prompt(kind: str) -> str:
    key = "spec_reviewer" if kind == "spec" else "code_reviewer"
    body = _strip_template_wrapper(_load_bundled(key))
    role = ("spec-compliance reviewer" if kind == "spec"
            else "code-quality reviewer")
    return "\n".join([
        f"You are the {role} subagent in a SignalOS-governed build. You have "
        "read tools (read_file, search_files, list_directory) and may run "
        "read-only commands. You are a REVIEWER: do NOT modify code -- the "
        "implementer fixes any issues you find. Verify by READING the actual "
        "code on disk, never by trusting the implementer's report.",
        "",
        body,
        "",
        "## Required output",
        "After your findings, end your report with a final line that is EXACTLY "
        "one of:",
        "  VERDICT: PASS",
        "  VERDICT: FAIL: <one concrete, actionable reason>",
        ("PASS only when the code on disk fully satisfies the task with nothing "
         "missing or extra." if kind == "spec"
         else "PASS only when there are no Critical or Important issues."),
    ])


def _task_context(repo_root: Path, prompt: str, project_id: str) -> str:
    """Scene-setting shared by every implementer dispatch: the product ask plus
    the signed artifacts (resolved via the manifest, capped)."""
    parts = [f"Product ask: {prompt}".strip()]
    arts = _artifact_by_label(repo_root, project_id,
                              "Acceptance Criteria", "Design Note", "Plan")
    for label in ("Acceptance Criteria", "Design Note", "Plan"):
        art = arts.get(label)
        if art is None:
            continue
        try:
            if art.path.is_file():
                txt = art.path.read_text(encoding="utf-8", errors="replace").strip()
                if txt:
                    parts.append(f"## {label} ({art.rel_path})\n{txt[:3000]}")
        except OSError:
            pass
    return "\n\n".join(parts)


def _current_src_files(repo_root: Path, stack: _StackContext) -> list[str]:
    """The code files already on disk under the stack's SOURCE dir (from the
    adapter's resolve_targets, not a hardcoded 'src'). Fed to each draft so a
    later task reuses the EXACT paths earlier tasks created instead of inventing
    parallel modules -- the drift that otherwise leaves the repair loop
    reconciling duplicates."""
    src = repo_root / stack.source_dir
    if not src.is_dir():
        return []
    return sorted(
        str(p.relative_to(repo_root)).replace("\\", "/")
        for p in src.rglob("*")
        if p.is_file() and p.suffix in _CODE_SUFFIXES
    )


def _read_test(repo_root: Path, test_path: str, project_id: str) -> str:
    """The plan-authored acceptance test's source (capped), or '' if absent."""
    if not test_path:
        return ""
    p = _workspace_path(repo_root, test_path, project_id)
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p and p.is_file() else ""
    except OSError:
        return ""


def _implementer_message(task: Task, context: str, repo_root: Path,
                         stack: _StackContext, project_id: str = "default",
                         fix_feedback: str = "") -> str:
    lines = [f"# Task {task.id}: {task.name}", "", "## Task (from the signed plan)",
             task.text]
    if task.extra:
        lines += ["", "Also cover these related criteria:"]
        lines += [f"- {e}" for e in task.extra]
    lines += ["", "## Context", context, ""]

    test_src = _read_test(repo_root, task.test, project_id)
    src_hint = f"under {stack.source_dir}/**"
    if task.test and test_src:
        # TEST-FIRST, plan-driven: the acceptance test ALREADY EXISTS (the plan
        # authored it and it is the signed spec). The build EXECUTES it -- make it
        # pass, never write a parallel test, never weaken assertions.
        lines += [
            f"## Your acceptance test (ALREADY WRITTEN by the plan): `{task.test}`",
            "This is the signed spec for this task. Your job is to make it PASS "
            f"(RED -> GREEN) by implementing the product {src_hint}. Rules:",
            "- Do NOT write a new or duplicate test, and do NOT copy this test "
            "anywhere else. This test file, at this path, IS the test.",
            "- Do NOT weaken, delete, or alter its assertions.",
            "- The ONLY thing you may change in the test file is a BROKEN import "
            "path (e.g. fix the relative depth) so it resolves to the real module "
            f"{src_hint}. Target implementation files: {', '.join(task.files) or src_hint}.",
            "- Run this one test to green via run_command.",
            "",
            "```",
            test_src[:3500],
            "```",
            "",
        ]
    else:
        test_note = (f"Write the test at `{task.test}` first (RED), then implement."
                     if task.test else
                     "Write ONE failing test first (RED), then implement to GREEN.")
        lines += [f"## Test-first: {test_note}", ""]

    existing = _current_src_files(repo_root, stack)
    if existing:
        lines += [
            "## Files already in the project (REUSE these EXACT paths -- import "
            "the modules that exist; never invent a parallel module or a nested "
            "`Foo/Foo.*` when `Foo.*` exists):",
            *[f"- {f}" for f in existing],
            "",
        ]
    if stack.gotchas:
        lines += [stack.gotchas, ""]
    lines += [f"Work from: {repo_root}", ""]
    if fix_feedback.strip():
        lines += [
            "## Fix these issues from the objective test run, then re-run to green:",
            fix_feedback.strip(),
            "",
        ]
    else:
        lines += [
            "Implement to make the acceptance test pass, then report Status "
            "(DONE / DONE_WITH_CONCERNS / BLOCKED / NEEDS_CONTEXT), files changed, "
            "and the test result.",
        ]
    return "\n".join(lines)


def _batch_errors(errors: str) -> str:
    """Cap the diagnostics fed to a fixer so its prompt never blows the context
    window (an all-errors dump has exceeded a provider's context ceiling). Fix
    the first batch; the loop re-checks and feeds the next batch."""
    batch = resolve_build_fixer_error_batch()
    lines = [l for l in errors.splitlines() if l.strip()]
    head = lines[:batch]
    more = len(lines) - len(head)
    if more > 0:
        head.append(f"... (+{more} more errors -- fix this batch first; the build "
                    "re-runs and shows the rest)")
    return "\n".join(head)


def _stall_directive(stalls: int) -> str:
    """When a repair pass changes NOTHING (identical error signature), the next
    pass must change strategy -- re-sending the same prompt to the same model
    mostly reproduces the same failed fix."""
    base = (
        "# STALLED: the previous fix pass did not reduce these errors.\n"
        "Do NOT repeat the previous approach. Change strategy:\n"
        "- Pick ONLY the FIRST failing file. Read the ENTIRE test file and the "
        "ENTIRE implementation module(s) it exercises with read_file before "
        "changing anything.\n"
        "- Reconcile the root cause (a wrong assumption, name, type, or state "
        "shape), not the symptom. Fix that ONE file-pair completely, re-run just "
        "that test, confirm it passes, and only then stop."
    )
    if stalls >= 2:
        base += (
            "\n- You are a FRESH reviewer-model pass: previous fixers failed "
            "twice on this. Question their assumptions from scratch."
        )
    return base


def _fixer_message(errors: str, repo_root: Path, stack: _StackContext) -> str:
    src_hint = f"under {stack.source_dir}/**"
    return "\n".join([
        "# Make the build GREEN -- fix these REAL errors (this batch first)",
        "These are the EXACT diagnostics from the real build/test run:",
        "",
        _batch_errors(errors),
        "",
        f"Fix these in real source {src_hint}:",
        "- The tests are the SIGNED SPEC. Never delete, weaken, or trivially "
        "satisfy a test to make it pass. If a test imports a module that does not "
        "exist, CREATE that module as a REAL, functional implementation "
        f"{src_hint}.",
        "- If a plan test's IMPORT PATH is broken (wrong relative depth), you MAY "
        "fix only that import path so it resolves -- but not its assertions.",
        "- If an assertion fails because the IMPLEMENTATION is wrong, fix the "
        "implementation to satisfy the test (the test encodes the required "
        "behavior).",
        "- If two files disagree on a path, name, or type, reconcile them; remove "
        "unused imports.",
        f"Re-run the failing check via run_command ({stack.build_and_test_hint}) "
        "and confirm green before finishing.",
        "",
        stack.gotchas,
        f"Work from: {repo_root}",
    ])


def _final_review_message(kind: str, prompt: str, stack: _StackContext) -> str:
    return "\n".join([
        "# Review the whole product against the ask",
        f"Product ask: {prompt}",
        "The build compiles and tests pass. Review the ACTUAL code on disk under "
        f"{stack.source_dir}/** (read it -- do not trust any report).",
        ("Spec compliance: does the implementation satisfy the product ask and "
         "the signed acceptance criteria -- nothing missing, nothing extra? Also "
         "flag DUPLICATE or parallel test files (the plan's authored tests are "
         "the spec; copies are drift)."
         if kind == "spec" else
         "Code quality: single responsibility, clear names, real (non-mock) "
         "tests that assert behavior, maintainability, accessibility."),
    ])


def _evidence_rel_path(repo_root: Path, project_id: str) -> str:
    """The Build Evidence artifact's canonical rel_path, ALWAYS manifest-driven.
    Primary: the workspace-resolved artifact. Fallback: the manifest's raw G4
    rows (pure data, no path resolution -- cannot fail on repo/project grounds),
    so no literal path exists anywhere in this module."""
    art = _artifact_by_label(repo_root, project_id, "Build Evidence").get("Build Evidence")
    if art is not None:
        return art.rel_path
    try:
        from ..artifacts import expected_gate_artifacts
        for row in expected_gate_artifacts("G4"):
            label = getattr(row, "label", None)
            rel = getattr(row, "rel_path", None)
            if label == "Build Evidence" and rel:
                return str(rel)
    except Exception:
        pass
    return ""


def _evidence_message(repo_root: Path, project_id: str, stack: _StackContext,
                      green: bool) -> str:
    rel = _evidence_rel_path(repo_root, project_id)
    target = f"`{rel}`" if rel else "the Build Evidence gate artifact"
    status_line = (
        "The objective build+test run is GREEN."
        if green else
        "The objective build+test run is NOT green -- the evidence MUST say so. "
        "Do not claim success; record the failing state exactly as it is."
    )
    return "\n".join([
        "# Record the build evidence",
        status_line,
        "Run the project's build and test commands "
        f"({stack.build_and_test_hint}) once more via run_command to read the "
        f"real numbers, then write {target} with CONCRETE values: source/test "
        "files created or changed, the exact commands run, whether the build is "
        "clean (yes/no), and the test result as pass/total. Record honestly -- "
        "including failures. No TBD/TODO/placeholders, no `{{...}}`, no `[DATE]`.",
        f"Work from: {repo_root}",
    ])


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _default_run_agent(
    repo_root: Path,
    enforcement_provider: Any,
    emit: Callable[[dict], None],
    project_id: str,
    signed_gates: list[int],
    usage: Optional[dict] = None,
) -> RunAgent:
    """Real dispatcher: each call is a FRESH AgentLoop (fresh run_id, fresh
    context) -- the "fresh subagent per task/review" the bundled skill requires,
    with a bounded tool budget so no single conversation blows the context.
    When *usage* is given, per-run token totals accumulate into it
    ({"in": int|None, "out": int|None}) for build-level cost accounting."""
    impl_budget = resolve_build_implementer_tool_budget()
    rev_budget = resolve_build_reviewer_tool_budget()

    def run(role: str, adapter: Any, system_prompt: str, user_message: str) -> str:
        limit = rev_budget if role.endswith("reviewer") else impl_budget
        loop = AgentLoop(
            adapter=adapter,
            repo_root=repo_root,
            enforcement_provider=enforcement_provider,
            emit=emit,
            execution_context="delivery",
            active_gate="G4",
            project_id=project_id,
            signed_gates=list(signed_gates),
            tool_call_limit=limit,
        )
        res = loop.run(system_prompt, user_message)
        if usage is not None:
            if res.tokens_in is not None:
                usage["in"] = (usage.get("in") or 0) + res.tokens_in
            if res.tokens_out is not None:
                usage["out"] = (usage.get("out") or 0) + res.tokens_out
        return res.final_text or ""
    return run


def _run_single_test(repo_root: Path, test_path: str, stack: _StackContext,
                     project_id: str = "default") -> "tuple[bool, str]":
    """Run ONE test file via the stack adapter's single-test command (the
    per-task green gate). Test-runner-agnostic result parsing: exit code rules;
    failure lines are extracted best-effort for the fixer. Returns (True, '')
    when the adapter has no single-test runner -- the integration phase (full
    suite) then provides the objective coverage."""
    if stack.test_file_command is None:
        return True, ""
    # Resolve the canonical rel_path to its physical location (project
    # namespacing) and hand the command a repo-root-relative path.
    phys = _workspace_path(repo_root, test_path, project_id)
    if phys is None or not phys.is_file():
        return False, f"plan test not found: {test_path}"
    try:
        rel = str(phys.relative_to(repo_root))
    except ValueError:
        rel = str(phys)
    try:
        argv = stack.test_file_command(repo_root, rel)
        p = subprocess.run(argv, cwd=str(repo_root), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=240, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"could not run test {test_path}: {exc}"
    out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", (p.stdout or "") + "\n" + (p.stderr or ""))
    if p.returncode == 0:
        return True, ""
    fails = [l for l in out.splitlines()
             if re.search(r"FAIL|Error[:\s]|error TS|AssertionError|expected|"
                          r"Cannot find|Does the file exist|Traceback", l)]
    return False, "\n".join(fails[:20]) or out[-1500:]


def _default_build_check(repo_root: Path, only_test: Optional[str] = None,
                         project_id: str = "default",
                         stack: Optional[_StackContext] = None) -> "tuple[bool, str]":
    """OBJECTIVE correctness gate. With only_test, run just that one plan test
    (per-task green gate) via the stack adapter. Otherwise run the stack's real
    validation plan (build + full test suite) -- the integration gate. Returns
    (green, actionable_error_text); the LLM only ever sees real diagnostics.
    Same machinery as _verify_g4_build."""
    if only_test:
        ctx = stack or _resolve_stack(repo_root)
        return _run_single_test(repo_root, only_test, ctx, project_id)
    try:
        from .stacks import detect_profile
        from .validation import build_validation_plan, run_validation
        profile = detect_profile(repo_root)
        plan = build_validation_plan(repo_root, profile)
        if not (plan.get("can_validate_build") and plan.get("can_validate_tests")):
            # Cannot verify here -> don't spin; the gate's own check is the wall.
            return True, ""
        result = run_validation(repo_root, plan)
        results = result.get("results", {})
        b, t = results.get("build", {}), results.get("test", {})
        if b.get("status") == "passed" and t.get("status") == "passed":
            return True, ""
        errs: list[str] = []
        for v in (result.get("violations") or [])[:30]:
            f = v.get("file") or v.get("path") or ""
            ln = v.get("line")
            errs.append(f"{f}{f':{ln}' if ln else ''} {v.get('code','')} {v.get('message','')}".strip())
        detail = "\n".join(e for e in errs if e) or (
            ((b.get("output") or "") + "\n" + (t.get("output") or ""))[-3000:])
        return False, detail or "build or tests failed"
    except Exception as exc:  # never bypass on error -- report not-green
        return False, f"build check error: {type(exc).__name__}: {exc}"


def run_subagent_driven_build(
    repo_root: Path,
    adapter: Any,
    *,
    reviewer_adapter: Any = None,
    enforcement_provider: Any = None,
    emit: Optional[Callable[[dict], None]] = None,
    project_id: str = "default",
    signed_gates: Optional[list[int]] = None,
    prompt: str = "",
    governance_frame: str = "",
    repair_cycles: int | None = None,
    run_agent: Optional[RunAgent] = None,
    build_check: Optional[BuildCheck] = None,
) -> LoopResult:
    """Execute Gate 4 by EXECUTING THE SIGNED PLAN, test-first and iteratively.

    This is the bundled executing-plans / TDD pattern: the plan authored a
    failing acceptance TEST per task; the build drives each to green ONE AT A
    TIME, so errors never pile up into an unfixable end-of-build heap.

    Phases:
      1. PER TASK (task-level, TDD) -- for each plan task: an implementer makes
         THAT task's plan-authored test pass (never writing a parallel test),
         then an OBJECTIVE per-task gate runs just that test and a bounded fixer
         drives it green before the next task.
      2. INTEGRATION -- run the FULL validation plan (build + whole suite);
         a bounded, batched fixer clears any cross-task breakage to green.
      3. REVIEW -- independent spec + code-quality reviewers (on the separate
         critic_adapter when configured) inspect the GREEN product; a FAIL
         triggers one fixer pass, then green is re-verified.
      4. EVIDENCE -- write the Build Evidence artifact with the real numbers.

    Returns a LoopResult so GateOrchestrator._run_gate treats it like any G4 run
    (its _verify_g4_build hard wall still independently confirms green).
    """
    emit = emit or (lambda _e: None)
    signed_gates = list(signed_gates or [])
    cycles = max(1, int(repair_cycles)) if repair_cycles else resolve_repair_cycle_budget()
    per_task_cycles = resolve_build_task_fix_cycles()
    reviewer = reviewer_adapter if reviewer_adapter is not None else adapter
    usage: dict = {"in": None, "out": None}
    run = run_agent or _default_run_agent(
        repo_root, enforcement_provider, emit, project_id, signed_gates,
        usage=usage)
    stack = _resolve_stack(repo_root)
    if build_check is not None:
        check = build_check
    else:
        def check(r: Path, only_test: Optional[str] = None) -> "tuple[bool, str]":
            return _default_build_check(r, only_test, project_id=project_id,
                                        stack=stack)

    tasks = decompose_tasks(repo_root, prompt, project_id)
    context = _task_context(repo_root, prompt, project_id)
    impl_sys = _implementer_system_prompt(governance_frame, stack)
    spec_sys = _reviewer_system_prompt("spec")
    cq_sys = _reviewer_system_prompt("code")

    plan_driven = any(t.test for t in tasks)
    emit({"type": "system",
          "text": f"Building the product task-by-task ({len(tasks)} step(s)), "
                  "test-first, driving each to green before the next."})

    summary: list[str] = []
    calls = 0

    # PHASE 1 -- per task, test-first, drive to green BEFORE the next task.
    for task in tasks:
        emit({"type": "system", "text": f"Building: {task.name}"})
        run("implementer", adapter, impl_sys,
            _implementer_message(task, context, repo_root, stack, project_id))
        calls += 1
        # OBJECTIVE per-task green gate (only when the plan gave this task a test).
        if task.test:
            ok, errs = check(repo_root, task.test)
            prev = None
            for _ in range(per_task_cycles):
                if ok:
                    break
                stalled = hash(errs.strip()) == prev
                prev = hash(errs.strip())
                emit({"type": "system", "text": f"Getting “{task.name}” to pass its test."})
                feedback = (_stall_directive(1) + "\n\n" + errs) if stalled else errs
                run("fixer", adapter, impl_sys,
                    _implementer_message(task, context, repo_root, stack,
                                         project_id, fix_feedback=feedback))
                calls += 1
                ok, errs = check(repo_root, task.test)
            summary.append(f"{task.id} test_green={ok}")
        else:
            summary.append(f"{task.id}: drafted (no plan test)")

    # PHASE 2 -- INTEGRATION: full build + whole suite to green (cross-task).
    # Same input -> same model -> most likely the same failed output, so a
    # stalled pass (identical error signature) must CHANGE something: first the
    # strategy (narrow deep-dive on one file), then the model (the independent
    # reviewer adapter, when it differs). Never re-send an identical prompt.
    green, last_errors = check(repo_root)
    prev_sig = None
    stalls = 0
    for cycle in range(cycles):
        if green:
            break
        sig = hash(last_errors.strip())
        stalls = stalls + 1 if sig == prev_sig else 0
        prev_sig = sig
        n = len([l for l in last_errors.splitlines() if l.strip()]) if last_errors else 0
        emit({"type": "system",
              "text": f"Integrating: compiling and fixing {n} real error(s) (pass {cycle + 1})"
                      + (f" — changing approach (stall {stalls})." if stalls else ".")})
        msg = _fixer_message(last_errors, repo_root, stack)
        fixer_adapter = adapter
        if stalls >= 1:
            msg = _stall_directive(stalls) + "\n\n" + msg
        if stalls >= 2 and reviewer is not adapter:
            fixer_adapter = reviewer  # different model on the same evidence
        run("fixer", fixer_adapter, impl_sys, msg)
        calls += 1
        green, last_errors = check(repo_root)
    emit({"type": "system",
          "text": "The product compiles and all tests pass."
                  if green else "Build still has errors after the repair budget."})
    summary.append(f"integration_green={green}")

    # PHASE 3 -- independent review on the GREEN product (spec, then quality).
    if green:
        for kind, sys_prompt in (("spec", spec_sys), ("code", cq_sys)):
            verdict = run(f"{kind}-reviewer", reviewer, sys_prompt,
                          _final_review_message(kind, prompt, stack))
            calls += 1
            if parse_verdict(verdict) == "FAIL":
                label = "requirements" if kind == "spec" else "quality"
                emit({"type": "system", "text": f"Applying independent {label} review feedback."})
                run("fixer", adapter, impl_sys,
                    _fixer_message("Independent reviewer feedback:\n" + verdict,
                                   repo_root, stack))
                calls += 1
                green, _ = check(repo_root)  # a review fix must not break the build
            summary.append(f"{kind}_review={parse_verdict(verdict)}")

    # PHASE 4 -- record the Build Evidence artifact with the real numbers,
    # honestly: a not-green build is recorded as not green (the sign gate will
    # refuse it; evidence must never claim otherwise).
    run("evidence", adapter, impl_sys,
        _evidence_message(repo_root, project_id, stack, green))
    calls += 1

    if usage.get("in") is not None or usage.get("out") is not None:
        summary.append(f"tokens_in={usage.get('in')} tokens_out={usage.get('out')}")
        emit({"type": "system",
              "text": f"Build used {usage.get('in') or 0:,} input / "
                      f"{usage.get('out') or 0:,} output tokens."})

    return LoopResult(
        run_id="g4-subagent-build",
        status="completed",
        final_text=f"Subagent-driven build complete (plan_driven={plan_driven}, "
                   f"green={green}).\n" + "\n".join(summary),
        tool_calls_made=calls,
        messages=[],
        error=None,
        tokens_in=usage.get("in"),
        tokens_out=usage.get("out"),
    )
