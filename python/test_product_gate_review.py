# test_product_gate_review.py
# Tests for gate review classification, request-changes, and rejection handling.

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.gate_review import (
    build_rejection_packet,
    build_rework_packet,
    classify_review,
    handle_rejection,
    handle_request_changes,
    latest_review_cycle,
    record_review_event,
)


class TestClassifyReview(unittest.TestCase):
    """Test classify_review() maps user replies to correct verdicts."""

    def test_yes_is_approve(self):
        result = classify_review("yes")
        self.assertEqual(result["verdict"], "approve")

    def test_looks_good_is_approve(self):
        result = classify_review("looks good")
        self.assertEqual(result["verdict"], "approve")

    def test_lgtm_is_approve(self):
        result = classify_review("lgtm")
        self.assertEqual(result["verdict"], "approve")

    def test_approve_but_is_approve_with_conditions(self):
        result = classify_review("approve but track the auth issue")
        self.assertEqual(result["verdict"], "approve-with-conditions")
        self.assertIn("auth issue", result["feedback"])

    def test_yes_but_is_approve_with_conditions(self):
        result = classify_review("yes but we need to revisit the API later")
        self.assertEqual(result["verdict"], "approve-with-conditions")

    def test_change_item_is_request_changes(self):
        result = classify_review("change item 3, it should use POST not GET")
        self.assertEqual(result["verdict"], "request-changes")
        self.assertTrue(len(result["specific_items"]) >= 1)

    def test_fix_is_request_changes(self):
        result = classify_review("fix the button color")
        self.assertEqual(result["verdict"], "request-changes")

    def test_no_with_direction_is_reject(self):
        result = classify_review(
            "no, this is completely wrong, I want a dashboard not a form"
        )
        self.assertEqual(result["verdict"], "reject")

    def test_start_over_is_reject(self):
        result = classify_review("start over")
        self.assertEqual(result["verdict"], "reject")

    def test_rejected_is_reject(self):
        result = classify_review("rejected")
        self.assertEqual(result["verdict"], "reject")

    def test_bare_no_is_reject(self):
        result = classify_review("no")
        self.assertEqual(result["verdict"], "reject")

    def test_skip_this_gate_is_waive(self):
        result = classify_review("skip this gate")
        self.assertEqual(result["verdict"], "waive")

    def test_waive_is_waive(self):
        result = classify_review("waive")
        self.assertEqual(result["verdict"], "waive")

    def test_not_needed_is_waive(self):
        result = classify_review("not needed")
        self.assertEqual(result["verdict"], "waive")

    def test_confidence_is_float(self):
        result = classify_review("yes")
        self.assertIsInstance(result["confidence"], float)
        self.assertGreaterEqual(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], 1.0)

    def test_specific_items_is_list(self):
        result = classify_review("fix X and update Y")
        self.assertIsInstance(result["specific_items"], list)

    def test_no_then_instruction_is_request_changes(self):
        result = classify_review("no, it should be a sidebar not a modal")
        self.assertEqual(result["verdict"], "request-changes")
        self.assertTrue(len(result["specific_items"]) >= 1)


