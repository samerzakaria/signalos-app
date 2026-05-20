"""Per-gate agent loader (M-W3).

Per WAVE-ENGINE-DESIGN §4. Each gate has a markdown agent file under
`_bundle/core/execution/agents/`. The wave engine treats each file as
an LLM system prompt — load it, send the relevant context, interpret
the LLM's response.

This module owns the gate → agent-file mapping and the loading. Actual
LLM invocation lives in the harness layer; this is the seam between
"which agent am I dispatching" and "what bytes do I send to the LLM".

The G3 design agent is created in M-W4; until then `load_agent("G3")`
returns `{exists: False, ...}` and the engine can choose to fall back
to the G2 plan agent or surface a TODO bubble to the user.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any


__all__ = [
    "GATE_AGENT_FILES",
    "load_agent",
    "list_available_agents",
]


# Gate → agent file. The file lives under
#   signalos_lib/_bundle/core/execution/agents/<file>
# and ships with the package (importlib.resources).
GATE_AGENT_FILES: dict[str, str] = {
    "G0": "onboarding.md",
    "G1": "brainstorm.md",
    "G2": "plan.md",
    "G3": "design.md",       # created in M-W4
    "G4": "build.md",
    "G5": "observability.md",
}


def _bundle_agents_dir() -> Path:
    """Resolve the on-disk path to the bundled agents directory.

    Uses importlib.resources so the path works whether the package is
    installed from sdist / wheel / editable. Falls back to the source
    layout when resources can't resolve (e.g., during development).
    """
    try:
        ref = resources.files("signalos_lib").joinpath(
            "_bundle", "core", "execution", "agents",
        )
        return Path(str(ref))
    except (ModuleNotFoundError, AttributeError):
        # Source-tree fallback for dev runs that don't go through the
        # installed package machinery.
        here = Path(__file__).resolve().parent
        return here / "_bundle" / "core" / "execution" / "agents"


def load_agent(gate: str) -> dict[str, Any]:
    """Load the agent file for *gate* and return its contents + metadata.

    Returns:
        {
            "gate": "G0" | ... | "G5",
            "filename": "<file>.md" | None,  # None if gate unknown
            "path": "<absolute path>" | None,
            "exists": bool,
            "content": "<markdown body>" | "",
        }

    Never raises for "missing file" — returns exists=False with empty
    content so the engine can fall through to a TODO bubble. Raises
    KeyError only for unknown gate names (the engine should validate
    before calling).
    """
    if gate not in GATE_AGENT_FILES:
        raise KeyError(f"Unknown gate: {gate!r}. Expected one of {sorted(GATE_AGENT_FILES)}.")

    filename = GATE_AGENT_FILES[gate]
    agents_dir = _bundle_agents_dir()
    path = agents_dir / filename

    if not path.is_file():
        return {
            "gate": gate,
            "filename": filename,
            "path": str(path),
            "exists": False,
            "content": "",
        }

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "gate": gate,
            "filename": filename,
            "path": str(path),
            "exists": False,
            "content": "",
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "gate": gate,
        "filename": filename,
        "path": str(path),
        "exists": True,
        "content": content,
    }


def list_available_agents() -> dict[str, bool]:
    """Return {gate: agent-file-exists} for all G0..G5.

    Useful for the engine's pre-flight check — if G3 hasn't shipped yet
    (M-W4), the engine knows to handle that gate specially.
    """
    return {gate: load_agent(gate)["exists"] for gate in GATE_AGENT_FILES}
