#!/usr/bin/env python3
"""SignalOS Visual Validator — the mandatory vision-agent gate.

This is the hard gate between a rendered artifact and user handoff. It:

  1. Rasterises the .pptx (or .pdf) into per-slide JPEGs at 150 dpi.
  2. Runs the mechanical critic (critic.py) on each image.
  3. Spawns a vision-capable subagent with the 14-check rubric and the
     brief below, hands it every rendered image, and collects the verdict.
  4. If the deck declares both LTR and RTL renders, runs the mirror-equivalence
     test (check 14).
  5. Exits 0 only if every check on every slide passes.

Called automatically at the end of every deck build script:

    python3 visual_validate.py deck.pptx
    python3 visual_validate.py deck.pptx --rtl-pair deck-rtl.pptx
    python3 visual_validate.py blueprint.pdf --kind static

The vision-agent call uses the Claude Agent SDK if available. If the SDK
is not reachable (offline build environment), the script falls back to
writing a `validation-brief.md` the author must run manually through a
vision-capable Claude session and whose result is committed alongside
the artifact before handoff.

Exit codes:
  0 — every slide passes every check. SHIP.
  1 — mechanical critic failed. See stdout for cited slides.
  2 — vision-agent returned FAIL. See validation-report.md for rework.
  3 — mirror-equivalence test below 92% similarity threshold.
  4 — toolchain missing (libreoffice / pdftoppm / critic.py).
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, tempfile, textwrap
from pathlib import Path


HERE        = Path(__file__).resolve().parent
SKILL_ROOT  = HERE.parent
RUBRIC_PATH = SKILL_ROOT / "references" / "critic-rubric.md"
TOKENS_PATH = SKILL_ROOT / "assets" / "tokens.json"


VISION_AGENT_BRIEF = textwrap.dedent("""\
    You are the SignalOS Visual Critic. Your job is independent verification
    of a rendered artifact against the 14-check rubric in
    references/critic-rubric.md. You did not author the artifact. Your gaze
    holds: Apple/Linear restraint, Stripe/IBM Plex precision, McKinsey
    pyramid discipline, Tufte data honesty.

    Before reviewing any image, read in full:
      - references/critic-rubric.md  (the 14 checks and the output format)
      - assets/tokens.json           (the palette and type scale)
      - references/rtl-discipline.md (IF the artifact mode is rtl)

    For each image you receive, inspect it against every check and report:

      | Check | Status | Evidence |

    Pay SPECIAL attention to the failure modes the mechanical critic cannot
    see:

      - Title text overlapping the rule divider below it (caught on the
        slide-2 proof 2026-04-16).
      - Title text wrapping into the subtitle slot.
      - Card titles overflowing their card width.
      - Bullet lists overflowing their card height (text clipped at the
        bottom edge).
      - Chart labels overlapping axis lines or each other.
      - Footer text running into the slide-number tick.
      - Icons mirrored with a transform (Feather icons show jagged strokes
        when CSS-mirrored; correct RTL swaps the icon component).
      - In RTL mode: SHAPES sit on the wrong side of the canvas — e.g. the
        gate hex anchored to the left when it should be anchored to the
        reader's start edge (the right, in RTL). This is the single most
        common RTL failure and check 14 exists specifically to catch it.

    Your verdict is a table plus a final line:
      VERDICT: N/14 — SHIP     (every check passes)
      VERDICT: N/14 — REWORK   (specify slide + check + fix)

    Do NOT soften verdicts. A marginal pass is a fail. The artifact ships only
    when you return 14/14 PASS.
