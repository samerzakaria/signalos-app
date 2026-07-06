# signalos_lib/product/repair_loop.py
# Phase P8 - Agent Execution Bridge: Repair Loop
#
# Runs a bounded repair loop for failed agent validation results.
# Each cycle stores logs and changed files; the loop never runs
# silently -- every repair cycle produces evidence.

from __future__ import annotations

__all__ = [
    "build_repair_packet",
    "run_repair_loop",
    "write_repair_packet",
]

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agent_packets import validate_agent_result


# ---------------------------------------------------------------------------
# Fix #24: type-contract error classification.
#
# The dominant convergence blocker is a component<->TYPE mismatch: the
# component uses a property (.vendor/.notes) that the generated interface never
# declares. The filtered repair scope only ever included the file tsc NAMED as
# the error location (the component) -- NEVER the type module that OWNS the
# contract -- so the contract could never reconcile (whack-a-mole).
#
# These tsc codes signal a property/type-contract mismatch against a generated
# interface. When a cycle's errors are of this class we pull the type module
# (src/types.ts) into the SAME repair cycle alongside the failing component and
# feed BOTH the component error AND the interface into the prompt so the model
# reconciles them together (add the missing field OR remove it from the
# component -- consistently).
# ---------------------------------------------------------------------------

_TYPE_CONTRACT_CODES = frozenset({
    "TS2339",  # Property 'X' does not exist on type 'Y'.
    "TS2551",  # Property 'X' does not exist on type 'Y'. Did you mean 'Z'?
    "TS2353",  # Object literal may only specify known properties.
    "TS2741",  # Property 'X' is missing in type 'Y'.
    "TS2322",  # Type 'A' is not assignable to type 'B'.
})


def _error_code(failure: Any) -> str:
    if isinstance(failure, dict):
        return str(failure.get("code") or "").upper()
    return ""


def _is_type_contract_failure(failure: Any) -> bool:
    """True when a failure is a property/type-contract mismatch (see codes)."""
    return _error_code(failure) in _TYPE_CONTRACT_CODES


def _is_valid(validation_result: dict) -> bool:
    """Bridge the two validation shapes. run_validation (PART 1) reports
    ``can_close_delivery``; validate_agent_result reports ``valid``. Either
    truthy means there is nothing left to repair."""
    if validation_result.get("valid", False):
        return True
    return bool(validation_result.get("can_close_delivery", False))


def _failures_from(validation_result: dict) -> list[dict[str, Any]]:
    """Extract the structured per-file failures the repair loop acts on.

    run_validation emits ``violations`` ({file, line, code, message, category}).
    validate_agent_result emits ``violations`` as flat strings. Both are
    returned as-is; build_repair_packet handles either element type.
    """
    return list(validation_result.get("violations", []) or [])


# ---------------------------------------------------------------------------
# run_repair_loop
# ---------------------------------------------------------------------------