class TestHandleRequestChanges(unittest.TestCase):
    """Test handle_request_changes creates rework packets correctly."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo_root = Path(self.tmp)
        (self.repo_root / ".signalos").mkdir(parents=True)

    def test_creates_rework_packet(self):
        result = handle_request_changes(
            repo_root=self.repo_root,
            gate_id="generation",
            feedback="fix the nav layout",
            specific_items=["nav should be horizontal"],
            current_artifact={"files": ["nav.tsx"]},
            max_cycles=3,
            cycle=0,
        )
        self.assertEqual(result["status"], "rework_dispatched")
        self.assertEqual(result["cycle"], 1)
        self.assertIsNotNone(result["rework_packet"])
        self.assertIsNotNone(result["packet_path"])
        # Packet was written to disk
        self.assertTrue(Path(result["packet_path"]).exists())

    def test_max_cycles_reached(self):
        result = handle_request_changes(
            repo_root=self.repo_root,
            gate_id="generation",
            feedback="still wrong",
            specific_items=["still broken"],
            max_cycles=3,
            cycle=3,  # Already at max
        )
        self.assertEqual(result["status"], "max_cycles_reached")
        self.assertIsNone(result["rework_packet"])

    def test_cycle_increments(self):
        result = handle_request_changes(
            repo_root=self.repo_root,
            gate_id="generation",
            feedback="tweak colors",
            specific_items=["use blue not red"],
            cycle=1,
        )
        self.assertEqual(result["cycle"], 2)


class TestLatestReviewCycle(unittest.TestCase):
    """latest_review_cycle recovers the persisted cycle from the review
    packets on disk -- the counter the standalone verdict path relies on."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo_root = Path(self.tmp)
        (self.repo_root / ".signalos").mkdir(parents=True)

    def test_zero_when_no_packets(self):
        self.assertEqual(latest_review_cycle(self.repo_root, "run-1"), 0)

    def test_tracks_dispatched_rework_cycles(self):
        r1 = handle_request_changes(
            repo_root=self.repo_root, gate_id="run-1",
            feedback="fix nav", specific_items=["nav"], cycle=0)
        self.assertEqual(r1["cycle"], 1)
        self.assertEqual(latest_review_cycle(self.repo_root, "run-1"), 1)
        r2 = handle_request_changes(
            repo_root=self.repo_root, gate_id="run-1",
            feedback="fix footer", specific_items=["footer"],
            cycle=latest_review_cycle(self.repo_root, "run-1"))
        self.assertEqual(r2["cycle"], 2)
        self.assertEqual(latest_review_cycle(self.repo_root, "run-1"), 2)

    def test_rework_and_regenerate_counted_separately(self):
        handle_request_changes(
            repo_root=self.repo_root, gate_id="run-1",
            feedback="fix nav", specific_items=["nav"], cycle=0)
        self.assertEqual(
            latest_review_cycle(self.repo_root, "run-1", packet_type="regenerate"), 0)
        handle_rejection(
            repo_root=self.repo_root, gate_id="run-1",
            reason="wrong", rejection_count=0)
        self.assertEqual(
            latest_review_cycle(self.repo_root, "run-1", packet_type="rework"), 1)
        self.assertEqual(
            latest_review_cycle(self.repo_root, "run-1", packet_type="regenerate"), 1)

    def test_max_cycles_writes_no_packet_so_refusal_is_stable(self):
        r1 = handle_request_changes(
            repo_root=self.repo_root, gate_id="run-1",
            feedback="fix", specific_items=["fix"], max_cycles=1, cycle=0)
        self.assertEqual(r1["status"], "rework_dispatched")
        r2 = handle_request_changes(
            repo_root=self.repo_root, gate_id="run-1",
            feedback="again", specific_items=["again"], max_cycles=1,
            cycle=latest_review_cycle(self.repo_root, "run-1"))
        self.assertEqual(r2["status"], "max_cycles_reached")
        # No new packet dir -> the persisted counter stays at the budget and
        # every further attempt keeps refusing.
        self.assertEqual(latest_review_cycle(self.repo_root, "run-1"), 1)


