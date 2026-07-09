"""Test-weakening guard (test-automation, anti-cheat layer).

In a governed AI build the build agent is *allowed* to edit its own working
test files -- real work legitimately changes tests -- but it must never
**weaken** them to fake a green run. This module compares the OLD vs NEW
content of a single JS/TS test file (vitest / jest idioms) and decides
whether the edit weakened the test.

A full TypeScript AST in Python is impractical, so detection is line/token
heuristic and deliberately biased toward **precision over recall**: it fires
only on unambiguous weakening, so a false "weakened" verdict (which would
block honest work) is rare, at the cost of possibly missing an exotic dodge.

Weakening signals detected
--------------------------
1. **Test count dropped** -- fewer ``it(`` / ``test(`` (and ``xit`` / ``fit``
   / modifier-chain variants) than before.
2. **Assertion count dropped** -- fewer *active* ``expect(`` than before.
3. **Skips / exclusions added** -- new ``.skip`` / ``.only`` / ``.todo`` /
   ``.failing`` / ``xit(`` / ``xdescribe(`` / ``fit(`` / ``fdescribe(``.
4. **Tautological assertion added** -- new always-true check such as
   ``expect(true).toBe(true)`` or ``expect(1).toBe(1)`` (an
   ``expect(LITERAL).toBe(SAME LITERAL)``). A *failing* stub such as
   ``expect(true).toBe(false)`` is NOT a tautology, so replacing it with real
   assertions reads as strengthening, never weakening.
5. **Stronger assertion replaced by a weaker one** -- ``toBeDefined`` /
   ``toBeTruthy`` / ``toBeFalsy`` / ``not.toThrow`` count went *up* while the
   count of real (strong) assertions went *down*.
6. **Assertion commented out** -- a previously-active ``expect(`` now lives in
   a ``//`` or ``/* */`` comment.
7. **Coverage threshold lowered / removed** -- a ``statements`` / ``branches``
   / ``functions`` / ``lines`` / ``threshold`` number went down or vanished
   (only when such config is present in the file).

The module is dependency-free (stdlib ``re`` only).
"""

from __future__ import annotations

__all__ = ["detect_weakening", "summarize"]

import re
from typing import Any

# --------------------------------------------------------------------------
# Token patterns (vitest / jest idioms)
# --------------------------------------------------------------------------

# An assertion opener. ``\b`` avoids matching inside identifiers.
_EXPECT_RE = re.compile(r"\bexpect\s*\(")

# A test-case declaration: it()/test() plus focus/skip prefixes (x/f) and any
# modifier chain (it.skip, test.only, it.each(...)). describe() is a *suite*,
# not a test case, so it is deliberately excluded from the count -- removing a
# describe removes its it()s, which is caught by the drop in it()/test().
# ``\b`` before the optional x/f prefix keeps ``submit(`` / ``emit(`` /
# ``latest`` / ``contest`` from matching.
_TEST_DECL_RE = re.compile(r"\b(?:x|f)?(?:it|test)(?:\s*\.\s*\w+)*\s*\(")

# Skip / focus / todo markers -- any of these *added* is a weakening. The
# it/test/describe-anchored form avoids flagging an unrelated ``.only`` /
# ``.skip`` property access; the x/f-prefixed call form is self-anchoring.
_SKIP_MARKER_RE = re.compile(
    r"\b(?:it|test|describe)\s*(?:\.\s*\w+)*\s*\.\s*(?:skip|only|todo|failing)\b"
    r"|\b(?:xit|xtest|xdescribe|fit|fdescribe)\s*\(",
)

# Weak matchers: assert "something exists / is truthy / did not throw" without
# pinning the value. Real (strong) matchers pin a value or behaviour.
_WEAK_MATCHER_RE = re.compile(
    r"\.\s*(?:toBeDefined|toBeTruthy|toBeFalsy)\s*\("
    r"|\.\s*not\s*\.\s*toThrow\s*\(",
)

# A literal (no interpolation, no expression) usable on both sides of an
# equality matcher. Template literals are excluded on purpose -- ``${...}``
# interpolation makes them non-literal.
_LIT_INNER = (
    r"(?:true|false|null|undefined|-?\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\")"
)

# expect(LITERAL) .[not.] toBe|toEqual|toStrictEqual (LITERAL)
_LITERAL_ASSERT_RE = re.compile(
    r"expect\s*\(\s*(" + _LIT_INNER + r")\s*\)\s*"
    r"\.\s*(not\s*\.\s*)?"
    r"(?:toBe|toEqual|toStrictEqual)\s*\(\s*(" + _LIT_INNER + r")\s*\)",
)

