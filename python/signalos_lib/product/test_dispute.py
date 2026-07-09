# signalos_lib/product/test_dispute.py
# Escape valve for a DEADLOCKED build task. When the build agent cannot make a
# plan-authored acceptance test pass after its fix budget, the engine used to
# silently blame the code and skip -- it never considered that the TEST itself
# might be broken/unpassable (a hallucinated planner wrote an assertion no valid
# implementation can satisfy, or a literal always-fail like
# `expect(true).toBe(false)`). This module diagnoses that, in two SAFE layers:
#
#   1. deterministic_test_health() -- zero-LLM structural check. Catches the
#      unmistakable cases (literal always-fail stubs, no real assertions, an
#      uncollectable/empty suite) with high confidence and no spend. This alone
#      catches the exact fixture bug that deadlocked every model.
#   2. A SECOND-OPINION classify on a DIFFERENT model with FRESH context (only
#      the task + test + errors, none of the builder's failed-attempt transcript
#      -- decorrelating the builder's "my code is right" anchoring). It only
#      CLASSIFIES test-broken vs impl-wrong; it never edits the exam.
#
# Deliberately NOT here: auto-amending the test. Per external review, letting an
# LLM arbiter rewrite the exam on its own word is "rubber-stamping" -- the only
# real guard is verifying an amendment against a reference implementation, which
# we do not have yet. So a broken test is RECORDED as a dispute for a separate
# arbiter/human, and the build still fails-closed (the builder never edits its
# own exam). The amendment loop is a deliberate follow-up.

from __future__ import annotations

__all__ = [
    "deterministic_test_health",
    "arbiter_messages",
    "parse_arbiter_verdict",
    "record_dispute",
]

import json
import re
from pathlib import Path
from typing import Optional

# An active (non-commented) assertion.
_EXPECT_RE = re.compile(r"\bexpect\s*\(")
_IT_RE = re.compile(r"\b(?:it|test)\s*\(")
# `expect(<literal>).toBe(<literal>)` -- a hardcoded comparison of two constants.
# When the two literals DIFFER it is an always-FAIL stub (unpassable by any
# implementation); when they are EQUAL it is a tautology (always pass).
_LITERAL_TOBE_RE = re.compile(
    r"expect\s*\(\s*(true|false|\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\")\s*\)"
    r"\s*\.\s*(?:toBe|toEqual|toStrictEqual)\s*\(\s*"
    r"(true|false|\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\")\s*\)"
)


def _strip_comments(src: str) -> str:
    """Remove block comments, then cut each line at its first // -- so counts
    reflect only ACTIVE (executable) code."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    out = []
    for line in src.splitlines():
        idx = line.find("//")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def _norm_lit(tok: str) -> str:
    t = tok.strip()
    if t and t[0] in "'\"":
        return t[1:-1]
    try:
        return str(float(t))
    except ValueError:
        return t


def deterministic_test_health(test_src: str, errors: str = "") -> dict:
    """Structural, zero-LLM verdict on whether a test is itself broken/unpassable.

    Returns {"broken": bool, "reason": str, "confidence": "high"|"low"}.
    Conservative: only reports broken on unmistakable signals, so a genuine
    impl failure is never misread as a broken test."""
    active = _strip_comments(test_src or "")
    expects = _EXPECT_RE.findall(active)
    cases = _IT_RE.findall(active)

    lit_pairs = _LITERAL_TOBE_RE.findall(active)
    always_fail = [(a, b) for a, b in lit_pairs if _norm_lit(a) != _norm_lit(b)]

    # 1. Every active assertion is a constant-vs-different-constant compare
    #    (e.g. `expect(true).toBe(false)`) -> no implementation can ever pass it.
    if expects and len(always_fail) >= len(expects):
        return {"broken": True, "confidence": "high",
                "reason": (f"all {len(expects)} assertion(s) compare two constants that "
                           f"differ (e.g. `expect({always_fail[0][0]}).toBe({always_fail[0][1]})`) "
                           "-- an always-fail stub no implementation can satisfy")}

    # 2. The suite has test cases but no real assertions (empty/placeholder).
    if cases and not expects:
        return {"broken": True, "confidence": "high",
                "reason": (f"{len(cases)} test case(s) but zero active assertions "
                           "-- an empty placeholder spec")}

    # 3. Vitest/jest could not collect the file at all (no runnable suite).
    if errors and re.search(r"no test suite found|no tests? found", errors, re.I):
        return {"broken": True, "confidence": "high",
                "reason": "the test runner reported no runnable test suite in the file"}

    return {"broken": False, "confidence": "low",
            "reason": "no structural defect found; deadlock likely an implementation gap"}


_ARBITER_SYSTEM = (
    "You are an impartial test arbiter for a governed build. A build agent could "
    "not make an acceptance test pass after its full fix budget. Decide, from the "
    "test and the failing output alone, whether the TEST ITSELF is broken/"
    "unpassable (an assertion no correct implementation could satisfy, a "
    "self-contradiction, a hardcoded always-fail, or a broken import/setup in the "
    "test), OR whether the implementation is simply incomplete/wrong. Be strict: "
    "prefer 'implementation wrong' unless the test is clearly at fault. You do NOT "
    "edit anything. Reply with ONLY a JSON object: "
    '{"test_broken": true|false, "reason": "<one sentence>"}.'
)


def arbiter_messages(task_name: str, test_src: str, errors: str,
                     impl_digest: str = "") -> "tuple[str, str]":
    """Build the (system, user) messages for the second-opinion classify. FRESH
    context by construction: only the task, the test, a short impl digest, and
    the errors -- never the builder's failed-attempt transcript."""
    user = (
        f"TASK: {task_name}\n\n"
        f"ACCEPTANCE TEST (read-only spec):\n```\n{(test_src or '').strip()[:6000]}\n```\n\n"
        + (f"IMPLEMENTATION SUMMARY:\n{impl_digest.strip()[:2000]}\n\n" if impl_digest else "")
        + f"PERSISTENT TEST FAILURE OUTPUT:\n```\n{(errors or '').strip()[:3000]}\n```\n\n"
        "Is the TEST broken/unpassable, or is the implementation wrong? "
        'Reply with ONLY {"test_broken": bool, "reason": "..."}.'
    )
    return _ARBITER_SYSTEM, user


def parse_arbiter_verdict(text: str) -> dict:
    """Parse the arbiter's JSON verdict. Fail-closed: any parse failure or
    missing field -> test_broken False (do NOT dispute on a garbled verdict)."""
    if not text:
        return {"test_broken": False, "reason": "no arbiter response"}
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return {"test_broken": False, "reason": "arbiter response was not JSON"}
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return {"test_broken": False, "reason": "arbiter JSON did not parse"}
    return {"test_broken": bool(obj.get("test_broken", False)),
            "reason": str(obj.get("reason", "")).strip() or "arbiter flagged the test"}


def record_dispute(repo_root: Path, task_id: str, task_name: str, reason: str,
                   source: str, test_path: str = "") -> None:
    """Append an auditable dispute record. Every dispute is logged regardless of
    outcome (a governed build needs the trail even when nothing is changed)."""
    try:
        d = Path(repo_root) / ".signalos" / "product"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"task_id": task_id, "task_name": task_name, "test_path": test_path,
               "reason": reason, "source": source}
        with (d / "TEST_DISPUTES.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