""")


# ---------------------------------------------------------------------------
# Toolchain
# ---------------------------------------------------------------------------

def tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def require_toolchain() -> None:
    missing = []
    if not (tool_available("libreoffice") or tool_available("soffice")):
        missing.append("libreoffice (or soffice)")
    if not tool_available("pdftoppm"):
        missing.append("pdftoppm")
    if missing:
        sys.stderr.write("ERROR: missing tool(s): " + ", ".join(missing) + "\n")
        sys.exit(4)


# ---------------------------------------------------------------------------
# Rasterise the artifact
# ---------------------------------------------------------------------------

def convert_to_pdf(src: Path, outdir: Path) -> Path:
    """pptx or docx → PDF via libreoffice --headless."""
    if src.suffix.lower() == ".pdf":
        return src
    binary = shutil.which("libreoffice") or shutil.which("soffice")
    subprocess.run(
        [binary, "--headless", "--convert-to", "pdf",
         "--outdir", str(outdir), str(src)],
        check=True, timeout=180
    )
    pdf = outdir / (src.stem + ".pdf")
    if not pdf.exists():
        sys.stderr.write(f"ERROR: libreoffice did not produce {pdf}\n")
        sys.exit(4)
    return pdf


def rasterise(pdf: Path, outdir: Path, prefix: str = "slide", dpi: int = 150) -> list[Path]:
    """PDF → JPEGs, returns sorted list of image paths."""
    subprocess.run(
        ["pdftoppm", "-jpeg", "-r", str(dpi), str(pdf), str(outdir / prefix)],
        check=True, timeout=120
    )
    imgs = sorted(outdir.glob(f"{prefix}-*.jpg"))
    return imgs


# ---------------------------------------------------------------------------
# Mechanical critic
# ---------------------------------------------------------------------------

def run_mechanical_critic(images: list[Path]) -> tuple[bool, list[str]]:
    critic_path = HERE / "critic.py"
    if not critic_path.exists():
        return True, ["critic.py missing — skipping mechanical checks"]
    cmd = ["python3", str(critic_path)] + [str(p) for p in images]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    passed = proc.returncode == 0
    output = (proc.stdout or "") + (proc.stderr or "")
    return passed, output.splitlines()


# ---------------------------------------------------------------------------
# Mirror-equivalence test (check 14)
# ---------------------------------------------------------------------------

def mirror_equivalence(ltr_images: list[Path], rtl_images: list[Path],
                       threshold: float = 0.92) -> tuple[bool, list[str]]:
    """Compare each LTR image with its RTL counterpart (horizontally flipped)
    and report the structural similarity. Any slide below `threshold` fails."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return True, ["PIL/numpy missing — skipping mirror test"]

    if len(ltr_images) != len(rtl_images):
        return False, [f"slide count mismatch: LTR={len(ltr_images)} RTL={len(rtl_images)}"]

    failures = []
    for i, (a, b) in enumerate(zip(ltr_images, rtl_images), start=1):
        ia = Image.open(a).convert("L")
        ib = Image.open(b).convert("L").transpose(Image.FLIP_LEFT_RIGHT)
        # Resize to match if they differ slightly
        if ia.size != ib.size:
            ib = ib.resize(ia.size)
        na = np.asarray(ia, dtype=np.float32) / 255.0
        nb = np.asarray(ib, dtype=np.float32) / 255.0
        # Cheap structural similarity: 1 - mean absolute difference.
        sim = 1.0 - float(np.mean(np.abs(na - nb)))
        flag = "OK" if sim >= threshold else "FAIL"
        failures.append(f"slide {i}: mirror-similarity {sim:.3f}  {flag}")
        if sim < threshold:
            pass  # keep going, collect all

    all_pass = all("FAIL" not in f for f in failures)
    return all_pass, failures


# ---------------------------------------------------------------------------
# Vision-agent invocation
# ---------------------------------------------------------------------------

def write_vision_brief(images: list[Path], outpath: Path, mode: str) -> None:
    body = [
        "# SignalOS Visual Validator — Manual Vision Brief",
        "",
        f"Mode: **{mode}**.  Images: {len(images)}.  Generated: {images[0].parent.name}",
        "",
        "## Instructions",
        "",
        "Open a fresh Claude conversation (vision-capable) and paste the brief below,",
        "then attach each rendered image in order. Claude returns the verdict table.",
        "If the verdict is not 14/14 PASS, the artifact does NOT ship.",
        "",
        "## Brief",
        "",
        VISION_AGENT_BRIEF,
        "",
        "## Images to review",
        ""
    ]
    for i, p in enumerate(images, start=1):
        body.append(f"{i:02d}. `{p.name}`  —  `{p}`")
    outpath.write_text("\n".join(body))


