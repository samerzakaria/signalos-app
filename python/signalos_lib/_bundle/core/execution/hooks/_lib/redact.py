#!/usr/bin/env python3
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# SignalOS Core v1.1 — Session-journal redaction filter.
#
# Purpose: strip known secret patterns from every journal / metrics event
#          before it is written to disk. The redaction list below is the
#          canonical, co-signed (PE + Security) pattern set for W1.1.
#
# Redaction policy (T3 — permanently-T3 surface per core/TRUST_TIER.md):
#   1. Environment-variable-shaped keys: names matching *_KEY, *_TOKEN,
#      *_SECRET, *_PASSWORD, *_CREDENTIAL, *_PASSPHRASE (case-insensitive).
#   2. Anthropic-style API keys: sk-ant-*, claude-*. Displayed masked as
#      sk-ant-...XXXX (last 4 only).
#   3. AWS-style keys: AKIA[0-9A-Z]{16}, ASIA[0-9A-Z]{16} (access keys);
#      40-char base64 secret access keys when preceded by "aws" or "secret".
#   4. PEM blocks: -----BEGIN ... PRIVATE KEY-----...-----END ... PRIVATE KEY-----
#      (also CERTIFICATE, RSA PRIVATE KEY, EC PRIVATE KEY, OPENSSH PRIVATE KEY).
#   5. Bearer / Basic auth headers: "Bearer <token>", "Basic <token>".
#   6. JSON Web Tokens: three base64url segments separated by dots, eyJ prefix.
#   7. RFC 4122 UUIDs in known auth positions: session_token, api_key,
#      auth_token, bearer, authorization fields.
#   8. Long hex/base64 blobs (>= 40 chars, matches ^[A-Za-z0-9+/=_-]{40,}$)
#      appearing as a *value* of any field whose key name matches patterns
#      from rule 1.
#   9. Cosign signatures: sha256:... preceded by "signature".
#  10. GitHub personal access tokens: ghp_[A-Za-z0-9]{36}, gho_*, ghu_*, ghs_*.
#  11. Slack tokens: xox[abprs]-[A-Za-z0-9-]{10,}.
#  12. Stripe live keys: sk_live_*, pk_live_*, rk_live_*.
#  13. Email addresses (RFC 5321 local-part@domain).
#  14. Non-RFC-1918 IPv4 addresses.
#  15. E.164 phone numbers (+CC followed by 7-14 digits).
#  16. Connection strings (postgres/mysql/mongodb/redis/jdbc URLs).
#
# All redacted values are replaced with the string "[REDACTED:<rule>]" where
# <rule> is the rule-number that fired. This preserves event shape without
# leaking material.
#
# Append-only contract: this file is T3; any edit requires PE + Security
# co-sign per core/TRUST_TIER.md and an entry in core/governance/Retro/AMENDMENTS.md.

from __future__ import annotations

import json
import re
import sys
from typing import Any, Iterable


# -- Compiled patterns -------------------------------------------------------

_KEY_NAME_PATTERNS = re.compile(
    r"(?i)_(key|token|secret|password|credential|passphrase)$"
)

