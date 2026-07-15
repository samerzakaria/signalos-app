# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/serve.py
# W4.1 — Browser-based gate signing (AMD-CORE-019)
# W7   — Visual companion QA mode (--qa flag, /qa route, /api/qa endpoint)
# Public API: start_server, SignalOSHandler, build_status_html, build_sign_html,
#             build_qa_html, load_latest_qa_evidence

from __future__ import annotations

__all__ = [
    "start_server",
    "SignalOSHandler",
    "build_status_html",
    "build_sign_html",
    "build_qa_html",
    "load_latest_qa_evidence",
    "parse_post_body",
    "handle_sign_post",
]

import glob
import html
import json
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .git_process import GitProcessPolicyError, run_git
from .sign import (
    GATE_MAP,
    GATE_LABELS,
    VALID_ROLES,
    VALID_VERDICTS,
    ArtifactStatus,
    check_gate,
    sign_artifact,
)
from .status import get_wave_status

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

_CSS = """
body{font-family:system-ui,sans-serif;max-width:860px;margin:40px auto;padding:0 20px;
     color:#1a1a1a;background:#fafafa}
h1{font-size:1.4rem;margin-bottom:4px}
h2{font-size:1.1rem;margin-top:28px;margin-bottom:8px;border-bottom:1px solid #ddd;
   padding-bottom:4px}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.78rem;
       font-weight:600;letter-spacing:.03em}
.ok{background:#d1fae5;color:#065f46}.deg{background:#fef3c7;color:#92400e}
.down{background:#fee2e2;color:#991b1b}.unknown{background:#f3f4f6;color:#374151}
table{border-collapse:collapse;width:100%;margin-bottom:16px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:.88rem}
th{font-weight:600;background:#f9fafb}
a{color:#2563eb;text-decoration:none}a:hover{text-decoration:underline}
form{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:20px;
     margin-top:16px;max-width:540px}
label{display:block;font-size:.85rem;font-weight:600;margin:10px 0 4px}
input,select,textarea{width:100%;box-sizing:border-box;border:1px solid #d1d5db;
                       border-radius:4px;padding:6px 10px;font-size:.88rem}
textarea{resize:vertical;height:70px}
button{margin-top:14px;background:#1d4ed8;color:#fff;border:none;border-radius:4px;
       padding:8px 20px;font-size:.88rem;cursor:pointer;font-weight:600}
button:hover{background:#1e40af}
.msg-ok{background:#d1fae5;color:#065f46;padding:10px 14px;border-radius:6px;margin:14px 0}
.msg-err{background:#fee2e2;color:#991b1b;padding:10px 14px;border-radius:6px;margin:14px 0}
nav{margin-bottom:20px;font-size:.85rem}nav a{margin-right:14px}
""".strip()

_PAGE = (
    "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "{refresh}"
    "<title>{title} — SignalOS</title>"
    "<style>{css}</style></head><body>{body}</body></html>"
)

_NAV = "<nav><a href='/'>Status</a>{gate_links}{qa_link}</nav>"

# W7: QA status colours
_QA_CSS = """
.qa-panel{margin-top:28px;border-top:2px solid #e5e7eb;padding-top:16px}
.qa-header{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.qa-badge-pass{background:#d1fae5;color:#065f46;padding:2px 8px;border-radius:4px;
               font-size:.78rem;font-weight:600}
.qa-badge-fail{background:#fee2e2;color:#991b1b;padding:2px 8px;border-radius:4px;
               font-size:.78rem;font-weight:600}
.qa-badge-skip{background:#f3f4f6;color:#374151;padding:2px 8px;border-radius:4px;
               font-size:.78rem;font-weight:600}
.qa-badge-pending{background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:4px;
                  font-size:.78rem;font-weight:600}
.qa-screenshot{font-size:.75rem;color:#6b7280}
.qa-error{font-size:.78rem;color:#991b1b;font-family:monospace;
          background:#fee2e2;padding:4px 8px;border-radius:4px;margin-top:4px}
.qa-vitals{font-size:.75rem;color:#374151}
.qa-refresh{font-size:.75rem;color:#6b7280;margin-left:auto}
""".strip()


