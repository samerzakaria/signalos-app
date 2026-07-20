"""Signing owns the ## Signatures block: placeholder DRAFT slots an authoring
agent leaves in it are dropped when the gate is signed, so a validly-signed
gate never reads as "unsigned or draft". Regression: deepseekv4pro run 4."""
from __future__ import annotations

from pathlib import Path

from signalos_lib import sign
from signalos_lib.sign import _strip_draft_signature_entries, _parse_signers


def test_strip_draft_entries_drops_placeholders_keeps_real_and_content() -> None:
    block = (
        "# Expectation Map\n\nreal content above\n\n"
        "## Signatures\n\n```yaml\n"
        "- signer: DRAFT — awaiting PO (client role)\n  role: PO\n  verdict: \n"
        "- signer: Real Founder (PO)\n  role: PO\n  verdict: APPROVED\n"
        "  artifact_hash: " + ("a" * 64) + "\n"
        "```\n"
    )
    out = _strip_draft_signature_entries(block)
    assert "DRAFT" not in out.upper().replace("DRAFT —", "")  # no DRAFT tokens
    assert "DRAFT" not in out
    assert "Real Founder" in out              # real signature preserved
    assert "real content above" in out        # content above untouched
    assert "## Signatures" in out


def test_sign_artifact_cleans_draft_slots_so_gate_reads_signed(tmp_path: Path) -> None:
    art = tmp_path / "EXPECTATION_MAP.md"
    art.write_text(
        "# Expectation Map\n\nreal content above the block\n\n"
        "## Signatures\n\n```yaml\n"
        "- signer: DRAFT — awaiting PO (client role)\n  role: PO\n"
        "- signer: DRAFT — awaiting PO\n  role: PO\n"
        "```\n",
        encoding="utf-8",
    )
    sign.sign_artifact(
        art, signer="Simulated Founder", role="PO", gate="G2", verdict="APPROVED"
    )
    signers, is_draft, _ = _parse_signers(art)
    assert is_draft is False                   # the fix: no DRAFT tokens survive
    assert any("Simulated Founder" in s for s in signers)
    assert "DRAFT" not in art.read_text(encoding="utf-8")


def test_draft_word_in_changelog_below_signatures_is_not_a_draft(tmp_path: Path) -> None:
    """OA-43: is_draft must key off an actual draft SIGNATURE ENTRY, not the bare
    word 'draft' anywhere in the post-`## Signatures` tail. A real APPROVED
    signature plus a revision-history row that legitimately says '(draft)' must
    read as SIGNED, not 'unsigned or draft'. Regression: a funded G0 was refused
    ('strict Gate 0 verification failed after signing: required gate artifacts
    are unsigned or draft') purely because the constitution's changelog table
    below ## Signatures contained '1.0 (draft) ... PO signature pending'."""
    art = tmp_path / "CONSTITUTION.md"
    art.write_text(
        "# Product Constitution\n\n`Version 1.0 · Draft — awaiting PO review`\n\n"
        "real constitution content\n\n"
        "## Signatures\n\n"
        "- signer: Simulated Founder (PO)\n  role: PO\n  verdict: APPROVED\n"
        "  artifact_hash: " + ("a" * 64) + "\n\n"
        "## Change history\n\n"
        "| Date | Version | Note | Status |\n"
        "|------|---------|------|--------|\n"
        "| 2026-04-17 | 1.0 (draft) | initial from onboarding | PO signature pending |\n",
        encoding="utf-8",
    )
    signers, is_draft, _ = _parse_signers(art)
    assert is_draft is False          # the fix: prose/changelog 'draft' is not a draft signature
    assert any("Simulated Founder" in s for s in signers)


def test_genuine_draft_signer_entry_still_flags_draft(tmp_path: Path) -> None:
    """The narrowed is_draft must STILL catch a real draft placeholder entry so a
    half-signed gate cannot pass -- the anti-forgery/fail-closed guarantee holds."""
    art = tmp_path / "BELIEF.md"
    art.write_text(
        "# Belief\n\ncontent\n\n## Signatures\n\n"
        "- signer: DRAFT -- awaiting PO review\n  role: PO\n",
        encoding="utf-8",
    )
    _signers, is_draft, _ = _parse_signers(art)
    assert is_draft is True
