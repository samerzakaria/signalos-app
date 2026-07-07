"""Deterministic test-quality gate (mechanical-verification Layer 3).

First cut of "verify the tests": acceptance evidence is only as strong as
the tests behind it, so this module deterministically detects tests that
cannot prove anything:

- **vacuous tests** -- an ``it(`` / ``test(`` block containing no assertion
  call (``expect(`` / ``assert`` / ``.should``);
- **assertion-free files** -- a ``*.test.*`` file with no assertion anywhere;
- **weak criterion links** (ADVISORY ONLY) -- a test file traced to an
  acceptance criterion (via the #6 acceptance traces) whose text never
  mentions the traced entity/operation words. This is a coarse string-level
  heuristic: precision over recall, it may under-detect legitimate indirect
  coverage, so it must never block in any mode.

The delivery pipeline folds this report through the SAME review channel as
acceptance traceability (`gate-compliance`: strict blocks, warn records);
this module only produces the deterministic report.

Scope note: analysis is manifest-driven (generated files + agent extras via
the trace manifest). Two analyzers share the report shape and blocking
semantics:

- JS/TS ``*.test.*`` files -- regex-level expect-style assertion detection;
- Python ``test_*.py`` / ``*_test.py`` files -- a precise stdlib ``ast``
  pass (fastapi-api products generate pytest suites): a test function with
  no ``assert``, no ``pytest.raises``/``warns`` context, no
  ``self.assert*``/``self.fail`` call and no ``pytest.fail/skip/xfail`` is
  vacuous; fixtures are never tests; a syntax-error file is reported as
  unanalyzable, never crashed on.

Only CLEAR vacuity is flagged, never style.
"""

from __future__ import annotations

__all__ = [
    "analyze_test_quality",
    "write_test_quality_report",
    "load_test_quality_report",
]

import ast
import json
import re
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = "signalos.test_quality.v1"

# Assertion markers: expect() (vitest/jest), node assert / assert.*, chai
# .should. Anything containing one of these is treated as asserting -- the
# gate only flags the total absence of assertions, never their quality.
_ASSERTION_RE = re.compile(r"\bexpect\s*\(|\bassert\s*[.(]|\.should\b")

# Start of an it()/test() block with a string title. Modifier chains
# (`it.skip`, `test.only`, ...) are captured; `it.each(...)("title", fn)`
# deliberately does not match (precision over recall -- first cut).
_TEST_START_RE = re.compile(
    r"\b(?:it|test)((?:\.\w+)*)\s*\(\s*(['\"`])((?:\\.|(?!\2).)*?)\2",
)

# Words carrying no entity/operation meaning for weak-link matching.
_LINK_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into",
    "are", "can", "all", "new", "app", "user", "users",
})


def _iter_test_blocks(text: str) -> Iterator[tuple[str, str, str]]:
    """Yield ``(test_name, modifier_chain, body)`` per it()/test() block.

    A block's body is coarsely taken as everything up to the next test
    start (or end of file) -- good enough to answer "does this block
    contain an assertion at all".
    """
    matches = list(_TEST_START_RE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield match.group(3), match.group(1) or "", text[match.end():end]


def _is_test_file(rel_path: str) -> bool:
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return ".test." in name


def _is_python_test_file(rel_path: str) -> bool:
    """pytest discovery conventions: ``test_*.py`` or ``*_test.py``."""
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py")
    )


# ---------------------------------------------------------------------------
# Python analyzer (stdlib ast -- precise, not regex)
# ---------------------------------------------------------------------------

# Fixture decorators: a function carrying one of these is pytest plumbing,
# never a test -- even when its name starts with test_ (rare but legal).
_FIXTURE_DECORATOR_NAMES = {"fixture"}

# pytest module-level calls that make a test non-vacuous: raises/warns
# (assertion contexts) and fail/skip/xfail/deprecated_call (explicit
# outcomes -- a test that calls pytest.fail() is claiming something).
_PYTEST_ASSERTION_FUNCS = {
    "raises", "warns", "deprecated_call", "fail", "skip", "xfail",
}


def _dotted_name(node: ast.AST) -> str:
    """Best-effort dotted name of a decorator/call target ('pytest.fixture')."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _is_fixture(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for deco in func.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        name = _dotted_name(target)
        if name.rsplit(".", 1)[-1] in _FIXTURE_DECORATOR_NAMES:
            return True
    return False


def _py_call_asserts(node: ast.Call) -> bool:
    """True when a Call node is itself an assertion-ish claim."""
    name = _dotted_name(node.func)
    if not name:
        return False
    head, _, tail = name.rpartition(".")
    # pytest.raises(...) / pytest.warns(...) / pytest.fail(...) etc. --
    # including the legacy non-context call form pytest.raises(Err, fn).
    if head == "pytest" and tail in _PYTEST_ASSERTION_FUNCS:
        return True
    # unittest-style: self.assertEqual(...), self.assertTrue(...), self.fail().
    if head == "self" and (tail.startswith("assert") or tail == "fail"):
        return True
    return False


def _py_test_is_vacuous(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A test function is vacuous when its whole subtree contains no assert
    statement and no assertion-ish call (see _py_call_asserts). Assertions in
    nested helpers/withs/loops count -- precision over recall."""
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            return False
        if isinstance(node, ast.Call) and _py_call_asserts(node):
            return False
    return True


def _iter_python_tests(
    tree: ast.Module,
) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield test functions: module-level ``test_*`` plus ``test_*`` methods
    of ``Test*`` classes (pytest collection rules). Fixtures are skipped --
    they are plumbing, not tests. Parametrized tests are plain tests (the
    parametrize decorator changes invocation count, not test-ness)."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_") and not _is_fixture(node):
                yield node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("test_") and not _is_fixture(item):
                        yield item


