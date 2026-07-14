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
