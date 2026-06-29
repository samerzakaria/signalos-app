"""CLI wrapper for technology-neutral release artifact proof."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from signalos_lib.product.release_proof import (
    produce_clean_machine_proof,
    produce_signature_proof,
    validate_release_proof,
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos release-proof",
        description="Validate release artifact, signature, installer, and clean-machine proof.",
    )
    sub = parser.add_subparsers(dest="action")

    p_validate = sub.add_parser("validate", help="Validate supplied release proof files")
    p_validate.add_argument("--repo-root", type=Path, default=None)
    p_validate.add_argument("--wave", default=None)
    p_validate.add_argument("--artifact", required=True, help="Release artifact file to hash and verify.")
    p_validate.add_argument("--artifact-kind", default=None, help="Optional package kind label.")
    p_validate.add_argument("--signature", default=None, help="Signature or signature proof file.")
    p_validate.add_argument("--clean-machine-proof", default=None, help="JSON proof from a clean install machine.")
    p_validate.add_argument("--installer-proof", default=None, help="JSON installer/package proof.")
    p_validate.add_argument("--readiness-evidence", default=None, help="release-readiness JSON evidence to link.")
    p_validate.add_argument("--require-signature", action="store_true")
    p_validate.add_argument("--require-clean-machine", action="store_true")
    p_validate.add_argument("--require-installer-proof", action="store_true")
    p_validate.add_argument("--require-readiness", action="store_true")
    p_validate.add_argument("--no-evidence", action="store_true")
    p_validate.add_argument("--json", action="store_true", dest="as_json")

    p_signature = sub.add_parser(
        "produce-signature",
        help="Write JSON signature proof from an artifact and real signature file",
    )
    p_signature.add_argument("--repo-root", type=Path, default=None)
    p_signature.add_argument("--wave", default=None)
    p_signature.add_argument("--artifact", required=True, help="Release artifact file.")
    p_signature.add_argument("--signature-file", required=True, help="Detached signature/proof file already produced by a signer.")
    p_signature.add_argument("--output", default=None, help="Output JSON proof path.")
    p_signature.add_argument("--signed-by", default=None)
    p_signature.add_argument("--signing-tool", default=None)
    p_signature.add_argument("--json", action="store_true", dest="as_json")

    p_clean = sub.add_parser(
        "produce-clean-machine",
        help="Write JSON proof that a clean environment performed release checks",
    )
    p_clean.add_argument("--repo-root", type=Path, default=None)
    p_clean.add_argument("--wave", default=None)
    p_clean.add_argument("--artifact", default=None, help="Optional release artifact checked on the clean machine.")
    p_clean.add_argument("--output", default=None, help="Output JSON proof path.")
    p_clean.add_argument("--fresh-workspace", action="store_true", help="Assert the producer is running in a clean workspace/runner.")
    p_clean.add_argument("--environment-label", default=None)
    p_clean.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)
    if args.action is None:
        parser.print_help()
        return 1

    if args.action == "produce-signature":
        payload = produce_signature_proof(
            repo_root=args.repo_root,
            artifact=args.artifact,
            signature_file=args.signature_file,
            output=args.output,
            signed_by=args.signed_by,
            signing_tool=args.signing_tool,
            wave=args.wave,
        )
        if args.as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            _print_producer_summary("signature proof", payload)
        return 0 if payload.get("ok") else 1

    if args.action == "produce-clean-machine":
        payload = produce_clean_machine_proof(
            repo_root=args.repo_root,
            artifact=args.artifact,
            output=args.output,
            fresh_workspace=args.fresh_workspace,
            environment_label=args.environment_label,
            wave=args.wave,
        )
        if args.as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            _print_producer_summary("clean-machine proof", payload)
        return 0 if payload.get("ok") else 1

    payload = validate_release_proof(
        repo_root=args.repo_root,
        artifact=args.artifact,
        artifact_kind=args.artifact_kind,
        signature=args.signature,
        clean_machine_proof=args.clean_machine_proof,
        installer_proof=args.installer_proof,
        readiness_evidence=args.readiness_evidence,
        require_signature=args.require_signature,
        require_clean_machine=args.require_clean_machine,
        require_installer_proof=args.require_installer_proof,
        require_readiness=args.require_readiness,
        wave=args.wave,
        write_evidence=not args.no_evidence,
    )

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_summary(payload)
    return 0 if payload.get("ok") else 1


def _print_producer_summary(label: str, payload: dict) -> None:
    sys.stdout.write(f"SignalOS {label}: {payload.get('status')}\n")
    if payload.get("path"):
        sys.stdout.write(f"Evidence: {payload['path']}\n")
    for blocker in payload.get("blockers", []):
        sys.stdout.write(f"- {blocker['id']}: {blocker['message']}\n")


def _print_summary(payload: dict) -> None:
    sys.stdout.write(f"SignalOS release proof: {payload.get('status')}\n")
    artifact = payload.get("artifact") or {}
    if artifact:
        sys.stdout.write(
            f"Artifact: {artifact.get('path')} "
            f"({artifact.get('kind')}, sha256={artifact.get('sha256')})\n"
        )
    if payload.get("evidence_path"):
        sys.stdout.write(f"Evidence: {payload['evidence_path']}\n")
    for check in payload.get("checks", []):
        sys.stdout.write(f"- {check['id']}: {check['status']} {check['message']}\n")
    if payload.get("blockers"):
        sys.stdout.write("Blockers:\n")
        for blocker in payload["blockers"]:
            sys.stdout.write(f"- {blocker['id']}: {blocker['message']}\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
