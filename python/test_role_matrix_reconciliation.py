# test_role_matrix_reconciliation.py
# #17 Edit 3.5 — lock the three role matrices together so a future edit cannot
# reopen the segregation-of-duties bypass.
#
# Source of truth: artifact `required_roles` (gate_artifacts.json). The Rust
# `role_can_sign` matrix (ipc.rs) and the Python `GATE_ROLES` map
# (gate_orchestrator.py) must AGREE with it per gate:
#
#   * one-directional invariant (Rust): for PO/PE/QA, if role_can_sign(role, g)
#     grants a gate, that role MUST be in the gate's required_roles. No
#     over-permission (the QA@G4 hole this pass closed would fail here). DevOps
#     is the documented deploy-role exception and is not asserted.
#   * GATE_ROLES: each configured signer role must be authorised for its gate.

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.artifacts import GATE_ARTIFACTS  # noqa: E402
from signalos_lib.product.gate_orchestrator import GATE_ROLES  # noqa: E402

_IPC_RS = Path(__file__).resolve().parent.parent / "src-tauri" / "src" / "ipc.rs"


def _required_roles_by_gate() -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for gate, artifacts in GATE_ARTIFACTS.items():
        gid = int(gate[1:])
        roles: set[str] = set()
        for a in artifacts:
            roles.update(a.required_roles)
        out[gid] = roles
    return out


def _parse_rust_role_can_sign() -> dict[str, set[int]]:
    """Parse the ("ROLE", gate) match arms from role_can_sign in ipc.rs."""
    text = _IPC_RS.read_text(encoding="utf-8")
    m = re.search(r"fn role_can_sign\(.*?\)\s*->\s*bool\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m, "could not locate role_can_sign in ipc.rs"
    body = m.group(1)
    grants: dict[str, set[int]] = {}
    # Match arms like: ("PO", 0) | ("PO", 1) => true,
    for arm in re.findall(r'\("([A-Za-z]+)",\s*(\d+)\)', body):
        role, gid = arm[0], int(arm[1])
        grants.setdefault(role, set()).add(gid)
    return grants


class TestRoleMatrixReconciliation(unittest.TestCase):
    def test_rust_matrix_never_over_permissions(self):
        required = _required_roles_by_gate()
        grants = _parse_rust_role_can_sign()
        violations = []
        for role in ("PO", "PE", "QA"):
            for gid in grants.get(role, set()):
                if role not in required.get(gid, set()):
                    violations.append((role, gid, sorted(required.get(gid, set()))))
        self.assertEqual(
            violations,
            [],
            f"role_can_sign grants a role a gate not in its required_roles: {violations}",
        )

    def test_gate_roles_are_authorised(self):
        required = _required_roles_by_gate()
        mismatches = []
        for gate, role in GATE_ROLES.items():
            gid = int(gate[1:])
            if role not in required.get(gid, set()):
                mismatches.append((gate, role, sorted(required.get(gid, set()))))
        self.assertEqual(
            mismatches,
            [],
            f"GATE_ROLES assigns an unauthorised signer role: {mismatches}",
        )

    def test_po_denied_on_qa_gate_across_sources(self):
        required = _required_roles_by_gate()
        grants = _parse_rust_role_can_sign()
        # G5 is QA-only in the source of truth and in the Rust matrix.
        self.assertEqual(required[5], {"QA"})
        self.assertNotIn(5, grants.get("PO", set()))
        self.assertNotEqual(GATE_ROLES["G5"], "PO")


if __name__ == "__main__":
    unittest.main()
