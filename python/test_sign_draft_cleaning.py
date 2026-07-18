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