class TestHandleRejection(unittest.TestCase):
    """Test handle_rejection creates regenerate packets correctly."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo_root = Path(self.tmp)
        (self.repo_root / ".signalos").mkdir(parents=True)

    def test_creates_regenerate_packet(self):
        result = handle_rejection(
            repo_root=self.repo_root,
            gate_id="generation",
            reason="I want a dashboard, not a form",
            max_rejections=2,
            rejection_count=0,
        )
        self.assertEqual(result["status"], "regenerate_dispatched")
        self.assertEqual(result["rejection_count"], 1)
        self.assertIsNotNone(result["regenerate_packet"])
        self.assertEqual(result["regenerate_packet"]["type"], "regenerate")

    def test_max_rejections_reached(self):
        result = handle_rejection(
            repo_root=self.repo_root,
            gate_id="generation",
            reason="still wrong",
            max_rejections=2,
            rejection_count=2,  # Already at max
        )
        self.assertEqual(result["status"], "max_rejections_reached")
        self.assertIsNone(result["regenerate_packet"])

    def test_rejection_count_increments(self):
        result = handle_rejection(
            repo_root=self.repo_root,
            gate_id="design",
            reason="wrong approach",
            rejection_count=0,
        )
        self.assertEqual(result["rejection_count"], 1)


class TestBuildReworkPacket(unittest.TestCase):
    """Test build_rework_packet includes all required fields."""

    def test_has_required_fields(self):
        packet = build_rework_packet(
            gate_id="generation",
            feedback="fix the layout",
            specific_items=["nav horizontal", "footer sticky"],
            current_artifact={"files": ["App.tsx"]},
            governance_context="Must pass acceptance criteria T-001",
        )
        self.assertEqual(packet["type"], "rework")
        self.assertEqual(packet["gate_id"], "generation")
        self.assertEqual(packet["feedback"], "fix the layout")
        self.assertEqual(packet["items_to_fix"], ["nav horizontal", "footer sticky"])
        self.assertEqual(packet["current_artifact"], {"files": ["App.tsx"]})
        self.assertIn("governance", packet)
        self.assertIn("instruction", packet)
        self.assertIn("schema_version", packet)
        self.assertIn("packet_id", packet)
        self.assertIn("created_at", packet)

    def test_instruction_text(self):
        packet = build_rework_packet(
            gate_id="design",
            feedback="wrong colors",
            specific_items=["use brand blue"],
            current_artifact=None,
            governance_context="",
        )
        self.assertIn("Fix the following items", packet["instruction"])


class TestBuildRejectionPacket(unittest.TestCase):
    """Test build_rejection_packet includes all required fields."""

    def test_has_required_fields(self):
        packet = build_rejection_packet(
            gate_id="generation",
            reason="I want a dashboard not a form",
            governance_context="Must implement primary workflows",
        )
        self.assertEqual(packet["type"], "regenerate")
        self.assertEqual(packet["gate_id"], "generation")
        self.assertEqual(packet["rejection_reason"], "I want a dashboard not a form")
        self.assertIn("governance", packet)
        self.assertIn("instruction", packet)
        self.assertIn("schema_version", packet)
        self.assertIn("packet_id", packet)
        self.assertIn("created_at", packet)

    def test_instruction_mentions_regenerate(self):
        packet = build_rejection_packet(
            gate_id="design",
            reason="wrong",
            governance_context="",
        )
        self.assertIn("Regenerate from scratch", packet["instruction"])


class TestRecordReviewEvent(unittest.TestCase):
    """Test record_review_event writes to audit trail."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo_root = Path(self.tmp)

    def test_writes_audit_entry(self):
        record_review_event(
            repo_root=self.repo_root,
            gate_id="generation",
            verdict="REQUEST-CHANGES",
            feedback="fix the nav",
            cycle=1,
        )
        audit_path = self.repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
        self.assertTrue(audit_path.exists())
        lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["event"], "gate_review")
        self.assertEqual(event["gate_id"], "generation")
        self.assertEqual(event["verdict"], "REQUEST-CHANGES")
        self.assertEqual(event["feedback"], "fix the nav")
        self.assertEqual(event["cycle"], 1)
        self.assertIn("timestamp", event)

    def test_appends_multiple_events(self):
        record_review_event(self.repo_root, "gen", "REQUEST-CHANGES", "a", 1)
        record_review_event(self.repo_root, "gen", "REQUEST-CHANGES", "b", 2)
        audit_path = self.repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
        lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)


class TestVerdictsInSign(unittest.TestCase):
    """Test that sign.py VALID_VERDICTS includes new verdicts."""

    def test_request_changes_in_verdicts(self):
        from signalos_lib.sign import VALID_VERDICTS
        self.assertIn("REQUEST-CHANGES", VALID_VERDICTS)

    def test_rejected_in_verdicts(self):
        from signalos_lib.sign import VALID_VERDICTS
        self.assertIn("REJECTED", VALID_VERDICTS)

    def test_original_verdicts_preserved(self):
        from signalos_lib.sign import VALID_VERDICTS
        self.assertIn("APPROVED", VALID_VERDICTS)
        self.assertIn("APPROVED-WITH-CONDITIONS", VALID_VERDICTS)
        self.assertIn("WAIVED", VALID_VERDICTS)


if __name__ == "__main__":
    unittest.main()
