#!/usr/bin/env python3
"""Gate the funded sidecar until its process is owned by a Windows Job Object.

The matrix driver starts this trusted bootstrap, assigns the still-blocked
process to a kill-on-close Job Object, and only then publishes the gate token.
The sidecar is executed in this same process so every descendant inherits the
verified Job Object membership before model-controlled work can begin.
"""

from __future__ import annotations

import argparse
import os
import runpy
import site
import stat
import sys
import time
from pathlib import Path


GATE_TIMEOUT_SECONDS = 120.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gate", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--sidecar", required=True)
    return parser


def _wait_for_release(gate: Path, token: str) -> None:
    expected = f"{os.getpid()}:{token}".encode("ascii")
    deadline = time.monotonic() + GATE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            metadata = gate.lstat()
        except FileNotFoundError:
            time.sleep(0.02)
            continue
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if (
            not stat.S_ISREG(metadata.st_mode)
            or gate.is_symlink()
            or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        ):
            raise RuntimeError("sidecar release gate is not a regular file")
        payload = gate.read_bytes()
        if payload != expected:
            raise RuntimeError("sidecar release gate token is invalid")
        gate.unlink()
        try:
            gate.parent.rmdir()
        except OSError:
            pass
        return
    raise TimeoutError("timed out waiting for Windows Job Object assignment")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.name != "nt":
        raise RuntimeError("Windows Job Object bootstrap cannot run on this platform")
    if len(args.token) != 64 or any(ch not in "0123456789abcdef" for ch in args.token):
        raise RuntimeError("sidecar release gate token has an invalid format")
    gate = Path(args.gate).resolve()
    sidecar = Path(args.sidecar).resolve()
    if not sidecar.is_file():
        raise RuntimeError("source sidecar is missing")
    _wait_for_release(gate, args.token)

    # runpy keeps the current process (and therefore its Job Object membership)
    # while giving the sidecar the same import path and argv shape as direct
    # ``python signalos_ipc_server.py`` execution.
    # The driver launches this file with -S so user-controlled .pth and
    # sitecustomize code cannot run before Job assignment. Enable normal
    # site-packages only after containment has been verified and released.
    site.main()
    sys.path.insert(0, str(sidecar.parent))
    sys.argv = [str(sidecar)]
    runpy.run_path(str(sidecar), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
