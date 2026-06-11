# signalos_lib/product/brownfield.py
# Brownfield governance — retroactively apply governance to an existing repo.
#
# The ExistingRepoAdapter (stacks.py) detects a pre-existing repo and writes
# governance metadata but stops there. This module goes the rest of the way:
# it audits the existing codebase against a baseline of governance expectations
# (tests, CI, license, secret hygiene, dependency locking, security policy,
# ignore rules, docs), produces a prioritised remediation plan, applies the
# governance scaffold, and records the act in the audit trail.
#
# The audit is deterministic and file-based (no LLM, no network) so it is fast
# and testable. It never modifies the user's source — it only adds .signalos
# governance artifacts and reports what should be remediated.

from __future__ import annotations

__all__ = [
    "GovernanceFinding",
    "audit_existing_repo",
    "apply_governance",
]

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SEVERITIES = ("high", "medium", "low")

# Hardcoded-secret heuristics: an assignment of a long opaque token to a
# secret-ish key. Conservative to avoid flagging ordinary config.
_SECRET_ASSIGN = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"
)

_TEST_HINTS = ("test", "tests", "spec", "__tests__")
_LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock",
              "poetry.lock", "Pipfile.lock", "go.sum", "requirements.txt")
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "target", ".venv",
              "venv", "__pycache__", ".signalos"}


@dataclass
class GovernanceFinding:
    area: str
    severity: str
    issue: str
    remediation: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _iter_source_files(root: Path, limit: int = 4000):
    """Yield source files, skipping vendored/build dirs. Bounded for safety."""
    count = 0
    for path in root.rglob("*"):
        if count >= limit:
            return
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        count += 1
        yield path


def _has_any(root: Path, names: tuple[str, ...]) -> bool:
    return any((root / n).exists() for n in names)


def _has_tests(root: Path) -> bool:
    for path in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        name = path.name.lower()
        if path.is_dir() and name in _TEST_HINTS:
            return True
        if path.is_file() and (".test." in name or ".spec." in name
                               or name.startswith("test_")):
            return True
    return False


def _scan_for_secrets(root: Path) -> list[str]:
    hits: list[str] = []
    for path in _iter_source_files(root):
        if path.suffix.lower() not in (".js", ".ts", ".tsx", ".jsx", ".py",
                                       ".rs", ".go", ".java", ".rb", ".env", ""):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _SECRET_ASSIGN.search(text):
            hits.append(str(path.relative_to(root)))
        if len(hits) >= 20:
            break
    return hits


