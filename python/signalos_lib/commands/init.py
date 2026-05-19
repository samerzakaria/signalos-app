"""SignalOS init — bootstrap a new SignalOS project.

`signalos init <path>` creates a fully wired SignalOS project at *path*
with no need to clone the repo. The bundled scaffolding ships inside
the wheel under `signalos_lib._bundle/` and includes:

  * `.claude-plugin/plugin.json`            — Claude Code plugin manifest
  * `core/execution/commands/*.md`          — 49 slash commands
  * `core/execution/skills/*`               — skills referenced by hooks
  * `core/tool-adapters/emitters/*`         — 8 IDE emitters (7 + harness)
  * `core/tool-adapters/_shared/*.json`     — wiring registries
  * `integrations/{rules, hooks, plugins}`  — per-IDE configs

Plus runtime scaffolding created fresh per project:
  * `.signalos/sessions/`                   — empty session dir
  * `.signalos/worktree-state.json`         — empty worktree list
  * `.signalos/AUDIT_TRAIL.jsonl`           — empty audit trail
  * `core/strategy/PLAN.md`                 — skeleton wave plan

After scaffolding the command auto-detects the active IDE via
`signalos_lib.ide.detect_ide()` and runs the matching emitter's
`register-hooks.sh` so the user's editor sees the SignalOS commands
on first reload.

Exit codes:
  0  — project bootstrapped successfully
  1  — user error (target exists non-empty, no path given, etc.)
  2  — internal error (bundle missing, IO failure)
"""

from __future__ import annotations

import argparse
import importlib.resources
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from signalos_lib.ide import detect_ide

__all__ = ["main"]

_BUNDLE_PACKAGE = "signalos_lib._bundle"

# Files we consider "safe to ignore" when deciding if a target dir is empty.
# A user pointing init at a fresh `git init` directory should still succeed.
_IGNORABLE_ENTRIES = {".git", ".gitignore", ".gitkeep", ".DS_Store"}

# Files inside _bundle that we never overwrite when --force is used. Keeps
# user-authored content like a customised PLAN.md from being clobbered.
_PROTECTED_FILES = {".env", "PLAN.md.signed", ".signalos/AUDIT_TRAIL.jsonl"}


# ---------------------------------------------------------------------------
# Path checks
# ---------------------------------------------------------------------------

def _is_target_empty(target: Path) -> bool:
    """Return True if *target* doesn't exist OR contains only ignorable files."""
    if not target.exists():
        return True
    if not target.is_dir():
        return False
    for entry in target.iterdir():
        if entry.name in _IGNORABLE_ENTRIES:
            continue
        return False
    return True


def _confirm_create(target: Path) -> bool:
    """Prompt the user to create a non-existent path. Returns True to proceed.

    Refuses (False) if stdin isn't a TTY — call sites must respect that
    and emit a "use --yes" hint so non-interactive callers don't hang.
    """
    if not sys.stdin.isatty():
        return False
    sys.stderr.write(
        f"Path '{target}' does not exist. Create it and bootstrap "
        f"SignalOS into it? [y/N] "
    )
    sys.stderr.flush()
    answer = sys.stdin.readline().strip().lower()
    return answer in {"y", "yes"}


# ---------------------------------------------------------------------------
# Bundle extraction
# ---------------------------------------------------------------------------

def _bundle_root():
    """Return a Traversable pointing at the packaged _bundle/ directory.

    Uses importlib.resources so the same code path works for editable
    installs (cli/signalos_lib/_bundle/) and for installed wheels (where
    the bundle is unpacked into site-packages).
    """
    try:
        return importlib.resources.files(_BUNDLE_PACKAGE)
    except (ModuleNotFoundError, FileNotFoundError) as exc:
        raise RuntimeError(
            "signalos init: bundled scaffolding (_bundle/) is missing. "
            "If you installed from a development checkout, run "
            "`python scripts/build_bundle.py` to regenerate it."
        ) from exc


def _copy_bundle(target: Path, force: bool) -> int:
    """Copy every file from the packaged bundle into *target*.

    Returns the count of files written.
    """
    bundle = _bundle_root()
    written = 0
    for src in _iter_bundle_files(bundle):
        rel = _bundle_relpath(bundle, src)
        if rel in _PROTECTED_FILES:
            continue
        dst = target / rel
        if dst.is_file() and not force:
            # Existing file in target — preserve user content. (Only
            # reachable when --force is off + a previous init left files.)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        with importlib.resources.as_file(src) as src_path:
            shutil.copy2(src_path, dst)
        written += 1
    return written


def _iter_bundle_files(bundle):
    """Yield every Traversable file under *bundle* recursively."""
    stack = [bundle]
    while stack:
        node = stack.pop()
        for child in node.iterdir():
            if child.is_file():
                yield child
            elif child.is_dir():
                stack.append(child)