# Coverage / threshold numbers, if the file carries any config.
_THRESHOLD_RE = re.compile(
    r"\b(statements|branches|functions|lines|threshold|mincoverage)\b"
    r"\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Block comment (possibly multi-line), removed before line-level analysis.
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _active_text(src: str) -> str:
    """Source with commented-out content removed, for counting *live* tokens.

    Block comments (``/* ... */``) are dropped whole; each line is then cut at
    its first ``//``. This is coarse (a ``//`` inside a string literal cuts the
    line early) but for token *counting* it is robust: ``expect(`` / test
    declarations sit before any trailing ``//`` comment, so the count is
    preserved, while a fully ``//``-commented assertion drops out as intended.
    """
    without_block = _BLOCK_COMMENT_RE.sub("  ", src)
    return "\n".join(line.split("//", 1)[0] for line in without_block.splitlines())


def _norm_lit(lit: str) -> tuple[str, Any]:
    """Normalise a literal so ``'a' == "a"`` and ``1 == 1.0`` compare equal."""
    lit = lit.strip()
    if len(lit) >= 2 and lit[0] in "'\"" and lit[-1] == lit[0]:
        return ("s", lit[1:-1])
    if lit in ("true", "false", "null", "undefined"):
        return ("k", lit)
    try:
        return ("n", float(lit))
    except ValueError:  # pragma: no cover - defensive; regex only yields the above
        return ("r", lit)


def _count_tautologies(text: str) -> int:
    """Count always-true literal equality checks.

    ``expect(true).toBe(true)`` / ``expect(1).toBe(1)`` -> tautology.
    ``expect(true).toBe(false)`` -> NOT a tautology (a failing stub).
    ``expect(true).not.toBe(false)`` -> tautology (negated, unequal).
    """
    count = 0
    for match in _LITERAL_ASSERT_RE.finditer(text):
        left, negated, right = match.group(1), match.group(2), match.group(3)
        equal = _norm_lit(left) == _norm_lit(right)
        if equal != bool(negated):
            count += 1
    return count


def _thresholds(text: str) -> dict[str, float]:
    """Map each coverage key to its lowest numeric value in the file."""
    out: dict[str, float] = {}
    for key, value in _THRESHOLD_RE.findall(text):
        low = key.lower()
        num = float(value)
        out[low] = num if low not in out else min(out[low], num)
    return out


def _metrics(src: str) -> dict[str, Any]:
    """Compute the full metric bundle for one source string."""
    active = _active_text(src)
    expects_active = len(_EXPECT_RE.findall(active))
    expects_raw = len(_EXPECT_RE.findall(src))
    weak = len(_WEAK_MATCHER_RE.findall(active))
    tautologies = _count_tautologies(active)
    return {
        "tests": len(_TEST_DECL_RE.findall(active)),
        "expects_active": expects_active,
        "expects_raw": expects_raw,
        "commented_expects": max(0, expects_raw - expects_active),
        "skips": len(_SKIP_MARKER_RE.findall(active)),
        "tautologies": tautologies,
        "weak": weak,
        # Strong = live assertions that are neither weak nor tautological.
        "strong": max(0, expects_active - weak - tautologies),
        "thresholds": _thresholds(active),
    }


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def detect_weakening(old_src: str, new_src: str) -> dict[str, Any]:
    """Decide whether the edit from *old_src* to *new_src* weakened the test.

    Returns::

        {
          "weakened": bool,
          "reasons": [str, ...],       # one clear line per fired signal
          "metrics": {"old": {...}, "new": {...}},
        }
    """
    old = _metrics(old_src)
    new = _metrics(new_src)
    reasons: list[str] = []

    # 1. Test count dropped.
    if new["tests"] < old["tests"]:
        reasons.append(
            f"test count dropped ({old['tests']} -> {new['tests']} it()/test())"
        )

    # 2. Assertion count dropped.
    if new["expects_active"] < old["expects_active"]:
        reasons.append(
            "assertion count dropped "
            f"({old['expects_active']} -> {new['expects_active']} active expect())"
        )

    # 3. Skips / exclusions added.
    if new["skips"] > old["skips"]:
        reasons.append(
            "test skip/exclusion marker(s) added "
            f"({old['skips']} -> {new['skips']}: .skip/.only/.todo/xit/fit)"
        )

    # 4. Tautological (always-true) assertion added.
    if new["tautologies"] > old["tautologies"]:
        reasons.append(
            "tautological always-true assertion(s) added "
            f"({old['tautologies']} -> {new['tautologies']}, "
            "e.g. expect(true).toBe(true))"
        )

    # 5. Stronger assertion replaced by a weaker one.
    if new["weak"] > old["weak"] and new["strong"] < old["strong"]:
        reasons.append(
            "stronger assertion(s) replaced by weaker ones "
            f"(strong {old['strong']} -> {new['strong']}, "
            f"weak {old['weak']} -> {new['weak']}: "
            "toBeDefined/toBeTruthy/not.toThrow)"
        )

    # 6. Assertion commented out.
    if new["commented_expects"] > old["commented_expects"]:
        reasons.append(
            "active assertion(s) commented out "
            f"({old['commented_expects']} -> {new['commented_expects']} "
            "commented expect())"
        )

    # 7. Coverage threshold lowered / removed.
    for key, old_value in old["thresholds"].items():
        new_value = new["thresholds"].get(key)
        if new_value is None:
            reasons.append(f"coverage threshold '{key}' removed")
        elif new_value < old_value:
            reasons.append(
                f"coverage threshold '{key}' lowered "
                f"({old_value:g} -> {new_value:g})"
            )

    return {
        "weakened": bool(reasons),
        "reasons": reasons,
        "metrics": {"old": old, "new": new},
    }


def summarize(old_src: str, new_src: str) -> str:
    """One-line human-readable summary, for logs."""
    result = detect_weakening(old_src, new_src)
    old = result["metrics"]["old"]
    new = result["metrics"]["new"]
    stats = (
        f"tests {old['tests']}->{new['tests']}, "
        f"expects {old['expects_active']}->{new['expects_active']}, "
        f"skips {old['skips']}->{new['skips']}"
    )
    if result["weakened"]:
        return f"WEAKENED: {stats}; " + "; ".join(result["reasons"])
    return f"OK: no weakening detected ({stats})"
