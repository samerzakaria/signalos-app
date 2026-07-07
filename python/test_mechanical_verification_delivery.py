"""Pipeline integration tests for the three mechanical-verification layers
(pattern follows test_delivery_traceability.py's integration test):

1. verifiability tiers persisted in ACCEPTANCE_MATRIX.json and surfaced in
   REVIEW_RESULT.json / CLOSEOUT.json / product-summary.md;
2. evidence-freshness snapshots bound to VALIDATION_RESULT.json and the
   runtime-proof artifact, verified at closeout (incl. the repair-loop
   ordering no-false-positive and a post-proof tamper detection);
3. the deterministic test-quality report persisted next to the other
   review evidence and folded into the review verdict.

Also covers the two delivery addenda:
- repair-written files are re-linked into the traceability report;
- the design phase threads repo_root so a seeded COMPETITORS.json reaches
  the design-architect prompt (mock LLM).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_lib.harness as harness  # noqa: E402
from signalos_lib.product import delivery as delivery_mod  # noqa: E402
from signalos_lib.product import design as design_mod  # noqa: E402
from signalos_lib.product.delivery import run_delivery  # noqa: E402


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestMechanicalVerificationPipeline:
    def test_delivery_lands_all_three_artifacts(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "mech-product"
            closeout = run_delivery(
                prompt="Build me a task management app with projects and tasks",
                name="mech-product",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                blueprint="auto",
                deploy="none",
                dry_run=True,
            )
            signalos = repo_root / ".signalos"

            # --- Layer 1: tiers in the matrix ---
            matrix = _read_json(signalos / "product" / "ACCEPTANCE_MATRIX.json")
            assert matrix["criteria"], "expected acceptance criteria"
            for criterion in matrix["criteria"]:
                assert criterion["verifiability"] in (
                    "mechanical", "partial", "human",
                )
            summary = matrix["verifiability_summary"]
            assert set(summary) == {
                "mechanical", "partial", "human", "mechanical_pct",
            }
            total = sum(
                summary[k] for k in ("mechanical", "partial", "human")
            )
            assert total == len(matrix["criteria"])

            # ... surfaced on the review verdict + test-quality check present
            review = _read_json(signalos / "product" / "REVIEW_RESULT.json")
            assert review["verifiability"] == summary
            assert "test_quality" in review["checks"]

            # ... and in the closeout + product summary line
            persisted = _read_json(signalos / "product" / "CLOSEOUT.json")
            assert persisted["verifiability_summary"] == summary
            assert closeout["verifiability_summary"] == summary
            product_summary = (
                signalos / "handoffs" / "product-summary.md"
            ).read_text(encoding="utf-8")
            assert (
                f"{summary['mechanical_pct']}% of acceptance criteria "
                f"are mechanically verified" in product_summary
            )

            # --- Layer 2: snapshots bound to the evidence ---
            val = _read_json(signalos / "product" / "VALIDATION_RESULT.json")
            snap = val["workspace_snapshot"]
            assert snap["algo"] == "sha256"
            assert snap["files"], "validation snapshot must hash real files"
            assert not any(k.startswith(".signalos/") for k in snap["files"])

            smoke = _read_json(
                signalos / "product" / "proof" / "runtime" / "smoke.json",
            )
            assert smoke["workspace_snapshot"]["files"]

            freshness = _read_json(
                signalos / "product" / "EVIDENCE_FRESHNESS.json",
            )
            assert freshness["fresh"] is True
            assert freshness["mode"] in ("strict", "warn")
            assert persisted["evidence_freshness"]["fresh"] is True

            # --- Layer 3: test-quality report persisted ---
            quality = _read_json(signalos / "product" / "TEST_QUALITY.json")
            for key in (
                "files_analyzed",
                "vacuous_tests",
                "assertion_free_files",
                "weak_criterion_links",
            ):
                assert key in quality

    def test_repair_written_files_relinked_and_snapshot_after_repair(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Addendum regression + Layer 2 ordering:

        - a file added during repair appears in the traceability report
          (previously it showed as neither covered nor unlinked);
        - the freshness snapshot sits AFTER the repair loop, so repair
          rewrites/additions never read as stale evidence at closeout.
        """
        monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")

        failing = {
            "can_close_delivery": False,
            "dry_run": False,
            "results": {
                "build": {"status": "failed", "output": "boom"},
                "test": {"status": "failed", "output": "boom"},
            },
            "blockers": ["build failed"],
            "checks": [],
        }
        passing = {
            "can_close_delivery": True,
            "dry_run": False,
            "results": {
                "build": {"status": "passed"},
                "test": {"status": "passed"},
            },
            "blockers": [],
            "checks": [],
        }

        def fake_run_validation(repo_root, plan, dry_run=False):
            return json.loads(json.dumps(failing))

        def fake_run_repair_loop(**kwargs):
            repo_root = Path(kwargs["repo_root"])
            # The repair legitimately ADDS a helper file...
            helper = repo_root / "src" / "zzhelper_util.py"
            helper.parent.mkdir(parents=True, exist_ok=True)
            helper.write_text("# repair helper\n", encoding="utf-8")
            # ...and REWRITES an existing generated file.
            for existing in sorted((repo_root / "src").rglob("*")):
                if existing.is_file() and existing != helper:
                    existing.write_text(
                        existing.read_text(encoding="utf-8") + "\n# repaired\n",
                        encoding="utf-8",
                    )
                    break
            return {
                "status": "repaired",
                "cycles_used": 1,
                "max_cycles": 3,
                "repairs": [{
                    "cycle": 1,
                    "action": "dispatched",
                    "files_written": ["src/zzhelper_util.py"],
                    "revalidation_passed": True,
                }],
                "final_validation": json.loads(json.dumps(passing)),
            }

        monkeypatch.setattr(delivery_mod, "run_validation", fake_run_validation)
        monkeypatch.setattr(delivery_mod, "run_repair_loop", fake_run_repair_loop)

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repair-product"
            closeout = run_delivery(
                prompt="Build me a task management app with projects and tasks",
                name="repair-product",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                blueprint="auto",
                deploy="none",
                dry_run=False,
            )
            signalos = repo_root / ".signalos"

            # The repair-added helper entered the trace view: it traces to no
            # criterion, so it must show up as an (advisory) unlinked path --
            # before the re-link it appeared NOWHERE in the report.
            matrix = _read_json(signalos / "product" / "ACCEPTANCE_MATRIX.json")
            trace = matrix["traceability"]
            assert "src/zzhelper_util.py" in trace["unlinked_paths"]

            # The proof snapshot (the LATEST one) was captured after the
            # repair, so it covers the repair-added file...
            smoke = _read_json(
                signalos / "product" / "proof" / "runtime" / "smoke.json",
            )
            assert "src/zzhelper_util.py" in smoke["workspace_snapshot"]["files"]

            # ...and the closeout freshness check reports NO drift: repair
            # rewrites between validation cycles are not stale evidence.
            assert closeout["evidence_freshness"]["fresh"] is True
            assert not any(
                "evidence is stale" in lim
                for lim in closeout.get("known_limitations", [])
            )

    def test_post_proof_tampering_is_stale_evidence(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A generated file changed AFTER proof (here: during acceptance
        reconciliation, which runs after the last snapshot) is drift."""
        monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")

        real_reconcile = delivery_mod.reconcile_acceptance_evidence

        def tampering_reconcile(matrix, repo_root, **kwargs):
            smoke_path = (
                Path(repo_root) / ".signalos" / "product" / "proof"
                / "runtime" / "smoke.json"
            )
            snapshot = _read_json(smoke_path)["workspace_snapshot"]
            assert snapshot["files"], "expected a non-empty proof snapshot"
            first = sorted(snapshot["files"])[0]
            target = Path(repo_root) / first
            target.write_text(
                target.read_text(encoding="utf-8") + "\n# tampered after proof\n",
                encoding="utf-8",
            )
            return real_reconcile(matrix, repo_root, **kwargs)

        monkeypatch.setattr(
            delivery_mod, "reconcile_acceptance_evidence", tampering_reconcile,
        )

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "stale-product"
            closeout = run_delivery(
                prompt="Build me a task management app with projects and tasks",
                name="stale-product",
                repo_root=repo_root,
                mode="greenfield",
                profile="generic",
                blueprint="auto",
                deploy="none",
                dry_run=True,
            )
            signalos = repo_root / ".signalos"

            freshness = _read_json(
                signalos / "product" / "EVIDENCE_FRESHNESS.json",
            )
            assert freshness["fresh"] is False
            assert freshness["changed"], "tampered file must be listed"
            assert closeout["evidence_freshness"]["fresh"] is False
            stale = [
                lim
                for lim in closeout.get("known_limitations", [])
                if "evidence is stale" in lim
            ]
            assert stale, "stale evidence must be recorded"
            assert freshness["changed"][0] in stale[0]
            # Default gate-compliance is strict -> a ready closure would be
            # downgraded; dry-run never reaches "ready" anyway.
            assert closeout["closure_level"] != "ready"


class TestDeliveryDesignCompetitiveContext:
    def test_design_phase_picks_up_seeded_competitors(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """delivery passes repo_root to build_design_system, so a seeded
        COMPETITORS.json reaches the design-architect prompt (mock LLM)."""
        monkeypatch.setenv("SIGNALOS_DISABLE_LLM", "1")
        monkeypatch.setenv("SIGNALOS_PROOF_TIMEOUT_S", "1")

        valid_design = json.dumps({
            "ui_library": {"name": "shadcn/ui", "version": "latest", "reason": "r"},
            "design_tokens": {
                "color_scheme": "light",
                "primary_color": "#3b82f6",
                "border_radius": "8px",
                "font_family": "Inter, sans-serif",
                "spacing_unit": 8,
                "type_scale": "regular",
            },
            "state_management": {"name": "zustand", "version": "^4.5.0", "reason": "r"},
            "data_layer": {"name": "local", "version": None, "reason": "r"},
            "form_handling": {"name": "native", "version": None, "reason": "r"},
        })

        class _RecordingProvider:
            def __init__(self):
                self.prompts: list[str] = []

            def call(self, prompt: str, model: str):
                self.prompts.append(prompt)
                return valid_design, 1, 1

        provider = _RecordingProvider()
        monkeypatch.setattr(
            harness, "_resolve_provider", lambda name=None: provider,
        )
        monkeypatch.setattr(
            harness,
            "resolve_model",
            lambda model=None, provider_name=None: "test-model",
        )
        # Only the design module sees an "available" LLM; every other stage
        # stays on its SIGNALOS_DISABLE_LLM deterministic path.
        monkeypatch.setattr(
            design_mod, "is_llm_available", lambda root=None: True,
        )

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "competitor-product"
            seed_dir = repo_root / ".signalos" / "product"
            seed_dir.mkdir(parents=True)
            (seed_dir / "COMPETITORS.json").write_text(
                json.dumps({
                    "schema_version": "signalos.competitors.v1",
                    "matrix": [{
                        "url": "https://acme.test",
                        "headline": "Run your team on Acme",
                        "primary_cta": "Start free trial",
                        "feature_count": 3,
                        "has_pricing": "yes",
                    }],
                    "insights": "- differentiate on speed",
                }),
                encoding="utf-8",
            )

            closeout = run_delivery(
                prompt="Build me a task management app with projects and tasks",
                name="competitor-product",
                repo_root=repo_root,
                mode="greenfield",
                profile="react-vite",
                blueprint="auto",
                deploy="none",
                dry_run=True,
            )

            assert closeout["profile"] == "react-vite"
            design_prompts = [
                p for p in provider.prompts if "Competitive Context" in p
            ]
            assert design_prompts, (
                "the seeded COMPETITORS.json never reached the design "
                "architect prompt"
            )
            assert "https://acme.test" in design_prompts[0]
            assert "Run your team on Acme" in design_prompts[0]