def audit_existing_repo(repo_root) -> dict[str, Any]:
    """Audit an existing repo against governance expectations.

    Returns {"findings": [...], "summary": {...}} with deterministic findings,
    each carrying an area, severity, the issue, and concrete remediation.
    """
    root = Path(repo_root)
    findings: list[GovernanceFinding] = []

    if not _has_tests(root):
        findings.append(GovernanceFinding(
            "testing", "high",
            "No test files or test directory were found.",
            "Add a test suite; gate merges on it (the Validate gate expects evidence).",
        ))

    if not (root / ".github" / "workflows").is_dir() and not _has_any(
        root, (".gitlab-ci.yml", "azure-pipelines.yml", ".circleci")
    ):
        findings.append(GovernanceFinding(
            "ci", "high",
            "No continuous-integration workflow was found.",
            "Add CI that runs build, tests, and a secret scan on every push.",
        ))

    secret_hits = _scan_for_secrets(root)
    if secret_hits:
        findings.append(GovernanceFinding(
            "secrets", "high",
            f"Possible hardcoded secrets in {len(secret_hits)} file(s): "
            + ", ".join(secret_hits[:5]) + ("…" if len(secret_hits) > 5 else ""),
            "Move secrets to the vault / environment; never commit credentials.",
        ))

    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        findings.append(GovernanceFinding(
            "ignore-rules", "medium",
            "No .gitignore — build output and secrets risk being committed.",
            "Add a .gitignore covering env files, build dirs, and dependencies.",
        ))
    else:
        try:
            ig = gitignore.read_text(encoding="utf-8")
            if ".env" not in ig:
                findings.append(GovernanceFinding(
                    "ignore-rules", "medium",
                    ".gitignore does not ignore .env files.",
                    "Add .env and .env.* to .gitignore so secrets stay local.",
                ))
        except (OSError, UnicodeDecodeError):
            pass

    if not _has_any(root, ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")):
        findings.append(GovernanceFinding(
            "license", "medium",
            "No LICENSE file was found.",
            "Add a license so usage terms are explicit.",
        ))

    if not _has_any(root, ("SECURITY.md", ".github/SECURITY.md")):
        findings.append(GovernanceFinding(
            "security-policy", "low",
            "No SECURITY.md (vulnerability-reporting policy).",
            "Add a SECURITY.md describing how to report vulnerabilities.",
        ))

    if not _has_any(root, _LOCKFILES):
        findings.append(GovernanceFinding(
            "dependencies", "medium",
            "No dependency lockfile — builds are not reproducible.",
            "Commit a lockfile (package-lock.json, Cargo.lock, poetry.lock, …).",
        ))

    if not _has_any(root, ("README.md", "README", "README.rst", "README.txt")):
        findings.append(GovernanceFinding(
            "docs", "low",
            "No README — newcomers have no entry point.",
            "Add a README describing what the project is and how to run it.",
        ))

    rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: rank.get(f.severity, 1))
    by_sev = {s: sum(1 for f in findings if f.severity == s) for s in _SEVERITIES}

    return {
        "findings": [f.as_dict() for f in findings],
        "summary": {
            "total": len(findings),
            "high": by_sev["high"],
            "medium": by_sev["medium"],
            "low": by_sev["low"],
            "governed": len(findings) == 0,
        },
    }


def _baseline_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Governance Baseline",
        "",
        "Foundry applied governance to this existing project. Below is the",
        "remediation plan from the initial audit, ordered by priority.",
        "",
        f"- Findings: {audit['summary']['total']} "
        f"(high {audit['summary']['high']}, medium {audit['summary']['medium']}, "
        f"low {audit['summary']['low']})",
        "",
    ]
    if not audit["findings"]:
        lines.append("No governance gaps found — this project already meets the baseline.")
        return "\n".join(lines) + "\n"
    lines.append("## Remediation plan")
    lines.append("")
    for i, f in enumerate(audit["findings"], 1):
        lines.append(f"{i}. **[{f['severity']}] {f['area']}** — {f['issue']}")
        lines.append(f"   - Fix: {f['remediation']}")
    return "\n".join(lines) + "\n"


def _append_audit(root: Path, action: str, payload: dict[str, Any]) -> None:
    trail = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    try:
        trail.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": action,
            **payload,
        }
        with trail.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def apply_governance(repo_root, intent: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply governance to an existing repo: scaffold + baseline + audit record.

    Never touches the user's source. Writes:
      - .signalos/profile.json (via ExistingRepoAdapter.scaffold)
      - .signalos/GOVERNANCE_BASELINE.md (the remediation plan)
    and appends a 'brownfield.governance-applied' audit entry.
    """
    root = Path(repo_root)
    from .stacks import ExistingRepoAdapter

    adapter = ExistingRepoAdapter()
    scaffold = adapter.scaffold(root, intent or {})

    audit = audit_existing_repo(root)

    signalos_dir = root / ".signalos"
    signalos_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = signalos_dir / "GOVERNANCE_BASELINE.md"
    baseline_path.write_text(_baseline_markdown(audit), encoding="utf-8")

    created = list(scaffold.get("created", []))
    created.append(".signalos/GOVERNANCE_BASELINE.md")

    _append_audit(root, "brownfield.governance-applied", {
        "findings": audit["summary"]["total"],
        "high": audit["summary"]["high"],
    })

    return {
        "profile": scaffold,
        "audit": audit,
        "created": created,
        "preserved": scaffold.get("preserved", []),
        "baseline_path": str(baseline_path.relative_to(root)),
    }
