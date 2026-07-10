"""Layer 1 -- WIRE-LEVEL golden-path regression net ($0, runs in seconds).

The trap this AVOIDS: an adapter-level fake (a fake AgentProvider that returns
parsed ToolCall objects) goes green forever and never exercises the code that
caused our worst bugs. Here the fake sits at the NETWORK boundary: we intercept
`httpx.Client.send` (the socket) and speak the real OpenRouter/OpenAI-compatible
wire protocol -- `choices[].message.tool_calls`, `finish_reason`, usage -- for a
REAL model id (`openrouter/z-ai/glm-5.2`).

Everything ABOVE the socket runs UNMODIFIED: real litellm request construction +
routing (the openrouter/ prefix is stripped to `z-ai/glm-5.2` in the wire body),
real detect_capabilities (which must trust its name heuristic over litellm's
false-negative registry so tools are actually offered), real ProviderAdapter,
real AgentLoop tool loop, real governance, real path-containment, real
write_file/run_command dispatch.

We script a known-good sequence of tool calls that writes a tiny module + its
test and verifies them, then a final no-tool "done" turn, and assert: tools were
offered on the wire, the scripted tool calls were dispatched, the files landed on
disk, and the mini-build command actually passed (exit_code 0).

Deps: httpx (already installed) via a monkeypatched `httpx.Client.send`. respx /
vcrpy are NOT installed in this env, so we use the stdlib-friendly monkeypatch
seam the panel allowed instead of adding a heavy dep.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.agent_loop import AgentLoop  # noqa: E402
from signalos_lib.product.enforcement_state import (  # noqa: E402
    StaticEnforcementProvider,
)
from signalos_lib.product.provider_adapter import ProviderAdapter  # noqa: E402

REAL_MODEL = "openrouter/z-ai/glm-5.2"
# The provider-native id litellm should put on the wire (openrouter/ stripped).
WIRE_MODEL = "z-ai/glm-5.2"

# A tiny fixture: the implementation + a test that passes iff the impl is right.
CALC_SRC = "def add(a, b):\n    return a + b\n"
CALC_TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
# A run-once mini-build that exits 0 only when the written module is correct.
MINI_BUILD_CMD = (
    "python -c \"import sys; sys.path.insert(0, 'src'); import calc; "
    "assert calc.add(2, 3) == 5; print('MINI_BUILD_OK')\""
)


def _tool_turn(name: str, args: dict) -> dict:
    """An OpenAI/OpenRouter-shaped completion carrying one tool call."""
    return {
        "id": "chatcmpl-golden",
        "object": "chat.completion",
        "created": 0,
        "model": WIRE_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{name}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _text_turn(text: str) -> dict:
    return {
        "id": "chatcmpl-golden",
        "object": "chat.completion",
        "created": 0,
        "model": WIRE_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class _WireFake:
    """Scripted fake at the NETWORK boundary. `transport()` returns a plain
    function suitable for `monkeypatch.setattr(httpx.Client, "send", ...)` (a
    bound method would swallow the client `self`); it replays one scripted
    completion per POST to /chat/completions, recording the exact request bodies
    litellm put on the wire."""

    def __init__(self, script: list[dict]):
        self._script = script
        self.requests: list[dict] = []

    def transport(self):
        wire = self

        def send(client_self, request, **kwargs):  # noqa: ANN001 - httpx.Client.send
            url = str(request.url)
            assert "chat/completions" in url, f"unexpected non-chat request: {url}"
            try:
                body = json.loads(request.content.decode("utf-8"))
            except Exception:  # pragma: no cover - defensive
                body = {}
            wire.requests.append(body)
            idx = len(wire.requests) - 1
            assert idx < len(wire._script), (
                f"provider called {idx + 1} times; script only has "
                f"{len(wire._script)} turns"
            )
            return httpx.Response(200, json=wire._script[idx], request=request)

        return send


@pytest.fixture()
def golden_repo(tmp_path: Path) -> Path:
    (tmp_path / ".signalos").mkdir()
    return tmp_path


def _run_golden(repo_root: Path, wire: _WireFake, monkeypatch) -> object:
    # A fake key so litellm proceeds to the (faked) HTTP call instead of raising
    # an auth error before the socket -- the transport is faked, the key is not
    # used by anyone real.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake-for-tests")
    monkeypatch.setattr(httpx.Client, "send", wire.transport(), raising=True)

    # REAL adapter: real detect_capabilities against the REAL litellm registry.
    adapter = ProviderAdapter(model=REAL_MODEL)
    events: list[dict] = []
    loop = AgentLoop(
        adapter=adapter,
        repo_root=repo_root,
        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
        run_id="golden-e2e",
        execution_context="delivery",
        emit=events.append,
    )
    result = loop.run(
        system_prompt="You are a build agent. Use tools to write and verify code.",
        user_message="Create src/calc.py with add(), a passing test, and verify it.",
    )
    result._events = events  # type: ignore[attr-defined]
    result._adapter = adapter  # type: ignore[attr-defined]
    return result


def test_golden_path_writes_files_and_verifies(golden_repo, monkeypatch):
    wire = _WireFake(
        [
            _tool_turn("write_file", {"path": "src/calc.py", "content": CALC_SRC}),
            _tool_turn("write_file", {"path": "tests/test_calc.py", "content": CALC_TEST}),
            _tool_turn("run_command", {"command": MINI_BUILD_CMD}),
            _text_turn("Wrote src/calc.py + tests/test_calc.py and verified the build. Done."),
        ]
    )
    result = _run_golden(golden_repo, wire, monkeypatch)

    # (0) The real capability path offered tools despite litellm's registry
    #     false-negativing this model id -- the bug that scored a funded run 0.
    assert result._adapter.supports_tool_calls is True  # type: ignore[attr-defined]

    # (1) Tools were actually offered ON THE WIRE (litellm serialized them into
    #     the request body), and litellm routed to the provider-native id.
    first_body = wire.requests[0]
    assert "tools" in first_body and first_body["tools"], "no tools offered on the wire"
    assert first_body["model"] == WIRE_MODEL, first_body["model"]
    tool_names = {
        t.get("function", {}).get("name") for t in first_body["tools"]
    }
    assert {"write_file", "run_command"} <= tool_names

    # (2) The scripted tool calls were dispatched through the real loop.
    assert result.status == "completed", result.error
    assert result.tool_calls_made == 3
    assert result.wrote_no_files is False
    assert len(wire.requests) == 4  # 3 tool turns + 1 closing text turn

    # (3) The files LANDED ON DISK via real path-containment + write dispatch.
    assert (golden_repo / "src" / "calc.py").read_text(encoding="utf-8") == CALC_SRC
    assert (golden_repo / "tests" / "test_calc.py").read_text(encoding="utf-8") == CALC_TEST

    # (4) The mini-build actually ran and PASSED (real run_command dispatch).
    run_results = [
        m for m in result.messages
        if m.get("role") == "tool" and m.get("name") == "run_command"
    ]
    assert run_results, "run_command produced no tool result"
    combined = "\n".join(m.get("content") or "" for m in run_results)
    assert "exit_code: 0" in combined, combined
    assert "MINI_BUILD_OK" in combined, combined


def test_golden_path_wire_body_shape_is_openai_compatible(golden_repo, monkeypatch):
    """Guards the request the adapter builds: messages array + tool_choice reach
    the wire in the OpenAI-compatible shape litellm expects (regression net for
    the routing/tool-offer plumbing)."""
    wire = _WireFake(
        [
            _tool_turn("write_file", {"path": "src/note.txt", "content": "ok\n"}),
            _text_turn("done"),
        ]
    )
    result = _run_golden(golden_repo, wire, monkeypatch)

    assert result.status == "completed"
    body = wire.requests[0]
    assert isinstance(body.get("messages"), list) and body["messages"]
    assert body["messages"][0]["role"] == "system"
    # tool_choice defaults to "auto" whenever tools are in play.
    assert body.get("tool_choice") == "auto"


if __name__ == "__main__":  # pragma: no cover - manual run convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
