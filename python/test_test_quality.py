"""Tests for mechanical-verification Layer 3: the deterministic test-quality
gate (first cut of "verify the tests").

- vacuous-test detection on realistic vitest content (true positives AND
  clean files -- only CLEAR vacuity is flagged, never style);
- assertion-free file detection;
- weak criterion links are ADVISORY in every mode (the never-blocks
  invariant, mirroring traceability's advisory channel);
- review-gate folding: strict blocks, warn records.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.test_quality import (
    analyze_test_quality,
    write_test_quality_report,
)
from signalos_lib.product.delivery import _apply_test_quality_review


_MIXED_VITEST = """\
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { TaskList } from "./TaskList";

describe("TaskList", () => {
  it("renders without crashing", () => {
    render(<TaskList tasks={[]} />);
  });

  it("shows the task title", () => {
    render(<TaskList tasks={[{ id: 1, title: "Write tests" }]} />);
    expect(screen.getByText("Write tests")).toBeInTheDocument();
  });

  it.todo("supports drag and drop");

  it.skip("flaky in CI", () => {
    render(<TaskList tasks={[]} />);
  });
});
"""

_CLEAN_VITEST = """\
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { TaskForm } from "./TaskForm";

describe("TaskForm", () => {
  it("submits a new task", () => {
    render(<TaskForm />);
    expect(screen.getByRole("button", { name: /add task/i })).toBeEnabled();
  });

  test("shows validation errors", () => {
    render(<TaskForm />);
    expect(screen.getByText("Title is required")).toBeVisible();
  });
});
"""

_ASSERTION_FREE = """\
import { render } from "@testing-library/react";
import { it } from "vitest";
import { App } from "./App";

it("mounts", () => {
  render(<App />);
});
"""


def _write(repo: Path, rel: str, content: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _manifest(*records: dict) -> dict:
    return {"files": list(records)}


# ---------------------------------------------------------------------------
# analyze_test_quality
# ---------------------------------------------------------------------------

class TestAnalyzeTestQuality:
    def test_vacuous_test_detected_in_mixed_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/TaskList.test.tsx", _MIXED_VITEST)
        report = analyze_test_quality(
            tmp_path,
            _manifest({"path": "src/TaskList.test.tsx", "kind": "test"}),
        )
        assert report["files_analyzed"] == 1
        assert report["vacuous_tests"] == [
            {"file": "src/TaskList.test.tsx", "test_name": "renders without crashing"},
        ]
        # File has assertions elsewhere -> not assertion-free
        assert report["assertion_free_files"] == []

    def test_clean_file_produces_no_findings(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/TaskForm.test.tsx", _CLEAN_VITEST)
        report = analyze_test_quality(
            tmp_path,
            _manifest({"path": "src/TaskForm.test.tsx", "kind": "test"}),
        )
        assert report["files_analyzed"] == 1
        assert report["vacuous_tests"] == []
        assert report["assertion_free_files"] == []
        assert report["weak_criterion_links"] == []

    def test_todo_and_skip_are_not_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/TaskList.test.tsx", _MIXED_VITEST)
        report = analyze_test_quality(
            tmp_path,
            _manifest({"path": "src/TaskList.test.tsx", "kind": "test"}),
        )
        flagged = {v["test_name"] for v in report["vacuous_tests"]}
        assert "supports drag and drop" not in flagged
        assert "flaky in CI" not in flagged

    def test_assertion_free_file_detected(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/App.test.tsx", _ASSERTION_FREE)
        report = analyze_test_quality(
            tmp_path,
            _manifest({"path": "src/App.test.tsx", "kind": "test"}),
        )
        assert report["assertion_free_files"] == ["src/App.test.tsx"]
        # The vacuous block inside is also individually reported
        assert {"file": "src/App.test.tsx", "test_name": "mounts"} in report[
            "vacuous_tests"
        ]

    def test_non_test_and_missing_files_ignored(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/App.tsx", "export const App = 1;")
        report = analyze_test_quality(
            tmp_path,
            _manifest(
                {"path": "src/App.tsx", "kind": "source"},
                {"path": "src/Ghost.test.tsx", "kind": "test"},  # not on disk
            ),
        )
        assert report["files_analyzed"] == 0
        assert report["vacuous_tests"] == []
        assert report["assertion_free_files"] == []

    def test_weak_criterion_link_flagged_when_entity_never_mentioned(
        self, tmp_path: Path,
    ) -> None:
        content = """\
import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { Widget } from "./Widget";

