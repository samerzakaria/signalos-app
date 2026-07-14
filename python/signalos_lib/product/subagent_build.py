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
    "decompose_canonical_plan_tasks",
    "decompose_plan_tasks",
    "decompose_tasks",
    "parse_verdict",
    "parse_implementer_status",
    "run_subagent_driven_build",
    "task_dod_violations",
    "is_vacuous_test",
    "BuildCancelled",
]

import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Optional

from ..artifacts import resolve_gate_artifacts, resolve_workspace_path
from .agent_loop import AgentLoop, LoopResult
from .wiring_check import unwired_lint
from .budgets import (
    resolve_build_doc_cap,
    resolve_build_fixer_error_batch,
    resolve_build_implementer_tool_budget,
    resolve_build_max_tasks,
    resolve_build_reviewer_tool_budget,
    resolve_build_task_fix_cycles,
    resolve_build_test_embed_cap,
    resolve_repair_cycle_budget,
)

# A single subagent turn: (role, adapter, system_prompt, user_message) -> report
RunAgent = Callable[[str, Any, str, str], str]
# (repo_root, only_test=None) -> (is_green, real_error_text). The OBJECTIVE
# gate. only_test=None runs the FULL build+suite (integration); a path runs just
# that one plan test (the per-task green gate, so errors never pile up).
BuildCheck = Callable[..., "tuple[bool, str]"]


class BuildCancelled(RuntimeError):
    """Raised internally when the parent delivery cancellation is observed."""


class ProviderExecutionError(RuntimeError):
    """A G4 subagent could not execute because its provider boundary failed."""

    def __init__(self, failure_type: str, message: str) -> None:
        super().__init__(message)
        self.failure_type = failure_type


# Verdict token the reviewer must end its report with. Parsed fail-closed but
# the loop is budget-bounded, and the objective build gate is the real wall.
_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)", re.I)
_STATUS_RE = re.compile(r"\b(DONE_WITH_CONCERNS|NEEDS_CONTEXT|BLOCKED|DONE)\b")

# Per bundled-doc / artifact char caps resolve through budgets.py (operator
# env-tunable, generous defaults) -- the engine never judges a model's context
# needs with a silent hardcoded number.

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


# A relative import that points at the repo-root source tree, e.g.
# `from '../../src/store/x'` or `import('../../../src/utils/y')`.
_REL_SRC_IMPORT_RE = re.compile(
    r"""(?P<pre>(?:from\s+|import\s*\(\s*|require\s*\(\s*)['"])"""
    r"""(?P<dots>(?:\.\./)+)"""
    r"""(?P<tail>%s/[^'"]+)"""
    r"""(?P<post>['"])"""
)


def repair_test_import_depths(repo_root: Path, tasks: "list[Task]",
                              source_dir: str, project_id: str = "default") -> list:
    """Deterministically correct the relative import DEPTH of plan-authored
    tests. A plan may ship `from '../../src/...'` from a directory several levels
    deep; the correct number of `../` is COMPUTABLE from the file's depth
    relative to the repo root -- it is arithmetic, not a judgment call. We do it
    in Python so no model ever has to (delegating it to the LLM turned a fixed
    computation into a guessing game that burned weak models' fix budgets).

    Only the `../`-prefix of a repo-root source import is rewritten; assertions,
    identifiers, and everything else are byte-untouched. Returns the list of
    (rel_path, old_prefix, new_prefix) repairs made."""
    src_alt = re.escape(source_dir.strip("/")) if source_dir and source_dir != "src" else ""
    pat = _REL_SRC_IMPORT_RE.pattern % (
        f"(?:src|{src_alt})" if src_alt else "src")
    rx = re.compile(pat)
    repairs: list = []
    seen: set = set()
    for task in tasks:
        if not task.test or task.test in seen:
            continue
        seen.add(task.test)
        p = _workspace_path(repo_root, task.test, project_id)
        if p is None or not p.is_file():
            continue
        try:
            rel_dir = p.parent.resolve().relative_to(repo_root.resolve())
        except (ValueError, OSError):
            continue
        correct = "../" * len(rel_dir.parts)  # depth from repo root = # of `../`
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        def _fix(m: "re.Match") -> str:
            if m.group("dots") == correct:
                return m.group(0)
            repairs.append((task.test, m.group("dots"), correct))
            return m.group("pre") + correct + m.group("tail") + m.group("post")

        new = rx.sub(_fix, text)
        if new != text:
            try:
                p.write_text(new, encoding="utf-8")
            except OSError:
                pass
    return repairs


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
    deps: list[str] = field(default_factory=list)   # prerequisite task ids (from plan)


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
_PLAN_FIELD_RE = re.compile(r"^\*\*(Files|Test|Dependencies)(?::\*\*|\*\*:)\s*(.+)$", re.I)
_TASK_ID_RE = re.compile(r"\bT\d+(?:\.\d+)?\b")
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


# Test-shaped path detection, for deriving a canonical task's acceptance-test
# path from its declared files when the machine plan carries no explicit test.
_TEST_PATH_RE = re.compile(r"(?:\.test\.|\.spec\.|_test\.|(?:^|/)test_)")


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path.replace("\\", "/")))


