"""Adversarial contracts for G4 attribution and G5 verify-before-push.

These tests exercise the real orchestration seams without a provider/network.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_lib.sign as sign_mod
from signalos_lib.commands.sign import main as sign_command
from signalos_lib.product.enforcement_state import StaticEnforcementProvider
from signalos_lib.product.gate_orchestrator import GateOrchestrator, resume_delivery
from signalos_lib.product.release_tree import (
    ReleaseTreeError,
    commit_control_tree,
    commit_release_tree,
    tree_digest,
    workspace_control_tree,
    workspace_release_tree,
)


class _Adapter:
    supports_tool_calls = True


class _BuildResult:
    def __init__(self, status: str = "completed", *, wrote_no_files: bool = False):
        self.status = status
        self.wrote_no_files = wrote_no_files
        self.tool_calls_made = 1
        self.error = None if status == "completed" else f"{status} outcome"


def _make_directory_redirect(link: Path, target: Path) -> bool:
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        if os.name != "nt":
            return False
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    return created.returncode == 0 and link.exists()


def _fake_sign(*_args, **_kwargs):
    return ["fake.md"]


_CURRENT_G4_PROOF = {
    "ok": True,
    "attribution": {"release_digest": "verified-release-tree"},
}


def _strict_ok(*_args, **_kwargs):
    return SimpleNamespace(signed=True, reasons=[])


def _make_g4(root: Path, *, run_id: str | None = None) -> GateOrchestrator:
    orch = GateOrchestrator(
        root,
        _Adapter(),
        lambda _event: None,
        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
        sign_fn=_fake_sign,
        run_id=run_id,
        prompt="build the product",
    )
    # Keep these contract tests independent of stack auto-detection.
    orch._product_source_dir = lambda: "src"
    return orch


def _write_source(root: Path, body: str = "def value():\n    return 1\n") -> Path:
    path = root / "src" / "feature.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _verify_green(orch: GateOrchestrator, result: _BuildResult) -> dict:
    with mock.patch.object(orch, "_unwired_modules", return_value=[]), \
         mock.patch.object(orch, "_verify_ux_acceptance",
                           return_value={"ok": True, "status": "passed"}), \
         mock.patch("signalos_lib.product.stacks.detect_profile",
                    return_value="generic"), \
         mock.patch("signalos_lib.product.validation.build_validation_plan",
                    return_value={"can_validate_build": True,
                                  "can_validate_tests": True,
                                  "profile": "generic"}), \
         mock.patch("signalos_lib.product.validation.run_validation",
                    return_value={"results": {
                        "build": {"status": "passed"},
                        "test": {"status": "passed"},
                    }}):
        return orch._verify_g4_build(result)


class TestG4CurrentRunAttribution(unittest.TestCase):
    def test_tracked_dist_output_is_bound_and_tamper_invalidates_g4(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "SignalOS Test"],
                           cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@signalos.local"],
                           cwd=root, check=True)
            dist = root / "dist" / "app.js"
            dist.parent.mkdir(parents=True)
            dist.write_text("window.APP = 'verified';\n", encoding="utf-8")
            subprocess.run(["git", "add", "dist/app.js"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "tracked build output"],
                           cwd=root, check=True)

            orch = _make_g4(root)
            orch._prepare_g4_attribution()
            _write_source(root)
            verified = _verify_green(orch, _BuildResult())
            self.assertTrue(verified["ok"], verified)
            evidence = json.loads(
                orch._g4_attribution_path().read_text(encoding="utf-8")
            )
            self.assertIn("dist/app.js", evidence["release_tree"])

            dist.write_text("window.APP = 'tampered';\n", encoding="utf-8")
            current = orch._g4_verification_for_current_tree()
            self.assertFalse(current["ok"])
            self.assertIn("shippable workspace files changed", current["reason"])

    def test_framework_scaffold_alone_cannot_be_the_g4_source_delta(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            orch = _make_g4(root)

            def scaffold_only():
                _write_source(root)  # deliberately looks like real app source

            completed = _BuildResult()
            with mock.patch.object(orch, "_scaffold_shell_if_greenfield",
                                   side_effect=scaffold_only), \
                 mock.patch(
                     "signalos_lib.product.subagent_build.run_subagent_driven_build",
                     return_value=completed,
                 ):
                returned = orch._execute_build_gate("G4", "build", [])

            self.assertIs(returned, completed)
            self.assertFalse(orch._g4_verify["ok"])
            self.assertIn("zero meaningful",
                          orch._g4_verify["reason"])
            evidence = json.loads(
                orch._g4_attribution_path().read_text(encoding="utf-8"))
            self.assertIn("src/feature.py", evidence["baseline_tree"])
            self.assertEqual(evidence["changed_product_source"], [])

    def test_stale_preexisting_green_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_source(root)
            orch = _make_g4(root)
            orch._prepare_g4_attribution()

            result = _verify_green(orch, _BuildResult())

            self.assertFalse(result["ok"])
            self.assertIn("zero meaningful", result["reason"])
            evidence = json.loads(orch._g4_attribution_path().read_text(encoding="utf-8"))
            self.assertEqual(evidence["phase"], "refused")
            self.assertEqual(evidence["changed_product_source"], [])

    def test_error_or_stall_is_refused_even_if_source_changed(self):
        for status in ("error", "stalled_no_tool", "budget_exhausted"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                orch = _make_g4(root)
                orch._prepare_g4_attribution()
                _write_source(root)

                result = _verify_green(orch, _BuildResult(status))

                self.assertFalse(result["ok"])
                self.assertIn(status, result["reason"])

    def test_reported_no_write_is_refused_even_if_disk_changed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            orch = _make_g4(root)
            orch._prepare_g4_attribution()
            _write_source(root)

            result = _verify_green(orch, _BuildResult(wrote_no_files=True))

            self.assertFalse(result["ok"])
            self.assertIn("wrote no files", result["reason"])

    def test_whitespace_and_comment_only_source_edit_is_refused_as_trivial(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = _write_source(root)
            orch = _make_g4(root)
            orch._prepare_g4_attribution()
            source.write_text(
                "# commentary only\n\n"
                "def value():\n"
                "        return    1\n",
                encoding="utf-8",
            )

            result = _verify_green(orch, _BuildResult())

            self.assertFalse(result["ok"])
            self.assertIn("zero meaningful", result["reason"])
            self.assertEqual(result["changed_product_source"], ["src/feature.py"])
            self.assertEqual(result["meaningful_product_source_change"], [])

    def test_deletion_only_delta_is_not_delivery_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            keep = root / "src" / "keep.py"
            remove = root / "src" / "remove.py"
            keep.parent.mkdir(parents=True, exist_ok=True)
            keep.write_text("def keep():\n    return True\n", encoding="utf-8")
            remove.write_text("def remove():\n    return True\n", encoding="utf-8")
            orch = _make_g4(root)
            orch._prepare_g4_attribution()
            remove.unlink()

            result = _verify_green(orch, _BuildResult())

            self.assertFalse(result["ok"])
            self.assertIn("deletion-only", result["reason"])
            self.assertEqual(result["meaningful_product_source_change"],
                             ["src/remove.py"])
            self.assertEqual(result["meaningful_written_product_source"], [])

    def test_current_source_change_and_green_validation_is_verified(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            orch = _make_g4(root)
            orch._prepare_g4_attribution()
            _write_source(root)

            result = _verify_green(orch, _BuildResult())

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["attribution"]["changed_product_source"],
                             ["src/feature.py"])
            self.assertTrue(orch._g4_verification_for_current_tree()["ok"])

    def test_interrupted_attempt_reuses_persisted_baseline_on_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            first = _make_g4(root, run_id="resume-g4")
            baseline = first._prepare_g4_attribution()
            first._persist()
            _write_source(root)  # landed before the simulated process crash

            resumed = resume_delivery(
                root, "resume-g4", _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=_fake_sign,
            )
            resumed._product_source_dir = lambda: "src"
            reused = resumed._prepare_g4_attribution()

            self.assertEqual(reused["attempt"], baseline["attempt"])
            self.assertEqual(reused["baseline_digest"], baseline["baseline_digest"])
            result = _verify_green(resumed, _BuildResult())
            self.assertTrue(result["ok"], result)

            # A second process can restore the verified decision, but only while
            # the product tree still matches the persisted post-build digest.
            resumed._persist()
            resumed._release_delivery_lock()
            again = resume_delivery(
                root, "resume-g4", _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=_fake_sign,
            )
            again._product_source_dir = lambda: "src"
            self.assertTrue(again._g4_verification_for_current_tree()["ok"])


def _seed_release(root: Path) -> Path:
    source = _write_source(root)
    quality = root / "core" / "governance" / "QUALITY_CHECK.md"
    quality.parent.mkdir(parents=True, exist_ok=True)
    quality.write_text("# Quality Check\n\nAll release checks passed.\n",
                       encoding="utf-8")
    (root / ".git").mkdir(exist_ok=True)
    return source


def _make_g5(root: Path, events: list[dict]) -> GateOrchestrator:
    orch = GateOrchestrator(
        root,
        _Adapter(),
        events.append,
        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
        prompt="ship the product",
        finalize_closeout=False,
    )
    orch._product_source_dir = lambda: "src"
    orch.state.current_gate = "G5"
    orch.state.status = "awaiting-verdict"
    orch.state.signed = ["G0", "G1", "G2", "G3", "G4"]
    return orch


class TestG5VerifyBeforeCommitPush(unittest.TestCase):
    def test_release_errors_redact_remote_and_environment_credentials(self):
        remote_secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        environment_secret = "openrouter-secret-value-123456"
        detail = (
            "fatal: repository 'https://user:"
            f"{remote_secret}@example.invalid/repo?access_token={remote_secret}' "
            f"failed with {environment_secret}"
        )
        with mock.patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": environment_secret},
            clear=False,
        ):
            redacted = sign_mod._redact_release_detail(detail)
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                sign_mod._record_g5_push_outcome(root, "failed", detail)
                durable = (
                    root / ".signalos" / "AUDIT_TRAIL.jsonl"
                ).read_text(encoding="utf-8")

        self.assertNotIn(remote_secret, redacted)
        self.assertNotIn(environment_secret, redacted)
        self.assertNotIn(remote_secret, durable)
        self.assertNotIn(environment_secret, durable)
        self.assertIn("[REDACTED]", redacted)

    def test_release_tree_refuses_a_tracked_path_through_redirected_parent(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root = base / "repo"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "src").mkdir()
            (root / "src" / "app.txt").write_text("approved\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "src/app.txt"], cwd=root, check=True)
            subprocess.run(
                [
                    "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-q", "-m", "seed",
                ],
                cwd=root,
                check=True,
            )
            (root / "src" / "app.txt").unlink()
            (root / "src").rmdir()
            (outside / "app.txt").write_text("external secret\n", encoding="utf-8")
            if not _make_directory_redirect(root / "src", outside):
                self.skipTest("directory redirects are unavailable on this platform")

            with self.assertRaisesRegex(ReleaseTreeError, "symlink|junction|outside"):
                workspace_release_tree(root)

    def test_release_commit_refuses_governance_artifact_through_redirected_parent(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root = base / "repo"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "core").mkdir()
            (root / "core" / "governance").mkdir()
            (outside / "QUALITY_CHECK.md").write_text(
                "# external authority\n", encoding="utf-8",
            )
            (root / "core" / "governance").rmdir()
            if not _make_directory_redirect(root / "core" / "governance", outside):
                self.skipTest("directory redirects are unavailable on this platform")

            with self.assertRaisesRegex(ReleaseTreeError, "symlink|junction|outside"):
                sign_mod._release_governance_paths(root, "default")

    def test_forged_release_trailers_cannot_reuse_or_push_wrong_tree(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "SignalOS Test"],
                           cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@signalos.local"],
                           cwd=root, check=True)
            source = root / "src" / "App.tsx"
            quality = root / "core" / "governance" / "QUALITY_CHECK.md"
            source.parent.mkdir(parents=True)
            quality.parent.mkdir(parents=True)
            source.write_text("export const value = 'evil';\n", encoding="utf-8")
            quality.write_text("# Quality\n\nUnsigned.\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root,
                           check=True)

            # Compute the genuine G4 product receipt from the approved bytes.
            source.write_text("export const value = 'approved';\n", encoding="utf-8")
            quality.write_text(
                "# Quality\n\nApproved.\n\n## Signatures\nQA signed.\n",
                encoding="utf-8",
            )
            release_digest = tree_digest(workspace_release_tree(root))
            approved_control = workspace_control_tree(root)
            release_id = "default:forged-repro"

            # Put malicious bytes in HEAD and forge all textual receipt fields.
            source.write_text("export const value = 'evil';\n", encoding="utf-8")
            quality.write_text("# Quality\n\nUnsigned.\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            tree_oid = subprocess.run(
                ["git", "write-tree"], cwd=root, capture_output=True, text=True,
                check=True,
            ).stdout.strip()
            forged_message = (
                "forged release\n\n"
                f"SignalOS-Release-ID: {release_id}\n"
                f"SignalOS-Release-Tree: {release_digest}\n"
                f"SignalOS-Release-Commit-Tree: {tree_oid}\n"
            )
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", forged_message],
                           cwd=root, check=True)
            forged_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                text=True, check=True,
            ).stdout.strip()

            # Approved worktree bytes cannot make forged HEAD reusable.
            source.write_text("export const value = 'approved';\n", encoding="utf-8")
            quality.write_text(
                "# Quality\n\nApproved.\n\n## Signatures\nQA signed.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                sign_mod._release_commit_at_head(root, release_id, release_digest),
                "",
            )
            outcome, detail, committed_sha = sign_mod._stage_and_commit_for_release(
                root, release_id=release_id, release_digest=release_digest,
            )
            self.assertEqual(outcome, "committed", detail)
            self.assertNotEqual(committed_sha, forged_sha)
            self.assertEqual(
                tree_digest(commit_release_tree(root, committed_sha)),
                release_digest,
            )
            self.assertEqual(commit_control_tree(root, committed_sha), approved_control)
            self.assertEqual(
                sign_mod._release_commit_at_head(root, release_id, release_digest),
                committed_sha,
            )
            committed_quality = subprocess.run(
                ["git", "show", f"{committed_sha}:core/governance/QUALITY_CHECK.md"],
                cwd=root, capture_output=True, text=True, check=True,
            ).stdout
            self.assertIn("QA signed", committed_quality)

    def test_release_commit_excludes_tracked_signalos_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "SignalOS Test"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@signalos.local"],
                cwd=root, check=True,
            )
            source = root / "src" / "App.tsx"
            quality = root / "core" / "governance" / "QUALITY_CHECK.md"
            leaked_state = root / ".signalos" / "identity" / "credential.txt"
            source.parent.mkdir(parents=True)
            quality.parent.mkdir(parents=True)
            leaked_state.parent.mkdir(parents=True)
            source.write_text("export const ready = true;\n", encoding="utf-8")
            quality.write_text("# Quality\n\nApproved.\n", encoding="utf-8")
            leaked_state.write_text("must never ship\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True)

            release_digest = tree_digest(workspace_release_tree(root))
            outcome, detail, sha = sign_mod._stage_and_commit_for_release(
                root,
                release_id="default:no-control-state",
                release_digest=release_digest,
            )

            self.assertEqual(outcome, "committed", detail)
            committed_paths = sign_mod._git_tree_paths(root, sha)
            self.assertNotIn(".signalos/identity/credential.txt", committed_paths)
            self.assertEqual(
                committed_paths,
                set(commit_release_tree(root, sha))
                | set(commit_control_tree(
                    root, sha, sign_mod._release_governance_paths(root, "default"),
                )),
            )
            self.assertEqual(
                sign_mod._release_commit_at_head(
                    root, "default:no-control-state", release_digest,
                ),
                sha,
            )

    def test_successful_push_receipt_is_read_back_from_exact_remote_ref(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root = base / "repo"
            remote = base / "origin.git"
            root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "config", "user.name", "SignalOS Test"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@signalos.local"],
                cwd=root,
                check=True,
            )
            source = root / "src" / "App.tsx"
            quality = root / "core" / "governance" / "QUALITY_CHECK.md"
            source.parent.mkdir(parents=True)
            quality.parent.mkdir(parents=True)
            source.write_text("export const version = 1;\n", encoding="utf-8")
            quality.write_text("# Quality\n\nApproved.\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)

            source.write_text("export const version = 2;\n", encoding="utf-8")
            release_digest = tree_digest(workspace_release_tree(root))
            seal = sign_mod._auto_seal_on_g5(root, project_id="default")
            result = sign_mod._auto_push_on_g5(
                root,
                release_id="default:remote-proof",
                release_digest=release_digest,
                project_id="default",
            )

            self.assertIn(result["commit"]["status"], {"committed", "already-committed"})
            proof = result["push"]
            self.assertEqual(proof["status"], "ok", proof)
            self.assertTrue(proof["verified"])
            self.assertEqual(proof["sha"], result["commit"]["sha"])
            self.assertTrue(proof["ref"].startswith("refs/heads/"))
            remote_sha = subprocess.run(
                ["git", "--git-dir", str(remote), "rev-parse", proof["ref"]],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(remote_sha, proof["sha"])

            orch = GateOrchestrator(
                root,
                _Adapter(),
                lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="remote-proof",
                prompt="ship",
                finalize_closeout=False,
            )
            outcome = {"status": "succeeded", "seal": seal, **result}
            self.assertEqual(
                orch._release_success_reasons(outcome, release_digest),
                [],
            )

            # A valid historical receipt must not remain trusted after origin
            # is replaced.  Persist the successful outcome exactly as a
            # completed delivery would, then point origin at a different bare
            # repository before resuming it.
            orch.state.current_gate = "G5"
            orch.state.status = "complete"
            orch.state.signed = list(f"G{i}" for i in range(6))
            orch.state.release_evidence["release_finalization"] = {
                "schema_version": "signalos.release-finalization.v1",
                "status": "succeeded",
                "phase": "signed",
                "run_id": "remote-proof",
                "project_id": "default",
                "profile": "benchmark",
                "release_digest": release_digest,
                "outcome": outcome,
            }
            orch._persist()
            replacement = base / "replacement-origin-secret.git"
            subprocess.run(["git", "init", "--bare", "-q", str(replacement)],
                           check=True)
            subprocess.run(["git", "remote", "set-url", "origin", str(replacement)],
                           cwd=root, check=True)
            current_truth = {
                "ok": True,
                "reasons": [],
                "release_digest": release_digest,
                "checked_at": "2026-07-14T00:00:00Z",
            }
            with mock.patch.object(
                    GateOrchestrator, "_verify_completed_g5_release",
                    return_value=current_truth), \
                 mock.patch.object(sign_mod, "finalize_g5_release") as finalized:
                resumed = resume_delivery(
                    root, "remote-proof", _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    recover_pending_release=True,
                )

            finalized.assert_not_called()
            verification = resumed.state.release_evidence["release_verification"]
            self.assertFalse(verification["ok"])
            joined_reasons = "\n".join(verification["reasons"])
            self.assertIn("origin remote URL no longer matches", joined_reasons)
            self.assertIn("origin remote ref no longer matches", joined_reasons)
            self.assertNotIn("replacement-origin-secret", joined_reasons)

            (root / seal["path"]).write_text("tampered\n", encoding="utf-8")
            self.assertTrue(any(
                "seal receipt hash mismatch" in reason
                for reason in orch._release_success_reasons(outcome, release_digest)
            ))

    def test_funded_push_uses_attested_path_even_if_origin_changes_after_lookup(self):
        from signalos_lib import git_remote

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root = base / "repo"
            remote = base / "origin.git"
            replacement = base / "replacement.git"
            hooks = base / "disabled-hooks"
            root.mkdir()
            hooks.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(
                ["git", "init", "--bare", "-q", str(replacement)], check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "SignalOS Test"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@signalos.local"],
                cwd=root,
                check=True,
            )
            source = root / "src" / "App.tsx"
            source.parent.mkdir(parents=True)
            source.write_text("export const funded = true;\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "seed"], cwd=root, check=True
            )
            subprocess.run(
                ["git", "remote", "add", "origin", str(remote)],
                cwd=root,
                check=True,
            )
            source.write_text("export const funded = 'sealed';\n", encoding="utf-8")
            release_digest = tree_digest(workspace_release_tree(root))
            real_lookup = git_remote.ensure_github_remote

            def lookup_then_swap(workspace: Path) -> str | None:
                configured = real_lookup(workspace)
                subprocess.run(
                    ["git", "remote", "set-url", "origin", str(replacement)],
                    cwd=root,
                    check=True,
                )
                return configured

            funded_env = {
                "SIGNALOS_SANDBOX_PROFILE": "funded",
                "SIGNALOS_FUNDED_GIT_HOOKS_DIR": str(hooks),
                "SIGNALOS_FUNDED_EXPECTED_GIT_REMOTE": str(remote),
                "OPENROUTER_API_KEY": "must-not-reach-git",
                "SIGNALOS_DEPENDENCY_ATTESTATION_SECRET_KEY": "ab" * 32,
            }
            with mock.patch.dict(os.environ, funded_env, clear=False), mock.patch.object(
                git_remote,
                "ensure_github_remote",
                side_effect=lookup_then_swap,
            ):
                result = sign_mod._auto_push_on_g5(
                    root,
                    release_id="default:funded-remote-binding",
                    release_digest=release_digest,
                    project_id="default",
                )

            self.assertEqual(result["push"]["status"], "ok", result)
            self.assertTrue(result["push"]["verified"])
            destination = result["push"]["ref"]
            expected_sha = subprocess.run(
                ["git", "--git-dir", str(remote), "rev-parse", destination],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(expected_sha, result["commit"]["sha"])
            replacement_probe = subprocess.run(
                ["git", "--git-dir", str(replacement), "rev-parse", destination],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(replacement_probe.returncode, 0)

    def test_release_receipt_rejects_cross_project_seal_path_replay(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            alpha = sign_mod._auto_seal_on_g5(root, project_id="tenant-alpha")
            beta = sign_mod._auto_seal_on_g5(root, project_id="tenant-beta")
            replay = dict(alpha)
            replay["path"] = beta["path"]
            replay["sha256"] = beta["sha256"]
            orch = GateOrchestrator(
                root, _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="cross-project-seal", prompt="ship",
                project_id="tenant-alpha", finalize_closeout=False,
            )
            outcome = {
                "status": "succeeded",
                "seal": replay,
                "commit": {"status": "failed"},
                "push": {"status": "failed"},
            }

            reasons = orch._release_success_reasons(outcome, "release-tree")

            self.assertTrue(any(
                "exact canonical wave/project path" in reason
                for reason in reasons
            ), reasons)

    def test_complete_resume_rejects_artifact_tampered_after_seal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            seal = sign_mod._auto_seal_on_g5(root, project_id="default")
            outcome = {
                "status": "succeeded",
                "seal": seal,
                "commit": {"status": "failed"},
                "push": {"status": "failed"},
            }
            orch = GateOrchestrator(
                root, _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="seal-artifact-tamper", prompt="ship",
                finalize_closeout=False,
            )
            orch.state.current_gate = "G5"
            orch.state.status = "complete"
            orch.state.signed = list(f"G{i}" for i in range(6))
            orch.state.release_evidence["release_finalization"] = {
                "schema_version": "signalos.release-finalization.v1",
                "status": "succeeded",
                "phase": "signed",
                "run_id": "seal-artifact-tamper",
                "project_id": "default",
                "profile": "benchmark",
                "release_digest": "release-tree",
                "outcome": outcome,
            }
            orch._persist()
            quality = root / "core" / "governance" / "QUALITY_CHECK.md"
            quality.write_text("tampered after sealing\n", encoding="utf-8")
            truth = {
                "ok": True,
                "reasons": [],
                "release_digest": "release-tree",
                "checked_at": "2026-07-14T00:00:00Z",
            }

            with mock.patch.object(
                GateOrchestrator, "_verify_completed_g5_release",
                return_value=truth,
            ):
                resumed = resume_delivery(
                    root, "seal-artifact-tamper", _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    recover_pending_release=True,
                )

            reasons = resumed.state.release_evidence["release_verification"]["reasons"]
            self.assertTrue(any(
                "seal semantic verification failed" in reason
                for reason in reasons
            ), reasons)

    def test_real_g5_requires_all_prior_state_and_strict_signatures(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            orch = _make_g5(root, [])
            orch.state.signed.remove("G4")  # corrupted/stale delivery state
            checked: list[str] = []

            def strict(_root, gate, project_id="default"):
                checked.append(gate)
                if gate == "G2":
                    return SimpleNamespace(
                        signed=False,
                        reasons=["tampered artifact hash"],
                    )
                return SimpleNamespace(signed=True, reasons=[])

            with mock.patch.object(sign_mod, "check_gate_signed_strict",
                                   side_effect=strict), \
                 mock.patch.object(orch, "_g4_verification_for_current_tree",
                                   return_value=_CURRENT_G4_PROOF) as g4_proof, \
                 mock.patch.object(sign_mod.subprocess, "run") as run_git, \
                 mock.patch.object(sign_mod, "_auto_seal_on_g5") as seal:
                result = orch.apply_verdict("approve")

            self.assertEqual(result["status"], "release-not-ready")
            self.assertEqual(checked, ["G0", "G1", "G2", "G3", "G4"])
            self.assertTrue(any("tampered artifact hash" in reason
                                for reason in result["reasons"]))
            self.assertTrue(any("G4: missing from this delivery" in reason
                                for reason in result["reasons"]))
            # G4 tree proof is unconditional even when corrupt state omits G4.
            g4_proof.assert_called_once()
            run_git.assert_not_called()
            seal.assert_not_called()

    def test_not_ready_never_signs_commits_pushes_or_seals(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            events: list[dict] = []
            orch = _make_g5(root, events)
            orch.state.waived = ["G1"]
            quality = root / "core" / "governance" / "QUALITY_CHECK.md"
            before = quality.read_text(encoding="utf-8")

            with mock.patch.object(orch, "_g4_verification_for_current_tree",
                                   return_value=_CURRENT_G4_PROOF), \
                 mock.patch.object(orch, "_prior_gate_release_reasons",
                                   return_value=[]), \
                 mock.patch.object(sign_mod.subprocess, "run") as run_git, \
                 mock.patch.object(sign_mod, "_auto_seal_on_g5") as seal:
                result = orch.apply_verdict("approve")

            self.assertEqual(result["status"], "release-not-ready")
            self.assertFalse(result["ready"])
            self.assertNotIn("G5", orch.state.signed)
            self.assertNotEqual(orch.state.status, "complete")
            self.assertEqual(quality.read_text(encoding="utf-8"), before)
            run_git.assert_not_called()
            seal.assert_not_called()
            self.assertFalse(any(e.get("type") == "delivery_complete" for e in events))

    def test_ready_release_verifies_once_then_commits_and_pushes_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            events: list[dict] = []
            orch = _make_g5(root, events)
            real_verify = orch._verify_g5_release
            with mock.patch.object(orch, "_g4_verification_for_current_tree",
                                   return_value=_CURRENT_G4_PROOF), \
                 mock.patch.object(orch, "_prior_gate_release_reasons",
                                   return_value=[]), \
                 mock.patch.object(orch, "_verify_g5_release",
                                   wraps=real_verify) as verify, \
                 mock.patch.object(sign_mod, "check_gate_signed_strict",
                                   side_effect=_strict_ok), \
                 mock.patch.object(sign_mod, "_governed_outcome_proof_reasons",
                                   return_value=[]), \
                 mock.patch.object(orch, "_release_success_reasons",
                                   return_value=[]), \
                 mock.patch.object(sign_mod, "finalize_g5_release", return_value={
                     "status": "succeeded",
                     "seal": {"status": "ok"},
                     "commit": {"status": "committed", "sha": "abc"},
                     "push": {"status": "ok"},
                 }) as finalized:
                result = orch.apply_verdict("approve")

            self.assertEqual(result["status"], "complete")
            self.assertTrue(result["ready"])
            self.assertIn("G5", orch.state.signed)
            self.assertEqual(verify.call_count, 2)
            finalized.assert_called_once_with(
                root,
                release_id=f"default:{orch.state.run_id}",
                release_digest="verified-release-tree",
                project_id="default",
                cancel_check=mock.ANY,
            )

    def test_terminal_state_and_closeout_precede_the_single_release_finalizer(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            orch = _make_g5(root, [])
            order: list[tuple] = []

            def fake_sign(*_args, **_kwargs):
                order.append(("sign", orch.state.status, "G5" in orch.state.signed))
                return ["core/governance/QUALITY_CHECK.md"]

            def fake_persist():
                order.append(("persist", orch.state.status,
                              "G5" in orch.state.signed))

            def fake_closeout(*, ready):
                order.append(("closeout", ready, orch.state.status,
                              "G5" in orch.state.signed))

            orch._sign = fake_sign
            orch._persist = fake_persist
            orch._finalize_closeout = fake_closeout

            def finalized(_root, **_kwargs):
                order.append(("finalizer", orch.state.status,
                              "G5" in orch.state.signed))
                return {
                    "status": "succeeded",
                    "seal": {"status": "ok"},
                    "commit": {"status": "committed", "sha": "abc"},
                    "push": {"status": "ok"},
                }

            with mock.patch.object(orch, "_prior_gate_release_reasons",
                                   return_value=[]), \
                 mock.patch.object(orch, "_g4_verification_for_current_tree",
                                   return_value=_CURRENT_G4_PROOF), \
                 mock.patch.object(sign_mod, "check_gate_signed_strict",
                                   side_effect=_strict_ok), \
                 mock.patch.object(orch, "_release_success_reasons",
                                   return_value=[]), \
                 mock.patch.object(sign_mod, "finalize_g5_release",
                                   side_effect=finalized) as finalizer:
                result = orch.apply_verdict("approve")

            self.assertEqual(result["status"], "complete")
            finalizer.assert_called_once_with(
                root,
                release_id=f"default:{orch.state.run_id}",
                release_digest="verified-release-tree",
                project_id="default",
                cancel_check=mock.ANY,
            )
            names = [item[0] for item in order]
            self.assertEqual(names,
                             ["persist", "sign", "persist", "persist",
                              "closeout", "finalizer", "persist"])
            self.assertEqual(order[0], ("persist", "awaiting-verdict", False))
            self.assertEqual(order[2], ("persist", "complete", True))
            self.assertEqual(order[4], ("closeout", True, "complete", True))
            self.assertEqual(order[5], ("finalizer", "complete", True))

    def test_product_tamper_after_g4_blocks_release_before_git(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            events: list[dict] = []
            orch = GateOrchestrator(
                root, _Adapter(), events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                prompt="build then ship", finalize_closeout=False,
            )
            orch._product_source_dir = lambda: "src"
            orch._prepare_g4_attribution()
            source = _write_source(root)
            verified = _verify_green(orch, _BuildResult())
            self.assertTrue(verified["ok"], verified)

            quality = root / "core" / "governance" / "QUALITY_CHECK.md"
            quality.parent.mkdir(parents=True, exist_ok=True)
            quality.write_text("# Quality Check\n\nPassed.\n", encoding="utf-8")
            (root / ".git").mkdir(exist_ok=True)
            orch.state.current_gate = "G5"
            orch.state.status = "awaiting-verdict"
            orch.state.signed = ["G0", "G1", "G2", "G3", "G4"]
            source.write_text("def value():\n    return 999\n", encoding="utf-8")

            with mock.patch.object(orch, "_prior_gate_release_reasons",
                                   return_value=[]), \
                 mock.patch.object(sign_mod.subprocess, "run") as run_git, \
                 mock.patch.object(sign_mod, "_auto_seal_on_g5") as seal:
                result = orch.apply_verdict("approve")

            self.assertEqual(result["status"], "release-not-ready")
            self.assertTrue(any("changed after G4" in reason
                                for reason in result["reasons"]))
            run_git.assert_not_called()
            seal.assert_not_called()

    def test_config_or_test_tamper_after_g4_blocks_unverified_ship_bytes(self):
        for rel_path, before, after in (
            ("package.json", '{"scripts":{"test":"pytest"}}\n',
             '{"scripts":{"test":"echo bypass"}}\n'),
            ("tests/acceptance.py", "def test_value():\n    assert True\n",
             "def test_value():\n    assert False\n"),
        ):
            with self.subTest(path=rel_path), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                orch = GateOrchestrator(
                    root, _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    prompt="build then ship", finalize_closeout=False,
                )
                orch._product_source_dir = lambda: "src"
                orch._prepare_g4_attribution()
                _write_source(root)
                extra = root / rel_path
                extra.parent.mkdir(parents=True, exist_ok=True)
                extra.write_text(before, encoding="utf-8")
                verified = _verify_green(orch, _BuildResult())
                self.assertTrue(verified["ok"], verified)

                quality = root / "core" / "governance" / "QUALITY_CHECK.md"
                quality.parent.mkdir(parents=True, exist_ok=True)
                quality.write_text("# Quality Check\n\nPassed.\n", encoding="utf-8")
                (root / ".git").mkdir(exist_ok=True)
                orch.state.current_gate = "G5"
                orch.state.status = "awaiting-verdict"
                orch.state.signed = ["G0", "G1", "G2", "G3", "G4"]
                extra.write_text(after, encoding="utf-8")

                with mock.patch.object(orch, "_prior_gate_release_reasons",
                                       return_value=[]), \
                     mock.patch.object(sign_mod.subprocess, "run") as run_git:
                    result = orch.apply_verdict("approve")

                self.assertEqual(result["status"], "release-not-ready")
                self.assertTrue(any("shippable workspace files changed" in reason
                                    for reason in result["reasons"]))
                run_git.assert_not_called()

    def test_g5_approval_with_conditions_is_not_signed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            orch = _make_g5(root, [])
            with mock.patch.object(orch, "_g4_verification_for_current_tree",
                                   return_value=_CURRENT_G4_PROOF), \
                 mock.patch.object(orch, "_prior_gate_release_reasons",
                                   return_value=[]), \
                 mock.patch.object(sign_mod.subprocess, "run") as run_git:
                result = orch.apply_verdict(
                    "approve-with-conditions", "fix the final release note")

            self.assertEqual(result["status"], "release-not-ready")
            self.assertTrue(any("unresolved condition" in reason
                                for reason in result["reasons"]))
            self.assertNotIn("G5", orch.state.signed)
            run_git.assert_not_called()

    def test_pending_complete_resume_revalidates_and_finalizes_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            orch = GateOrchestrator(
                root, _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="pending-release", prompt="ship", finalize_closeout=False,
            )
            orch.state.current_gate = "G5"
            orch.state.status = "complete"
            orch.state.signed = list(f"G{i}" for i in range(6))
            orch.state.release_evidence = {
                "release_verification": {"ok": True, "reasons": []},
                "release_finalization": {
                    "schema_version": "signalos.release-finalization.v1",
                    "status": "pending",
                    "phase": "signed",
                    "run_id": "pending-release",
                    "project_id": "default",
                    "profile": "benchmark",
                    "release_digest": "verified-release-tree",
                    "attempts": 0,
                },
            }
            orch._persist()
            truth = {
                "ok": True,
                "reasons": [],
                "release_digest": "verified-release-tree",
                "checked_at": "2026-07-14T00:00:00Z",
            }
            outcome = {
                "status": "succeeded",
                "seal": {"status": "ok"},
                "commit": {"status": "committed", "sha": "release-sha"},
                "push": {"status": "ok"},
            }
            with mock.patch.object(
                    GateOrchestrator, "_verify_completed_g5_release",
                    return_value=truth) as verified, \
                 mock.patch.object(
                     GateOrchestrator, "_release_success_reasons",
                     return_value=[]), \
                 mock.patch.object(GateOrchestrator, "_finalize_closeout"), \
                 mock.patch.object(sign_mod, "finalize_g5_release",
                                   return_value=outcome) as finalized:
                resumed = resume_delivery(
                    root, "pending-release", _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    recover_pending_release=True,
                )
                again = resume_delivery(
                    root, "pending-release", _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    recover_pending_release=True,
                )

            self.assertGreaterEqual(verified.call_count, 2)
            finalized.assert_called_once()
            self.assertEqual(
                resumed.state.release_evidence["release_finalization"]["status"],
                "succeeded",
            )
            self.assertEqual(
                again.state.release_evidence["release_finalization"]["status"],
                "succeeded",
            )
            persisted = json.loads(
                (root / ".signalos" / "agent-runs" / "pending-release"
                 / "delivery.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                persisted["release_evidence"]["release_finalization"]["status"],
                "succeeded",
            )

    def test_complete_resume_replaces_cached_ready_when_current_truth_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            orch = GateOrchestrator(
                root, _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="tampered-release", prompt="ship", finalize_closeout=False,
            )
            orch.state.current_gate = "G5"
            orch.state.status = "complete"
            orch.state.signed = list(f"G{i}" for i in range(6))
            orch.state.release_evidence = {
                "release_verification": {"ok": True, "reasons": []},
                "release_finalization": {
                    "status": "succeeded",
                    "release_digest": "old-tree",
                },
            }
            orch._persist()
            drift = {
                "ok": False,
                "reasons": ["shippable workspace files changed after G4 verification"],
                "release_digest": "",
                "checked_at": "2026-07-14T00:00:00Z",
            }
            with mock.patch.object(
                    GateOrchestrator, "_verify_completed_g5_release",
                    return_value=drift) as verified, \
                 mock.patch.object(sign_mod, "finalize_g5_release") as finalized:
                resumed = resume_delivery(
                    root, "tampered-release", _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    recover_pending_release=True,
                )

            verified.assert_called_once()
            finalized.assert_not_called()
            current = resumed.state.release_evidence["release_verification"]
            self.assertFalse(current["ok"])
            self.assertIn("changed after G4", current["reasons"][0])

    def test_deferred_finalization_stays_pending_and_next_resume_retries(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            orch = GateOrchestrator(
                root, _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="retry-release", prompt="ship", finalize_closeout=False,
            )
            orch.state.current_gate = "G5"
            orch.state.status = "complete"
            orch.state.signed = list(f"G{i}" for i in range(6))
            orch.state.release_evidence["release_finalization"] = {
                "schema_version": "signalos.release-finalization.v1",
                "status": "pending",
                "phase": "signed",
                "run_id": "retry-release",
                "project_id": "default",
                "profile": "benchmark",
                "release_digest": "verified-release-tree",
                "attempts": 0,
            }
            orch._persist()
            truth = {
                "ok": True,
                "reasons": [],
                "release_digest": "verified-release-tree",
                "checked_at": "2026-07-14T00:00:00Z",
            }
            deferred = {
                "status": "deferred",
                "seal": {"status": "ok"},
                "commit": {"status": "committed", "sha": "release-sha"},
                "push": {"status": "deferred", "reason": "offline"},
            }
            succeeded = {
                "status": "succeeded",
                "seal": {"status": "ok", "reused": True},
                "commit": {"status": "already-committed", "sha": "release-sha"},
                "push": {"status": "ok"},
            }
            with mock.patch.object(
                    GateOrchestrator, "_verify_completed_g5_release",
                    return_value=truth), \
                 mock.patch.object(
                     GateOrchestrator, "_release_success_reasons",
                     return_value=[]), \
                 mock.patch.object(GateOrchestrator, "_finalize_closeout"), \
                 mock.patch.object(
                     sign_mod, "finalize_g5_release",
                     side_effect=[deferred, succeeded],
                 ) as finalized:
                first = resume_delivery(
                    root, "retry-release", _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    recover_pending_release=True,
                )
                self.assertEqual(
                    first.state.release_evidence["release_finalization"]["status"],
                    "pending",
                )
                self.assertEqual(
                    first.state.release_evidence["release_finalization"]
                    ["last_attempt"]["status"],
                    "deferred",
                )
                second = resume_delivery(
                    root, "retry-release", _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    recover_pending_release=True,
                )

            self.assertEqual(finalized.call_count, 2)
            self.assertEqual(
                second.state.release_evidence["release_finalization"]["status"],
                "succeeded",
            )

    def test_tampered_succeeded_marker_fails_closed_without_repush(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            orch = GateOrchestrator(
                root, _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="edited-marker", prompt="ship", finalize_closeout=False,
            )
            orch.state.current_gate = "G5"
            orch.state.status = "complete"
            orch.state.signed = list(f"G{i}" for i in range(6))
            orch.state.release_evidence["release_finalization"] = {
                "schema_version": "signalos.release-finalization.v1",
                "status": "succeeded",
                "phase": "signed",
                "run_id": "some-other-run",
                "project_id": "default",
                "profile": "benchmark",
                "release_digest": "verified-release-tree",
            }
            orch._persist()
            truth = {
                "ok": True,
                "reasons": [],
                "release_digest": "verified-release-tree",
                "checked_at": "2026-07-14T00:00:00Z",
            }
            with mock.patch.object(
                    GateOrchestrator, "_verify_completed_g5_release",
                    return_value=truth), \
                 mock.patch.object(sign_mod, "finalize_g5_release") as finalized:
                resumed = resume_delivery(
                    root, "edited-marker", _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    recover_pending_release=True,
                )

            finalized.assert_not_called()
            verification = resumed.state.release_evidence["release_verification"]
            self.assertFalse(verification["ok"])
            self.assertTrue(any("run_id mismatch" in reason
                                for reason in verification["reasons"]))

    def test_pending_resume_with_failed_truth_never_calls_release_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_release(root)
            orch = GateOrchestrator(
                root, _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="blocked-pending", prompt="ship", finalize_closeout=False,
            )
            orch.state.current_gate = "G5"
            orch.state.status = "complete"
            orch.state.signed = list(f"G{i}" for i in range(6))
            orch.state.release_evidence["release_finalization"] = {
                "schema_version": "signalos.release-finalization.v1",
                "status": "pending",
                "phase": "signed",
                "run_id": "blocked-pending",
                "project_id": "default",
                "profile": "benchmark",
                "release_digest": "old-tree",
            }
            orch._persist()
            drift = {
                "ok": False,
                "reasons": ["G5 signature hash mismatch"],
                "release_digest": "",
                "checked_at": "2026-07-14T00:00:00Z",
            }
            with mock.patch.object(
                    GateOrchestrator, "_verify_completed_g5_release",
                    return_value=drift), \
                 mock.patch.object(sign_mod, "finalize_g5_release") as finalized:
                resumed = resume_delivery(
                    root, "blocked-pending", _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    recover_pending_release=True,
                )

            finalized.assert_not_called()
            self.assertEqual(resumed.state.status, "blocked")
            self.assertEqual(
                resumed.state.release_evidence["release_finalization"]["status"],
                "pending",
            )
            self.assertEqual(
                resumed.state.release_evidence["release_finalization"]
                ["last_attempt"]["status"],
                "failed",
            )
            resumed._release_delivery_lock()

    def test_cancelled_or_stopped_pending_release_is_never_resurrected(self):
        for terminal_status in ("cancelled", "stopped"):
            with self.subTest(status=terminal_status), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                _seed_release(root)
                run_id = f"terminal-{terminal_status}"
                orch = GateOrchestrator(
                    root,
                    _Adapter(),
                    lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    run_id=run_id,
                    prompt="ship",
                    finalize_closeout=False,
                )
                orch.state.current_gate = "G5"
                orch.state.status = terminal_status
                orch.state.signed = list(f"G{i}" for i in range(6))
                orch.state.release_evidence["release_finalization"] = {
                    "schema_version": "signalos.release-finalization.v1",
                    "status": "pending",
                    "phase": "signed",
                    "run_id": run_id,
                    "project_id": "default",
                    "profile": "benchmark",
                    "release_digest": "verified-release-tree",
                }
                orch._persist()

                with mock.patch.object(sign_mod, "finalize_g5_release") as finalized:
                    resumed = resume_delivery(
                        root,
                        run_id,
                        _Adapter(),
                        lambda _event: None,
                        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                        recover_pending_release=True,
                    )

                finalized.assert_not_called()
                self.assertEqual(resumed.state.status, terminal_status)
                self.assertEqual(
                    resumed.state.release_evidence["release_finalization"]["status"],
                    "pending",
                )

    def test_live_project_lock_refuses_nonterminal_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            first = GateOrchestrator(
                root, _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="lock-owner", prompt="one", sign_fn=_fake_sign,
            )
            first.state.status = "awaiting-verdict"
            first._persist()
            self.assertIsNone(first._acquire_delivery_lock())
            second = GateOrchestrator(
                root, _Adapter(), lambda _event: None,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                run_id="lock-contender", prompt="two", sign_fn=_fake_sign,
            )
            second.state.status = "awaiting-verdict"
            second._persist()

            with self.assertRaisesRegex(RuntimeError, "already active"):
                resume_delivery(
                    root, "lock-contender", _Adapter(), lambda _event: None,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    sign_fn=_fake_sign,
                )
            first._release_delivery_lock()

    def test_failed_seal_never_commits_or_pushes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(
                    sign_mod, "_auto_seal_on_g5",
                    return_value={"status": "failed", "reason": "seal broke"}), \
                 mock.patch.object(sign_mod, "_auto_push_on_g5") as git_release:
                outcome = sign_mod.finalize_g5_release(
                    root,
                    release_id="default:test",
                    release_digest="tree",
                    project_id="default",
                )
            self.assertEqual(outcome["status"], "failed")
            git_release.assert_not_called()

    def test_cli_and_core_refuse_raw_g4_g5_signing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for gate, role in (("G4", "PE"), ("G5", "QA")):
                with self.subTest(surface="core", gate=gate), \
                     self.assertRaisesRegex(ValueError, "governed proof"):
                    sign_mod.sign_gate(root, gate, "Sam", role, "APPROVED")
                with self.subTest(surface="cli", gate=gate):
                    code = sign_command([
                        gate,
                        "--repo-root", str(root),
                        "--signer", "Sam",
                        "--role", role,
                        "--verdict", "APPROVED",
                    ])
                    self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
