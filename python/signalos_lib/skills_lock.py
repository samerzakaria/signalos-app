"""License-checked skill lockfile — governed external-skill supply chain.

This is a CLEAN-ROOM reimplementation. The lockfile *idea/schema shape*
(a `skills-lock.json` that pins external skills) is borrowed at the level
of concept only; no third-party source code is copied. The differentiator
versus an ungoverned lockfile is that SignalOS additionally enforces a
*license policy* on every pinned skill: a skill whose content hash drifts
OR whose license is missing / unknown / non-permissive is REFUSED.

Convention (SignalOS enforces, never advises):
  * Verification fails-closed. The CLI command returns a non-zero exit on
    ANY hash mismatch or license refusal.
  * An absent/empty/unknown license is a refusal, not a pass. We never
    auto-trust an undeclared license.

The module is stdlib-only and composes with the existing SHA-256 + audit
+ evidence conventions (see registry.py / commands/integrity_witness.py).

Lockfile schema: ``signalos.skills_lock.v1``

  {
    "version": 1,
    "skills": {
      "<id>": {
        "source": "<repo url | local path | url>",
        "source_type": "github" | "local" | "url",
        "skill_path": "<path of the installed skill under the workspace>",
        "sha256": "<64 hex of the installed skill content>",
        "license": "<SPDX id, e.g. MIT>",
        "license_source": "license-file" | "readme" | "declared"
      }
    }
  }

Default lockfile location: ``.signalos/skills-lock.json`` in the adopter
workspace. Installed skills are resolved relative to ``installed_root``
(defaults to the workspace root) via each entry's ``skill_path``.
"""

from __future__ import annotations

__all__ = [
    "SCHEMA_VERSION",
    "LOCK_REL_PATH",
    "PERMISSIVE_LICENSES",
    "SkillLockError",
    "SkillStatus",
    "SkillLockResult",
    "LicenseResolution",
    "PinResult",
    "normalize_spdx",
    "is_permissive",
    "load_lockfile",
    "verify_skills_lock",
    "lockfile_path",
    "witness_watch_paths",
    "detect_license_from_text",
    "detect_license_from_readme",
    "resolve_license",
    "pin_skill",
]

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "signalos.skills_lock.v1"
LOCK_REL_PATH = Path(".signalos") / "skills-lock.json"

# SPDX permissive allowlist. Anything not in this set (including an empty
# or unrecognised license) is REFUSED — fail-closed, never auto-trust.
PERMISSIVE_LICENSES: frozenset[str] = frozenset({
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "0BSD",
    "Unlicense",
    "CC0-1.0",
})

# Per-skill verdicts.
STATUS_OK = "ok"
STATUS_HASH_MISMATCH = "hash-mismatch"
STATUS_LICENSE_REFUSED = "license-refused"
STATUS_MISSING = "missing"

# Small SPDX normalizer: maps common loose spellings onto canonical SPDX
# identifiers. Unknown inputs are returned trimmed (and will then fail the
# allowlist check — an unknown license is never silently trusted).
_SPDX_ALIASES: dict[str, str] = {
    "mit": "MIT",
    "mit license": "MIT",
    "apache": "Apache-2.0",
    "apache2": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "bsd": "BSD-3-Clause",
    "bsd-2": "BSD-2-Clause",
    "bsd2": "BSD-2-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3": "BSD-3-Clause",
    "bsd3": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "isc": "ISC",
    "0bsd": "0BSD",
    "unlicense": "Unlicense",
    "the unlicense": "Unlicense",
    "cc0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
}


class SkillLockError(RuntimeError):
    """Raised when the lockfile itself is malformed (not a per-skill refusal)."""


