"""Live Jira adapter integration test (Wave 1.7 backend).

Skips automatically unless Jira creds are available (from .env or the
environment), so it is a no-op in CI but a real end-to-end check locally. Each
run creates and then DELETES a throwaway issue, leaving the board clean.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()
_HAVE_JIRA = bool(os.environ.get("JIRA_TOKEN") and os.environ.get("JIRA_SITE"))


@unittest.skipUnless(_HAVE_JIRA, "no Jira creds (.env JIRA_SITE/JIRA_TOKEN)")
class JiraIntegrationTests(unittest.TestCase):
    def _tracker(self):
        from signalos_lib.product.tracker_jira import JiraTracker
        return JiraTracker(
            os.environ["JIRA_SITE"], os.environ["JIRA_EMAIL"],
            os.environ["JIRA_TOKEN"], os.environ.get("JIRA_PROJECT", "KAN"),
        )

    def test_create_fetch_update_delete_roundtrip(self):
        t = self._tracker()
        ext = t.upsert("foundry-itest", {"title": "Foundry itest — auto-deleted"})
        self.assertTrue(ext)
        try:
            self.assertEqual(t.fetch(ext)["summary"], "Foundry itest — auto-deleted")
            same = t.upsert("foundry-itest", {"title": "Foundry itest — updated"})
            self.assertEqual(same, ext)  # updates the same issue, no duplicate
            self.assertEqual(t.fetch(ext)["summary"], "Foundry itest — updated")
        finally:
            t.delete(ext)  # always clean up

    def test_adapter_satisfies_the_sync_protocol(self):
        from signalos_lib.product.tracker_sync import TrackerAdapter
        self.assertIsInstance(self._tracker(), TrackerAdapter)


if __name__ == "__main__":
    unittest.main()
