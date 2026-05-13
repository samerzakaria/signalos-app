# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/commands/sign.py
# W3.1 — signalos sign subcommand (AMD-CORE-014)

from __future__ import annotations

__all__ = ["main"]

import sys
from pathlib import Path


def main(argv: list[str]) -> int:  # noqa: C901
    import argparse
    from signalos_lib.sign import GATE_MAP, VALID_ROLES, VALID_VERDICTS, GATE_LABELS
    import signalos_lib.sign as sign_lib

    parser = argparse.ArgumentParser(
        prog="signalos sign",
        description=(
            "Guided gate signing wizard (W3.1, AMD-CORE-014).\n"
            "Validates artifact presence, computes artifact_hash, appends\n"
            "a YAML signature block, and records the event in AUDIT_TRAIL.jsonl."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "gate",
        choices=list(GATE_MAP.keys()),
        metavar="GATE",
        help="Gate to sign: G0 G1 G2 G3 G4 G5",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print signature status for the gate (no write).",
    )
    parser.add_argument("--signer", default=None, help="Full name (skips interactive prompt).")
    parser.add_argument(
        "--role",
        choices=list(VALID_ROLES),
        default=None,
        help="Role: PO PE QA DevOps",
    )
    parser.add_argument(
        "--verdict",
        choices=list(VALID_VERDICTS),
        default=None,
        help="Verdict (default APPROVED).",
    )
    parser.add_argument(
        "--conditions",
        default="",
        help="Required when --verdict APPROVED-WITH-CONDITIONS.",
    )
    parser.add_argument("--repo-root", default=None, help="Repository root path.")
    parser.add_argument(
        "--oidc",
        action="store_true",
        help=(
            "Authenticate via OIDC before signing (W6.3, AMD-CORE-027). "
            "Opens a browser OAuth flow; embeds oidc_sub_hash + oidc_issuer in the "
            "signature block. Requires SIGNALOS_OIDC_ISSUER and "
            "SIGNALOS_OIDC_CLIENT_ID to be set."
        ),
    )

    args = parser.parse_args(argv)
    gate = args.gate.upper()

    # ------------------------------------------------------------------
    # Resolve repo root
    # ------------------------------------------------------------------
    if args.repo_root:
        root = Path(args.repo_root)
    else:
        try:
            import subprocess
            out = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            root = Path(out.strip())
        except Exception:
            root = Path.cwd()

    statuses = sign_lib.check_gate(root, gate)

    # ------------------------------------------------------------------
    # --check: read-only status report
    # ------------------------------------------------------------------
    if args.check:
        return _render_check(gate, statuses, GATE_LABELS)

    # ------------------------------------------------------------------
    # Sign mode
    # ------------------------------------------------------------------
    missing = [s for s in statuses if not s.exists]
    present = [s for s in statuses if s.exists]

    if missing:
        print(f"\n  ⚠  {len(missing)} artifact(s) not yet created for {gate}:")
        for s in missing:
            print(f"     - {s.label}  ({s.rel_path})")

    if not present:
        print(f"\n  No artifacts to sign for {gate}. Create them first.\n")
        return 1

    # ------------------------------------------------------------------
    # Gather signer identity (interactive or from flags)
    # ------------------------------------------------------------------
    signer = args.signer
    role = args.role
    verdict = args.verdict or "APPROVED"
    conditions = args.conditions

    if not signer or not role:
        # Interactive wizard
        print(f"\n  SignalOS — {gate} ({GATE_LABELS[gate]}) Signing Wizard")
        print(f"  {chr(0x2500) * 48}")
        print(f"  Artifacts ({len(present)}):")
        for s in present:
            print(f"    {s.label:<38}  {s.rel_path}")
        print()

        if not signer:
            signer = _prompt("  Your full name: ").strip()
            if not signer:
                print("  Aborted — signer name is required.", file=sys.stderr)
                return 1

        if not role:
            raw = _prompt(
                f"  Your role [{'/'.join(sign_lib.VALID_ROLES)}]: "
            ).strip().upper()
            if raw not in sign_lib.VALID_ROLES:
                print(f"  Invalid role: {raw!r}", file=sys.stderr)
                return 1
            role = raw

        if args.verdict is None:
            raw_v = _prompt("  Verdict [APPROVED / APPROVED-WITH-CONDITIONS / WAIVED]: ").strip().upper()
            verdict = raw_v if raw_v in sign_lib.VALID_VERDICTS else "APPROVED"

        if verdict == "APPROVED-WITH-CONDITIONS" and not conditions:
            conditions = _prompt("  Conditions: ").strip()

    # ------------------------------------------------------------------
    # Optional OIDC authentication (W6.3)
    # ------------------------------------------------------------------
    oidc_sub_hash = ""
    oidc_issuer = ""
    if args.oidc:
        try:
            from signalos_lib.oidc_provider import fetch_oidc_token, OIDCError
            print("\n  Launching browser for OIDC authentication…")
            oidc_result = fetch_oidc_token()
            oidc_sub_hash = oidc_result["oidc_sub_hash"]
            oidc_issuer = oidc_result["oidc_issuer"]
            display_name = oidc_result.get("name") or oidc_result.get("email") or "authenticated"
            print(f"  ✓  OIDC identity verified: {display_name}")
        except OIDCError as exc:
            print(f"\n  ✗  OIDC authentication failed: {exc}", file=sys.stderr)
            return 1

    # ------------------------------------------------------------------
    # Write signatures
    # ------------------------------------------------------------------
    audit_log = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    signed: list[str] = []
    errors: list[str] = []

    for s in present:
        try:
            sign_lib.sign_artifact(
                s.path, signer, role, gate, verdict, conditions,
                oidc_sub_hash=oidc_sub_hash, oidc_issuer=oidc_issuer,
            )
            sign_lib._append_audit(audit_log, signer, role, gate, s.rel_path, s.path, verdict)
            signed.append(s.label)
        except Exception as exc:
            errors.append(f"{s.label}: {exc}")

    if signed:
        print(f"\n  ✓  Signed {len(signed)} artifact(s) for {gate}:")
        for name in signed:
            print(f"     {name}")

    if errors:
        print(f"\n  ✗  {len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"     {e}", file=sys.stderr)
        return 1

    print(f"\n  Verify: signalos sign --check {gate}")
    print()
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_check(gate: str, statuses: list, gate_labels: dict) -> int:
    label = gate_labels.get(gate, gate)
    width = 48
    print(f"\n  {gate}  ({label}) — signature status")
    print(f"  {chr(0x2500) * width}")
    all_ok = True
    for s in statuses:
        if not s.exists:
            print(f"  ✗  {s.label:<36}  MISSING")
            all_ok = False
        elif s.is_draft:
            print(f"  ✗  {s.label:<36}  DRAFT (replace with real name)")
            all_ok = False
        elif not s.has_signatures:
            print(f"  ✗  {s.label:<36}  UNSIGNED")
            all_ok = False
        elif s.hash_valid is False:
            print(f"  ⚠  {s.label:<36}  HASH MISMATCH (modified after signing?)")
            all_ok = False
        else:
            tag = "  ⚠  hash not declared" if s.hash_valid is None else ""
            signers_str = ", ".join(s.signers)
            print(f"  ✓  {s.label:<36}  {signers_str}{tag}")
    print()
    return 0 if all_ok else 1


def _prompt(msg: str) -> str:
    try:
        return input(msg)
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
