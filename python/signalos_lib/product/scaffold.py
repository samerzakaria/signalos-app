"""Scaffold orchestrator for SignalOS product delivery.

Composes existing modules (lifecycle, intent, blueprints, stacks) to
run the full scaffold phase: detect mode, init repo, extract intent,
match blueprint, run adapter scaffold, validate results.
"""

from __future__ import annotations

__all__ = [
    "explain_profile_selection",
    "run_postflight",
    "run_scaffold",
]

from pathlib import Path
from typing import Any

from . import stacks
from .blueprints import registry as blueprint_registry
from . import intent as intent_mod
from . import lifecycle


# ---------------------------------------------------------------------------
# Profile explanation
# ---------------------------------------------------------------------------

_PROFILE_EXPLANATIONS: dict[str, str] = {
    "react-vite": (
        "React + Vite detected: the repository contains a Vite dependency "
        "in package.json, indicating a modern React single-page application."
    ),
    "existing-repo": (
        "Existing repository detected: the directory contains recognised "
        "project markers but does not match a more specific profile."
    ),
    "generic": (
        "Generic profile selected: no recognised project markers were found. "
        "Governance files will be created but no runnable application scaffold."
    ),
}


def explain_profile_selection(profile: str, detected: bool) -> str:
    """Return a human-readable explanation of why this profile was selected.

    Used when ``--profile auto`` is specified.
    """
    if not detected:
        return f"Profile '{profile}' was explicitly specified by the user."
    return _PROFILE_EXPLANATIONS.get(
        profile,
        f"Profile '{profile}' was auto-detected from repository contents.",
    )


# ---------------------------------------------------------------------------
# Postflight validation
# ---------------------------------------------------------------------------

def run_postflight(repo_root: Path, profile: str) -> dict[str, Any]:
    """Validate scaffold completeness for the given profile.

    For react-vite: check package.json exists, has name field, has
    dependencies, src/ exists, at least one .tsx file exists.
    For generic: check .signalos/ exists (minimal).
    For existing-repo: check original files preserved.

    Returns ``{"passed": bool, "checks": [...]}``.
    """
    checks: list[dict[str, Any]] = []

    if profile == "react-vite":
        checks.extend(_postflight_react_vite(repo_root))
    elif profile == "existing-repo":
        checks.extend(_postflight_existing_repo(repo_root))
    else:
        # generic / unknown
        checks.extend(_postflight_generic(repo_root))

    passed = all(c["passed"] for c in checks)
    return {"passed": passed, "checks": checks}


