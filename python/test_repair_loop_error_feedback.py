"""Tests for PART 2: compile-error feedback -> per-file regeneration.

The repair loop must CLOSE the loop: given the structured, per-file
``violations`` that PART 1's run_validation now produces (tsc/vitest
diagnostics), it must

  1. build a repair packet that regenerates ONLY the failing files, with each
     failing file's EXACT diagnostics injected as ``error_context`` on its
     file_spec (so the build agent is told precisely what to fix);
  2. in an active (dispatch) mode, dispatch that filtered packet through the
     #12 chunked per-file dispatcher and RE-VALIDATE, bounded by max_cycles;
  3. STOP as soon as re-validation passes (build green);
  4. GIVE UP after max_cycles with truthful evidence when it never passes.

Dispatch + validation are injected (dependency injection) so these tests are
hermetic: no LLM, no npm, no toolchain. The default (production) path wires the
real dispatch_build_agent_chunked + run_validation.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.repair_loop import (
    build_repair_packet,
    run_repair_loop,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _original_packet():
    """A packet as written to scope.json: full generation with file_specs."""
    return {
        "run_id": "repair-run",
        "profile": "react-vite",
        "wave": "1",
        "allowed_paths": ["src/**"],
        "forbidden_paths": [".env", ".signalos/"],
        "generation": {
            "profile": "react-vite",
            "product": "Acme",
            "file_specs": [
                {"path": "src/types.ts", "kind": "config"},
                {"path": "src/App.tsx", "kind": "source", "entity": "Task"},
                {"path": "src/components/TaskList.tsx", "kind": "source", "entity": "Task"},
                {"path": "src/components/TaskForm.tsx", "kind": "source", "entity": "Task"},
            ],
            "component_manifest": [
                {"componentName": "TaskList", "importPath": "./components/TaskList",
                 "filePath": "src/components/TaskList.tsx"},
                {"componentName": "TaskForm", "importPath": "./components/TaskForm",
                 "filePath": "src/components/TaskForm.tsx"},
            ],
            "entities": [{"name": "Task", "fields": ["id", "title"]}],
            "allowed_paths": ["src/**"],
            "forbidden_paths": [".env", ".signalos/"],
        },
    }


def _validation_with_violations(violations):
    """Shape produced by run_validation (PART 1)."""
    return {
        "schema_version": "signalos.validation_result.v1",
        "profile": "react-vite",
        "dry_run": False,
        "results": {"build": {"status": "failed", "output": "tsc failed"}},
        "can_close_delivery": False,
        "blockers": ["build check failed"],
        "violations": violations,
    }


def _clean_validation():
    return {
        "schema_version": "signalos.validation_result.v1",
        "profile": "react-vite",
        "dry_run": False,
        "results": {"build": {"status": "passed"}, "test": {"status": "passed"}},
        "can_close_delivery": True,
        "blockers": [],
        "violations": [],
    }


def _write_scope(repo: Path, packet: dict) -> Path:
    run_dir = repo / ".signalos" / "product" / "agent-runs" / packet["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scope.json").write_text(json.dumps(packet), encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------------------
# build_repair_packet -- targets ONLY failing files, injects error text
# ---------------------------------------------------------------------------

def test_repair_packet_targets_only_failing_files():
    violations = [
        {"file": "src/components/TaskList.tsx", "line": 12, "code": "TS2307",
         "message": "Cannot find module '@/ui/button'.", "category": "build"},
    ]
    packet = build_repair_packet(
        repo_root=Path("."),
        cycle=1,
        failures=violations,
        validation_logs="",
        original_packet=_original_packet(),
    )
    specs = packet["generation"]["file_specs"]
    paths = [s["path"] for s in specs]
    # ONLY the failing file is regenerated -- not types.ts / App.tsx / TaskForm.
    assert paths == ["src/components/TaskList.tsx"], paths


def test_repair_packet_injects_exact_error_text_per_file():
    violations = [
        {"file": "src/components/TaskList.tsx", "line": 12, "code": "TS2307",
         "message": "Cannot find module '@/ui/button'.", "category": "build"},
        {"file": "src/App.tsx", "line": 3, "code": "TS2304",
         "message": "Cannot find name 'Widget'.", "category": "build"},
    ]
    packet = build_repair_packet(
        repo_root=Path("."),
        cycle=2,
        failures=violations,
        validation_logs="",
        original_packet=_original_packet(),
    )
    specs = {s["path"]: s for s in packet["generation"]["file_specs"]}
    assert set(specs) == {"src/components/TaskList.tsx", "src/App.tsx"}
    ec = specs["src/components/TaskList.tsx"]["error_context"]
    assert any("TS2307" == e.get("code") for e in ec)
    assert any("@/ui/button" in e.get("message", "") for e in ec)
    # App.tsx gets ITS OWN diagnostics, not TaskList's.
    ec_app = specs["src/App.tsx"]["error_context"]
    assert all("Widget" in e.get("message", "") or e.get("code") == "TS2304" for e in ec_app)


def test_repair_packet_multiple_errors_same_file_grouped():
    violations = [
        {"file": "src/App.tsx", "line": 3, "code": "TS2304",
         "message": "Cannot find name 'Widget'.", "category": "build"},
        {"file": "src/App.tsx", "line": 9, "code": "TS2307",
         "message": "Cannot find module '@/store'.", "category": "build"},
    ]
    packet = build_repair_packet(
        repo_root=Path("."),
        cycle=1,
        failures=violations,
        validation_logs="",
        original_packet=_original_packet(),
    )
    specs = packet["generation"]["file_specs"]
    assert len(specs) == 1
    ec = specs[0]["error_context"]
    codes = [e.get("code") for e in ec]
    # both original errors are grouped under the one file...
    assert "TS2304" in codes
    assert "TS2307" in codes
    # ...plus the #47 import-drift enrichment the TS2307 triggers.
    assert "IMPORT-DRIFT" in codes


def test_repair_packet_preserves_manifest_and_context():
    violations = [
        {"file": "src/components/TaskList.tsx", "code": "TS2307",
         "message": "boom", "category": "build"},
    ]
    packet = build_repair_packet(
        repo_root=Path("."),
        cycle=1,
        failures=violations,
        validation_logs="",
        original_packet=_original_packet(),
    )
    gen = packet["generation"]
    # The cross-file contract (#12 manifest) still flows so the regenerated
    # file imports the real components, not phantoms.
    assert gen.get("component_manifest")
    assert gen.get("entities")
    assert gen.get("allowed_paths") == ["src/**"]


# ---------------------------------------------------------------------------
# run_repair_loop -- active dispatch mode closes / bounds the loop
# ---------------------------------------------------------------------------

def test_loop_stops_when_validation_passes():
    """Cycle 1 dispatches; re-validation is clean -> status repaired, 1 cycle."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        _write_scope(repo, _original_packet())

        dispatched = []

        def fake_dispatch(repo_root, packet, governance):
            dispatched.append(packet)
            return {"status": "completed", "files_written": ["src/App.tsx"], "errors": []}

        validations = iter([_clean_validation()])

        def fake_validate(repo_root):
            return next(validations)

        initial = _validation_with_violations([
            {"file": "src/App.tsx", "line": 3, "code": "TS2304",
             "message": "Cannot find name 'Widget'.", "category": "build"},
        ])
        result = run_repair_loop(
            repo_root=repo,
            validation_result=initial,
            profile="react-vite",
            max_cycles=3,
            agent_mode="auto",
            dispatch_fn=fake_dispatch,
            validate_fn=fake_validate,
        )
        assert result["status"] == "repaired", result
        assert result["cycles_used"] == 1
        # Exactly one dispatch, and it targeted only the failing file.
        assert len(dispatched) == 1
        specs = dispatched[0]["generation"]["file_specs"]
        assert [s["path"] for s in specs] == ["src/App.tsx"]


