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

# Track whether we've already warned about missing bash for emit.sh, so we
# don't spam stderr if the user has multiple emitters or runs init twice.
_BASH_WARNED = False

from signalos_lib.adoption import scan_existing_repo, write_adoption_artifacts
from signalos_lib.ide import detect_ide
from signalos_lib.profiles import (
    ProfileNotFoundError,
    dry_run_profile_validation,
    list_profile_ids,
    load_profile,
)

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
    # Trust-tier path allowlist for the governed agent loop (v4). Seeds
    # .signalos/trust-tier-paths.json with the default per-tier read/write/
    # execute allowlists + always-forbidden set. No-op if already present.
    from signalos_lib.product.enforcement_state import seed_trust_tier_paths
    seed_trust_tier_paths(target)


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


def _safe_profile_path(root: Path, rel_path: str) -> Path:
    candidate = (root / rel_path).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise RuntimeError(f"profile path escapes workspace: {rel_path}") from exc
    return candidate


def _copy_profile_template(root: Path, source: str, destination: str, overwrite: bool) -> bool:
    src = _safe_profile_path(root, source)
    dst = _safe_profile_path(root, destination)
    if not src.exists() or not src.is_file():
        raise RuntimeError(f"profile template source missing: {source}")
    if dst.exists() and not overwrite:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve(strict=False) != dst.resolve(strict=False):
        shutil.copy2(src, dst)
    return True


def _apply_profile(
    target: Path,
    profile_id: str,
    project_name: str,
    overwrite: bool,
    emit_templates: bool = True,
) -> tuple[int, dict]:
    try:
        profile = load_profile(profile_id)
    except ProfileNotFoundError as exc:
        available = ", ".join(list_profile_ids())
        raise RuntimeError(f"{exc}; available profiles: {available}") from exc

    generated = 0
    if emit_templates:
        for template in profile.required_templates:
            generated += int(_copy_profile_template(target, template.source, template.destination, overwrite))
        if profile.ci.enabled:
            for template in profile.ci.templates:
                generated += int(_copy_profile_template(target, template.source, template.destination, overwrite))

    metadata = {
        "schema_version": "signalos.profile_selection.v1",
        "profile_id": profile.id,
        "profile_name": profile.name,
        "project_name": project_name,
        "preview": profile.preview.to_dict(),
        "commands": {
            name: command.to_dict() if command else None
            for name, command in profile.commands.items()
        },
        "validator_groups": list(profile.validator_groups),
        "generated_templates": generated,
    }
    profile_path = target / ".signalos" / "profile.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    # Init writes the selected profile and emits profile-owned templates, but
    # frontend governance instantiation may still fill placeholders/sign G0
    # immediately after this command. Generated-file validation is enforced by
    # `signalos validate --group layer1` once that fill step has happened.
    report = dry_run_profile_validation(profile)
    validation_path = target / ".signalos" / "profile-validation.json"
    validation_path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    if not report.ok:
        raise RuntimeError(
            "profile validation failed: "
            + "; ".join(issue.message for issue in report.issues[:5])
        )
    return generated, metadata


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
            timeout=15,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, FileNotFoundError):  # pragma: no cover — git missing
        sys.stderr.write(
            "  warn: `git` not found on PATH; skipping `git init`. "
            "Install git or pass --no-git to silence this warning.\n"
        )
    except subprocess.TimeoutExpired:  # pragma: no cover
        sys.stderr.write(
            "  warn: `git init` timed out; skipping repository initialization. "
            "Run `git init` manually or rerun `signalos init --no-git`.\n"
        )


def _bash_candidates() -> list[str]:
    """Return candidate bash executables in preferred order."""
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str | None) -> None:
        if not candidate:
            return
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            return
        seen.add(normalized)
        candidates.append(candidate)

    add(shutil.which("bash"))
    if os.name == "nt":
        for root in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            r"C:\Program Files",
            r"C:\Program Files (x86)",
        ):
            if not root:
                continue
            add(str(Path(root) / "Git" / "bin" / "bash.exe"))
            add(str(Path(root) / "Git" / "usr" / "bin" / "bash.exe"))
    return candidates


