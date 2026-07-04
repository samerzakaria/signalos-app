"""#39 breadth: prove the generation machinery generalizes across product
types, not just the one expense-tracker that was e2e-proven.

This is the CREDIT-FREE half of breadth. It drives a MATRIX of diverse
product shapes through the REAL pipeline -- `build_generation_packet` (spec
building, entity resolution, component manifest) + the DETERMINISTIC
`_render_react_vite_files` renderer (types.ts, App.tsx, foundation, components)
-- and asserts the invariants every generated product must hold:

  * types.ts renders and names every entity interface
  * App.tsx renders each component PROPLESS (`<Comp />`, never `<Comp x=...>`)
    -- the #36 prop-drift invariant, structurally guaranteed on this path
  * the shared foundation files are present (setup / theme / layout / css)
  * every component + its test file renders
  * the #37 operations contract derives correctly per entity (create + delete
    always; toggle for each boolean field)

The LLM-PATH matrix (does the model generate correct COMPONENT BODIES for a
CRM vs an expense tracker?) genuinely needs a credit-funded run and is tracked
separately in the ledger -- this harness does NOT claim to cover it. What it
DOES lock down: the deterministic scaffold + contracts hold across product
shapes, so a regression in any of them fails here, fast and hermetically.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.generation import build_generation_packet
from signalos_lib.product.agent_dispatch import (
    _operations_contract,
    _render_react_vite_files,
)


# Each product: (product_name, product_type, entities-with-fields, the entity
# expected to yield a TOGGLE op + its boolean field). Deliberately diverse
# domains so a shape-specific regression surfaces.
_MATRIX = [
    {
        "product_name": "Expense Tracker",
        "product_type": "expense-tracker",
        "entities": [
            {"name": "Expense", "fields": ["id", "amount", "category", "reimbursed", "date"]},
            {"name": "Category", "fields": ["id", "name", "color"]},
        ],
        "toggle": ("Expense", "reimbursed"),
    },
    {
        "product_name": "Task Board",
        "product_type": "task-management",
        "entities": [
            {"name": "Task", "fields": ["id", "title", "priority", "completed", "due_date"]},
            {"name": "Project", "fields": ["id", "name"]},
        ],
        "toggle": ("Task", "completed"),
    },
    {
        "product_name": "Contact CRM",
        "product_type": "crm",
        "entities": [
            {"name": "Contact", "fields": ["id", "name", "email", "active"]},
            {"name": "Company", "fields": ["id", "name", "industry"]},
        ],
        "toggle": ("Contact", "active"),
    },
    {
        "product_name": "Habit Tracker",
        "product_type": "habit-tracker",
        "entities": [
            {"name": "Habit", "fields": ["id", "name", "streak", "archived"]},
            {"name": "Entry", "fields": ["id", "habit", "completed", "date"]},
        ],
        "toggle": ("Habit", "archived"),
    },
    {
        "product_name": "Inventory Manager",
        "product_type": "inventory",
        "entities": [
            {"name": "Item", "fields": ["id", "name", "quantity", "price", "featured"]},
            {"name": "Supplier", "fields": ["id", "name", "contact"]},
        ],
        "toggle": ("Item", "featured"),
    },
    {
        "product_name": "Bookmark Vault",
        "product_type": "bookmarks",
        "entities": [
            {"name": "Bookmark", "fields": ["id", "url", "title", "favorite"]},
            {"name": "Tag", "fields": ["id", "label"]},
        ],
        "toggle": ("Bookmark", "favorite"),
    },
]

_FOUNDATION = [
    "src/types.ts",
    "src/test/setup.ts",
    "src/ui/theme.ts",
    "src/ui/layouts/AppLayout.tsx",
    "src/product.css",
    "src/App.tsx",
]


def _blueprint_for(product: dict) -> dict:
    surfaces = [
        {
            "id": f"{e['name'].lower()}-manager",
            "component": f"{e['name']}Manager",
            "entity": e["name"],
        }
        for e in product["entities"]
    ]
    return {
        "id": product["product_type"],
        "entities": product["entities"],
        "workflows": [{"name": f"manage {e['name'].lower()}"} for e in product["entities"]],
        "ui_detail": {"surfaces": surfaces},
    }


def _packet_for(product: dict, root: Path) -> dict:
    intent = {
        "product_name": product["product_name"],
        "product_type": product["product_type"],
        "entities": [e["name"] for e in product["entities"]],
        "primary_workflows": [f"manage {e['name'].lower()}" for e in product["entities"]],
    }
    return build_generation_packet(
        repo_root=root,
        intent=intent,
        blueprint=_blueprint_for(product),
        profile="react-vite",
        design={
            "ui_library": {"name": "Mantine"},
            "state_management": {"name": "zustand"},
            "form_handling": {"name": "react-hook-form"},
            "design_tokens": {"primary_color": "#2563eb", "font_family": "Inter"},
        },
    )


class GenerationBreadthMatrix(unittest.TestCase):
    def test_foundation_and_types_render_for_every_product(self):
        for product in _MATRIX:
            with self.subTest(product=product["product_name"]):
                with tempfile.TemporaryDirectory() as d:
                    packet = _packet_for(product, Path(d))
                    files = _render_react_vite_files(packet)
                    for f in _FOUNDATION:
                        self.assertIn(f, files, f"{product['product_name']}: missing {f}")
                    types = files["src/types.ts"]
                    for e in product["entities"]:
                        self.assertIn(
                            f"interface {e['name']}", types,
                            f"{product['product_name']}: types.ts missing {e['name']}",
                        )

    def test_app_renders_every_component_propless(self):
        # The #36 invariant, structurally guaranteed on the deterministic path:
        # App renders `<Comp />` and NEVER passes a prop `<Comp x={...}>`.
        for product in _MATRIX:
            with self.subTest(product=product["product_name"]):
                with tempfile.TemporaryDirectory() as d:
                    packet = _packet_for(product, Path(d))
                    files = _render_react_vite_files(packet)
                    app = files["src/App.tsx"]
                    comps = [m["componentName"] for m in packet.get("component_manifest", [])]
                    self.assertTrue(comps, product["product_name"])
                    for comp in comps:
                        self.assertIn(f"<{comp} />", app,
                                      f"{product['product_name']}: App not rendering <{comp} />")
                        # No prop ever passed: `<Comp ` followed by a letter.
                        self.assertIsNone(
                            re.search(rf"<{comp}\s+[A-Za-z]", app),
                            f"{product['product_name']}: App passes a prop to <{comp}>",
                        )

    def test_every_component_and_test_file_renders(self):
        for product in _MATRIX:
            with self.subTest(product=product["product_name"]):
                with tempfile.TemporaryDirectory() as d:
                    packet = _packet_for(product, Path(d))
                    files = _render_react_vite_files(packet)
                    for m in packet["component_manifest"]:
                        comp = m["componentName"]
                        src = f"src/components/{comp}.tsx"
                        test = f"src/components/{comp}.test.tsx"
                        self.assertIn(src, files, src)
                        self.assertIn(test, files, test)
                        self.assertTrue(files[src].strip())

    def test_operations_contract_derives_per_entity(self):
        # create + delete always; the designated boolean field yields a toggle.
        for product in _MATRIX:
            with self.subTest(product=product["product_name"]):
                toggle_entity, toggle_field = product["toggle"]
                for e in product["entities"]:
                    ops = _operations_contract(e["name"], e["fields"])
                    keys = [o["key"] for o in ops]
                    self.assertIn("create", keys)
                    self.assertIn("delete", keys)
                    # never an inline edit/update op (the #37 exclusion)
                    self.assertFalse(any("edit" in k or "update" in k for k in keys))
                    if e["name"] == toggle_entity:
                        self.assertIn(f"toggle_{toggle_field}", keys,
                                      f"{product['product_name']}: {e['name']} missing toggle")


if __name__ == "__main__":
    unittest.main()
