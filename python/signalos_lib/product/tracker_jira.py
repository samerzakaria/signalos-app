"""Real Jira Cloud adapter for headless tracker sync (Wave 1.7 backend).

Implements the same `TrackerAdapter` protocol as the in-memory tracker, against
Jira Cloud's REST v3 API. Stdlib-only (urllib) so it adds no dependency. The
token lives in Foundry's vault in production; here it is passed in explicitly and
never logged.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any


class JiraError(Exception):
    pass


class JiraTracker:
    """Minimal Jira Cloud tracker: create/update issues and read them back."""

    def __init__(self, site: str, email: str, token: str, project_key: str,
                 issue_type: str = "Task", timeout: float = 20.0):
        self.site = site.rstrip("/")
        self.project_key = project_key
        self.issue_type = issue_type
        self.timeout = timeout
        self._auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._map: dict[str, str] = {}  # local task id -> Jira issue key

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.site + path, data=data, method=method,
            headers={
                "Authorization": f"Basic {self._auth}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise JiraError(f"{exc.code} {exc.reason}: {detail}") from None

    # — TrackerAdapter protocol —
    def upsert(self, key: str, fields: dict[str, Any]) -> str:
        summary = str(fields.get("title") or key)
        issue_key = self._map.get(key)
        if issue_key:
            self._call("PUT", f"/rest/api/3/issue/{issue_key}",
                       {"fields": {"summary": summary}})
            return issue_key
        created = self._call("POST", "/rest/api/3/issue", {"fields": {
            "project": {"key": self.project_key},
            "issuetype": {"name": self.issue_type},
            "summary": summary,
        }})
        issue_key = str(created.get("key", ""))
        if issue_key:
            self._map[key] = issue_key
        return issue_key

    def fetch(self, external_id: str) -> dict[str, Any]:
        res = self._call("GET", f"/rest/api/3/issue/{external_id}?fields=summary,status")
        f = res.get("fields", {}) or {}
        status = (f.get("status") or {}).get("name", "")
        return {"id": res.get("key"), "summary": f.get("summary"), "status": status}

    # — helper for verification/cleanup (not part of the sync protocol) —
    def delete(self, external_id: str) -> None:
        self._call("DELETE", f"/rest/api/3/issue/{external_id}")
