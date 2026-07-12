# conftest.py
# Hermetic LLM env for the offline test suite.
#
# Many tests assert deterministic "without-LLM" behavior (is_llm_available() is
# False -> deterministic fallbacks, "needs API key" messaging, empty clarifying
# questions, etc.). Those tests are correct in CI (no keys) but FAIL on a
# developer's machine that exports a real ANTHROPIC_API_KEY / OPENAI_API_KEY
# (e.g. from a sourced .env), because the code then sees a live provider.
#
# This autouse fixture clears provider keys by default so every test runs
# hermetically regardless of the ambient shell. A test that genuinely needs a
# provider sets it explicitly via monkeypatch.setenv AFTER this fixture runs, so
# its intent still holds (its setenv overrides the clear).
from __future__ import annotations

import os
from pathlib import Path

import pytest

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "COHERE_API_KEY",
    "TOGETHER_API_KEY",
    "DEEPSEEK_API_KEY",
    "XAI_API_KEY",
    "PERPLEXITY_API_KEY",
    # OpenRouter + the remaining first-class providers the product auto-detects.
    # These were missing, so a repo-root .env carrying OPENROUTER_API_KEY (loaded
    # into os.environ by test_postgres_task_store._load_dotenv at import) leaked
    # past this hermetic guard and made downstream deliveries route to a REAL
    # provider -- non-deterministic, slow, order-dependent failures in the
    # offline suite. Clearing every provider key keeps the unit suite hermetic.
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_BASE",
    "CEREBRAS_API_KEY",
    "DASHSCOPE_API_KEY",
    "SIGNALOS_LLM_PROVIDER",
    "SIGNALOS_LLM_MODEL",
)


# Clear provider keys at conftest IMPORT (pytest loads conftest before it
# imports/collects any test module). Some modules capture a live flag at import
# time -- e.g. `_LIVE = os.getenv("ANTHROPIC_API_KEY") ...` -- which a per-test
# fixture cannot undo. Popping here makes those import-time flags hermetic too,
# so the offline suite never routes to a real (possibly out-of-credit) provider.
# A live-integration run should set keys explicitly and opt in, not rely on the
# ambient shell leaking into the unit suite.
for _var in _PROVIDER_ENV:
    os.environ.pop(_var, None)


@pytest.fixture(autouse=True)
def _hermetic_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_ENV:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Gate-artifact seeding (fail-closed gate detection)
#
# status._detect_gates counts a gate as passed only when it is VALIDLY signed
# per the single strict validator (signalos_lib.sign.is_gate_signed_strict):
# every required artifact exists, carries a non-draft APPROVED signature from an
# authorized role with a valid current hash, is AUDIT-LINKED in the workspace
# AUDIT_TRAIL.jsonl, and is non-revoked. A bare seeded file is honest "drafted,
# not approved" state and does not count; a seeded file with only an in-file
# signature block (no audit row) is likewise NOT signed under the strict
# validator. Fixtures that simulate already-passed gates therefore mirror the
# real `signalos sign` CLI path: write the signature block AND append a matching
# audit row (sign._append_audit) to the workspace-global trail.
# ---------------------------------------------------------------------------

# One plausible signing role per gate, from the gate manifest
# (signalos_lib/gate_artifacts.json): G0 PO+PE, G1 PO, G2 PO, G3 PO/PE,
# G4 PE, G5 QA. Detection needs >=1 valid signer per artifact.
GATE_SEED_ROLES: dict[str, str] = {
    "G0": "PO",
    "G1": "PO",
    "G2": "PO",
    "G3": "PO",
    "G4": "PE",
    "G5": "QA",
}

# >=3 filled lines so status._is_non_template counts the artifact as filled.
_SEED_CONTENT = (
    "Seeded gate artifact content line one.\n"
    "Seeded gate artifact content line two.\n"
    "Seeded gate artifact content line three.\n"
)


def seed_signed_artifact(
    base: Path | str,
    rel_path: str,
    gate: str,
    content: str = _SEED_CONTENT,
    *,
    role: str | None = None,
    signer: str | None = None,
) -> Path:
    """Write a gate artifact under *base*, sign it, AND audit-link it for *gate*.

    Use this (not a bare write_text) whenever a fixture seeds an artifact
    file to simulate a passed gate — gate detection is strict and fail-closed
    (see the section comment above). Returns the artifact Path.

    The AUDIT_TRAIL.jsonl is workspace-global (never per-project), so when *base*
    is a namespaced governance dir (.signalos/projects/<id>/governance) the audit
    row is written to the WORKSPACE root's .signalos/, exactly where the strict
    validator (validate_gate) reads it.
    """
    from signalos_lib.sign import sign_artifact, _append_audit

    gate = gate.upper()
    if role is None:
        role = GATE_SEED_ROLES[gate]
    base = Path(base)
    path = base.joinpath(*rel_path.replace("\\", "/").split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    signer = signer or f"Test {role}"
    sign_artifact(path, signer, role, gate, "APPROVED")
    _append_audit(
        _audit_trail_root(base) / ".signalos" / "AUDIT_TRAIL.jsonl",
        signer, role, gate, rel_path, path, "APPROVED",
    )
    return path


def _audit_trail_root(base: Path) -> Path:
    """Workspace root that owns the global AUDIT_TRAIL.jsonl for *base*.

    For a namespaced governance base (.signalos/projects/<id>/governance) the
    workspace root is four parents up; otherwise *base* IS the workspace root.
    """
    parts = base.parts
    if (
        len(parts) >= 4
        and parts[-1] == "governance"
        and parts[-3] == "projects"
        and parts[-4] == ".signalos"
    ):
        return base.parents[3]
    return base


def seed_signed_gate(
    base: Path | str,
    gate: str,
    *,
    bodies: dict[str, str] | None = None,
    default_content: str = _SEED_CONTENT,
    role: str | None = None,
    signer: str | None = None,
) -> list[Path]:
    """Seed AND sign EVERY required artifact of *gate* — a full-gate seed.

    Gate detection is fail-closed on the WHOLE manifest: status._detect_gates
    and product.preflight both count a gate as passed only when ALL its
    required artifacts exist and carry a signature (sign.check_gate). Seeding
    only the primary artifact therefore does NOT mark a gate passed — this
    mirrors the signing over every manifest artifact of the gate, exactly as
    test_product_preflight._ready_repo does via expected_gate_artifacts.

    *bodies* optionally maps an artifact rel_path OR its manifest label to the
    content to write for that specific artifact (e.g. a Soul body a test asserts
    a snippet from); every other required artifact gets *default_content*.
    Returns the signed Paths in manifest order.
    """
    from signalos_lib.artifacts import expected_gate_artifacts

    gate = gate.upper()
    bodies = bodies or {}
    signed: list[Path] = []
    for artifact in expected_gate_artifacts(gate):
        content = bodies.get(
            artifact.rel_path, bodies.get(artifact.label, default_content)
        )
        signed.append(
            seed_signed_artifact(
                base, artifact.rel_path, gate, content,
                role=role, signer=signer,
            )
        )
    return signed
