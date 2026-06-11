"""Tests for the shareable read-only project snapshot."""

from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.product.share_export import (
    collect_share_data,
    render_share_html,
    write_share_bundle,
)


def _seed(root: Path):
    d = root / ".signalos"
    d.mkdir(parents=True, exist_ok=True)
    (d / "profile.json").write_text('{"profile":"existing-repo"}', encoding="utf-8")
    (d / "AUDIT_TRAIL.jsonl").write_text(
        '{"ts":"2026-06-01T10:00:00Z","action":"wave.start","wave":"W1"}\n'
        '{"ts":"2026-06-01T10:10:00Z","action":"gate.signed","gate":"G2","role":"PO"}\n',
        encoding="utf-8",
    )


def test_collect_is_read_only_snapshot(tmp_path):
    _seed(tmp_path)
    data = collect_share_data(tmp_path, generated_at="2026-06-11T00:00:00Z")
    assert data["read_only"] is True
    assert data["kind"] == "foundry-share-snapshot"
    assert data["gate_state"]["G2"]["signed"] is True
    assert len(data["timeline"]) == 2


def test_collect_tolerates_empty_project(tmp_path):
    data = collect_share_data(tmp_path)
    assert data["timeline"] == []
    assert data["gate_state"] == {}
    assert data["read_only"] is True


def test_render_html_is_self_contained(tmp_path):
    _seed(tmp_path)
    html = render_share_html(collect_share_data(tmp_path))
    assert "<!doctype html>" in html.lower()
    assert "Read-only" in html
    assert "Decision timeline" in html
    assert "G2" in html
    # Self-contained: styles inline, no external script/link tags.
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()


def test_render_escapes_untrusted_content(tmp_path):
    d = tmp_path / ".signalos"
    d.mkdir(parents=True)
    (d / "AUDIT_TRAIL.jsonl").write_text(
        '{"ts":"t","action":"<script>alert(1)</script>"}\n', encoding="utf-8"
    )
    html = render_share_html(collect_share_data(tmp_path))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_write_bundle_creates_files(tmp_path):
    _seed(tmp_path)
    paths = write_share_bundle(tmp_path, generated_at="2026-06-11T00:00:00Z")
    assert (tmp_path / paths["html"]).is_file()
    assert (tmp_path / paths["json"]).is_file()
    parsed = json.loads((tmp_path / paths["json"]).read_text(encoding="utf-8"))
    assert parsed["read_only"] is True
