#!/usr/bin/env python3
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — static HTML dashboard renderer.
#
# Reads every .signalos/sessions/<sid>/metrics.jsonl plus
# .signalos/sessions/INDEX.jsonl, and emits a single-file HTML document to
# stdout (or --output <path>). No JavaScript. No CDN. No external libraries.
# Pure Python stdlib (html, json, sys, pathlib, statistics, argparse,
# datetime, os) with inline CSS and inline SVG charts.
#
# Determinism contract: given identical input bytes, two invocations produce
# byte-identical HTML. Session ordering is deterministic (last-updated then
# session_id tiebreak, both sorted stably). No timestamps of "now" leak into
# the output. This lets us git-track a snapshot.
#
# Read-side redaction scan: the renderer refuses any metric row carrying a
# field outside the known allowlist — this catches a rogue writer that tried
# to bypass metrics-append.sh. Such rows are dropped from the aggregation
# and counted as schema_rejected.
#
# Performance target: >10k rows aggregated in <2s on a laptop. The renderer
# streams each metrics.jsonl line-by-line, never loading full files into
# memory; per-session aggregates are accumulated as scalars.

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from statistics import mean

# Field allowlist — mirrors metrics-append.sh. Any metric row containing a
# key outside this set is a schema violation and is rejected.
ALLOWED_FIELDS = frozenset({
    "ts", "schema_version", "session_id", "step_id", "hook", "tool",
    "duration_ms", "tokens_in", "tokens_out", "cost_usd", "wave_id",
    "phase", "actor", "subagent_count",
})

# Any value string longer than this in a known string field is also refused
# (belt-and-braces guard against a writer that tried to stuff a prompt body
# into an allowed field like "hook" or "phase").
MAX_STRING_VALUE = 256

# Session list cap — last N by updated_at.
MAX_SESSIONS_DISPLAYED = 20

# Timeline: maximum steps shown per session. Prevents runaway SVG width.
MAX_TIMELINE_STEPS = 80


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Render the SignalOS observability static HTML dashboard.",
    )
    ap.add_argument(
        "--root",
        default=".",
        help="Repo root (contains .signalos/). Defaults to cwd.",
    )
    ap.add_argument(
        "--output",
        default=None,
        help="Output path. If omitted, HTML is written to stdout.",
    )
    ap.add_argument(
        "--watch",
        action="store_true",
        default=False,
        help=(
            "W3.2 (AMD-CORE-015): re-render on every journal change. "
            "Requires --output. Falls back to --interval polling if inotifywait unavailable."
        ),
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=2.0,
        metavar="SECS",
        help="Polling interval for --watch fallback (default 2s).",
    )
    return ap.parse_args()


def _is_valid_row(row: dict) -> bool:
    """Enforce the read-side allowlist and basic type checks."""
    if not isinstance(row, dict):
        return False
    for k, v in row.items():
        if k not in ALLOWED_FIELDS:
            return False
        if isinstance(v, str) and len(v) > MAX_STRING_VALUE:
            return False
    required = ("ts", "schema_version", "session_id", "step_id", "duration_ms")
    for k in required:
        if k not in row:
            return False
    if row.get("schema_version") != 1:
        return False
    if not isinstance(row.get("duration_ms"), (int, float)) or row["duration_ms"] < 0:
        return False
    if "hook" not in row and "tool" not in row:
        return False
    return True


class SessionAgg:
    """Streaming per-session aggregator — scalars only."""

    __slots__ = (
        "session_id", "row_count", "error_count", "total_duration_ms",
        "total_tokens_in", "total_tokens_out", "total_cost_usd",
        "first_ts", "last_ts", "steps",
    )

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.row_count = 0
        self.error_count = 0
        self.total_duration_ms = 0
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.total_cost_usd = 0.0
        self.first_ts: str | None = None
        self.last_ts: str | None = None
        # (step_id, duration_ms, kind) — kind is "hook" or "tool" or "error".
        self.steps: list[tuple[str, int, str]] = []

    def ingest(self, row: dict) -> None:
        self.row_count += 1
        dur = int(row["duration_ms"])
        self.total_duration_ms += dur
        self.total_tokens_in += int(row.get("tokens_in", 0) or 0)
        self.total_tokens_out += int(row.get("tokens_out", 0) or 0)
        cost = row.get("cost_usd", 0.0) or 0.0
        self.total_cost_usd += float(cost)
        hook = row.get("hook", "")
        tool = row.get("tool", "")
        kind = "hook" if hook else "tool"
        # Any hook/tool whose name ends with "-failed" or equals "step-failed"
        # counts as an error. Keeps error-rate signal in the dashboard.
        name = hook or tool
        if name.endswith("-failed") or name == "step-failed":
            self.error_count += 1
            kind = "error"
        ts = row.get("ts")
        if isinstance(ts, str):
            if self.first_ts is None or ts < self.first_ts:
                self.first_ts = ts
            if self.last_ts is None or ts > self.last_ts:
                self.last_ts = ts
        if len(self.steps) < MAX_TIMELINE_STEPS:
            self.steps.append((str(row.get("step_id", "")), dur, kind))


