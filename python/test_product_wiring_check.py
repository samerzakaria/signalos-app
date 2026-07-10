"""Shared wiring check: modules written but never composed into the app entry
are flagged. Used by BOTH the final G4 gate and the in-loop build reviewer."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.wiring_check import find_unwired_modules  # noqa: E402


def _repo(files: dict) -> Path:
    d = Path(tempfile.mkdtemp())
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return d


class TestWiringCheck(unittest.TestCase):
    def test_orphan_util_is_flagged(self):
        d = _repo({
            "src/main.tsx": "import App from './App';\n",
            "src/App.tsx": "import { ExpenseList } from './components/ExpenseList';\nexport default function App(){return null}\n",
            "src/components/ExpenseList.tsx": "export function ExpenseList(){return null}\n",
            "src/utils/currency.ts": "export function dollarsToCents(){return 0}\n",  # never imported
        })
        orphans = find_unwired_modules(d, "src")
        self.assertEqual(orphans, ["src/utils/currency.ts"])

    def test_fully_wired_is_clean(self):
        d = _repo({
            "src/main.tsx": "import App from './App';\n",
            "src/App.tsx": "import { fmt } from './utils/currency';\nexport default function App(){return fmt()}\n",
            "src/utils/currency.ts": "export function fmt(){return ''}\n",
        })
        self.assertEqual(find_unwired_modules(d, "src"), [])

    def test_test_files_and_scaffold_ignored(self):
        d = _repo({
            "src/main.tsx": "import App from './App';\n",
            "src/App.tsx": "export default function App(){return null}\n",
            "src/App.test.tsx": "import App from './App';\n",   # test file -> ignored
            "src/setup.ts": "// scaffold\n",                    # scaffold name -> ignored
        })
        self.assertEqual(find_unwired_modules(d, "src"), [])

    def test_no_entry_stays_silent(self):
        d = _repo({"src/utils/currency.ts": "export const x=1\n"})  # no main/index
        self.assertEqual(find_unwired_modules(d, "src"), [])

    def test_transitive_wiring_followed(self):
        d = _repo({
            "src/main.tsx": "import App from './App';\n",
            "src/App.tsx": "import { a } from './a';\nexport default function App(){return a()}\n",
            "src/a.ts": "import { b } from './b';\nexport function a(){return b()}\n",
            "src/b.ts": "export function b(){return 1}\n",   # reached transitively via a
            "src/c.ts": "export function c(){return 2}\n",   # orphan
        })
        self.assertEqual(find_unwired_modules(d, "src"), ["src/c.ts"])

    def test_missing_source_dir(self):
        self.assertEqual(find_unwired_modules(Path(tempfile.mkdtemp()), "src"), [])


class TestGateOrchestratorDelegates(unittest.TestCase):
    def test_gate_uses_the_shared_check(self):
        # The gate's _unwired_modules must return the same result as the shared
        # function (delegation, no drift).
        from signalos_lib.product.gate_orchestrator import GateOrchestrator
        self.assertTrue(hasattr(GateOrchestrator, "_unwired_modules"))
        # Constants are the single source (repointed, not duplicated).
        from signalos_lib.product import wiring_check
        self.assertIs(GateOrchestrator._CODE_SUFFIXES, wiring_check.CODE_SUFFIXES)
        self.assertIs(GateOrchestrator._SCAFFOLD_NAMES, wiring_check.SCAFFOLD_NAMES)


if __name__ == "__main__":
    unittest.main()
