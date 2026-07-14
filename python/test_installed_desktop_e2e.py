"""Installed-binary END-TO-END walk of the Foundry DESKTOP product path.

The gap this closes (consultant rec #10): every other test imports the Python
engine *from source* (fast, in-process). NOTHING ever spawned the actual
PyInstaller-packaged sidecar --
``src-tauri/bin/signalos-python-<triple>.exe`` -- and drove it over the real
newline-delimited-JSON stdio protocol the Tauri host uses. So "green unit
tests" could ship while the *installed* path was never walked. This file walks
it: it launches the real shipped binary and drives it the way the desktop app
does.

What runs, and how:

  1. HANDSHAKE (real .exe, $0)   -- spawn the shipped binary, send
     {"command":"capabilities"} and {"command":"ping"}; assert the version and
     that agent:deliver is advertised. Proves the shipped binary is the fresh
     one that can generate.
  2. FRESH PROJECT + STATE (real .exe, $0) -- in a throwaway workspace, drive
     project:create and state:gates over IPC; assert real governance state.
  3. GOVERNED DELIVERY WALK (in-process, $0, deterministic) -- drive
     agent:deliver -> agent:verdict through the SAME engine module the binary
     packages, with a deterministic provider; assert the G0->G4 gate walk
     advances and G4 refuses to sign a stub (INV-2 anti-fake-green).
  4. REAL FILE GENERATION (in-process, $0, deterministic) -- drive the real
     AgentLoop (the per-gate engine) with a scripted provider to write a real
     module + test on disk, then actually RUN the generated test to prove the
     files landed and the tests are runnable.
  5. BROWSE THE RESULT ($0) -- enumerate a generated tree through a faithful
     reimplementation of the Rust ``list_workspace_dir`` contract (nested,
     skip-dirs, refuse-outside-root).
  6. LIVE SMOKE (real .exe, OPT-IN, costs cents) -- SIGNALOS_E2E_LIVE=1 +
     OPENROUTER_API_KEY: drive agent:deliver through the real binary against a
     real provider and assert it reaches the G0 checkpoint. Proves the shipped
     binary's generation engine actually reaches a live model.
  7. INSTALLED GOVERNANCE-INIT (real .exe, OPT-IN reproduction) --
     SIGNALOS_E2E_SLOW=1: reproduces the finding that ``signal-init`` through
     the frozen binary does not complete within the desktop's own 120s budget
     (it copies 479 bundle files one-by-one via importlib.resources.as_file,
     which is fast from a wheel but pathological when frozen).

GUI-ONLY (cannot run headless here; see the module docstring in
``e2e/user-journey.smoke.mjs`` and the report): the Preact cockpit rendering,
real window/button clicks, the Rust ``list_workspace_dir`` /
``preview_workspace_files`` / ``start_preview`` Tauri commands (they need the
compiled host + a selected WorkspaceState + a display), and a full funded
G0->G5 delivery with a human approving each gate.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent


# ---------------------------------------------------------------------------
# Locate the shipped, PyInstaller-packaged sidecar binary.
#
# It is a build artifact (src-tauri/bin/ is gitignored), so it is absent in a
# fresh checkout / CI without a sidecar build, and absent in a git worktree
# (which does not carry ignored files). Every binary-driven test SKIPS cleanly
# when it cannot be found, so the suite is green where the artifact is missing
# and meaningful where it exists.
# ---------------------------------------------------------------------------


def _candidate_bin_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("SIGNALOS_SIDECAR_BIN", "").strip()
    if env:
        p = Path(env)
        # Allow either a direct file path or a directory.
        return [p if p.is_dir() else p.parent]
    dirs.append(REPO_ROOT / "src-tauri" / "bin")
    # In a worktree the real (ignored) binary lives in the MAIN checkout.
    parts = REPO_ROOT.as_posix().split("/.claude/worktrees/")
    if len(parts) == 2:
        dirs.append(Path(parts[0]) / "src-tauri" / "bin")
    return dirs


def _find_sidecar() -> Path | None:
    env = os.environ.get("SIGNALOS_SIDECAR_BIN", "").strip()
    if env and Path(env).is_file():
        return Path(env)
    for d in _candidate_bin_dirs():
        if not d.is_dir():
            continue
        for cand in sorted(d.glob("signalos-python-*")):
            if cand.suffix in (".pkg", ".spec"):
                continue
            if cand.is_file() and os.access(cand, os.X_OK):
                return cand
        # Non-executable-bit filesystems (Windows): accept a .exe by name.
        for cand in sorted(d.glob("signalos-python-*.exe")):
            if cand.is_file():
                return cand
    return None


SIDECAR = _find_sidecar()

# ensure-sidecar.sh writes a tiny shell STUB (<100 KB) for lint-only CI runs; a
# real PyInstaller onefile is tens of MB. A stub does nothing and cannot answer
# the IPC handshake, so a binary-driven test run against it dies on a
# BrokenPipe. Treat a stub as "no real binary" and SKIP those tests (they need a
# genuine build) rather than fail them. Matches ensure-sidecar.sh's own 100 KB
# stub threshold.
_STUB_MAX_BYTES = 100 * 1024
if SIDECAR is not None:
    try:
        if SIDECAR.stat().st_size < _STUB_MAX_BYTES:
            SIDECAR = None
    except OSError:
        SIDECAR = None

_SKIP_NO_BIN = SIDECAR is None
_SKIP_REASON = (
    "packaged sidecar binary not found or is a lint-only stub (<100 KB). Set "
    "SIGNALOS_SIDECAR_BIN, or build a real one with scripts/bundle-sidecar.ps1; "
    "src-tauri/bin/ is a gitignored artifact."
)

# The rebuilt sidecar under test reports this version in its capability
# handshake. Read from package.json so this test never hardcodes a stale number
# and tracks the shipped app version the way the sidecar itself does.
try:
    _PKG_VERSION = json.loads((REPO_ROOT / "package.json").read_text("utf-8")).get("version")
except Exception:  # pragma: no cover - defensive
    _PKG_VERSION = None


def _host_env(extra: dict | None = None) -> dict:
    """Reproduce the environment the Tauri host injects when it spawns the
    sidecar. The host sets SIGNALOS_APP_VERSION = CARGO_PKG_VERSION
    (src-tauri/src/sidecar.rs), so the sidecar reports its version from env
    rather than by walking up the workspace cwd for package.json -- without it,
    a sidecar launched in a bare workspace reports version "unknown" (its
    cwd-based fallback). Drive it the way the desktop does."""
    env = dict(os.environ)
    if _PKG_VERSION:
        env["SIGNALOS_APP_VERSION"] = _PKG_VERSION
    if extra:
        env.update(extra)
    return env


def _rmtree_retry(path: str) -> bool:
    """Remove a workspace the sidecar chdir'd into. On Windows the child holds
    the cwd until it fully exits, so retry a few times."""
    import shutil

    for _ in range(15):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
        time.sleep(0.3)
    return not os.path.exists(path)


class _Sidecar:
    """Spawn the packaged sidecar and speak its NDJSON stdio protocol with a
    background reader + per-call timeout, exactly as a robust IPC client (the
    Rust host) would -- so a slow/hung command surfaces as a clear timeout, not
    an infinite block."""

    def __init__(self, cwd: str, env: dict | None = None):
        self.cwd = cwd
        self.proc = subprocess.Popen(
            [str(SIDECAR)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env or _host_env(),
            cwd=cwd,
        )
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        # Consume the init line: {"id":"init","ok":true,"data":{"ready":true}}.
        # The FIRST spawn in a session pays a heavy PyInstaller onefile
        # extraction cost (the 72MB frozen binary unpacks to a temp dir), so the
        # init line can take tens of seconds on a cold/loaded machine -- give it
        # generous headroom. A late init line that arrives after this returns is
        # harmless: it simply doesn't match any later call's req_id and is
        # skipped.
        self.ready = self._drain_terminal("init", timeout=90) is not None

    def _pump(self) -> None:
        try:
            for line in self.proc.stdout:
                self._q.put(line)
        finally:
            self._q.put(None)

    def _next(self, timeout: float):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return "<timeout>"

    def _drain_terminal(self, req_id: str, timeout: float):
        """Return the terminal response for req_id, skipping streamed
        progress/agent-event lines. Returns None on timeout/EOF."""
        end = time.time() + timeout
        while time.time() < end:
            line = self._next(max(0.05, end - time.time()))
            if line == "<timeout>":
                return None
            if line is None:
                return None
            try:
                msg = json.loads(line)
            except (TypeError, ValueError):
                continue
            if msg.get("kind") in ("progress", "agent-event"):
                continue
            if msg.get("id") == req_id and "ok" in msg:
                return msg
        return None

    def call(self, command: str, args=None, timeout: float = 45.0, **extra):
        req = {"id": command, "command": command, "cwd": self.cwd}
        if args is not None:
            req["args"] = args
        req.update(extra)
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        return self._drain_terminal(command, timeout)

    def collect(self, command, args, req_id, budget):
        """Send a command and collect every event line until the terminal
        response for req_id or the budget elapses. Returns (events, final)."""
        req = {"id": req_id, "command": command, "cwd": self.cwd, "args": args}
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        end = time.time() + budget
        events, final = [], None
        while time.time() < end:
            line = self._next(max(0.1, end - time.time()))
            if line in ("<timeout>", None):
                break
            try:
                msg = json.loads(line)
            except (TypeError, ValueError):
                continue
            if msg.get("id") == req_id and msg.get("kind") is None and "ok" in msg:
                final = msg
                break
            events.append(msg)
        return events, final

    def close(self):
        with contextlib.suppress(Exception):
            self.proc.stdin.close()
        with contextlib.suppress(Exception):
            self.proc.terminate()
            self.proc.wait(timeout=8)
        with contextlib.suppress(Exception):
            self.proc.kill()


@unittest.skipIf(_SKIP_NO_BIN, _SKIP_REASON)
class TestShippedBinaryHandshake(unittest.TestCase):
    """Step 1 -- the capability handshake against the real packaged .exe."""

    def test_capabilities_reports_version_and_required_commands(self):
        ws = tempfile.mkdtemp(prefix="sos_e2e_hs_")
        sc = _Sidecar(ws)
        try:
            resp = sc.call("capabilities", timeout=60)
            self.assertIsNotNone(resp, "no capabilities response from the shipped binary")
            self.assertTrue(resp.get("ok"), msg=resp)
            data = resp.get("data") or {}
            # The shipped binary must advertise every desktop-critical command.
            # Checking only the older generation command lets a binary built
            # before the War Room panel incorrectly pass this freshness gate.
            self.assertIn("agent:deliver", data.get("commands", []),
                          "shipped binary does not advertise agent:deliver -- stale sidecar")
            self.assertIn("panel:consult", data.get("commands", []),
                          "shipped binary does not advertise panel:consult -- stale sidecar")
            self.assertEqual(data.get("protocol"), 1)
            if _PKG_VERSION:
                self.assertEqual(data.get("version"), _PKG_VERSION,
                                 f"binary version {data.get('version')} != package.json {_PKG_VERSION}")

            ping = sc.call("ping", timeout=45)
            self.assertTrue(ping and ping.get("ok"), msg=ping)
            self.assertEqual((ping.get("data") or {}).get("version"), data.get("version"),
                             "ping version disagrees with capabilities version")
        finally:
            sc.close()
            self.assertTrue(_rmtree_retry(ws), f"failed to clean {ws}")


@unittest.skipIf(_SKIP_NO_BIN, _SKIP_REASON)
class TestShippedBinaryFreshProject(unittest.TestCase):
    """Step 2 (runnable half) -- a fresh workspace, driven over IPC through the
    real binary: create a project and read real governance gate state.

    NOTE: the core-CLI-backed commands pay a one-time cold cost inside the
    frozen binary (importing signalos_lib.cli), so timeouts are generous."""

    def test_project_create_and_gate_state(self):
        ws = tempfile.mkdtemp(prefix="sos_e2e_proj_")
        sc = _Sidecar(ws)
        try:
            created = sc.call("project:create", [json.dumps({"name": "Demo Tracker"})], timeout=60)
            self.assertTrue(created and created.get("ok"), msg=created)
            cdata = created.get("data") or {}
            self.assertEqual(cdata.get("status"), "ok", msg=cdata)
            self.assertEqual(cdata.get("active"), "demo-tracker")
            self.assertTrue((Path(ws) / ".signalos" / "projects.json").is_file(),
                            "project registry not written to disk")

            gates = sc.call("state:gates", timeout=60)
            self.assertTrue(gates and gates.get("ok"), msg=gates)
            gate_list = gates.get("data") or []
            self.assertEqual(len(gate_list), 6, "expected 6 governance gates G0..G5")
            self.assertEqual(gate_list[0]["name"], "Constitution")
            # A brand-new workspace: G0 is the open/current gate, none signed.
            self.assertEqual(gate_list[0]["status"], "current")
        finally:
            sc.close()
            self.assertTrue(_rmtree_retry(ws), f"failed to clean {ws}")


# ---------------------------------------------------------------------------
# In-process engine proofs. These import the exact module the binary packages
# (signalos_ipc_server) and drive it with a deterministic provider, so they run
# $0 and offline. They prove the ENGINE the shipped binary carries; the binary's
# own live generation is proven by the opt-in TestLiveDeliverySmoke below.
# ---------------------------------------------------------------------------


def _end_adapter_factory():
    from signalos_lib.harness import AgentResponse, TokenUsage
    from signalos_lib.product.provider_adapter import ProviderAdapter, ProviderCapabilities

    class _EndProvider:
        def chat(self, messages, model="test", tools=None, stream=False):
            return AgentResponse(content="Understood.", tool_calls=None,
                                 stop_reason="end_turn", usage=TokenUsage(1, 1))

    def factory(model, provider=None):
        caps = ProviderCapabilities(model=model, supports_tool_calls=True,
                                    supports_streaming=True, context_length=200_000)
        return ProviderAdapter(model=model, provider=_EndProvider(), capabilities=caps)

    return factory


class TestGovernedDeliveryWalkDeterministic(unittest.TestCase):
    """Step 2/3 -- drive agent:deliver -> agent:verdict through the IPC engine
    module and assert the governed G0->G4 gate walk advances, then that G4
    REFUSES to sign a stub (INV-2: never sign a fake-green build)."""

    def setUp(self):
        import signalos_ipc_server as srv
        from signalos_lib.product.enforcement_state import StaticEnforcementProvider

        self.srv = srv
        self._prev_cwd = os.getcwd()
        self._ws = tempfile.mkdtemp(prefix="sos_e2e_walk_")
        os.chdir(self._ws)
        self._signed: list[str] = []

        def fake_sign(root, gate, signer, role, verdict, conditions):
            self._signed.append(gate)
            return [f"{gate}.md"]

        srv._AGENT_ADAPTER_FACTORY = _end_adapter_factory()
        srv._AGENT_ENFORCEMENT_FACTORY = lambda: StaticEnforcementProvider(trust_tier="T3")
        srv._DELIVERY_SIGN_FN = fake_sign
        srv._ACTIVE_DELIVERIES.clear()

    def tearDown(self):
        srv = self.srv
        srv._AGENT_ADAPTER_FACTORY = None
        srv._AGENT_ENFORCEMENT_FACTORY = None
        srv._DELIVERY_SIGN_FN = None
        srv._ACTIVE_DELIVERIES.clear()
        os.chdir(self._prev_cwd)
        _rmtree_retry(self._ws)

    def _handle(self, req):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return self.srv.handle(req)

    def test_walk_advances_and_g4_refuses_stub(self):
        d = self._handle({
            "id": "d", "command": "agent:deliver", "cwd": self._ws,
            "args": {"prompt": "build a task tracker", "provider": "openai",
                     "model": "gpt-test", "run_id": "det-walk"},
        })
        self.assertTrue(d.get("ok"), msg=d)
        self.assertEqual((d.get("data") or {}).get("gate"), "G0")
        # The scripted end-turn provider writes no gate artifacts, so the real
        # outcome gate must surface this checkpoint as blocked.  This test
        # injects its own signing seam below (which deliberately owns/bypasses
        # normal reviewability enforcement) only so it can continue to G4 and
        # exercise the independent anti-fake-green build wall.
        self.assertEqual((d.get("data") or {}).get("status"), "blocked")
        state_path = (Path(self._ws) / ".signalos" / "agent-runs"
                      / "det-walk" / "delivery.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertFalse(state.get("last_outcome", {}).get("ok"))
        self.assertIn("stalled_no_tool", state.get("last_outcome", {}).get("reason", ""))

        gates_seen = []
        last = None
        for i in range(6):
            v = self._handle({
                "id": f"v{i}", "command": "agent:verdict", "cwd": self._ws,
                "args": {"run_id": "det-walk", "gate_id": f"G{i}", "verdict": "approve"},
            })
            self.assertTrue(v.get("ok"), msg=v)
            last = v.get("data") or {}
            gates_seen.append(last.get("gate"))
            if last.get("status") in ("complete", "stopped", "build-not-verified"):
                break

        # The walk advanced through the early gates and reached the BUILD gate.
        self.assertEqual(self._signed, ["G0", "G1", "G2", "G3"],
                         f"expected G0..G3 signed on the walk, got {self._signed}")
        self.assertIn("G4", gates_seen, f"walk never reached G4: {gates_seen}")
        # G4 must NOT sign: the deterministic provider wrote no real product
        # source, so the INV-2 build verifier refuses (anti-fake-green).
        self.assertEqual(last.get("status"), "build-not-verified",
                         f"G4 signed a stub build (INV-2 violated): {last}")
        self.assertNotIn("G4", self._signed)


class TestRealFileGenerationRunnable(unittest.TestCase):
    """Step 2 -- the per-gate engine (AgentLoop) writes REAL files on disk and
    the generated test is runnable. Drives the same AgentLoop agent:deliver runs
    at each gate, with a scripted provider, in delivery context; then executes
    the generated test to prove it is green."""

    def test_agent_loop_writes_files_and_test_passes(self):
        from signalos_lib.harness import AgentResponse, AgentTestProvider, TokenUsage, ToolCall
        from signalos_lib.product.agent_loop import AgentLoop
        from signalos_lib.product.enforcement_state import StaticEnforcementProvider
        from signalos_lib.product.provider_adapter import ProviderAdapter, ProviderCapabilities

        calc_src = "def add(a, b):\n    return a + b\n"
        calc_test = (
            "import sys, os\n"
            "sys.path.insert(0, os.path.dirname(__file__))\n"
            "from calc import add\n\n\n"
            "def test_add():\n    assert add(2, 3) == 5\n"
        )

        def tool(name, arguments, cid="c1"):
            return AgentResponse(content=None,
                                 tool_calls=[ToolCall(id=cid, name=name, arguments=arguments)],
                                 stop_reason="tool_use", usage=TokenUsage(1, 1))

        script = [
            tool("write_file", {"path": "calc.py", "content": calc_src}),
            tool("write_file", {"path": "test_calc.py", "content": calc_test}),
            AgentResponse(content="Wrote calc.py + test_calc.py.", tool_calls=None,
                          stop_reason="end_turn", usage=TokenUsage(1, 1)),
        ]

        ws = Path(tempfile.mkdtemp(prefix="sos_e2e_gen_"))
        try:
            (ws / ".signalos").mkdir(parents=True, exist_ok=True)
            caps = ProviderCapabilities(model="gpt-test", supports_tool_calls=True,
                                        supports_streaming=True, context_length=200_000)
            adapter = ProviderAdapter(model="gpt-test",
                                      provider=AgentTestProvider(script=list(script)),
                                      capabilities=caps)
            events: list[dict] = []
            loop = AgentLoop(
                adapter=adapter, repo_root=ws,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="gen-e2e", execution_context="delivery", emit=events.append,
            )
            result = loop.run(system_prompt="You are a build agent. Use tools to write and verify code.",
                              user_message="Write calc.py with add() and a passing test.")

            self.assertEqual(result.status, "completed", getattr(result, "error", None))
            # REAL files landed on disk via the engine's write dispatch.
            self.assertTrue((ws / "calc.py").is_file(), "calc.py was not written")
            self.assertTrue((ws / "test_calc.py").is_file(), "test_calc.py was not written")
            self.assertEqual((ws / "calc.py").read_text("utf-8"), calc_src)

            # The generated test is RUNNABLE and green.
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "test_calc.py"],
                cwd=str(ws), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(proc.returncode, 0,
                             f"generated test did not pass:\n{proc.stdout}\n{proc.stderr}")
        finally:
            _rmtree_retry(str(ws))


# ---------------------------------------------------------------------------
# Step 3 -- BROWSE THE RESULT. Faithful reimplementation of the Rust
# ``list_workspace_dir`` contract (src-tauri/src/ipc.rs::list_workspace_entries):
# nested listing via a relative path, a fixed skip-set of build dirs, and a
# hard refusal to list outside the workspace root. The actual Tauri command
# needs the compiled host + a selected WorkspaceState + a display, so this
# proves the *contract* over a real generated tree headlessly.
# ---------------------------------------------------------------------------

_LWD_SKIP = {".git", "node_modules", "target", "dist", "build", ".venv", "venv",
             ".sidecar-venv", "__pycache__", ".next", ".turbo", ".cache"}


def _list_workspace_dir(workspace: Path, relative_path: str | None):
    root = workspace.resolve()
    rel = (relative_path or ".").strip() or "."
    target = root if rel == "." else (root / rel)
    canon = target.resolve()
    if not (canon == root or str(canon).startswith(str(root) + os.sep)):
        raise ValueError("Refused to list outside the workspace.")
    out = []
    for ent in sorted(canon.iterdir(), key=lambda p: p.name):
        if ent.name in _LWD_SKIP:
            continue
        out.append({
            "name": ent.name,
            "path": ent.relative_to(root).as_posix(),
            "kind": "dir" if ent.is_dir() else "file",
            "bytes": ent.stat().st_size if ent.is_file() else None,
        })
    return out


class TestGeneratedTreeIsBrowsable(unittest.TestCase):
    def test_nested_enumeration_and_containment(self):
        ws = Path(tempfile.mkdtemp(prefix="sos_e2e_browse_"))
        try:
            # A realistic generated product tree, nested, plus a build dir that
            # the contract must hide.
            (ws / "src" / "components").mkdir(parents=True)
            (ws / "tests").mkdir()
            (ws / "node_modules" / "left-pad").mkdir(parents=True)
            (ws / "package.json").write_text('{"name":"demo"}', "utf-8")
            (ws / "src" / "App.tsx").write_text("export const App = () => null\n", "utf-8")
            (ws / "src" / "components" / "Button.tsx").write_text("export const Button = () => null\n", "utf-8")
            (ws / "tests" / "app.test.ts").write_text("test('x', () => {})\n", "utf-8")

            top = _list_workspace_dir(ws, ".")
            names = {e["name"]: e["kind"] for e in top}
            self.assertEqual(names.get("src"), "dir")
            self.assertEqual(names.get("tests"), "dir")
            self.assertEqual(names.get("package.json"), "file")
            self.assertNotIn("node_modules", names, "build dir must be skipped")

            nested = _list_workspace_dir(ws, "src")
            nnames = {e["name"] for e in nested}
            self.assertEqual(nnames, {"App.tsx", "components"})
            # Paths are workspace-relative in POSIX form (the UI file tree relies
            # on this).
            self.assertEqual({e["path"] for e in nested}, {"src/App.tsx", "src/components"})

            deep = _list_workspace_dir(ws, "src/components")
            self.assertEqual({e["name"] for e in deep}, {"Button.tsx"})

            # Containment: a traversal outside the workspace is refused.
            with self.assertRaises(ValueError):
                _list_workspace_dir(ws, "../..")
        finally:
            _rmtree_retry(str(ws))


# ---------------------------------------------------------------------------
# Step 4 -- OPT-IN live smoke through the REAL binary (costs cents).
# ---------------------------------------------------------------------------


def _load_env_key(name: str) -> str | None:
    """Read a key straight from the repo .env (conftest.py clears provider keys
    from os.environ for hermetic tests, so we must not rely on the ambient
    environment). Checks the worktree and, if applicable, the main checkout."""
    candidates = [REPO_ROOT / ".env"]
    parts = REPO_ROOT.as_posix().split("/.claude/worktrees/")
    if len(parts) == 2:
        candidates.append(Path(parts[0]) / ".env")
    for env_file in candidates:
        if not env_file.is_file():
            continue
        for line in env_file.read_text("utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == name and v.strip():
                return v.strip()
    return None


_LIVE_ON = os.environ.get("SIGNALOS_E2E_LIVE") == "1"


@unittest.skipIf(_SKIP_NO_BIN, _SKIP_REASON)
@unittest.skipUnless(_LIVE_ON, "live smoke is opt-in: set SIGNALOS_E2E_LIVE=1 (uses OPENROUTER_API_KEY, costs cents)")
class TestLiveDeliverySmoke(unittest.TestCase):
    """The one integration a $0 path cannot prove: the shipped binary's
    generation engine actually reaching a live provider. Drives agent:deliver
    through the real .exe and asserts it runs the G0 gate to a checkpoint."""

    def test_agent_deliver_reaches_g0_checkpoint(self):
        key = _load_env_key("OPENROUTER_API_KEY")
        if not key:
            self.skipTest("OPENROUTER_API_KEY not found in .env")
        env = _host_env({
            "OPENROUTER_API_KEY": key,
            "SIGNALOS_AI_WAVE_BUDGET_USD": "0.50",  # hard budget guard
        })
        ws = tempfile.mkdtemp(prefix="sos_e2e_live_")
        sc = _Sidecar(ws, env=env)
        try:
            events, final = sc.collect(
                "agent:deliver",
                {"prompt": "Create a tiny Python module with add(a,b) and a test.",
                 "provider": "openrouter", "model": "openai/gpt-oss-120b",
                 "run_id": "live-smoke"},
                req_id="live", budget=180,
            )
            self.assertIsNotNone(final, f"no terminal response; events={events[:5]}")
            self.assertTrue(final.get("ok"), msg=final)
            data = final.get("data") or {}
            self.assertEqual(data.get("gate"), "G0")
            self.assertEqual(data.get("status"), "awaiting-verdict")
            # The real gate engine emitted a G0 checkpoint after the live turn.
            self.assertTrue(any(e.get("type") == "gate" and e.get("gate") == "G0" for e in events),
                            "no G0 gate checkpoint event from the live run")
        finally:
            sc.close()
            _rmtree_retry(ws)


_SLOW_ON = os.environ.get("SIGNALOS_E2E_SLOW") == "1"


@unittest.skipIf(_SKIP_NO_BIN, _SKIP_REASON)
@unittest.skipUnless(_SLOW_ON, "opt-in reproduction: set SIGNALOS_E2E_SLOW=1 (runs up to ~130s)")
class TestInstalledGovernanceInit(unittest.TestCase):
    """REGRESSION GUARD for an installed-path finding: signal-init (the
    governance bootstrap the onboarding flow calls, src/services/workspace.ts)
    used to NOT complete within the desktop's 120s budget through the frozen
    binary -- it copied ~479 bundle files one-by-one via
    importlib.resources.as_file, cheap from a wheel but pathological when
    PyInstaller-frozen (5 of 479 files at 150s). From source the same command
    finishes in ~4s. The fix resolves the bundle to a concrete dir and copies
    with os.walk. This asserts the FIXED behavior against the REAL frozen binary
    (the only place the bug is visible)."""

    def test_signal_init_completes_within_desktop_budget(self):
        ws = tempfile.mkdtemp(prefix="sos_e2e_init_")
        sc = _Sidecar(ws)
        try:
            t0 = time.perf_counter()
            resp = sc.call("signal-init", ["--mode", "keep"], timeout=120)
            elapsed = time.perf_counter() - t0
            # 1. Completed within the desktop's budget (the pathology timed out).
            self.assertIsNotNone(resp, "signal-init did not complete within the desktop's 120s budget")
            self.assertTrue(resp.get("ok"), msg=resp)
            root = Path(ws)
            # 2. Structural sentinels: the ~479-file bundle lands in the PROJECT
            #    ROOT, not under .signalos/. A healthy init has all three. (The
            #    old test wrongly counted .signalos/ files -- a healthy init only
            #    writes ~5 there, so that assertion failed even on a good init.)
            for sentinel in ("core", "integrations", ".claude-plugin"):
                self.assertTrue((root / sentinel).is_dir(), f"missing bundle dir: {sentinel!r}")
            # 3. The FULL bundle copied (hundreds of files), not a partial stall.
            total = sum(len(files) for _, _, files in os.walk(root))
            self.assertGreater(total, 450, f"init wrote only {total} total files -- partial/stalled bundle copy")
            # 4. The completion marker (init's FINAL write) is present -> the
            #    workspace is fully, not half-, initialized.
            self.assertTrue((root / ".signalos" / "INIT_COMPLETE.json").is_file(),
                            "init completion marker absent -- workspace half-initialized")
            # 5. Generous wall-clock: the failure mode was 150s+, not 8s, so a
            #    wide bound catches the pathology without CI-jitter flake.
            self.assertLess(elapsed, 90, f"signal-init took {elapsed:.0f}s -- pathologically slow")
        finally:
            sc.close()
            _rmtree_retry(ws)


if __name__ == "__main__":
    unittest.main(verbosity=2)
