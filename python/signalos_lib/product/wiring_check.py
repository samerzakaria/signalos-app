# signalos_lib/product/wiring_check.py
# One shared "is every module actually wired into the app?" static check, used
# by BOTH the final G4 gate (objective refusal) and the in-loop build reviewer
# (so a module written-but-never-composed is fixed DURING the build instead of
# killing it at the gate). "Pieces without wiring" -- components written but
# never imported/composed into the running app -- is the dominant "green but not
# a product" failure. Best-effort reachability from the app entry through the
# local import graph: unresolvable/aliased/bare imports never create false
# positives (only same-tree modules can be flagged).

from __future__ import annotations

__all__ = ["find_unwired_modules", "CODE_SUFFIXES", "ENTRY_NAMES", "SCAFFOLD_NAMES"]

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


def find_unwired_modules(repo_root: Path, source_dir: str) -> list:
    """Product-source modules UNREACHABLE from the app entry through the local
    import graph, as repo-relative POSIX paths (sorted). Empty when there is no
    source dir, no code, or no recognizable entry (cannot judge -> stay silent)."""
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
