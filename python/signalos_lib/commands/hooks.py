# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/commands/hooks.py
# W3.5 — signalos hooks subcommand (AMD-CORE-018)

from __future__ import annotations

__all__ = ["main"]

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="signalos hooks",
        description=(
            "Hook management and dry-run testing (W3.5, AMD-CORE-018).\n\n"
            "Actions:\n"
            "  test  — invoke each registered hook with a synthetic dry-run\n"
            "          payload; reports pass/fail without journal writes\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("action", choices=["test"], help="Action to run.")
    parser.add_argument("--repo-root", default=None, metavar="PATH",
                        help="Repo root (default: cwd).")
    parser.add_argument("--hook", default=None, metavar="NAME",
                        help="Test only this hook by name.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit JSON output.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()

    if args.action == "test":
        return _cmd_test(repo_root, args)

    parser.print_help(sys.stderr)
    return 1


def _cmd_test(repo_root: Path, args) -> int:
    hooks_json = repo_root / "core" / "tool-adapters" / "_shared" / "hooks.json"
    if not hooks_json.exists():
        sys.stderr.write(f"signalos hooks test: hooks.json not found at {hooks_json}\n")
        return 2

    try:
        hooks = json.loads(hooks_json.read_text(encoding="utf-8"))
    except Exception as exc:
        sys.stderr.write(f"signalos hooks test: cannot parse hooks.json: {exc}\n")
        return 2

    if not isinstance(hooks, list):
        sys.stderr.write("signalos hooks test: hooks.json must be a list\n")
        return 2

    if args.hook:
        hooks = [h for h in hooks if h.get("name") == args.hook]
        if not hooks:
            sys.stderr.write(f"signalos hooks test: hook {args.hook!r} not found\n")
            return 2

    results = []
    for hook in hooks:
        r = _run_hook_dry(repo_root, hook)
        results.append(r)

    if args.as_json:
        sys.stdout.write(json.dumps({"results": results}, ensure_ascii=False) + "\n")
        return 0 if all(r["passed"] for r in results) else 1

    _render_hook_table(results)
    return 0 if all(r["passed"] for r in results) else 1


def _run_hook_dry(repo_root: Path, hook: dict) -> dict:
    name = hook.get("name", "?")
    source = hook.get("source", "")
    script = repo_root / source if source else None

    if not script or not script.exists():
        # Fail closed: a registered hook whose script is missing is broken
        # wiring, not a skippable hook — reporting it green would leave the
        # guard silently inert.
        return {
            "name": name, "passed": False, "skipped": True,
            "reason": f"script not found: {source}",
            "duration_ms": 0, "stdout": "", "stderr": "",
        }

    env = {**os.environ,
           "SIGNALOS_DRY_RUN": "1",
           "SIGNALOS_HOOK_TEST": "1",
           "REPO_ROOT": str(repo_root)}
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            ["bash", str(script)],
            capture_output=True, text=True, timeout=30,
            cwd=str(repo_root), env=env,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)
        return {
            "name": name, "passed": proc.returncode == 0, "skipped": False,
            "reason": "", "duration_ms": duration_ms,
            "stdout": proc.stdout[:500], "stderr": proc.stderr[:500],
        }
    except subprocess.TimeoutExpired:
        return {
            "name": name, "passed": False, "skipped": False,
            "reason": "timed out", "duration_ms": 30000,
            "stdout": "", "stderr": "timed out after 30s",
        }
    except Exception as exc:
        return {
            "name": name, "passed": False, "skipped": False,
            "reason": str(exc), "duration_ms": 0,
            "stdout": "", "stderr": str(exc),
        }


def _render_hook_table(results: list[dict]) -> None:
    name_w = max((len(r["name"]) for r in results), default=4)
    name_w = max(name_w, 4)

    sys.stdout.write("\n")
    sys.stdout.write(f"  {'Hook':<{name_w}}  Status   ms\n")
    sys.stdout.write(f"  {'-'*name_w}  -------  ---\n")
    for r in results:
        if r.get("skipped"):
            icon, label = "–", "skip  "
        elif r["passed"]:
            icon, label = "✓", "pass  "
        else:
            icon, label = "✗", "FAIL  "
        ms = str(r["duration_ms"]) if not r.get("skipped") else "—"
        sys.stdout.write(f"  {r['name']:<{name_w}}  {icon} {label}  {ms}\n")
    sys.stdout.write("\n")