@dataclass(frozen=True)
class SkillStatus:
    """Per-skill verification verdict."""

    skill_id: str
    status: str  # ok | hash-mismatch | license-refused | missing
    source: str = ""
    source_type: str = ""
    skill_path: str = ""
    expected_sha256: str = ""
    actual_sha256: str | None = None
    license: str = ""
    license_normalized: str = ""
    license_source: str = ""
    license_permitted: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "status": self.status,
            "source": self.source,
            "source_type": self.source_type,
            "skill_path": self.skill_path,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "license": self.license,
            "license_normalized": self.license_normalized,
            "license_source": self.license_source,
            "license_permitted": self.license_permitted,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SkillLockResult:
    """Structured result of verifying the whole lockfile."""

    ok: bool
    schema_version: str
    lock_path: str
    skills: list[SkillStatus] = field(default_factory=list)

    @property
    def refusals(self) -> list[SkillStatus]:
        return [s for s in self.skills if not s.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "lock_path": self.lock_path,
            "skill_count": len(self.skills),
            "refusal_count": len(self.refusals),
            "skills": [s.to_dict() for s in self.skills],
        }


def normalize_spdx(license_id: str | None) -> str:
    """Normalize a loose license string to a canonical SPDX id.

    Returns "" for None/empty input. Unknown (non-aliased) inputs are
    returned trimmed of surrounding whitespace; the caller decides whether
    they are permitted (they are not, unless they match the allowlist).
    """
    if not license_id or not str(license_id).strip():
        return ""
    raw = str(license_id).strip()
    canonical = _SPDX_ALIASES.get(raw.lower())
    if canonical:
        return canonical
    # Already-canonical SPDX ids in the allowlist pass through untouched.
    for known in PERMISSIVE_LICENSES:
        if raw.lower() == known.lower():
            return known
    return raw


def is_permissive(license_id: str | None) -> bool:
    """True only if the (normalized) license is in the permissive allowlist."""
    normalized = normalize_spdx(license_id)
    return bool(normalized) and normalized in PERMISSIVE_LICENSES


def lockfile_path(repo_root: Path | str, lock_path: Path | str | None = None) -> Path:
    """Resolve the lockfile path (default ``.signalos/skills-lock.json``)."""
    if lock_path is not None:
        return Path(lock_path).expanduser()
    return Path(repo_root).expanduser() / LOCK_REL_PATH


