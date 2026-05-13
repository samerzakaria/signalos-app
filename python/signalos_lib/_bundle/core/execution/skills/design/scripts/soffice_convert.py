#!/usr/bin/env python3
"""Minimal .pptx/.docx → PDF converter for the QA loop.

Usage:
    python3 soffice_convert.py --pdf deck.pptx
    python3 soffice_convert.py --pdf doc.docx [--outdir ./pdfs]
"""
from __future__ import annotations
import argparse, subprocess, shutil, sys
from pathlib import Path


def convert(input_path: str, outdir: str | None = None) -> str:
    src = Path(input_path).resolve()
    if not src.exists():
        sys.exit(f"ERROR: input not found: {src}")
    outdir_p = Path(outdir).resolve() if outdir else src.parent
    outdir_p.mkdir(parents=True, exist_ok=True)
    binary = shutil.which("libreoffice") or shutil.which("soffice")
    if not binary:
        sys.exit("ERROR: install libreoffice or soffice")
    # LibreOffice is picky — run from a tmpdir to isolate user profile
    subprocess.run(
        [binary, "--headless", "--convert-to", "pdf", "--outdir", str(outdir_p), str(src)],
        check=True, timeout=120
    )
    pdf = outdir_p / (src.stem + ".pdf")
    print(f"wrote {pdf}")
    return str(pdf)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pdf", required=True, help="input .pptx or .docx file")
    ap.add_argument("--outdir", default=None, help="output directory (default: alongside input)")
    args = ap.parse_args()
    convert(args.pdf, args.outdir)


if __name__ == "__main__":
    main()
