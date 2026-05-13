# SignalOS Core — W7 Sprint QA.
# cli/signalos_lib/regression.py
#
# Regression auto-generation — when a bug fix PR is merged, this module
# auto-generates a minimal browser regression scenario YAML and appends
# it to core/governance/QA/regressions/. The scenario is written from
# a structured bug description so the QA runner (qa_runner.py) can
# replay the failure path on every subsequent run.
#
# Entry points:
#
#   generate_regression(bug: BugDescription) -> Path
#       Generates a YAML scenario file. Returns the path written.
#
#   generate_regression_from_dict(data: dict) -> Path
#       Convenience wrapper for CLI / hook callers passing raw JSON.
#
# CLI (via signalos commands/qa.py):
#
#   signalos qa regression --generate \
#       --bug-id   BUG-042 \
#       --name     "Dashboard throws 500 on empty dataset" \
#       --url      "https://staging.example.com/dashboard" \
#       --steps    '[{"action":"navigate","url":"..."},{"action":"wait_for","selector":"#chart"}]' \
#       --assertions '[{"type":"console_errors"},{"type":"element_visible","selector":"#chart"}]'
#
#   signalos qa regression --run
#       Delegates to qa_runner.run_scenario_suite() with the regressions glob.
#       Alias for the regression portion of /signal-qa-only.
#
# Hook integration (post-merge):
#   core/execution/hooks/post-merge calls this module automatically when
#   a PR description contains a "Bug-Fix: BUG-NNN" trailer and a
#   "Regression-URL:" trailer. See hooks/post-merge for details.

from __future__ import annotations

__all__ = [
    "BugDescription",
    "generate_regression",
    "generate_regression_from_dict",
    "REGRESSIONS_DIR",
]

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REGRESSIONS_DIR = Path("core/governance/QA/regressions")

# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class BugDescription:
    bug_id: str                             # e.g. "BUG-042"
    name: str                               # human-readable regression name
    url: str                                # URL that reproduces the bug
    steps: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    capture_vitals: bool = False
    pr_ref: str = ""                        # PR number or URL that fixed the bug
    fixed_at: str = ""                      # ISO-8601 date of fix merge

    def __post_init__(self) -> None:
        if not self.bug_id:
            raise ValueError("BugDescription.bug_id is required")
        if not self.name:
            raise ValueError("BugDescription.name is required")
        if not self.url:
            raise ValueError("BugDescription.url is required")

        # Default assertions: at minimum assert zero console errors
        if not self.assertions:
            self.assertions = [{"type": "console_errors"}]

        # Default fixed_at to now if not provided
        if not self.fixed_at:
            self.fixed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def _sanitize_id(bug_id: str) -> str:
    """Normalise bug_id to a safe filename component."""
    return re.sub(r"[^a-z0-9-]", "-", bug_id.lower()).strip("-")


def _next_regression_id(regressions_dir: Path) -> str:
    """
    Return the next sequential regression scenario ID in the form
    ``reg-NNN``, based on existing files in *regressions_dir*.
    """
    existing = sorted(regressions_dir.glob("reg-*.yaml"))
    if not existing:
        return "reg-001"
    last = existing[-1].stem  # e.g. "reg-042-bug-123"
    match = re.match(r"reg-(\d+)", last)
    n = int(match.group(1)) + 1 if match else 1
    return f"reg-{n:03d}"


# ---------------------------------------------------------------------------
# YAML serialisation (stdlib — no PyYAML dependency for generation)
# ---------------------------------------------------------------------------

def _steps_to_yaml(steps: list[dict[str, Any]], indent: int = 4) -> str:
    """Minimal inline YAML list renderer for step/assertion dicts."""
    lines: list[str] = []
    pad = " " * indent
    for step in steps:
        items = list(step.items())
        first_key, first_val = items[0]
        lines.append(f"{pad}- {first_key}: {_yaml_scalar(first_val)}")
        for key, val in items[1:]:
            lines.append(f"{pad}  {key}: {_yaml_scalar(val)}")
    return "\n".join(lines)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    # Quote strings containing special YAML characters
    if any(c in s for c in (':', '#', '{', '}', '[', ']', ',', '&', '*', '?',
                              '|', '-', '<', '>', '=', '!', '%', '@', '`')):
        return f'"{s}"'
    return s


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_regression(
    bug: BugDescription,
    regressions_dir: Path | None = None,
) -> Path:
    """
    Generate a YAML regression scenario file for *bug* and write it
    to *regressions_dir* (default: ``core/governance/QA/regressions/``).

    Returns the path of the written file.
    """
    out_dir = regressions_dir or REGRESSIONS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    reg_id = _next_regression_id(out_dir)
    slug = _sanitize_id(bug.bug_id)
    filename = f"{reg_id}-{slug}.yaml"
    out_path = out_dir / filename

    # Build YAML content (hand-crafted string — keeps stdlib-only for generation)
    steps_yaml = _steps_to_yaml(bug.steps) if bug.steps else "    # no steps — navigate only"
    assertions_yaml = _steps_to_yaml(bug.assertions)

    content = f"""\
# SignalOS regression scenario — auto-generated by signalos_lib.regression
# Bug: {bug.bug_id} — {bug.name}
# Fixed: {bug.fixed_at}{"  PR: " + bug.pr_ref if bug.pr_ref else ""}
# DO NOT EDIT the id, bug_ref, or auto_generated fields.
# Add or refine steps/assertions as the product evolves.

id: {reg_id}
name: "[regression] {bug.name}"
bug_ref: {bug.bug_id}
auto_generated: true
generated_at: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
pr_ref: {bug.pr_ref or ""}

url: "{bug.url}"

steps:
{steps_yaml}

assertions:
{assertions_yaml}

evidence:
  screenshot: true
  vitals: {str(bug.capture_vitals).lower()}
"""

    out_path.write_text(content, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Dict convenience wrapper
# ---------------------------------------------------------------------------

def generate_regression_from_dict(data: dict[str, Any]) -> Path:
    """
    Convenience wrapper: build a BugDescription from a raw dict and
    call generate_regression(). Useful for CLI and hook callers.

    Expected keys: bug_id, name, url, steps, assertions,
                   capture_vitals, pr_ref, fixed_at (all optional
                   except bug_id, name, url).
    """
    bug = BugDescription(
        bug_id=data["bug_id"],
        name=data["name"],
        url=data["url"],
        steps=data.get("steps", []),
        assertions=data.get("assertions", []),
        capture_vitals=data.get("capture_vitals", False),
        pr_ref=data.get("pr_ref", ""),
        fixed_at=data.get("fixed_at", ""),
    )
    return generate_regression(bug)