def stream_sessions(root: Path) -> tuple[dict[str, SessionAgg], int, int]:
    """Walk every metrics.jsonl under .signalos/sessions/, return aggregates.

    Returns (aggregates, rows_ingested, rows_rejected).
    """
    sessions_dir = root / ".signalos" / "sessions"
    aggs: dict[str, SessionAgg] = {}
    ingested = 0
    rejected = 0
    if not sessions_dir.is_dir():
        return aggs, 0, 0
    # Deterministic traversal: sort session-dir names.
    for entry in sorted(sessions_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        metrics_path = entry / "metrics.jsonl"
        if not metrics_path.is_file():
            continue
        sid = entry.name
        agg = aggs.get(sid) or SessionAgg(sid)
        # Line-by-line stream; never load full file.
        try:
            with metrics_path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        rejected += 1
                        continue
                    if not _is_valid_row(row):
                        rejected += 1
                        continue
                    if row.get("session_id") != sid:
                        # Row claims a different session — treat as invalid.
                        rejected += 1
                        continue
                    agg.ingest(row)
                    ingested += 1
        except OSError:
            continue
        aggs[sid] = agg
    return aggs, ingested, rejected


def read_index(root: Path) -> dict[str, dict]:
    """Read .signalos/sessions/INDEX.jsonl into a dict keyed by session_id."""
    idx_path = root / ".signalos" / "sessions" / "INDEX.jsonl"
    out: dict[str, dict] = {}
    if not idx_path.is_file():
        return out
    try:
        with idx_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = obj.get("session_id")
                if isinstance(sid, str):
                    out[sid] = obj
    except OSError:
        return out
    return out


# -- Rendering helpers -------------------------------------------------------

def _esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _fmt_ms(ms: int) -> str:
    if ms < 1000:
        return f"{ms} ms"
    return f"{ms / 1000:.2f} s"


def _fmt_cost(c: float) -> str:
    return f"${c:.4f}"


def render_session_timeline_svg(agg: SessionAgg) -> str:
    """One stacked-bar SVG per session — one <rect> per step.

    Deterministic: step order is insertion order (which comes from on-disk
    file order, which is append order). No time.time() calls, no hash()
    usage that could vary by PYTHONHASHSEED.
    """
    steps = agg.steps
    if not steps:
        return (
            '<svg viewBox="0 0 600 40" width="600" height="40" '
            'xmlns="http://www.w3.org/2000/svg" role="img" '
            'aria-label="no steps recorded"><rect x="0" y="0" width="600" '
            'height="40" fill="#f3f4f6"/><text x="8" y="24" '
            'font-family="monospace" font-size="12" fill="#6b7280">'
            '(no steps)</text></svg>'
        )
    width = 600
    height = 40
    total = sum(max(d, 1) for _, d, _ in steps)
    colors = {"hook": "#2563eb", "tool": "#059669", "error": "#dc2626"}
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="timeline for session {_esc(agg.session_id)}">'
    ]
    x = 0
    for step_id, dur, kind in steps:
        w = max(1, int((max(dur, 1) / total) * width))
        if x + w > width:
            w = width - x
            if w <= 0:
                break
        color = colors.get(kind, "#9ca3af")
        title = f"{step_id} — {dur} ms ({kind})"
        parts.append(
            f'<rect x="{x}" y="0" width="{w}" height="{height}" '
            f'fill="{color}"><title>{_esc(title)}</title></rect>'
        )
        x += w
    parts.append("</svg>")
    return "".join(parts)