def _gate_links() -> str:
    links = ""
    for gate in sorted(GATE_MAP):
        links += f"<a href='/sign?gate={gate}'>{gate}</a>"
    return links


def _page(title: str, body: str, qa_mode: bool = False, auto_refresh_s: int = 0) -> str:
    qa_link = "&nbsp;|&nbsp;<a href='/qa'>QA</a>" if qa_mode else ""
    nav = _NAV.format(gate_links="&nbsp;|&nbsp;" + _gate_links(), qa_link=qa_link)
    css = _CSS + "\n" + _QA_CSS if qa_mode else _CSS
    refresh_tag = (
        f"<meta http-equiv='refresh' content='{auto_refresh_s}'>"
        if auto_refresh_s > 0 else ""
    )
    return _PAGE.format(
        title=html.escape(title),
        css=css,
        body=nav + body,
        refresh=refresh_tag,
    )


def _badge(signed: bool) -> str:
    if signed:
        return "<span class='badge ok'>✓ signed</span>"
    return "<span class='badge unknown'>unsigned</span>"


# ---------------------------------------------------------------------------
# Status page
# ---------------------------------------------------------------------------

def build_status_html(status: dict[str, Any]) -> str:
    """Render the Wave status page from a get_wave_status() dict."""
    gates: dict[str, bool] = status.get("gates", {})
    tasks: list[dict] = status.get("tasks", [])
    belief: str = status.get("belief_line", "")
    wave_id: str = status.get("wave_id", "—")
    phase: str = status.get("phase", "—")
    na: dict = status.get("next_action", {})

    gate_rows = ""
    for g in sorted(gates):
        signed = gates[g]
        label = GATE_LABELS.get(g, g)
        sign_link = f"<a href='/sign?gate={g}'>sign</a>" if not signed else "—"
        gate_rows += (
            f"<tr><td><b>{html.escape(g)}</b></td>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{_badge(signed)}</td>"
            f"<td>{sign_link}</td></tr>"
        )

    task_rows = ""
    for t in tasks:
        tid = html.escape(str(t.get("id", "")))
        subj = html.escape(str(t.get("subject", t.get("title", ""))))
        st = html.escape(str(t.get("status", "")))
        tier = html.escape(str(t.get("tier", "")))
        task_rows += f"<tr><td>{tid}</td><td>{subj}</td><td>{st}</td><td>{tier}</td></tr>"

    if not task_rows:
        task_rows = "<tr><td colspan='4' style='color:#6b7280'>No tasks found</td></tr>"

    next_action_html = ""
    if na.get("command"):
        role = html.escape(na.get("role", ""))
        cmd = html.escape(na.get("command", ""))
        next_action_html = (
            f"<h2>Next action</h2>"
            f"<p><b>{role}</b> → <code>{cmd}</code></p>"
        )

    body = f"""
<h1>SignalOS — Wave {html.escape(str(wave_id))}</h1>
<p><b>Phase:</b> {html.escape(str(phase))} &nbsp;
   <b>Belief:</b> {html.escape(str(belief))}</p>
<h2>Gates</h2>
<table>
<tr><th>Gate</th><th>Label</th><th>Status</th><th>Action</th></tr>
{gate_rows}
</table>
<h2>Tasks</h2>
<table>
<tr><th>ID</th><th>Subject</th><th>Status</th><th>Tier</th></tr>
{task_rows}
</table>
{next_action_html}
"""
    return _page(f"Wave {wave_id}", body)


# ---------------------------------------------------------------------------
# Sign form
# ---------------------------------------------------------------------------

