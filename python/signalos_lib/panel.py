#!/usr/bin/env python3
"""Cross-vendor second-opinion panel — the War Room engine.

PROVENANCE / RE-SYNC
--------------------
The panel *core* below (DEFAULT_MODELS, DEFAULT_SYSTEM, load_key, total_usage,
ask) is a faithful copy of the ``consult-panel`` skill at
``~/.claude/skills/consult-panel/panel.py``. It is copied, not imported,
because the packaged desktop sidecar must be self-contained (the global skills
directory is not on the PyInstaller bundle path). Keep these functions
structurally identical to the skill so a future skill change re-syncs by
re-copying the changed function; the desktop-specific parts (the parallel
``consult`` driver + the IPC-friendly return shape) are a thin wrapper around
that verbatim core and should stay separate from it.

Why cross-vendor + blind: decorrelated blind spots beat one family's biases, so
each model answers the SAME question independently — never each other's answers
(that would herd them). Synthesis is the caller's job.

Only stdlib (urllib) is used, so this bundles cleanly and adds no dependency.
"""
from __future__ import annotations

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

# ── consult-panel core (keep 1:1 with the skill) ───────────────────────────

# Cross-vendor by default -- decorrelated blind spots beat one family's biases.
DEFAULT_MODELS: list[tuple[str, str]] = [
    ("anthropic/claude-sonnet-5", "Sonnet-5"),
    ("openai/gpt-5.6-sol", "GPT-5.6-Sol"),
    ("deepseek/deepseek-v4-pro", "DeepSeek-V4-Pro"),
    ("qwen/qwen3.7-max", "Qwen3.7-Max"),
]

DEFAULT_SYSTEM = (
    "You are a senior engineer giving a candid, expert second opinion. Be "
    "direct, concrete, and opinionated. Cite what real systems/tools actually "
    "do. Explicitly flag over-engineering. No hedging; keep it tight."
)


def load_key() -> str:
    """Resolve an OpenRouter key: env -> ~/.claude/openrouter.key -> a
    discoverable .env (best effort). Mirrors the consult-panel skill."""
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if k:
        return k
    keyfile = Path.home() / ".claude" / "openrouter.key"
    if keyfile.is_file():
        v = keyfile.read_text(encoding="utf-8").strip()
        if v:
            return v
    for env in (Path.home() / ".openrouter",
                Path.home() / "dev" / "ClearReq" / "apps" / "api" / ".env"):
        try:
            if env.is_file():
                for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.strip().startswith("OPENROUTER_API_KEY") and "=" in line:
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


def total_usage(key: str, *, opener: Callable[..., Any] = urllib.request.urlopen) -> Optional[float]:
    """Total lifetime usage (USD) on the key -- cost delta = after - before."""
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {key}"})
        d = json.loads(opener(req, timeout=20).read().decode())["data"]
        return float(d["total_usage"])
    except Exception:
        return None


def ask(key: str, model: str, system: str, user: str, *,
        opener: Callable[..., Any] = urllib.request.urlopen) -> str:
    """One model's answer to *user* under *system*. Independent (blind) — the
    caller never threads other models' answers into *user*."""
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": user}]
    body = json.dumps({"model": model, "messages": msgs}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    d = json.loads(opener(req, timeout=300).read().decode())
    return d["choices"][0]["message"]["content"]


# ── desktop driver (parallel fan-out + IPC-friendly result) ────────────────


def _normalise_models(models: Any) -> list[tuple[str, str]]:
    """Accept the default set, a comma string, or a list of ids/(id,name)."""
    if not models:
        return list(DEFAULT_MODELS)
    if isinstance(models, str):
        ids = [m.strip() for m in models.split(",") if m.strip()]
    else:
        ids = list(models)
    out: list[tuple[str, str]] = []
    for m in ids:
        if isinstance(m, (list, tuple)) and len(m) == 2:
            out.append((str(m[0]), str(m[1])))
        else:
            mid = str(m).strip()
            if mid:
                out.append((mid, mid.split("/")[-1]))
    return out or list(DEFAULT_MODELS)


def consult(
    question: str,
    *,
    models: Any = None,
    system: Optional[str] = None,
    key: Optional[str] = None,
    max_workers: int = 4,
    _ask: Callable[..., str] = ask,
    _usage: Callable[..., Optional[float]] = total_usage,
) -> dict[str, Any]:
    """Fan the SAME question out to every model IN PARALLEL and collect each
    candid, independent answer plus the exact OpenRouter cost delta.

    Parallel is a pure win over the skill's sequential loop: identical blind
    answers, but wall-clock ~= the slowest single call instead of their sum.
    Never raises for a single model failure -- that model's entry carries
    ``ok: False`` and an error, so one vendor's outage can't sink the panel.

    Returns an IPC-friendly dict:
        {"answers": [{"model","name","text","ok","error"}...],
         "cost_usd": float|None, "models": [...], "system": str}
    """
    q = str(question or "").strip()
    if not q:
        raise ValueError("panel:consult requires a non-empty question")
    api_key = (key or "").strip() or load_key()
    if not api_key:
        raise ValueError(
            "No OpenRouter key. Set OPENROUTER_API_KEY (or add it in Settings > "
            "Secrets), or write it to ~/.claude/openrouter.key.")
    sys_prompt = system if system is not None else DEFAULT_SYSTEM
    selected = _normalise_models(models)

    before = _usage(api_key)

    def _one(entry: tuple[str, str]) -> dict[str, Any]:
        model, name = entry
        try:
            text = _ask(api_key, model, sys_prompt, q)
            return {"model": model, "name": name, "text": text, "ok": True, "error": None}
        except Exception as exc:  # one vendor's failure never sinks the panel
            return {"model": model, "name": name, "text": "", "ok": False,
                    "error": f"{type(exc).__name__}: {exc}"}

    workers = max(1, min(int(max_workers or 1), len(selected)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Preserve the requested order in the result regardless of finish order.
        answers = list(pool.map(_one, selected))

    after = _usage(api_key)
    cost = (round(after - before, 4)
            if before is not None and after is not None else None)

    return {
        "answers": answers,
        "cost_usd": cost,
        "models": [m for m, _ in selected],
        "system": sys_prompt,
    }
