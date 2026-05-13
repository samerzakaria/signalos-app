"""
cli/signalos_lib/brain.py — SignalOS Knowledge Brain (AMD-CORE-030)

Persistent AI memory index with pure-Python BM25 search.
Storage: .signalos/brain/index.jsonl (one JSON object per line, append-only).
No runtime third-party dependencies.
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "BrainEntry",
    "brain_put",
    "brain_search",
    "brain_list",
    "brain_prune",
    "brain_export",
    "brain_upgrade_embeddings",
    "check_brain_hook_wired",
    "BRAIN_INDEX_RELATIVE",
]

BRAIN_INDEX_RELATIVE = ".signalos/brain/index.jsonl"
_ID_PREFIX = "brain-"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BrainEntry:
    id: str
    product_id: str
    gate: str
    wave: str
    type: str          # artifact | decision | qa | session | note
    content: str
    source_path: str
    ts: str            # ISO-8601
    weight: float = 1.0
    embedding: list[float] = field(default_factory=list)  # optional; empty = BM25 only

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _index_path(repo_root: Path) -> Path:
    return repo_root / BRAIN_INDEX_RELATIVE


def _load_entries(repo_root: Path) -> list[BrainEntry]:
    idx = _index_path(repo_root)
    if not idx.exists():
        return []
    # Ordered last-write-wins: process lines in file order so a tombstone
    # followed by a re-appended entry (e.g. after embeddings upgrade) is
    # visible, while a tombstone that is the *last* record for an id hides it.
    order: list[str] = []          # insertion-order of ids (first seen)
    seen: set[str] = set()         # all ids ever encountered (prevents duplicates in order)
    live: dict[str, BrainEntry] = {}
    for line in idx.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            eid = d.get("id", "")
            if d.get("pruned"):
                live.pop(eid, None)
            else:
                entry = BrainEntry(**{k: d[k] for k in BrainEntry.__dataclass_fields__ if k in d})
                if eid not in seen:
                    order.append(eid)
                    seen.add(eid)
                live[eid] = entry
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
    return [live[eid] for eid in order if eid in live]


def _append_raw(repo_root: Path, record: dict[str, Any]) -> None:
    idx = _index_path(repo_root)
    idx.parent.mkdir(parents=True, exist_ok=True)
    with idx.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _next_id(repo_root: Path) -> str:
    idx = _index_path(repo_root)
    if not idx.exists():
        return f"{_ID_PREFIX}001"
    ids: list[int] = []
    for line in idx.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            raw = d.get("id", "")
            if raw.startswith(_ID_PREFIX):
                ids.append(int(raw[len(_ID_PREFIX):]))
        except (json.JSONDecodeError, ValueError):
            continue
    nxt = max(ids, default=0) + 1
    return f"{_ID_PREFIX}{nxt:03d}"


def _tokenise(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric."""
    return re.findall(r"[a-z0-9]+", text.lower())


# ---------------------------------------------------------------------------
# Pure-Python BM25
# ---------------------------------------------------------------------------

def _bm25_scores(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """Return BM25 score for each document against query."""
    if not docs:
        return []

    q_terms = _tokenise(query)
    tokenised = [_tokenise(d) for d in docs]
    N = len(tokenised)
    avgdl = sum(len(t) for t in tokenised) / N if N else 1.0

    # Document frequency per term
    df: dict[str, int] = {}
    for terms in tokenised:
        for t in set(terms):
            df[t] = df.get(t, 0) + 1

    scores: list[float] = []
    for terms in tokenised:
        dl = len(terms)
        tf_map: dict[str, int] = {}
        for t in terms:
            tf_map[t] = tf_map.get(t, 0) + 1

        score = 0.0
        for q in q_terms:
            if q not in df:
                continue
            idf = math.log((N - df[q] + 0.5) / (df[q] + 0.5) + 1.0)
            tf = tf_map.get(q, 0)
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * dl / avgdl)
            score += idf * numerator / (denominator or 1.0)
        scores.append(score)
    return scores


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def brain_put(
    repo_root: Path,
    content: str,
    source_path: str,
    gate: str = "",
    wave: str = "",
    product_id: str = "core",
    entry_type: str = "note",
    weight: float = 1.0,
) -> BrainEntry:
    """Append a new entry to the brain index. Returns the created entry."""
    entry = BrainEntry(
        id=_next_id(repo_root),
        product_id=product_id,
        gate=gate,
        wave=wave,
        type=entry_type,
        content=content,
        source_path=source_path,
        ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        weight=weight,
        embedding=[],
    )
    _append_raw(repo_root, entry.as_dict())
    return entry


def brain_search(
    repo_root: Path,
    query: str,
    top_n: int = 5,
    wave: Optional[str] = None,
    gate: Optional[str] = None,
    entry_type: Optional[str] = None,
) -> list[BrainEntry]:
    """BM25 search over brain index. Returns top_n entries by relevance."""
    entries = brain_list(repo_root, wave=wave, gate=gate, entry_type=entry_type)
    if not entries:
        return []
    if not query.strip():
        return entries[:top_n]
    docs = [e.content for e in entries]
    scores = _bm25_scores(query, docs)
    ranked = sorted(zip(scores, entries), key=lambda x: x[0], reverse=True)
    return [e for score, e in ranked if score > 0][:top_n]