def build_sign_html(
    gate: str,
    artifacts: list[ArtifactStatus],
    message: str = "",
    error: bool = False,
) -> str:
    """Render the gate signing form."""
    gate_label = GATE_LABELS.get(gate.upper(), gate)

    art_rows = ""
    for a in artifacts:
        exists_mark = "✓" if a.exists else "✗ (missing)"
        signed_mark = _badge(a.has_signatures)
        art_rows += (
            f"<tr><td>{html.escape(a.label)}</td>"
            f"<td style='font-size:.78rem;color:#6b7280'>{html.escape(str(a.rel_path))}</td>"
            f"<td>{exists_mark}</td>"
            f"<td>{signed_mark}</td></tr>"
        )

    role_options = "".join(
        f"<option value='{r}'>{r}</option>" for r in VALID_ROLES
    )
    verdict_options = "".join(
        f"<option value='{v}'>{v}</option>" for v in VALID_VERDICTS
    )

    msg_html = ""
    if message:
        cls = "msg-err" if error else "msg-ok"
        msg_html = f"<div class='{cls}'>{html.escape(message)}</div>"

    body = f"""
<h1>Sign {html.escape(gate_label)}</h1>
{msg_html}
<h2>Artifacts</h2>
<table>
<tr><th>Artifact</th><th>Path</th><th>Present</th><th>Signed</th></tr>
{art_rows}
</table>
<form method='post' action='/sign'>
<input type='hidden' name='gate' value='{html.escape(gate.upper())}'>
<label for='signer'>Signer name</label>
<input type='text' id='signer' name='signer' required placeholder='Jane Smith'>
<label for='role'>Role</label>
<select id='role' name='role'>{role_options}</select>
<label for='verdict'>Verdict</label>
<select id='verdict' name='verdict'>{verdict_options}</select>
<label for='conditions'>Conditions (only for APPROVED-WITH-CONDITIONS)</label>
<textarea id='conditions' name='conditions' placeholder='Leave blank if not applicable'></textarea>
<button type='submit'>Sign {html.escape(gate.upper())}</button>
</form>
"""
    return _page(f"Sign {gate_label}", body)


# ---------------------------------------------------------------------------
# W7 — QA evidence loader + visual QA page
# ---------------------------------------------------------------------------

_QA_EVIDENCE_GLOB = "core/governance/QA/evidence/wave-*-qa-evidence.json"
_QA_ONLY_GLOB = "core/governance/QA/evidence/qa-only-*.json"


def load_latest_qa_evidence(repo_root: Path) -> dict[str, Any] | None:
    """
    Return the most recently modified QA evidence JSON from
    core/governance/QA/evidence/, or None if none exists.
    Prefers gating runs (wave-*) over qa-only snapshots.
    """
    gating = sorted(
        glob.glob(str(repo_root / _QA_EVIDENCE_GLOB)),
        key=lambda p: Path(p).stat().st_mtime,
        reverse=True,
    )
    if gating:
        with open(gating[0], encoding="utf-8") as fh:
            return json.load(fh)

    qa_only = sorted(
        glob.glob(str(repo_root / _QA_ONLY_GLOB)),
        key=lambda p: Path(p).stat().st_mtime,
        reverse=True,
    )
    if qa_only:
        with open(qa_only[0], encoding="utf-8") as fh:
            return json.load(fh)

    return None


def _qa_status_badge(status: str) -> str:
    cls_map = {
        "pass": "qa-badge-pass",
        "fail": "qa-badge-fail",
        "skip": "qa-badge-skip",
    }
    label_map = {"pass": "✅ pass", "fail": "❌ fail", "skip": "⏭ skip"}
    cls = cls_map.get(status, "qa-badge-pending")
    label = label_map.get(status, status)
    return f"<span class='{cls}'>{label}</span>"