def _bundle_relpath(bundle, src) -> str:
    """Return the path of *src* inside *bundle*, in POSIX form.

    importlib.resources Traversables don't expose a clean relative_to(),
    so we build the path by walking parent links until we hit the bundle
    root. Falls back to str() on platforms where `.parent` is unavailable.
    """
    parts: list[str] = []
    node = src
    bundle_name = bundle.name
    while node.name and node.name != bundle_name:
        parts.append(node.name)
        try:
            node = node.parent  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover  — defensive only
            break
    return "/".join(reversed(parts))


# ---------------------------------------------------------------------------
# Runtime scaffolding (created fresh, never copied from bundle)
# ---------------------------------------------------------------------------

def _create_runtime_state(target: Path) -> None:
    """Create the per-project `.signalos/` runtime dir + state files."""
    sig = target / ".signalos"
    (sig / "sessions").mkdir(parents=True, exist_ok=True)
    state = sig / "worktree-state.json"
    if not state.is_file():
        state.write_text(json.dumps({"worktrees": []}, indent=2),
                         encoding="utf-8")
    audit = sig / "AUDIT_TRAIL.jsonl"
    if not audit.is_file():
        audit.write_text("", encoding="utf-8")


def _render_plan_template(target: Path, project_name: str) -> None:
    """Render core/strategy/PLAN.md from a minimal skeleton if absent.

    The bundle does not ship a PLAN.md because it's per-project content
    the user owns. We seed a minimal version so `signalos status` and
    `signalos plan` work immediately.
    """
    plan = target / "core" / "strategy" / "PLAN.md"
    if plan.is_file():
        return
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        f"""# {project_name} — Wave Plan

<!-- SignalOS skeleton plan generated by `signalos init`. Customize freely. -->

## Wave 1 — Discovery

- [ ] T1 — Define the problem: what are we solving?
- [ ] T2 — Identify the user: who feels the pain today?
- [ ] T3 — Sketch the smallest end-to-end path (G0 ceremony)

## Gates

- G0  Pre-Wave Brief    : not signed
- G1  Discovery         : not signed
- G2  Plan              : not signed
- G3  Build             : not signed
- G4  QA                : not signed
- G5  Release           : not signed

## Next steps

1. Run `signalos status` to see this plan in card form.
2. Edit this file to replace the skeleton tasks with real ones.
3. When ready, run `signalos sign G0` to mark the brief signed.
""",
        encoding="utf-8",
    )


def _render_readme(target: Path, project_name: str, ide: str) -> None:
    readme = target / "README.md"
    if readme.is_file():
        return
    ide_hint = (
        f"Detected: **{ide}**. Open this directory there; "
        f"slash commands (`/signal-status`, `/signal-qa`, ...) "
        f"will appear after the next reload.\n"
        if ide
        else "No IDE was detected in the shell that ran `signalos init`. "
             "Open this directory in any of: claude-code, cursor, "
             "github-copilot, vs-code, windsurf, codex, antigravity. "
             "SignalOS auto-detects the active IDE per command "
             "invocation — no further config needed.\n"
    )
    readme.write_text(
        f"""# {project_name}

Bootstrapped with `signalos init`. This directory is a fully-wired
SignalOS project: 49 slash commands, 7 IDE surfaces, governance
scaffolding, and runtime state are all in place.

{ide_hint}
## Quick start

```
signalos status                # show the wave plan
signalos session start         # start a new session
/signal-status                 # same thing, in chat
```

## What's where

- `core/strategy/PLAN.md` — your wave plan. **Edit this first.**
- `core/execution/commands/` — slash command definitions (read-only).
- `integrations/` — per-IDE plugin/hook configurations.
- `.signalos/` — runtime state (sessions, audit trail). Don't commit.

## Documentation

Full docs: <https://github.com/samerzakaria/SignalOS>
""",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# git init + IDE register-hooks
# ---------------------------------------------------------------------------

def _git_init(target: Path) -> None:
    """Run `git init` in *target* — best-effort, never fatal."""
    if (target / ".git").is_dir():
        return
    try:
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=str(target), check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, FileNotFoundError):  # pragma: no cover — git missing
        sys.stderr.write(
            "  warn: `git` not found on PATH; skipping `git init`. "
            "Install git or pass --no-git to silence this warning.\n"
        )


