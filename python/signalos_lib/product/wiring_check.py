# signalos_lib/product/wiring_check.py
# Import-graph reachability -- now a FAST ADVISORY LINT, no longer a gate/blocker.
#
# History: "modules written but never composed into the running app" (an
# ExpenseForm.tsx that exists but the App never renders) was the dominant
# "green but not a product" failure. It used to be enforced two reactive ways --
# a hard G4 gate refusal AND an in-loop reviewer subagent -- both "connection by
# reviewer" mechanisms that a passing test suite could not itself force. Scoring
# a task "done" on FILE EXISTENCE rather than on the app RENDERING the module is
# an incentive gap no reviewer tuning fixes.
#
# The root-cause fix moves enforcement INTO the test: the acceptance/integration
# test renders the real app entry (`render(<App/>)`) and asserts user-observable
# behaviour that REQUIRES the new module to be mounted ("user adds an expense,
# sees it in the list"), so an unwired module fails a RED test through the normal
# build loop -- self-healing, no reviewer, no separate static gate.
#
# What remains here is a cheap, EARLY, INFORMATIONAL signal: `unwired_lint`
# names any source module not reachable from the app entry so the build can emit
# a crisp heads-up ("src/X is not reachable from the app entry"). It NEVER
# decides pass/fail. `find_unwired_modules` is kept as a DEMOTED no-op shim so
# the legacy static G4 wiring gate no longer refuses a build on reachability
# alone (that refusal is now the App-rendering test's job).
#
# Best-effort reachability from the app entry through the local import graph:
# unresolvable/aliased/bare imports never create false positives (only same-tree
# modules can be flagged).

from __future__ import annotations

__all__ = ["unwired_lint", "find_unwired_modules", "CODE_SUFFIXES",
           "ENTRY_NAMES", "SCAFFOLD_NAMES"]

import re
from pathlib import Path

# ES import / require() / python `from x import` -- enough for a reachability graph.
IMPORT_RE = re.compile(
    r"""(?:from\s+['"](?P<es>[^'"]+)['"]|require\(\s*['"](?P<req>[^'"]+)['"]\s*\)|"""
    r"""^\s*from\s+(?P<py>[\w.]+)\s+import)""",
    re.M,
)
ENTRY_NAMES = ("main.tsx", "main.ts", "index.tsx", "index.ts", "main.py",
               "index.js", "main.js", "app.py")
CODE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".vue", ".py", ".go", ".rs",
                 ".cs", ".java", ".kt", ".dart", ".rb", ".php")
# Scaffold entry/config files that are wired via build config, not imports.
SCAFFOLD_NAMES = ("main.tsx", "main.ts", "vite-env.d.ts", "main.py",
                  "__init__.py", "conftest.py", "setup.ts")

_TEST_MARKERS = (".test.", ".spec.", "_test.")


def _is_test(name: str) -> bool:
    return any(m in name for m in _TEST_MARKERS) or name.startswith("test_")


def unwired_lint(repo_root: Path, source_dir: str) -> list:
    """ADVISORY reachability lint (never pass/fail). Product-source modules
    UNREACHABLE from the app entry through the local import graph, as
    repo-relative POSIX paths (sorted). Empty when there is no source dir, no
    code, or no recognizable entry (cannot judge -> stay silent).

    This is an informational early signal only -- the build emits it as a
    heads-up. Reachability is ENFORCED by the acceptance/integration test that
    renders the real app entry, not by this function."""
    repo_root = Path(repo_root)
    src = repo_root / source_dir
    if not src.is_dir():
        return []
    code: dict = {}
    for p in src.rglob("*"):
        if not p.is_file() or p.suffix not in CODE_SUFFIXES:
            continue
        if _is_test(p.name):
            continue
        code[p.resolve()] = p
    if not code:
        return []
    entries = [p for p in code if p.name in ENTRY_NAMES]
    if not entries:
        return []  # no recognizable entry -> cannot judge wiring
    suffixes = ("", *CODE_SUFFIXES, *[f"/index{s}" for s in CODE_SUFFIXES])
    seen = set(entries)
    stack = list(entries)
    while stack:
        cur = stack.pop()
        try:
            text = code[cur].read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in IMPORT_RE.finditer(text):
            spec = m.group("es") or m.group("req")
            if not spec or not spec.startswith("."):
                continue  # bare package / python import -- not a local edge
            base = cur.parent / spec
            for suf in suffixes:
                cand = Path(str(base) + suf).resolve() if suf else base.resolve()
                if cand in code and cand not in seen:
                    seen.add(cand)
                    stack.append(cand)
                    break
    return sorted(
        str(code[p].relative_to(repo_root)).replace("\\", "/")
        for p in code
        if p not in seen and code[p].name not in SCAFFOLD_NAMES
    )


def find_unwired_modules(repo_root: Path, source_dir: str) -> list:
    """DEMOTED (v1.2) -- wiring is no longer a gate/blocker. Reachability is now
    enforced by the acceptance/integration test that renders the real app entry
    (an unwired module fails a RED test through the normal loop), so this shim
    returns an EMPTY list: the legacy static G4 wiring gate never refuses a build
    on reachability alone (a refusal that caused false-positive "green but the
    gate killed it" deaths and is the very reason an in-loop reviewer had to
    exist). For the informational early signal, call ``unwired_lint``."""
    return []
