"""Validation helpers for profile CI and template contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .loader import Profile, ProfileTemplate, load_profile

_PROFILE_DIR = Path(__file__).resolve().parent
_DEFAULT_BUNDLE_ROOT = _PROFILE_DIR.parent / "_bundle"

_PLACEHOLDER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("double-brace", re.compile(r"(?<!\$)\{\{[^{}\n]{1,80}\}\}")),
    ("single-brace", re.compile(r"\{[A-Za-z][A-Za-z0-9 _-]{1,60}\}")),
    ("date-token", re.compile(r"\[DATE\]")),
    ("link-token", re.compile(r"\[link\]")),
    ("feature-token", re.compile(r"\[###-feature-name\]")),
    ("fill-token", re.compile(r"<to be filled[^>]*>", re.IGNORECASE)),
    ("todo-token", re.compile(r"\b(TODO|TBD|FIXME|XXX)\b")),
)

# A placeholder token that appears inside an inline-code span (`...`) or a fenced
# code block (``` ... ```) is a *reference to* the token, not a live unfilled
# slot. A governance artifact that states the marker rule -- e.g. a Constitution
# §Documentation-Standards line "artifacts must be free of `[DATE]` or
# `<to be filled>` markers" -- legitimately names the very tokens it forbids.
# Scanning the raw text flags that rule as its own violation and refuses to sign
# the artifact (the model authored a documentation standard, not a leftover
# slot). Real unfilled slots in every SignalOS template are BARE ("Created:
# [DATE]"), never backticked, so masking code spans keeps genuine leftovers
# caught while ending this self-referential false positive.
_CODE_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _mask_code_spans(line: str) -> str:
    """Blank inline-code spans so their contents are not scanned, preserving
    column positions with an equal-length space fill."""
    return _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)


@dataclass(frozen=True)
class ProfileValidationIssue:
    code: str
    severity: str
    message: str
    path: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "details": self.details,
        }


@dataclass(frozen=True)
class ProfileValidationReport:
    profile_id: str
    ok: bool
    issues: tuple[ProfileValidationIssue, ...]
    checked_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "checked_paths": list(self.checked_paths),
        }


def validate_profile_contract(
    profile: Profile | str,
    *,
    profile_dir: Path | None = None,
    bundle_root: Path | None = None,
) -> ProfileValidationReport:
    """Validate profile CI/template metadata without touching a product repo."""

    loaded = _load(profile, profile_dir=profile_dir)
    bundle = bundle_root or _DEFAULT_BUNDLE_ROOT
    issues: list[ProfileValidationIssue] = []
    checked: list[str] = []

    templates = list(loaded.required_templates) + list(loaded.ci.templates)
    issues.extend(_duplicate_destination_issues(templates))

    for template in templates:
        source_path = bundle / template.source
        checked.append(template.source)
        if not source_path.is_file():
            issues.append(
                ProfileValidationIssue(
                    code="template-source-missing",
                    severity="BLOCK_MERGE" if template.required else "WARN",
                    message=f"profile template source is missing: {template.source}",
                    path=template.source,
                    details={"destination": template.destination, "group": template.group},
                )
            )

    if loaded.ci.enabled:
        if not loaded.ci.files:
            issues.append(
                ProfileValidationIssue(
                    code="ci-files-missing",
                    severity="BLOCK_MERGE",
                    message="enabled CI profiles must declare emitted CI files",
                )
            )
        if not loaded.ci.templates:
            issues.append(
                ProfileValidationIssue(
                    code="ci-templates-missing",
                    severity="BLOCK_MERGE",
                    message="enabled CI profiles must declare CI templates",
                )
            )
        ci_template_destinations = {template.destination for template in loaded.ci.templates}
        for ci_file in loaded.ci.files:
            checked.append(ci_file)
            if ci_file not in ci_template_destinations:
                issues.append(
                    ProfileValidationIssue(
                        code="ci-file-not-backed-by-template",
                        severity="BLOCK_MERGE",
                        message=f"CI file is not backed by a profile CI template: {ci_file}",
                        path=ci_file,
                    )
                )
    else:
        if loaded.ci.files or loaded.ci.templates:
            issues.append(
                ProfileValidationIssue(
                    code="disabled-ci-has-outputs",
                    severity="BLOCK_MERGE",
                    message="disabled CI profiles must not declare CI files or templates",
                )
            )

    if loaded.preview.requires_install and loaded.command("install") is None:
        issues.append(
            ProfileValidationIssue(
                code="preview-install-command-missing",
                severity="BLOCK_MERGE",
                message="preview requires install but the profile install command is disabled",
            )
        )

    return _report(loaded.id, issues, checked)


def validate_generated_profile_files(
    repo_root: Path,
    profile: Profile | str,
    *,
    profile_dir: Path | None = None,
) -> ProfileValidationReport:
    """Validate generated files for a profile inside a product repo."""

    loaded = _load(profile, profile_dir=profile_dir)
    root = Path(repo_root)
    issues: list[ProfileValidationIssue] = []
    checked: list[str] = []

    generated_paths = [
        template.destination
        for template in loaded.required_templates
        if template.required
    ]
    if loaded.ci.enabled:
        generated_paths.extend(loaded.ci.files)

    for rel_path in sorted(dict.fromkeys(generated_paths)):
        checked.append(rel_path)
        path = root / rel_path
        if not path.is_file():
            issues.append(
                ProfileValidationIssue(
                    code="generated-file-missing",
                    severity="BLOCK_MERGE",
                    message=f"required generated file is missing: {rel_path}",
                    path=rel_path,
                )
            )
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            issues.append(
                ProfileValidationIssue(
                    code="generated-file-not-utf8",
                    severity="BLOCK_MERGE",
                    message=f"required generated file is not UTF-8 text: {rel_path}",
                    path=rel_path,
                    details={"error": str(exc)},
                )
            )
            continue
        issues.extend(_placeholder_issues(rel_path, content))

    return _report(loaded.id, issues, checked)


def dry_run_profile_validation(
    profile: Profile | str,
    *,
    repo_root: Path | None = None,
    profile_dir: Path | None = None,
    bundle_root: Path | None = None,
) -> ProfileValidationReport:
    """Run profile metadata checks and, when provided, generated-file checks."""

    loaded = _load(profile, profile_dir=profile_dir)
    reports = [
        validate_profile_contract(loaded, bundle_root=bundle_root),
    ]
    if repo_root is not None:
        reports.append(validate_generated_profile_files(repo_root, loaded))

    issues: list[ProfileValidationIssue] = []
    checked: list[str] = []
    for report in reports:
        issues.extend(report.issues)
        checked.extend(report.checked_paths)
    return _report(loaded.id, issues, checked)


def find_unresolved_placeholders(content: str) -> list[dict[str, Any]]:
    """Return obvious unresolved template tokens in generated text.

    Tokens inside inline-code spans or fenced code blocks are references, not
    unfilled slots, and are ignored -- see _CODE_FENCE_RE / _INLINE_CODE_RE and
    the rationale beside them. This prevents a governance artifact that documents
    the marker rule from tripping the scanner over its own rulebook.
    """

    findings: list[dict[str, Any]] = []
    in_fence = False
    for line_number, raw in enumerate(content.splitlines(), start=1):
        if _CODE_FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = _mask_code_spans(raw)
        for kind, pattern in _PLACEHOLDER_PATTERNS:
            for match in pattern.finditer(line):
                findings.append(
                    {
                        "kind": kind,
                        "line": line_number,
                        "token": match.group(0),
                    }
                )
    return findings


def _load(profile: Profile | str, *, profile_dir: Path | None = None) -> Profile:
    if isinstance(profile, Profile):
        return profile
    return load_profile(profile, profile_dir=profile_dir)


def _duplicate_destination_issues(
    templates: list[ProfileTemplate],
) -> list[ProfileValidationIssue]:
    seen: dict[str, ProfileTemplate] = {}
    issues: list[ProfileValidationIssue] = []
    for template in templates:
        first = seen.get(template.destination)
        if first is not None:
            issues.append(
                ProfileValidationIssue(
                    code="template-destination-duplicate",
                    severity="BLOCK_MERGE",
                    message=f"profile template destination is duplicated: {template.destination}",
                    path=template.destination,
                    details={
                        "first_source": first.source,
                        "duplicate_source": template.source,
                    },
                )
            )
            continue
        seen[template.destination] = template
    return issues


def _placeholder_issues(rel_path: str, content: str) -> list[ProfileValidationIssue]:
    findings = find_unresolved_placeholders(content)
    return [
        ProfileValidationIssue(
            code="generated-file-unresolved-placeholder",
            severity="BLOCK_MERGE",
            message=f"generated file contains unresolved placeholder token: {finding['token']}",
            path=rel_path,
            details=finding,
        )
        for finding in findings
    ]


def _report(
    profile_id: str,
    issues: list[ProfileValidationIssue],
    checked_paths: list[str],
) -> ProfileValidationReport:
    blocking = [issue for issue in issues if issue.severity != "WARN"]
    return ProfileValidationReport(
        profile_id=profile_id,
        ok=not blocking,
        issues=tuple(issues),
        checked_paths=tuple(sorted(dict.fromkeys(checked_paths))),
    )