def _vitals_summary(vitals: dict[str, Any]) -> str:
    if not vitals:
        return "—"
    parts = []
    if "lcp_ms" in vitals:
        v = vitals["lcp_ms"]
        color = "color:#065f46" if v <= 2500 else "color:#991b1b"
        parts.append(f"<span style='{color}'>LCP {v:.0f}ms</span>")
    if "cls" in vitals:
        v = vitals["cls"]
        color = "color:#065f46" if v <= 0.1 else "color:#991b1b"
        parts.append(f"<span style='{color}'>CLS {v:.3f}</span>")
    if "ttfb_ms" in vitals and vitals["ttfb_ms"] is not None:
        parts.append(f"TTFB {vitals['ttfb_ms']:.0f}ms")
    return " &nbsp; ".join(parts) if parts else "—"


def build_qa_html(evidence: dict[str, Any], auto_refresh_s: int = 10) -> str:
    """Render the full /qa status page from an evidence pack dict."""
    wave = html.escape(str(evidence.get("wave", "—")))
    run_at = html.escape(str(evidence.get("run_at", "—")))
    engine = html.escape(str(evidence.get("browser_engine", "—")))
    gating = evidence.get("gating", True)
    gating_label = "gating run (Gate 5)" if gating else "non-gating (qa-only)"
    s_count = evidence.get("scenario_count", 0)
    r_count = evidence.get("regression_count", 0)
    pass_n = evidence.get("pass", 0)
    fail_n = evidence.get("fail", 0)
    skip_n = evidence.get("skip", 0)
    total = pass_n + fail_n + skip_n

    overall_cls = "qa-badge-pass" if fail_n == 0 else "qa-badge-fail"
    overall_label = "✅ ALL PASS" if fail_n == 0 else f"❌ {fail_n} FAIL"

    scenario_rows = ""
    for s in evidence.get("scenarios", []):
        sid = html.escape(str(s.get("id", "")))
        name = html.escape(str(s.get("name", "")))
        status = s.get("status", "pending")
        dur = s.get("duration_ms", 0)
        screenshot = s.get("screenshot", "")
        vitals = s.get("vitals", {})
        error = s.get("error") or ""

        badge = _qa_status_badge(status)
        vitals_html = f"<div class='qa-vitals'>{_vitals_summary(vitals)}</div>"
        screenshot_html = (
            f"<div class='qa-screenshot'><a href='file://{html.escape(screenshot)}' target='_blank'>"
            f"📸 screenshot</a></div>"
            if screenshot else ""
        )
        error_html = (
            f"<div class='qa-error'>{html.escape(error[:200])}</div>"
            if error else ""
        )

        scenario_rows += f"""
<tr>
  <td><code>{sid}</code></td>
  <td>{name}</td>
  <td>{badge}</td>
  <td style='font-size:.8rem;color:#6b7280'>{dur:.0f} ms</td>
  <td>{vitals_html}{screenshot_html}{error_html}</td>
</tr>"""

    refresh_note = f"<span class='qa-refresh'>↻ auto-refresh {auto_refresh_s}s</span>"

    body = f"""
<div class='qa-panel'>
<div class='qa-header'>
  <h1 style='margin:0'>QA Status — Wave {wave}</h1>
  <span class='{overall_cls}' style='padding:4px 12px;border-radius:4px;font-weight:600'>
    {overall_label}
  </span>
  {refresh_note}
</div>
<p style='font-size:.83rem;color:#6b7280;margin:4px 0 16px'>
  Run: {run_at} &nbsp;·&nbsp; Engine: {engine} &nbsp;·&nbsp;
  {s_count} scenarios + {r_count} regressions &nbsp;·&nbsp;
  {pass_n} pass / {fail_n} fail / {skip_n} skip &nbsp;·&nbsp;
  <em>{gating_label}</em>
</p>
<table>
<tr>
  <th>ID</th><th>Name</th><th>Status</th><th>Duration</th><th>Details</th>
</tr>
{scenario_rows}
</table>
</div>
"""
    return _page(
        f"QA — Wave {wave}",
        body,
        qa_mode=True,
        auto_refresh_s=auto_refresh_s,
    )


