"""
cli/signalos_lib/commands/brain.py — CLI for SignalOS Knowledge Brain (AMD-CORE-030)
Handles: signalos brain put/search/list/prune/export/upgrade
         signalos signal-learn review/search/prune/export
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional


def _repo(args_root: Optional[str] = None) -> Path:
    return Path(args_root) if args_root else Path.cwd()


# ---------------------------------------------------------------------------
# brain sub-commands
# ---------------------------------------------------------------------------

def cmd_brain(args: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="signalos brain")
    sub = p.add_subparsers(dest="action")

    # put
    pp = sub.add_parser("put", help="Add an entry to the brain index")
    pp.add_argument("content", help="Text content to store")
    pp.add_argument("--source", default="", help="Source file path")
    pp.add_argument("--gate", default="", help="Associated gate (G0–G5)")
    pp.add_argument("--wave", default="", help="Associated wave (e.g. 09)")
    pp.add_argument("--product-id", default="core")
    pp.add_argument("--type", default="note",
                    choices=["artifact", "decision", "qa", "session", "note"])
    pp.add_argument("--weight", type=float, default=1.0)
    pp.add_argument("--repo-root", default=None)
    pp.add_argument("--json", dest="as_json", action="store_true")

    # search
    sp = sub.add_parser("search", help="BM25 search the brain index")
    sp.add_argument("query", help="Search query")
    sp.add_argument("--top", type=int, default=5)
    sp.add_argument("--wave", default=None)
    sp.add_argument("--gate", default=None)
    sp.add_argument("--type", dest="entry_type", default=None)
    sp.add_argument("--repo-root", default=None)
    sp.add_argument("--json", dest="as_json", action="store_true")

    # list
    lp = sub.add_parser("list", help="List brain index entries")
    lp.add_argument("--wave", default=None)
    lp.add_argument("--gate", default=None)
    lp.add_argument("--type", dest="entry_type", default=None)
    lp.add_argument("--repo-root", default=None)
    lp.add_argument("--json", dest="as_json", action="store_true")

    # prune
    rp = sub.add_parser("prune", help="Soft-delete a brain entry by ID")
    rp.add_argument("entry_id", help="brain-NNN id to prune")
    rp.add_argument("--repo-root", default=None)
    rp.add_argument("--json", dest="as_json", action="store_true")

    # export
    ep = sub.add_parser("export", help="Export brain index to a portable JSONL bundle")
    ep.add_argument("--out", required=True, help="Output .jsonl file path")
    ep.add_argument("--repo-root", default=None)
    ep.add_argument("--json", dest="as_json", action="store_true")

    # upgrade
    up = sub.add_parser("upgrade", help="Upgrade brain index with embeddings")
    up.add_argument("--embeddings", action="store_true", help="Enable embeddings upgrade")
    up.add_argument("--api-key", default=None)
    up.add_argument("--provider", choices=["openai", "voyage"], default=None,
                    help="Embedding provider (default: auto-detect from key/env)")
    up.add_argument("--repo-root", default=None)
    up.add_argument("--json", dest="as_json", action="store_true")

    ns = p.parse_args(args)
    if not ns.action:
        p.print_help()
        return 1

    from signalos_lib.brain import (
        brain_put, brain_search, brain_list, brain_prune,
        brain_export, brain_upgrade_embeddings,
    )
    root = _repo(getattr(ns, "repo_root", None))
    as_json = getattr(ns, "as_json", False)

    if ns.action == "put":
        entry = brain_put(
            root, ns.content, ns.source,
            gate=ns.gate, wave=ns.wave,
            product_id=ns.product_id,
            entry_type=ns.type,
            weight=ns.weight,
        )
        if as_json:
            print(json.dumps(entry.as_dict(), indent=2))
        else:
            print(f"✓ stored {entry.id}  [{entry.type}] wave={entry.wave} gate={entry.gate}")
        return 0

    if ns.action == "search":
        results = brain_search(root, ns.query, top_n=ns.top,
                               wave=ns.wave, gate=ns.gate, entry_type=ns.entry_type)
        if as_json:
            print(json.dumps([e.as_dict() for e in results], indent=2))
        else:
            if not results:
                print("No results.")
            for e in results:
                print(f"[{e.id}] [{e.type}] w={e.wave} g={e.gate}  {e.content[:100]}")
        return 0

    if ns.action == "list":
        results = brain_list(root, wave=ns.wave, gate=ns.gate, entry_type=ns.entry_type)
        if as_json:
            print(json.dumps([e.as_dict() for e in results], indent=2))
        else:
            if not results:
                print("Brain index is empty.")
            for e in results:
                print(f"[{e.id}] [{e.type}] w={e.wave} g={e.gate} ts={e.ts}  {e.content[:80]}")
        return 0

    if ns.action == "prune":
        ok = brain_prune(root, ns.entry_id)
        result = {"pruned": ok, "id": ns.entry_id}
        if as_json:
            print(json.dumps(result))
        else:
            print(f"✓ pruned {ns.entry_id}" if ok else f"✗ entry {ns.entry_id} not found")
        return 0 if ok else 1

    if ns.action == "export":
        out = Path(ns.out)
        count = brain_export(root, out)
        result = {"exported": count, "path": str(out)}
        if as_json:
            print(json.dumps(result))
        else:
            print(f"✓ exported {count} entries → {out}")
        return 0

    if ns.action == "upgrade":
        if not ns.embeddings:
            print("Pass --embeddings to enable the embeddings upgrade.")
            return 1
        summary = brain_upgrade_embeddings(root, api_key=ns.api_key, provider=ns.provider)
        if as_json:
            print(json.dumps(summary))
        else:
            print(f"backend={summary['backend']}  upgraded={summary['upgraded']}  skipped={summary['skipped']}")
            if "reason" in summary:
                print(f"  reason: {summary['reason']}")
        return 0

    return 1


# ---------------------------------------------------------------------------
# signal-learn sub-commands
# ---------------------------------------------------------------------------

def cmd_signal_learn(args: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="signalos signal-learn")
    sub = p.add_subparsers(dest="action")

    # review
    rv = sub.add_parser("review", help="Paginated review of brain entries by wave/gate")
    rv.add_argument("--wave", default=None)
    rv.add_argument("--gate", default=None)
    rv.add_argument("--page-size", type=int, default=10)
    rv.add_argument("--repo-root", default=None)
    rv.add_argument("--json", dest="as_json", action="store_true")

    # search
    sv = sub.add_parser("search", help="Search brain entries")
    sv.add_argument("query")
    sv.add_argument("--top", type=int, default=5)
    sv.add_argument("--repo-root", default=None)
    sv.add_argument("--json", dest="as_json", action="store_true")

    # prune
    pv = sub.add_parser("prune", help="Prune a stale brain entry")
    pv.add_argument("entry_id")
    pv.add_argument("--repo-root", default=None)
    pv.add_argument("--json", dest="as_json", action="store_true")

    # export
    ev = sub.add_parser("export", help="Export brain to portable bundle")
    ev.add_argument("--out", required=True)
    ev.add_argument("--repo-root", default=None)
    ev.add_argument("--json", dest="as_json", action="store_true")

    ns = p.parse_args(args)
    if not ns.action:
        p.print_help()
        return 1

    from signalos_lib.brain import (
        brain_list, brain_search, brain_prune, brain_export,
    )
    root = _repo(getattr(ns, "repo_root", None))
    as_json = getattr(ns, "as_json", False)

    if ns.action == "review":
        entries = brain_list(root, wave=ns.wave, gate=ns.gate)
        page = ns.page_size
        total = len(entries)
        if as_json:
            print(json.dumps({"total": total, "entries": [e.as_dict() for e in entries[:page]]}, indent=2))
        else:
            print(f"Brain entries: {total} total" + (f" (wave={ns.wave})" if ns.wave else "") + (f" (gate={ns.gate})" if ns.gate else ""))
            for i, e in enumerate(entries[:page]):
                print(f"  {i+1:>3}. [{e.id}] [{e.type}] w={e.wave} g={e.gate}  {e.content[:90]}")
            if total > page:
                print(f"  ... {total - page} more (use --page-size to show more)")
        return 0

    if ns.action == "search":
        results = brain_search(root, ns.query, top_n=ns.top)
        if as_json:
            print(json.dumps([e.as_dict() for e in results], indent=2))
        else:
            for e in results:
                print(f"[{e.id}] [{e.type}] w={e.wave} g={e.gate}  {e.content[:100]}")
        return 0

    if ns.action == "prune":
        ok = brain_prune(root, ns.entry_id)
        if as_json:
            print(json.dumps({"pruned": ok, "id": ns.entry_id}))
        else:
            print(f"✓ pruned {ns.entry_id}" if ok else f"✗ {ns.entry_id} not found")
        return 0 if ok else 1

    if ns.action == "export":
        out = Path(ns.out)
        count = brain_export(root, out)
        if as_json:
            print(json.dumps({"exported": count, "path": str(out)}))
        else:
            print(f"✓ exported {count} entries → {out}")
        return 0

    return 1
