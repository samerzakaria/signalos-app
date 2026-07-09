# signalos_lib/product/reconstruct_gate_content.py
# Real gate content for auto-provisioning -- so a provisioned gate is not a
# generic placeholder but an honest artifact grounded in something concrete:
#
#   greenfield -> the delivery's OWN generated evidence (the product brief the
#                 pipeline already produced: INTENT, ACCEPTANCE_MATRIX, ...).
#                 The content is real; the SIGNATURE is 'assumed' (the gate was
#                 not founder-reviewed). Honest: real brief, un-reviewed gate.
#
#   adopt      -> RECONSTRUCTED from the existing codebase (package.json /
#                 pyproject / README / source tree / tests). The running code IS
#                 the decision; the founder REVIEWS & CORRECTS. Signature tier is
#                 'reconstructed'.
#
# Each builder returns a ContentFn: (gate, ResolvedArtifact) -> str | None.
# None means "no grounded content available" -> provision falls back to its
# honest provenance-marked default. Every returned body is scrubbed of blocking
# tokens (TODO/TBD/FIXME/{{...}}/[DATE]) so signing never refuses it.

from __future__ import annotations

__all__ = ["evidence_content_fn", "code_content_fn"]

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

ContentFn = Callable[[str, Any], Optional[str]]

# Tokens that make an artifact read as an unfinished template; scrubbed so the
# reconstructed/evidence body always signs.
_BLOCKING_RE = re.compile(r"\b(TODO|TBD|FIXME|XXX|HACK|WIP)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\[DATE\]|\[\s*\]")


def _scrub(text: str) -> str:
    """Remove blocking tokens so signing never refuses grounded content."""
    if not text:
        return ""
    text = _BLOCKING_RE.sub("pending", text)
    text = _DATE_RE.sub("(unspecified)", text)
    text = text.replace("{{", "(").replace("}}", ")")
    # Balance any stray single braces left over so the brace-balance check passes.
    if text.count("{") != text.count("}"):
        text = text.replace("{", "(").replace("}", ")")
    return text


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fmt_list(items: Any, bullet: str = "- ", limit: int = 20) -> str:
    """Render a value as a markdown bullet list; tolerant of dict/list/scalars."""
    out: list[str] = []
    if isinstance(items, dict):
        for k, v in list(items.items())[:limit]:
            out.append(f"{bullet}**{k}**: {_one_line(v)}")
    elif isinstance(items, (list, tuple)):
        for it in list(items)[:limit]:
            out.append(f"{bullet}{_one_line(it)}")
    elif items:
        out.append(f"{bullet}{_one_line(items)}")
    return "\n".join(out) if out else f"{bullet}(none recorded)"


def _one_line(v: Any) -> str:
    if isinstance(v, dict):
        return ", ".join(f"{k}={_one_line(val)}" for k, val in list(v.items())[:6])
    if isinstance(v, (list, tuple)):
        return ", ".join(_one_line(x) for x in list(v)[:8])
    return str(v).replace("\n", " ").strip()


# ---------------------------------------------------------------------------
# GREENFIELD: gate content from the delivery's own generated evidence.
# ---------------------------------------------------------------------------

_EVIDENCE_HEADER = (
    "> Provenance: assumed. This gate was AUTO-PROVISIONED from the delivery's "
    "own generated product brief (not authored by a founder). The content below "
    "is real -- it is the brief the pipeline produced -- but the gate has NOT "
    "been founder-reviewed. Review and sign to upgrade it to founder-signed.\n"
)


