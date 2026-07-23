"""Executable regression tests for the external expense-tracker oracle.

These tests create only temporary production-build fixtures.  They never start
SignalOS and never contact an LLM provider.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "scripts" / "backend_matrix" / "oracles" / "expense_tracker.mjs"


GOOD_APP = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Expense tracker oracle fixture</title>
  <style>
    :focus-visible { outline: 3px solid #2255cc; outline-offset: 2px; }
    article { border: 1px solid #888; margin: 8px 0; padding: 8px; }
    label { display: block; margin: 4px 0; }
  </style>
</head>
<body>
  <main>
    <h1>Expenses</h1>
    <form id="expense-form">
      <label>Description <input id="description" name="description" required></label>
      <label>Amount <input id="amount" name="amount" type="number" step="0.01" required></label>
      <label>Category
        <select id="category" name="category"><option>Food</option><option>Travel</option></select>
      </label>
      <label>Date <input id="date" name="date" type="date" required></label>
      <button type="submit">Add expense</button>
    </form>
    <label>Filter category
      <select id="filter"><option>All</option><option>Food</option><option>Travel</option></select>
    </label>
    <section id="expenses" aria-label="Expense list"></section>
  </main>
  <script>
    const storageKey = "oracle-fixture-expenses";
    let expenses = JSON.parse(localStorage.getItem(storageKey) || "[]");
    const list = document.querySelector("#expenses");
    const filter = document.querySelector("#filter");
    function save() { localStorage.setItem(storageKey, JSON.stringify(expenses)); render(); }
    function render() {
      list.replaceChildren();
      for (const expense of expenses.filter((item) => filter.value === "All" || item.category === filter.value)) {
        const article = document.createElement("article");
        const description = document.createElement("strong");
        description.textContent = expense.description;
        const details = document.createElement("span");
        details.textContent = ` ${Number(expense.amount).toFixed(2)} ${expense.category} ${expense.date} `;
        const reconcileLabel = document.createElement("label");
        reconcileLabel.textContent = "Reconciled ";
        const reconcile = document.createElement("input");
        reconcile.type = "checkbox";
        reconcile.checked = Boolean(expense.reconciled);
        reconcile.setAttribute("aria-label", `Reconciled ${expense.description}`);
        reconcile.addEventListener("change", () => {
          expense.reconciled = reconcile.checked;
          save();
        });
        reconcileLabel.append(reconcile);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "Delete";
        remove.setAttribute("aria-label", `Delete ${expense.description}`);
        remove.addEventListener("click", () => {
          expenses = expenses.filter((item) => item.id !== expense.id);
          save();
        });
        article.append(description, details, reconcileLabel, remove);
        list.append(article);
      }
    }
    document.querySelector("#expense-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      expenses.push({
        id: crypto.randomUUID(),
        description: data.get("description"),
        amount: Number(data.get("amount")),
        category: data.get("category"),
        date: data.get("date"),
        reconciled: false,
      });
      event.currentTarget.reset();
      save();
    });
    filter.addEventListener("change", render);
    render();
  </script>
</body>
</html>
"""


# Every rendered row still has its own controls, but both handlers mutate the
# first array element instead of the record represented by the clicked row.
# This is intentionally plausible: the UI looks correct and a target inserted
# at index zero passes shallow black-box checks.  The oracle must exercise a
# non-zero-index target and reject it.
FIRST_RECORD_HARDWIRED_APP = GOOD_APP.replace(
    "expense.reconciled = reconcile.checked;",
    "expenses[0].reconciled = reconcile.checked;",
).replace(
    "expenses = expenses.filter((item) => item.id !== expense.id);",
    "expenses.splice(0, 1);",
)


def _run_oracle(dist: Path, evidence: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "node",
            str(ORACLE),
            "--dist",
            str(dist),
            "--evidence",
            str(evidence),
            "--artifacts",
            str(evidence.parent / "artifacts"),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )


