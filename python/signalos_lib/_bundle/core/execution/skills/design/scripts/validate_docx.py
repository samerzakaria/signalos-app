#!/usr/bin/env python3
"""Validate a governance .docx against the SignalOS Visual System.

Runs mechanical checks that the document uses the expected fonts, sizes,
and colours. Narrative-level checks (one insight per page, rhythm) go to
the subagent critic.

Usage:
    python3 validate_docx.py output.docx
"""
from __future__ import annotations
import sys, json
from pathlib import Path

try:
    from docx import Document
except ImportError:
    sys.exit("install python-docx: pip install --break-system-packages python-docx")

SKILL_ROOT = Path(__file__).resolve().parent.parent
TOKENS = json.loads((SKILL_ROOT / "assets" / "tokens.json").read_text())

EXPECTED_FONTS = {"IBM Plex Sans", "IBM Plex Serif", "IBM Plex Mono", "IBM Plex Sans Arabic"}
PALETTE = set(v.upper().lstrip("#") for v in TOKENS["palette"].values())
TYPE_SCALE = set(TOKENS["type"]["scale_pt"])


def check_font(name: str) -> bool:
    return name in EXPECTED_FONTS


def run(path: str) -> int:
    doc = Document(path)
    fails = []

    for i, para in enumerate(doc.paragraphs):
        for run in para.runs:
            face = run.font.name
            size = run.font.size.pt if run.font.size else None
            color = run.font.color.rgb if run.font.color and run.font.color.rgb else None

            if face and not check_font(face):
                fails.append(f"para {i}: off-system font '{face}'")

            if size and int(size) not in TYPE_SCALE:
                fails.append(f"para {i}: off-scale size {size}pt")

            if color:
                hx = str(color).upper()
                if hx not in PALETTE and hx not in {"000000", "0B1221", "1F2A44"}:
                    fails.append(f"para {i}: off-palette colour #{hx}")

    print(f"SignalOS Visual — docx validation: {path}")
    print("=" * 60)
    if not fails:
        print("PASS (mechanical). Run subagent critic for narrative & rhythm.")
        return 0
    for f in fails[:40]:
        print(f"  FAIL · {f}")
    if len(fails) > 40:
        print(f"  … {len(fails) - 40} more")
    print(f"\nTOTAL FAILS: {len(fails)}")
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: validate_docx.py <file.docx>")
    sys.exit(run(sys.argv[1]))