def build_qa_panel_html(evidence: dict[str, Any]) -> str:
    """
    Render a compact QA status panel for embedding in the main status page
    when --qa mode is active.
    """
    wave = html.escape(str(evidence.get("wave", "—")))
    pass_n = evidence.get("pass", 0)
    fail_n = evidence.get("fail", 0)
    skip_n = evidence.get("skip", 0)
    run_at = html.escape(str(evidence.get("run_at", "—")))
    overall_cls = "qa-badge-pass" if fail_n == 0 else "qa-badge-fail"
    overall_label = "✅ CLEAN" if fail_n == 0 else f"❌ {fail_n} FAIL"

    rows = ""
    for s in evidence.get("scenarios", []):
        sid = html.escape(str(s.get("id", "")))
        name = html.escape(str(s.get("name", "")))
        status = s.get("status", "pending")
        dur = s.get("duration_ms", 0)
        rows += (
            f"<tr><td><code>{sid}</code></td><td>{name}</td>"
            f"<td>{_qa_status_badge(status)}</td>"
            f"<td style='font-size:.8rem;color:#6b7280'>{dur:.0f} ms</td></tr>"
        )

    return f"""
<div class='qa-panel'>
<div class='qa-header'>
  <h2 style='margin:0;border:none;padding:0'>QA — Wave {wave}</h2>
  <span class='{overall_cls}'>{overall_label}</span>
  <span class='qa-refresh'>last run: {run_at} &nbsp;·&nbsp;
    <a href='/qa'>full view</a></span>
</div>
<table>
<tr><th>ID</th><th>Name</th><th>Status</th><th>Duration</th></tr>
{rows}
</table>
</div>
"""


# ---------------------------------------------------------------------------
# POST body parser
# ---------------------------------------------------------------------------

