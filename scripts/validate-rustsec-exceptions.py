#!/usr/bin/env python3
"""Validate the committed RustSec exception policy before cargo-audit runs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import tomllib
from pathlib import Path


ADVISORY_RE = re.compile(r"^RUSTSEC-\d{4}-\d{4}$")
REQUIRED_FIELDS = {
    "id",
    "crate",
    "version",
    "category",
    "owner",
    "approved_by",
    "approved_on",
    "expires",
    "reason",
    "mitigation",
    "follow_up",
    "risk_acceptance",
}
PLACEHOLDERS = {"", "tbd", "todo", "pending", "none", "n/a"}
ALLOWED_CATEGORIES = {"unmaintained", "unsound", "vulnerability", "yanked", "notice"}


def fail(message: str) -> None:
    print(f"rustsec-policy: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_toml(path: Path) -> dict:
    if not path.exists():
        fail(f"missing cargo-audit config: {path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_json(path: Path) -> dict:
    if not path.exists():
        fail(f"missing RustSec exception evidence: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_date(raw: str, field: str, advisory_id: str) -> dt.date:
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        fail(f"{advisory_id}: {field} must use YYYY-MM-DD")


def cargo_lock_packages(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        fail(f"missing Cargo.lock: {path}")
    data = load_toml(path)
    return {
        (package.get("name", ""), package.get("version", ""))
        for package in data.get("package", [])
    }


def normalize_ids(values: list[str], label: str) -> set[str]:
    ids = set()
    for value in values:
        if not isinstance(value, str) or not ADVISORY_RE.match(value):
            fail(f"{label} contains invalid advisory id: {value!r}")
        if value in ids:
            fail(f"{label} contains duplicate advisory id: {value}")
        ids.add(value)
    return ids


def validate(args: argparse.Namespace) -> None:
    cargo_dir = Path(args.cargo_dir).resolve()
    policy_path = cargo_dir / ".cargo" / "rustsec-exceptions.json"
    audit_toml_path = cargo_dir / ".cargo" / "audit.toml"
    cargo_lock_path = cargo_dir / "Cargo.lock"

    policy = load_json(policy_path)
    audit_toml = load_toml(audit_toml_path)
    packages = cargo_lock_packages(cargo_lock_path)

    if policy.get("schema_version") != 1:
        fail("rustsec-exceptions.json schema_version must be 1")

    exceptions = policy.get("exceptions")
    if not isinstance(exceptions, list) or not exceptions:
        fail("rustsec-exceptions.json must contain a non-empty exceptions list")

    today = dt.date.today()
    exception_ids: list[str] = []

    for item in exceptions:
        if not isinstance(item, dict):
            fail("each exception must be an object")

        missing = sorted(REQUIRED_FIELDS - item.keys())
        if missing:
            fail(f"{item.get('id', '<unknown>')}: missing fields: {', '.join(missing)}")

        advisory_id = item["id"]
        if not isinstance(advisory_id, str) or not ADVISORY_RE.match(advisory_id):
            fail(f"invalid advisory id: {advisory_id!r}")
        if advisory_id in exception_ids:
            fail(f"duplicate exception id: {advisory_id}")

        for field in REQUIRED_FIELDS:
            value = item.get(field)
            if not isinstance(value, str) or value.strip().lower() in PLACEHOLDERS:
                fail(f"{advisory_id}: field {field} must be explicit")

        if item["category"] not in ALLOWED_CATEGORIES:
            fail(f"{advisory_id}: unsupported category {item['category']!r}")

        if (item["crate"], item["version"]) not in packages:
            fail(
                f"{advisory_id}: {item['crate']} {item['version']} is not present in Cargo.lock"
            )

        approved_on = parse_date(item["approved_on"], "approved_on", advisory_id)
        expires = parse_date(item["expires"], "expires", advisory_id)
        if approved_on > today:
            fail(f"{advisory_id}: approved_on is in the future")
        if expires <= today:
            fail(f"{advisory_id}: exception expired on {expires.isoformat()}")
        if (expires - today).days > args.max_days:
            fail(
                f"{advisory_id}: exception expiry is more than {args.max_days} days away"
            )

        if len(item["reason"]) < 40:
            fail(f"{advisory_id}: reason is too short for risk acceptance")
        if len(item["mitigation"]) < 40:
            fail(f"{advisory_id}: mitigation is too short for risk acceptance")
        if item["category"] == "unsound" and "unsound" not in item["risk_acceptance"]:
            fail(f"{advisory_id}: unsound advisory requires explicit unsound risk acceptance")

        exception_ids.append(advisory_id)

    ignore_ids = normalize_ids(
        audit_toml.get("advisories", {}).get("ignore", []),
        ".cargo/audit.toml advisories.ignore",
    )
    evidence_ids = normalize_ids(exception_ids, "rustsec-exceptions.json")

    if ignore_ids != evidence_ids:
        missing = sorted(evidence_ids - ignore_ids)
        extra = sorted(ignore_ids - evidence_ids)
        details = []
        if missing:
            details.append(f"missing from audit.toml: {', '.join(missing)}")
        if extra:
            details.append(f"not evidenced in rustsec-exceptions.json: {', '.join(extra)}")
        fail("; ".join(details))

    print(
        "rustsec-policy: "
        f"{len(evidence_ids)} explicit exceptions validated; "
        f"latest expiry {max(parse_date(item['expires'], 'expires', item['id']) for item in exceptions)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cargo-dir", default="src-tauri")
    parser.add_argument("--max-days", type=int, default=45)
    validate(parser.parse_args())


if __name__ == "__main__":
    main()