it("shows the total", () => {
  render(<Widget />);
  expect(screen.getByText("Total")).toBeVisible();
});
"""
        _write(tmp_path, "src/Widget.test.tsx", content)
        matrix = {
            "criteria": [
                {"id": "AC-002", "entity": "Expense", "workflow": None,
                 "description": "CRUD operations for Expense"},
            ],
        }
        report = analyze_test_quality(
            tmp_path,
            _manifest({
                "path": "src/Widget.test.tsx", "kind": "test",
                "acceptance_id": "AC-002",
            }),
            acceptance_matrix=matrix,
        )
        assert report["weak_criterion_links"] == [{
            "file": "src/Widget.test.tsx",
            "acceptance_id": "AC-002",
            "missing_words": ["expense"],
        }]

    def test_criterion_words_present_means_no_weak_link(
        self, tmp_path: Path,
    ) -> None:
        content = """\
import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { ExpenseList } from "./ExpenseList";

it("lists expenses", () => {
  render(<ExpenseList />);
  expect(screen.getByText("expenses")).toBeVisible();
});
"""
        _write(tmp_path, "src/ExpenseList.test.tsx", content)
        matrix = {
            "criteria": [
                {"id": "AC-002", "entity": "Expense", "workflow": None,
                 "description": "CRUD operations for Expense"},
            ],
        }
        report = analyze_test_quality(
            tmp_path,
            _manifest({
                "path": "src/ExpenseList.test.tsx", "kind": "test",
                "acceptance_id": "AC-002",
            }),
            acceptance_matrix=matrix,
        )
        assert report["weak_criterion_links"] == []

    def test_report_persists(self, tmp_path: Path) -> None:
        import json

        signalos = tmp_path / ".signalos"
        report = analyze_test_quality(tmp_path, None)
        path = write_test_quality_report(report, signalos)
        assert path.name == "TEST_QUALITY.json"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == "signalos.test_quality.v1"
        assert loaded["files_analyzed"] == 0


# ---------------------------------------------------------------------------
# Python analyzer (ast) -- fastapi-api products generate pytest suites
# ---------------------------------------------------------------------------

_MIXED_PYTEST = '''\
import pytest
from httpx import AsyncClient

from src.main import app


@pytest.fixture
def client():
    return AsyncClient(app=app, base_url="http://test")


@pytest.fixture
def test_data():
    # test_-prefixed name + no assert: would be a vacuous false positive
    # if fixtures were treated as tests.
    return {"title": "x"}


@pytest.mark.parametrize("path", ["/tasks", "/tasks/1"])
def test_routes_respond(path, client):
    response = client.get(path)
    assert response.status_code == 200


def test_create_task_smoke(client):
    client.post("/tasks", json={"title": "x"})  # no assertion at all


class TestTaskValidation:
    def test_rejects_empty_title(self, client):
        with pytest.raises(ValueError):
            validate_title("")

    def test_touches_endpoint(self, client):
        client.get("/tasks")  # vacuous method
'''

_CLEAN_PYTEST = '''\
from src.calc import add


def test_add_two_numbers():
    assert add(1, 2) == 3


def test_add_is_commutative():
    assert add(2, 3) == add(3, 2)
'''

_RAISES_ONLY_PYTEST = '''\
import pytest

from src.calc import divide


def test_divide_by_zero_raises():
    with pytest.raises(ZeroDivisionError):
        divide(1, 0)
'''

_UNITTEST_STYLE_PYTEST = '''\
import unittest


class TestThing(unittest.TestCase):
    def test_equal(self):
        self.assertEqual(1, 1)

    def test_explicit_fail_path(self):
        self.fail("not implemented is an explicit claim")
'''

_ALL_VACUOUS_PYTEST = '''\
def test_first():
    print("ran")


def test_second():
    value = 1 + 1
'''

_SYNTAX_ERROR_PYTEST = '''\
def test_broken(:
    assert True
'''


class TestPythonAnalyzer:
    def test_vacuous_functions_detected_in_mixed_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_tasks.py", _MIXED_PYTEST)
        report = analyze_test_quality(
            tmp_path,
            _manifest({"path": "tests/test_tasks.py", "kind": "test"}),
        )
        assert report["files_analyzed"] == 1
        flagged = {v["test_name"] for v in report["vacuous_tests"]}
        assert flagged == {"test_create_task_smoke", "test_touches_endpoint"}
        # Parametrized test with a plain assert is NOT flagged; fixtures are
        # plumbing, never tests -- even the test_-prefixed one.
        assert "test_routes_respond" not in flagged
        assert "test_data" not in flagged
        # File has asserting tests elsewhere -> not assertion-free.
        assert report["assertion_free_files"] == []

    def test_clean_pytest_file_produces_no_findings(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_calc.py", _CLEAN_PYTEST)
        report = analyze_test_quality(
            tmp_path,
            _manifest({"path": "tests/test_calc.py", "kind": "test"}),
        )
        assert report["files_analyzed"] == 1
        assert report["vacuous_tests"] == []
        assert report["assertion_free_files"] == []
        assert report["unanalyzable_files"] == []

    def test_pytest_raises_only_test_is_not_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_divide.py", _RAISES_ONLY_PYTEST)
        report = analyze_test_quality(
            tmp_path,
            _manifest({"path": "tests/test_divide.py", "kind": "test"}),
        )
        assert report["vacuous_tests"] == []
        assert report["assertion_free_files"] == []

    def test_unittest_style_assertions_count(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_thing.py", _UNITTEST_STYLE_PYTEST)
        report = analyze_test_quality(
            tmp_path,
            _manifest({"path": "tests/test_thing.py", "kind": "test"}),
        )
        assert report["vacuous_tests"] == []
        assert report["assertion_free_files"] == []

    def test_all_vacuous_file_is_assertion_free(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_nothing.py", _ALL_VACUOUS_PYTEST)
        report = analyze_test_quality(
            tmp_path,
            _manifest({"path": "tests/test_nothing.py", "kind": "test"}),
        )
        assert {v["test_name"] for v in report["vacuous_tests"]} == {
            "test_first", "test_second",
        }
        assert report["assertion_free_files"] == ["tests/test_nothing.py"]

    def test_syntax_error_file_is_unanalyzable_never_a_crash(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, "tests/test_broken.py", _SYNTAX_ERROR_PYTEST)
        report = analyze_test_quality(
            tmp_path,
            _manifest({"path": "tests/test_broken.py", "kind": "test"}),
        )
        assert report["files_analyzed"] == 0
        assert len(report["unanalyzable_files"]) == 1
        entry = report["unanalyzable_files"][0]
        assert entry["file"] == "tests/test_broken.py"
        assert "SyntaxError" in entry["reason"]
        # An unanalyzable file makes no vacuity claims either way.
        assert report["vacuous_tests"] == []
        assert report["assertion_free_files"] == []

    def test_non_test_python_files_ignored(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/main.py", "app = 1\n")
        _write(tmp_path, "tests/conftest.py", "import pytest\n")
        report = analyze_test_quality(
            tmp_path,
            _manifest(
                {"path": "src/main.py", "kind": "source"},
                {"path": "tests/conftest.py", "kind": "test"},
            ),
        )
        assert report["files_analyzed"] == 0


# ---------------------------------------------------------------------------
# Review-gate folding (strict blocks, warn records, advisory never blocks)
# ---------------------------------------------------------------------------

def _review(mode: str = "strict", status: str = "pass") -> dict:
    return {
        "schema_version": "signalos.review_gate.v1",
        "status": status,
        "mode": mode,
        "blocking": False,
        "checks": {},
        "findings": [],
    }


def _quality(vacuous=None, assertion_free=None, weak=None) -> dict:
    return {
        "schema_version": "signalos.test_quality.v1",
        "files_analyzed": 3,
        "vacuous_tests": vacuous or [],
        "assertion_free_files": assertion_free or [],
        "weak_criterion_links": weak or [],
    }


class TestApplyTestQualityReview:
    def test_vacuous_blocks_in_strict_mode(self) -> None:
        result = _apply_test_quality_review(
            _review("strict"),
            _quality(vacuous=[{"file": "src/A.test.tsx", "test_name": "renders"}]),
        )
        assert result["status"] == "blocked"
        assert result["blocking"] is True
        assert result["checks"]["test_quality"] is False
        assert any(
            "vacuous test 'renders'" in f and "src/A.test.tsx" in f
            for f in result["findings"]
        )

    def test_assertion_free_blocks_in_strict_mode(self) -> None:
        result = _apply_test_quality_review(
            _review("strict"),
            _quality(assertion_free=["src/B.test.tsx"]),
        )
        assert result["status"] == "blocked"
        assert result["blocking"] is True

    def test_vacuous_recorded_not_blocking_in_warn_mode(self) -> None:
        result = _apply_test_quality_review(
            _review("warn"),
            _quality(vacuous=[{"file": "src/A.test.tsx", "test_name": "renders"}]),
        )
        assert result["status"] == "warn"
        assert result["blocking"] is False
        assert any("test-quality" in f for f in result["findings"])

    def test_weak_links_are_advisory_never_blocking(self) -> None:
        """The invariant: weak criterion links must never block -- even in
        strict mode they are advisory findings only."""
        result = _apply_test_quality_review(
            _review("strict"),
            _quality(weak=[{
                "file": "src/W.test.tsx",
                "acceptance_id": "AC-001",
                "missing_words": ["task"],
            }]),
        )
        assert result["status"] == "pass"
        assert result["blocking"] is False
        assert result["checks"]["test_quality"] is True
        advisories = [f for f in result["findings"] if "advisory" in f]
        assert len(advisories) == 1
        assert "AC-001" in advisories[0]

    def test_clean_report_sets_check_true(self) -> None:
        result = _apply_test_quality_review(_review("strict"), _quality())
        assert result["status"] == "pass"
        assert result["checks"]["test_quality"] is True
        assert result["findings"] == []

    def test_none_report_leaves_review_untouched(self) -> None:
        review = _review("strict")
        assert _apply_test_quality_review(review, None) is review
        assert review["checks"] == {}