def load_lockfile(path: Path | str) -> dict[str, Any]:
    """Read and shape-check a lockfile. Raises SkillLockError on malformed."""
    p = Path(path)
    if not p.is_file():
        raise SkillLockError(f"skill-lock: lockfile not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SkillLockError(f"skill-lock: lockfile unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillLockError("skill-lock: lockfile must be a JSON object")
    if data.get("version") != 1:
        raise SkillLockError(
            f"skill-lock: unsupported lockfile version {data.get('version')!r} "
            "(expected 1)"
        )
    skills = data.get("skills")
    if not isinstance(skills, dict):
        raise SkillLockError("skill-lock: lockfile 'skills' must be an object")
    return data


def _sha256_file(path: Path) -> str:
    """SHA-256 of a single file (1 MiB streaming, mirrors registry._sha256_file)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_dir(path: Path) -> str:
    """Deterministic SHA-256 over a directory tree.

    Hashes each contained file's POSIX-relative path and content in sorted
    order so the digest is stable across machines. Used when a skill is
    installed as a directory rather than a single file.
    """
    h = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file())
    for f in files:
        rel = f.relative_to(path).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()


def _sha256_path(path: Path) -> str | None:
    """SHA-256 of a file or directory; None if the path does not exist."""
    if path.is_file():
        return _sha256_file(path)
    if path.is_dir():
        return _sha256_dir(path)
    return None


def _resolve_skill_path(installed_root: Path, entry: dict[str, Any], skill_id: str) -> Path:
    """Resolve where the installed skill lives under the workspace.

    Uses the entry's ``skill_path`` when present; otherwise falls back to a
    conventional ``.signalos/skills/<id>`` layout.
    """
    skill_path = entry.get("skill_path")
    if isinstance(skill_path, str) and skill_path.strip():
        return (installed_root / skill_path).expanduser()
    return installed_root / ".signalos" / "skills" / skill_id


def verify_skills_lock(
    repo_root: Path | str,
    lock_path: Path | str | None = None,
    *,
    installed_root: Path | str | None = None,
) -> SkillLockResult:
    """Verify every locked skill against its pinned hash AND license policy.

    For each skill:
      * compute the SHA-256 of the installed skill content under the
        workspace and compare to the pinned ``sha256`` (mismatch => REFUSED);
      * require ``license`` to be present AND in the permissive allowlist
        (missing/unknown/non-permissive => REFUSED). An absent or empty
        license is a refusal, never a pass.

    The overall ``ok`` is True only when ALL skills pass BOTH checks.
    Raises SkillLockError only if the lockfile itself is malformed.
    """
    root = Path(repo_root).expanduser()
    inst_root = Path(installed_root).expanduser() if installed_root is not None else root
    path = lockfile_path(root, lock_path)
    data = load_lockfile(path)

    statuses: list[SkillStatus] = []
    for skill_id in sorted(data["skills"].keys()):
        entry = data["skills"][skill_id]
        if not isinstance(entry, dict):
            statuses.append(SkillStatus(
                skill_id=skill_id,
                status=STATUS_MISSING,
                reason="lock entry is not an object",
            ))
            continue

        source = str(entry.get("source", ""))
        source_type = str(entry.get("source_type", ""))
        skill_path_decl = str(entry.get("skill_path", ""))
        expected = str(entry.get("sha256", "")).strip().lower()
        license_decl = entry.get("license")
        license_source = str(entry.get("license_source", ""))
        license_norm = normalize_spdx(license_decl)
        permitted = is_permissive(license_decl)

        common = dict(
            skill_id=skill_id,
            source=source,
            source_type=source_type,
            skill_path=skill_path_decl,
            expected_sha256=expected,
            license=str(license_decl or ""),
            license_normalized=license_norm,
            license_source=license_source,
            license_permitted=permitted,
        )

        installed = _resolve_skill_path(inst_root, entry, skill_id)
        actual = _sha256_path(installed)

        # Order of refusal: missing content first (can't trust an absent
        # skill), then hash drift, then license policy. Every branch is
        # fail-closed.
        if actual is None:
            statuses.append(SkillStatus(
                **common,
                status=STATUS_MISSING,
                actual_sha256=None,
                reason=f"installed skill not found at {skill_path_decl or installed}",
            ))
            continue

        if not expected or actual != expected:
            statuses.append(SkillStatus(
                **common,
                status=STATUS_HASH_MISMATCH,
                actual_sha256=actual,
                reason=(
                    "pinned sha256 is empty" if not expected
                    else "content hash does not match pinned sha256"
                ),
            ))
            continue

        if not license_norm:
            statuses.append(SkillStatus(
                **common,
                status=STATUS_LICENSE_REFUSED,
                actual_sha256=actual,
                reason="license is missing/empty — undeclared licenses are never auto-trusted",
            ))
            continue

        if not permitted:
            statuses.append(SkillStatus(
                **common,
                status=STATUS_LICENSE_REFUSED,
                actual_sha256=actual,
                reason=(
                    f"license {license_norm!r} is not in the permissive allowlist "
                    f"({', '.join(sorted(PERMISSIVE_LICENSES))})"
                ),
            ))
            continue

        statuses.append(SkillStatus(
            **common,
            status=STATUS_OK,
            actual_sha256=actual,
            reason="ok",
        ))

    overall_ok = bool(statuses) and all(s.ok for s in statuses)
    # An empty lockfile (no skills) is not a pass — there is nothing to
    # govern, so refuse rather than report a vacuous success.
    if not statuses:
        overall_ok = False

    return SkillLockResult(
        ok=overall_ok,
        schema_version=SCHEMA_VERSION,
        lock_path=str(path),
        skills=statuses,
    )


def witness_watch_paths(repo_root: Path | str) -> list[str]:
    """Repo-relative governance paths the integrity-witness should watch.

    Returns the lockfile path (as a POSIX repo-relative string) when it is
    present, so tampering with the lockfile is drift-detected. Returns an
    empty list when the lockfile is absent — coupling stays optional and
    never crashes when no lockfile exists.
    """
    root = Path(repo_root).expanduser()
    path = root / LOCK_REL_PATH
    if path.is_file():
        return [LOCK_REL_PATH.as_posix()]
    return []


# ---------------------------------------------------------------------------
# License detection (acquisition / pin side)
# ---------------------------------------------------------------------------
#
# A skill cannot be pinned unless we can resolve a permissive SPDX license for
# it. License resolution precedence (most explicit first):
#   1. an explicitly supplied SPDX id (operator-declared),
#   2. a LICENSE/COPYING file's text (signature-matched), or
#   3. a README ``## License`` section's declared id.
# Anything else is unresolved -> the pin is REFUSED (fail-closed).

_LICENSE_FILE_NAMES = (
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "LICENCE",
    "LICENCE.md",
    "LICENCE.txt",
    "COPYING",
    "COPYING.md",
    "COPYING.txt",
)


@dataclass(frozen=True)
class LicenseResolution:
    """Outcome of resolving a skill's license at pin time."""

    spdx: str            # canonical SPDX id ("" when unresolved)
    source: str          # declared | license-file | readme | "" (none)
    permitted: bool      # spdx is non-empty AND in PERMISSIVE_LICENSES
    detail: str = ""     # human-readable note (e.g. which file matched)


def detect_license_from_text(text: str) -> str:
    """Conservatively map full license *text* onto a canonical SPDX id.

    Matches only strong, unambiguous signatures. Returns "" when no
    confident match is found — an undetectable license is never trusted.
    Non-permissive licenses are still *identified* (so the caller can give a
    precise refusal reason), but they are not in ``PERMISSIVE_LICENSES``.
    """
    if not text:
        return ""
    t = " ".join(text.split())  # collapse whitespace/newlines
    low = t.lower()

    # --- Non-permissive first (so e.g. an Apache-headed GPL preamble cannot
    #     be misclassified as Apache). These are identified but refused. ---
    if "gnu general public license" in low:
        if "version 3" in low or "v3" in low:
            return "GPL-3.0-only"
        if "version 2" in low or "v2" in low:
            return "GPL-2.0-only"
        return "GPL-3.0-only"
    if "gnu lesser general public license" in low or "lesser general public" in low:
        return "LGPL-3.0-only"
    if "gnu affero general public license" in low or "affero general public" in low:
        return "AGPL-3.0-only"
    if "business source license" in low:
        return "BUSL-1.1"
    if "mozilla public license" in low:
        return "MPL-2.0"

    # --- Permissive ---
    if "permission is hereby granted, free of charge" in low and "mit" in low:
        return "MIT"
    if "apache license" in low and ("version 2.0" in low or "version 2" in low):
        return "Apache-2.0"
    if "redistribution and use in source and binary forms" in low:
        # BSD family. 3-clause adds the no-endorsement clause; otherwise 2.
        if "neither the name" in low or "endorse or promote" in low:
            return "BSD-3-Clause"
        return "BSD-2-Clause"
    if "isc license" in low or re.search(r"\bisc\b", low):
        return "ISC"
    if "this is free and unencumbered software released into the public domain" in low:
        return "Unlicense"

    return ""


def detect_license_from_readme(text: str) -> str:
    """Parse a README ``## License`` section and return its declared SPDX id.

    Looks for a markdown ``## License`` (or ``# License``) heading and reads
    the first meaningful line/token beneath it, normalizing it via
    ``normalize_spdx``. Returns "" when no License section or no recognizable
    id is found. As a fallback, attempts full-text signature detection on the
    section body (so an inlined license blob is still caught).
    """
    if not text:
        return ""
    lines = text.splitlines()
    heading = re.compile(r"^\s{0,3}#{1,6}\s+licen[cs]e\b", re.IGNORECASE)
    start = None
    for i, line in enumerate(lines):
        if heading.match(line):
            start = i + 1
            break
    if start is None:
        return ""

    body: list[str] = []
    for line in lines[start:]:
        if re.match(r"^\s{0,3}#{1,6}\s+\S", line):  # next heading ends section
            break
        body.append(line)

    # First, try to read a declared id from the first non-empty line.
    for raw in body:
        stripped = raw.strip().lstrip("-*> ").strip()
        if not stripped:
            continue
        # Strip common markdown link/badge wrapping: [MIT](...) -> MIT.
        m = re.match(r"^\[([^\]]+)\]", stripped)
        candidate = m.group(1).strip() if m else stripped
        # Take the leading license-ish token (e.g. "MIT License" -> tries
        # "MIT License" then the bare first word).
        for probe in (candidate, candidate.split()[0] if candidate.split() else ""):
            norm = normalize_spdx(probe)
            if norm and norm in PERMISSIVE_LICENSES:
                return norm
        # If the declared token is a recognizable-but-non-permissive id,
        # surface it (so the caller refuses with a precise reason).
        norm_any = normalize_spdx(candidate)
        if norm_any and norm_any not in PERMISSIVE_LICENSES:
            return norm_any
        break  # only inspect the first meaningful line for a declared id

    # Fallback: the section may inline the full license text.
    return detect_license_from_text("\n".join(body))


def _find_license_file(skill_dir: Path) -> Path | None:
    """Locate a LICENSE/COPYING file in the skill dir, then its parent."""
    search_dirs = [skill_dir]
    parent = skill_dir.parent
    if parent != skill_dir:
        search_dirs.append(parent)
    for d in search_dirs:
        if not d.is_dir():
            continue
        for name in _LICENSE_FILE_NAMES:
            candidate = d / name
            if candidate.is_file():
                return candidate
    return None


def _find_readme(skill_dir: Path) -> Path | None:
    """Locate a README in the skill dir, then its parent."""
    names = ("README.md", "README.txt", "README", "readme.md")
    search_dirs = [skill_dir]
    parent = skill_dir.parent
    if parent != skill_dir:
        search_dirs.append(parent)
    for d in search_dirs:
        if not d.is_dir():
            continue
        for name in names:
            candidate = d / name
            if candidate.is_file():
                return candidate
    return None


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def resolve_license(
    skill_path: Path,
    *,
    declared: str | None = None,
) -> LicenseResolution:
    """Resolve a skill's license following the documented precedence.

    ``skill_path`` may be a file or a directory; for license/README discovery
    we look in the skill directory (the file's parent when a file is given)
    and its parent directory.

    Precedence:
      1. ``declared`` — an explicit SPDX id supplied by the operator;
      2. a LICENSE/COPYING file — SPDX detected from its full text;
      3. a README ``## License`` section — declared id parsed from it.

    Returns a :class:`LicenseResolution`. ``permitted`` is True only when the
    resolved SPDX id is non-empty AND in ``PERMISSIVE_LICENSES``.
    """
    # (1) Operator-declared explicit license wins.
    if declared and str(declared).strip():
        norm = normalize_spdx(declared)
        return LicenseResolution(
            spdx=norm,
            source="declared",
            permitted=bool(norm) and norm in PERMISSIVE_LICENSES,
            detail=f"declared via --license ({declared})",
        )

    skill_dir = skill_path if skill_path.is_dir() else skill_path.parent

    # (2) LICENSE / COPYING file text.
    lic_file = _find_license_file(skill_dir)
    if lic_file is not None:
        spdx = detect_license_from_text(_read_text_safe(lic_file))
        if spdx:
            return LicenseResolution(
                spdx=spdx,
                source="license-file",
                permitted=spdx in PERMISSIVE_LICENSES,
                detail=f"detected from {lic_file.name}",
            )

    # (3) README ## License section.
    readme = _find_readme(skill_dir)
    if readme is not None:
        spdx = detect_license_from_readme(_read_text_safe(readme))
        if spdx:
            return LicenseResolution(
                spdx=spdx,
                source="readme",
                permitted=spdx in PERMISSIVE_LICENSES,
                detail=f"declared in {readme.name} ## License section",
            )

    return LicenseResolution(spdx="", source="", permitted=False,
                             detail="no resolvable license")


@dataclass(frozen=True)
class PinResult:
    """Outcome of a ``pin`` attempt."""

    ok: bool
    skill_id: str
    reason: str
    license: LicenseResolution
    sha256: str = ""
    entry: dict[str, Any] = field(default_factory=dict)


def pin_skill(
    repo_root: Path | str,
    skill_id: str,
    from_path: Path | str,
    *,
    source: str = "",
    source_type: str = "local",
    skill_path: str = "",
    license_decl: str | None = None,
    lock_path: Path | str | None = None,
) -> PinResult:
    """Pin a skill into the lockfile — fail-closed on license policy.

    Resolves the skill content at ``from_path`` (a file or directory present
    on disk), computes its SHA-256, and resolves its license. If the resolved
    license is missing/unknown/non-permissive, REFUSES and writes NOTHING to
    the lockfile. On success, writes/updates the entry (creating the lockfile
    when absent) and returns ``ok=True``.

    This function performs no audit/evidence I/O — that is the CLI's job. It
    is pure enough to unit-test offline.
    """
    root = Path(repo_root).expanduser()
    src = Path(from_path).expanduser()

    skill_id = str(skill_id).strip()
    if not skill_id:
        return PinResult(
            ok=False, skill_id="", reason="skill id is empty",
            license=LicenseResolution("", "", False, "n/a"),
        )

    if not src.exists():
        return PinResult(
            ok=False, skill_id=skill_id,
            reason=f"--from path does not exist: {src}",
            license=LicenseResolution("", "", False, "n/a"),
        )

    sha = _sha256_path(src)
    if sha is None:
        return PinResult(
            ok=False, skill_id=skill_id,
            reason=f"--from path is neither a file nor a directory: {src}",
            license=LicenseResolution("", "", False, "n/a"),
        )

    resolution = resolve_license(src, declared=license_decl)

    # Fail-closed: refuse to pin an unlicensed / non-permissive skill.
    if not resolution.spdx:
        return PinResult(
            ok=False, skill_id=skill_id, sha256=sha,
            reason=("license could not be resolved — undeclared licenses are "
                    "never auto-trusted; pass --license <SPDX> or include a "
                    "LICENSE file / README ## License section"),
            license=resolution,
        )
    if not resolution.permitted:
        return PinResult(
            ok=False, skill_id=skill_id, sha256=sha,
            reason=(f"license {resolution.spdx!r} is not in the permissive "
                    f"allowlist ({', '.join(sorted(PERMISSIVE_LICENSES))})"),
            license=resolution,
        )

    # Determine the skill_path recorded in the lockfile. Prefer an explicit
    # workspace-relative path; otherwise fall back to the conventional layout.
    recorded_skill_path = skill_path.strip() if skill_path else ""
    if not recorded_skill_path:
        recorded_skill_path = (Path(".signalos") / "skills" / skill_id).as_posix()

    entry = {
        "source": source or str(src),
        "source_type": source_type or "local",
        "skill_path": recorded_skill_path,
        "sha256": sha,
        "license": resolution.spdx,
        "license_source": resolution.source,
    }

    # Write/update the lockfile (create when absent).
    path = lockfile_path(root, lock_path)
    if path.is_file():
        try:
            data = load_lockfile(path)
        except SkillLockError:
            # A malformed existing lockfile is a hard error — do not clobber.
            raise
    else:
        data = {"version": 1, "skills": {}}
    if not isinstance(data.get("skills"), dict):
        data["skills"] = {}
    data["version"] = 1
    data["skills"][skill_id] = entry

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return PinResult(
        ok=True, skill_id=skill_id, sha256=sha,
        reason="pinned", license=resolution, entry=entry,
    )