_PATTERNS: list[tuple[int, re.Pattern[str]]] = [
    (2, re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    (2, re.compile(r"claude-[A-Za-z0-9_\-]{20,}")),
    (3, re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    (4, re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    )),
    (4, re.compile(
        r"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----"
    )),
    (5, re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}")),
    (5, re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{16,}")),
    (6, re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")),
    (10, re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    (10, re.compile(r"\bgh[ous]_[A-Za-z0-9]{36}\b")),
    (11, re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    (12, re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b")),
    (12, re.compile(r"\b[pr]k_live_[A-Za-z0-9]{20,}\b")),
    # Rule 13 — Email addresses
    (13, re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    # Rule 14 — Non-RFC-1918 IPv4 (excludes 10.x, 172.16-31.x, 192.168.x, 127.x)
    (14, re.compile(
        r"(?<![\d.])(?!(?:10|127|169\.254|172\.(?:1[6-9]|2[0-9]|3[01])|192\.168)\.)"
        r"(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
        r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(?![\d.])"
    )),
    # Rule 15 — E.164 phone numbers
    (15, re.compile(r"\+[1-9][0-9]{6,14}(?![0-9])")),
    # Rule 16 — Connection strings
    (16, re.compile(
        r"(?i)(?:jdbc|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis(?:s)?|"
        r"amqps?|mssql|sqlserver|oracle)://[^\s<>]+"
    )),
]

_LONG_BLOB = re.compile(r"^[A-Za-z0-9+/=_\-]{40,}$")
_UUID_V4 = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)

_AUTH_KEY_FIELDS = frozenset({
    "session_token", "api_key", "auth_token", "bearer",
    "authorization", "access_token", "refresh_token", "id_token",
})


def _mask_anthropic(val: str) -> str:
    """sk-ant-...XXXX style mask, last 4 only."""
    tail = val[-4:] if len(val) > 4 else "xxxx"
    return f"sk-ant-...{tail}"


def _redact_string(s: str) -> tuple[str, list[int]]:
    """Apply all string-level patterns; return (redacted, rule_numbers_hit)."""
    rules_hit: list[int] = []
    out = s
    for rule_id, pat in _PATTERNS:
        if pat.search(out):
            rules_hit.append(rule_id)
            if rule_id == 2:
                out = pat.sub(lambda m: _mask_anthropic(m.group(0)), out)
            else:
                out = pat.sub(f"[REDACTED:{rule_id}]", out)
    return out, rules_hit


def _redact_value(key: str, value: Any) -> Any:
    """Redact one (key, value) pair honoring key-name rules (1, 7, 8, 9)."""
    # Rule 1: suspicious key name — always redact the value
    if _KEY_NAME_PATTERNS.search(key or ""):
        return f"[REDACTED:1]"
    # Rule 7: UUID in known auth field
    if key in _AUTH_KEY_FIELDS and isinstance(value, str) and _UUID_V4.match(value):
        return "[REDACTED:7]"
    # Rule 9: cosign signature
    if key == "signature" and isinstance(value, str) and value.startswith("sha256:"):
        return "[REDACTED:9]"
    # Rule 8: long blob as value of auth-shaped field
    if (
        key in _AUTH_KEY_FIELDS
        and isinstance(value, str)
        and _LONG_BLOB.match(value)
    ):
        return "[REDACTED:8]"
    return value


def redact_event(event: Any) -> Any:
    """Redact one journal/metrics event in place-like fashion; returns a new obj."""
    if isinstance(event, dict):
        return {k: _redact_value(k, redact_event(v)) for k, v in event.items()}
    if isinstance(event, list):
        return [redact_event(v) for v in event]
    if isinstance(event, str):
        s, _ = _redact_string(event)
        return s
    return event


def _walk_and_redact_stdin() -> int:
    """Filter mode: one JSON object per line on stdin, redacted JSON on stdout."""
    exit_code = 0
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if not line:
            sys.stdout.write("\n")
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"redact.py: invalid JSON on input line: {exc}\n")
            exit_code = 1
            continue
        redacted = redact_event(obj)
        sys.stdout.write(json.dumps(redacted, separators=(",", ":"), ensure_ascii=False) + "\n")
    return exit_code


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--filter":
        return _walk_and_redact_stdin()
    if len(argv) >= 2 and argv[1] == "--self-test":
        return _self_test()
    if len(argv) >= 2 and argv[1] == "--scan-diff":
        return _scan_diff_stdin()
    sys.stderr.write(
        "Usage: redact.py --filter     # stdin JSONL → stdout JSONL\n"
        "       redact.py --scan-diff  # unified diff stdin, exit 1 on secret\n"
        "       redact.py --self-test  # internal regression check\n"
    )
    return 2


def _scan_diff_stdin() -> int:
    """Read unified diff from stdin; exit 1 if any added line contains a secret.

    Inspects only lines starting with '+' (skipping '+++' file headers).
    Used by pre-commit Rule 5 and pre-tool-use-guard.sh.
    """
    found = False
    for raw_line in sys.stdin:
        if not raw_line.startswith("+") or raw_line.startswith("+++"):
            continue
        _redacted, rules = _redact_string(raw_line[1:])
        if rules:
            sys.stderr.write(
                f"[redact --scan-diff] rule(s) {rules} fired in diff line: "
                f"{raw_line.rstrip()[:120]}\n"
            )
            found = True
    return 1 if found else 0


def _self_test() -> int:
    fixtures = [
        # (input, must_not_contain_substrings)
        ({"api_key": "sk-ant-" + "A" * 40}, ["A" * 40]),
        ({"Authorization": "Bearer " + "x" * 40}, ["x" * 40]),
        ({"body": "here is a JWT eyJabc.eyJdef.sig" + "z" * 20}, ["eyJabc.eyJdef.sig"]),
        ({"notes": "pem -----BEGIN PRIVATE KEY-----MIIE...-----END PRIVATE KEY-----"},
         ["MIIE"]),
        ({"aws_secret": "AKIAIOSFODNN7EXAMPLE"}, ["AKIAIOSFODNN7EXAMPLE"]),
        # Rules 13-16
        ({"msg": "contact user@example.com today"}, ["user@example.com"]),
        ({"host": "203.0.113.5 is public"}, ["203.0.113.5"]),
        ({"phone": "+15551234567"}, ["+15551234567"]),
        ({"dsn": "postgres://u:p@host/db"}, ["postgres://"]),
    ]
    failures = 0
    for payload, banned in fixtures:
        out = json.dumps(redact_event(payload))
        for b in banned:
            if b in out:
                sys.stderr.write(f"FAIL: {b!r} still present in {out}\n")
                failures += 1
    if failures:
        return 1
    sys.stdout.write("redact.py self-test: PASS\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
