"""Phase 12 release-scenario coverage for factory governance flows."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib import cli
from signalos_lib.commands import init
from signalos_lib.commands import validate_cmd as validate_command
from signalos_lib.commands import verify_product
from signalos_lib.intent import import_source_document, persist_prompt_source


def _init_product(root: Path, name: str, *, keep_existing: bool = False) -> None:
    args = [str(root), "--yes", "--no-git", "--name", name]
    if keep_existing:
        args.append("--keep-existing")
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = init.main(args)
    if code != 0:
        raise AssertionError(f"signalos init failed with {code}: {stdout.getvalue()}")


def _run_layer1_validate(root: Path, validator: str | None = None) -> tuple[int, dict]:
    args = ["--repo-root", str(root), "--group", "layer1", "--json"]
    if validator:
        args.extend(["--validator", validator])
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = validate_command.main(args)
    return code, json.loads(stdout.getvalue())


def _write_unknowns(root: Path) -> None:
    unknowns = root / ".signalos" / "unknowns.json"
    unknowns.parent.mkdir(parents=True, exist_ok=True)
    unknowns.write_text(
        json.dumps(
            [
                {
                    "id": "human-next-step",
                    "question": "Confirm the first governed product work item.",
                    "status": "open",
                }
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _cli_commands() -> set[str]:
    parser = cli._build_parser()  # type: ignore[attr-defined]
    commands: set[str] = set()
    for action in parser._actions:  # argparse does not expose this publicly.
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            commands.update(str(key) for key in choices)
    return commands


class FactoryReleaseScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-release-scenario-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_repo_creation_reaches_layer1_valid_after_required_human_inputs(self) -> None:
        root = self.tmp / "task-management"

        _init_product(root, "Task Management")
        persist_prompt_source("Build a task management system", repo_root=root)
        _write_unknowns(root)

        code, payload = _run_layer1_validate(root)

        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["summary"]["failed"], 0)
        self.assertTrue((root / ".signalos" / "sources" / "initial-intent.json").is_file())
        self.assertTrue((root / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md").is_file())
        self.assertFalse((Path.cwd() / ".signalos").exists(), "test must not create repo-root .signalos")

    def test_existing_repo_adoption_preserves_files_and_reaches_layer1_valid(self) -> None:
        root = self.tmp / "adopted-product"
        source = root / "src" / "App.tsx"
        source.parent.mkdir(parents=True)
        package = root / "package.json"
        package.write_text(
            json.dumps(
                {
                    "scripts": {"build": "vite build", "dev": "vite"},
                    "dependencies": {"react": "^19.0.0", "vite": "^7.0.0"},
                }
            ),
            encoding="utf-8",
        )
        source.write_text("export function App() { return <main />; }\n", encoding="utf-8")
        original_package = package.read_bytes()
        original_source = source.read_bytes()

        _init_product(root, "Adopted Product", keep_existing=True)
        code, payload = _run_layer1_validate(root)

        self.assertEqual(package.read_bytes(), original_package)
        self.assertEqual(source.read_bytes(), original_source)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["status"], "PASS")
        self.assertTrue((root / ".signalos" / "adoption" / "surface-inventory.json").is_file())
        self.assertTrue((root / ".signalos" / "adoption" / "unknowns.json").is_file())
        adoption_intent = json.loads(
            (root / ".signalos" / "sources" / "initial-intent.json").read_text(encoding="utf-8")
        )
        self.assertEqual(adoption_intent["source_type"], "existing_repo_adoption")

    def test_prompt_and_prd_inputs_are_traceable_layer1_sources(self) -> None:
        root = self.tmp / "source-product"
        _init_product(root, "Source Product")
        _write_unknowns(root)
        prompt = persist_prompt_source("I need a task management system", repo_root=root)
        prd = self.tmp / "Task PRD.md"
        prd.write_text("# Task PRD\n\nUsers create, assign, and complete tasks.\n", encoding="utf-8")
        source = import_source_document(prd, repo_root=root, source_kind="prd")

        code, payload = _run_layer1_validate(root, "layer1-source-traceability")

        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(prompt["record_path"], ".signalos/sources/initial-intent.json")
        self.assertTrue((root / source["stored_path"]).is_file())
        self.assertTrue((root / source["record_path"]).is_file())

    def test_verify_product_writes_release_evidence_shape(self) -> None:
        root = self.tmp / "verified-product"
        _init_product(root, "Verified Product")
        persist_prompt_source("Build a verified product", repo_root=root)
        _write_unknowns(root)

        payload = verify_product.verify_product(
            root,
            profile_id="generic",
            wave="release-test",
            include_qa=False,
            include_e2e=False,
        )

        self.assertEqual(payload["schema_version"], "signalos.verify_product.v1")
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["evidence_dir"], ".signalos/evidence/release-test")
        self.assertEqual(payload["evidence_path"], ".signalos/evidence/release-test/verify-product.json")
        evidence = json.loads((root / payload["evidence_path"]).read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "PASS")
        checks = {check["name"]: check for check in evidence["checks"]}
        self.assertEqual(checks["workspace"]["status"], "PASS")
        self.assertEqual(checks["profile"]["status"], "PASS")
        self.assertEqual(checks["build"]["status"], "SKIP")
        self.assertIn("does not declare", checks["build"]["reason"])

    def test_release_readiness_cli_contract_when_agent12_api_is_available(self) -> None:
        if "release-readiness" not in _cli_commands() or importlib.util.find_spec(
            "signalos_lib.commands.release_readiness"
        ) is None:
            self.skipTest("Agent 12 release-readiness CLI/API is not merged yet")

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "signalos",
                "release-readiness",
                "--repo-root",
                str(self.tmp / "missing-product"),
                "--json",
            ])

        self.assertNotEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertIn(str(payload["status"]).upper(), {"FAIL", "BLOCKED"})
        self.assertTrue(payload.get("blockers"), payload)


if __name__ == "__main__":
    unittest.main()
