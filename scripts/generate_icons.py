#!/usr/bin/env python3
"""
generate_icons.py - convert src-tauri/icons/icon.svg into all required Tauri icon formats.

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
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
SVG = ROOT / "src-tauri" / "icons" / "icon.svg"
OUT = ROOT / "src-tauri" / "icons"

try:
    import cairosvg
    from PIL import Image
except ImportError:
    print("Missing deps. Run: pip install cairosvg Pillow")
    sys.exit(1)


def svg_to_png(size: int) -> bytes:
    return cairosvg.svg2png(url=str(SVG), output_width=size, output_height=size)


def save_png(size: int, filename: str) -> None:
    data = svg_to_png(size)
    path = OUT / filename
    path.write_bytes(data)
    print(f"  [OK] {filename}  ({size}x{size})")


def save_ico(sizes=(16, 32, 48, 64, 128, 256)) -> None:
    """Build a multi-resolution .ico from the SVG."""
    images = []
    for size in sizes:
        raw = svg_to_png(size)
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        images.append(img)

    path = OUT / "icon.ico"
    images[0].save(
        str(path),
        format="ICO",
        sizes=[(img.width, img.height) for img in images],
        append_images=images[1:],
    )
    print(f"  [OK] icon.ico  ({'/'.join(str(size) for size in sizes)})")


def save_icns() -> None:
    """
    Build an .icns for macOS.
    Uses iconutil if available on macOS, otherwise writes a minimal ICNS.
    """
    iconutil = shutil.which("iconutil")
    if iconutil:
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "SignalOS.iconset"
            iconset.mkdir()
            for size, suffix in [
                (16, "icon_16x16.png"),
                (32, "icon_16x16@2x.png"),
                (32, "icon_32x32.png"),
                (64, "icon_32x32@2x.png"),
                (128, "icon_128x128.png"),
                (256, "icon_128x128@2x.png"),
                (256, "icon_256x256.png"),
                (512, "icon_256x256@2x.png"),
                (512, "icon_512x512.png"),
                (1024, "icon_512x512@2x.png"),
            ]:
                (iconset / suffix).write_bytes(svg_to_png(size))
            dest = OUT / "icon.icns"
            subprocess.run([iconutil, "-c", "icns", str(iconset), "-o", str(dest)], check=True)
            print("  [OK] icon.icns  (via iconutil)")
    else:
        chunks = []
        for ostype, size in [(b"ic09", 512), (b"ic07", 32)]:
            png = svg_to_png(size)
            length = 8 + len(png)
            chunks.append(ostype + struct.pack(">I", length) + png)
        body = b"".join(chunks)
        header = b"icns" + struct.pack(">I", 8 + len(body))
        (OUT / "icon.icns").write_bytes(header + body)
        print("  [OK] icon.icns  (minimal fallback - run on macOS for full iconset)")


if __name__ == "__main__":
    print(f"Generating icons from {SVG.name} ...")
    save_png(32, "32x32.png")
    save_png(128, "128x128.png")
    save_png(256, "128x128@2x.png")
    save_png(512, "icon.png")
    save_ico()
    save_icns()
    print("\nDone. Icons written to src-tauri/icons/")
    print("Commit them: git add src-tauri/icons/ && git commit -m 'chore: add app icons'")
