"""SignalOS retrospective lifecycle hook runners."""
from __future__ import annotations

__all__ = [
    "POST_RETRO_HOOK_RELATIVE",
    "PostRetroHookError",
    "run_post_retro_hook",
]

import shutil
import subprocess
from pathlib import Path


POST_RETRO_HOOK_RELATIVE = "core/execution/hooks/post-retro"


class PostRetroHookError(RuntimeError):
    """Raised when the post-retro closure hook blocks a wave transition."""


def run_post_retro_hook(repo_root: Path, wave: str, *, timeout_s: int = 60) -> None:
    """Run the installed post-retro hook for a completed wave.

    The hook enforces the Phase-8 closure rule that every retro must produce a
    Constitution delta, either as an amendment or as a signed no-change record.
    Missing, failing, or non-runnable hooks block closure instead of being
    silently skipped.
    """
    repo_root = Path(repo_root)
    wave = (wave or "").strip()
    if not wave:
        raise PostRetroHookError("wave is required")

    hook = repo_root / POST_RETRO_HOOK_RELATIVE
    if not hook.is_file():
        raise PostRetroHookError(f"post-retro hook is not installed at {POST_RETRO_HOOK_RELATIVE}")

    bash = shutil.which("bash")
    if not bash:
        raise PostRetroHookError("post-retro hook requires bash, but bash was not found")

    proc = subprocess.run(
        [bash, str(hook), wave],
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    if proc.returncode != 0:
        output = "\n".join(
            part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip()
        )
        detail = output or f"post-retro exited with {proc.returncode}"
        raise PostRetroHookError(detail)