def run_repair_loop(
    repo_root: Path,
    validation_result: dict,
    profile: str,
    max_cycles: int = 3,
    agent_mode: str = "packet-only",
    dispatch_fn: Callable[..., dict] | None = None,
    validate_fn: Callable[..., dict] | None = None,
    governance: dict[str, str] | None = None,
    install_fn: Callable[..., dict] | None = None,
) -> dict:
    """Run the repair loop for failed validation.

    Each cycle:
    1. Identify per-file failures from the validation result.
    2. Build a repair packet that regenerates ONLY the failing files, with
       each file's EXACT diagnostics injected as ``error_context``.
    3. Dispatch or pause depending on *agent_mode*:
       * ``"none"``          -- return ``manual_repair_needed`` (no packet).
       * ``"packet-only"``   -- write the repair packet and pause
         (``awaiting_agent``).
       * ``"auto"`` / ``"remote"`` with an injected dispatch_fn -- DISPATCH
         through the caller's governed executor, then RE-VALIDATE.
       * ``"chunked"`` / ``"legacy-chunked"`` -- explicit legacy opt-in to the
         #12 chunked dispatcher, then RE-VALIDATE.
    4. Track cycle count; stop at *max_cycles* with truthful evidence.

    Parameters
    ----------
    repo_root:
        Workspace root containing ``.signalos/``.
    validation_result:
        Output of ``run_validation`` (``can_close_delivery`` / ``violations``)
        or ``validate_agent_result`` (``valid`` / ``violations``). Either
        shape is accepted.
    profile:
        Stack profile name (e.g. ``"react-vite"``).
    max_cycles:
        Upper bound on repair attempts. 0 means return immediately.
    agent_mode:
        See above.
    dispatch_fn:
        ``(repo_root, packet, governance) -> {status, files_written, errors}``.
        In production this should be a governed AgentLoop dispatcher. If omitted,
        active repair pauses unless *agent_mode* explicitly opts into the legacy
        chunked dispatcher.
    validate_fn:
        ``(repo_root) -> validation_result``. Defaults to a real
        ``run_validation`` against the profile's plan. Injected in tests.
    governance:
        Governance instructions forwarded to the dispatcher (active modes).
    install_fn:
        ``(repo_root) -> {status}``. Runs the package install after an
        add-dependency repair action materializes a missing devDependency.
        Defaults to ``agent_dispatch``'s npm install helper. Injected in tests.
    """
    repairs: list[dict[str, Any]] = []
    normalized_agent_mode = (agent_mode or "packet-only").strip().lower()

    if max_cycles <= 0:
        return {
            "status": "max_cycles_reached",
            "cycles_used": 0,
            "max_cycles": max_cycles,
            "repairs": [],
            "final_validation": validation_result,
        }

    if _is_valid(validation_result):
        return {
            "status": "repaired",
            "cycles_used": 0,
            "max_cycles": max_cycles,
            "repairs": [],
            "final_validation": validation_result,
        }

    # Locate the run directory + load the original scope for repair context
    # (the #12 generation packet with file_specs / manifest / entities).
    run_dir = _find_run_dir(repo_root, validation_result)
    original_packet: dict = {}
    if run_dir is not None:
        scope_path = run_dir / "scope.json"
        if scope_path.is_file():
            try:
                original_packet = json.loads(
                    scope_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                pass

    current_validation = validation_result

    for cycle in range(1, max_cycles + 1):
        failures = _failures_from(current_validation)
        if not failures:
            return {
                "status": "repaired",
                "cycles_used": cycle - 1,
                "max_cycles": max_cycles,
                "repairs": repairs,
                "final_validation": current_validation,
            }

        validation_logs = json.dumps(
            current_validation.get("checks", [])
            or current_validation.get("blockers", []),
            indent=2,
        )

        if normalized_agent_mode == "none":
            repairs.append({
                "cycle": cycle,
                "failures": failures,
                "action": "skipped",
                "packet_path": None,
            })
            return {
                "status": "manual_repair_needed",
                "cycles_used": cycle,
                "max_cycles": max_cycles,
                "repairs": repairs,
                "final_validation": current_validation,
            }

        packet = build_repair_packet(
            repo_root=repo_root,
            cycle=cycle,
            failures=failures,
            validation_logs=validation_logs,
            original_packet=original_packet,
        )
        if run_dir is None:
            run_dir = (
                repo_root
                / ".signalos"
                / "product"
                / "agent-runs"
                / packet["run_id"]
            )
            run_dir.mkdir(parents=True, exist_ok=True)
        packet_path = write_repair_packet(packet, run_dir, cycle)

        if normalized_agent_mode == "packet-only":
            repairs.append({
                "cycle": cycle,
                "failures": failures,
                "action": "packet_created",
                "repair_type": packet.get("repair_type"),
                "packet_path": str(packet_path),
            })
            return {
                "status": "awaiting_agent",
                "cycles_used": cycle,
                "max_cycles": max_cycles,
                "repairs": repairs,
                "final_validation": current_validation,
            }

        # Active modes dispatch through the caller-supplied governed executor.
        # The legacy chunked repair path is retained only behind explicit
        # chunked/legacy-chunked modes for regression/debug use.
        if dispatch_fn is None and normalized_agent_mode not in ("chunked", "legacy-chunked"):
            repairs.append({
                "cycle": cycle,
                "failures": failures,
                "action": "packet_created",
                "repair_type": packet.get("repair_type"),
                "packet_path": str(packet_path),
                "reason": (
                    "active repair requires a governed dispatch_fn; legacy "
                    "chunked repair requires agent_mode='legacy-chunked'"
                ),
            })
            return {
                "status": "awaiting_agent_loop",
                "cycles_used": cycle,
                "max_cycles": max_cycles,
                "repairs": repairs,
                "final_validation": current_validation,
            }

        dispatch = dispatch_fn or _default_dispatch
        validate = validate_fn or _make_default_validate(profile)
        install = install_fn or _default_install
        gov = governance or {}

        # add-dependency repair action: a TS2307 for a KNOWN devDependency is a
        # MISSING-PACKAGE problem -- no code regeneration can fix a package the
        # generated test imports but package.json lacks. Add the dep to
        # devDependencies and re-run install, rather than dispatching a regen.
        missing_deps = _known_missing_devdeps(failures)
        if missing_deps and not _nondep_failures(failures):
            added = _add_dev_dependencies(repo_root, missing_deps)
            install_result = install(repo_root) if added else {"status": "skipped"}
            current_validation = validate(repo_root)
            repaired_now = _is_valid(current_validation)
            repairs.append({
                "cycle": cycle,
                "failures": failures,
                "action": "added_dependency",
                "repair_type": packet.get("repair_type"),
                "packet_path": str(packet_path),
                "dependencies_added": added,
                "install_status": install_result.get("status"),
                "revalidation_passed": repaired_now,
            })
            if repaired_now:
                return {
                    "status": "repaired",
                    "cycles_used": cycle,
                    "max_cycles": max_cycles,
                    "repairs": repairs,
                    "final_validation": current_validation,
                }
            # Missing package resolved but other errors remain (or the dep did
            # not fully clear the build): continue to the next cycle, which will
            # target whatever tsc now reports.
            continue

        dispatch_result = dispatch(repo_root, packet, gov)
        targeted = [
            s.get("path") for s in packet["generation"].get("file_specs", [])
        ]
        if dispatch_result.get("status") not in ("completed",):
            repairs.append({
                "cycle": cycle,
                "failures": failures,
                "action": "dispatch_failed",
                "repair_type": packet.get("repair_type"),
                "packet_path": str(packet_path),
                "targeted_files": targeted,
                "errors": dispatch_result.get("errors", []),
            })
            return {
                "status": "dispatch_failed",
                "cycles_used": cycle,
                "max_cycles": max_cycles,
                "repairs": repairs,
                "final_validation": current_validation,
            }

        current_validation = validate(repo_root)
        repaired_now = _is_valid(current_validation)
        repairs.append({
            "cycle": cycle,
            "failures": failures,
            "action": "dispatched",
            "repair_type": packet.get("repair_type"),
            "packet_path": str(packet_path),
            "targeted_files": targeted,
            "files_written": dispatch_result.get("files_written", []),
            "revalidation_passed": repaired_now,
        })
        if repaired_now:
            return {
                "status": "repaired",
                "cycles_used": cycle,
                "max_cycles": max_cycles,
                "repairs": repairs,
                "final_validation": current_validation,
            }

    # Exhausted max_cycles without the build going green.
    return {
        "status": "max_cycles_reached",
        "cycles_used": max_cycles,
        "max_cycles": max_cycles,
        "repairs": repairs,
        "final_validation": current_validation,
    }


def _default_dispatch(
    repo_root: Path, packet: dict, governance: dict[str, str],
) -> dict:
    """Explicit legacy active-mode dispatch: the #12 chunked per-file build agent."""
    from .agent_dispatch import dispatch_build_agent_chunked

    return dispatch_build_agent_chunked(
        repo_root=repo_root, packet=packet, governance=governance,
    )


# ---------------------------------------------------------------------------
# add-dependency repair action
#
# tsc reports TS2307 'Cannot find module @testing-library/user-event' when a
# generated test imports a devDependency that package.json never listed. No
# code-regen fixes a missing package. This small allowlist names the KNOWN test
# devDependencies a react-vite product legitimately needs; a TS2307 for one of
# these adds it to devDependencies and re-runs install instead of regenerating.
# Deliberately narrow -- only well-known test tooling, never runtime deps.
# ---------------------------------------------------------------------------

_KNOWN_DEV_DEPENDENCIES: dict[str, str] = {
    "@testing-library/user-event": "^14.5.2",
    "@testing-library/jest-dom": "^6.4.2",
    "@testing-library/react": "^16.0.1",
    "@testing-library/dom": "^10.4.0",
    "@types/testing-library__jest-dom": "^6.0.0",
    "jsdom": "^25.0.1",
}

# TS2307 with a package specifier (bare module, not a relative './' path).
_MODULE_NOT_FOUND_CODES = frozenset({"TS2307"})


def _missing_module_name(failure: Any) -> str | None:
    """Extract the module specifier a TS2307 'Cannot find module X' names."""
    if not isinstance(failure, dict):
        return None
    if _error_code(failure) not in _MODULE_NOT_FOUND_CODES:
        return None
    import re

    msg = str(failure.get("message") or "")
    m = re.search(r"Cannot find module ['\"]([^'\"]+)['\"]", msg)
    if not m:
        return None
    return m.group(1)


def _known_missing_devdeps(failures: list) -> list[str]:
    """Package names from this cycle's TS2307 errors that are KNOWN devDeps."""
    names: list[str] = []
    for failure in failures:
        mod = _missing_module_name(failure)
        if mod and mod in _KNOWN_DEV_DEPENDENCIES and mod not in names:
            names.append(mod)
    return names


def _nondep_failures(failures: list) -> list:
    """Failures that are NOT a known-devDep missing-package error.

    A TS2307 for an unknown/relative module is a code problem (regenerate);
    only a KNOWN missing devDependency is handled by the add-dependency action.
    """
    out: list = []
    for failure in failures:
        mod = _missing_module_name(failure)
        if mod and mod in _KNOWN_DEV_DEPENDENCIES:
            continue
        out.append(failure)
    return out


def _add_dev_dependencies(repo_root: Path, names: list[str]) -> list[str]:
    """Add the given known devDependencies to package.json; return those added.

    No-op (returns []) when there is no package.json or nothing to add. Never
    raises -- a malformed package.json simply yields no additions.
    """
    pkg_path = repo_root / "package.json"
    if not pkg_path.is_file():
        return []
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    dev = pkg.setdefault("devDependencies", {})
    if not isinstance(dev, dict):
        return []
    added: list[str] = []
    for name in names:
        if name in dev:
            continue
        dev[name] = _KNOWN_DEV_DEPENDENCIES[name]
        added.append(name)
    if added:
        pkg_path.write_text(
            json.dumps(pkg, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return added


def _default_install(repo_root: Path) -> dict:
    """Default install after add-dependency: npm install in the workspace.

    Reuses validation's shell runner so it honors the same which/timeout
    contract. Never raises -- an install failure is reported, not thrown, so
    the repair loop keeps its truthful evidence.
    """
    try:
        from .validation import _run_commands
    except Exception:
        return {"status": "skipped", "reason": "no installer available"}
    try:
        return _run_commands(repo_root, ["npm install --legacy-peer-deps"])
    except Exception as exc:  # never let install crash the repair loop
        return {"status": "failed", "errors": [str(exc)]}


def _make_default_validate(profile: str) -> Callable[[Path], dict]:
    """Default re-validation: build + run the profile's validation plan."""

    def _validate(repo_root: Path) -> dict:
        from .validation import build_validation_plan, run_validation

        plan = build_validation_plan(repo_root, profile)
        return run_validation(repo_root, plan, dry_run=False)

    return _validate


# ---------------------------------------------------------------------------
# build_repair_packet
# ---------------------------------------------------------------------------

def build_repair_packet(
    repo_root: Path,
    cycle: int,
    failures: list,
    validation_logs: str,
    original_packet: dict,
) -> dict:
    """Build a repair packet for a failed validation cycle.

    The repair packet inherits context from the original packet (tasks,
    allowed paths, and the #12 generation context: manifest / entities /
    design constraints) and, crucially, regenerates ONLY the failing files:
    its ``generation.file_specs`` is filtered to the specs whose path matches a
    failing file, with that file's EXACT diagnostics injected as
    ``error_context`` so the build agent knows precisely what to fix.

    *failures* may be structured per-file dicts (``{file, line, code,
    message}`` from run_validation) or flat strings (validate_agent_result);
    both are handled.
    """
    run_id = original_packet.get("run_id", str(uuid.uuid4()))
    forbidden_violated = _has_forbidden_failure(failures)

    original_gen = dict(original_packet.get("generation", {}) or {})
    filtered_gen = _build_filtered_generation(original_gen, failures, repo_root)

    packet = {
        "schema_version": "signalos.repair_packet.v1",
        "run_id": run_id,
        "repair_cycle": cycle,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repair_type": (
            "regenerate_from_clean_packet"
            if forbidden_violated
            else "repair_within_scope"
        ),
        "profile": original_packet.get("profile", ""),
        "wave": original_packet.get("wave", ""),
        "intent_summary": original_packet.get("intent_summary", {}),
        "tasks": original_packet.get("tasks", []),
        "success_criteria": original_packet.get("success_criteria", []),
        "evidence_required": original_packet.get("evidence_required", []),
        "forbidden_rules": original_packet.get("forbidden_rules", []),
        "repair_policy": original_packet.get("repair_policy", {}),
        "escalation_policy": original_packet.get("escalation_policy", []),
        "source_policy": original_packet.get("source_policy", {}),
        "allowed_paths": original_packet.get("allowed_paths", []),
        "forbidden_paths": original_packet.get("forbidden_paths", []),
        "forbidden_actions": original_packet.get("forbidden_actions", []),
        "validation_commands": original_packet.get("validation_commands", []),
        "failures": failures,
        "validation_logs": validation_logs,
        # The dispatch-ready, per-file-scoped generation packet.
        "generation": filtered_gen,
    }
    return packet


def _failure_file(failure: Any) -> str | None:
    """The target file path a failure names, or None for a non-file failure."""
    if isinstance(failure, dict):
        f = failure.get("file")
        return str(f).replace("\\", "/") if f else None
    return None


def _balance_enrichment(
    fpath: str, errors: list[dict], repo_root: Path | None,
) -> dict | None:
    """#38: when a file's failures include a tsc syntax-class code (where the
    reported column often misleads), run the deterministic delimiter-balance
    pass on the on-disk file and, if it is unbalanced, return a crisp
    synthesized diagnostic that localizes the real problem for the repair
    prompt. Corroboration-gated: only ever fires alongside an existing tsc
    syntax error, so a heuristic miscount can never invent a failure -- it only
    sharpens one tsc already raised. Returns None when nothing to add."""
    if repo_root is None:
        return None
    from .validation import _SYNTAX_ERROR_CODES, analyze_delimiter_balance

    codes = {str(e.get("code") or "").upper() for e in errors}
    if not (codes & _SYNTAX_ERROR_CODES):
        return None
    if not fpath.endswith((".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")):
        return None
    try:
        text = (repo_root / fpath).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    report = analyze_delimiter_balance(text)
    if report.get("balanced") or not report.get("hint"):
        return None
    return {
        "file": fpath,
        "line": None,
        "col": None,
        "code": "BALANCE",
        "message": (
            "Delimiter-balance check (deterministic): "
            + report["hint"]
            + ". tsc's reported column is often downstream of the real "
            "imbalance -- re-emit the ENTIRE file with every (), {}, and [] "
            "correctly matched; do not just edit near the tsc line."
        ),
        "source": "signalos-balance",
    }


_TS2307_RE = re.compile(r"[Cc]annot find module ['\"]([^'\"]+)['\"]")


def _import_drift_enrichment(
    fpath: str, errors: list[dict], gen: dict,
) -> dict | None:
    """#47: when a file fails with TS2307 'Cannot find module X', tell the
    regeneration -- crisply and with MANIFEST context -- that X does not exist
    and list the ONLY local modules that do (the #12 component manifest + the
    shared types). Without this the repair loop re-invents the same phantom
    path (the funded run's `./store/taskStore`). Returns None when no such
    error is present."""
    phantom: list[str] = []
    for e in errors:
        code = str(e.get("code") or "").upper()
        msg = str(e.get("message") or "")
        if code == "TS2307" or "cannot find module" in msg.lower():
            m = _TS2307_RE.search(msg)
            if m and m.group(1) not in phantom:
                phantom.append(m.group(1))
    if not phantom:
        return None
    manifest = gen.get("component_manifest", []) or []
    allowed = [str(m.get("importPath")) for m in manifest if m.get("importPath")]
    allowed_line = ", ".join(f"`{p}`" for p in allowed) if allowed else "(none)"
    return {
        "file": fpath,
        "line": None,
        "col": None,
        "code": "IMPORT-DRIFT",
        "message": (
            "These imported modules DO NOT EXIST: "
            + ", ".join(f"`{p}`" for p in phantom)
            + ". Remove or correct each one. The ONLY local modules you may "
            "import are the generated components (" + allowed_line
            + ") and the shared types via `./types`. NEVER invent a "
            "`./store/*`, `./hooks/*`, `@/*`, or `../ui/*` path -- if you need "
            "a component's store, import it from THAT component's module listed "
            "above (e.g. `import Component, { useStore } from './Component'`), "
            "not a separate file."
        ),
        "source": "signalos-import-drift",
    }


def _build_filtered_generation(
    original_gen: dict, failures: list, repo_root: Path | None = None,
) -> dict:
    """Return a copy of the original generation packet whose ``file_specs``
    are filtered to ONLY the failing files, each carrying an ``error_context``
    with that file's exact diagnostics.

    Cross-file context (component_manifest, entities, design_constraints,
    types_module_names, allowed/forbidden paths) is preserved so the
    regenerated file still imports the REAL components/types (#12), not
    phantoms. When no failure names a file that is in the original specs, the
    packet falls back to the full spec list so a bundler-only failure still
    regenerates something rather than nothing.
    """
    gen = deepcopy(original_gen)
    specs = list(gen.get("file_specs", []) or [])
    by_path = {
        str(s.get("path", "")).replace("\\", "/"): s
        for s in specs
        if s.get("path")
    }

    # Group each failure under the spec path it names.
    errors_by_file: dict[str, list[dict]] = {}
    order: list[str] = []
    for failure in failures:
        fpath = _failure_file(failure)
        if not fpath or fpath not in by_path:
            continue
        if fpath not in errors_by_file:
            errors_by_file[fpath] = []
            order.append(fpath)
        errors_by_file[fpath].append(_normalize_error(failure))

    if not order:
        # No structured per-file target matched a known spec -- keep the full
        # packet (whole-packet repair) rather than emitting zero files.
        return gen

    filtered: list[dict] = []
    for fpath in order:
        spec = deepcopy(by_path[fpath])
        errors = errors_by_file[fpath]
        # #38: sharpen a misleading tsc syntax error with a deterministic
        # delimiter-balance diagnostic so the repair loop can converge on the
        # "closed a call with `}` instead of `});`" class it otherwise loops on.
        enrichment = _balance_enrichment(fpath, errors, repo_root)
        if enrichment is not None:
            errors = errors + [enrichment]
        # #47: sharpen TS2307 'Cannot find module' with the real manifest paths
        # so the repair removes the phantom import instead of re-inventing it.
        drift = _import_drift_enrichment(fpath, errors, gen)
        if drift is not None:
            errors = errors + [drift]
        spec["error_context"] = errors
        filtered.append(spec)

    # Fix #24: when any failure this cycle is a property/type-contract error
    # against a generated interface, the repair scope MUST include the module
    # that OWNS the contract (the type module), not just the file tsc pointed
    # at. Add the type module spec alongside the failing component(s) and carry
    # the component's contract error onto it so the model reconciles both
    # together (add the missing field to the interface OR remove it from the
    # component -- consistently), instead of playing whack-a-mole.
    contract_errors = [
        _normalize_error(f) for f in failures if _is_type_contract_failure(f)
    ]
    if contract_errors:
        type_path = _type_module_path(gen, by_path)
        already = {s["path"].replace("\\", "/") for s in filtered}
        if type_path and type_path in by_path and type_path not in already:
            type_spec = deepcopy(by_path[type_path])
            # The type module did not itself trigger a tsc error; it is pulled
            # in to reconcile the contract. Feed it the component contract
            # errors so the prompt shows exactly which fields are in conflict.
            type_spec["error_context"] = [
                {**e, "reconcile_contract": True} for e in contract_errors
            ]
            filtered.append(type_spec)

    gen["file_specs"] = filtered
    return gen


def _type_module_path(gen: dict, by_path: dict[str, dict]) -> str | None:
    """Locate the type module spec path that OWNS the entity contract.

    Prefers a spec named by ``types_module_names`` semantics -- in practice the
    react-vite foundation type module ``src/types.ts`` (kind=config). Falls back
    to any spec path ending in ``types.ts``/``types.tsx`` present in the packet.
    Returns None when the packet has no type module (nothing to reconcile).
    """
    # Direct: a spec whose path is the canonical types module.
    for path in by_path:
        norm = path.replace("\\", "/")
        if norm.endswith("/types.ts") or norm == "types.ts" or norm.endswith("/types.tsx"):
            return path
    return None


def _normalize_error(failure: Any) -> dict:
    """Coerce a failure into the ``{file, line, col, code, message}`` shape the
    single-file prompt renders. Strings become a bare ``message``."""
    if isinstance(failure, dict):
        return {
            "file": failure.get("file"),
            "line": failure.get("line"),
            "col": failure.get("col"),
            "code": failure.get("code"),
            "message": failure.get("message", ""),
        }
    return {"file": None, "line": None, "col": None, "code": None, "message": str(failure)}


# ---------------------------------------------------------------------------
# write_repair_packet
# ---------------------------------------------------------------------------

def write_repair_packet(
    packet: dict,
    run_dir: Path,
    cycle: int,
) -> Path:
    """Write repair packet to ``<run_dir>/repair-<cycle>/``.

    Creates:
    - ``repair-scope.json`` -- full repair packet
    - ``REPAIR.md``         -- human-readable repair instructions

    Returns the repair directory path.
    """
    repair_dir = run_dir / f"repair-{cycle}"
    repair_dir.mkdir(parents=True, exist_ok=True)

    (repair_dir / "repair-scope.json").write_text(
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    md = _render_repair_md(packet, cycle)
    (repair_dir / "REPAIR.md").write_text(md, encoding="utf-8")

    return repair_dir


def _render_repair_md(packet: dict, cycle: int) -> str:
    """Render a human-readable Markdown repair instruction."""
    lines: list[str] = []
    lines.append(f"# Repair Cycle {cycle}")
    lines.append("")
    lines.append(f"**Run ID:** {packet.get('run_id', '')}")
    lines.append(f"**Created:** {packet.get('created_at', '')}")
    lines.append(f"**Profile:** {packet.get('profile', '')}")
    lines.append(f"**Wave:** {packet.get('wave', '')}")
    lines.append(f"**Repair type:** {packet.get('repair_type', '')}")
    lines.append("")
    lines.append("## Success Criteria")
    lines.append("")
    for item in packet.get("success_criteria", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Evidence Required")
    lines.append("")
    for item in packet.get("evidence_required", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Forbidden Rules")
    lines.append("")
    for item in packet.get("forbidden_rules", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Repair/Rework Policy")
    lines.append("")
    for key, value in packet.get("repair_policy", {}).items():
        lines.append(f"- **{key}:** {value}")
    lines.append("")
    lines.append("## Failures to Fix")
    lines.append("")
    for failure in packet.get("failures", []):
        if isinstance(failure, dict):
            loc = ""
            if failure.get("line") is not None:
                loc = f":{failure['line']}"
            code = failure.get("code", "")
            code_part = f"{code}: " if code else ""
            lines.append(
                f"- {failure.get('file', '')}{loc} -- "
                f"{code_part}{failure.get('message', '')}".rstrip()
            )
        else:
            lines.append(f"- {failure}")
    lines.append("")
    lines.append("## Validation Logs")
    lines.append("")
    lines.append("```json")
    lines.append(packet.get("validation_logs", ""))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _has_forbidden_failure(failures: list) -> bool:
    markers = (
        "forbidden path",
        "forbidden action",
        "in forbidden paths",
        "not within allowed paths",
        "outside allowed paths",
        ".signalos",
        ".git",
        ".env",
        ".pem",
        ".key",
    )
    for failure in failures:
        if isinstance(failure, dict):
            text = " ".join(
                str(failure.get(k, ""))
                for k in ("file", "message", "code")
            )
        else:
            text = str(failure)
        low = text.lower()
        if any(marker in low for marker in markers):
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_run_dir(
    repo_root: Path, validation_result: dict
) -> Path | None:
    """Attempt to locate the agent-run directory for a validation result.

    The result doesn't carry a run_id directly; we look for the most
    recent run directory under ``.signalos/product/agent-runs/``.
    """
    runs_dir = repo_root / ".signalos" / "product" / "agent-runs"
    if not runs_dir.is_dir():
        return None
    # Find the most recently modified run directory
    candidates = [
        d for d in runs_dir.iterdir()
        if d.is_dir() and (d / "scope.json").is_file()
    ]
    if not candidates:
        return None
    # Sort by modification time, newest first
    candidates.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return candidates[0]