def test_known_good_black_box_product_passes_all_oracle_checks(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(GOOD_APP, encoding="utf-8")
    evidence = tmp_path / "evidence.json"

    completed = _run_oracle(dist, evidence)

    assert completed.returncode == 0, completed.stdout or completed.stderr
    result = json.loads(evidence.read_text(encoding="utf-8"))
    assert result["status"] == "pass"
    assert result["exitCode"] == 0
    assert result["oracleVersion"] == "1.1.0"
    assert [check["name"] for check in result["checks"]] == [
        "BOOT_FORM",
        "ADD_FIELDS",
        "DELETE_DURABLE",
        "RECONCILE_DURABLE",
        "FILTER",
        "PERSIST_ADD",
    ]
    assert all(check["status"] == "pass" for check in result["checks"])
    assert result["checks"][0]["evidence"]["keyboard"]
    assert not (tmp_path / "artifacts").exists(), "passing checks must not create screenshots"


def test_missing_production_build_is_an_infrastructure_error(tmp_path: Path) -> None:
    evidence = tmp_path / "missing-evidence.json"

    completed = _run_oracle(tmp_path / "missing-dist", evidence)

    assert completed.returncode == 2
    result = json.loads(evidence.read_text(encoding="utf-8"))
    assert result["status"] == "infra-error"
    assert result["exitCode"] == 2
    assert result["checks"] == []


def test_record_actions_must_mutate_the_selected_non_first_record(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(FIRST_RECORD_HARDWIRED_APP, encoding="utf-8")
    evidence = tmp_path / "evidence.json"

    completed = _run_oracle(dist, evidence)

    assert completed.returncode == 1, completed.stdout or completed.stderr
    result = json.loads(evidence.read_text(encoding="utf-8"))
    by_name = {check["name"]: check for check in result["checks"]}
    assert by_name["DELETE_DURABLE"]["status"] == "fail"
    assert by_name["RECONCILE_DURABLE"]["status"] == "fail"


# ---------------------------------------------------------------------------
# Seeded known-bad audit (OA-56): every oracle check must have killed at least
# one bad product. Each seed is ONE plausible, surgical mutation of GOOD_APP —
# the app still looks right and works in-session; only the targeted behaviour
# is broken. A seed that stops failing means the oracle rung regressed.
# ---------------------------------------------------------------------------

# Focus indicator suppressed everywhere (the classic "designer killed the
# outline" bug). Everything else is fully functional and accessible.
NO_FOCUS_APP = GOOD_APP.replace(
    ":focus-visible { outline: 3px solid #2255cc; outline-offset: 2px; }",
    "*:focus, *:focus-visible { outline: none; box-shadow: none; }",
)

# Description input loses its label: no label text, no aria-label, no title.
UNLABELED_INPUT_APP = GOOD_APP.replace(
    '<label>Description <input id="description" name="description" required></label>',
    '<input id="description" name="description" required>',
)

# Submit control taken out of the tab order (a real button with an accessible
# name, but tabindex=-1 -- keyboard users can never reach it).
UNREACHABLE_SUBMIT_APP = GOOD_APP.replace(
    '<button type="submit">Add expense</button>',
    '<button type="submit" tabindex="-1">Add expense</button>',
)

# Rows render the description but silently drop the amount and date.
MISSING_FIELDS_APP = GOOD_APP.replace(
    "details.textContent = ` ${Number(expense.amount).toFixed(2)} ${expense.category} ${expense.date} `;",
    "details.textContent = ` ${expense.category} `;",
)

# The filter UI exists and looks wired, but the predicate ignores it.
BROKEN_FILTER_APP = GOOD_APP.replace(
    'expenses.filter((item) => filter.value === "All" || item.category === filter.value)',
    "expenses.filter(() => true)",
)

# State lives only in memory: everything works in-session, nothing survives a
# reload (the exact bug the scenario prompt warns against).
NO_PERSIST_APP = GOOD_APP.replace(
    "function save() { localStorage.setItem(storageKey, JSON.stringify(expenses)); render(); }",
    "function save() { render(); }",
)


def _oracle_verdict(tmp_path: Path, fixture: str) -> dict:
    assert fixture != GOOD_APP, "seed mutation failed to apply"
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(fixture, encoding="utf-8")
    evidence = tmp_path / "evidence.json"
    completed = _run_oracle(dist, evidence)
    assert completed.returncode == 1, completed.stdout or completed.stderr
    result = json.loads(evidence.read_text(encoding="utf-8"))
    assert result["status"] == "fail"
    return {check["name"]: check for check in result["checks"]}


def test_suppressed_focus_outline_fails_boot_form(tmp_path: Path) -> None:
    by_name = _oracle_verdict(tmp_path, NO_FOCUS_APP)
    assert by_name["BOOT_FORM"]["status"] == "fail"


def test_unlabeled_input_fails_boot_form(tmp_path: Path) -> None:
    by_name = _oracle_verdict(tmp_path, UNLABELED_INPUT_APP)
    assert by_name["BOOT_FORM"]["status"] == "fail"


def test_keyboard_unreachable_submit_fails_boot_form(tmp_path: Path) -> None:
    by_name = _oracle_verdict(tmp_path, UNREACHABLE_SUBMIT_APP)
    assert by_name["BOOT_FORM"]["status"] == "fail"


def test_missing_amount_and_date_fails_add_fields(tmp_path: Path) -> None:
    by_name = _oracle_verdict(tmp_path, MISSING_FIELDS_APP)
    # the mutation is surgical: the form itself stays accessible
    assert by_name["BOOT_FORM"]["status"] == "pass"
    assert by_name["ADD_FIELDS"]["status"] == "fail"


def test_noop_filter_fails_filter_check(tmp_path: Path) -> None:
    by_name = _oracle_verdict(tmp_path, BROKEN_FILTER_APP)
    assert by_name["BOOT_FORM"]["status"] == "pass"
    assert by_name["FILTER"]["status"] == "fail"


def test_in_memory_only_state_fails_persist_add(tmp_path: Path) -> None:
    by_name = _oracle_verdict(tmp_path, NO_PERSIST_APP)
    assert by_name["BOOT_FORM"]["status"] == "pass"
    assert by_name["PERSIST_ADD"]["status"] == "fail"