def _analyze_python_test_file(
    rel: str,
    text: str,
    report: dict[str, Any],
) -> bool:
    """Analyze one Python test file into *report*.

    Returns True when the file was analyzed, False when it was unanalyzable
    (recorded under ``unanalyzable_files`` -- a file the gate cannot see
    through is reported honestly, never crashed on and never guessed at).
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError) as exc:
        report["unanalyzable_files"].append({
            "file": rel,
            "reason": f"{type(exc).__name__}: {getattr(exc, 'msg', exc)}",
        })
        return False

    tests = list(_iter_python_tests(tree))
    vacuous = [t for t in tests if _py_test_is_vacuous(t)]
    for t in vacuous:
        report["vacuous_tests"].append({"file": rel, "test_name": t.name})
    # Assertion-free: every collected test in the file is vacuous. A file
    # with no collected tests (conftest-style helpers) is NOT flagged --
    # it claims no coverage, so it cannot claim it falsely.
    if tests and len(vacuous) == len(tests):
        report["assertion_free_files"].append(rel)
    return True


def _criterion_words(criterion: dict[str, Any]) -> set[str]:
    """Entity/operation words for the weak-link check (entity + workflow)."""
    words: set[str] = set()
    for key in ("entity", "workflow"):
        value = criterion.get(key)
        if not value:
            continue
        for word in re.findall(r"[a-z]+", str(value).lower()):
            if len(word) >= 3 and word not in _LINK_STOPWORDS:
                words.add(word)
    return words


def _mentions_any(text_lower: str, words: set[str]) -> bool:
    # Singular/plural tolerance: "expenses" also matches a file that only
    # says "expense" (and vice versa via substring containment).
    return any(
        word in text_lower or word.rstrip("s") in text_lower
        for word in words
    )


def analyze_test_quality(
    repo_root: Path,
    manifest: dict[str, Any] | None,
    *,
    acceptance_matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze every generated test file on disk for clear vacuity.

    Covers JS/TS ``*.test.*`` files (regex expect-style analyzer) and Python
    ``test_*.py`` / ``*_test.py`` files (ast analyzer). *manifest* is the
    generation/trace manifest whose ``files`` records name the generated
    files (the trace manifest also includes agent-written extras); analysis
    is manifest-driven so pre-existing/scaffold tests are never judged as
    generated evidence.

    Returns::

        {
          "schema_version": ...,
          "files_analyzed": n,
          "vacuous_tests": [{"file": ..., "test_name": ...}],
          "assertion_free_files": [...],
          "unanalyzable_files": [{"file": ..., "reason": ...}],
          "weak_criterion_links": [
              {"file": ..., "acceptance_id": ..., "missing_words": [...]}
          ],   # advisory only, in every mode
        }
    """
    repo_root = Path(repo_root)
    criteria_by_id = {
        str(criterion.get("id")): criterion
        for criterion in (acceptance_matrix or {}).get("criteria", []) or []
        if criterion.get("id") is not None
    }

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "files_analyzed": 0,
        "vacuous_tests": [],
        "assertion_free_files": [],
        "unanalyzable_files": [],
        "weak_criterion_links": [],
    }

    seen: set[str] = set()
    for record in (manifest or {}).get("files", []) or []:
        if not isinstance(record, dict):
            continue
        rel = str(record.get("path") or "").replace("\\", "/").lstrip("/")
        if not rel or rel in seen:
            continue
        is_js_test = _is_test_file(rel)
        is_py_test = _is_python_test_file(rel)
        if not is_js_test and not is_py_test:
            continue
        seen.add(rel)
        target = repo_root / rel
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        if is_py_test:
            if _analyze_python_test_file(rel, text, report):
                report["files_analyzed"] += 1
                # Weak-link matching below needs "does this file assert at
                # all"; for Python that is "not listed assertion-free".
                has_any_assertion = rel not in report["assertion_free_files"]
            else:
                continue  # unanalyzable: recorded, no further judgement
        else:
            report["files_analyzed"] += 1
            has_any_assertion = bool(_ASSERTION_RE.search(text))
            if not has_any_assertion:
                report["assertion_free_files"].append(rel)

            for test_name, modifiers, body in _iter_test_blocks(text):
                # .todo has no body by design; .skip is intentionally
                # disabled -- neither is a CLEAR false claim of coverage
                # (first cut: only flag unambiguous vacuity).
                if ".todo" in modifiers or ".skip" in modifiers:
                    continue
                if not _ASSERTION_RE.search(body):
                    report["vacuous_tests"].append(
                        {"file": rel, "test_name": test_name},
                    )

        # Weak criterion link (advisory): the file traces to a criterion but
        # never mentions its entity/operation words. Only meaningful when the
        # file asserts at all (assertion-free is already the stronger
        # finding) and the criterion carries concrete words.
        acceptance_id = record.get("acceptance_id")
        criterion = criteria_by_id.get(str(acceptance_id)) if acceptance_id else None
        if criterion is not None and has_any_assertion:
            words = _criterion_words(criterion)
            if words and not _mentions_any(text.lower(), words):
                report["weak_criterion_links"].append({
                    "file": rel,
                    "acceptance_id": str(acceptance_id),
                    "missing_words": sorted(words),
                })

    return report


def write_test_quality_report(
    report: dict[str, Any],
    signalos_dir: Path,
) -> Path:
    """Write to ``.signalos/product/TEST_QUALITY.json`` (next to the other
    review evidence)."""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "TEST_QUALITY.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_test_quality_report(signalos_dir: Path) -> dict[str, Any] | None:
    path = signalos_dir / "product" / "TEST_QUALITY.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
