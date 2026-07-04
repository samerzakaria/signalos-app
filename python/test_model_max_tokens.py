# test_model_max_tokens.py
# #30: the per-file completion budget must be clamped to the MODEL's output
# ceiling. _file_max_tokens scales up to 24K for a CRUD .tsx, which gpt-4o
# hard-400s ("max_tokens is too large: 24000; supports at most 16384").
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.agent_dispatch import (
    _file_max_tokens,
    _model_max_output_tokens,
)


def test_gpt4o_cap_is_16384():
    assert _model_max_output_tokens("gpt-4o") == 16384
    assert _model_max_output_tokens("gpt-4o-2024-08-06") == 16384


def test_family_caps():
    assert _model_max_output_tokens("gpt-4-turbo") == 4096
    assert _model_max_output_tokens("claude-opus-4-8") == 64000
    assert _model_max_output_tokens("gemini-2.5-pro") == 32768
    # unknown -> conservative default that no mainstream chat model 400s on
    assert _model_max_output_tokens("some-new-model") == 16384
    assert _model_max_output_tokens(None) == 16384


def test_tsx_budget_exceeds_gpt4o_cap_but_clamp_fits():
    tsx = {"path": "src/components/Patient.tsx", "kind": "source"}
    raw = _file_max_tokens(tsx)
    assert raw == 24000  # the unclamped CRUD-component budget
    clamped = min(raw, _model_max_output_tokens("gpt-4o"))
    assert clamped == 16384  # <= gpt-4o ceiling, no 400


def test_claude_keeps_headroom():
    tsx = {"path": "src/components/Patient.tsx", "kind": "source"}
    clamped = min(_file_max_tokens(tsx), _model_max_output_tokens("claude-opus-4-8"))
    assert clamped == 24000  # Claude tolerates the full budget
