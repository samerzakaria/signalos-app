"""test_orchestrator_core.py - Tests for the orchestrator's core helpers.

Covers the pure / near-pure functions the orchestrator uses to translate
LLM responses into on-disk files and to build per-task prompts. These
are the load-bearing pieces under signalos -- if they regress, "build
me a todo app" silently produces nothing.

What's covered here:
  - _FILE_BLOCK_RE + _extract_files_from_response  (LLM response parser)
  - _write_extracted_files                          (path-safe writer)
  - _tasks_from_plan                                (YAML -> task dicts)
  - _relevant_skills                                (regex fallback paths)
  - _build_task_prompt                              (structural assertions)
  - _emit_task_progress                             (progress event shape)
  - _bash_available                                 (smoke / return type)

The explicit-skill injection path is covered separately in
test_orchestrator_skills.py.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.orchestrator import (
    _bash_available,
    _build_task_prompt,
    _emit_task_progress,
    _extract_files_from_response,
    _read_existing_files_context,
    _read_harness_response,
    _record_missing_deps,
    _relevant_skills,
    _scan_js_imports,
    _tasks_from_plan,
    _write_extracted_files,
)
from signalos_lib.plan import PlanDoc, Task, dump_tasks

_BUNDLE = HERE / "signalos_lib" / "_bundle"


def _stage_workspace(tmpdir: Path) -> Path:
    """Copy the parts of the bundle that the regex paths read from."""
    targets = [
        "core/execution/build/test-driven-development",
        "core/execution/build/test-generation",
        "core/execution/build/systematic-debugging",
        "core/execution/build/verification-before-completion",
        "core/execution/plan/writing-plans",
        "core/execution/review/comprehensive-code-review",
        "core/execution/review/receiving-code-review",
        "core/execution/worktree/using-git-worktrees",
        "core/governance/SecurityAudit",
    ]
    for rel in targets:
        src = _BUNDLE / rel
        if not src.is_dir():
            continue
        dst = tmpdir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
    return tmpdir


# ---------------------------------------------------------------------------
# _extract_files_from_response  (LLM-response parser)
# ---------------------------------------------------------------------------

class ExtractFilesFromResponse(unittest.TestCase):
    def test_empty_response_returns_empty(self) -> None:
        self.assertEqual(_extract_files_from_response(""), [])
        self.assertEqual(_extract_files_from_response("just prose, no files"), [])

    def test_single_well_formed_block(self) -> None:
        response = """### filepath: src/foo.ts
```ts
export const foo = 1;
```
"""
        out = _extract_files_from_response(response)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "src/foo.ts")
        self.assertIn("export const foo = 1;", out[0][1])

    def test_multiple_files_preserved_in_document_order(self) -> None:
        response = """### filepath: src/a.ts
```ts
export const a = 1;
```

prose between

### filepath: src/b.tsx
```tsx
export const B = () => null;
```
"""
        out = _extract_files_from_response(response)
        self.assertEqual([p for p, _ in out], ["src/a.ts", "src/b.tsx"])

    def test_accepts_FILE_and_path_header_variants(self) -> None:
        response = """FILE: src/x.ts
```ts
x
```

path: src/y.ts
```ts
y
```
"""
        out = _extract_files_from_response(response)
        self.assertEqual([p for p, _ in out], ["src/x.ts", "src/y.ts"])

    def test_accepts_block_without_language_tag(self) -> None:
        response = """### filepath: README.md
```
hello
```
"""
        out = _extract_files_from_response(response)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "README.md")

    def test_rejects_path_traversal_with_double_dot(self) -> None:
        response = """### filepath: ../../etc/passwd
```
:(
```
"""
        self.assertEqual(_extract_files_from_response(response), [])

    def test_rejects_unix_absolute_path(self) -> None:
        response = """### filepath: /etc/passwd
```
:(
```
"""
        self.assertEqual(_extract_files_from_response(response), [])

    def test_rejects_windows_drive_letter(self) -> None:
        response = """### filepath: C:/Windows/System32/evil.dll
```
:(
```
"""
        self.assertEqual(_extract_files_from_response(response), [])

    def test_keeps_relative_subpaths_that_contain_dotdot_substring(self) -> None:
        """`..` is rejected only as a *path component*, not as a substring."""
        response = """### filepath: src/a..b/c.ts