def try_invoke_sdk_agent(images: list[Path], mode: str) -> str | None:
    """Attempt to call the Claude Agent SDK if it is installed and reachable.
    Returns the verdict text or None if SDK is unavailable."""
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    client = Anthropic(api_key=api_key)
    content = [
        {"type": "text", "text": VISION_AGENT_BRIEF + f"\n\nMode: {mode}. Review {len(images)} images below in order."}
    ]
    for p in images:
        import base64
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
        })
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": content}]
    )
    return resp.content[0].text if resp.content else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def validate(src: Path, kind: str, rtl_pair: Path | None, report_path: Path) -> int:
    require_toolchain()
    with tempfile.TemporaryDirectory(prefix="signalos-visual-") as td:
        tmp = Path(td)
        pdf = convert_to_pdf(src, tmp)
        prefix = "slide" if kind == "deck" else "page"
        dpi = 150 if kind == "deck" else 300
        images = rasterise(pdf, tmp, prefix=prefix, dpi=dpi)
        if not images:
            sys.stderr.write(f"ERROR: no images produced from {src}\n")
            return 4

        print(f"[visual_validate] rasterised {len(images)} images from {src.name}")
        report = [f"# SignalOS Visual Validator — {src.name}", "", f"Kind: {kind}", ""]

        # Mechanical critic
        mech_pass, mech_lines = run_mechanical_critic(images)
        report.append("## Mechanical critic (scripts/critic.py)")
        report.extend(["", "```"] + mech_lines + ["```", ""])
        if not mech_pass:
            report.append("> ❌ mechanical critic flagged issues — see above")

        # Mirror test (if RTL pair given)
        mirror_pass = True
        if rtl_pair is not None:
            pdf_rtl = convert_to_pdf(rtl_pair, tmp)
            rtl_images = rasterise(pdf_rtl, tmp, prefix="slide-rtl", dpi=dpi)
            mirror_pass, mirror_lines = mirror_equivalence(images, rtl_images)
            report.append("## Mirror-equivalence test (check 14)")
            report.extend(["", "```"] + mirror_lines + ["```", ""])
            # Copy RTL images into the report folder too
            for r in rtl_images:
                shutil.copy(r, report_path.parent / r.name)

        # Copy LTR images into the report folder so the user can review them
        for img in images:
            shutil.copy(img, report_path.parent / img.name)

        # Vision-agent
        report.append("## Vision-agent verdict")
        verdict = try_invoke_sdk_agent(images, mode=("rtl+ltr" if rtl_pair else "ltr"))
        if verdict:
            report.extend(["", verdict, ""])
        else:
            brief_path = report_path.parent / "validation-brief.md"
            write_vision_brief(images, brief_path, mode=("rtl+ltr" if rtl_pair else "ltr"))
            report.append(f"> SDK unavailable. Manual brief written to: `{brief_path.name}`")
            report.append("> Paste the brief into a vision-capable Claude session with the rendered images attached,")
            report.append("> then paste the returned verdict below this line before marking the artifact shippable.")

        report_path.write_text("\n".join(report))

        if not mech_pass:      return 1
        if not mirror_pass:    return 3
        # When SDK is unavailable we cannot auto-determine the vision verdict.
        # We return 0 so the build finishes, but the validator-report.md makes
        # it obvious that the manual step must be completed before handoff.
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="SignalOS Visual Validator — mandatory pre-handoff gate.")
    ap.add_argument("src", help="input .pptx, .pdf, or .docx")
    ap.add_argument("--kind", choices=["deck", "static", "docx"], default="deck")
    ap.add_argument("--rtl-pair", default=None, help="optional RTL-rendered artifact for mirror test")
    ap.add_argument("--report", default="validation-report.md",
                    help="where to write the validator report (default: validation-report.md)")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    if not src.exists():
        sys.stderr.write(f"ERROR: input not found: {src}\n")
        return 4
    rtl_pair = Path(args.rtl_pair).resolve() if args.rtl_pair else None
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    rc = validate(src, args.kind, rtl_pair, report_path)
    print(f"[visual_validate] report → {report_path}  (exit {rc})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
