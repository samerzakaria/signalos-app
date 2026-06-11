# signalos_lib/product/share_export.py
# Shareable read-only project snapshot.
#
# True multi-player (live co-editing) needs a sync backend that a desktop app
# does not have. The useful in-app slice is a read-only snapshot a founder can
# hand to a human collaborator (a QA tester, a marketer) so they can review the
# project's state -- gate progress, the audit timeline, release readiness,
# closeout -- without installing Foundry. It is a single self-contained HTML
# file plus the underlying JSON; no server, no write-back.

from __future__ import annotations

__all__ = [
    "collect_share_data",
    "render_share_html",
    "write_share_bundle",
]

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def collect_share_data(repo_root, generated_at: str | None = None) -> dict[str, Any]:
    """Assemble a read-only snapshot of the project's governance state.

    Tolerant: every section is optional and absent artifacts are simply omitted.
    """
    root = Path(repo_root)
    signalos = root / ".signalos"

    from signalos_lib.audit_replay import build_timeline

    timeline = build_timeline(root)
    gate_state = timeline[-1]["state_after"]["gates"] if timeline else {}

    profile = _read_json(signalos / "profile.json") or {}
    closeout = _read_json(signalos / "CLOSEOUT.json")
    closeout_summary = None
    if isinstance(closeout, dict):
        closeout_summary = {
            "closure_level": closeout.get("closure_level"),
            "product_name": closeout.get("product_name"),
        }

    return {
        "kind": "foundry-share-snapshot",
        "read_only": True,
        "generated_at": generated_at or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": root.name,
        "profile": profile,
        "gate_state": gate_state,
        "timeline": [
            {"index": f["index"], "ts": f["ts"], "summary": f["summary"]}
            for f in timeline
        ],
        "closeout": closeout_summary,
    }


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def render_share_html(data: dict[str, Any]) -> str:
    """Render the snapshot as a single self-contained, read-only HTML page."""
    gates = data.get("gate_state", {}) or {}
    gate_cells = "".join(
        f'<li class="{"signed" if g.get("signed") else "open"}">'
        f'<b>{_esc(code)}</b> — {"signed" if g.get("signed") else "not signed"}'
        f'{(" (" + _esc(g.get("role")) + ")") if g.get("role") else ""}</li>'
        for code, g in gates.items()
    ) or "<li>No gate history.</li>"

    rows = "".join(
        f'<tr><td>{_esc(f["index"])}</td><td>{_esc(f.get("ts") or "")}</td>'
        f'<td>{_esc(f.get("summary") or "")}</td></tr>'
        for f in data.get("timeline", [])
    ) or '<tr><td colspan="3">No audit history yet.</td></tr>'

    closeout = data.get("closeout")
    closeout_html = ""
    if closeout:
        closeout_html = (
            f'<section><h2>Closeout</h2><p>Product: '
            f'{_esc(closeout.get("product_name") or "—")} · Closure level: '
            f'{_esc(closeout.get("closure_level") or "—")}</p></section>'
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(data.get("project") or "Project")} — Foundry snapshot</title>
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0; color: #1c1c28; background: #f6f6fb; }}
  .wrap {{ max-width: 820px; margin: 0 auto; padding: 32px 20px; }}
  header {{ display: flex; align-items: baseline; gap: 12px; }}
  h1 {{ margin: 0; font-size: 22px; }}
  .ro {{ font-size: 12px; color: #fff; background: #6c5ce7; padding: 2px 8px; border-radius: 999px; }}
  .meta {{ color: #6b6b82; font-size: 13px; margin: 4px 0 24px; }}
  section {{ background: #fff; border: 1px solid #e7e7f0; border-radius: 12px; padding: 16px 18px; margin-bottom: 16px; }}
  h2 {{ font-size: 15px; margin: 0 0 10px; }}
  ul {{ list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 8px; }}
  li {{ padding: 4px 10px; border-radius: 8px; background: #f0f0f6; font-size: 13px; }}
  li.signed {{ background: #e6f7ec; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  td, th {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #efeff5; }}
</style></head>
<body><div class="wrap">
  <header><h1>{_esc(data.get("project") or "Project")}</h1><span class="ro">Read-only</span></header>
  <p class="meta">Foundry project snapshot · generated {_esc(data.get("generated_at"))}</p>
  <section><h2>Gates</h2><ul>{gate_cells}</ul></section>
  {closeout_html}
  <section><h2>Decision timeline</h2>
    <table><thead><tr><th>#</th><th>When</th><th>Event</th></tr></thead>
    <tbody>{rows}</tbody></table>
  </section>
  <p class="meta">Shared from Foundry. This is a point-in-time, read-only view — changes in the app are not reflected here.</p>
</div></body></html>
"""


def write_share_bundle(repo_root, generated_at: str | None = None) -> dict[str, str]:
    """Write share.html + share.json under .signalos/share/. Returns the paths."""
    root = Path(repo_root)
    data = collect_share_data(root, generated_at=generated_at)
    out_dir = root / ".signalos" / "share"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "share.html"
    json_path = out_dir / "share.json"
    html_path.write_text(render_share_html(data), encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "html": str(html_path.relative_to(root)),
        "json": str(json_path.relative_to(root)),
    }
