# SignalOS Core v1.3 — `signalos install|publish|verify|list|uninstall` CLI
# (AMD-CORE-006, W1.3). Concept adapted from a5c-ai/babysitter (MIT).
# No source code copied.
#
# Argparse wrapper around signalos_lib.registry. Zero business logic.
#
# Top-level sub-commands (not nested under a `registry` prefix — users
# type `signalos install`, not `signalos registry install`):
#   install <tarball-path> [--allow-unsigned] [--key <ref>]
#   verify                 [--key <ref>]
#   list
#   uninstall <plugin-id>@<version>
#   publish   <package-dir> [--out <dir>] [--key <ref>]
#
# Exit codes (mirror the RegistryError hierarchy in registry.py):
#   0 — success
#   1 — usage error (bad CLI args)
#   2 — manifest invalid / generic execution error
#   3 — signature refused
#   4 — namespace refused
#   5 — compat refused

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .. import registry as reg_lib

# <plugin-id>@<version> — e.g. @signalos/test-skill@1.0.0 or community/foo@0.1.0
_PLUGIN_ID_AT_VERSION_RE = re.compile(
    r"^(?P<id>(?:@signalos|community)/[a-z0-9][a-z0-9-]*)@(?P<ver>[0-9A-Za-z.+-]+)$"
)


def _dump(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, sort_keys=True, indent=2, default=str) + "\n")


def _cmd_install(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="signalos install")
    p.add_argument("tarball", type=Path, help="Path to <name>-<version>.tar.gz")
    p.add_argument("--allow-unsigned", action="store_true",
                   help="Install an unsigned or signature-refused package. "
                        "Requires a co-signed Amendment in .signalos/AMENDMENTS.md; "
                        "the audit trail row is tagged unsigned: true.")
    p.add_argument("--key", dest="key_ref", default=None,
                   help="Cosign key reference (file path or sigstore ref).")
    p.add_argument("--cwd", type=Path, default=None, help=argparse.SUPPRESS)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0

    try:
        result = reg_lib.install(
            args.tarball,
            allow_unsigned=args.allow_unsigned,
            root=args.cwd,
            key_ref=args.key_ref,
        )
    except reg_lib.RegistryUnsignedError as exc:
        sys.stderr.write(f"signalos install: {exc}\n")
        return 3
    except reg_lib.RegistryNamespaceError as exc:
        sys.stderr.write(f"signalos install: {exc}\n")
        return 4
    except reg_lib.RegistryCompatError as exc:
        sys.stderr.write(f"signalos install: {exc}\n")
        return 5
    except reg_lib.RegistryManifestError as exc:
        sys.stderr.write(f"signalos install: {exc}\n")
        return 2
    except reg_lib.RegistryError as exc:
        sys.stderr.write(f"signalos install: {exc}\n")
        return 2
    _dump(result)
    return 0


def _cmd_verify(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="signalos verify")
    p.add_argument("--key", dest="key_ref", default=None)
    p.add_argument("--cwd", type=Path, default=None, help=argparse.SUPPRESS)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0

    rows = reg_lib.verify(root=args.cwd, key_ref=args.key_ref)
    _dump(rows)
    # Fail-on-any is conventional for `verify`; in test mode missing
    # packages return [] which still exits 0.
    return 0 if all(r.get("ok") for r in rows) else 2


def _cmd_list(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="signalos list")
    p.add_argument("--cwd", type=Path, default=None, help=argparse.SUPPRESS)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0

    rows = reg_lib.list_installed(root=args.cwd)
    _dump(rows)
    return 0


def _cmd_uninstall(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="signalos uninstall")
    p.add_argument("target", help="<plugin-id>@<version>, e.g. @signalos/foo@1.0.0")
    p.add_argument("--cwd", type=Path, default=None, help=argparse.SUPPRESS)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0

    m = _PLUGIN_ID_AT_VERSION_RE.match(args.target)
    if not m:
        sys.stderr.write(
            "signalos uninstall: target must match <plugin-id>@<version> "
            "(e.g. @signalos/foo@1.0.0)\n"
        )
        return 1
    try:
        result = reg_lib.uninstall(
            m.group("id"), m.group("ver"),
            root=args.cwd,
        )
    except reg_lib.RegistryNamespaceError as exc:
        sys.stderr.write(f"signalos uninstall: {exc}\n")
        return 4
    except reg_lib.RegistryError as exc:
        sys.stderr.write(f"signalos uninstall: {exc}\n")
        return 2
    _dump(result)
    return 0


def _cmd_publish(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="signalos publish")
    p.add_argument("package_dir", type=Path,
                   help="Directory containing manifest.json plus payload.")
    p.add_argument("--out", dest="out_dir", type=Path, default=Path("."),
                   help="Output directory for the tarball.")
    p.add_argument("--key", dest="key_ref", default=None,
                   help="Cosign key reference (file path or sigstore ref).")
    p.add_argument("--update-catalog", dest="catalog_path", type=Path, default=None,
                   metavar="PATH",
                   help="Update local catalog index after publish. AMD-CORE-021.")
    p.add_argument("--cwd", type=Path, default=None, help=argparse.SUPPRESS)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0

    try:
        tar_path = reg_lib.publish(
            args.package_dir, args.out_dir,
            key_ref=args.key_ref,
            catalog_path=args.catalog_path,
        )
    except reg_lib.RegistryManifestError as exc:
        sys.stderr.write(f"signalos publish: {exc}\n")
        return 2
    except reg_lib.RegistryNamespaceError as exc:
        sys.stderr.write(f"signalos publish: {exc}\n")
        return 4
    except reg_lib.RegistryError as exc:
        sys.stderr.write(f"signalos publish: {exc}\n")
        return 2
    _dump({"tarball": str(tar_path)})
    return 0


def _cmd_search(argv: list[str]) -> int:
    from signalos_lib.commands.catalog import cmd_search
    return cmd_search(argv)


def _cmd_info(argv: list[str]) -> int:
    from signalos_lib.commands.catalog import cmd_info
    return cmd_info(argv)


_DISPATCH = {
    "install":   _cmd_install,
    "verify":    _cmd_verify,
    "list":      _cmd_list,
    "uninstall": _cmd_uninstall,
    "publish":   _cmd_publish,
    "search":    _cmd_search,
    "info":      _cmd_info,
}


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(
            "signalos registry: subcommand required "
            "(install|verify|list|uninstall|publish|search|info)\n"
        )
        return 1
    sub, rest = argv[0], argv[1:]
    handler = _DISPATCH.get(sub)
    if handler is None:
        sys.stderr.write(f"signalos registry: unknown subcommand: {sub!r}\n")
        return 1
    return handler(rest)