def parse_post_body(raw: bytes) -> dict[str, str]:
    """Parse application/x-www-form-urlencoded body into a plain dict."""
    pairs = urllib.parse.parse_qsl(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {k: v for k, v in pairs}


# ---------------------------------------------------------------------------
# Sign POST handler (pure logic, separated for testability)
# ---------------------------------------------------------------------------

def handle_sign_post(
    repo_root: Path,
    fields: dict[str, str],
    commit: bool = True,
) -> tuple[bool, str]:
    """
    Validate fields, call sign_artifact for each present artifact in gate.
    Returns (success, message).
    *commit* can be set to False in tests to skip git.
    """
    gate = fields.get("gate", "").upper()
    signer = fields.get("signer", "").strip()
    role = fields.get("role", "").strip()
    verdict = fields.get("verdict", "").strip()
    conditions = fields.get("conditions", "").strip()

    if gate not in GATE_MAP:
        return False, f"Unknown gate: {gate!r}"
    if not signer:
        return False, "Signer name is required."
    if role not in VALID_ROLES:
        return False, f"Invalid role: {role!r}"
    if verdict not in VALID_VERDICTS:
        return False, f"Invalid verdict: {verdict!r}"

    signed: list[str] = []
    skipped: list[str] = []
    for rel, _roles, label in GATE_MAP[gate]:
        p = repo_root / rel
        if not p.exists():
            skipped.append(label)
            continue
        try:
            sign_artifact(p, signer, role, gate, verdict, conditions)
            signed.append(rel)
        except Exception as exc:
            return False, f"Failed to sign {label}: {exc}"

    if not signed:
        return False, "No artifacts were present to sign."

    if commit:
        try:
            run_git(
                ["add", *signed],
                cwd=repo_root,
                runner=subprocess.run,
                check=True,
                capture_output=True,
            )
            msg = f"sign({gate}): {signer} [{role}] — {verdict}"
            run_git(
                ["commit", "-m", msg],
                cwd=repo_root,
                runner=subprocess.run,
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, GitProcessPolicyError) as exc:
            if isinstance(exc, GitProcessPolicyError):
                return False, f"Signature written but Git policy blocked commit: {exc}"
            return False, f"Signature written but git commit failed: {exc.stderr.decode()}"

    skip_note = f" ({len(skipped)} artifact(s) skipped — not present)" if skipped else ""
    return True, f"Signed {len(signed)} artifact(s) for {gate}{skip_note}."


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class SignalOSHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for SignalOS serve."""

    repo_root: Path = Path(".")   # set by start_server before use
    qa_mode: bool = False         # W7: set by start_server when --qa flag is active

    def log_message(self, fmt: str, *args: object) -> None:  # silence default logging
        pass

    def _send(self, code: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            try:
                status = get_wave_status(self.repo_root)
            except Exception as exc:
                self._send(500, f"<pre>Error reading status: {html.escape(str(exc))}</pre>")
                return
            page_html = build_status_html(status)
            # W7: in --qa mode, embed QA panel in the status page
            if self.qa_mode:
                evidence = load_latest_qa_evidence(self.repo_root)
                if evidence:
                    panel = build_qa_panel_html(evidence)
                    # Inject before closing </body>
                    page_html = page_html.replace("</body>", panel + "</body>")
            self._send(200, page_html)

        elif path == "/sign":
            gate = qs.get("gate", ["G0"])[0].upper()
            if gate not in GATE_MAP:
                self._send(400, f"<p>Unknown gate: {html.escape(gate)}</p>")
                return
            artifacts = check_gate(self.repo_root, gate)
            self._send(200, build_sign_html(gate, artifacts))

        elif path == "/api/status":
            try:
                status = get_wave_status(self.repo_root)
            except Exception as exc:
                self._send(500, json.dumps({"error": str(exc)}), "application/json")
                return
            self._send(200, json.dumps(status, default=str), "application/json")

        # W7 — QA routes (available in all modes; prominent nav link when --qa active)
        elif path == "/qa":
            evidence = load_latest_qa_evidence(self.repo_root)
            if evidence is None:
                body = (
                    "<h1>QA Status</h1>"
                    "<p style='color:#6b7280'>No QA evidence found. "
                    "Run <code>signalos qa-only</code> or <code>/signal-qa</code> first.</p>"
                )
                self._send(200, _page("QA Status", body, qa_mode=True))
                return
            self._send(200, build_qa_html(evidence, auto_refresh_s=10))

        elif path == "/api/qa":
            evidence = load_latest_qa_evidence(self.repo_root)
            if evidence is None:
                self._send(404, json.dumps({"error": "No QA evidence found"}), "application/json")
                return
            self._send(200, json.dumps(evidence, default=str), "application/json")

        else:
            self._send(404, "<p>Not found</p>")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path != "/sign":
            self._send(404, "<p>Not found</p>")
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        fields = parse_post_body(raw)
        gate = fields.get("gate", "G0").upper()

        ok, msg = handle_sign_post(self.repo_root, fields)

        artifacts = check_gate(self.repo_root, gate) if gate in GATE_MAP else []
        page = build_sign_html(gate, artifacts, message=msg, error=not ok)
        self._send(200, page)


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

def start_server(
    repo_root: Path,
    port: int = 4000,
    host: str = "127.0.0.1",
    qa_mode: bool = False,
) -> None:
    """
    Start the SignalOS HTTP server. Blocks until Ctrl-C.

    Parameters
    ----------
    repo_root : Path
        Root of the SignalOS product repo.
    port : int
        TCP port to bind (default 4000).
    host : str
        Bind address (default 127.0.0.1).
    qa_mode : bool
        W7: when True, embed the QA scenario status panel on the main
        status page and show a QA nav link. Also enables the /qa and
        /api/qa routes (these are always available, but the nav link
        only appears in qa_mode). Pass --qa on the CLI to activate.
    """
    SignalOSHandler.repo_root = repo_root
    SignalOSHandler.qa_mode = qa_mode  # W7

    server = HTTPServer((host, port), SignalOSHandler)
    url = f"http://{host}:{port}"
    qa_note = "  [--qa mode: QA panel active at /qa]" if qa_mode else ""
    print(f"SignalOS serving on {url}{qa_note}  (Ctrl-C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
    finally:
        server.server_close()
