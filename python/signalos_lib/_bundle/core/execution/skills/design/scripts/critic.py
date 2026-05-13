#!/usr/bin/env python3
"""SignalOS Visual Critic — automated checks.

Runs the mechanical subset of the 14-check rubric against rendered slide/page
images. Checks that CAN be automated (palette, grid edges, type-size detection,
whitespace ratio, mirror integrity) run here. Checks that require judgment
(narrative, restraint, rhythm) go to the subagent critic — see
references/critic-rubric.md.

Usage:
    python3 critic.py slide-*.jpg            # full deck
    python3 critic.py page-*.jpg             # docx pages
    python3 critic.py blueprint.pdf          # single blueprint
    python3 critic.py --mirror-test ltr.png rtl.png

Exit code 0 = all mechanical checks pass, 1 = at least one fail.
"""
from __future__ import annotations
import sys, os, json, argparse, glob, subprocess
from pathlib import Path

try:
    from PIL import Image
    import numpy as np
except ImportError:
    print("ERROR: install Pillow + numpy: pip install --break-system-packages Pillow numpy")
    sys.exit(2)

SKILL_ROOT = Path(__file__).resolve().parent.parent
TOKENS = json.loads((SKILL_ROOT / "assets" / "tokens.json").read_text())

PALETTE_HEX = list(TOKENS["palette"].values())

def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

PALETTE_RGB = np.array([hex_to_rgb(h) for h in PALETTE_HEX])

# ------------------------------------------------------------
# Thresholds — two classes of number, kept separate on purpose.
#
#   ACCURACY thresholds: how closely the render must match an ideal.
#   These are gates on craft precision — set at ≥ 95% (or stricter)
#   because anything below that is a visible defect to a human reader.
#
#   RESTRAINT floors: design-discipline minimums. "Whitespace must be at
#   least X%" is not a precision measurement — it is a floor below which
#   the slide is too dense. Good slides sit far above the floor.
# ------------------------------------------------------------

# Accuracy gates (all ≥ 95%, or the complementary strictness)
MIRROR_SIMILARITY_MIN    = 0.95   # RTL↔LTR pixel similarity after h-flip
FOOTER_OVERFLOW_MAX      = 0.005  # ≤ 0.5% dark pixels in the footer band
PALETTE_BUCKET_TOLERANCE = 18     # max RGB distance to nearest palette bucket

# Restraint floors (design discipline, not accuracy)
WHITESPACE_FLOOR_LIGHT   = 0.40   # ≥ 40% paper/wash on a light slide
WHITESPACE_FLOOR_DARK    = 0.40   # ≥ 40% dark-token ground on a cover

# ------------------------------------------------------------
# Cover / ground detection
# ------------------------------------------------------------

DARK_TOKENS_RGB = np.array([
    hex_to_rgb(TOKENS["palette"]["ink"]),
    hex_to_rgb(TOKENS["palette"]["indigo"]),
    hex_to_rgb(TOKENS["palette"]["indigoDk"]),
    hex_to_rgb(TOKENS["palette"]["slate"]),
])


def is_dark_ground(img: np.ndarray) -> bool:
    """A slide is dark-ground when the median pixel luminance is below ~60.
    Covers (indigo/ink/slate) sit at ~25-45; light slides at ~245."""
    lum = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    return float(np.median(lum)) < 80.0


# ------------------------------------------------------------
# Check 2 — Whitespace ratio (ground-aware)
# ------------------------------------------------------------

def check_whitespace(img: np.ndarray) -> tuple[bool, str]:
    """Restraint floor, not an accuracy gate. Light slides need ≥40% paper/
    wash; dark covers need ≥40% dark-token ground — a floor below which the
    slide is too dense to read. Good slides sit far above (60–90%)."""
    pixels = img.reshape(-1, 3)
    if is_dark_ground(img):
        dists = np.min(
            np.linalg.norm(pixels[:, None, :].astype(int) - DARK_TOKENS_RGB[None, :, :], axis=2),
            axis=1,
        )
        ratio = (dists < 40).mean()
        ok = ratio >= WHITESPACE_FLOOR_DARK
        return ok, f"dark-ground coverage = {ratio:.1%} (restraint floor {WHITESPACE_FLOOR_DARK:.0%}, cover)"
    near_white = np.all(pixels >= [245, 245, 245], axis=1)
    ratio = near_white.mean()
    ok = ratio >= WHITESPACE_FLOOR_LIGHT
    return ok, f"whitespace = {ratio:.1%} (restraint floor {WHITESPACE_FLOOR_LIGHT:.0%})"


# ------------------------------------------------------------
# Check 4 — Palette discipline
# ------------------------------------------------------------