```ts
c
```
"""
        out = _extract_files_from_response(response)
        self.assertEqual(out[0][0], "src/a..b/c.ts")

    def test_strips_surrounding_quotes_from_path(self) -> None:
        """The regex captures the path string; surrounding single or
        double quotes are stripped post-capture."""
        response = """### filepath: "src/quoted.ts"
```ts
q
```
"""
        out = _extract_files_from_response(response)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "src/quoted.ts")


# ---------------------------------------------------------------------------
# _write_extracted_files  (safe writer with resolve-time defense)
# ---------------------------------------------------------------------------

class WriteExtractedFiles(unittest.TestCase):
    def test_writes_files_and_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            written = _write_extracted_files(
                root,
                [("src/deeply/nested/foo.ts", "export const foo = 1;")],
            )
            self.assertEqual(written, ["src/deeply/nested/foo.ts"])
            self.assertEqual(
                (root / "src" / "deeply" / "nested" / "foo.ts").read_text(),
                "export const foo = 1;",
            )

    def test_returns_paths_in_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            files = [("a.ts", "a"), ("b/c.ts", "c"), ("d.md", "d")]
            written = _write_extracted_files(Path(d), files)
            self.assertEqual(written, ["a.ts", "b/c.ts", "d.md"])

    def test_rejects_path_escaping_via_resolve(self) -> None:
        """Belt-and-braces: even if the regex check missed something,
        target.resolve() must stay inside root."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "subdir").mkdir()
            # A symlink-like construction: "subdir/../../outside.txt"
            # resolves to outside the root.
            written = _write_extracted_files(
                root,
                [("subdir/../../outside.txt", "evil")],
            )
            self.assertEqual(written, [])
            self.assertFalse((root.parent / "outside.txt").exists())

    def test_overwrites_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "x.ts").write_text("old")
            _write_extracted_files(root, [("x.ts", "new")])
            self.assertEqual((root / "x.ts").read_text(), "new")

    @unittest.skipUnless(_bash_available(), "bash unavailable")
    def test_pre_write_guard_blocks_malicious_payload(self) -> None:
        """AMD-CORE-110: orchestrator must invoke pre-tool-use-guard.sh on
        every file write, refuse the write on non-zero exit, and append a
        violation:write-blocked entry to AUDIT_TRAIL.jsonl.

        Strategy: stage the real guard + redact.py + a stub
        PERMANENTLY_T3.md so the guard runs end-to-end. Submit two files:
        one clean, one containing an AWS-key-shaped string the secret
        scanner rejects. Assert the clean file lands, the malicious file
        does not, and the audit trail records the block.
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # Stage the guard + its redact.py dependency in the layout the
            # guard expects (workspace-relative).
            hooks_dst = root / "core" / "execution" / "hooks"
            hooks_dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                _BUNDLE / "core" / "execution" / "hooks" / "pre-tool-use-guard.sh",
                hooks_dst / "pre-tool-use-guard.sh",
            )
            (hooks_dst / "pre-tool-use-guard.sh").chmod(0o755)
            (hooks_dst / "_lib").mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                _BUNDLE / "core" / "execution" / "hooks" / "_lib" / "redact.py",
                hooks_dst / "_lib" / "redact.py",
            )

            clean = ("docs/notes.md", "Hello world. Nothing sensitive.")
            # AKIA-prefixed string is a canonical AWS access key pattern
            # the redact.py secret scanner detects.
            malicious = ("src/leaked.ts", 'const k = "AKIAIOSFODNN7EXAMPLE";')

            written = _write_extracted_files(root, [clean, malicious])

            self.assertIn("docs/notes.md", written, "clean file must be written")
            self.assertNotIn(
                "src/leaked.ts",
                written,
                "malicious file must be refused by the guard",
            )
            self.assertTrue(
                (root / "docs" / "notes.md").is_file(),
                "clean file expected on disk",
            )
            self.assertFalse(
                (root / "src" / "leaked.ts").is_file(),
                "malicious file must not exist on disk",
            )

            trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
            self.assertTrue(trail.is_file(), "audit trail must be written")
            entries = [
                json.loads(line)
                for line in trail.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            blocks = [
                e for e in entries
                if e.get("action") == "violation:write-blocked"
                and e.get("path") == "src/leaked.ts"
            ]
            self.assertEqual(
                len(blocks),
                1,
                f"expected one violation:write-blocked entry for src/leaked.ts; "
                f"got entries: {entries}",
            )


# ---------------------------------------------------------------------------
# _tasks_from_plan  (YAML -> dict[])
# ---------------------------------------------------------------------------

class TasksFromPlan(unittest.TestCase):
    def test_missing_file_returns_empty_no_crash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = _tasks_from_plan(Path(d) / "missing.yaml", wave_id="1")
            self.assertEqual(out, [])

    def test_filters_by_wave(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            plan = Path(d) / "PLAN.tasks.yaml"
            dump_tasks(
                PlanDoc(
                    wave="1",
                    tasks=[
                        Task(id="01HABCDEFGHJKMNPQRSTVWXYZ0", title="t1",
                             status="pending", tier="T2", wave="1"),
                        Task(id="01HABCDEFGHJKMNPQRSTVWXYZ1", title="t2",
                             status="pending", tier="T2", wave="2"),
                    ],
                ),
                plan,
            )
            out_w1 = _tasks_from_plan(plan, wave_id="1")
            out_w2 = _tasks_from_plan(plan, wave_id="2")
            self.assertEqual([t["title"] for t in out_w1], ["t1"])
            self.assertEqual([t["title"] for t in out_w2], ["t2"])

    def test_tasks_without_explicit_wave_inherit_wave_id_arg(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            plan = Path(d) / "PLAN.tasks.yaml"
            dump_tasks(
                PlanDoc(
                    wave="3",
                    tasks=[
                        Task(id="01HABCDEFGHJKMNPQRSTVWXYZ0", title="t",
                             status="pending", tier="T2"),
                    ],
                ),
                plan,
            )
            out = _tasks_from_plan(plan, wave_id="3")
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["wave"], "3")

    def test_branch_defaults_to_task_prefix_when_unset(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            plan = Path(d) / "PLAN.tasks.yaml"
            dump_tasks(
                PlanDoc(
                    wave="1",
                    tasks=[
                        Task(id="01HABCDEFGHJKMNPQRSTVWXYZ0", title="t",
                             status="pending", tier="T2"),
                    ],
                ),
                plan,
            )
            out = _tasks_from_plan(plan, wave_id="1")
            self.assertTrue(out[0]["branch"].startswith("task-"))

    def test_malformed_yaml_returns_empty_no_crash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            plan = Path(d) / "PLAN.tasks.yaml"
            plan.write_text("not: [valid yaml: structure")
            out = _tasks_from_plan(plan, wave_id="1")
            self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# _relevant_skills  (regex fallback paths)
# ---------------------------------------------------------------------------

class RelevantSkillsRegexFallback(unittest.TestCase):
    """Covers regex paths not exercised in test_orchestrator_skills.py."""

    def _build(self, title: str, desc: str = "") -> tuple[Path, dict]:
        # Returns (workspace, task_dict). Caller uses contextlib.
        d = tempfile.mkdtemp()
        ws = _stage_workspace(Path(d))
        return ws, {"title": title, "description": desc}

    def test_debug_keyword_pulls_systematic_debugging(self) -> None:
        ws, task = self._build("Fix the crash on save", "investigate root cause")
        try:
            out = _relevant_skills(task, ws)
            labels = [label for label, _ in out]
            self.assertIn("Systematic Debugging", labels)
        finally:
            shutil.rmtree(ws)

    def test_plan_keyword_pulls_writing_plans(self) -> None:
        ws, task = self._build("Design the schema", "decompose into tasks")
        try:
            out = _relevant_skills(task, ws)
            labels = [label for label, _ in out]
            self.assertIn("Writing Plans", labels)
        finally:
            shutil.rmtree(ws)

    def test_worktree_keyword_pulls_using_git_worktrees(self) -> None:
        ws, task = self._build("Run waves in parallel", "use a worktree per task")
        try:
            out = _relevant_skills(task, ws)
            labels = [label for label, _ in out]
            self.assertIn("Using Git Worktrees", labels)
        finally:
            shutil.rmtree(ws)

    def test_review_keyword_pulls_receiving_code_review(self) -> None:
        ws, task = self._build("Address the review comments on PR #12", "")
        try:
            out = _relevant_skills(task, ws)
            labels = [label for label, _ in out]
            self.assertIn("Receiving Code Review", labels)
        finally:
            shutil.rmtree(ws)

    def test_verification_skill_always_included(self) -> None:
        """Even for a task with no matching keywords, verification fires."""
        ws, task = self._build("totally generic title", "no triggers")
        try:
            out = _relevant_skills(task, ws)
            labels = [label for label, _ in out]
            self.assertIn("Verification Before Completion", labels)
        finally:
            shutil.rmtree(ws)


# ---------------------------------------------------------------------------
# _read_harness_response  (harness -> orchestrator filename handoff)
#
# REGRESSION SHIELD: the harness writes the LLM response to
# `response.preview.txt`. The orchestrator's earlier candidate list
# only looked for `response.txt` / `preview.txt` / `response.md`, so
# every wave silently produced zero files while reporting "completed".
# These tests lock the contract: response.preview.txt must be found.
# ---------------------------------------------------------------------------

class ReadHarnessResponse(unittest.TestCase):
    def _make_session(self, root: Path, sid: str, call_id: str) -> Path:
        # Mirror harness._call_dir layout: sessions/<sid>/harness/<call_id>/
        cdir = root / ".signalos" / "sessions" / sid / "harness" / call_id
        cdir.mkdir(parents=True, exist_ok=True)
        return cdir

    def test_finds_response_preview_txt(self) -> None:
        """The actual filename harness._persist_response_preview writes
        today. If this assertion ever flips False, file extraction
        breaks across the whole orchestrator."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cdir = self._make_session(root, "sess-1", "call-1")
            (cdir / "response.preview.txt").write_text(
                "### filepath: src/foo.ts\n```ts\nexport const foo = 1;\n```\n"
            )
            result = {"call_id": "call-1", "session_id": "sess-1"}
            text = _read_harness_response(root, "sess-1", result)
            self.assertIn("### filepath: src/foo.ts", text)
            self.assertIn("export const foo = 1;", text)

    def test_finds_legacy_response_txt(self) -> None:
        """Fallback for legacy session dirs from older harness versions."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cdir = self._make_session(root, "s", "c")
            (cdir / "response.txt").write_text("legacy content")
            text = _read_harness_response(
                root, "s", {"call_id": "c", "session_id": "s"},
            )
            self.assertEqual(text, "legacy content")

    def test_prefers_response_preview_over_legacy_names(self) -> None:
        """If both files exist, response.preview.txt wins (it's the
        current canonical name)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cdir = self._make_session(root, "s", "c")
            (cdir / "response.preview.txt").write_text("current")
            (cdir / "response.txt").write_text("legacy")
            text = _read_harness_response(
                root, "s", {"call_id": "c", "session_id": "s"},
            )
            self.assertEqual(text, "current")

    def test_falls_back_to_response_preview_field_when_no_file(self) -> None:
        """In-memory fallback: when the disk write hasn't happened yet
        (or a test patched the harness), the result dict's
        response_preview can still carry usable text."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            result = {
                "call_id": "c",
                "session_id": "s",
                "response_preview": "from memory",
            }
            self.assertEqual(_read_harness_response(root, "s", result), "from memory")

    def test_returns_empty_when_no_call_id(self) -> None:
        """Defensive: bad result dict produces empty string, not exception."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_read_harness_response(Path(d), None, {}), "")

    def test_returns_empty_when_no_file_and_no_preview(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            result = {"call_id": "c", "session_id": "s"}  # no response_preview
            self.assertEqual(_read_harness_response(root, "s", result), "")

    def test_end_to_end_with_real_harness(self) -> None:
        """Integration: actually call harness.run_step with TestProvider
        and confirm the response flows back through _read_harness_response
        and into _extract_files_from_response producing the expected
        files. This is the regression test that would have caught the
        filename + directory-path mismatch bugs from the start.

        Critical: the canned response MUST be longer than 200 chars so
        result.response_preview (which harness truncates) cannot mask
        a broken disk-read path. If you shrink this, you're testing the
        in-memory fallback, not the disk read."""
        import signalos_lib.harness as h

        # ~600 chars: well above the 200-char response_preview truncation.
        # If disk read is broken, response_preview won't carry both file
        # blocks, the second file extraction will fail, and the test
        # catches it.
        long_filler = "// " + ("x" * 80 + "\n") * 5
        canned = (
            f"I'll add the requested module.\n\n"
            f"### filepath: src/hello.ts\n```ts\n"
            f"{long_filler}"
            f"export const x = 1;\n```\n\n"
            f"### filepath: src/world.ts\n```ts\n"
            f"export const y = 2;\n```\n"
        )
        self.assertGreater(len(canned), 200, "canned must exceed 200-char preview")

        original_canned = h._HARNESS_TEST_CANNED  # type: ignore[attr-defined]
        h._HARNESS_TEST_CANNED = canned  # type: ignore[attr-defined]
        try:
            with tempfile.TemporaryDirectory(prefix="harness-integration-") as d:
                root = Path(d)
                (root / ".signalos").mkdir()
                result = h.run_step(
                    step_id="t1",
                    prompt="Build two files",
                    session_id="sess-integ",
                    cwd=root,
                    intent="integration test",
                    provider=h.TestProvider(),
                )
                self.assertEqual(result["status"], "completed", result)

                # Sanity: result.response_preview is TRUNCATED to 200 chars,
                # so any in-memory fallback alone can't see the second file.
                preview_field = result.get("response_preview", "")
                self.assertLessEqual(len(preview_field), 200)
                self.assertNotIn("src/world.ts", preview_field,
                                 "preview should not contain second file -- "
                                 "if it does, test is no longer guarding the "
                                 "disk-read path")

                # Real handoff: must find the full text on disk.
                response_text = _read_harness_response(root, "sess-integ", result)
                self.assertIn("### filepath: src/hello.ts", response_text)
                self.assertIn("### filepath: src/world.ts", response_text)

                files = _extract_files_from_response(response_text)
                self.assertEqual(len(files), 2, files)
                self.assertEqual({f[0] for f in files}, {"src/hello.ts", "src/world.ts"})
        finally:
            h._HARNESS_TEST_CANNED = original_canned  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _build_task_prompt  (structural assertions)
# ---------------------------------------------------------------------------

class BuildTaskPrompt(unittest.TestCase):
    def test_includes_task_identity_fields(self) -> None:
        task = {
            "task": "T-001",
            "title": "Add login",
            "branch": "task-T-001",
            "wave": "1",
            "tier": "T2",
            "description": "Wire OAuth",
            "files": ["src/Login.tsx"],
        }
        prompt = _build_task_prompt(task, root=None)
        self.assertIn("Task id: T-001", prompt)
        self.assertIn("Task title: Add login", prompt)
        self.assertIn("Branch: task-T-001", prompt)
        self.assertIn("Trust tier: T2", prompt)
        self.assertIn("Wire OAuth", prompt)
        self.assertIn("src/Login.tsx", prompt)

    def test_missing_description_falls_back_to_placeholder(self) -> None:
        prompt = _build_task_prompt({"task": "T1", "title": "x"}, root=None)
        self.assertIn("(no description)", prompt)

    def test_missing_files_falls_back_to_placeholder(self) -> None:
        prompt = _build_task_prompt({"task": "T1", "title": "x"}, root=None)
        self.assertIn("no specific files declared", prompt)

    def test_root_none_omits_skills_section(self) -> None:
        prompt = _build_task_prompt(
            {"task": "T1", "title": "anything", "skills": ["security-audit"]},
            root=None,
        )
        self.assertNotIn("### Security Audit", prompt)
        self.assertNotIn("Applicable SignalOS skills", prompt)

    def test_always_emits_output_protocol(self) -> None:
        """The MANDATORY filepath fenced-block protocol must be in every
        prompt -- this is how the orchestrator parses the response back."""
        prompt = _build_task_prompt({"task": "T1", "title": "x"}, root=None)
        self.assertIn("### filepath:", prompt)
        self.assertIn("Output format (MANDATORY)", prompt)


# ---------------------------------------------------------------------------
# _emit_task_progress  (progress event shape)
# ---------------------------------------------------------------------------

class EmitTaskProgress(unittest.TestCase):
    def test_writes_one_json_line_with_expected_shape(self) -> None:
        # _emit_task_progress writes to sys.__stdout__ to bypass the
        # contextlib.redirect_stdout used by callers. We swap __stdout__
        # for the duration of the test.
        buf = io.StringIO()
        original = sys.__stdout__
        sys.__stdout__ = buf
        try:
            _emit_task_progress(
                wave_id="1",
                task_id="01HABCDEFGHJKMNPQRSTVWXYZ0",
                state="completed",
                detail="ok",
            )
        finally:
            sys.__stdout__ = original

        lines = buf.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 1, f"expected single JSON line, got: {buf.getvalue()!r}")
        payload = json.loads(lines[0])
        self.assertEqual(payload.get("phase"), "orchestrate")
        self.assertEqual(payload.get("substep"), "01HABCDEFGHJKMNPQRSTVWXYZ0")
        self.assertEqual(payload.get("kind"), "progress")
        self.assertIn("id", payload)