def brain_list(
    repo_root: Path,
    wave: Optional[str] = None,
    gate: Optional[str] = None,
    entry_type: Optional[str] = None,
) -> list[BrainEntry]:
    """Return all non-pruned entries, optionally filtered."""
    entries = _load_entries(repo_root)
    if wave is not None:
        entries = [e for e in entries if e.wave == wave]
    if gate is not None:
        entries = [e for e in entries if e.gate == gate]
    if entry_type is not None:
        entries = [e for e in entries if e.type == entry_type]
    return entries


def brain_prune(repo_root: Path, entry_id: str) -> bool:
    """Soft-delete an entry by appending a tombstone record. Returns True if found."""
    entries = _load_entries(repo_root)
    found = any(e.id == entry_id for e in entries)
    if not found:
        return False
    tombstone = {
        "id": entry_id,
        "pruned": True,
        "pruned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _append_raw(repo_root, tombstone)
    return True


def brain_export(repo_root: Path, out_path: Path) -> int:
    """Export all active entries to a portable JSONL bundle. Returns count."""
    entries = brain_list(repo_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e.as_dict()) + "\n")
    return len(entries)


def brain_upgrade_embeddings(
    repo_root: Path,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
) -> dict[str, Any]:
    """
    Opt-in embeddings upgrade: embed each entry and store the vector in the
    entry's ``embedding`` field.  Falls back to BM25 when no key is available.

    Supported providers (auto-detected from environment if not specified):
      - ``openai``  — uses OPENAI_API_KEY, model text-embedding-3-small
      - ``voyage``  — uses VOYAGE_API_KEY, model voyage-2

    Uses stdlib ``urllib.request`` — no new runtime dependency.

    Returns a summary dict: {upgraded: int, skipped: int, backend: str}.
    """
    import os

    # ── resolve provider + key ───────────────────────────────────────────────
    if provider is None:
        if api_key:
            # Guess from key prefix: OpenAI keys start with "sk-"
            provider = "openai" if api_key.startswith("sk-") else "voyage"
        elif os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
            api_key = os.environ["OPENAI_API_KEY"]
        elif os.environ.get("VOYAGE_API_KEY"):
            provider = "voyage"
            api_key = os.environ["VOYAGE_API_KEY"]
        else:
            return {"upgraded": 0, "skipped": 0, "backend": "bm25", "reason": "no api key"}
    else:
        if not api_key:
            env_map = {"openai": "OPENAI_API_KEY", "voyage": "VOYAGE_API_KEY"}
            api_key = os.environ.get(env_map.get(provider, ""), "")
        if not api_key:
            return {"upgraded": 0, "skipped": 0, "backend": "bm25", "reason": "no api key"}

    # ── build embed function for chosen provider ─────────────────────────────
    import urllib.request as _req

    if provider == "openai":
        _embed_url = "https://api.openai.com/v1/embeddings"
        _embed_model = "text-embedding-3-small"
        def _parse_response(data: dict) -> list[float]:  # type: ignore[misc]
            return data["data"][0]["embedding"]
    else:
        _embed_url = "https://api.voyageai.com/v1/embeddings"
        _embed_model = "voyage-2"
        def _parse_response(data: dict) -> list[float]:  # type: ignore[misc]
            return data["data"][0]["embedding"]

    def _embed(text: str) -> list[float]:
        payload = json.dumps({"input": [text], "model": _embed_model}).encode()
        request = _req.Request(
            _embed_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with _req.urlopen(request, timeout=15) as resp:
            data = json.loads(resp.read())
        return _parse_response(data)

    entries = brain_list(repo_root)
    upgraded = 0
    skipped = 0

    for entry in entries:
        if entry.embedding:
            skipped += 1
            continue
        try:
            vector = _embed(entry.content)
        except Exception:
            skipped += 1
            continue

        # Tombstone the old record then re-append with embedding.
        # _load_entries uses last-write-wins so the re-appended entry is live.
        tombstone = {
            "id": entry.id,
            "pruned": True,
            "pruned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _append_raw(repo_root, tombstone)
        new_entry = BrainEntry(
            id=entry.id,
            product_id=entry.product_id,
            gate=entry.gate,
            wave=entry.wave,
            type=entry.type,
            content=entry.content,
            source_path=entry.source_path,
            ts=entry.ts,
            weight=entry.weight,
            embedding=vector,
        )
        _append_raw(repo_root, new_entry.as_dict())
        upgraded += 1

    return {"upgraded": upgraded, "skipped": skipped, "backend": "embeddings"}


def brain_context_block(repo_root: Path, query: str = "", top_n: int = 5) -> str:
    """
    Return a human-readable context block of top_n brain entries for session injection.
    Returns empty string if no index exists.
    """
    idx = _index_path(repo_root)
    if not idx.exists():
        return ""

    results = brain_search(repo_root, query, top_n=top_n) if query else brain_list(repo_root)[:top_n]
    if not results:
        return ""

    lines = ["## Brain Context (top knowledge entries)\n"]
    for e in results:
        lines.append(f"- [{e.id}] [{e.type}] wave={e.wave} gate={e.gate}: {e.content[:120]}")
    return "\n".join(lines)


def check_brain_hook_wired(repo_root: Path) -> tuple[bool, str]:
    """
    C15: If brain index exists, brain-auto-ingest.sh must be present.
    Returns (ok, message).
    """
    idx = _index_path(repo_root)
    hook = repo_root / "core" / "execution" / "hooks" / "_lib" / "brain-auto-ingest.sh"
    if not idx.exists():
        return True, "brain index not yet created — no check needed"
    if hook.exists():
        return True, "brain-auto-ingest.sh present"
    return False, f"brain index exists but {hook.relative_to(repo_root)} is missing"