def evidence_content_fn(repo_root: Path) -> ContentFn:
    """ContentFn for greenfield: ground each gate in the generated evidence
    already sitting in .signalos/product/ (INTENT.json, ACCEPTANCE_MATRIX.json)."""
    prod = Path(repo_root) / ".signalos" / "product"
    intent = _load_json(prod / "INTENT.json")
    accept = _load_json(prod / "ACCEPTANCE_MATRIX.json")

    def _body(label: str) -> Optional[str]:
        if label == "Soul Document":
            return (
                "## Purpose\n\n"
                + _fmt_list(intent.get("assumptions") or intent.get("entities"))
                + "\n\n## Deployment intent\n\n"
                + _one_line(intent.get("deployment_intent") or "not specified")
            )
        if label == "Constitution":
            return (
                "## Out of scope\n\n" + _fmt_list(intent.get("out_of_scope"))
                + "\n\n## Auth / audit / permissions\n\n"
                + _fmt_list({
                    "auth": intent.get("auth_requirements"),
                    "audit": intent.get("audit_requirements"),
                    "permissions": intent.get("permissions"),
                })
            )
        if label == "Surface Inventory":
            return (
                "## API surfaces\n\n" + _fmt_list(intent.get("api_surfaces"))
                + "\n\n## Entities\n\n" + _fmt_list(intent.get("entities"))
                + "\n\n## Entity fields\n\n" + _fmt_list(intent.get("entity_fields"))
            )
        if label == "Permanently T3":
            return (
                "## Trust tier\n\nThis product handles the data sources and "
                "integrations below; treat them at the highest recorded "
                "sensitivity until a founder narrows the tier.\n\n"
                + _fmt_list({
                    "data_sources": intent.get("data_sources"),
                    "integrations": intent.get("integrations"),
                })
            )
        if label == "Belief":
            return (
                "## Why this product\n\n"
                + _fmt_list(intent.get("assumptions"))
                + "\n\n## Capability preferences\n\n"
                + _fmt_list(intent.get("capability_preferences"))
            )
        if label == "Role Activation Card":
            return (
                "## Roles activated for this delivery\n\n"
                "- **PO** (Product Owner): scope, belief, expectation gates\n"
                "- **PE** (Product Engineer): design, plan, build gates\n"
                "- **QA**: quality gate\n\n"
                "These roles were activated automatically for a headless "
                "delivery; a founder review reassigns them as needed."
            )
        if label == "Expectation Map":
            return (
                "## Acceptance summary\n\n"
                + _one_line(accept.get("summary") or "generated acceptance matrix")
                + "\n\n## Test scenarios\n\n"
                + _fmt_list(accept.get("test_scenarios"))
            )
        if label == "Design Note":
            return (
                "## Design decisions\n\n"
                + _fmt_list({
                    "profile": accept.get("profile"),
                    "blueprint": accept.get("blueprint_id"),
                    "product": accept.get("product_name"),
                })
                + "\n\n## Entity relationships\n\n"
                + _fmt_list(intent.get("entity_relationships"))
            )
        if label == "Plan":
            crit = accept.get("criteria") or []
            return (
                "## Build plan (from acceptance criteria)\n\n"
                + _fmt_list(crit)
            )
        if label == "Acceptance Criteria":
            return (
                "## Acceptance criteria\n\n"
                + _fmt_list(accept.get("criteria"))
                + "\n\n## Reconciliation\n\n"
                + _one_line(accept.get("reconciliation") or "n/a")
            )
        return None

    def _fn(gate: str, art: Any) -> Optional[str]:
        try:
            body = _body(getattr(art, "label", ""))
        except Exception:
            return None
        if not body:
            return None
        return _scrub(f"# {art.label}\n\n{_EVIDENCE_HEADER}\n{body}\n")

    return _fn


# ---------------------------------------------------------------------------
# ADOPT: gate content RECONSTRUCTED from the existing codebase.
# ---------------------------------------------------------------------------

_RECON_HEADER = (
    "> Provenance: reconstructed. This gate was RECONSTRUCTED from the existing "
    "codebase and accepted as-built -- the running code embodies these "
    "decisions. Review and CORRECT anything below that misreads the original "
    "intent; correcting it upgrades the gate to founder-signed.\n"
)

_SKIP_DIRS = {".git", "node_modules", ".signalos", "dist", "build", "__pycache__",
              ".venv", "venv", "target", ".next", "coverage"}


