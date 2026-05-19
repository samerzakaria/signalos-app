"""test_orchestrator_skills.py - Integration test for skill injection.

Proves the end-to-end wiring of the "skills" feature without an LLM:

    plan task tagged skills=[...]
      -> _tasks_from_plan loads it
      -> _build_task_prompt assembles the prompt
      -> _relevant_skills looks up the SKILL.md path
      -> _load_skill reads from the workspace
      -> the actual SKILL.md heading + body appear in the prompt string

This is the verification we can do at the desk - we don't need to call
an LLM provider. The prompt string is the contract between the
orchestrator and the harness; whatever shows up there is what the model
will see.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.orchestrator import (
    _SKILL_KEY_TO_PATH,
    _build_task_prompt,
    _relevant_skills,
    _tasks_from_plan,
)
from signalos_lib.plan import PlanDoc, Task, dump_tasks

_BUNDLE = HERE / "signalos_lib" / "_bundle"


def _stage_workspace(tmpdir: Path) -> Path:
    """Copy the bundle into tmpdir to mimic what signal-init does."""
    # _copy_bundle in commands/init.py walks _bundle and copies to root.
    # For the test we replicate just enough of that: copy the directories
    # _relevant_skills will reach into.
    targets = [
        "core/execution/build/test-generation",
        "core/execution/build/test-driven-development",
        "core/execution/build/verification-before-completion",
        "core/execution/review/comprehensive-code-review",
        "core/execution/review/receiving-code-review",
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


class SkillCatalogIntegrity(unittest.TestCase):
    """Lock the JS/Python catalog contract."""

    def test_every_skill_key_resolves_to_a_real_bundle_path(self) -> None:
        """Catch typos / renames before they ship: every catalog entry's path
        must exist in the bundle so _load_skill returns content at runtime."""
        missing: list[tuple[str, str]] = []
        for key, (_label, path) in _SKILL_KEY_TO_PATH.items():
            if not (_BUNDLE / path).is_file():
                missing.append((key, path))
        self.assertEqual(
            missing,
            [],
            f"Catalog points at SKILL.md files that don't exist in the bundle: {missing}",
        )

    def test_catalog_matches_js_side(self) -> None:
        """src/services/signalosPrompt.ts must list the same keys.

        Drift here means the AI gets told skill keys the Python side
        can't resolve (or vice versa). Read the JS source and extract
        the VALID_SKILL_KEYS Set literal precisely.
        """
        import re

        ts_path = HERE.parent / "src" / "services" / "signalosPrompt.ts"
        text = ts_path.read_text(encoding="utf-8")

        # Capture only the array literal inside `new Set([ ... ])` that
        # follows VALID_SKILL_KEYS. Anchored so we don't pick up strings
        # from elsewhere in the file (e.g. the prompt template's JSON).
        m = re.search(
            r"VALID_SKILL_KEYS\s*=\s*new\s+Set\s*\(\s*\[([^\]]+)\]",
            text,
        )
        self.assertIsNotNone(m, "VALID_SKILL_KEYS = new Set([...]) literal not found")
        body = m.group(1)
        js_keys = set(re.findall(r"['\"]([a-z][a-z0-9\-]+)['\"]", body))
        py_keys = set(_SKILL_KEY_TO_PATH.keys())
        self.assertEqual(
            py_keys,
            js_keys,
            f"Catalog drift between Python and TypeScript:\n"
            f"  python only: {sorted(py_keys - js_keys)}\n"
            f"  js only:     {sorted(js_keys - py_keys)}",
        )


class ExplicitSkillInjection(unittest.TestCase):
    """A task tagged with skills=[...] must produce a prompt containing
    the actual SKILL.md content under a ### heading."""

    def test_security_audit_skill_lands_in_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ws = _stage_workspace(Path(d))
            task = {
                "task": "01HABCDEFGHJKMNPQRSTVWXYZ0",
                "title": "Add auth",
                "description": "Wire auth middleware",
                "files": ["src/auth.ts"],
                "skills": ["security-audit"],
            }
            prompt = _build_task_prompt(task, ws)

            # The label heading must be present.
            self.assertIn("### Security Audit", prompt, "Security Audit heading missing")
            # And a distinctive line from the actual SKILL.md body.
            # SecurityAudit/SKILL.md mentions "OWASP Top 10" in its description.
            self.assertIn("OWASP", prompt, "SecurityAudit SKILL.md body not loaded into prompt")

    def test_multiple_explicit_skills_compose(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ws = _stage_workspace(Path(d))
            task = {
                "task": "T1",
                "title": "Build login page",
                "description": "Form + validation",
                "files": ["src/Login.tsx"],
                "skills": ["security-audit", "test-generation"],
            }
            prompt = _build_task_prompt(task, ws)
            self.assertIn("### Security Audit", prompt)
            self.assertIn("### Test Generation", prompt)

    def test_unknown_skill_key_is_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ws = _stage_workspace(Path(d))
            task = {
                "task": "T1",
                "title": "do a thing",
                "skills": ["security-audit", "totally-not-a-real-skill"],
            }
            prompt = _build_task_prompt(task, ws)
            self.assertIn("### Security Audit", prompt)
            self.assertNotIn("totally-not-a-real-skill", prompt)


class KeywordFallback(unittest.TestCase):
    """When no explicit skills are tagged, the title/description regex
    fallback should still pull in the right SKILL.md."""

    def test_security_keyword_triggers_security_audit(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ws = _stage_workspace(Path(d))
            task = {
                "task": "T1",
                "title": "Validate user input for XSS",
                "description": "All composer text must be sanitised",
                "files": ["src/composer.ts"],
                # NO explicit skills - rely on regex
            }
            prompt = _build_task_prompt(task, ws)
            self.assertIn("### Security Audit", prompt)

    def test_explicit_and_regex_dedupe_by_path(self) -> None:
        """If a task is BOTH tagged skills=[security-audit] AND has 'xss' in
        the title, the SKILL.md must appear exactly once, not twice."""
        with tempfile.TemporaryDirectory() as d:
            ws = _stage_workspace(Path(d))
            task = {
                "task": "T1",
                "title": "Fix XSS in composer",
                "description": "Sanitise the input",
                "files": ["src/composer.ts"],
                "skills": ["security-audit"],
            }
            prompt = _build_task_prompt(task, ws)
            self.assertEqual(
                prompt.count("### Security Audit"),
                1,
                "Security Audit heading should appear exactly once even when both "
                "explicit and regex paths match the same skill",
            )


class TasksFromPlanCarriesSkills(unittest.TestCase):
    """PLAN.tasks.yaml -> task dict round-trip must preserve the skills
    field, otherwise the orchestrator never sees the explicit tags."""

    def test_skills_field_survives_yaml_to_dict(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            plan_path = Path(d) / "PLAN.tasks.yaml"
            doc = PlanDoc(
                wave="1",
                tasks=[
                    Task(
                        id="01HABCDEFGHJKMNPQRSTVWXYZ0",
                        title="Add auth",
                        status="pending",
                        tier="T2",
                        description="Wire auth middleware",
                        files=["src/auth.ts"],
                        skills=["security-audit", "test-generation"],
                    )
                ],
            )
            dump_tasks(doc, plan_path)
            tasks = _tasks_from_plan(plan_path, wave_id="1")
            self.assertEqual(len(tasks), 1)
            self.assertEqual(
                tasks[0].get("skills"),
                ["security-audit", "test-generation"],
                f"skills field lost: {tasks[0]}",
            )
            self.assertEqual(tasks[0].get("description"), "Wire auth middleware")
            self.assertEqual(tasks[0].get("files"), ["src/auth.ts"])


class MissingBundleIsHarmless(unittest.TestCase):
    """A workspace where signal-init hasn't been run (no .signalos/ + no
    bundle files) must NOT crash the prompt builder. The skills section
    should just be omitted."""

    def test_no_workspace_files_no_crash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)  # empty - no bundle
            task = {
                "task": "T1",
                "title": "Add auth (XSS-sensitive)",
                "skills": ["security-audit"],
            }
            # Should not raise, and should not contain any ### heading from
            # an unloaded SKILL.md.
            prompt = _build_task_prompt(task, ws)
            self.assertNotIn("### Security Audit", prompt)
            # The boilerplate (task id, output protocol) must still be there.
            self.assertIn("Task id:", prompt)
            self.assertIn("Output format (MANDATORY)", prompt)

    def test_explicit_skills_function_returns_empty_on_missing_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            out = _relevant_skills(
                {"title": "x", "description": "", "skills": ["security-audit"]},
                ws,
            )
            self.assertEqual(out, [])


class StaleBundleWarning(unittest.TestCase):
    """When an explicit skill key resolves to a path that's not in the
    workspace, the orchestrator must surface a clear remediation hint
    (the user's workspace bundle is older than the installed app)."""

    def test_warning_includes_refresh_bundle_command(self) -> None:
        """The user must learn HOW to fix it from the warning text."""
        import io
        import contextlib

        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)  # no bundle at all
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                _relevant_skills(
                    {
                        "title": "x",
                        "description": "",
                        "skills": ["security-audit"],
                    },
                    ws,
                )
            output = buf.getvalue()
            self.assertIn("security-audit", output)
            self.assertIn("--refresh-bundle", output)

    def test_no_warning_when_skill_loads_successfully(self) -> None:
        """Don't cry wolf on a workspace that has the bundle."""
        import io
        import contextlib

        with tempfile.TemporaryDirectory() as d:
            ws = _stage_workspace(Path(d))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                _relevant_skills(
                    {
                        "title": "x",
                        "description": "",
                        "skills": ["security-audit"],
                    },
                    ws,
                )
            self.assertNotIn("WARN", buf.getvalue())
            self.assertNotIn("--refresh-bundle", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
