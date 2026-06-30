"""Fail-closed tests for the license-checked skill lockfile.

Covers refusal paths (NOT happy-path only): hash mismatch, non-permissive
license, and missing/empty license, plus CLI reachability and the
verified/blocked audit + evidence behavior.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from signalos_lib.cli import _build_parser, main as cli_main
from signalos_lib.commands import skill_lock
from signalos_lib import skills_lock


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _install_skill(root: Path, skill_id: str, body: str) -> str:
    """Write a single-file skill under the workspace and return its sha256.

    Writes bytes (not text) so the on-disk content — and thus the digest —
    is identical across platforms (no newline translation), matching how the
    verifier hashes the raw file bytes.
    """
    path = root / ".signalos" / "skills" / skill_id / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8"))
    return _sha256_text(body)


def _write_lock(root: Path, skills: dict) -> Path:
    path = root / skills_lock.LOCK_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "skills": skills}, indent=2),
        encoding="utf-8",
    )
    return path


def _entry(skill_id: str, sha: str, license_id) -> dict:
    return {
        "source": f"https://github.com/example/{skill_id}",
        "source_type": "github",
        "skill_path": f".signalos/skills/{skill_id}/SKILL.md",
        "sha256": sha,
        "license": license_id,
        "license_source": "license-file",
    }


def _audit_rows(root: Path) -> list[dict]:
    audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not audit.is_file():
        return []
    return [
        json.loads(line)
        for line in audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Normalizer / allowlist unit checks
# ---------------------------------------------------------------------------

def test_spdx_normalizer_maps_loose_spellings() -> None:
    assert skills_lock.normalize_spdx("mit") == "MIT"
    assert skills_lock.normalize_spdx("apache2") == "Apache-2.0"
    assert skills_lock.normalize_spdx("apache-2.0") == "Apache-2.0"
    assert skills_lock.normalize_spdx("") == ""
    assert skills_lock.normalize_spdx(None) == ""


def test_permissive_allowlist_rejects_unknown_and_empty() -> None:
    assert skills_lock.is_permissive("MIT") is True
    assert skills_lock.is_permissive("apache2") is True
    assert skills_lock.is_permissive("BUSL-1.1") is False
    assert skills_lock.is_permissive("LicenseRef-SourceAvailable") is False
    assert skills_lock.is_permissive("") is False
    assert skills_lock.is_permissive(None) is False


# ---------------------------------------------------------------------------
# Happy path: hash match + permissive license
# ---------------------------------------------------------------------------

def test_verify_pass_writes_evidence_and_verified_audit(tmp_path: Path) -> None:
    sha = _install_skill(tmp_path, "good-skill", "# Good skill\nbody\n")
    _write_lock(tmp_path, {"good-skill": _entry("good-skill", sha, "MIT")})

    rc = cli_main(["signalos", "skill-lock", "verify", "--repo-root", str(tmp_path)])
    assert rc == skill_lock.EXIT_OK

    evidence = tmp_path / skill_lock.EVIDENCE_REL_PATH
    assert evidence.is_file()
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["refusal_count"] == 0

    rows = _audit_rows(tmp_path)
    assert rows[-1]["action"] == "skill-lock-verified"


def test_verify_result_structure_marks_per_skill_ok(tmp_path: Path) -> None:
    sha = _install_skill(tmp_path, "good-skill", "content\n")
    _write_lock(tmp_path, {"good-skill": _entry("good-skill", sha, "apache2")})

    result = skills_lock.verify_skills_lock(tmp_path)
    assert result.ok is True
    assert result.skills[0].status == "ok"
    assert result.skills[0].license_normalized == "Apache-2.0"


# ---------------------------------------------------------------------------
# Refusal: hash mismatch
# ---------------------------------------------------------------------------

def test_hash_mismatch_is_blocked_nonzero_and_audited(tmp_path: Path) -> None:
    _install_skill(tmp_path, "drift-skill", "real content\n")
    # Pin a hash for *different* content.
    wrong = _sha256_text("different content\n")
    _write_lock(tmp_path, {"drift-skill": _entry("drift-skill", wrong, "MIT")})

    result = skills_lock.verify_skills_lock(tmp_path)
    assert result.ok is False
    assert result.skills[0].status == "hash-mismatch"

    rc = cli_main(["signalos", "skill-lock", "verify", "--repo-root", str(tmp_path)])
    assert rc == skill_lock.EXIT_LOCK_VIOLATION
    assert _audit_rows(tmp_path)[-1]["action"] == "skill-lock-blocked"


# ---------------------------------------------------------------------------
# Refusal: non-permissive license
# ---------------------------------------------------------------------------

def test_non_permissive_license_is_refused(tmp_path: Path) -> None:
    sha = _install_skill(tmp_path, "busl-skill", "body\n")
    _write_lock(tmp_path, {"busl-skill": _entry("busl-skill", sha, "BUSL-1.1")})

    result = skills_lock.verify_skills_lock(tmp_path)
    assert result.ok is False
    assert result.skills[0].status == "license-refused"

    rc = cli_main(["signalos", "skill-lock", "verify", "--repo-root", str(tmp_path)])
    assert rc == skill_lock.EXIT_LOCK_VIOLATION
    assert _audit_rows(tmp_path)[-1]["action"] == "skill-lock-blocked"


def test_source_available_licenseref_is_refused(tmp_path: Path) -> None:
    sha = _install_skill(tmp_path, "sa-skill", "body\n")
    _write_lock(tmp_path, {"sa-skill": _entry("sa-skill", sha, "LicenseRef-SourceAvailable")})

    result = skills_lock.verify_skills_lock(tmp_path)
    assert result.ok is False
    assert result.skills[0].status == "license-refused"


# ---------------------------------------------------------------------------
# Refusal: missing/empty license — never auto-trust
# ---------------------------------------------------------------------------

def test_missing_license_is_refused_never_auto_trusted(tmp_path: Path) -> None:
    sha = _install_skill(tmp_path, "nolic-skill", "body\n")
    _write_lock(tmp_path, {"nolic-skill": _entry("nolic-skill", sha, "")})

    result = skills_lock.verify_skills_lock(tmp_path)
    assert result.ok is False
    assert result.skills[0].status == "license-refused"

    rc = cli_main(["signalos", "skill-lock", "verify", "--repo-root", str(tmp_path)])
    assert rc == skill_lock.EXIT_LOCK_VIOLATION


def test_missing_installed_skill_is_refused(tmp_path: Path) -> None:
    # Lock references a skill that was never installed on disk.
    _write_lock(tmp_path, {"ghost": _entry("ghost", "0" * 64, "MIT")})

    result = skills_lock.verify_skills_lock(tmp_path)
    assert result.ok is False
    assert result.skills[0].status == "missing"


# ---------------------------------------------------------------------------
# Aggregate: one bad skill blocks the whole lockfile
# ---------------------------------------------------------------------------

def test_one_refusal_blocks_overall(tmp_path: Path) -> None:
    good = _install_skill(tmp_path, "ok-skill", "ok\n")
    bad = _install_skill(tmp_path, "bad-skill", "bad\n")
    _write_lock(tmp_path, {
        "ok-skill": _entry("ok-skill", good, "MIT"),
        "bad-skill": _entry("bad-skill", bad, "BUSL-1.1"),
    })

    result = skills_lock.verify_skills_lock(tmp_path)
    assert result.ok is False
    assert len(result.refusals) == 1


# ---------------------------------------------------------------------------
# Directory-installed skill hashing
# ---------------------------------------------------------------------------

def test_directory_skill_hash_roundtrips(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".signalos" / "skills" / "dir-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# dir skill\n", encoding="utf-8")
    (skill_dir / "helper.py").write_text("print('hi')\n", encoding="utf-8")

    computed = skills_lock._sha256_dir(skill_dir)
    _write_lock(tmp_path, {
        "dir-skill": {
            "source": "https://github.com/example/dir-skill",
            "source_type": "github",
            "skill_path": ".signalos/skills/dir-skill",
            "sha256": computed,
            "license": "MIT",
            "license_source": "license-file",
        }
    })

    result = skills_lock.verify_skills_lock(tmp_path)
    assert result.ok is True
    assert result.skills[0].status == "ok"


# ---------------------------------------------------------------------------
# CLI reachability + list + no-evidence
# ---------------------------------------------------------------------------

def test_skill_lock_registered_in_top_level_parser() -> None:
    parser = _build_parser()
    choices: dict = {}
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices)
    assert "skill-lock" in choices


def test_top_level_cli_reaches_skill_lock_verify(tmp_path: Path) -> None:
    sha = _install_skill(tmp_path, "cli-skill", "cli body\n")
    _write_lock(tmp_path, {"cli-skill": _entry("cli-skill", sha, "mit")})

    rc = cli_main(["signalos", "skill-lock", "verify", "--repo-root", str(tmp_path), "--json"])
    assert rc == skill_lock.EXIT_OK


def test_skill_lock_list_runs(tmp_path: Path) -> None:
    sha = _install_skill(tmp_path, "list-skill", "x\n")
    _write_lock(tmp_path, {"list-skill": _entry("list-skill", sha, "MIT")})

    rc = skill_lock.main(["list", "--repo-root", str(tmp_path), "--json"])
    assert rc == skill_lock.EXIT_OK


def test_no_evidence_flag_skips_evidence_file(tmp_path: Path) -> None:
    sha = _install_skill(tmp_path, "ne-skill", "y\n")
    _write_lock(tmp_path, {"ne-skill": _entry("ne-skill", sha, "MIT")})

    rc = skill_lock.main(["verify", "--repo-root", str(tmp_path), "--no-evidence"])
    assert rc == skill_lock.EXIT_OK
    assert not (tmp_path / skill_lock.EVIDENCE_REL_PATH).is_file()


def test_malformed_lockfile_returns_bad_args(tmp_path: Path) -> None:
    rc = skill_lock.main(["verify", "--repo-root", str(tmp_path)])
    assert rc == skill_lock.EXIT_BAD_ARGS  # lockfile absent


# ---------------------------------------------------------------------------
# Integrity-witness composition
# ---------------------------------------------------------------------------

def test_witness_watches_lockfile_when_present(tmp_path: Path) -> None:
    from signalos_lib.commands import integrity_witness as iw

    _write_lock(tmp_path, {"w-skill": _entry("w-skill", "0" * 64, "MIT")})
    paths = {e.path for e in iw.current_entries(tmp_path)}
    assert ".signalos/skills-lock.json" in paths


def test_witness_does_not_crash_when_lockfile_absent(tmp_path: Path) -> None:
    from signalos_lib.commands import integrity_witness as iw

    # No lockfile written — should simply be absent from the watch set.
    paths = {e.path for e in iw.current_entries(tmp_path)}
    assert ".signalos/skills-lock.json" not in paths
    assert skills_lock.witness_watch_paths(tmp_path) == []


# ===========================================================================
# pin (acquisition) — NEGATIVE-first
# ===========================================================================

_MIT_TEXT = (
    "MIT License\n\n"
    "Copyright (c) 2026 Example\n\n"
    "Permission is hereby granted, free of charge, to any person obtaining a "
    "copy of this software and associated documentation files (the "
    '"Software"), to deal in the Software without restriction...\n'
)
_APACHE_TEXT = (
    "                                 Apache License\n"
    "                           Version 2.0, January 2004\n"
    "        http://www.apache.org/licenses/\n"
)
_BSD3_TEXT = (
    "Copyright (c) 2026 Example. All rights reserved.\n\n"
    "Redistribution and use in source and binary forms, with or without "
    "modification, are permitted provided that the following conditions are "
    "met:\n"
    "3. Neither the name of the copyright holder nor the names of its "
    "contributors may be used to endorse or promote products...\n"
)
_BSD2_TEXT = (
    "Copyright (c) 2026 Example. All rights reserved.\n\n"
    "Redistribution and use in source and binary forms, with or without "
    "modification, are permitted provided that the following conditions are "
    "met:\n"
    "1. Redistributions of source code must retain the above copyright "
    "notice.\n"
    "2. Redistributions in binary form must reproduce the above copyright "
    "notice.\n"
)
_ISC_TEXT = (
    "ISC License\n\n"
    "Copyright (c) 2026 Example\n\n"
    "Permission to use, copy, modify, and/or distribute this software for any "
    "purpose...\n"
)
_GPL_TEXT = (
    "                    GNU GENERAL PUBLIC LICENSE\n"
    "                       Version 3, 29 June 2007\n"
)
_BUSL_TEXT = "Business Source License 1.1\n\nParameters\n"
_MPL_TEXT = "Mozilla Public License Version 2.0\n"


def _make_skill_dir(root: Path, skill_id: str, *, body: str = "# skill\n",
                    license_text: str | None = None,
                    readme_text: str | None = None) -> Path:
    """Create a skill directory on disk with optional LICENSE/README."""
    d = root / "src" / skill_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_bytes(body.encode("utf-8"))
    if license_text is not None:
        (d / "LICENSE").write_text(license_text, encoding="utf-8")
    if readme_text is not None:
        (d / "README.md").write_text(readme_text, encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# SPDX text detector: each strong signature maps correctly
# ---------------------------------------------------------------------------

def test_detect_license_from_text_maps_each_signature() -> None:
    d = skills_lock.detect_license_from_text
    assert d(_MIT_TEXT) == "MIT"
    assert d(_APACHE_TEXT) == "Apache-2.0"
    assert d(_BSD3_TEXT) == "BSD-3-Clause"
    assert d(_BSD2_TEXT) == "BSD-2-Clause"
    assert d(_ISC_TEXT) == "ISC"
    # Non-permissive are still *identified* (for precise refusal reasons).
    assert d(_GPL_TEXT) == "GPL-3.0-only"
    assert d(_BUSL_TEXT) == "BUSL-1.1"
    assert d(_MPL_TEXT) == "MPL-2.0"
    # Unknown / empty -> "".
    assert d("some random notes with no license signature") == ""
    assert d("") == ""


def test_detect_license_from_readme_parses_license_section() -> None:
    r = skills_lock.detect_license_from_readme
    assert r("# Tool\n\n## License\n\nMIT\n") == "MIT"
    assert r("## License\nApache-2.0\n") == "Apache-2.0"
    # Markdown-wrapped declaration.
    assert r("## License\n\n[MIT](./LICENSE)\n") == "MIT"
    # No License section -> "".
    assert r("# Readme\n\nNo license here.\n") == ""
    # Non-permissive id is surfaced (so the caller can refuse precisely).
    assert r("## License\nGPL-3.0-only\n") == "GPL-3.0-only"


# ---------------------------------------------------------------------------
# pin REFUSALS (negative-first): no entry must be written
# ---------------------------------------------------------------------------

def test_pin_refused_when_no_resolvable_license(tmp_path: Path) -> None:
    _make_skill_dir(tmp_path, "nolic")  # no LICENSE, no README
    rc = skill_lock.main([
        "pin", "nolic", "--from", str(tmp_path / "src" / "nolic"),
        "--repo-root", str(tmp_path),
    ])
    assert rc == skill_lock.EXIT_LOCK_VIOLATION
    # No lockfile entry written.
    lock = tmp_path / skills_lock.LOCK_REL_PATH
    assert not lock.is_file()
    assert _audit_rows(tmp_path)[-1]["action"] == "skill-lock-pin-blocked"


def test_pin_refused_when_license_file_is_gpl(tmp_path: Path) -> None:
    _make_skill_dir(tmp_path, "gpl", license_text=_GPL_TEXT)
    rc = skill_lock.main([
        "pin", "gpl", "--from", str(tmp_path / "src" / "gpl"),
        "--repo-root", str(tmp_path),
    ])
    assert rc == skill_lock.EXIT_LOCK_VIOLATION
    assert not (tmp_path / skills_lock.LOCK_REL_PATH).is_file()
    assert _audit_rows(tmp_path)[-1]["action"] == "skill-lock-pin-blocked"


def test_pin_refused_when_declared_license_is_busl(tmp_path: Path) -> None:
    # Even with a permissive LICENSE file present, an explicit non-permissive
    # --license wins (highest precedence) and is REFUSED.
    _make_skill_dir(tmp_path, "busl", license_text=_MIT_TEXT)
    rc = skill_lock.main([
        "pin", "busl", "--from", str(tmp_path / "src" / "busl"),
        "--license", "BUSL-1.1", "--repo-root", str(tmp_path),
    ])
    assert rc == skill_lock.EXIT_LOCK_VIOLATION
    assert not (tmp_path / skills_lock.LOCK_REL_PATH).is_file()


def test_pin_refused_when_from_path_missing(tmp_path: Path) -> None:
    rc = skill_lock.main([
        "pin", "ghost", "--from", str(tmp_path / "does-not-exist"),
        "--license", "MIT", "--repo-root", str(tmp_path),
    ])
    assert rc == skill_lock.EXIT_LOCK_VIOLATION
    assert not (tmp_path / skills_lock.LOCK_REL_PATH).is_file()


# ---------------------------------------------------------------------------
# pin SUCCESS paths: entry written + subsequent verify passes
# ---------------------------------------------------------------------------

def _read_lock(root: Path) -> dict:
    return json.loads((root / skills_lock.LOCK_REL_PATH).read_text(encoding="utf-8"))


def test_pin_with_mit_license_file_writes_entry_and_verifies(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(tmp_path, "mitskill", license_text=_MIT_TEXT)
    rc = skill_lock.main([
        "pin", "mitskill", "--from", str(skill_dir),
        "--source", "https://github.com/example/mitskill",
        "--source-type", "github",
        "--skill-path", "src/mitskill",
        "--repo-root", str(tmp_path),
    ])
    assert rc == skill_lock.EXIT_OK

    data = _read_lock(tmp_path)
    entry = data["skills"]["mitskill"]
    assert entry["license"] == "MIT"
    assert entry["license_source"] == "license-file"
    assert entry["source_type"] == "github"
    assert entry["skill_path"] == "src/mitskill"
    assert len(entry["sha256"]) == 64

    assert _audit_rows(tmp_path)[-1]["action"] == "skill-lock-pinned"
    assert (tmp_path / skill_lock.PIN_EVIDENCE_REL_PATH).is_file()

    # Re-running verify against the installed skill must now PASS.
    rc2 = cli_main([
        "signalos", "skill-lock", "verify",
        "--repo-root", str(tmp_path), "--installed-root", str(tmp_path),
    ])
    assert rc2 == skill_lock.EXIT_OK


def test_pin_with_explicit_apache_license_ok(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(tmp_path, "apa")  # no LICENSE file
    rc = skill_lock.main([
        "pin", "apa", "--from", str(skill_dir),
        "--license", "Apache-2.0",
        "--skill-path", "src/apa",
        "--repo-root", str(tmp_path),
    ])
    assert rc == skill_lock.EXIT_OK
    entry = _read_lock(tmp_path)["skills"]["apa"]
    assert entry["license"] == "Apache-2.0"
    assert entry["license_source"] == "declared"


def test_pin_resolves_license_from_readme_section(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(
        tmp_path, "rdme",
        readme_text="# Readme\n\nDoes stuff.\n\n## License\n\nMIT\n",
    )
    rc = skill_lock.main([
        "pin", "rdme", "--from", str(skill_dir),
        "--skill-path", "src/rdme",
        "--repo-root", str(tmp_path), "--json",
    ])
    assert rc == skill_lock.EXIT_OK
    entry = _read_lock(tmp_path)["skills"]["rdme"]
    assert entry["license"] == "MIT"
    assert entry["license_source"] == "readme"


def test_pin_is_a_subcommand_via_top_level_cli(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(tmp_path, "tl", license_text=_MIT_TEXT)
    rc = cli_main([
        "signalos", "skill-lock", "pin", "tl",
        "--from", str(skill_dir), "--skill-path", "src/tl",
        "--repo-root", str(tmp_path),
    ])
    assert rc == skill_lock.EXIT_OK
    assert "tl" in _read_lock(tmp_path)["skills"]
