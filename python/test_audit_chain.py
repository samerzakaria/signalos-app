"""Audit-ledger tamper-evidence (Wave 0.2).

A forward-linked hash chain over AUDIT_TRAIL.jsonl: each signed row commits to
the previous row's hash, so an in-place edit, insertion, deletion, or reorder is
detectable. Rows written by appenders that do not (yet) chain are tolerated as
boundaries -- a following chained row still commits to their exact bytes.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib import sign


class AuditChainTests(unittest.TestCase):
    def _append(self, audit_log: Path, root: Path, i: int) -> None:
        art = root / f"a{i}.md"
        art.write_text(f"content {i}\n", encoding="utf-8")
        sign._append_audit(audit_log, "alice", "PE", "G0", f"a{i}.md", art,
                           "APPROVED", wave="7")

    def test_chain_intact_after_appends(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            log = root / "AUDIT_TRAIL.jsonl"
            for i in range(3):
                self._append(log, root, i)
            self.assertEqual(sign.verify_audit_chain(log), [])
            rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
            self.assertTrue(all("entry_hash" in r and "prev_hash" in r for r in rows))
            self.assertEqual(rows[0]["prev_hash"], "GENESIS")

    def test_chain_detects_in_place_edit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            log = root / "AUDIT_TRAIL.jsonl"
            for i in range(3):
                self._append(log, root, i)
            lines = log.read_text().splitlines()
            row = json.loads(lines[1])
            row["verdict"] = "TAMPERED"  # forge the middle row in place
            lines[1] = json.dumps(row, ensure_ascii=False)
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertTrue(sign.verify_audit_chain(log))  # non-empty = caught

    def test_chain_detects_deletion(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            log = root / "AUDIT_TRAIL.jsonl"
            for i in range(3):
                self._append(log, root, i)
            lines = log.read_text().splitlines()
            del lines[1]  # remove the middle row
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertTrue(sign.verify_audit_chain(log))  # forward link breaks

    def test_chain_tolerates_unchained_rows(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            log = root / "AUDIT_TRAIL.jsonl"
            log.write_text(json.dumps({"ts": "x", "action": "legacy"}) + "\n",
                           encoding="utf-8")
            for i in range(2):
                self._append(log, root, i)
            self.assertEqual(sign.verify_audit_chain(log), [])

    def test_integrity_witness_flags_audit_tampering(self):
        """0.2 load-bearing: the integrity-witness check surfaces a broken
        audit chain as drift, not just governance-file drift."""
        from signalos_lib.commands import integrity_witness as iw
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir(parents=True, exist_ok=True)
            # a witnessed governance file so create/check have a baseline
            gov = root / ".signalos" / "CONSTITUTION.md"
            gov.write_text("# Constitution\n", encoding="utf-8")
            log = root / ".signalos" / "AUDIT_TRAIL.jsonl"
            for i in range(3):
                self._append(log, root, i)
            iw.create_witness(root, actor="Samer", role="PO")
            self.assertTrue(iw.check_witness(root)["ok"])  # clean chain
            # forge a row in place
            lines = log.read_text().splitlines()
            row = json.loads(lines[1]); row["verdict"] = "FORGED"
            lines[1] = json.dumps(row, ensure_ascii=False)
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = iw.check_witness(root)
            self.assertFalse(result["ok"])
            self.assertTrue(any("audit ledger tampering" in i for i in result["issues"]))


if __name__ == "__main__":
    unittest.main()
