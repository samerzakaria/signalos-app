# cli/signalos_lib/commands/serve.py — W4.1 (AMD-CORE-019)
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("serve", help="Start browser-based gate signing server (W4.1)")
    p.add_argument("--port", type=int, default=4000, help="TCP port (default 4000)")
    p.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    p.add_argument("--repo-root", default=None, help="Repo root (default: auto-detect)")


def run(args: argparse.Namespace) -> int:
    from ..serve import start_server
    from ..status import _repo_root  # reuse the existing auto-detect helper

    repo_root = Path(args.repo_root) if args.repo_root else _repo_root()
    if not (repo_root / ".signalos").exists():
        print(
            f"error: .signalos/ not found under {repo_root}\n"
            "Run from a SignalOS repo root, or pass --repo-root.",
            file=sys.stderr,
        )
        return 1
    start_server(repo_root=repo_root, port=args.port, host=args.host)
    return 0