def _resolve_bash() -> str | None:
    """Return a working bash executable, or None if none can run scripts."""
    for candidate in _bash_candidates():
        if not Path(candidate).is_file() and shutil.which(candidate) is None:
            continue
        try:
            proc = subprocess.run(
                [candidate, "-c", "echo ok"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0 and "ok" in (proc.stdout or ""):
            return candidate
    return None


def _bash_available() -> bool:
    """Return True if bash resolves to a working shell on this machine.

    Mirrors the helper in orchestrator.py — on Windows this typically
    resolves to Git Bash (C:/Program Files/Git/bin/bash.exe). When bash
    is unavailable we silently skip shell-out steps in init rather than
    failing, so headless / Windows-without-Git-Bash installs still work.
    """
    return _resolve_bash() is not None


def _register_ide_hooks(target: Path, ide: str) -> None:
    """Run the active IDE's register-hooks.sh AND emit.sh, if any.

    Two shell-outs in order:
      1. `register-hooks.sh` — wires session-hook-dispatch into the IDE's
         hook config (e.g. .claude/settings.json).
      2. `emit.sh` — reads the canonical _shared/{commands,skills,hooks}.json
         registries and the rendered session-preamble.md, then writes
         IDE-native config files under the supplied --output-dir
         (conventionally `.signalos/<ide>/`).

    Both are best-effort: a missing script for a given IDE is silently
    skipped, and a missing/broken bash short-circuits the entire step
    with a one-time stderr warning (matches the orchestrator's
    `_bash_available` lenient policy).

    No-op when no IDE was detected (headless install) — the bundle still
    contains all 8 emitters, so the user can re-run init or any signalos
    command from inside an IDE later and the right register-hooks.sh +
    emit.sh will fire on first invocation.
    """
    global _BASH_WARNED
    if not ide:
        return

    register_script = (target / "core" / "tool-adapters" / "emitters"
                       / ide / "register-hooks.sh")
    emit_script = (target / "core" / "tool-adapters" / "emitters"
                   / ide / "emit.sh")

    # If neither script exists for this IDE, nothing to do.
    if not register_script.is_file() and not emit_script.is_file():
        return

    bash_cmd = _resolve_bash()
    if bash_cmd is None:
        if not _BASH_WARNED:
            sys.stderr.write(
                "  warn: `bash` not available; skipping IDE hook "
                "registration + emit.sh. Install Git Bash (Windows) or "
                "bash to populate `.signalos/" + ide + "/` automatically. "
                "You can rerun `signalos init` once bash is on PATH.\n"
            )
            _BASH_WARNED = True
        return

    # 1) register-hooks.sh
    if register_script.is_file():
        try:
            subprocess.run(
                [bash_cmd, register_script.relative_to(target).as_posix()],
                cwd=str(target), check=False,
                timeout=15,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, FileNotFoundError, subprocess.TimeoutExpired):  # pragma: no cover
            # bash disappeared between the check and the call — non-fatal
            pass

    # 2) emit.sh — write IDE-native config files at the workspace root.
    # IMPORTANT: --output-dir is the workspace root ("."), not a subdir under
    # `.signalos/`. Every emitter creates its own IDE-native subdirectory
    # inside output_dir (claude-code → `.claude/`, cursor → `.cursor/`,
    # windsurf → `.windsurfrules`, harness → `.signalos/harness/`, etc.) so
    # IDE auto-discovery finds them at the conventional locations. Pointing
    # this at `.signalos/<ide>` would produce e.g. `.signalos/claude-code/.claude/commands/`
    # which Claude Code does not scan.
    if emit_script.is_file():
        try:
            proc = subprocess.run(
                [
                    bash_cmd,
                    emit_script.relative_to(target).as_posix(),
                    "--commands-json", "core/tool-adapters/_shared/commands.json",
                    "--skills-json",   "core/tool-adapters/_shared/skills.json",
                    "--hooks-json",    "core/tool-adapters/_shared/hooks.json",
                    "--preamble",      "core/tool-adapters/_shared/session-preamble.md",
                    "--output-dir",    ".",
                ],
                cwd=str(target),
                check=False,
                capture_output=True,
                timeout=15,
            )
            if proc.returncode != 0:
                _emit_ide_fallback(target, ide)
        except (OSError, FileNotFoundError, subprocess.TimeoutExpired):  # pragma: no cover
            # Non-fatal — the user can rerun manually
            pass

# ---------------------------------------------------------------------------
# Python fallback emitters
# ---------------------------------------------------------------------------

def _emit_ide_fallback(target: Path, ide: str) -> bool:
    """Emit minimal IDE-native files without external jq/python binaries."""
    if ide != "claude-code":
        return False

    shared = target / "core" / "tool-adapters" / "_shared"
    commands_json = shared / "commands.json"
    preamble = shared / "session-preamble.md"
    if not commands_json.is_file() or not preamble.is_file():
        return False

    try:
        commands = json.loads(commands_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(commands, list):
        return False

    commands_dir = target / ".claude" / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(preamble, target / "CLAUDE.md")

    for item in commands:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        desc = str(item.get("description") or "")
        source = str(item.get("source") or "")
        output_file = commands_dir / f"{name}.md"
        parts = ["---", f"description: {desc}", "---", ""]
        source_path = target / source
        if source and source_path.is_file():
            try:
                parts.append(source_path.read_text(encoding="utf-8"))
            except OSError:
                pass
        output_file.write_text("\n".join(parts), encoding="utf-8")
    return True


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
    parser.add_argument("--profile", default="generic", choices=list_profile_ids(),
                        help="Factory stack/profile to embed in .signalos/profile.json")
    return parser


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)
    target = Path(args.path).expanduser().resolve()
    project_name = args.name or target.name or "signalos-project"
    profile_id = args.profile or "generic"
    should_adopt_existing = (
        target.exists()
        and target.is_dir()
        and args.keep_existing
        and not _is_target_empty(target)
    )
    adoption_report = (
        scan_existing_repo(target, project_name)
        if should_adopt_existing
        else None
    )

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
    if adoption_report is not None:
        write_adoption_artifacts(target, adoption_report)
    _render_plan_template(target, project_name)
    ide = detect_ide()
    _render_readme(target, project_name, ide)
    try:
        generated_profile_templates, profile_metadata = _apply_profile(
            target,
            profile_id,
            project_name,
            overwrite=args.force or args.refresh_bundle,
            emit_templates=not args.minimal,
        )
    except RuntimeError as exc:
        sys.stderr.write(f"signalos init: {exc}\n")
        return 2

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
    print(f"  [OK] Profile: {profile_metadata['profile_id']} "
          f"({generated_profile_templates} profile template file(s) generated)")
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
