"""test_skill_validators.py - Smart artifact-based skill enforcement.

Covers validate_skill_artifacts() and each registered validator.
Validators key off file-shape proofs (artifact exists with the right
sections / no obvious lint failures) -- no LLM-as-judge.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.skill_validators import (
    SkillViolation,
    VALIDATORS,
    validate_skill_artifacts,
)


def _task(**overrides) -> dict:
    base = {
        "task": "T-001",
        "step_id": "T-001",
        "title": "test",
        "wave": "1",
        "skills": [],
    }
    base.update(overrides)
    return base


class SecurityAuditLint(unittest.TestCase):
    def test_rejects_innerHTML_assignment_from_variable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "foo.ts").write_text("el.innerHTML = userInput;\n")
            violations = validate_skill_artifacts(
                ["security-audit"], _task(), root, ["foo.ts"], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("innerHTML", violations[0].message)

    def test_allows_textContent_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "foo.ts").write_text("el.textContent = userInput;\n")
            violations = validate_skill_artifacts(
                ["security-audit"], _task(), root, ["foo.ts"], "",
            )
            self.assertEqual(violations, [])

    def test_rejects_dangerouslySetInnerHTML(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "App.tsx").write_text('<div dangerouslySetInnerHTML={{__html: x}} />')
            violations = validate_skill_artifacts(
                ["security-audit"], _task(), root, ["App.tsx"], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("dangerouslySetInnerHTML", violations[0].message)

    def test_rejects_eval(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "x.js").write_text("eval(userCode);\n")
            violations = validate_skill_artifacts(
                ["security-audit"], _task(), root, ["x.js"], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("eval", violations[0].message)

    def test_rejects_subprocess_shell_true(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "x.py").write_text("subprocess.run('rm -rf', shell=True)\n")
            violations = validate_skill_artifacts(
                ["security-audit"], _task(), root, ["x.py"], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("shell=True", violations[0].message)

    def test_rejects_hardcoded_api_key_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config.ts").write_text(
                'const apiKey = "sk-ABC123def456ghi789jklmnopqr";\n'
            )
            violations = validate_skill_artifacts(
                ["security-audit"], _task(), root, ["config.ts"], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("hardcoded", violations[0].message.lower())

    def test_no_violations_on_clean_code(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "App.tsx").write_text(
                "export function App() { return <div>{userName}</div>; }\n"
            )
            violations = validate_skill_artifacts(
                ["security-audit"], _task(), root, ["App.tsx"], "",
            )
            self.assertEqual(violations, [])


class TestGenerationArtifact(unittest.TestCase):
    def test_passes_when_a_test_file_was_produced(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "foo.test.ts").write_text("it('works', () => {});")
            violations = validate_skill_artifacts(
                ["test-generation"], _task(), root, ["foo.ts", "foo.test.ts"], "",
            )
            self.assertEqual(violations, [])

    def test_fails_when_no_test_file_was_produced(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "foo.ts").write_text("export const x = 1;")
            violations = validate_skill_artifacts(
                ["test-generation"], _task(), root, ["foo.ts"], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("test file", violations[0].message)

    def test_python_test_file_naming_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "test_storage.py").write_text("def test_storage(): pass")
            violations = validate_skill_artifacts(
                ["test-generation"], _task(), root, ["storage.py", "test_storage.py"], "",
            )
            self.assertEqual(violations, [])


class ComprehensiveCodeReviewArtifact(unittest.TestCase):
    def test_fails_when_artifact_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            violations = validate_skill_artifacts(
                ["comprehensive-code-review"], _task(task="rev-1"), root, [], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("review", violations[0].message.lower())

    def test_passes_when_artifact_has_all_severity_sections(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            reviews = root / ".signalos" / "reviews"
            reviews.mkdir(parents=True)
            (reviews / "rev-1.md").write_text(
                "# Review\n## Critical\nNone\n## High\nNone\n## Medium\nx\n## Low\ny\n"
            )
            violations = validate_skill_artifacts(
                ["comprehensive-code-review"], _task(task="rev-1"), root, [], "",
            )
            self.assertEqual(violations, [])

    def test_fails_when_severity_section_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            reviews = root / ".signalos" / "reviews"
            reviews.mkdir(parents=True)
            (reviews / "rev-1.md").write_text(
                "# Review\n## Critical\nNone\n## High\nx\n"
                # Missing Medium and Low
            )
            violations = validate_skill_artifacts(
                ["comprehensive-code-review"], _task(task="rev-1"), root, [], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("Medium", violations[0].message)
            self.assertIn("Low", violations[0].message)


class SystematicDebuggingArtifact(unittest.TestCase):
    def test_passes_with_all_four_sections(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            debug = root / ".signalos" / "debug"
            debug.mkdir(parents=True)
            (debug / "T-001.md").write_text(
                "# Debug\n## Reproduce\nx\n## Hypothesis\ny\n## Test\nz\n## Fix\nw\n"
            )
            violations = validate_skill_artifacts(
                ["systematic-debugging"], _task(), root, [], "",
            )
            self.assertEqual(violations, [])

    def test_fails_missing_section(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            debug = root / ".signalos" / "debug"
            debug.mkdir(parents=True)
            (debug / "T-001.md").write_text(
                "## Reproduce\nx\n## Fix\nw\n"  # missing Hypothesis + Test
            )
            violations = validate_skill_artifacts(
                ["systematic-debugging"], _task(), root, [], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("Hypothesis", violations[0].message)
            self.assertIn("Test", violations[0].message)


class ReceivingCodeReviewArtifact(unittest.TestCase):
    def test_fails_when_artifact_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            violations = validate_skill_artifacts(
                ["receiving-code-review"], _task(task="resp-1"), root, [], "",
            )
            self.assertEqual(len(violations), 1)

    def test_passes_with_mapping_section(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            resp = root / ".signalos" / "responses"
            resp.mkdir(parents=True)
            (resp / "resp-1.md").write_text(
                "## Addressed\n- comment 1\n## Declined\n- comment 2 (reason: X)\n"
            )
            violations = validate_skill_artifacts(
                ["receiving-code-review"], _task(task="resp-1"), root, [], "",
            )
            self.assertEqual(violations, [])


class WritingPlansArtifact(unittest.TestCase):
    def test_passes_when_plan_yaml_exists(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "PLAN.tasks.yaml").write_text("wave: '1'\ntasks: []\n")
            violations = validate_skill_artifacts(
                ["writing-plans"], _task(), root, [], "",
            )
            self.assertEqual(violations, [])

    def test_fails_when_plan_yaml_missing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            violations = validate_skill_artifacts(
                ["writing-plans"], _task(), Path(d), [], "",
            )
            self.assertEqual(len(violations), 1)


class UsingGitWorktreesArtifact(unittest.TestCase):
    def test_passes_when_worktree_state_has_entries(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            signalos = root / ".signalos"
            signalos.mkdir()
            (signalos / "worktree-state.json").write_text(json.dumps({
                "worktrees": [{"id": "wt-1", "branch": "task-T1"}],
            }))
            violations = validate_skill_artifacts(
                ["using-git-worktrees"], _task(), root, [], "",
            )
            self.assertEqual(violations, [])

    def test_does_not_complain_in_no_worktree_mode(self) -> None:
        # When bash isn't available, the orchestrator runs sequentially
        # with no worktree-state.json. This is a legitimate path; we
        # shouldn't punish the user for the orchestrator's own choice.
        with tempfile.TemporaryDirectory() as d:
            violations = validate_skill_artifacts(
                ["using-git-worktrees"], _task(), Path(d), [], "",
            )
            self.assertEqual(violations, [])


class VerificationBeforeCompletion(unittest.TestCase):
    def test_warning_only_when_no_verification_section(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            violations = validate_skill_artifacts(
                ["verification-before-completion"], _task(), Path(d), ["a.ts"], "",
            )
            # Severity is "warning"; the orchestrator emits a warn line
            # but does not fail the task.
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].severity, "warning")

    def test_passes_when_response_has_verification_heading(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            violations = validate_skill_artifacts(
                ["verification-before-completion"],
                _task(),
                Path(d),
                ["a.ts"],
                task_response="## Verification\nRan tsc, ran tests, manually clicked Approve.\n",
            )
            self.assertEqual(violations, [])


class UnknownSkillsSkippedSilently(unittest.TestCase):
    def test_advisory_skills_have_no_validator(self) -> None:
        # The cognitive / process skills (brainstorming, memory, ...)
        # should NOT have validators -- their value is the prompt
        # content, not an artifact.
        # "design" was advisory pre-M-W4 but landed as an enforced
        # validator in the M-W4 design-shape work (audit §6.7).
        for advisory_key in (
            "brainstorming", "memory", "context", "intent-router",
            "compress-context", "operator-tooling",
        ):
            self.assertNotIn(advisory_key, VALIDATORS,
                            f"Advisory skill {advisory_key!r} should not have a validator")

    def test_validate_skips_unknown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            violations = validate_skill_artifacts(
                ["totally-not-a-skill", "brainstorming"], _task(), Path(d), [], "",
            )
            self.assertEqual(violations, [])


class MultipleSkillsCompose(unittest.TestCase):
    def test_two_skills_each_with_violations_returns_both(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "foo.ts").write_text("eval(x);\n")  # security violation
            # No test file, no review artifact, no plan -- all should fire.
            violations = validate_skill_artifacts(
                ["security-audit", "test-generation"],
                _task(), root, ["foo.ts"], "",
            )
            # Expect at least 2 violations from these two skills.
            self.assertGreaterEqual(len(violations), 2)
            skills_hit = {v.skill for v in violations}
            self.assertEqual(skills_hit, {"security-audit", "test-generation"})


# ---------------------------------------------------------------------------
# G3 design validator (audit §6.7 three-shape contract)
# ---------------------------------------------------------------------------

def _design_dir(root: Path, wave_id: str) -> Path:
    p = root / ".signalos" / "designs" / wave_id
    p.mkdir(parents=True, exist_ok=True)
    return p


class DesignValidatorThreeShapes(unittest.TestCase):
    """Per audit §6.7 the validator accepts any one of three shapes."""

    def test_doc_plus_populated_prototype_dir_passes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ddir = _design_dir(root, "W7.1")
            (ddir / "design-doc.md").write_text(
                "# Design\nChosen approach: feature-flagged React component.\n",
            )
            (ddir / "prototype").mkdir()
            (ddir / "prototype" / "Card.stories.tsx").write_text("export const x = 1;")
            violations = validate_skill_artifacts(
                ["design"], _task(wave="W7.1"), root, ["Card.stories.tsx"], "",
            )
            self.assertEqual(violations, [])

    def test_doc_plus_external_design_reference_passes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ddir = _design_dir(root, "W7.1")
            (ddir / "design-doc.md").write_text(
                "# Design\n\n## External design reference\n"
                "Figma file: https://figma.com/design/abc/Spec\n",
            )
            violations = validate_skill_artifacts(
                ["design"], _task(wave="W7.1"), root, [], "",
            )
            self.assertEqual(violations, [])

    def test_doc_plus_no_UI_attestation_passes_when_task_has_no_ui_writes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ddir = _design_dir(root, "W7.1")
            (ddir / "design-doc.md").write_text(
                "# Design\nUI surface: none — see attestation\n"
                "Backend schema migration only.\n",
            )
            violations = validate_skill_artifacts(
                ["design"], _task(wave="W7.1"), root,
                ["migrations/001_add_col.sql", "src/schema.py"], "",
            )
            self.assertEqual(violations, [])


class DesignValidatorRefusals(unittest.TestCase):
    def test_missing_design_doc_fails(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            violations = validate_skill_artifacts(
                ["design"], _task(wave="W7.1"), root, [], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("design-doc.md missing", violations[0].message)

    def test_empty_prototype_dir_fails(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ddir = _design_dir(root, "W7.1")
            (ddir / "design-doc.md").write_text("# Design\nchosen approach\n")
            (ddir / "prototype").mkdir()  # exists but empty
            violations = validate_skill_artifacts(
                ["design"], _task(wave="W7.1"), root, [], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("prototype/", violations[0].message)
            self.assertIn("empty", violations[0].message)

    def test_no_ui_attestation_contradicted_by_ui_writes_fails(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ddir = _design_dir(root, "W7.1")
            (ddir / "design-doc.md").write_text(
                "# Design\nUI surface: none — see attestation\n",
            )
            violations = validate_skill_artifacts(
                ["design"], _task(wave="W7.1"), root,
                ["src/components/Card.tsx", "src/page.html"], "",
            )
            self.assertEqual(len(violations), 1)
            msg = violations[0].message
            self.assertIn("no-UI-attestation contradicts", msg)
            self.assertIn("Card.tsx", msg)

    def test_no_shape_match_fails_with_three_shape_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ddir = _design_dir(root, "W7.1")
            (ddir / "design-doc.md").write_text(
                "# Design\nSome plain prose about the approach.\n",
            )
            violations = validate_skill_artifacts(
                ["design"], _task(wave="W7.1"), root, [], "",
            )
            self.assertEqual(len(violations), 1)
            msg = violations[0].message
            self.assertIn("three valid shapes", msg)

    def test_task_without_wave_id_fails_with_clear_message(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            violations = validate_skill_artifacts(
                ["design"], _task(wave=""), root, [], "",
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("'wave' id", violations[0].message)


class DesignValidatorRegistration(unittest.TestCase):
    def test_design_validator_registered_in_dispatch_table(self) -> None:
        self.assertIn("design", VALIDATORS)


if __name__ == "__main__":
    unittest.main()