def test_loop_gives_up_after_max_cycles():
    """Build never goes green -> max_cycles_reached, exactly max_cycles dispatches."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        _write_scope(repo, _original_packet())

        dispatched = []

        def fake_dispatch(repo_root, packet, governance):
            dispatched.append(packet)
            return {"status": "completed", "files_written": ["src/App.tsx"], "errors": []}

        bad = _validation_with_violations([
            {"file": "src/App.tsx", "line": 3, "code": "TS2304",
             "message": "still broken", "category": "build"},
        ])

        def fake_validate(repo_root):
            # Always returns a fresh failing validation.
            return _validation_with_violations([
                {"file": "src/App.tsx", "line": 3, "code": "TS2304",
                 "message": "still broken", "category": "build"},
            ])

        result = run_repair_loop(
            repo_root=repo,
            validation_result=bad,
            profile="react-vite",
            max_cycles=3,
            agent_mode="auto",
            dispatch_fn=fake_dispatch,
            validate_fn=fake_validate,
        )
        assert result["status"] == "max_cycles_reached", result
        assert result["cycles_used"] == 3
        assert len(dispatched) == 3
        assert result["final_validation"]["can_close_delivery"] is False


def test_loop_returns_repaired_when_already_valid():
    """No violations to begin with -> immediate repaired, no dispatch."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        called = []
        result = run_repair_loop(
            repo_root=repo,
            validation_result=_clean_validation(),
            profile="react-vite",
            max_cycles=3,
            agent_mode="auto",
            dispatch_fn=lambda *a, **k: called.append(1),
            validate_fn=lambda *a, **k: _clean_validation(),
        )
        assert result["status"] == "repaired"
        assert result["cycles_used"] == 0
        assert called == []


