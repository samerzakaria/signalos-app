"""Wiring reachability: now a FAST ADVISORY LINT (`unwired_lint`), no longer a
gate/blocker. `find_unwired_modules` is a DEMOTED no-op shim so the legacy
static G4 wiring gate never refuses a build on reachability alone -- wiring is
enforced by the acceptance/integration test that renders the real app entry."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.wiring_check import (  # noqa: E402
    find_unwired_modules,
    unwired_lint,
)


def _repo(files: dict) -> Path:
    d = Path(tempfile.mkdtemp())
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return d


class TestUnwiredLint(unittest.TestCase):
    """The advisory lint still computes reachability (crisp early signal)."""

    def test_orphan_util_is_flagged(self):
        d = _repo({
            "src/main.tsx": "import App from './App';\n",
            "src/App.tsx": "import { ExpenseList } from './components/ExpenseList';\nexport default function App(){return null}\n",
            "src/components/ExpenseList.tsx": "export function ExpenseList(){return null}\n",
            "src/utils/currency.ts": "export function dollarsToCents(){return 0}\n",  # never imported
        })
        orphans = unwired_lint(d, "src")
        self.assertEqual(orphans, ["src/utils/currency.ts"])

    def test_fully_wired_is_clean(self):
        d = _repo({
            "src/main.tsx": "import App from './App';\n",
            "src/App.tsx": "import { fmt } from './utils/currency';\nexport default function App(){return fmt()}\n",
            "src/utils/currency.ts": "export function fmt(){return ''}\n",
        })
        self.assertEqual(unwired_lint(d, "src"), [])

    def test_test_files_and_scaffold_ignored(self):
        d = _repo({
            "src/main.tsx": "import App from './App';\n",
            "src/App.tsx": "export default function App(){return null}\n",
            "src/App.test.tsx": "import App from './App';\n",   # test file -> ignored
            "src/setup.ts": "// scaffold\n",                    # scaffold name -> ignored
        })
        self.assertEqual(unwired_lint(d, "src"), [])

    def test_no_entry_stays_silent(self):
        d = _repo({"src/utils/currency.ts": "export const x=1\n"})  # no main/index
        self.assertEqual(unwired_lint(d, "src"), [])

    def test_transitive_wiring_followed(self):
        d = _repo({
            "src/main.tsx": "import App from './App';\n",
            "src/App.tsx": "import { a } from './a';\nexport default function App(){return a()}\n",
            "src/a.ts": "import { b } from './b';\nexport function a(){return b()}\n",
            "src/b.ts": "export function b(){return 1}\n",   # reached transitively via a
            "src/c.ts": "export function c(){return 2}\n",   # orphan
        })
        self.assertEqual(unwired_lint(d, "src"), ["src/c.ts"])

    def test_missing_source_dir(self):
        self.assertEqual(unwired_lint(Path(tempfile.mkdtemp()), "src"), [])


class TestFindUnwiredIsDemoted(unittest.TestCase):
    """`find_unwired_modules` is a no-op shim: it never blocks a build. An
    unwired module that the advisory lint DOES flag is deliberately NOT flagged
    by the demoted gate function -- reachability is the App-rendering test's
    job now, not a static gate refusal."""

    def test_returns_empty_even_with_an_orphan(self):
        d = _repo({
            "src/main.tsx": "import App from './App';\n",
            "src/App.tsx": "export default function App(){return null}\n",
            "src/utils/currency.ts": "export function dollarsToCents(){return 0}\n",  # orphan
        })
        # The advisory lint sees the orphan...
        self.assertEqual(unwired_lint(d, "src"), ["src/utils/currency.ts"])
        # ...but the demoted gate function refuses to block on it.
        self.assertEqual(find_unwired_modules(d, "src"), [])


class TestGateOrchestratorDelegates(unittest.TestCase):
    def test_gate_still_exposes_the_check_and_shared_constants(self):
        # The gate keeps its (now non-blocking) hook + shares the constant
        # source with the wiring module (repointed, not duplicated).
        from signalos_lib.product.gate_orchestrator import GateOrchestrator
        self.assertTrue(hasattr(GateOrchestrator, "_unwired_modules"))
        from signalos_lib.product import wiring_check
        self.assertIs(GateOrchestrator._CODE_SUFFIXES, wiring_check.CODE_SUFFIXES)
        self.assertIs(GateOrchestrator._SCAFFOLD_NAMES, wiring_check.SCAFFOLD_NAMES)


if __name__ == "__main__":
    unittest.main()
