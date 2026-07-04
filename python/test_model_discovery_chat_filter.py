# test_model_discovery_chat_filter.py
# #28: model discovery ("newest flagship") must skip non-CHAT modalities. Some
# providers (OpenAI) ship gpt-realtime-*/gpt-audio-*/gpt-4o-transcribe as the
# NEWEST ids by date; picking them 404s on /chat/completions ("not a chat
# model"). pick_best_model must exclude them and fall back to a chat flagship.
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.harness import pick_best_model, _is_non_flagship


def test_realtime_and_audio_are_excluded():
    ids = [
        "gpt-4o", "gpt-4.1",
        "gpt-realtime-2025-08-28",     # newest by date, but audio/realtime
        "gpt-audio-2025-08-01",
        "gpt-4o-transcribe",
    ]
    best = pick_best_model(ids)
    assert best in ("gpt-4o", "gpt-4.1"), best
    assert "realtime" not in best and "audio" not in best and "transcribe" not in best


def test_non_flagship_flags_modalities():
    for bad in ("gpt-realtime-2025-08-28", "gpt-audio-preview", "gpt-4o-transcribe",
                "tts-1", "whisper-1", "text-embedding-3-large", "gpt-4o-mini"):
        assert _is_non_flagship(bad), bad
    for good in ("gpt-4o", "gpt-4.1", "claude-opus-4-8", "gemini-2.5-pro"):
        assert not _is_non_flagship(good), good


def test_falls_back_to_newest_when_all_filtered():
    # If EVERYTHING is a non-flagship, still return something (newest), not None.
    ids = ["gpt-4o-mini", "gpt-4o-nano"]
    assert pick_best_model(ids) in ids
