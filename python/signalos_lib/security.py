"""
cli/signalos_lib/security.py — SignalOS Security Sprint (AMD-CORE-031)
OWASP+STRIDE threat modelling, canary tokens, injection hardening.
No runtime third-party dependencies.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "ThreatEntry",
    "generate_owasp_stride",
    "plant_canary_token",
    "check_canary_token",
    "scan_injection_risks",
    "threat_list",
    "threat_export",
    "check_security_hook_wired",
    "THREAT_INDEX_RELATIVE",
]

THREAT_INDEX_RELATIVE = ".signalos/security/threats.jsonl"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ThreatEntry:
    id: str
    category: str          # "owasp" | "stride"
    title: str
    description: str
    severity: str          # "critical" | "high" | "medium" | "low"
    mitigation: str
    surface: str
    wave: str
    ts: str                # ISO-8601

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _next_threat_id(repo_root: Path) -> str:
    """Read threats.jsonl and return the next sequential threat-NNN id."""
    index_path = repo_root / THREAT_INDEX_RELATIVE
    if not index_path.exists():
        return "threat-001"
    highest = 0
    try:
        for raw in index_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            eid = entry.get("id", "")
            if eid.startswith("threat-"):
                try:
                    n = int(eid[len("threat-"):])
                    if n > highest:
                        highest = n
                except ValueError:
                    pass
    except OSError:
        pass
    return f"threat-{highest + 1:03d}"


def _append_threat(repo_root: Path, record: dict) -> None:
    """Create directory if needed and append a JSON line to threats.jsonl."""
    index_path = repo_root / THREAT_INDEX_RELATIVE
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------------------
# OWASP + STRIDE generation
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    t = time.gmtime()
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}T"
        f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"
    )


_STRIDE_DEFINITIONS = [
    (
        "Spoofing",
        "high",
        "An attacker impersonates a legitimate component or user on surface {surface}.",
        "Enforce strong authentication (MFA, mutual TLS) for all entry points on {surface}.",
    ),
    (
        "Tampering",
        "high",
        "An attacker modifies data in transit or at rest on surface {surface}.",
        "Apply integrity checks (HMAC, digital signatures) and enforce write-access controls on {surface}.",
    ),
    (
        "Repudiation",
        "medium",
        "Actions on surface {surface} cannot be traced back to an actor due to missing audit logs.",
        "Implement tamper-evident, append-only audit logging for all mutations on {surface}.",
    ),
    (
        "Information Disclosure",
        "high",
        "Sensitive data leaks from surface {surface} through error messages, logs, or side-channels.",
        "Sanitise error messages, encrypt data at rest/transit, and restrict access to logs on {surface}.",
    ),
    (
        "Denial of Service",
        "medium",
        "Surface {surface} can be made unavailable through resource exhaustion or crash conditions.",
        "Apply rate-limiting, circuit breakers, and resource quotas on {surface}.",
    ),
    (
        "Elevation of Privilege",
        "critical",
        "An attacker escalates privileges on surface {surface} to gain unauthorised capabilities.",
        "Enforce least-privilege, validate authorisation on every request, and audit role assignments on {surface}.",
    ),
]

_OWASP_DEFINITIONS = [
    (
        "Injection (A03)",
        "critical",
        "User-controlled input on surface {surface} is passed unsanitised to interpreters (SQL, shell, LDAP).",
        "Use parameterised queries, allowlists, and context-aware output encoding for all inputs on {surface}.",
    ),
    (
        "Broken Authentication (A07)",
        "high",
        "Authentication mechanisms on surface {surface} are weak or bypassable.",
        "Enforce MFA, rotate secrets, and audit session management on {surface}.",
    ),
    (
        "Security Misconfiguration (A05)",
        "medium",
        "Default or insecure configurations expose surface {surface} to unnecessary risk.",
        "Harden defaults, disable unused features, and continuously audit configuration on {surface}.",
    ),
]


def generate_owasp_stride(
    surface: str,
    wave: str,
    repo_root: Path,
) -> list[ThreatEntry]:
    """Generate 6 STRIDE + 3 OWASP ThreatEntry objects for *surface*.

    All entries are appended to the threat index.  Returns the list.
    """
    entries: list[ThreatEntry] = []
    ts = _iso_now()

    for title, severity, description, mitigation in _STRIDE_DEFINITIONS:
        tid = _next_threat_id(repo_root)
        entry = ThreatEntry(
            id=tid,
            category="stride",
            title=title,
            description=description.format(surface=surface),
            severity=severity,
            mitigation=mitigation.format(surface=surface),
            surface=surface,
            wave=wave,
            ts=ts,
        )
        _append_threat(repo_root, entry.as_dict())
        entries.append(entry)

    for title, severity, description, mitigation in _OWASP_DEFINITIONS:
        tid = _next_threat_id(repo_root)
        entry = ThreatEntry(
            id=tid,
            category="owasp",
            title=title,
            description=description.format(surface=surface),
            severity=severity,
            mitigation=mitigation.format(surface=surface),
            surface=surface,
            wave=wave,
            ts=ts,
        )
        _append_threat(repo_root, entry.as_dict())
        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Canary tokens
# ---------------------------------------------------------------------------

def plant_canary_token(
    repo_root: Path,
    label: str = "default",
) -> dict[str, str]:
    """Generate a UUID canary token and write it to disk.

    File: .signalos/security/canary-<label>.json
    Returns the written dict.
    """
    token = str(uuid.uuid4())
    ts = _iso_now()
    record: dict[str, str] = {
        "token": token,
        "label": label,
        "planted_at": ts,
        "wave": "10",
    }
    canary_dir = repo_root / ".signalos" / "security"
    canary_dir.mkdir(parents=True, exist_ok=True)
    canary_path = canary_dir / f"canary-{label}.json"
    canary_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def check_canary_token(
    repo_root: Path,
    label: str = "default",
) -> dict[str, Any]:
    """Check whether canary-<label>.json exists.

    Returns ``{"found": True, "token": "...", "label": "..."}`` if present,
    ``{"found": False, "token": None, "label": label}`` otherwise.
    """
    canary_path = repo_root / ".signalos" / "security" / f"canary-{label}.json"
    if not canary_path.exists():
        return {"found": False, "token": None, "label": label}
    try:
        data = json.loads(canary_path.read_text(encoding="utf-8"))
        return {"found": True, "token": data.get("token"), "label": data.get("label", label)}
    except (OSError, json.JSONDecodeError):
        return {"found": False, "token": None, "label": label}


# ---------------------------------------------------------------------------
# Injection risk scanner
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[tuple[str, str]] = [
    (
        r'execute\s*\(.*(%s|\.format\s*\(|f["\']|\+)',
        "SQL injection via string concat/format",
    ),
    (
        r'os\.system\s*\(',
        "Shell injection via os.system",
    ),
    (
        r'subprocess\.(call|run|Popen)\s*\(.*shell\s*=\s*True',
        "Shell injection via subprocess shell=True",
    ),
    (
        r'\beval\s*\(',
        "Code injection via eval",
    ),
    (
        r'render_template_string\s*\(',
        "Template injection via render_template_string",
    ),
]

_COMPILED_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(pattern), pattern, risk)
    for pattern, risk in _INJECTION_PATTERNS
]


def scan_injection_risks(
    repo_root: Path,
    target_path: str,
) -> list[dict[str, Any]]:
    """Scan *target_path* for injection risk patterns.

    *target_path* may be absolute or relative to *repo_root*.
    Returns a list of finding dicts; empty if file missing or clean.
    """
    path = Path(target_path)
    if not path.is_absolute():
        path = repo_root / target_path
    if not path.exists():
        return []

    findings: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    for lineno, line in enumerate(lines, start=1):
        for compiled, pattern_str, risk in _COMPILED_PATTERNS:
            if compiled.search(line):
                findings.append({
                    "file": target_path,
                    "line": lineno,
                    "pattern": pattern_str,
                    "risk": risk,
                })

    return findings


# ---------------------------------------------------------------------------
# Threat list + export
# ---------------------------------------------------------------------------

def threat_list(
    repo_root: Path,
    wave: Optional[str] = None,
    category: Optional[str] = None,
) -> list[ThreatEntry]:
    """Load all threats from index, optionally filtered by wave and/or category."""
    index_path = repo_root / THREAT_INDEX_RELATIVE
    if not index_path.exists():
        return []
    entries: list[ThreatEntry] = []
    try:
        for raw in index_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if wave is not None and d.get("wave") != wave:
                continue
            if category is not None and d.get("category") != category:
                continue
            try:
                entries.append(ThreatEntry(**d))
            except (TypeError, KeyError):
                continue
    except OSError:
        pass
    return entries


def threat_export(repo_root: Path, out_path: Path) -> int:
    """Write all threats to *out_path* as JSONL.  Returns count of entries."""
    entries = threat_list(repo_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry.as_dict(), separators=(",", ":")) + "\n")
    return len(entries)


# ---------------------------------------------------------------------------
# Wiring check (C16)
# ---------------------------------------------------------------------------

def check_security_hook_wired(repo_root: Path) -> tuple[bool, str]:
    """C16: if threats.jsonl exists, signal-cso.md must also exist.

    Returns (True, ok_message) or (False, fail_message).
    """
    index_path = repo_root / THREAT_INDEX_RELATIVE
    cmd_path = repo_root / "core" / "execution" / "commands" / "signal-cso.md"

    if not index_path.exists():
        return (True, "C16: threats.jsonl not present — wiring check skipped")

    if cmd_path.exists():
        return (True, f"C16: signal-cso wired — {cmd_path.name} present")

    return (
        False,
        f"C16: threats.jsonl exists but {cmd_path} is missing — wire signal-cso.md",
    )