def _postflight_react_vite(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    # 1. package.json exists
    pkg_path = repo_root / "package.json"
    pkg_exists = pkg_path.is_file()
    checks.append({
        "name": "package.json exists",
        "passed": pkg_exists,
        "detail": str(pkg_path) if pkg_exists else "package.json not found",
    })

    # 2. package.json has name field
    has_name = False
    has_deps = False
    if pkg_exists:
        try:
            import json
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            has_name = bool(pkg.get("name"))
            has_deps = bool(pkg.get("dependencies"))
        except Exception:
            pass

    checks.append({
        "name": "package.json has name",
        "passed": has_name,
        "detail": "name field present" if has_name else "name field missing",
    })

    # 3. package.json has dependencies
    checks.append({
        "name": "package.json has dependencies",
        "passed": has_deps,
        "detail": "dependencies present" if has_deps else "dependencies missing",
    })

    # 4. src/ exists
    src_exists = (repo_root / "src").is_dir()
    checks.append({
        "name": "src/ directory exists",
        "passed": src_exists,
        "detail": "src/ found" if src_exists else "src/ not found",
    })

    # 5. at least one .tsx file
    tsx_files = list((repo_root / "src").glob("*.tsx")) if src_exists else []
    has_tsx = len(tsx_files) > 0
    checks.append({
        "name": ".tsx file exists in src/",
        "passed": has_tsx,
        "detail": f"{len(tsx_files)} .tsx file(s) found" if has_tsx else "no .tsx files found",
    })

    return checks


def _postflight_existing_repo(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    signalos_exists = (repo_root / ".signalos").is_dir()
    checks.append({
        "name": ".signalos/ directory exists",
        "passed": signalos_exists,
        "detail": ".signalos/ found" if signalos_exists else ".signalos/ not found",
    })

    # Check that non-dot files still exist (preserved)
    non_dot = [c for c in repo_root.iterdir() if not c.name.startswith(".")]
    has_files = len(non_dot) > 0
    checks.append({
        "name": "original files preserved",
        "passed": has_files,
        "detail": f"{len(non_dot)} original item(s) present" if has_files else "no original files found",
    })

    return checks


def _postflight_generic(repo_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    signalos_exists = (repo_root / ".signalos").is_dir()
    checks.append({
        "name": ".signalos/ directory exists",
        "passed": signalos_exists,
        "detail": ".signalos/ found" if signalos_exists else ".signalos/ not found",
    })

    return checks


# ---------------------------------------------------------------------------
# Main scaffold orchestrator
# ---------------------------------------------------------------------------

def run_scaffold(
    repo_root: Path,
    profile: str,
    product_name: str,
    prompt: str,
    blueprint_id: str | None = None,
    mode: str = "auto",
) -> dict[str, Any]:
    """Run the full scaffold phase.

    Steps:
    1. Detect or use provided mode (delegate to lifecycle.detect_mode)
    2. Init product repo (delegate to lifecycle.init_product_repo)
    3. Extract and write product intent (delegate to intent module)
    4. Match or use provided blueprint
    5. Detect or use provided profile
    6. Run adapter scaffold (delegate to stacks.get_adapter().scaffold())
    7. Run scaffold postflight validation
    8. Create delivery state at phase="scaffolded"
    9. Return result dict

    Returns a dict with keys: success, mode, profile, blueprint,
    scaffold_files, postflight, errors, warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []
    scaffold_files: list[str] = []
    resolved_mode = mode
    resolved_profile = profile
    resolved_blueprint: str | None = blueprint_id
    postflight_result: dict[str, Any] = {"passed": False, "checks": []}

    # 1. Detect mode
    if resolved_mode == "auto":
        resolved_mode = lifecycle.detect_mode(repo_root)

    # For a brand-new product, "auto" must choose a runnable product shell.
    # Detecting an empty directory as generic only produces governance files,
    # which is correct for adoption safety but wrong for greenfield delivery.
    profile_detected = False
    if resolved_profile == "auto":
        if resolved_mode == "greenfield":
            resolved_profile = "react-vite"
        else:
            resolved_profile = stacks.detect_profile(repo_root)
            profile_detected = True

    # 2. Init product repo
    try:
        init_result = lifecycle.init_product_repo(
            repo_root=repo_root,
            mode=resolved_mode,
            profile=resolved_profile,
            product_name=product_name,
        )
        if not init_result["success"]:
            errors.extend(init_result.get("errors", []))
            return _build_result(
                success=False,
                mode=resolved_mode,
                profile=resolved_profile,
                blueprint=resolved_blueprint,
                scaffold_files=scaffold_files,
                postflight=postflight_result,
                errors=errors,
                warnings=warnings,
            )
    except Exception as exc:
        errors.append(f"init failed: {exc}")
        return _build_result(
            success=False,
            mode=resolved_mode,
            profile=resolved_profile,
            blueprint=resolved_blueprint,
            scaffold_files=scaffold_files,
            postflight=postflight_result,
            errors=errors,
            warnings=warnings,
        )

    # 3. Extract and write product intent
    product_intent = intent_mod.extract_product_intent(prompt)
    if product_name and not product_intent.get("product_name"):
        product_intent["product_name"] = product_name
    signalos_dir = repo_root / ".signalos"
    try:
        intent_mod.write_intent(product_intent, signalos_dir)
        scaffold_files.append(".signalos/product/INTENT.json")
    except Exception as exc:
        warnings.append(f"intent write failed: {exc}")

    # 4. Match or use provided blueprint
    if resolved_blueprint is None:
        resolved_blueprint = blueprint_registry.match_blueprint(product_intent)

    # 6. Run adapter scaffold (skip for adopt mode - preserve existing files)
    if resolved_mode != "adopt":
        try:
            adapter = stacks.get_adapter(resolved_profile)
            adapter_result = adapter.scaffold(repo_root, product_intent)
            scaffold_files.extend(adapter_result.get("created", []))
        except Exception as exc:
            errors.append(f"adapter scaffold failed: {exc}")
            return _build_result(
                success=False,
                mode=resolved_mode,
                profile=resolved_profile,
                blueprint=resolved_blueprint,
                scaffold_files=scaffold_files,
                postflight=postflight_result,
                errors=errors,
                warnings=warnings,
            )
    else:
        # Adopt mode: only governance metadata via existing-repo adapter
        try:
            adapter = stacks.get_adapter("existing-repo")
            adapter_result = adapter.scaffold(repo_root, product_intent)
            scaffold_files.extend(adapter_result.get("created", []))
        except Exception as exc:
            warnings.append(f"adopt metadata write failed: {exc}")

    # Add non-runnable warning for generic profile
    if resolved_profile == "generic":
        warnings.append(
            "No runnable profile \u2014 delivery will be partial"
        )

    # 7. Run postflight
    postflight_result = run_postflight(repo_root, resolved_profile)

    # 8. Create delivery state
    try:
        lifecycle.create_delivery_state(
            repo_root=repo_root,
            mode=resolved_mode,
            prompt=prompt,
            profile=resolved_profile,
            blueprint=resolved_blueprint or "",
        )
        lifecycle.update_delivery_phase(repo_root, phase="scaffolded")
    except Exception as exc:
        warnings.append(f"delivery state write failed: {exc}")

    return _build_result(
        success=True,
        mode=resolved_mode,
        profile=resolved_profile,
        blueprint=resolved_blueprint,
        scaffold_files=scaffold_files,
        postflight=postflight_result,
        errors=errors,
        warnings=warnings,
    )


def _build_result(
    *,
    success: bool,
    mode: str,
    profile: str,
    blueprint: str | None,
    scaffold_files: list[str],
    postflight: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "success": success,
        "mode": mode,
        "profile": profile,
        "blueprint": blueprint,
        "scaffold_files": scaffold_files,
        "postflight": postflight,
        "errors": errors,
        "warnings": warnings,
    }