# ---------------------------------------------------------------------------
# _bash_available  (smoke)
# ---------------------------------------------------------------------------

class BashAvailable(unittest.TestCase):
    def test_returns_a_boolean(self) -> None:
        result = _bash_available()
        self.assertIsInstance(result, bool)


# ---------------------------------------------------------------------------
# #2 -- Iterative refinement: _read_existing_files_context
# ---------------------------------------------------------------------------

class ReadExistingFilesContext(unittest.TestCase):
    def test_empty_files_returns_empty_string(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_read_existing_files_context(Path(d), []), "")

    def test_missing_files_are_skipped_silently(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            # No file at this path exists -- the function should return ""
            # rather than raise.
            out = _read_existing_files_context(Path(d), ["src/does-not-exist.ts"])
            self.assertEqual(out, "")

    def test_existing_file_contents_are_included_under_heading(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "src" / "auth.ts"
            target.parent.mkdir(parents=True)
            target.write_text("export const SECRET = 1;\n")
            out = _read_existing_files_context(root, ["src/auth.ts"])
            self.assertIn("### src/auth.ts", out)
            self.assertIn("```ts", out)
            self.assertIn("export const SECRET = 1;", out)

    def test_mix_of_existing_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.ts").write_text("a")
            out = _read_existing_files_context(root, ["a.ts", "missing.ts", "also-missing.ts"])
            self.assertIn("### a.ts", out)
            self.assertNotIn("missing.ts", out)

    def test_budget_caps_total_injected_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # One file way over budget should be truncated.
            (root / "big.ts").write_text("x" * 100_000)
            out = _read_existing_files_context(root, ["big.ts"])
            self.assertIn("truncated for prompt budget", out)
            self.assertLess(len(out), 60_000, "should respect ~50KB budget")


# ---------------------------------------------------------------------------
# #2 + #5: integration into _build_task_prompt
# ---------------------------------------------------------------------------

class BuildTaskPromptIterativeAndRetry(unittest.TestCase):
    def test_prompt_includes_current_file_contents_when_files_exist(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "src").mkdir()
            (root / "src" / "TodoList.tsx").write_text("const buggy = true;\n")
            task = {
                "task": "T1",
                "title": "Fix bug in TodoList",
                "description": "The toggle button doesn't work",
                "files": ["src/TodoList.tsx"],
            }
            prompt = _build_task_prompt(task, root=root)
            self.assertIn("Current state of files you may modify", prompt)
            self.assertIn("const buggy = true;", prompt)

    def test_prompt_omits_existing_section_when_no_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            task = {
                "task": "T1",
                "title": "Create TodoList from scratch",
                "files": ["src/TodoList.tsx"],
            }
            prompt = _build_task_prompt(task, root=root)
            self.assertNotIn("Current state of files you may modify", prompt)

    def test_previous_failure_section_prepended_when_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            task = {
                "task": "T1",
                "title": "Add auth",
                "previous_failure": "TypeError: bcrypt.hash is not a function",
            }
            prompt = _build_task_prompt(task, root=Path(d))
            self.assertIn("Previous attempt failed", prompt)
            self.assertIn("TypeError: bcrypt.hash", prompt)
            # And the retry section must precede the task identity block.
            self.assertLess(prompt.find("Previous attempt failed"), prompt.find("Task id:"))

    def test_no_previous_failure_section_on_first_attempt(self) -> None:
        prompt = _build_task_prompt({"task": "T1", "title": "x"}, root=None)
        self.assertNotIn("Previous attempt failed", prompt)


# ---------------------------------------------------------------------------
# #3 -- Auto-deps: _scan_js_imports + _record_missing_deps
# ---------------------------------------------------------------------------

class ScanJsImports(unittest.TestCase):
    def test_es_module_default_import(self) -> None:
        self.assertEqual(
            _scan_js_imports("import preact from 'preact';"),
            {"preact"},
        )

    def test_es_module_named_import(self) -> None:
        self.assertEqual(
            _scan_js_imports("import { motion } from 'framer-motion';"),
            {"framer-motion"},
        )

    def test_es_module_bare_side_effect_import(self) -> None:
        self.assertEqual(
            _scan_js_imports("import 'normalize.css';"),
            {"normalize.css"},
        )

    def test_require_call(self) -> None:
        self.assertEqual(
            _scan_js_imports("const fs = require('graceful-fs');"),
            {"graceful-fs"},
        )

    def test_excludes_relative_imports(self) -> None:
        content = "import { foo } from './foo';\nimport bar from '../bar';"
        self.assertEqual(_scan_js_imports(content), set())

    def test_excludes_node_builtins(self) -> None:
        content = "import fs from 'fs';\nimport path from 'node:path';"
        self.assertEqual(_scan_js_imports(content), set())

    def test_reduces_subpath_to_package_name(self) -> None:
        # `import { foo } from 'lodash/fp'` -> needs `lodash`, not `lodash/fp`.
        self.assertEqual(
            _scan_js_imports("import x from 'lodash/fp';"),
            {"lodash"},
        )

    def test_preserves_scoped_package_name(self) -> None:
        self.assertEqual(
            _scan_js_imports("import { x } from '@preact/signals';"),
            {"@preact/signals"},
        )

    def test_reduces_scoped_subpath(self) -> None:
        self.assertEqual(
            _scan_js_imports("import x from '@scope/pkg/sub';"),
            {"@scope/pkg"},
        )

    def test_multiple_imports_in_one_file(self) -> None:
        content = """
            import preact from 'preact';
            import { signal } from '@preact/signals';
            import './styles.css';
            import fs from 'fs';
            const x = require('lodash');
        """
        self.assertEqual(_scan_js_imports(content), {"preact", "@preact/signals", "lodash"})


class RecordMissingDeps(unittest.TestCase):
    def test_writes_missing_deps_when_package_json_lacks_imports(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "name": "test", "dependencies": {"preact": "^10.0.0"},
            }))
            (root / "src").mkdir()
            (root / "src" / "App.tsx").write_text(
                "import preact from 'preact';\n"
                "import { motion } from 'framer-motion';\n"
            )
            new_missing = _record_missing_deps(root, ["src/App.tsx"])
            self.assertEqual(new_missing, ["framer-motion"])
            record = json.loads((root / ".signalos" / "missing-deps.json").read_text())
            self.assertEqual(record, ["framer-motion"])

    def test_does_not_flag_already_declared_packages(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "dependencies": {"preact": "^10.0.0", "framer-motion": "^11.0.0"},
            }))
            (root / "App.tsx").write_text("import preact from 'preact';\n"
                                          "import { motion } from 'framer-motion';\n")
            self.assertEqual(_record_missing_deps(root, ["App.tsx"]), [])

    def test_devdependencies_count_as_declared(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({
                "dependencies": {},
                "devDependencies": {"vitest": "^2.0.0"},
            }))
            (root / "App.test.ts").write_text("import { vi } from 'vitest';\n")
            self.assertEqual(_record_missing_deps(root, ["App.test.ts"]), [])

    def test_accumulates_across_tasks_in_the_wave(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({"dependencies": {}}))
            (root / "a.ts").write_text("import a from 'pkg-a';\n")
            (root / "b.ts").write_text("import b from 'pkg-b';\n")

            first = _record_missing_deps(root, ["a.ts"])
            self.assertEqual(first, ["pkg-a"])

            second = _record_missing_deps(root, ["b.ts"])
            # Only the genuinely new dep is reported; the merged file
            # contains both.
            self.assertEqual(second, ["pkg-b"])
            record = json.loads((root / ".signalos" / "missing-deps.json").read_text())
            self.assertEqual(record, ["pkg-a", "pkg-b"])

    def test_no_package_json_means_no_scan(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.ts").write_text("import a from 'pkg-a';\n")
            # No package.json -> we don't know what's declared, so skip
            # silently rather than flag everything as missing.
            self.assertEqual(_record_missing_deps(root, ["a.ts"]), [])

    def test_ignores_non_js_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({"dependencies": {}}))
            (root / "README.md").write_text("import preact from 'preact';\n")
            (root / "data.json").write_text('{"import": "preact"}')
            self.assertEqual(_record_missing_deps(root, ["README.md", "data.json"]), [])


if __name__ == "__main__":
    unittest.main()
