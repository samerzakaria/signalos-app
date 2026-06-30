"""LLM-judge adapter for wave-engine scope-drift detection.

Per WAVE-ENGINE-DESIGN §6. The cheap heuristic in
`wave_engine.detect_scope_drift` covers the obvious cases (high overlap
→ no drift; zero overlap → drift). For ambiguous-zone overlap
(0.1 < x < 0.4) the engine defers to a Callable hook with this shape:

    def llm_judge(soul_text: str, user_request: str) -> {
        "drifted": bool,
        "confidence": float,
        "reasoning": str,
    }

`build_llm_judge()` returns a Callable wired to the existing harness
provider stack. The judge invokes one focused LLM turn per ambiguous
verdict. Cost is bounded — the prompt is small (~200 tokens) and only
the ambiguous-zone calls hit it.

When `SIGNALOS_HARNESS_TEST=1` is set the harness returns its canned
response, which the judge parses as a deterministic "no drift" verdict
for proof scenarios.

Design notes:
  - The judge module is separate from `wave_engine` so importing the
    engine doesn't pull the harness LLM SDK chain (anthropic/openai/etc).
    Callers that want the LLM-judge wire it explicitly.
  - JSON parsing is lenient: if the LLM emits prose around the JSON, the
    judge extracts the first balanced `{...}` substring and parses that.
    On total parse failure, the judge returns `drifted=False, confidence=0.3`
    so the engine falls through to "ambiguous" rather than false-positive.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Callable


__all__ = [
    "build_llm_judge",
    "default_llm_judge",
    "clear_judge_cache",
    "llm_judge_enabled",
]


# Per WAVE-ENGINE-DESIGN §13.Q2 — cache the verdict for the duration of
# the wave so we don't re-pay per turn. Keyed by content hash (sha256
# of soul + request) so identical (soul, request) pairs hit the cache
# regardless of which wave_id surfaces them. Module-level cache: lives
# for the IPC process lifetime, which matches "duration of the wave"
# closely enough in practice — wave turns are seconds apart, IPC server
# typically lives for hours.
_JUDGE_CACHE: dict[str, dict[str, Any]] = {}


def _cache_key(soul_text: str, user_request: str) -> str:
    raw = (soul_text or "").strip() + "\x00" + (user_request or "").strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clear_judge_cache() -> None:
    """Drop all cached verdicts. Used by tests; not part of the
    runtime path."""
    _JUDGE_CACHE.clear()


_JUDGE_PROMPT_TEMPLATE = """You judge whether two short product descriptions are about the same project, or have drifted to a different project.

Signed product Soul (the original committed direction):
\"\"\"
{soul}
\"\"\"

New user request (what they just asked for):
\"\"\"
{request}\"\"\"

Respond with EXACTLY a JSON object on a single line, no surrounding prose:
{{"drifted": <true|false>, "confidence": <0.0-1.0>, "reasoning": "<one short sentence>"}}

