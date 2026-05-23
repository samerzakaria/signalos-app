"""Existing-repo adoption scanner used by ``signalos init --keep-existing``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".signalos",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "coverage",
}


def scan_existing_repo(root: Path, project_name: str) -> dict[str, Any]:
    """Return deterministic adoption metadata for files that already exist.

    The scanner is deliberately conservative. It records observable surfaces
    and open questions, but does not mutate user files or infer product intent
    beyond what local repo markers support.
    """

    root = root.resolve()
    files = _collect_files(root)
    package = _read_package_json(root / "package.json")
    detected_profile = _detect_profile(package)
    surfaces = _detect_surfaces(root, files, package)
    unknowns = _build_unknowns(root, files, package, detected_profile, surfaces)
    source_intent = {
        "schema_version": SCHEMA_VERSION,
        "source_type": "existing_repo_adoption",
        "project_name": project_name,
        "repo_root_name": root.name,
        "detected_profile": detected_profile,
        "evidence": [surface["path"] for surface in surfaces[:10]],
    }
    surface_inventory = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "signalos init --keep-existing",
        "project_name": project_name,
        "repo_root_name": root.name,
        "detected_profile": detected_profile,
        "surfaces": surfaces,
        "files": {
            "total_scanned": len(files),
            "sample": [path.as_posix() for path in files[:50]],
            "counts_by_extension": _counts_by_extension(files),
        },
    }
    if package is not None:
        surface_inventory["package"] = {
            "scripts": package.get("scripts", {}),
            "dependencies": sorted(_package_names(package)),
        }
    return {
        "surface_inventory": surface_inventory,
        "unknowns": {
            "schema_version": SCHEMA_VERSION,
            "generated_by": "signalos init --keep-existing",
            "project_name": project_name,
            "items": unknowns,
        },
        "source_intent": source_intent,
        "onboarding_draft": _render_onboarding_draft(project_name, detected_profile, surfaces, unknowns),
        "next_steps": _render_next_steps(project_name, detected_profile, unknowns),
    }


def write_adoption_artifacts(root: Path, report: dict[str, Any]) -> None:
    """Write adoption outputs under ``.signalos/``.

    Only SignalOS-owned artifact paths are written. Existing source files are
    never touched by this function.
    """

    sig = root / ".signalos"
    adoption = sig / "adoption"
    sources = sig / "sources"
    adoption.mkdir(parents=True, exist_ok=True)
    sources.mkdir(parents=True, exist_ok=True)
    _write_json(adoption / "surface-inventory.json", report["surface_inventory"])
    _write_json(adoption / "unknowns.json", report["unknowns"])
    _write_json(sources / "initial-intent.json", report["source_intent"])
    (adoption / "onboarding-draft.md").write_text(report["onboarding_draft"], encoding="utf-8")
    (adoption / "next-steps.md").write_text(report["next_steps"], encoding="utf-8")


def _collect_files(root: Path, limit: int = 1000) -> list[Path]:
    files: list[Path] = []
    for current_raw, dirnames, filenames in os.walk(root):
        current = Path(current_raw)
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
        rel_dir = current.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel_dir.parts):
            continue
        for name in sorted(filenames):
            path = current / name
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            files.append(rel)
            if len(files) >= limit:
                return files
    return files


def _read_package_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _detect_profile(package: dict[str, Any] | None) -> str:
    if package is not None:
        names = _package_names(package)
        scripts = package.get("scripts", {})
        script_text = " ".join(
            str(value)
            for value in scripts.values()
        ) if isinstance(scripts, dict) else ""
        if "vite" in names or "vite" in script_text:
            return "react-vite"
    return "generic"


def _detect_surfaces(root: Path, files: list[Path], package: dict[str, Any] | None) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    file_set = {path.as_posix() for path in files}
    if package is not None:
        scripts = package.get("scripts", {})
        surfaces.append({
            "type": "package",
            "path": "package.json",
            "evidence": sorted(scripts.keys()) if isinstance(scripts, dict) else [],
        })
        names = _package_names(package)
        if "vite" in names or "react" in names or "next" in names:
            surfaces.append({
                "type": "frontend",
                "path": "package.json",
                "evidence": sorted(names.intersection({"vite", "react", "next"})),
            })
    for rel in ("src", "app", "pages", "public"):
        if (root / rel).is_dir():
            surfaces.append({"type": "source-tree", "path": rel, "evidence": _sample_under(files, rel)})
    for rel in ("pyproject.toml", "requirements.txt"):
        if rel in file_set:
            surfaces.append({"type": "python", "path": rel, "evidence": [rel]})
    if "Cargo.toml" in file_set:
        surfaces.append({"type": "rust", "path": "Cargo.toml", "evidence": ["Cargo.toml"]})
    if (root / "src-tauri").is_dir():
        surfaces.append({"type": "tauri", "path": "src-tauri", "evidence": _sample_under(files, "src-tauri")})
    workflow_evidence = [
        path.as_posix()
        for path in files
        if path.as_posix().startswith(".github/workflows/")
        and path.suffix.lower() in {".yml", ".yaml"}
    ]
    if workflow_evidence:
        surfaces.append({"type": "ci", "path": ".github/workflows", "evidence": workflow_evidence[:10]})
    test_evidence = [
        path.as_posix()
        for path in files
        if _looks_like_test_path(path)
    ]
    if test_evidence:
        surfaces.append({"type": "tests", "path": "tests", "evidence": test_evidence[:20]})
    deployment = [
        rel for rel in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml")
        if rel in file_set
    ]
    if deployment:
        surfaces.append({"type": "deployment", "path": ".", "evidence": deployment})
    docs = [
        path.as_posix()
        for path in files
        if path.parts and path.parts[0].lower() in {"docs", "doc"} and path.suffix.lower() in {".md", ".txt"}
    ]
    if docs:
        surfaces.append({"type": "documentation", "path": "docs", "evidence": docs[:20]})
    return surfaces


def _build_unknowns(
    root: Path,
    files: list[Path],
    package: dict[str, Any] | None,
    detected_profile: str,
    surfaces: list[dict[str, Any]],
) -> list[dict[str, str]]:
    unknowns = [
        {
            "id": "adoption-product-intent",
            "question": "Confirm the product purpose, target users, and success criteria for this adopted repo.",
            "reason": "Source code structure does not prove product intent.",
            "status": "open",
        },
        {
            "id": "adoption-governance-owner",
            "question": "Name the human signer responsible for G0 adoption approval.",
            "reason": "Gate signatures require an explicit accountable signer.",
            "status": "open",
        },
    ]
    if detected_profile not in {"generic", "react-vite"}:
        unknowns.append({
            "id": "adoption-profile-confirmation",
            "question": f"Confirm or map the detected profile '{detected_profile}' to a supported SignalOS profile.",
            "reason": "Profile support is still being expanded beyond generic and react-vite.",
            "status": "open",
        })
    if package is None and detected_profile == "generic":
        unknowns.append({
            "id": "adoption-build-command",
            "question": "Identify the build, test, and preview commands for this repo, if any.",
            "reason": "No package manifest or known build profile was detected.",
            "status": "open",
        })
    elif package is not None:
        scripts = package.get("scripts", {})
        if not isinstance(scripts, dict) or "test" not in scripts:
            unknowns.append({
                "id": "adoption-test-command",
                "question": "Confirm the product test command or mark testing as intentionally manual for now.",
                "reason": "package.json does not expose a test script.",
                "status": "open",
            })
    if not any(surface["type"] == "ci" for surface in surfaces):
        unknowns.append({
            "id": "adoption-ci-policy",
            "question": "Confirm the CI gate policy for this product repo.",
            "reason": "No existing GitHub Actions workflow was detected.",
            "status": "open",
        })
    if not any(_looks_like_test_path(path) for path in files):
        unknowns.append({
            "id": "adoption-test-surface",
            "question": "Identify where automated or manual test evidence should live.",
            "reason": "No obvious test files or test directories were detected.",
            "status": "open",
        })
    return unknowns


def _render_onboarding_draft(
    project_name: str,
    detected_profile: str,
    surfaces: list[dict[str, Any]],
    unknowns: list[dict[str, str]],
) -> str:
    lines = [
        f"# {project_name} Adoption Draft",
        "",
        f"- Detected profile: `{detected_profile}`",
        f"- Detected surfaces: {len(surfaces)}",
        f"- Open unknowns: {len(unknowns)}",
        "",
        "## Surfaces",
        "",
    ]
    if surfaces:
        lines.extend(
            f"- `{surface['type']}` at `{surface['path']}`"
            for surface in surfaces
        )
    else:
        lines.append("- No product surfaces were detected automatically.")
    lines.extend([
        "",
        "## Adoption Notes",
        "",
        "This draft is generated from local repo markers only. Confirm product intent, owners, commands, and release policy before signing G0.",
        "",
    ])
    return "\n".join(lines)


def _render_next_steps(project_name: str, detected_profile: str, unknowns: list[dict[str, str]]) -> str:
    lines = [
        f"# {project_name} Adoption Next Steps",
        "",
        "1. Review `.signalos/adoption/surface-inventory.json`.",
        "2. Resolve or explicitly defer `.signalos/adoption/unknowns.json`.",
        f"3. Confirm the selected profile: `{detected_profile}`.",
        "4. Fill governance artifacts and sign G0 when the adoption brief is accepted.",
    ]
    if unknowns:
        lines.extend(["", "## Open Questions", ""])
        lines.extend(f"- {item['question']}" for item in unknowns)
    return "\n".join(lines) + "\n"


def _package_names(package: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = package.get(key)
        if isinstance(value, dict):
            names.update(str(name) for name in value.keys())
    return names


def _sample_under(files: list[Path], prefix: str) -> list[str]:
    root = Path(prefix)
    return [
        path.as_posix()
        for path in files
        if path == root or (path.parts and path.parts[0] == prefix)
    ][:10]


def _looks_like_test_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    return (
        "test" in parts
        or "tests" in parts
        or "__tests__" in parts
        or name.startswith("test_")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
        or name.endswith("_test.py")
    )


def _counts_by_extension(files: list[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in files:
        ext = path.suffix.lower() or "<none>"
        counts[ext] = counts.get(ext, 0) + 1
    return dict(sorted(counts.items()))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
