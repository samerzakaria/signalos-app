"""Tests (TEST-FIRST) for the repair-loop CONVERGENCE + honesty fixes.

A prior real e2e (expense tracker, agent_mode=remote, dry_run=False,
max_repair_cycles=3) proved four defects. These tests pin the fixes:

  #24 (convergence blocker): a repair cycle whose tsc errors are property/
       type-contract errors (TS2339/TS2551/TS2353/TS2741/TS2322) against a
       generated interface must INCLUDE the type module (src/types.ts) in the
       regenerated file set alongside the failing component -- so the model can
       reconcile the contract instead of playing whack-a-mole. A NON-contract
       error (TS2307 module-not-found) must NOT pull types.ts in.

  add-dependency repair action: a TS2307 for a KNOWN devDependency
       (@testing-library/user-event, ...) must trigger an add-dependency action
       -- write the dep into package.json devDependencies + re-run install --
       NOT a code regeneration.

  #23 (fake-green hard block): a dispatch RESULT.json with status=failed /
       files_written=[] (or generated files absent on disk) must make
       run_delivery fail-closed -- build_status != passed, closure_level not
       "ready", and a clear blocker present. Never report build passed off the
       scaffold stub.

  #25 (filename sanitize): an intent/workflow phrase containing ';' (or other
       illegal path chars) must sanitize to a valid PascalCase component
       filename -- no 'Category;SeeRunningTotal.tsx' on disk.

Hermetic: dispatch/validate/install are injected; no LLM, no npm, no tsc.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.repair_loop import (
    build_repair_packet,
    run_repair_loop,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _original_packet() -> dict:
    """A packet as written to scope.json: full generation with file_specs.

    Mirrors the real expense-tracker packet -- src/types.ts OWNS the Expense
    contract; Expense.tsx is the component that must agree with it.
    """
    return {
        "run_id": "conv-run",
        "profile": "react-vite",
        "wave": "1",
        "allowed_paths": ["src/**"],
        "forbidden_paths": [".env", ".signalos/"],
        "generation": {
            "profile": "react-vite",
            "product": "Expense Tracker",
            "file_specs": [
                {"path": "src/types.ts", "kind": "config",
                 "description": "TypeScript type definitions for entities: Expense."},
                {"path": "src/App.tsx", "kind": "registration"},
                {"path": "src/components/Expense.tsx", "kind": "source", "entity": "Expense"},
            ],
            "component_manifest": [
                {"componentName": "Expense", "importPath": "./components/Expense",
                 "filePath": "src/components/Expense.tsx"},
            ],
            "types_module_names": ["Expense"],
            "entities": [{"name": "Expense",
                          "fields": ["id", "description", "amount", "categoryId",
                                     "date", "isReimbursed", "createdAt", "updatedAt"]}],
            "allowed_paths": ["src/**"],
            "forbidden_paths": [".env", ".signalos/"],
        },
    }


def _validation_with_violations(violations: list[dict]) -> dict:
    return {
        "schema_version": "signalos.validation_result.v1",
        "profile": "react-vite",
        "dry_run": False,
        "results": {"build": {"status": "failed", "output": "tsc failed"}},
        "can_close_delivery": False,
        "blockers": ["build check failed"],
        "violations": violations,
    }


def _write_scope(repo: Path, packet: dict) -> Path:
    run_dir = repo / ".signalos" / "product" / "agent-runs" / packet["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scope.json").write_text(json.dumps(packet), encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------------------
# #24 -- type-contract errors pull the OWNING type module into repair scope
# ---------------------------------------------------------------------------

class TestTypeContractInRepairScope(unittest.TestCase):
    def test_ts2339_property_error_includes_types_module(self):
        """A TS2339 'Property does not exist on type' against Expense.tsx must
        regenerate BOTH Expense.tsx AND src/types.ts (the contract owner)."""
        violations = [
            {"file": "src/components/Expense.tsx", "line": 42, "code": "TS2339",
             "message": "Property 'vendor' does not exist on type 'Expense'.",
             "category": "build"},
        ]
        packet = build_repair_packet(
            repo_root=Path("."),
            cycle=1,
            failures=violations,
            validation_logs="",
            original_packet=_original_packet(),
        )
        paths = {s["path"].replace("\\", "/")
                 for s in packet["generation"]["file_specs"]}
        self.assertIn("src/components/Expense.tsx", paths)
        self.assertIn("src/types.ts", paths)

    def test_types_spec_carries_the_component_contract_error(self):
        """The regenerated types.ts spec must see the component's error so the
        model can reconcile the interface (add/remove the field) -- not just
        the component. The contract error text must reach the types spec."""
        violations = [
            {"file": "src/components/Expense.tsx", "line": 42, "code": "TS2339",
             "message": "Property 'vendor' does not exist on type 'Expense'.",
             "category": "build"},
        ]
        packet = build_repair_packet(
            repo_root=Path("."),
            cycle=1,
            failures=violations,
            validation_logs="",
            original_packet=_original_packet(),
        )
        specs = {s["path"].replace("\\", "/"): s
                 for s in packet["generation"]["file_specs"]}
        types_spec = specs["src/types.ts"]
        ec = types_spec.get("error_context") or []
        blob = json.dumps(ec)
        self.assertIn("vendor", blob)
        self.assertIn("Expense", blob)

    def test_ts2353_and_ts2741_and_ts2322_also_pull_types(self):
        for code, msg in (
            ("TS2353", "Object literal may only specify known properties, and "
                       "'notes' does not exist in type 'Expense'."),
            ("TS2741", "Property 'categoryId' is missing in type ..."),
            ("TS2322", "Type 'string' is not assignable to type 'Date'."),
            ("TS2551", "Property 'reciept' does not exist on type 'Expense'. "
                       "Did you mean 'receipt'?"),
        ):
            with self.subTest(code=code):
                violations = [
                    {"file": "src/components/Expense.tsx", "line": 5, "code": code,
                     "message": msg, "category": "build"},
                ]
                packet = build_repair_packet(
                    repo_root=Path("."),
                    cycle=1,
                    failures=violations,
                    validation_logs="",
                    original_packet=_original_packet(),
                )
                paths = {s["path"].replace("\\", "/")
                         for s in packet["generation"]["file_specs"]}
                self.assertIn("src/types.ts", paths, code)
                self.assertIn("src/components/Expense.tsx", paths, code)

    def test_non_contract_error_does_not_pull_types(self):
        """A TS2307 (module not found) is NOT a contract mismatch -- types.ts
        must NOT be dragged in (preserves the existing minimal-scope behavior)."""
        violations = [
            {"file": "src/components/Expense.tsx", "line": 1, "code": "TS2307",
             "message": "Cannot find module '@/ui/button'.", "category": "build"},
        ]
        packet = build_repair_packet(
            repo_root=Path("."),
            cycle=1,
            failures=violations,
            validation_logs="",
            original_packet=_original_packet(),
        )
        paths = [s["path"].replace("\\", "/")
                 for s in packet["generation"]["file_specs"]]
        self.assertEqual(paths, ["src/components/Expense.tsx"])


# ---------------------------------------------------------------------------
# add-dependency repair action -- TS2307 for a KNOWN devDep -> add it, not regen
# ---------------------------------------------------------------------------

class TestAddDependencyRepairAction(unittest.TestCase):
    def test_known_devdep_ts2307_triggers_add_dependency_not_regen(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            _write_scope(repo, _original_packet())
            (repo / "package.json").write_text(
                json.dumps({"name": "x", "devDependencies": {}}),
                encoding="utf-8",
            )

            dispatched: list = []
            installs: list = []

            def fake_dispatch(repo_root, packet, governance):
                dispatched.append(packet)
                return {"status": "completed", "files_written": [], "errors": []}

            def fake_install(repo_root):
                installs.append(str(repo_root))
                return {"status": "ok"}

            # After the dep is added, re-validation is clean.
            seq = iter([{
                "schema_version": "signalos.validation_result.v1",
                "profile": "react-vite", "dry_run": False,
                "results": {"build": {"status": "passed"},
                            "test": {"status": "passed"}},
                "can_close_delivery": True, "blockers": [], "violations": [],
            }])

            initial = _validation_with_violations([
                {"file": "src/components/Expense.test.tsx", "line": 2,
                 "code": "TS2307",
                 "message": "Cannot find module '@testing-library/user-event' "
                            "or its corresponding type declarations.",
                 "category": "build"},
            ])
            result = run_repair_loop(
                repo_root=repo,
                validation_result=initial,
                profile="react-vite",
                max_cycles=3,
                agent_mode="auto",
                dispatch_fn=fake_dispatch,
                validate_fn=lambda *a, **k: next(seq),
                install_fn=fake_install,
            )

            # The missing devDep must now be in package.json devDependencies.
            pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
            self.assertIn(
                "@testing-library/user-event",
                pkg.get("devDependencies", {}),
            )
            # install was run to materialize it.
            self.assertTrue(installs, "npm install should re-run after add-dep")
            # A code regen was NOT dispatched for a pure missing-package error.
            self.assertEqual(dispatched, [],
                             "add-dependency must not fall back to code regen")
            # The repair record names the add-dependency action.
            actions = [r.get("action") for r in result["repairs"]]
            self.assertIn("added_dependency", actions)

    def test_unknown_module_ts2307_still_regenerates_code(self):
        """A TS2307 for a NON-allowlisted module is a code problem, not a
        missing-package problem -> still dispatch a code regen (no dep added)."""
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            _write_scope(repo, _original_packet())
            (repo / "package.json").write_text(
                json.dumps({"name": "x", "devDependencies": {}}),
                encoding="utf-8",
            )
            dispatched: list = []

            def fake_dispatch(repo_root, packet, governance):
                dispatched.append(packet)
                return {"status": "completed", "files_written": [], "errors": []}

            seq = iter([{
                "schema_version": "signalos.validation_result.v1",
                "profile": "react-vite", "dry_run": False,
                "results": {"build": {"status": "passed"}},
                "can_close_delivery": True, "blockers": [], "violations": [],
            }])

            initial = _validation_with_violations([
                {"file": "src/App.tsx", "line": 1, "code": "TS2307",
                 "message": "Cannot find module './components/Ghost'.",
                 "category": "build"},
            ])
            run_repair_loop(
                repo_root=repo,
                validation_result=initial,
                profile="react-vite",
                max_cycles=3,
                agent_mode="auto",
                dispatch_fn=fake_dispatch,
                validate_fn=lambda *a, **k: next(seq),
                install_fn=lambda r: {"status": "ok"},
            )
            pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(pkg.get("devDependencies", {}), {})
            self.assertEqual(len(dispatched), 1)


# ---------------------------------------------------------------------------
# #23 -- dispatch failure is a HARD BLOCKER (no fake-green off the scaffold stub)
# ---------------------------------------------------------------------------

class TestDispatchFailureFailsClosed(unittest.TestCase):
    def test_failed_result_with_no_files_written_fails_delivery_closed(self):
        """A dispatch RESULT.json status=failed / files_written=[] must make
        run_delivery fail-closed: build_status != passed, not ready, a blocker
        naming the generation failure -- even if a trivially-building scaffold
        stub App.tsx exists on disk."""
        from unittest.mock import patch
        from signalos_lib.product.delivery import run_delivery

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "fake-green"

            failed_agent_result = {
                "status": "failed",
                "files_written": [],
                "errors": ["anthropic package not installed"],
            }

            # Force the chunked-LLM route and a failed dispatch; the trivially
            # green scaffold stub still exists on disk (build would "pass").
            with patch(
                "signalos_lib.product.agent_dispatch.dispatch_build_agent_chunked",
                return_value=failed_agent_result,
            ), patch(
                "signalos_lib.product.delivery._choose_dispatch_route",
                return_value="chunked-llm",
            ), patch(
                "signalos_lib.product.secrets_resolver.is_llm_available",
                return_value=True,
            ):
                closeout = run_delivery(
                    prompt="Build an expense tracker",
                    name="fake-green",
                    repo_root=repo_root,
                    mode="greenfield",
                    profile="react-vite",
                    blueprint="auto",
                    deploy="none",
                    dry_run=False,
                    agent_mode="remote",
                    max_repair_cycles=0,
                )

            self.assertNotEqual(
                closeout.get("build_status"), "passed",
                "must NOT report build passed off the scaffold stub when "
                "generation produced no real files",
            )
            self.assertNotEqual(closeout.get("closure_level"), "ready")
            limitations = " ".join(closeout.get("known_limitations", []))
            self.assertTrue(
                "generation" in limitations.lower()
                or "no real files" in limitations.lower()
                or "dispatch" in limitations.lower()
                or "not installed" in limitations.lower(),
                f"expected a generation-failure blocker; got: "
                f"{closeout.get('known_limitations')}",
            )


# ---------------------------------------------------------------------------
# #23b -- is_llm_available verifies the provider SDK is importable
# ---------------------------------------------------------------------------

class TestLlmAvailabilityVerifiesSdk(unittest.TestCase):
    def test_env_key_without_importable_sdk_is_not_available(self):
        """An env key alone must NOT report an LLM available when the provider
        SDK cannot be imported -- else dispatch is attempted and fails for
        every file while delivery still claims green."""
        import os
        from unittest.mock import patch
        import importlib
        from signalos_lib.product import secrets_resolver

        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *a, **k):
            # Simulate the anthropic SDK being absent.
            if name.split(".")[0] == "anthropic":
                return None
            return real_find_spec(name, *a, **k)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test",
                                     "SIGNALOS_LLM_PROVIDER": "anthropic"},
                        clear=False), \
             patch("importlib.util.find_spec", side_effect=fake_find_spec):
            os.environ.pop("SIGNALOS_DISABLE_LLM", None)
            self.assertFalse(
                secrets_resolver.is_llm_available(),
                "SDK-absent provider must report unavailable",
            )

    def test_test_provider_selector_is_available_without_sdk(self):
        """The deterministic 'test' provider needs no SDK -- selecting it must
        still report available so hermetic proof scenarios run."""
        import os
        from unittest.mock import patch
        from signalos_lib.product import secrets_resolver

        with patch.dict(os.environ, {"SIGNALOS_LLM_PROVIDER": "test"},
                        clear=False):
            os.environ.pop("SIGNALOS_DISABLE_LLM", None)
            self.assertTrue(secrets_resolver.is_llm_available())


# ---------------------------------------------------------------------------
# #25 -- illegal spec path is sanitized
# ---------------------------------------------------------------------------

class TestFileSpecPathSanitized(unittest.TestCase):
    def test_semicolon_workflow_phrase_sanitized_to_pascalcase(self):
        """A workflow/entity phrase with ';' must not yield an illegal path
        like 'Category;SeeRunningTotal.tsx'."""
        from signalos_lib.product.generation import build_generation_packet

        with tempfile.TemporaryDirectory() as d:
            packet = build_generation_packet(
                repo_root=Path(d),
                intent={
                    "product_name": "Budget",
                    "entities": ["Category;See Running Total"],
                    "primary_workflows": ["categorize; see running total"],
                },
                blueprint=None,
                profile="react-vite",
                design={"ui_library": {"name": "Mantine"}},
            )
            for spec in packet["file_specs"]:
                p = spec["path"]
                self.assertNotIn(";", p, p)
                # No illegal path characters at all.
                for bad in (";", ":", "*", "?", '"', "<", ">", "|"):
                    self.assertNotIn(bad, p, p)

    def test_sanitizer_collapses_to_valid_component_filename(self):
        from signalos_lib.product.generation import _sanitize_component_name

        self.assertEqual(
            _sanitize_component_name("Category;See Running Total"),
            "CategorySeeRunningTotal",
        )
        self.assertEqual(_sanitize_component_name("Expense"), "Expense")


if __name__ == "__main__":
    unittest.main()
