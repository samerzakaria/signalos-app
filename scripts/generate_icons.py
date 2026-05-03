#!/usr/bin/env python3
"""
generate_icons.py — convert src-tauri/icons/icon.svg into all required Tauri icon formats.

Requirements:
    pip install cairosvg Pillow

Usage:
    python scripts/generate_icons.py

Outputs written to src-tauri/icons/:
    32x32.png
    128x128.png
    128x128@2x.png   (256x256)
    icon.icns        (macOS, requires iconutil or cairosvg)
    icon.ico         (Windows, multi-size)
    icon.png         (512x512 master)
"""

import io
import os
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).parent.parent
SVG  = ROOT / "src-tauri" / "icons" / "icon.svg"
OUT  = ROOT / "src-tauri" / "icons"

try:
    import cairosvg
    from PIL import Image
except ImportError:
    print("Missing deps. Run: pip install cairosvg Pillow")
    sys.exit(1)


def svg_to_png(size: int) -> bytes:
    return cairosvg.svg2png(url=str(SVG), output_width=size, output_height=size)


def save_png(size: int, filename: str):
    data = svg_to_png(size)
    path = OUT / filename
    path.write_bytes(data)
    print(f"  ✓ {filename}  ({size}×{size})")


def save_ico(sizes=(16, 32, 48, 64, 128, 256)):
    """Build a multi-resolution .ico from the SVG."""
    images = []
    for s in sizes:
        raw = svg_to_png(s)
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        images.append(img)

    path = OUT / "icon.ico"
    # PIL can write ICO directly
    images[0].save(
        str(path),
        format="ICO",
        sizes=[(img.width, img.height) for img in images],
        append_images=images[1:],
    )
    print(f"  ✓ icon.ico  ({'/'.join(str(s) for s in sizes)})")


def save_icns():
    """
    Build an .icns for macOS.
    Uses iconutil if available (macOS), otherwise writes a minimal ICNS manually.
    """
    import subprocess, tempfile, shutil

    iconutil = shutil.which("iconutil")
    if iconutil:
        # Build an iconset directory and call iconutil
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "SignalOS.iconset"
            iconset.mkdir()
            for (size, suffix) in [
                (16,  "icon_16x16.png"),
                (32,  "icon_16x16@2x.png"),
                (32,  "icon_32x32.png"),
                (64,  "icon_32x32@2x.png"),
                (128, "icon_128x128.png"),
                (256, "icon_128x128@2x.png"),
                (256, "icon_256x256.png"),
                (512, "icon_256x256@2x.png"),
                (512, "icon_512x512.png"),
                (1024,"icon_512x512@2x.png"),
            ]:
                (iconset / suffix).write_bytes(svg_to_png(size))
            dest = OUT / "icon.icns"
            subprocess.run([iconutil, "-c", "icns", str(iconset), "-o", str(dest)], check=True)
            print("  ✓ icon.icns  (via iconutil)")
    else:
        # Fallback: write a minimal ICNS with just the 512 and 32 sizes
        # ICNS format: 4-byte magic, 4-byte total-length, then OSType+length+data chunks
        chunks = []
        for (ostype, size) in [(b"ic09", 512), (b"ic07", 32)]:
            png = svg_to_png(size)
            length = 8 + len(png)
            chunks.append(ostype + struct.pack(">I", length) + png)
        body = b"".join(chunks)
        header = b"icns" + struct.pack(">I", 8 + len(body))
        (OUT / "icon.icns").write_bytes(header + body)
        print("  ✓ icon.icns  (minimal fallback — run on macOS for full iconset)")


if __name__ == "__main__":
    print(f"Generating icons from {SVG.name} …")
    save_png(32,  "32x32.png")
    save_png(128, "128x128.png")
    save_png(256, "128x128@2x.png")
    save_png(512, "icon.png")
    save_ico()
    save_icns()
    print("\nDone. Icons written to src-tauri/icons/")
    print("Commit them: git add src-tauri/icons/ && git commit -m 'chore: add app icons'")