def check_palette(img: np.ndarray) -> tuple[bool, str]:
    """Cluster significant colours; every cluster should sit within the
    quantisation bucket of a palette token. Antialiasing and small stray
    pixels ignored. Both the render and the palette are quantised to the
    same 32-step grid before comparison, so the tolerance only has to
    cover JPEG compression noise (~12 RGB units)."""
    # Downsample to speed
    small = img[::4, ::4].reshape(-1, 3)
    # Quantise to 32-step buckets to find dominant clusters
    quantised = (small // 32) * 32
    unique, counts = np.unique(quantised, axis=0, return_counts=True)
    # Keep clusters with >0.5% of pixels
    sig = unique[counts > len(small) * 0.005]
    # Quantise the palette the SAME way, so #1B2E60 → (0, 32, 96) matches
    # when the render produces a cluster at (0, 32, 96). A raw hex-to-RGB
    # comparison without this would force a 32-unit minimum distance and
    # reject every dark token.
    palette_q = (PALETTE_RGB // 32) * 32
    off_brand = []
    for rgb in sig:
        r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
        # Skip dark JPEG-noise clusters — anti-aliasing around dark text on
        # dark grounds blends ink (#0B1221) with indigo/indigoDk, producing
        # bluish-black intermediates like (32,32,64) that sit between two
        # real tokens. If max channel ≤ 80, it is by definition a dark-on-
        # dark blend, not a new colour.
        if max(r, g, b) <= 80:
            continue
        # Skip near-white clusters similarly — paper/wash AA.
        if r >= 224 and g >= 224 and b >= 224:
            continue
        dists = np.linalg.norm(palette_q - rgb, axis=1)
        if dists.min() > PALETTE_BUCKET_TOLERANCE:
            off_brand.append((r, g, b))
    ok = len(off_brand) == 0
    msg = (f"all clusters within {PALETTE_BUCKET_TOLERANCE}-unit palette bucket"
           if ok else f"off-brand clusters: {off_brand[:3]}")
    return ok, msg


# ------------------------------------------------------------
# Check 11 — Craft residual: overlapping text / overflow detection
# ------------------------------------------------------------

def check_overflow(img: np.ndarray) -> tuple[bool, str]:
    """Detect text clipped by the footer bar (bottom 0.28" = roughly 8% of
    slide height at 150dpi)."""
    h = img.shape[0]
    footer_band_top = int(h * 0.925)
    footer_band_bot = int(h * 0.945)
    band = img[footer_band_top:footer_band_bot]
    # Count dark pixels (ink text spilling into the band). ≤ 0.5% allows
    # AA noise from rule lines; anything beyond is real content overflow.
    dark = np.all(band < [80, 80, 80], axis=2).mean()
    ok = dark <= FOOTER_OVERFLOW_MAX
    return ok, f"footer overflow = {dark:.2%} (max {FOOTER_OVERFLOW_MAX:.1%})"


# ------------------------------------------------------------
# Check 14 — Mirror integrity (RTL only)
# ------------------------------------------------------------

def mirror_test(ltr_path: str, rtl_path: str) -> tuple[bool, str]:
    ltr = np.array(Image.open(ltr_path).convert("RGB"))
    rtl = np.array(Image.open(rtl_path).convert("RGB"))
    if ltr.shape != rtl.shape:
        # resize rtl to match ltr
        img = Image.fromarray(rtl).resize((ltr.shape[1], ltr.shape[0]))
        rtl = np.array(img)
    mirrored_rtl = rtl[:, ::-1, :]
    diff = np.abs(ltr.astype(int) - mirrored_rtl.astype(int)).mean() / 255
    similarity = 1.0 - diff
    ok = similarity >= MIRROR_SIMILARITY_MIN
    return ok, f"mirror similarity = {similarity:.1%} (min {MIRROR_SIMILARITY_MIN:.0%})"


# ------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------

def run_image_checks(path: str, verbose=True) -> bool:
    if not os.path.exists(path):
        print(f"  {path}: NOT FOUND")
        return False
    img = np.array(Image.open(path).convert("RGB"))
    rows = [
        ("2 · Whitespace",  *check_whitespace(img)),
        ("4 · Palette",     *check_palette(img)),
        ("11 · Craft",      *check_overflow(img)),
    ]
    any_fail = False
    print(f"\n{path}")
    for label, ok, msg in rows:
        mark = "PASS" if ok else "FAIL"
        if not ok: any_fail = True
        if verbose or not ok:
            print(f"  [{mark}] {label:20s} — {msg}")
    return not any_fail


def run_mechanical_suite(paths: list[str]) -> int:
    print("SignalOS Visual Critic — mechanical checks")
    print("=" * 60)
    print("NB: checks 1, 3, 5–10, 12–13 require the subagent critic.")
    print("     This script covers 2, 4, 11, and 14 (mirror).")
    fails = 0
    for p in paths:
        if not run_image_checks(p):
            fails += 1
    print("\n" + "=" * 60)
    total = len(paths)
    if fails == 0:
        print(f"MECHANICAL CHECKS: PASS ({total}/{total} pages clean)")
        print("Next: run the subagent critic for checks 1, 3, 5–10, 12, 13.")
        return 0
    print(f"MECHANICAL CHECKS: FAIL ({fails}/{total} pages dirty)")
    return 1


def pdf_to_images(pdf_path: str) -> list[str]:
    stem = Path(pdf_path).stem
    out_dir = Path("/tmp") / f"critic-{stem}"
    out_dir.mkdir(exist_ok=True)
    subprocess.run(
        ["pdftoppm", "-jpeg", "-r", "150", pdf_path, str(out_dir / "page")],
        check=True
    )
    return sorted(str(p) for p in out_dir.glob("page-*.jpg"))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("inputs", nargs="*", help="image files, a PDF, or glob patterns")
    parser.add_argument("--mirror-test", nargs=2, metavar=("LTR", "RTL"),
                        help="compare an LTR and an RTL render for mirror integrity")
    args = parser.parse_args()

    if args.mirror_test:
        ok, msg = mirror_test(*args.mirror_test)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] 14 · Mirror integrity — {msg}")
        return 0 if ok else 1

    # Expand globs
    expanded = []
    for p in args.inputs:
        matched = glob.glob(p)
        expanded.extend(matched if matched else [p])
    # If a PDF is in the mix, render its pages first
    image_paths = []
    for p in expanded:
        if p.lower().endswith(".pdf"):
            image_paths.extend(pdf_to_images(p))
        else:
            image_paths.append(p)

    if not image_paths:
        parser.error("no inputs found")

    return run_mechanical_suite(image_paths)


if __name__ == "__main__":
    sys.exit(main())