Use drifted=true ONLY when the new request implies a meaningfully different product (different stakeholders, different domain, different success criteria). Surface-level refinements of the same project — adding a feature, changing styling, fixing a bug — are drifted=false.
"""


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    """Find and parse the first balanced {...} block in *text*. Returns
    None if no parseable object is found."""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        parsed = json.loads(candidate)
                    except (TypeError, ValueError):
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


def build_llm_judge(
    *,
    provider: Any = None,
    model: str | None = None,
) -> Callable[[str, str], dict[str, Any]]:
    """Return a Callable suitable for `detect_scope_drift(..., llm_judge=...)`.

    *provider* defaults to the harness's resolved provider
    (`SIGNALOS_HARNESS_TEST=1` → TestProvider for proof runs).
    *model* defaults to the provider's discovered flagship (no hardcoded id);
    test mode / a TestProvider skips discovery and uses a placeholder.

    The returned Callable wraps the provider's `call()` with a focused
    drift-judging prompt and lenient JSON parsing. It never raises:
    on parse failure / provider error it returns a low-confidence
    "no drift" verdict so the engine falls through to "ambiguous"
    rather than false-positive.
    """
    # Resolve the provider lazily (cheap, no network) so importing this
    # module doesn't load the harness SDK chain unless someone builds a
    # judge. The MODEL is resolved inside the closure, at call time, so
    # building a judge NEVER fails on a missing key / discovery error —
    # the verdict path is the only place that needs a real model and it
    # already degrades gracefully on any failure.
    if provider is None:
        from .harness import _resolve_provider
        provider = _resolve_provider()

    def judge(soul_text: str, user_request: str) -> dict[str, Any]:
        # §13.Q2 — cache the verdict to avoid re-paying per turn.
        cache_key = _cache_key(soul_text, user_request)
        cached = _JUDGE_CACHE.get(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

        # No hardcoded default. Test mode / a TestProvider needs no real
        # model and must not hit the network during proof runs; otherwise
        # discover the flagship from the provider's API.
        import os as _os

        from .harness import resolve_model, TestProvider
        use_model = model
        if use_model is None:
            if (
                _os.environ.get("SIGNALOS_HARNESS_TEST") == "1"
                or isinstance(provider, TestProvider)
            ):
                use_model = "test"
            else:
                try:
                    use_model = resolve_model(None)
                except Exception as exc:  # noqa: BLE001 — degrade, never raise
                    return {
                        "drifted": False,
                        "confidence": 0.3,
                        "reasoning": f"model-resolution-failed: {type(exc).__name__}",
                    }

        prompt = _JUDGE_PROMPT_TEMPLATE.format(
            soul=soul_text.strip(), request=user_request.strip(),
        )
        try:
            response_text, _tok_in, _tok_out = provider.call(prompt, use_model)
        except Exception as exc:  # noqa: BLE001 — defensive; LLM call can fail
            return {
                "drifted": False,
                "confidence": 0.3,
                "reasoning": f"llm-call-failed: {type(exc).__name__}",
            }

        parsed = _extract_first_json_object(response_text)
        if parsed is None:
            return {
                "drifted": False,
                "confidence": 0.3,
                "reasoning": "llm-response-unparseable",
            }

        # Coerce types defensively — the LLM may return numbers as strings
        # or omit fields entirely.
        raw_drifted = parsed.get("drifted")
        if isinstance(raw_drifted, bool):
            drifted = raw_drifted
        elif isinstance(raw_drifted, str):
            drifted = raw_drifted.strip().lower() in {"true", "yes", "1"}
        else:
            drifted = False

        raw_conf = parsed.get("confidence", 0.5)
        try:
            confidence = float(raw_conf)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        reasoning = parsed.get("reasoning")
        if not isinstance(reasoning, str):
            reasoning = ""

        verdict = {
            "drifted": drifted,
            "confidence": confidence,
            "reasoning": reasoning[:200],
        }
        _JUDGE_CACHE[cache_key] = verdict
        return verdict

    return judge


def default_llm_judge(soul_text: str, user_request: str) -> dict[str, Any]:
    """Module-level convenience: build a fresh judge and call it once.

    Prefer `build_llm_judge()` when calling repeatedly — it lets you
    cache the provider + model resolution across calls.
    """
    return build_llm_judge()(soul_text, user_request)


# Used by the IPC layer to decide whether to attach an llm_judge when
# constructing a WaveEngine. Default **on** per design §13.Q2
# (RESOLVED: accepted, with caching) — the heuristic still runs first
# and only the ambiguous-zone (0.1 < overlap < 0.4) reaches the LLM,
# and the verdict is cached per (soul, request) pair so we don't
# re-pay across turns. Set SIGNALOS_LLM_JUDGE_DRIFT=0 to disable
# (e.g., CI/proof-scenario runs that shouldn't burn provider tokens).
def llm_judge_enabled() -> bool:
    val = os.environ.get("SIGNALOS_LLM_JUDGE_DRIFT", "1").strip().lower()
    return val not in {"0", "false", "no", "off", ""}
