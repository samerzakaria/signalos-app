"""test_emit_invocation.py — Milestone 6: emit.sh invocation wiring.

`signalos init` is supposed to invoke the detected IDE's `emit.sh` after
`register-hooks.sh` so per-IDE config files under `.signalos/<ide>/` get
generated at init time. Before M6 this was a no-op (the script was
shipped in the bundle but never executed).

These tests pin the contract:

  1. Unit test (mock subprocess.run) — guarantees `_register_ide_hooks`
     issues both a register-hooks.sh call AND an emit.sh call with the
     canonical 5-flag argument set, in cwd == project root, with paths
     relative to that root.

  2. Integration test (real bash, real bundle) — runs the actual
     `signalos init` flow against a tempdir with an IDE forced via the
     SIGNALOS_TOOL env var (handled by signalos_lib.ide.detect_ide
     priority order). Asserts the emitter populated `.signalos/<ide>/`
     OR (for emitters that write to IDE-native locations like
     `.claude/commands/`) that *some* emit.sh side-effect is observable.
     Skipped if bash is not available on the host (Windows without Git
     Bash).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.commands import init as init_cmd  # noqa: E402


def _bash_works() -> bool:
    """Return True if a usable `bash` is on PATH (cross-platform check)."""
    if shutil.which("bash") is None:
        return False
    try:
        proc = subprocess.run(
            ["bash", "-c", "echo ok"],
            capture_output=True, text=True, timeout=5,
        )
        return proc.returncode == 0 and "ok" in (proc.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return False


class RegisterIdeHooksUnitTests(unittest.TestCase):
    """Verify _register_ide_hooks invokes register-hooks.sh AND emit.sh."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-emit-unit-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

        # Fake out the on-disk script layout that _register_ide_hooks
        # probes for. We only need the files to *exist* — subprocess.run
        # is going to be mocked so the scripts aren't actually executed.
        self.ide = "claude-code"
        scripts_root = self.tmp / "core" / "tool-adapters" / "emitters" / self.ide
        scripts_root.mkdir(parents=True)
        (scripts_root / "register-hooks.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (scripts_root / "emit.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    def test_invokes_both_scripts_in_order(self):
        """register-hooks.sh fires first, then emit.sh with canonical args."""
        with mock.patch.object(init_cmd, "_bash_available", return_value=True), \
             mock.patch.object(init_cmd.subprocess, "run") as mock_run:
            init_cmd._register_ide_hooks(self.tmp, self.ide)

        # Should be exactly 2 subprocess.run calls.
        self.assertEqual(mock_run.call_count, 2,
                         f"expected 2 subprocess calls, got {mock_run.call_count}: {mock_run.call_args_list}")

        # Call 1: register-hooks.sh
        first_args, first_kwargs = mock_run.call_args_list[0]
        first_argv = first_args[0]
        self.assertEqual(first_argv[0], "bash")
        self.assertTrue(first_argv[1].endswith("register-hooks.sh"),
                        f"first call should target register-hooks.sh, got {first_argv}")
        self.assertEqual(first_kwargs.get("cwd"), str(self.tmp))

        # Call 2: emit.sh with the canonical 5-flag argset
        second_args, second_kwargs = mock_run.call_args_list[1]
        second_argv = second_args[0]
        self.assertEqual(second_argv[0], "bash")
        self.assertTrue(second_argv[1].endswith("emit.sh"),
                        f"second call should target emit.sh, got {second_argv}")
        # Argv should contain all five canonical flags.
        for flag in ("--commands-json", "--skills-json", "--hooks-json",
                     "--preamble", "--output-dir"):
            self.assertIn(flag, second_argv,
                          f"emit.sh argv missing {flag}: {second_argv}")
        # Output dir is the workspace root ("."), not a subdir under .signalos/.
        # Every emit.sh creates its own IDE-native subdirectory inside output_dir
        # (claude-code -> .claude/, cursor -> .cursor/, etc.) at the conventional
        # locations IDEs auto-discover. Nesting under .signalos/<ide> would put
        # config files where IDEs do not scan.
        out_dir_index = second_argv.index("--output-dir") + 1
        self.assertEqual(second_argv[out_dir_index], ".")
        # All shared-registry paths are relative (POSIX form) so the
        # script can resolve them from cwd=target.
        for flag in ("--commands-json", "--skills-json", "--hooks-json", "--preamble"):
            idx = second_argv.index(flag) + 1
            self.assertTrue(second_argv[idx].startswith("core/tool-adapters/_shared/"),
                            f"{flag} should point under core/tool-adapters/_shared/: {second_argv[idx]}")
            self.assertNotIn("\\", second_argv[idx],
                             f"{flag} value should use POSIX separators: {second_argv[idx]}")
        # cwd is the project root.
        self.assertEqual(second_kwargs.get("cwd"), str(self.tmp))
        # timeout guards against a runaway emitter.
        self.assertIn("timeout", second_kwargs)

    def test_skips_when_ide_empty(self):
        """Headless install (no IDE) is a no-op for both scripts."""
        with mock.patch.object(init_cmd.subprocess, "run") as mock_run:
            init_cmd._register_ide_hooks(self.tmp, "")
        self.assertEqual(mock_run.call_count, 0)

    def test_skips_emit_when_only_register_exists(self):
        """If only register-hooks.sh exists, we still run it; emit is skipped."""
        (self.tmp / "core" / "tool-adapters" / "emitters" / self.ide / "emit.sh").unlink()
        with mock.patch.object(init_cmd, "_bash_available", return_value=True), \
             mock.patch.object(init_cmd.subprocess, "run") as mock_run:
            init_cmd._register_ide_hooks(self.tmp, self.ide)
        self.assertEqual(mock_run.call_count, 1)
        argv = mock_run.call_args_list[0][0][0]
        self.assertTrue(argv[1].endswith("register-hooks.sh"))

    def test_skips_register_when_only_emit_exists(self):
        """If only emit.sh exists (some IDEs may ship one without the other)."""
        (self.tmp / "core" / "tool-adapters" / "emitters" / self.ide / "register-hooks.sh").unlink()
        with mock.patch.object(init_cmd, "_bash_available", return_value=True), \
             mock.patch.object(init_cmd.subprocess, "run") as mock_run:
            init_cmd._register_ide_hooks(self.tmp, self.ide)
        self.assertEqual(mock_run.call_count, 1)
        argv = mock_run.call_args_list[0][0][0]
        self.assertTrue(argv[1].endswith("emit.sh"))

    def test_no_op_when_bash_unavailable(self):
        """If bash isn't available, both scripts are skipped (with warning)."""
        # Reset the module-level warned flag so the warning path is exercised.
        init_cmd._BASH_WARNED = False
        with mock.patch.object(init_cmd, "_bash_available", return_value=False), \
             mock.patch.object(init_cmd.subprocess, "run") as mock_run, \
             mock.patch.object(init_cmd.sys, "stderr") as mock_stderr:
            init_cmd._register_ide_hooks(self.tmp, self.ide)
        self.assertEqual(mock_run.call_count, 0)
        # Warning was emitted to stderr.
        self.assertTrue(mock_stderr.write.called,
                        "expected a stderr warning when bash is unavailable")


@unittest.skipUnless(_bash_works(), "requires bash on PATH (Git Bash on Windows)")
class EmitIntegrationTests(unittest.TestCase):
    """Real end-to-end: signalos init populates `.signalos/<ide>/` via emit.sh."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="signalos-emit-int-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        # Save and restore the IDE env vars so we don't leak into other tests.
        self._saved_env = {
            k: os.environ.get(k) for k in
            ("SIGNALOS_TOOL", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE",
             "CURSOR_TRACE_ID", "GITHUB_COPILOT_SESSION", "VSCODE_PID",
             "WINDSURF_SESSION", "CODEX_SESSION", "ANTIGRAVITY_SESSION")
        }
        # Force claude-code detection via the explicit override path.
        for k in self._saved_env:
            os.environ.pop(k, None)
        os.environ["CLAUDE_CODE_SESSION_ID"] = "test-session-m6"

    def tearDown(self):
        # Restore env.
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_init_invokes_emit_for_detected_ide(self):
        """After `signalos init`, the emit.sh side-effects must be visible."""
        target = self.tmp / "proj"
        # Run init non-interactively into a new (non-existent) dir.
        rc = init_cmd.main(["--yes", "--no-git", str(target)])
        self.assertEqual(rc, 0, "signalos init failed")

        # The IDE the test forces is claude-code; its emit.sh treats
        # --output-dir as the project root and writes
        #   <root>/CLAUDE.md
        #   <root>/.claude/commands/*.md
        # We now pass --output-dir=. so these land at conventional locations
        # Claude Code auto-discovers.
        claude_md = target / "CLAUDE.md"
        commands_dir = target / ".claude" / "commands"
        self.assertTrue(
            claude_md.is_file(),
            f"emit.sh did not create CLAUDE.md at {claude_md}; "
            f"target contents: {sorted(p.name for p in target.iterdir())}",
        )
        self.assertTrue(
            commands_dir.is_dir(),
            f"emit.sh did not create .claude/commands/ at {commands_dir}",
        )
        commands = list(commands_dir.glob("*.md"))
        self.assertGreater(
            len(commands), 0,
            f".claude/commands/ was created but contains no *.md files",
        )


if __name__ == "__main__":
    unittest.main()
