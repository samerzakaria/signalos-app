# Tests for delivery agent-dispatch routing (Foundry gen fix, STEP 5).
#
# The routing decision: a founder WITH an LLM key (agent_mode auto/remote)
# gets the chunked PER-FILE LLM path -> a real working product. A founder
# WITHOUT a key (or agent_mode == "local") gets the fast, git-free local
# parallel path -> still complete + buildable. The decision is a pure helper
# so it is unit-testable without running the whole (heavy) delivery pipeline.

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.delivery import _choose_dispatch_route


SUPPORTED = "react-vite"


def test_key_present_auto_routes_to_chunked_llm():
    assert _choose_dispatch_route("auto", SUPPORTED, llm_available=True) == "chunked-llm"


def test_key_present_remote_routes_to_chunked_llm():
    assert _choose_dispatch_route("remote", SUPPORTED, llm_available=True) == "chunked-llm"


def test_no_key_auto_routes_to_local_parallel():
    assert _choose_dispatch_route("auto", SUPPORTED, llm_available=False) == "local-parallel"


def test_explicit_local_always_local_even_with_key():
    assert _choose_dispatch_route("local", SUPPORTED, llm_available=True) == "local-parallel"


def test_no_key_remote_falls_back_to_local_parallel():
    # remote requested but no key -> cannot call LLM; stay buildable locally.
    assert _choose_dispatch_route("remote", SUPPORTED, llm_available=False) == "local-parallel"


def test_unsupported_profile_with_key_still_uses_llm():
    # A profile the local renderer does not support MUST use the LLM path when
    # a key is available (there is no deterministic renderer to fall back to).
    assert _choose_dispatch_route("auto", "django-api", llm_available=True) == "chunked-llm"