def _register_ide_hooks(target: Path, ide: str) -> None:
    """Run the active IDE's register-hooks.sh, if any.

    No-op when no IDE was detected (headless install) — the bundle still
    contains all 8 emitters, so the user can re-run init or any signalos
    command from inside an IDE later and the right register-hooks.sh
    will fire on first invocation.
    """
    if not ide:
        return
    script = (target / "core" / "tool-adapters" / "emitters"
              / ide / "register-hooks.sh")
    if not script.is_file():
        return
    try:
        subprocess.run(
            ["bash", script.relative_to(target).as_posix()],
            cwd=str(target), check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, FileNotFoundError):  # pragma: no cover
        # bash missing — non-fatal; the user can rerun manually
        pass


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalos init",
        description="Bootstrap a new SignalOS project at <PATH>.",
    )
    parser.add_argument("path", metavar="PATH",
                        help="Target directory (will be created if absent)")
    parser.add_argument("--name", default=None, metavar="NAME",
                        help="Project name (default: directory basename)")
    parser.add_argument("--no-git", action="store_true",
                        help="Skip `git init` in the target directory")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Don't prompt — create the path if missing")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing non-empty target")
    parser.add_argument("--refresh-bundle", action="store_true",
                        help="Refresh the bundled protocol files (skills, "
                             "governance templates, etc.) in an existing "
                             "workspace. Implies --force for bundle files "
                             "but preserves user-authored data (.env, "
                             "PLAN.md.signed, AUDIT_TRAIL.jsonl, and any "
                             "file outside the bundle namespace).")
    parser.add_argument("--keep-existing", action="store_true",
                        help="Allow non-empty target but never overwrite "
                             "files that already exist. The bundle fills in "
                             "missing files only; user-authored content is "
                             "preserved untouched. Mutually exclusive with --force.")
    parser.add_argument("--minimal", action="store_true",
                        help="Skip the IDE-config bundle; ship only "
                             "the .signalos/ runtime + governance "
                             "templates (CLI-only mode)")
    return parser


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)
    target = Path(args.path).expanduser().resolve()
    project_name = args.name or target.name or "signalos-project"

    # 1. Path-existence handling
    if not target.exists():
        if not (args.yes or _confirm_create(target)):
            if not sys.stdin.isatty() and not args.yes:
                sys.stderr.write(
                    f"signalos init: '{target}' does not exist "
                    f"(use --yes to create non-interactively).\n"
                )
            else:
                sys.stderr.write("aborted: target not created.\n")
            return 1
        target.mkdir(parents=True, exist_ok=True)
    else:
        if not target.is_dir():
            sys.stderr.write(
                f"signalos init: '{target}' exists but is not a directory.\n"
            )
            return 1
        if args.force and args.keep_existing:
            sys.stderr.write(
                "signalos init: --force and --keep-existing are mutually exclusive.\n"
            )
            return 1
        if args.refresh_bundle and args.keep_existing:
            sys.stderr.write(
                "signalos init: --refresh-bundle and --keep-existing are mutually exclusive.\n"
            )
            return 1
        if not _is_target_empty(target) and not (args.force or args.keep_existing or args.refresh_bundle):
            sys.stderr.write(
                f"signalos init: target '{target}' is not empty "
                f"(use --keep-existing to merge without overwriting, "
                f"--force to overwrite, or pick a different path).\n"
            )
            return 1

    # 2. Copy the packaged bundle (unless --minimal)
    #    --refresh-bundle is treated as a more discoverable name for
    #    --force when the goal is updating shipped protocol files in an
    #    existing workspace. Either flag overwrites bundle files;
    #    _PROTECTED_FILES are never touched.
    file_count = 0
    if not args.minimal:
        try:
            overwrite = args.force or args.refresh_bundle
            file_count = _copy_bundle(target, force=overwrite)
        except RuntimeError as exc:
            sys.stderr.write(f"signalos init: {exc}\n")
            return 2

    # 3. Create runtime state + per-project templates
    _create_runtime_state(target)
    _render_plan_template(target, project_name)
    ide = detect_ide()
    _render_readme(target, project_name, ide)

    # 4. git init (unless suppressed)
    if not args.no_git:
        _git_init(target)

    # 5. IDE register-hooks (best-effort)
    if not args.minimal:
        _register_ide_hooks(target, ide)

    # 6. Print next-steps. Use plain ASCII so Windows cmd / cp1252
    # consoles don't UnicodeEncodeError on emoji or em-dashes.
    print(f"\n  [OK] SignalOS project bootstrapped at {target}")
    print(f"  [OK] {file_count} bundled files + .signalos/ runtime state")
    if ide:
        print(f"  [OK] Detected IDE: {ide} - slash commands "
              "ready on next reload")
    else:
        print("  [OK] No IDE detected - supports claude-code, cursor, "
              "github-copilot, vs-code, windsurf, codex, antigravity")
    print(f"\n  Next: cd {target}")
    print("        signalos session start    # or /signal-status in chat")
    print("\n  No API key needed for chat / IDE usage. ANTHROPIC_API_KEY")
    print("  is only required for autonomous-mode commands "
          "(`signalos harness call`,")
    print("  `signalos orchestrate`) when running headless / CI / cron.\n")
    return 0