def _canonical_plan_path(repo_root: Path, project_id: str) -> Optional[Path]:
    """Locate the CANONICAL machine plan ``PLAN.tasks.yaml``. It lives beside
    the rendered PLAN.md (the "Plan" artifact); we also try the project-
    namespaced canonical workspace path. None when absent."""
    candidates: list[Path] = []
    art = _artifact_by_label(repo_root, project_id, "Plan").get("Plan")
    if art is not None:
        try:
            candidates.append(art.path.parent / "PLAN.tasks.yaml")
        except (OSError, AttributeError):
            pass
    ws = _workspace_path(repo_root, "core/execution/PLAN.tasks.yaml", project_id)
    if ws is not None:
        candidates.append(ws)
    seen: set = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _canonical_task_test(raw: dict, files: list[str]) -> "tuple[str, list[str]]":
    """The acceptance-test path notion for a canonical task. The canonical
    schema carries no test field, so we derive one: prefer an explicit
    ``test`` / ``acceptance_test`` / ``acceptance_path`` key if a plan author
    supplied one (forward-compatible), else the first test-shaped entry in the
    task's declared files (that entry is then removed from the impl file list).
    Returns ``(test_path, impl_files)``. A non-path sentinel ("N/A"/"none"/
    "manual") yields no test, matching the markdown parser's own guard."""
    test = ""
    for key in ("test", "acceptance_test", "acceptance_path"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            test = val.strip()
            break
    impl_files = list(files)
    if not test:
        for f in files:
            if _is_test_path(f):
                test = f
                impl_files = [x for x in files if x != f]
                break
    if test and ("/" not in test.replace("\\", "/")
                 or re.match(r"^(n/?a|none|manual)\b", test, re.I)):
        test = ""
    return test, impl_files


def decompose_canonical_plan_tasks(repo_root: Path,
                                   project_id: str = "default") -> list[Task]:
    """Parse the CANONICAL machine plan ``PLAN.tasks.yaml`` into ordered build
    tasks (Claim 5: G4 consumes the machine plan, not only rendered markdown).
    Each build Task carries its declared implementation files, dependency ids,
    and a derived acceptance-test path. Empty list when there is no canonical
    plan or it does not parse/validate -- the caller falls back to the markdown
    parser, so the benchmark fixture's markdown-shaped plan keeps working."""
    path = _canonical_plan_path(repo_root, project_id)
    if path is None:
        return []
    try:
        from .. import plan as _plan
        doc = _plan.load_tasks(path)
    except Exception:
        return []  # invalid/absent canonical plan -> markdown fallback
    if not doc.tasks:
        return []
    # Raw dicts (by id) so an optional explicit test path the typed loader drops
    # is still available to _canonical_task_test.
    raw_by_id: dict[str, dict] = {}
    try:
        import yaml  # type: ignore[import]
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for t in (data.get("tasks") or []):
            if isinstance(t, dict) and t.get("id"):
                raw_by_id[str(t["id"])] = t
    except Exception:
        raw_by_id = {}
    tasks: list[Task] = []
    for pt in doc.tasks:
        raw = raw_by_id.get(pt.id, {})
        test, impl_files = _canonical_task_test(raw, list(pt.files))
        body = (pt.description or pt.notes or pt.title or "").strip()
        name = (pt.title or pt.id)[:70]
        tasks.append(Task(id=pt.id, name=name, text=body, files=impl_files,
                          test=test, deps=list(pt.depends_on)))
    return tasks


def decompose_plan_tasks(repo_root: Path, project_id: str = "default") -> list[Task]:
    """Parse the signed plan into ordered build tasks, each carrying its target
    source files and its acceptance TEST path. PREFERS the canonical machine
    plan ``PLAN.tasks.yaml`` (Claim 5); falls back to parsing the rendered
    PLAN.md markdown for back-compat (the benchmark fixture's plan is
    markdown-shaped, so that path must keep working). Empty list when neither
    yields a structured task list (caller falls back to acceptance
    decomposition)."""
    canonical = decompose_canonical_plan_tasks(repo_root, project_id)
    if canonical:
        return canonical
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
        deps: list[str] = []
        for bl in block:
            fm = _PLAN_FIELD_RE.match(bl.strip())
            if not fm:
                continue
            key = fm.group(1).lower()
            if key == "dependencies":
                deps = _TASK_ID_RE.findall(fm.group(2))
                continue
            paths = _BACKTICK_PATH_RE.findall(fm.group(2)) or [fm.group(2).strip()]
            if key == "files":
                files = [p.strip() for p in paths if p.strip()]
            else:
                test = paths[0].strip() if paths else ""
                # A plan may declare a non-automated verification ("N/A",
                # "manual test protocol", "none"). That is NOT a test path --
                # treating it as one made the build (and preflight) chase a
                # file literally named "N/A (...)" and burn the task's whole
                # fix budget on an unrunnable gate.
                if test and ("/" not in test.replace("\\", "/")
                             or re.match(r"^(n/?a|none|manual)\b", test, re.I)):
                    test = ""
        text = "\n".join(block).strip()
        name = (f"{tid} — {title}" if title else tid)[:70]
        tasks.append(Task(id=tid, name=name, text=text, files=files, test=test,
                          deps=deps))
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
    cap = resolve_build_doc_cap()
    impl = _strip_template_wrapper(_load_bundled("implementer"))[:cap]
    tdd = _load_bundled("tdd")[:cap]
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
        parts += ["", "## Governance frame (binding forbidden rules)", governance_frame[:cap]]
    return "\n".join(parts)


def _reviewer_system_prompt(kind: str) -> str:
    key = "spec_reviewer" if kind == "spec" else "code_reviewer"
    body = _strip_template_wrapper(_load_bundled(key))
    role = ("spec-compliance reviewer" if kind == "spec"
            else "code-quality reviewer")
    return "\n".join([
        f"You are the {role} subagent in a SignalOS-governed build. You have "
        "READ-ONLY tools only (read_file, search_files, list_directory) -- no "
        "write, edit, or command tools exist for you. You are a REVIEWER: you "
        "cannot and must not modify code -- the implementer fixes any issues you "
        "find. Verify by READING the actual code on disk, never by trusting the "
        "implementer's report.",
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
                    parts.append(f"## {label} ({art.rel_path})\n{txt[:resolve_build_doc_cap()]}")
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


def _impl_digest(repo_root: Path, task: Task, project_id: str) -> str:
    """A short summary of the task's target implementation files (path + head),
    for the dispute arbiter -- enough to reason about impl vs test fault without
    the builder's failed-attempt transcript."""
    out: list[str] = []
    for rel in (getattr(task, "files", None) or [])[:3]:
        p = _workspace_path(repo_root, rel, project_id)
        try:
            if p and p.is_file():
                head = p.read_text(encoding="utf-8", errors="replace")[:800]
                out.append(f"// {rel}\n{head}")
            else:
                out.append(f"// {rel} (not written)")
        except OSError:
            out.append(f"// {rel} (unreadable)")
    return "\n\n".join(out)


def _diagnose_deadlock(repo_root: Path, task: Task, errs: str, reviewer, adapter,
                       run, stack, project_id: str) -> dict:
    """When a task deadlocks (fix budget exhausted, still red), decide whether the
    TEST is broken (dispute) or the impl is just wrong. Deterministic health
    check first (zero LLM); then, only if a genuinely DIFFERENT model is wired, a
    fresh-context second-opinion classify. Never edits the test. Returns
    {disputed, reason, source, used_arbiter}."""
    from .test_dispute import (arbiter_messages, deterministic_test_health,
                               parse_arbiter_verdict, record_dispute)
    test_src = _read_test(repo_root, task.test, project_id)
    health = deterministic_test_health(test_src, errs)
    if health["broken"]:
        record_dispute(repo_root, task.id, task.name, health["reason"],
                       "deterministic", task.test)
        return {"disputed": True, "reason": health["reason"],
                "source": "deterministic", "used_arbiter": False}
    # Second opinion only when a genuinely different model/vendor is configured
    # (reviewer is adapter when none was) -- a same-model self-diagnosis inherits
    # the builder's anchoring and adds spend for little signal.
    if reviewer is not adapter:
        sysm, usrm = arbiter_messages(
            task.name, test_src, errs, _impl_digest(repo_root, task, project_id))
        try:
            verdict_text = run("test-arbiter", reviewer, sysm, usrm)
        except ProviderExecutionError:
            raise
        except Exception:
            verdict_text = ""
        v = parse_arbiter_verdict(verdict_text)
        if v["test_broken"]:
            record_dispute(repo_root, task.id, task.name, v["reason"],
                           "arbiter", task.test)
            return {"disputed": True, "reason": v["reason"],
                    "source": "arbiter", "used_arbiter": True}
        return {"disputed": False, "reason": v["reason"],
                "source": "arbiter", "used_arbiter": True}
    return {"disputed": False, "reason": health["reason"],
            "source": "deterministic", "used_arbiter": False}


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
            "- The test file is READ-ONLY. Do NOT edit it at all -- not its "
            "assertions, not its imports. Its import paths are already correct; "
            "make the test pass by implementing the modules it imports "
            f"{src_hint}. Target implementation files: {', '.join(task.files) or src_hint}.",
            "- Run this one test to green via run_command.",
            "",
            "```",
            # The FULL test, never truncated: a capped embed hid part of the
            # signed spec (observed: a 4.7k-char plan test lost its validation
            # assertions past a 3.5k cap, and the builder was graded on
            # expectations it could not see). Plan tests are small; the real
            # context risk lives in command output, which is capped separately.
            test_src[:resolve_build_test_embed_cap()],
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
        "- The tests are the SIGNED SPEC and are READ-ONLY -- never edit, delete, "
        "weaken, or trivially satisfy a test (imports included; their paths are "
        "already correct). If a test imports a module that does not exist, CREATE "
        f"that module as a REAL, functional implementation {src_hint}.",
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
                      green: bool, repair_metrics: Optional[dict] = None) -> str:
    rel = _evidence_rel_path(repo_root, project_id)
    target = f"`{rel}`" if rel else "the Build Evidence gate artifact"
    status_line = (
        "The objective build+test run is GREEN."
        if green else
        "The objective build+test run is NOT green -- the evidence MUST say so. "
        "Do not claim success; record the failing state exactly as it is."
    )
    lines = [
        "# Record the build evidence",
        status_line,
        "Run the project's build and test commands "
        f"({stack.build_and_test_hint}) once more via run_command to read the "
        f"real numbers, then write {target} with CONCRETE values: source/test "
        "files created or changed, the exact commands run, whether the build is "
        "clean (yes/no), and the test result as pass/total. Record honestly -- "
        "including failures. No TBD/TODO/placeholders, no `{{...}}`, no `[DATE]`.",
    ]
    if repair_metrics:
        p = repair_metrics.get("repairs_by_phase", {}) or {}
        lines.append(
            "Also record the build's self-repair effort under a "
            "`Repair attempts:` line, verbatim -- total "
            f"{repair_metrics.get('repair_attempts', 0)} "
            f"(per-task fixes {p.get('per_task', 0)}, integration fixes "
            f"{p.get('integration', 0)}, review fixes {p.get('review', 0)}). "
            "A build that reached green with 0 repairs is stronger than one that "
            "needed several -- write the real count, never round it down.")
    lines.append(f"Work from: {repo_root}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Traceability matrix -- the build's LIVE decision state, persisted on every
# change. One row per plan task: criterion/task -> declared files -> test ->
# result -> attempts -> dependency disposition. The loop's control decisions
# (skip / block / fail-stop / proceed) are READS of this object, and the
# on-disk artifact is the same object's latest snapshot -- so the audit trail
# can never drift from what the loop actually decided, and a crashed run
# leaves an exact, current matrix behind.
# ---------------------------------------------------------------------------

class _TraceMatrix:
    REL_PATH = ".signalos/traceability.json"

    def __init__(self, repo_root: Path, tasks: "list[Task]"):
        self.repo_root = Path(repo_root)
        self.rows: "dict[str, dict]" = {
            t.id: {
                "task": t.id,
                "name": t.name,
                "deps": list(t.deps),
                "files_declared": list(t.files),
                "test": t.test or None,
                "status": "pending",   # pending | green | pre-existing-green |
                                        # failed | blocked | drafted-no-test
                "attempts": 0,
                "blocked_by": [],
            }
            for t in tasks
        }
        self.persist()

    def set(self, task_id: str, **fields: Any) -> None:
        row = self.rows.get(task_id)
        if row is None:
            return
        row.update(fields)
        self.persist()

    def bump_attempts(self, task_id: str) -> None:
        row = self.rows.get(task_id)
        if row is not None:
            row["attempts"] = int(row.get("attempts") or 0) + 1
            self.persist()

    def failed_or_blocked(self, ids: "list[str]") -> "list[str]":
        return [i for i in ids
                if self.rows.get(i, {}).get("status") in ("failed", "blocked")]

    def ids_with_status(self, *statuses: str) -> "list[str]":
        return [i for i, r in self.rows.items() if r.get("status") in statuses]

    def all_green(self) -> bool:
        return all(r.get("status") in ("green", "pre-existing-green",
                                       "drafted-no-test")
                   for r in self.rows.values())

    def snapshot(self, **extra: Any) -> dict:
        return {"schema_version": "signalos.traceability.v1",
                "rows": list(self.rows.values()), **extra}

    def persist(self, **extra: Any) -> None:
        """Best-effort write; the matrix must never break the build."""
        try:
            import json as _json
            path = self.repo_root / self.REL_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            # files_present resolved at write time (cheap, always current)
            for r in self.rows.values():
                r["files_present"] = [
                    f for f in r.get("files_declared", [])
                    if (self.repo_root / f).is_file()
                ]
            path.write_text(_json.dumps(self.snapshot(**extra), indent=2) + "\n",
                            encoding="utf-8")
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Reviewer read-only tool set (enforced by the tool schema, not prompt text)
#
# A REVIEWER must never modify code -- the implementer fixes what it finds. But
# AgentLoop advertises the FULL AGENT_TOOLS (write_file / edit_file /
# run_command) to every loop it runs, so "read-only" lived only in the
# reviewer's prompt text and a reviewer COULD write. AgentLoop builds its tool
# list from the module-global AGENT_TOOLS and exposes no per-loop tool override,
# so the smallest surface we own is the ADAPTER: wrap the reviewer's adapter and
# strip every non-read-only tool from the advertised set BEFORE the model sees
# it. The reviewer is then physically unable to call write_file / edit_file /
# run_command -- those tools do not exist in the schema it receives.
#
# INTEGRATION FLAG: a truly restricted, verification-ONLY run_command for
# reviewers (rather than dropping run_command entirely) needs an agent_loop
# capability we do not own -- a per-loop tool set, or a read-only execution mode
# on AgentLoop. Until then reviewers verify by READING the code on disk
# (read_file / search_files / list_directory), which is exactly what the
# reviewer contract asks for; command execution stays with the write-capable
# implementer/fixer loops.
_REVIEWER_READONLY_TOOLS: frozenset = frozenset(
    {"read_file", "search_files", "list_directory"})


def _tool_name(tool: Any) -> str:
    """Function name of an OpenAI-shaped tool dict (best-effort, '' on shape
    mismatch so an unrecognized tool is filtered OUT, fail-closed)."""
    if isinstance(tool, dict):
        fn = tool.get("function")
        if isinstance(fn, dict):
            return str(fn.get("name") or "")
        return str(tool.get("name") or "")
    return ""


def _filter_readonly_tools(tools: Optional[list]) -> Optional[list]:
    """Keep only read-only tools from an advertised tool list; None stays None
    (a text-only turn advertises no tools)."""
    if not tools:
        return tools
    return [t for t in tools if _tool_name(t) in _REVIEWER_READONLY_TOOLS]


class _ReadOnlyReviewerAdapter:
    """Adapter proxy that hands a reviewer's AgentLoop ONLY read-only tools.

    Delegates every attribute/behaviour to the wrapped adapter, but intercepts
    chat() to strip write/command tools from the advertised set. This enforces
    the reviewer's read-only contract through the actual tool schema the model
    receives -- not prompt text alone -- so a reviewer cannot write files or run
    commands even if it tries."""

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)

    @property
    def supports_tool_calls(self) -> bool:
        return bool(getattr(self._inner, "supports_tool_calls", True))

    def chat(self, *, messages, tools=None, tool_choice=None, **kwargs):
        readonly = _filter_readonly_tools(tools)
        if tool_choice is not None:
            return self._inner.chat(messages=messages, tools=readonly,
                                    tool_choice=tool_choice, **kwargs)
        return self._inner.chat(messages=messages, tools=readonly, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes not defined on this proxy (e.g. model,
        # supports_streaming) -> delegate to the real adapter.
        return getattr(object.__getattribute__(self, "_inner"), name)


def _is_reviewer_role(role: str) -> bool:
    """Reviewer roles get the read-only tool set. Mirrors the reviewer tool
    budget's own role test (role ends with 'reviewer')."""
    return role.endswith("reviewer")


def _loop_adapter_for_role(role: str, adapter: Any) -> Any:
    """The adapter handed to the AgentLoop for *role*: reviewers get a read-only
    proxy (no write_file / edit_file / run_command); implementers, fixers, and
    the evidence pass keep the real write-capable adapter."""
    if _is_reviewer_role(role):
        return _ReadOnlyReviewerAdapter(adapter)
    return adapter


def _default_run_agent(
    repo_root: Path,
    enforcement_provider: Any,
    emit: Callable[[dict], None],
    project_id: str,
    signed_gates: list[int],
    usage: Optional[dict] = None,
    parent_run_id: str = "",
    cancel_check: Optional[Callable[[], bool]] = None,
) -> RunAgent:
    """Real dispatcher: each call is a FRESH AgentLoop (fresh run_id, fresh
    context) -- the "fresh subagent per task/review" the bundled skill requires,
    with a bounded tool budget so no single conversation blows the context.
    When *usage* is given, per-run token totals accumulate into it
    ({"in": int|None, "out": int|None}) for build-level cost accounting."""
    impl_budget = resolve_build_implementer_tool_budget()
    rev_budget = resolve_build_reviewer_tool_budget()

    def run(role: str, adapter: Any, system_prompt: str, user_message: str) -> str:
        if cancel_check is not None and cancel_check():
            raise BuildCancelled("parent delivery cancellation requested")
        limit = rev_budget if role.endswith("reviewer") else impl_budget
        loop = AgentLoop(
            # Reviewers get a READ-ONLY tool set (no write_file / edit_file /
            # run_command) via the adapter proxy -- enforced by the schema the
            # model receives, not just the reviewer prompt.
            adapter=_loop_adapter_for_role(role, adapter),
            repo_root=repo_root,
            enforcement_provider=enforcement_provider,
            run_id=(f"{parent_run_id[:48]}-g4-{uuid.uuid4().hex[:12]}"
                    if parent_run_id else None),
            emit=emit,
            cancel_check=cancel_check,
            execution_context="delivery",
            active_gate="G4",
            project_id=project_id,
            signed_gates=list(signed_gates),
            tool_call_limit=limit,
        )
        res = loop.run(system_prompt, user_message)
        if res.status == "cancelled":
            raise BuildCancelled("parent delivery cancelled during G4 subagent")
        if usage is not None:
            if res.tokens_in is not None:
                usage["in"] = (usage.get("in") or 0) + res.tokens_in
            if res.tokens_out is not None:
                usage["out"] = (usage.get("out") or 0) + res.tokens_out
        failure_type = str(getattr(res, "failure_type", "") or "")
        if failure_type.startswith("provider-"):
            raise ProviderExecutionError(
                failure_type,
                res.error or f"{role} provider execution failed",
            )
        # Fix: do NOT silently swallow a "narrated, wrote nothing" / truncated
        # outcome. The loop now refuses to call a no-tool narration turn (or a
        # cut-off max_tokens turn) "completed"; surface that here so a step that
        # described work instead of performing it is distinguishable from real
        # work. Roles that are supposed to change files (implementer/fixer) also
        # flag a run that landed no write; reviewers legitimately emit only text.
        writes_expected = role in ("implementer", "fixer")
        if res.status in ("stalled_no_tool", "max_tokens"):
            emit({"type": "system",
                  "text": f"The {role} step did not perform tool work "
                          f"(outcome: {res.status}); it described the work "
                          "instead of doing it. Treating the step as incomplete."})
        elif writes_expected and res.wrote_no_files:
            emit({"type": "system",
                  "text": f"The {role} step made no file changes -- no product "
                          "was written this pass."})
        return res.final_text or ""
    return run


def _run_single_test(repo_root: Path, test_path: str, stack: _StackContext,
                     project_id: str = "default") -> "tuple[bool, str]":
    """Run ONE test file via the stack adapter's single-test command (the
    per-task green gate). Test-runner-agnostic result parsing: exit code rules;
    failure lines are extracted best-effort for the fixer.

    When the adapter has NO single-test runner we must NOT fake green: returning
    (True, '') here made the caller stamp the task "pre-existing-green" and skip
    the implementer, so a task could be marked done with nothing built. Instead
    we return an honest "cannot verify" (False, <reason>) so the caller proceeds
    to implement rather than treating an unverified test as already-passing; the
    real gate is the full-suite validation in PHASE 2 (run_validation)."""
    if stack.test_file_command is None:
        return False, ("no per-test runner for this stack; cannot confirm this "
                       "test is green -- proceeding to implement (the full-suite "
                       "validation is the real gate)")
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


# ---------------------------------------------------------------------------
# Per-task DEFINITION-OF-DONE hard gate ("grade only what you enforce")
# ---------------------------------------------------------------------------
#
# A task's test passing is necessary but NOT sufficient. The Definition of Done
# also demands a quality bar, and this gate BLOCKS a task from being marked
# "done" (green) until that bar is met -- it is not advisory lint. The checks
# are deterministic (no LLM) and deliberately GENEROUS so genuine work is never
# false-failed; they fire only on egregious violations:
#
#   * TEST RIGOR   -- the acceptance test actually asserts behaviour (a vacuous
#                     always-true test proves nothing -> the task is not done).
#   * DEAD CODE    -- a declared implementation file that NOTHING references is
#                     unwired dead code (wire it in or remove it).
#   * COMPLEXITY   -- no single function blows the complexity / length ceiling.
#   * DUPLICATION  -- two declared files are not near-identical copies.
#   * A11Y         -- blatantly unlabeled inputs / nameless buttons are refused.
#
# Test-rigor violations hard-block immediately (the signed test is read-only, a
# fixer cannot rewrite it); the impl-quality violations are driven out by a
# bounded fixer loop that must not break the test.

def _dod_int_env(name: str, default: int) -> int:
    try:
        v = int(str(os.environ.get(name, "")).strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _dod_max_function_lines() -> int:
    return _dod_int_env("SIGNALOS_DOD_MAX_FUNCTION_LINES", 200)


def _dod_max_complexity() -> int:
    return _dod_int_env("SIGNALOS_DOD_MAX_COMPLEXITY", 40)


def _dod_dup_similarity() -> float:
    try:
        v = float(str(os.environ.get("SIGNALOS_DOD_DUP_SIMILARITY", "")).strip())
        return v if 0.0 < v <= 1.0 else 0.95
    except (TypeError, ValueError):
        return 0.95


_ASSERT_RE = re.compile(r"\b(?:expect|assert)\s*\(|\.\s*should\b")
# An assertion whose subject AND expected are both literals -> proves nothing.
_TRIVIAL_ASSERT_RE = re.compile(
    r"expect\(\s*(?:true|false|null|undefined|-?\d+(?:\.\d+)?|"
    r'"[^"]*"|\'[^\']*\')\s*\)\s*\.\s*(?:toBe|toEqual|toStrictEqual|'
    r"toBeTruthy|toBeFalsy|toBeDefined|toBeNull|toBeGreaterThan|toBeLessThan)"
    r"\(\s*(?:true|false|null|undefined|-?\d+(?:\.\d+)?|"
    r'"[^"]*"|\'[^\']*\')?\s*\)')
_TEST_BLOCK_RE = re.compile(r"\b(?:it|test)\s*\(")


def _strip_noncode(src: str) -> str:
    """Blank out comments and string/template literal CONTENTS so brace/branch
    scans do not trip on punctuation inside strings or comments."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"//[^\n]*", " ", src)
    src = re.sub(r'"(?:\\.|[^"\\])*"', '""', src)
    src = re.sub(r"'(?:\\.|[^'\\])*'", "''", src)
    src = re.sub(r"`(?:\\.|[^`\\])*`", "``", src)
    return src


_FUNC_HEAD_RE = re.compile(r"=>\s*\{|\bfunction\b|\)\s*\{")
_BRANCH_RE = re.compile(r"\b(?:if|for|while|case|catch)\b|&&|\|\|")


def is_vacuous_test(test_src: str) -> bool:
    """True when a test has test blocks but NO genuine behavioural assertion:
    zero assertions, or every assertion is a literal-vs-literal tautology
    (``expect(true).toBe(true)``). Deterministic; a real assertion over a
    variable/call subject is never flagged."""
    if not test_src or not _TEST_BLOCK_RE.search(test_src):
        return False  # not a test file (or no test blocks) -> not our concern
    asserts = _ASSERT_RE.findall(test_src)
    if not asserts:
        return True
    expect_calls = re.findall(r"expect\s*\([^;]*?\)\s*\.\s*[A-Za-z]", test_src)
    if expect_calls:
        trivial = _TRIVIAL_ASSERT_RE.findall(test_src)
        if len(trivial) >= len(expect_calls):
            return True  # every expect(...) is a literal tautology
    return False


def _function_complexity(src: str) -> "tuple[int, int]":
    """(max_branches_in_a_function, max_function_line_count) via a brace-matched
    scan. Overlapping matches (an ``if (...) {`` also matches) only ADD smaller
    inner bodies; taking the MAX still yields the outermost function, so the
    proxy is conservative (never over-reports the top function)."""
    code = _strip_noncode(src)
    n = len(code)
    max_branch = 0
    max_lines = 0
    for m in _FUNC_HEAD_RE.finditer(code):
        brace = code.find("{", m.start())
        if brace == -1:
            continue
        depth = 0
        j = brace
        while j < n:
            c = code[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = code[brace:j + 1]
        branches = len(_BRANCH_RE.findall(body))
        ternary = (body.count("?") - body.count("?.") - 2 * body.count("??"))
        branches += max(0, ternary)
        max_branch = max(max_branch, branches)
        max_lines = max(max_lines, body.count("\n") + 1)
    return max_branch, max_lines


def _a11y_issues(src: str) -> list[str]:
    """Blatant a11y violations in JSX: an input with no name/label mechanism, or
    a button with no accessible name. Conservative -- a dynamic ``{...}`` child
    or any labelling attribute clears it (the render-based UX acceptance test is
    the thorough a11y check; this only catches obvious per-file misses)."""
    issues: list[str] = []
    for m in re.finditer(r"<input\b([^>]*?)/?>", src):
        attrs = m.group(1)
        if re.search(r"type\s*=\s*[\"'](?:hidden|submit|button)[\"']", attrs):
            continue
        if not re.search(r"\b(?:id|aria-label|aria-labelledby|placeholder|name)\b",
                         attrs):
            issues.append("an <input> has no label/id/aria-label (a11y)")
            break
    for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", src, re.S):
        attrs, inner = m.group(1), m.group(2)
        if re.search(r"aria-label|aria-labelledby|title", attrs):
            continue
        text = re.sub(r"<[^>]+>", "", inner)
        if not text.strip() and "{" not in inner:
            issues.append("a <button> has no accessible name (text/aria-label)")
            break
    return issues


_IMPORT_PATH_TMPL = r"""(?:from|import|require)\s*\(?\s*[\"'][^\"']*%s[\"']"""


def _dead_impl_files(repo_root: Path, impl_files: list, source_dir: str,
                     project_id: str) -> list:
    """Declared implementation files that NOTHING in the source tree references
    -- neither by an import of their path nor by use of their module name. Zero
    inbound references == dead/unwired code. Conservative on purpose: an
    entry/index file or any referenced module is never flagged (the false-
    positive class that got the old static wiring GATE demoted)."""
    src = Path(repo_root) / source_dir
    if not src.is_dir():
        return []
    corpus: dict = {}
    for p in src.rglob("*"):
        if p.is_file() and p.suffix in _CODE_SUFFIXES:
            try:
                corpus[p.resolve()] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    dead: list = []
    for rel in impl_files:
        p = _workspace_path(repo_root, rel, project_id)
        if p is None or not p.is_file():
            continue
        target = p.resolve()
        stem = Path(rel).stem
        if stem in ("index", "main", "App", "app"):
            continue  # entry/index files are referenced by the tool/host
        path_re = re.compile(_IMPORT_PATH_TMPL % re.escape(stem))
        name_re = re.compile(r"\b%s\b" % re.escape(stem))
        referenced = False
        for q, text in corpus.items():
            if q == target:
                continue
            if path_re.search(text) or name_re.search(text):
                referenced = True
                break
        if not referenced:
            dead.append(rel)
    return dead


def _duplicate_module_violations(existing: list) -> list:
    """Near-duplicate declared implementation files (line-set Jaccard over the
    dup-similarity threshold). Catches a task that forked a parallel copy of a
    module instead of reusing one."""
    texts: list = []
    for rel, p in existing:
        try:
            lines = [ln.strip() for ln in
                     p.read_text(encoding="utf-8", errors="replace").splitlines()
                     if ln.strip()]
        except OSError:
            continue
        texts.append((rel, set(lines), len(lines)))
    out: list = []
    thresh = _dod_dup_similarity()
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            (ra, sa, na), (rb, sb, nb) = texts[i], texts[j]
            if na < 8 or nb < 8:
                continue
            union = len(sa | sb)
            jac = (len(sa & sb) / union) if union else 0.0
            if jac > thresh:
                out.append(f"{ra} and {rb} are near-duplicates "
                           f"({int(jac * 100)}% identical) -- consolidate them")
    return out


def task_dod_violations(repo_root: Path, task: "Task", *, source_dir: str = "src",
                        project_id: str = "default") -> list:
    """Deterministic Definition-of-Done violations for a task's IMPLEMENTATION
    files (dead code, complexity, duplication, a11y). Empty == the impl meets
    the bar. Test rigor is checked separately (the test is read-only). Operates
    only on files that exist on disk, so it is a no-op for a task whose declared
    files were never written."""
    impl_files = [f for f in (task.files or []) if not _is_test_path(f)]
    existing: list = []
    for rel in impl_files:
        p = _workspace_path(repo_root, rel, project_id)
        if p is not None and p.is_file():
            existing.append((rel, p))

    violations: list = []
    max_lines_ceil = _dod_max_function_lines()
    max_cx_ceil = _dod_max_complexity()
    for rel, p in existing:
        try:
            code = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        branches, lines = _function_complexity(code)
        if branches > max_cx_ceil:
            violations.append(f"{rel}: a function is too complex (complexity "
                              f"{branches} > {max_cx_ceil}) -- split it up")
        if lines > max_lines_ceil:
            violations.append(f"{rel}: a function is too long ({lines} lines > "
                              f"{max_lines_ceil}) -- break it into smaller units")
        for issue in _a11y_issues(code):
            violations.append(f"{rel}: {issue}")

    for rel in _dead_impl_files(repo_root, [r for r, _ in existing], source_dir,
                                project_id):
        violations.append(f"{rel}: dead/unwired code -- nothing imports or uses "
                          "it. Wire it into the app, or remove it")
    violations.extend(_duplicate_module_violations(existing))
    return violations


def _dod_fixer_message(task: "Task", violations: list, repo_root: Path,
                       stack: "_StackContext") -> str:
    src_hint = f"under {stack.source_dir}/**"
    return "\n".join([
        f"# Definition of Done not yet met for task {task.id}: {task.name}",
        "The task's test passes, but the code does not yet meet the quality bar "
        "required to mark it done. Fix these in the real implementation "
        f"{src_hint} (the tests are the signed spec and are READ-ONLY -- never "
        "edit, weaken, or delete them):",
        *[f"- {v}" for v in violations],
        "",
        "Then re-run the task's test via run_command and confirm it still passes.",
        f"Work from: {repo_root}",
    ])


def _enforce_task_dod(*, task: "Task", repo_root: Path, stack: "_StackContext",
                      project_id: str, run: RunAgent, adapter: Any,
                      impl_sys: str, check: BuildCheck, cycles: int,
                      emit: Callable[[dict], None]) -> "tuple[bool, str, int]":
    """Drive a task's Definition of Done to GREEN. Returns
    ``(ok, detail, repairs_made)``. A vacuous acceptance test hard-blocks (the
    read-only spec cannot be rewritten); impl-quality violations get a bounded
    fixer loop that must not break the test."""
    repairs = 0
    test_src = _read_test(repo_root, task.test, project_id)
    if test_src and is_vacuous_test(test_src):
        return (False,
                "the acceptance test is vacuous (no genuine behavioural "
                "assertion) -- the task cannot be 'done' on a test that proves "
                "nothing", repairs)
    viol = task_dod_violations(repo_root, task, source_dir=stack.source_dir,
                               project_id=project_id)
    for _ in range(max(1, cycles)):
        if not viol:
            return True, "", repairs
        emit({"type": "system",
              "text": f"Meeting Definition of Done for “{task.name}”: "
                      + "; ".join(viol[:4])})
        run("fixer", adapter, impl_sys,
            _dod_fixer_message(task, viol, repo_root, stack))
        repairs += 1
        if task.test:
            ok_test, _ = check(repo_root, task.test)
            if not ok_test:
                return (False, "a Definition-of-Done fix broke the acceptance "
                        "test", repairs)
        viol = task_dod_violations(repo_root, task, source_dir=stack.source_dir,
                                   project_id=project_id)
    if viol:
        return False, "; ".join(viol[:4]), repairs
    return True, "", repairs


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
    parent_run_id: str = "",
    cancel_check: Optional[Callable[[], bool]] = None,
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
        usage=usage, parent_run_id=parent_run_id, cancel_check=cancel_check)
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

    # Self-repair accounting (panel ask): make "converged in 0 repairs"
    # distinguishable from "needed N", so a retry-heavy (shaky) build cannot hide
    # behind a green outcome. A FIXER dispatch is a repair; the initial
    # implementer draft is NOT. Counted per phase, totaled into repair_attempts,
    # and surfaced in three places a downstream grader can read: the machine-
    # readable traceability snapshot, the run summary / final_text, and
    # BUILD_EVIDENCE. Informational ONLY -- it never changes control flow, and a
    # build that is green after its tasks does zero extra work (every counter
    # stays 0, no extra dispatch, no extra check).
    per_task_repairs = 0
    integration_repairs = 0
    review_repairs = 0

    def _repair_metrics() -> dict:
        return {
            "repair_attempts": (per_task_repairs + integration_repairs
                                + review_repairs),
            "repairs_by_phase": {
                "per_task": per_task_repairs,
                "integration": integration_repairs,
                "review": review_repairs,
            },
        }

    def _repair_summary_line() -> str:
        m = _repair_metrics()
        p = m["repairs_by_phase"]
        return (f"repair_attempts={m['repair_attempts']} "
                f"(per_task={p['per_task']}, integration={p['integration']}, "
                f"review={p['review']})")

    matrix = _TraceMatrix(repo_root, tasks)

    # PHASE 0a -- deterministic spec repair: a plan test may ship with a wrong
    # relative import depth (e.g. `../../src/...` from a 5-deep directory). The
    # correct depth is arithmetic, so we fix it in Python BEFORE the run -- the
    # model never has to (and must not: the spec is read-only to it). Runs only
    # on the real path (a custom build_check means a unit test simulating state).
    if build_check is None:
        repairs = repair_test_import_depths(
            repo_root, tasks, stack.source_dir, project_id)
        if repairs:
            emit({"type": "system",
                  "text": f"Repaired {len(repairs)} plan-test import path(s) so the "
                          "acceptance tests resolve (deterministic, spec assertions "
                          "untouched)."})

    # PHASE 0b -- PREFLIGHT: verify every precondition BEFORE the first model
    # dispatch (zero LLM spend on a broken repo). Fail loud with exactly which
    # precondition is broken -- never degrade silently into a doomed walk.
    # Skipped when a custom build_check is injected (unit tests / callers that
    # deliberately simulate repo states preflight would reject).
    if build_check is None:
        from .preflight import validate_build_readiness
        problems = validate_build_readiness(
            repo_root, project_id=project_id,
            enforcement_provider=enforcement_provider)
        if problems:
            detail = "; ".join(problems[:10])
            emit({"type": "system",
                  "text": f"Build preflight failed -- fix before building: {detail}"})
            return LoopResult(
                run_id="g4-subagent-build",
                status="error",
                final_text="Build preflight failed (no model was dispatched, "
                           "nothing was spent):\n- " + "\n- ".join(problems),
                tool_calls_made=0,
                messages=[],
                error=f"preflight failed: {detail}",
            )

    # PHASE 1 -- per task, test-first, drive each to green before moving on.
    # A DEFINITIVELY-failed task (budget exhausted) does NOT stop the build:
    # independent tasks still run (bounded spend each, and they carry real
    # signal + product value). Only its DEPENDENTS are skipped -- for free --
    # because paying to build on a red prerequisite is the actual waste.
    for task in tasks:
        if cancel_check is not None and cancel_check():
            raise BuildCancelled("parent delivery cancellation requested")
        # CONTROL DECISION 1 (matrix read): block when any prerequisite row
        # is failed/blocked -- skipped for free.
        blocked_by = matrix.failed_or_blocked(task.deps)
        if blocked_by:
            matrix.set(task.id, status="blocked", blocked_by=blocked_by)
            emit({"type": "system",
                  "text": f"Skipping “{task.name}” — its prerequisite "
                          f"({', '.join(blocked_by)}) did not pass."})
            summary.append(f"{task.id} blocked_by={','.join(blocked_by)}")
            continue
        # CONTROL DECISION 2 (objective read): the task's plan test ALREADY
        # passes (resumed/partially-built repo) -- skip, spend nothing.
        if task.test:
            ok, errs = check(repo_root, task.test)
            if ok:
                matrix.set(task.id, status="pre-existing-green")
                emit({"type": "system", "text": f"“{task.name}” already passes its test — skipping."})
                summary.append(f"{task.id} test_green=True (pre-existing)")
                continue
        emit({"type": "system", "text": f"Building: {task.name}"})
        run("implementer", adapter, impl_sys,
            _implementer_message(task, context, repo_root, stack, project_id))
        calls += 1
        matrix.bump_attempts(task.id)
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
                per_task_repairs += 1  # a per-task FIX pass (not the draft)
                matrix.bump_attempts(task.id)
                ok, errs = check(repo_root, task.test)
            if ok:
                # PER-TASK DEFINITION-OF-DONE hard gate: a passing test is
                # necessary but not sufficient -- the task is not "done" until
                # its code also meets the quality bar (genuine test rigor, no
                # dead code, complexity/dup within bounds, a11y). Blocks the
                # task (status failed) when the bar cannot be met, so a doomed
                # DoD stops the build fail-fast (no evidence -> sign stays
                # fail-closed), exactly like a red test.
                dod_ok, dod_detail, dod_repairs = _enforce_task_dod(
                    task=task, repo_root=repo_root, stack=stack,
                    project_id=project_id, run=run, adapter=adapter,
                    impl_sys=impl_sys, check=check, cycles=per_task_cycles,
                    emit=emit)
                calls += dod_repairs
                per_task_repairs += dod_repairs
                if dod_ok:
                    matrix.set(task.id, status="green")
                    summary.append(f"{task.id} test_green=True")
                else:
                    matrix.set(task.id, status="failed")
                    summary.append(f"{task.id} dod_failed: {dod_detail}")
                    emit({"type": "system",
                          "text": f"“{task.name}” passed its test but did NOT "
                                  f"meet the Definition of Done: {dod_detail}. "
                                  "Blocking the task (a passing test alone is "
                                  "not 'done')."})
                continue
            if not ok:
                # ESCAPE VALVE (test-dispute): a red deadlock is NOT proof the
                # CODE is wrong -- the plan-authored test itself may be broken/
                # unpassable (a hallucinated assertion, or a literal always-fail
                # stub). Diagnose it -- deterministic health check first (zero
                # LLM), then a SECOND-OPINION classify on a genuinely DIFFERENT
                # model with FRESH context -- instead of silently blaming the
                # model. A broken test is RECORDED as a dispute for a separate
                # arbiter; the builder never edits its own exam, and scheduling
                # is unchanged (status stays 'failed' so dependent-skip and
                # fail-fast are intact).
                matrix.set(task.id, status="failed")
                summary.append(f"{task.id} test_green=False")
                dispute = _diagnose_deadlock(
                    repo_root, task, errs, reviewer, adapter, run, stack,
                    project_id)
                if dispute.get("used_arbiter"):
                    calls += 1
                if dispute["disputed"]:
                    emit({"type": "system",
                          "text": f"“{task.name}” DISPUTED — the plan test appears "
                                  f"broken/unpassable ({dispute['source']}): "
                                  f"{dispute['reason']}. Recorded for arbiter review; "
                                  "the build never edits its own exam."})
                    summary.append(
                        f"{task.id} test_disputed[{dispute['source']}]: {dispute['reason']}")
                else:
                    emit({"type": "system",
                          "text": f"“{task.name}” failed its gate after the fix budget — "
                                  "continuing with tasks that don't depend on it."})
        else:
            matrix.set(task.id, status="drafted-no-test")
            summary.append(f"{task.id}: drafted (no plan test)")

    failed_tasks = matrix.ids_with_status("failed")
    blocked_tasks = matrix.ids_with_status("blocked")
    if failed_tasks or blocked_tasks:
        matrix.persist(phase="stopped-red", integration_green=False,
                       **_repair_metrics())
        # FAIL FAST at the PHASE level: with any task red/blocked the build can
        # never sign, so integration/review/evidence would be paid spend on a
        # refused build. Every attemptable task was still attempted above.
        summary.append(_repair_summary_line())
        if usage.get("in") is not None or usage.get("out") is not None:
            summary.append(f"tokens_in={usage.get('in')} tokens_out={usage.get('out')}")
        return LoopResult(
            run_id="g4-subagent-build",
            status="budget_exhausted",
            final_text=("Subagent-driven build finished RED: "
                        f"failed={','.join(failed_tasks) or 'none'} "
                        f"blocked={','.join(blocked_tasks) or 'none'} -- every "
                        "independent task was attempted; integration/review/"
                        "evidence skipped (a red build cannot sign).\n"
                        + "\n".join(summary)),
            tool_calls_made=calls,
            messages=[],
            error=f"tasks failed their objective gate: {','.join(failed_tasks)}",
            tokens_in=usage.get("in"),
            tokens_out=usage.get("out"),
        )

    if cancel_check is not None and cancel_check():
        raise BuildCancelled("parent delivery cancellation requested")

    # PHASE 1b -- author the UX ACCEPTANCE test into the suite before
    # integration, so the build is TOLD (the model reads the exact bar) and MADE
    # (the full-suite integration check below must pass it) to ship a real,
    # styled, usable UI -- not merely graded on it after the fact. The test is
    # the signed UX spec (read-only to the model); a browser build that renders
    # no interactive controls or bare unstyled HTML fails it and stays RED here.
    # No-op for non-browser profiles / no App entry. Real path only.
    if build_check is None:
        try:
            from .acceptance import ensure_ux_acceptance_test
            authored = ensure_ux_acceptance_test(
                repo_root, source_dir=stack.source_dir, profile=stack.profile)
            if authored is not None:
                emit({"type": "system",
                      "text": "Authored the UX acceptance test: the build must "
                              "render a real, styled, usable UI (interactive "
                              "controls, real styling, a11y) to pass."})
        except Exception:
            pass

    if cancel_check is not None and cancel_check():
        raise BuildCancelled("parent delivery cancellation requested")

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
        integration_repairs += 1  # a final full-suite / integration FIX pass
        green, last_errors = check(repo_root)
    emit({"type": "system",
          "text": "The product compiles and all tests pass."
                  if green else "Build still has errors after the repair budget — "
                                "stopping (fail-fast)."})
    summary.append(f"integration_green={green}")
    matrix.persist(phase="integration", integration_green=green,
                   **_repair_metrics())

    if not green:
        # FAIL FAST: integration definitively failed its budget. Review and
        # evidence would be spend on a build the gate must refuse anyway; the
        # missing Build Evidence artifact keeps the sign fail-closed. The build
        # stays NOT-verified here -- the repair budget is exhausted, never forced
        # green; the logged repair count records how hard it tried.
        summary.append(_repair_summary_line())
        if usage.get("in") is not None or usage.get("out") is not None:
            summary.append(f"tokens_in={usage.get('in')} tokens_out={usage.get('out')}")
        return LoopResult(
            run_id="g4-subagent-build",
            status="budget_exhausted",
            final_text="Subagent-driven build STOPPED (fail-fast): integration "
                       "stayed red after the repair budget.\n" + "\n".join(summary),
            tool_calls_made=calls,
            messages=[],
            error="integration failed its objective gate",
            tokens_in=usage.get("in"),
            tokens_out=usage.get("out"),
        )

    if cancel_check is not None and cancel_check():
        raise BuildCancelled("parent delivery cancellation requested")

    # PHASE 3 -- independent review on the GREEN product (spec, then quality).
    # The reviewer is a HARD WALL, not an advisory pass: a reviewer FAIL is NOT
    # cleared by running one fixer and re-confirming the MECHANICAL build is
    # green. The reviewer must RE-RUN and return PASS -- an unresolved FAIL
    # blocks completion (green tests must never override an open reviewer
    # finding). The re-review/fix cycle is bounded (per-task fix budget) so a
    # stubborn FAIL cannot spin forever; when the budget is spent still-red, the
    # build fails-fast BEFORE the evidence pass, exactly like the integration
    # wall -- the missing Build Evidence artifact keeps the sign fail-closed.
    review_blocked: list[str] = []
    review_budget = max(1, per_task_cycles)
    if green:
        for kind, sys_prompt in (("spec", spec_sys), ("code", cq_sys)):
            verdict = run(f"{kind}-reviewer", reviewer, sys_prompt,
                          _final_review_message(kind, prompt, stack))
            calls += 1
            v = parse_verdict(verdict)
            attempts = 0
            while v == "FAIL" and green and attempts < review_budget:
                attempts += 1
                label = "requirements" if kind == "spec" else "quality"
                emit({"type": "system",
                      "text": f"Applying independent {label} review feedback, then "
                              "re-reviewing (a reviewer PASS is required to finish)."})
                run("fixer", adapter, impl_sys,
                    _fixer_message("Independent reviewer feedback:\n" + verdict,
                                   repo_root, stack))
                calls += 1
                review_repairs += 1  # an independent-review FIX pass
                green, last_errors = check(repo_root)  # a review fix must not break the build
                if not green:
                    break  # the fix broke the mechanical build -> handled below
                # HARD WALL: re-run the SAME reviewer on the reworked product and
                # require a final PASS. Green tests alone must not clear a FAIL.
                verdict = run(f"{kind}-reviewer", reviewer, sys_prompt,
                              _final_review_message(kind, prompt, stack))
                calls += 1
                v = parse_verdict(verdict)
            summary.append(f"{kind}_review={v}")
            if v == "FAIL":
                # Persist the blocking finding (its last, most-specific line).
                last_line = next((l.strip() for l in reversed(verdict.splitlines())
                                  if l.strip()), "unresolved reviewer FAIL")
                review_blocked.append(f"{kind}: {last_line}")
            if not green:
                break  # a broken build during rework: stop reviewing, fail below

    # Reviewer HARD WALL: an unresolved reviewer FAIL (or a review fix that broke
    # the mechanical build) blocks completion. Fail-fast BEFORE the evidence
    # pass so no BUILD_EVIDENCE artifact is written -- its absence keeps the sign
    # fail-closed, mirroring the integration fail-fast. Green mechanical tests do
    # NOT override an open reviewer finding.
    if not green or review_blocked:
        matrix.persist(phase="review-blocked", integration_green=green,
                       review_blocked=list(review_blocked), **_repair_metrics())
        summary.append(_repair_summary_line())
        if usage.get("in") is not None or usage.get("out") is not None:
            summary.append(f"tokens_in={usage.get('in')} tokens_out={usage.get('out')}")
        detail = ("the mechanical build broke during review rework"
                  if not green else "; ".join(review_blocked))
        return LoopResult(
            run_id="g4-subagent-build",
            status="budget_exhausted",
            final_text=("Subagent-driven build STOPPED (fail-fast): an independent "
                        "reviewer finding is unresolved and blocks completion "
                        "(green tests do not override it).\n" + detail + "\n"
                        + "\n".join(summary)),
            tool_calls_made=calls,
            messages=[],
            error=f"reviewer hard wall: {detail}",
            tokens_in=usage.get("in"),
            tokens_out=usage.get("out"),
        )

    # PHASE 3b -- WIRING advisory lint (informational, NEVER pass/fail). The
    # old in-loop "wiring reviewer" subagent pass was DELETED: wiring is now
    # enforced BY CONSTRUCTION through the acceptance/integration test that
    # renders the real app entry (`render(<App/>)`) and asserts behaviour that
    # requires the new module to be mounted -- an unwired module fails a RED
    # test through the normal loop above, so no reviewer is needed. What stays
    # here is only a cheap, crisp heads-up naming any module that is not
    # reachable from the entry; it does not gate and dispatches no fixer.
    if green:
        orphans = unwired_lint(repo_root, stack.source_dir)
        if orphans:
            emit({"type": "system",
                  "text": "Wiring lint (advisory, does not gate): "
                          + "; ".join(f"{o} is not reachable from the app entry"
                                      for o in orphans[:10])
                          + (f" (+{len(orphans) - 10} more)" if len(orphans) > 10 else "")})
            summary.append(f"wiring_lint_advisory={len(orphans)}")
        else:
            summary.append("wiring_lint=clean")

    if cancel_check is not None and cancel_check():
        raise BuildCancelled("parent delivery cancellation requested")

    # PHASE 4 -- record the Build Evidence artifact with the real numbers,
    # honestly: a not-green build is recorded as not green (the sign gate will
    # refuse it; evidence must never claim otherwise). The self-repair count is
    # handed to the evidence pass so BUILD_EVIDENCE.md states how many repairs
    # the build needed (0 == converged cleanly).
    run("evidence", adapter, impl_sys,
        _evidence_message(repo_root, project_id, stack, green,
                          repair_metrics=_repair_metrics()))
    calls += 1

    # Final machine-readable snapshot: the traceability artifact now carries the
    # total + per-phase repair_attempts (including any review-triggered fix), so
    # a downstream grader reads the definitive count from disk.
    matrix.persist(phase="complete", integration_green=green, **_repair_metrics())
    summary.append(_repair_summary_line())

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