def render_cost_bar_svg(session_list: list[SessionAgg]) -> str:
    """Horizontal cost-per-session bar chart."""
    if not session_list:
        return (
            '<svg viewBox="0 0 600 40" width="600" height="40" '
            'xmlns="http://www.w3.org/2000/svg" role="img" '
            'aria-label="no cost data"><rect x="0" y="0" width="600" '
            'height="40" fill="#f3f4f6"/><text x="8" y="24" '
            'font-family="monospace" font-size="12" fill="#6b7280">'
            '(no cost data)</text></svg>'
        )
    max_cost = max((s.total_cost_usd for s in session_list), default=0.0)
    if max_cost <= 0:
        max_cost = 1.0
    row_h = 22
    width = 600
    height = row_h * len(session_list) + 8
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="cost per session">'
    ]
    for i, s in enumerate(session_list):
        y = 4 + i * row_h
        w = int((s.total_cost_usd / max_cost) * 380) if max_cost > 0 else 0
        parts.append(
            f'<text x="0" y="{y + 14}" font-family="monospace" '
            f'font-size="11" fill="#111827">{_esc(s.session_id[:18])}</text>'
        )
        parts.append(
            f'<rect x="180" y="{y + 2}" width="{w}" height="16" '
            f'fill="#6366f1"/>'
        )
        parts.append(
            f'<text x="{185 + w}" y="{y + 14}" font-family="monospace" '
            f'font-size="11" fill="#374151">{_esc(_fmt_cost(s.total_cost_usd))}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


CSS = """
:root{color-scheme:light;}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
margin:0;padding:24px;background:#f9fafb;color:#111827;}
h1{font-size:20px;margin:0 0 8px;}
h2{font-size:15px;margin:24px 0 8px;color:#374151;}
.meta{color:#6b7280;font-size:12px;margin-bottom:16px;}
.totals{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:8px 0 24px;}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;}
.card .n{font-size:20px;font-weight:600;color:#111827;}
.card .k{font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.04em;}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;
border-radius:8px;overflow:hidden;font-size:12px;}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #f3f4f6;}
th{background:#f3f4f6;color:#374151;font-weight:600;}
tr:last-child td{border-bottom:none;}
.session-block{background:#fff;border:1px solid #e5e7eb;border-radius:8px;
padding:12px 14px;margin:8px 0;}
.session-block .sid{font-family:monospace;font-size:12px;color:#111827;margin-bottom:4px;}
.session-block .sub{font-size:11px;color:#6b7280;margin-bottom:6px;}
.legend{font-size:11px;color:#4b5563;margin-bottom:12px;}
.legend span{display:inline-block;padding:2px 6px;border-radius:3px;color:#fff;
margin-right:6px;font-family:monospace;font-size:10px;}
.foot{margin-top:24px;font-size:11px;color:#6b7280;}
""".strip()


def render_html(
    aggs: dict[str, SessionAgg],
    index: dict[str, dict],
    ingested: int,
    rejected: int,
) -> str:
    # Deterministic session ordering — last_ts desc, then session_id asc.
    def sort_key(a: SessionAgg) -> tuple:
        last = a.last_ts or ""
        # Negative via tuple trick: invert string comparison by pairing.
        return (last, a.session_id)

    sessions_sorted = sorted(aggs.values(), key=sort_key, reverse=True)
    # Stable alphabetic tiebreak at equal last_ts:
    sessions_sorted = sorted(
        aggs.values(),
        key=lambda a: (a.last_ts or "", a.session_id),
        reverse=True,
    )
    last_20 = sessions_sorted[:MAX_SESSIONS_DISPLAYED]

    total_rows = sum(a.row_count for a in aggs.values())
    total_errors = sum(a.error_count for a in aggs.values())
    total_cost = sum(a.total_cost_usd for a in aggs.values())
    total_tokens = sum(
        a.total_tokens_in + a.total_tokens_out for a in aggs.values()
    )
    error_rate = (total_errors / total_rows * 100.0) if total_rows else 0.0

    body_parts: list[str] = []
    body_parts.append(
        f'<h1>SignalOS Core — metrics sidecar dashboard</h1>'
    )
    body_parts.append(
        f'<div class="meta">Static HTML snapshot. Regenerate with: '
        f'<code>python3 core/observability/render-dashboard.py '
        f'--output core/observability/dashboard.html</code></div>'
    )
    body_parts.append('<div class="totals">')
    for k, v in (
        ("Sessions", str(len(aggs))),
        ("Rows", str(total_rows)),
        ("Errors", str(total_errors)),
        ("Error rate", f"{error_rate:.2f}%"),
        ("Cost (USD)", _fmt_cost(total_cost)),
    ):
        body_parts.append(
            f'<div class="card"><div class="n">{_esc(v)}</div>'
            f'<div class="k">{_esc(k)}</div></div>'
        )
    body_parts.append('</div>')

    body_parts.append(
        f'<div class="meta">Tokens total: {total_tokens} &nbsp;|&nbsp; '
        f'Rows rejected (schema): {rejected}</div>'
    )

    # Session list (last 20).
    body_parts.append(f'<h2>Session list (last {len(last_20)} of {len(aggs)})</h2>')
    body_parts.append(
        '<table><thead><tr><th>Session ID</th><th>Last event</th>'
        '<th>Rows</th><th>Errors</th><th>Duration</th><th>Cost</th>'
        '<th>Tokens in/out</th></tr></thead><tbody>'
    )
    for s in last_20:
        idx_row = index.get(s.session_id, {})
        last_event = idx_row.get("last_event", s.last_ts or "-")
        body_parts.append(
            f'<tr><td>{_esc(s.session_id)}</td>'
            f'<td>{_esc(last_event)}</td>'
            f'<td>{s.row_count}</td>'
            f'<td>{s.error_count}</td>'
            f'<td>{_esc(_fmt_ms(s.total_duration_ms))}</td>'
            f'<td>{_esc(_fmt_cost(s.total_cost_usd))}</td>'
            f'<td>{s.total_tokens_in}/{s.total_tokens_out}</td></tr>'
        )
    body_parts.append('</tbody></table>')

    # Cost chart.
    body_parts.append('<h2>Cost per session</h2>')
    body_parts.append(render_cost_bar_svg(last_20))

    # Per-session timelines.
    body_parts.append('<h2>Per-session step timelines</h2>')
    body_parts.append(
        '<div class="legend">'
        '<span style="background:#2563eb">hook</span>'
        '<span style="background:#059669">tool</span>'
        '<span style="background:#dc2626">error</span>'
        '(widths proportional to duration_ms within the session)</div>'
    )
    for s in last_20:
        body_parts.append('<div class="session-block">')
        body_parts.append(f'<div class="sid">{_esc(s.session_id)}</div>')
        body_parts.append(
            f'<div class="sub">{s.row_count} rows · '
            f'{_esc(_fmt_ms(s.total_duration_ms))} total · '
            f'{_esc(_fmt_cost(s.total_cost_usd))}</div>'
        )
        body_parts.append(render_session_timeline_svg(s))
        body_parts.append('</div>')

    body_parts.append(
        '<div class="foot">Concept adapted from a5c-ai/babysitter (MIT). '
        f'No JavaScript. No CDN. No build step. '
        f'Rows ingested: {ingested}. Rows rejected by read-side allowlist: '
        f'{rejected}.</div>'
    )

    body = "".join(body_parts)
    html_doc = (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8"/>\n'
        '<title>SignalOS Core metrics dashboard</title>\n'
        f'<style>{CSS}</style>\n'
        '</head>\n<body>\n'
        f'{body}\n'
        '</body>\n</html>\n'
    )
    return html_doc




# ---------------------------------------------------------------------------
# W3.2 — watch mode helpers (AMD-CORE-015 T3)
# ---------------------------------------------------------------------------

def _inotifywait_available() -> bool:
    import shutil
    return shutil.which("inotifywait") is not None


def _watch_once(watch_dir: str, timeout: float) -> None:
    """Block until inotifywait fires or timeout elapses."""
    import subprocess as _sp, time as _t
    if _inotifywait_available():
        try:
            _sp.run(
                ["inotifywait", "-r", "-q", "--timeout", str(int(timeout)),
                 "-e", "modify,create,moved_to", watch_dir],
                capture_output=True, timeout=timeout + 2,
            )
        except Exception:
            _t.sleep(timeout)
    else:
        _t.sleep(timeout)


def _run_once(root: Path, output: Path) -> None:
    aggs, ingested, rejected = stream_sessions(root)
    index = read_index(root)
    out_html = render_html(aggs, index, ingested, rejected)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(out_html, encoding="utf-8")


def watch_dashboard(root: Path, output: Path, interval: float = 2.0) -> None:
    """Continuously re-render dashboard.html on any session journal change."""
    watch_dir = str(root / ".signalos" / "sessions")
    use_inotify = _inotifywait_available()
    sys.stderr.write(
        f"  render-dashboard --watch: {'inotify' if use_inotify else f'polling {interval}s'} → {output}\n"
    )
    try:
        _run_once(root, output)
        while True:
            _watch_once(watch_dir, interval)
            _run_once(root, output)
            sys.stderr.write(f"  rendered → {output}\n")
    except KeyboardInterrupt:
        sys.stderr.write("\n")

def main(argv: list[str] | None = None) -> int:
    args = parse_args()
    root = Path(args.root).resolve()

    if args.watch:
        if not args.output:
            sys.stderr.write("render-dashboard --watch: --output <path> is required\n")
            return 2
        watch_dashboard(root, Path(args.output), interval=args.interval)
        return 0

    aggs, ingested, rejected = stream_sessions(root)
    index = read_index(root)
    out = render_html(aggs, index, ingested, rejected)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
