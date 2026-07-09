"""test_wave_engine_judge.py — LLM-judge for scope-drift.

Per WAVE-ENGINE-DESIGN §6 and §13.Q2. The judge wraps the harness
provider stack with a focused drift prompt + lenient JSON parsing.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from conftest import seed_signed_artifact
from signalos_lib.wave_engine_judge import (
    _extract_first_json_object,
    build_llm_judge,
    clear_judge_cache,
    llm_judge_enabled,
)


class _FakeProvider:
    """Test provider that returns a canned response."""

    def __init__(self, response: str):
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def call(self, prompt: str, model: str):
        self.calls.append((prompt, model))
        return self._response, 10, 20


class _RaisingProvider:
    def call(self, prompt: str, model: str):
        raise RuntimeError("provider exploded")


class ExtractFirstJsonObjectTests(unittest.TestCase):
    def test_bare_json_parsed(self):
        result = _extract_first_json_object('{"drifted": true, "confidence": 0.9}')
        self.assertEqual(result, {"drifted": True, "confidence": 0.9})

    def test_prose_wrapped_json_extracted(self):
        result = _extract_first_json_object(
            'Sure! Here you go: {"drifted": false} -- hope that helps.'
        )
        self.assertEqual(result, {"drifted": False})

    def test_nested_braces_handled(self):
        result = _extract_first_json_object(
            'noise {"outer": {"inner": 1}, "drifted": true} more noise'
        )
        self.assertEqual(result, {"outer": {"inner": 1}, "drifted": True})

    def test_no_braces_returns_none(self):
        self.assertIsNone(_extract_first_json_object("just prose"))

    def test_malformed_json_returns_none(self):
        self.assertIsNone(_extract_first_json_object("{not valid"))

    def test_empty_input(self):
        self.assertIsNone(_extract_first_json_object(""))


class JudgeContractTests(unittest.TestCase):
    def setUp(self):
        # Cache is module-level; clear so tests don't leak verdicts
        # between each other.
        clear_judge_cache()

    def test_bare_json_response_yields_drifted_true(self):
        provider = _FakeProvider(
            '{"drifted": true, "confidence": 0.9, "reasoning": "domain shift"}'
        )
        judge = build_llm_judge(provider=provider, model="test-model")
        result = judge("old soul text", "new request text")
        self.assertTrue(result["drifted"])
        self.assertEqual(result["confidence"], 0.9)
        self.assertEqual(result["reasoning"], "domain shift")

    def test_drifted_false_round_trips(self):
        provider = _FakeProvider('{"drifted": false, "confidence": 0.85}')
        judge = build_llm_judge(provider=provider, model="x")
        result = judge("soul", "request")
        self.assertFalse(result["drifted"])
        self.assertEqual(result["confidence"], 0.85)

    def test_prose_wrapped_json_still_parses(self):
        provider = _FakeProvider(
            'Sure, here:\n\n{"drifted": true, "confidence": 0.7}\n\nLet me know.'
        )
        judge = build_llm_judge(provider=provider, model="x")
        result = judge("soul", "request")
        self.assertTrue(result["drifted"])

    def test_unparseable_response_returns_no_drift_with_low_confidence(self):
        provider = _FakeProvider("I don't know")
        judge = build_llm_judge(provider=provider, model="x")
        result = judge("soul", "request")
        self.assertFalse(result["drifted"])
        self.assertLessEqual(result["confidence"], 0.4)
        self.assertIn("unparseable", result["reasoning"])

    def test_provider_exception_falls_back_safely(self):
        judge = build_llm_judge(provider=_RaisingProvider(), model="x")
        result = judge("soul", "request")
        self.assertFalse(result["drifted"])
        self.assertLessEqual(result["confidence"], 0.4)
        self.assertIn("llm-call-failed", result["reasoning"])

    def test_string_drifted_yes_is_truthy(self):
        provider = _FakeProvider('{"drifted": "yes", "confidence": 0.6}')
        judge = build_llm_judge(provider=provider, model="x")
        self.assertTrue(judge("s", "r")["drifted"])

    def test_string_drifted_no_is_falsy(self):
        provider = _FakeProvider('{"drifted": "no", "confidence": 0.6}')
        judge = build_llm_judge(provider=provider, model="x")
        self.assertFalse(judge("s", "r")["drifted"])

    def test_confidence_out_of_range_clamped(self):
        provider = _FakeProvider('{"drifted": true, "confidence": 1.5}')
        judge = build_llm_judge(provider=provider, model="x")
        result = judge("s", "r")
        self.assertEqual(result["confidence"], 1.0)

    def test_string_confidence_coerced_to_float(self):
        provider = _FakeProvider('{"drifted": true, "confidence": "0.75"}')
        judge = build_llm_judge(provider=provider, model="x")
        self.assertEqual(judge("s", "r")["confidence"], 0.75)

    def test_missing_confidence_uses_safe_default(self):
        provider = _FakeProvider('{"drifted": true}')
        judge = build_llm_judge(provider=provider, model="x")
        self.assertEqual(judge("s", "r")["confidence"], 0.5)

    def test_prompt_contains_both_soul_and_request(self):
        provider = _FakeProvider('{"drifted": false}')
        judge = build_llm_judge(provider=provider, model="x")
        judge("Customer onboarding tool", "Build a customer dashboard")
        prompt_sent = provider.calls[0][0]
        self.assertIn("Customer onboarding tool", prompt_sent)
        self.assertIn("Build a customer dashboard", prompt_sent)

    def test_reasoning_truncated_to_200_chars(self):
        long_reason = "x" * 500
        provider = _FakeProvider(
            '{"drifted": true, "confidence": 0.9, "reasoning": "' + long_reason + '"}'
        )
        judge = build_llm_judge(provider=provider, model="x")
        result = judge("s", "r")
        self.assertEqual(len(result["reasoning"]), 200)


class FeatureFlagTests(unittest.TestCase):
    """Per WAVE-ENGINE-DESIGN §13.Q2 — RESOLVED: accepted, with caching.
    The judge is on by default; SIGNALOS_LLM_JUDGE_DRIFT=0 (or false/no/off)
    disables it."""

    def test_enabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SIGNALOS_LLM_JUDGE_DRIFT", None)
            self.assertTrue(llm_judge_enabled())

    def test_enabled_when_env_set_to_1(self):
        with mock.patch.dict(os.environ, {"SIGNALOS_LLM_JUDGE_DRIFT": "1"}):
            self.assertTrue(llm_judge_enabled())

    def test_disabled_when_env_set_to_disable_values(self):
        for v in ("0", "false", "no", "off", ""):
            with mock.patch.dict(os.environ, {"SIGNALOS_LLM_JUDGE_DRIFT": v}):
                self.assertFalse(llm_judge_enabled(), f"value {v!r} should disable")

    def test_enabled_for_arbitrary_truthy_values(self):
        for v in ("yes", "true", "on", "ENABLE"):
            with mock.patch.dict(os.environ, {"SIGNALOS_LLM_JUDGE_DRIFT": v}):
                self.assertTrue(llm_judge_enabled(), f"value {v!r} should enable")


class CacheTests(unittest.TestCase):
    """§13.Q2 — verdict cached per (soul, request) pair so we don't
    re-pay per turn."""

    def setUp(self):
        clear_judge_cache()

    def test_repeated_call_with_same_inputs_hits_cache(self):
        provider = _FakeProvider('{"drifted": true, "confidence": 0.9}')
        judge = build_llm_judge(provider=provider, model="x")
        r1 = judge("same soul body", "same request body")
        r2 = judge("same soul body", "same request body")
        # Provider was called exactly once for the two judge() calls.
        self.assertEqual(len(provider.calls), 1)
        # Second call's verdict is flagged as cached.
        self.assertTrue(r2.get("cached"))
        self.assertFalse(r1.get("cached"))
        # Verdict shape preserved.
        self.assertEqual(r1["drifted"], r2["drifted"])
        self.assertEqual(r1["confidence"], r2["confidence"])

    def test_different_inputs_each_hit_provider(self):
        provider = _FakeProvider('{"drifted": false, "confidence": 0.5}')
        judge = build_llm_judge(provider=provider, model="x")
        judge("soul A", "request A")
        judge("soul A", "request B")  # different request
        judge("soul B", "request A")  # different soul
        self.assertEqual(len(provider.calls), 3)

    def test_failed_calls_not_cached(self):
        """A provider exception must not poison the cache — the next
        call gets a fresh attempt at the provider."""
        good = _FakeProvider('{"drifted": true, "confidence": 0.9}')
        bad = _RaisingProvider()
        bad_judge = build_llm_judge(provider=bad, model="x")
        bad_judge("s", "r")  # returns the llm-call-failed fallback
        good_judge = build_llm_judge(provider=good, model="x")
        good_result = good_judge("s", "r")
        # The good judge gets to call the provider — the failed
        # earlier verdict was not cached.
        self.assertEqual(len(good.calls), 1)
        self.assertTrue(good_result["drifted"])
        self.assertFalse(good_result.get("cached"))

    def test_unparseable_responses_not_cached(self):
        provider = _FakeProvider("nonsense not json")
        judge = build_llm_judge(provider=provider, model="x")
        judge("s", "r")
        judge("s", "r")  # same inputs — but the first call's
        # response was unparseable, so nothing was cached.
        self.assertEqual(len(provider.calls), 2)

    def test_clear_judge_cache_drops_entries(self):
        provider = _FakeProvider('{"drifted": false}')
        judge = build_llm_judge(provider=provider, model="x")
        judge("s", "r")
        clear_judge_cache()
        judge("s", "r")
        self.assertEqual(len(provider.calls), 2)


class IntegrationWithDetectScopeDriftTests(unittest.TestCase):
    """End-to-end: detect_scope_drift uses the judge for ambiguous-zone."""

    def setUp(self):
        clear_judge_cache()

    def test_judge_resolves_ambiguous_zone(self):
        import tempfile
        from signalos_lib.wave_engine import detect_scope_drift

        # Soul + request engineered to land in the 0.1-0.4 overlap zone.
        soul = "Personal helper application customer onboarding workflows daily"
        root = Path(tempfile.mkdtemp(prefix="signalos-judge-int-")).resolve()
        (root / ".signalos").mkdir()
        # Gate detection is signature-based (fail-closed): the Soul must be
        # signed for drift detection to see a passed G0.
        seed_signed_artifact(
            root,
            "core/governance/Governance/SOUL-DOCUMENT.md",
            "G0",
            soul + "\nOwner: PO.\nReviewer: lead.\nReady when signed.\n",
        )
        request = "Personal helper but inventory tracking warehouse manifests forklift"

        # Without judge → "ambiguous"
        result = detect_scope_drift(root, request)
        self.assertEqual(result["method"], "ambiguous")

        # With judge → "llm-judged"
        provider = _FakeProvider(
            '{"drifted": true, "confidence": 0.85, "reasoning": "different domain"}'
        )
        judge = build_llm_judge(provider=provider, model="x")
        result2 = detect_scope_drift(root, request, llm_judge=judge)
        self.assertEqual(result2["method"], "llm-judged")
        self.assertTrue(result2["drifted"])


if __name__ == "__main__":
    unittest.main()