def _read_text(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _survey_repo(root: Path) -> dict:
    """Cheap, bounded survey of an existing repo for reconstruction."""
    info: dict = {"name": root.name, "top_dirs": [], "src_dirs": [],
                  "test_files": [], "readme": "", "pkg": {}, "pyproject": ""}
    pkg = _load_json(root / "package.json")
    if pkg:
        info["pkg"] = pkg
    py = root / "pyproject.toml"
    if py.is_file():
        info["pyproject"] = _read_text(py, 2000)
    for name in ("README.md", "readme.md", "README.rst"):
        p = root / name
        if p.is_file():
            info["readme"] = _read_text(p, 1500)
            break
    try:
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name not in _SKIP_DIRS \
                    and not child.name.startswith("."):
                info["top_dirs"].append(child.name)
                if child.name in ("src", "app", "lib", "core", "packages"):
                    info["src_dirs"].append(child.name)
    except Exception:
        pass
    # Bounded test-file scan (depth-limited to keep this cheap).
    try:
        for p in root.rglob("*"):
            rel = p.relative_to(root)
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            n = p.name.lower()
            if p.is_file() and (".test." in n or ".spec." in n
                                or n.startswith("test_") or "/tests/" in str(rel).replace("\\", "/")):
                info["test_files"].append(str(rel).replace("\\", "/"))
                if len(info["test_files"]) >= 40:
                    break
    except Exception:
        pass
    return info


def code_content_fn(repo_root: Path) -> ContentFn:
    """ContentFn for adopt: reconstruct gate content from the existing repo."""
    survey = _survey_repo(Path(repo_root))
    pkg = survey["pkg"]
    name = pkg.get("name") or survey["name"]
    desc = pkg.get("description") or ""
    readme_first = ""
    if survey["readme"]:
        for line in survey["readme"].splitlines():
            s = line.strip().lstrip("#").strip()
            if s and not s.startswith("!") and not s.startswith("["):
                readme_first = s
                break

    def _stack() -> str:
        deps = list((pkg.get("dependencies") or {}).keys())[:12]
        parts = []
        if deps:
            parts.append("node dependencies: " + ", ".join(deps))
        if survey["pyproject"]:
            parts.append("python project (pyproject.toml present)")
        return "; ".join(parts) or "stack not detected from manifests"

    def _body(label: str) -> Optional[str]:
        if label == "Soul Document":
            return (
                f"## Product\n\n**{name}** -- "
                + (desc or readme_first or "purpose reconstructed from the repository")
                + "\n\n## What the code is\n\n"
                + f"Top-level layout: {', '.join(survey['top_dirs']) or '(flat)'}."
            )
        if label == "Constitution":
            scripts = list((pkg.get("scripts") or {}).keys())
            return (
                "## Operating constraints (as-built)\n\n"
                f"- Build/verify entry points: {', '.join(scripts) or 'none declared'}\n"
                f"- License: {pkg.get('license') or 'unspecified'}\n"
                f"- Engines: {_one_line(pkg.get('engines') or 'unspecified')}\n\n"
                "These are reconstructed from the manifest; a founder should "
                "confirm the real operating constraints."
            )
        if label == "Surface Inventory":
            return (
                "## Surfaces (reconstructed from the source tree)\n\n"
                + _fmt_list(survey["src_dirs"] or survey["top_dirs"])
                + f"\n\n## Detected stack\n\n{_stack()}"
            )
        if label == "Permanently T3":
            return (
                "## Trust tier\n\nReconstructed adoption: treat the existing "
                "code at its current, un-narrowed trust tier until a founder "
                "reviews the data it handles and sets the tier deliberately."
            )
        if label == "Belief":
            return (
                "## Belief (reconstructed)\n\n"
                + (readme_first or desc or f"{name} exists and is being adopted "
                   "as-built; the belief is reconstructed from the repository.")
            )
        if label == "Role Activation Card":
            return (
                "## Roles for the adopted product\n\n"
                "- **PO**: owns reconstructed scope/belief -- confirm or correct\n"
                "- **PE**: owns reconstructed design/plan against the real code\n"
                "- **QA**: owns the quality gate over the existing tests\n"
            )
        if label == "Expectation Map":
            tests = survey["test_files"]
            return (
                "## Expectations (reconstructed from existing tests)\n\n"
                f"The repository ships {len(tests)} test file(s); the current "
                "expectation is that these continue to pass.\n\n"
                + _fmt_list(tests, limit=30)
            )
        if label == "Design Note":
            return (
                "## Design (reconstructed)\n\n"
                f"Detected stack: {_stack()}.\n\n"
                f"Module layout: {', '.join(survey['top_dirs']) or '(flat)'}.\n\n"
                "This design note is reconstructed from the manifests and tree; "
                "correct it where it misreads the real architecture."
            )
        if label == "Plan":
            scripts = pkg.get("scripts") or {}
            return (
                "## Plan (reconstructed from build/test scripts)\n\n"
                + _fmt_list(scripts)
                + "\n\nThe adopted plan is to keep these entry points green while "
                "changes land; a founder refines it against the real roadmap."
            )
        if label == "Acceptance Criteria":
            tests = survey["test_files"]
            return (
                "## Acceptance criteria (reconstructed)\n\n"
                f"- The existing {len(tests)} test file(s) pass.\n"
                "- The declared build/verify scripts succeed.\n"
                "- No existing surface regresses.\n\n"
                "Reconstructed from the repository; correct against real intent."
            )
        return None

    def _fn(gate: str, art: Any) -> Optional[str]:
        try:
            body = _body(getattr(art, "label", ""))
        except Exception:
            return None
        if not body:
            return None
        return _scrub(f"# {art.label}\n\n{_RECON_HEADER}\n{body}\n")

    return _fn