def test_loop_second_cycle_targets_newly_failing_file():
    """Cycle 1 fixes App.tsx but reveals a TaskList error; cycle 2 targets it."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        _write_scope(repo, _original_packet())
        dispatched = []

        def fake_dispatch(repo_root, packet, governance):
            dispatched.append([s["path"] for s in packet["generation"]["file_specs"]])
            return {"status": "completed", "files_written": [], "errors": []}

        # After cycle 1: App fixed, but TaskList now fails. After cycle 2: clean.
        seq = iter([
            _validation_with_violations([
                {"file": "src/components/TaskList.tsx", "code": "TS2307",
                 "message": "boom", "category": "build"},
            ]),
            _clean_validation(),
        ])

        initial = _validation_with_violations([
            {"file": "src/App.tsx", "code": "TS2304", "message": "x", "category": "build"},
        ])
        result = run_repair_loop(
            repo_root=repo,
            validation_result=initial,
            profile="react-vite",
            max_cycles=3,
            agent_mode="auto",
            dispatch_fn=fake_dispatch,
            validate_fn=lambda *a, **k: next(seq),
        )
        assert result["status"] == "repaired", result
        assert result["cycles_used"] == 2
        assert dispatched[0] == ["src/App.tsx"]
        assert dispatched[1] == ["src/components/TaskList.tsx"]


def test_packet_only_mode_still_writes_packet_and_pauses():
    """Back-compat: packet-only mode still produces a repair packet + pauses."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        _write_scope(repo, _original_packet())
        result = run_repair_loop(
            repo_root=repo,
            validation_result=_validation_with_violations([
                {"file": "src/App.tsx", "code": "TS2304", "message": "x", "category": "build"},
            ]),
            profile="react-vite",
            max_cycles=3,
            agent_mode="packet-only",
        )
        assert result["status"] == "awaiting_agent"
        assert result["repairs"][0]["packet_path"]
        packet_path = Path(result["repairs"][0]["packet_path"])
        scope = json.loads((packet_path / "repair-scope.json").read_text(encoding="utf-8"))
        # Even in packet-only mode the packet is per-file + error-injected.
        specs = scope["generation"]["file_specs"]
        assert [s["path"] for s in specs] == ["src/App.tsx"]
        assert specs[0]["error_context"]


def test_dispatch_failure_stops_loop_without_false_repaired():
    """If dispatch itself fails, the loop must not claim 'repaired'."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        _write_scope(repo, _original_packet())

        def fake_dispatch(repo_root, packet, governance):
            return {"status": "failed", "files_written": [], "errors": ["boom"]}

        result = run_repair_loop(
            repo_root=repo,
            validation_result=_validation_with_violations([
                {"file": "src/App.tsx", "code": "TS2304", "message": "x", "category": "build"},
            ]),
            profile="react-vite",
            max_cycles=3,
            agent_mode="auto",
            dispatch_fn=fake_dispatch,
            validate_fn=lambda *a, **k: _clean_validation(),
        )
        assert result["status"] != "repaired"
        assert result["status"] in ("dispatch_failed", "max_cycles_reached")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
