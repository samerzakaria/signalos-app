from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.profiles import (  # noqa: E402
    dry_run_profile_validation,
    find_unresolved_placeholders,
    load_profile,
    validate_generated_profile_files,
    validate_profile_contract,
)
from signalos_lib.profiles.loader import PROFILE_SCHEMA_VERSION  # noqa: E402


class ProfileValidationTests(unittest.TestCase):
    def test_builtin_profile_contracts_validate_deterministically(self) -> None:
        generic = validate_profile_contract("generic")
        react_vite = validate_profile_contract("react-vite")

        self.assertTrue(generic.ok, generic.to_dict())
        self.assertTrue(react_vite.ok, react_vite.to_dict())
        self.assertIn("core/governance/Governance/SOUL-DOCUMENT.md", generic.checked_paths)
        self.assertIn(".github/workflows/signalos-ci.yml", react_vite.checked_paths)
        self.assertEqual(
            validate_profile_contract("react-vite").to_dict(),
            react_vite.to_dict(),
        )

    def test_enabled_ci_file_must_be_backed_by_ci_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture_dir = Path(tmp)
            manifest = load_profile("react-vite").to_dict()
            manifest["id"] = "missing-ci-template"
            manifest["ci"]["templates"] = []
            (fixture_dir / "missing-ci-template.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            report = validate_profile_contract("missing-ci-template", profile_dir=fixture_dir)

        self.assertFalse(report.ok)
        self.assertIn("ci-templates-missing", _codes(report))
        self.assertIn("ci-file-not-backed-by-template", _codes(report))

    def test_duplicate_template_destinations_block_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture_dir = Path(tmp)
            manifest = load_profile("generic").to_dict()
            manifest["id"] = "duplicate-destination"
            manifest["required_templates"][1]["destination"] = manifest["required_templates"][0][
                "destination"
            ]
            (fixture_dir / "duplicate-destination.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            report = validate_profile_contract("duplicate-destination", profile_dir=fixture_dir)

        self.assertFalse(report.ok)
        self.assertIn("template-destination-duplicate", _codes(report))

    def test_generated_required_file_placeholders_block_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_generated_files(repo, "generic")
            target = repo / "core/governance/Governance/SOUL-DOCUMENT.md"
            target.write_text("# Soul Document - {Product Name}\n", encoding="utf-8")

            report = validate_generated_profile_files(repo, "generic")

        self.assertFalse(report.ok)
        self.assertIn("generated-file-unresolved-placeholder", _codes(report))
        issue = report.issues[0]
        self.assertEqual(issue.path, "core/governance/Governance/SOUL-DOCUMENT.md")
        self.assertEqual(issue.details["token"], "{Product Name}")

    def test_generated_missing_required_file_blocks_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_generated_files(repo, "generic")
            (repo / "core/governance/QUALITY_CHECK.md").unlink()

            report = validate_generated_profile_files(repo, "generic")

        self.assertFalse(report.ok)
        self.assertIn("generated-file-missing", _codes(report))

    def test_github_actions_expressions_are_not_placeholder_findings(self) -> None:
        content = 'run: bash "${{ env.SIGNALOS_PATH }}/core/governance/Validators/gate.sh"\n'

        self.assertEqual(find_unresolved_placeholders(content), [])

    def test_marker_rule_documented_in_backticks_is_not_a_placeholder(self) -> None:
        # OA-50: a governance artifact that DOCUMENTS the marker rule names the
        # forbidden tokens in inline code. It must not trip the scanner over its
        # own rulebook (this exact §P7 Documentation-Standards line refused the
        # G0 sign in the deepseekv4pro funded run).
        rule = (
            "## Documentation Standards\n\n"
            "- All governance artifacts must be free of reserved markers -- no "
            "`TBD`, `TODO`, `FIXME`, `XXX`, `[DATE]`, `{{...}}`, or "
            "`<to be filled>` tokens.\n"
        )
        self.assertEqual(find_unresolved_placeholders(rule), [])

    def test_bare_slots_still_block_after_code_span_masking(self) -> None:
        # The masking must not open a hole: a genuinely UNFILLED, bare slot is
        # still a blocking finding.
        bare = "# Soul\n\nPurpose: <to be filled by the founder>\nRatified: [DATE]\n"
        kinds = {f["kind"] for f in find_unresolved_placeholders(bare)}
        self.assertIn("fill-token", kinds)
        self.assertIn("date-token", kinds)

    def test_placeholder_inside_fenced_code_block_is_ignored(self) -> None:
        fenced = "Example template line:\n\n```\nCreated: [DATE]\n```\n\nEnd.\n"
        self.assertEqual(find_unresolved_placeholders(fenced), [])

    def test_dry_run_combines_contract_and_generated_file_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_generated_files(repo, "react-vite")
            ci_file = repo / ".github/workflows/signalos-ci.yml"
            ci_file.parent.mkdir(parents=True, exist_ok=True)
            ci_file.write_text(
                'run: bash "${{ env.SIGNALOS_PATH }}/core/governance/Validators/gate.sh"\n',
                encoding="utf-8",
            )

            report = dry_run_profile_validation("react-vite", repo_root=repo)

        self.assertTrue(report.ok, report.to_dict())
        self.assertIn(".github/workflows/signalos-ci.yml", report.checked_paths)

    def test_schema_version_constant_still_matches_loader(self) -> None:
        self.assertEqual(PROFILE_SCHEMA_VERSION, 1)


def _write_generated_files(repo: Path, profile_id: str) -> None:
    profile = load_profile(profile_id)
    destinations = [template.destination for template in profile.required_templates if template.required]
    if profile.ci.enabled:
        destinations.extend(profile.ci.files)
    for destination in destinations:
        path = repo / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated content\n", encoding="utf-8")


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


if __name__ == "__main__":
    unittest.main()
