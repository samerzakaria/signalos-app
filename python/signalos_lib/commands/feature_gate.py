"""`signalos feature-gate` - app-native mid-wave scope drift check."""

from __future__ import annotations

__all__ = [
    "EXIT_BAD_ARGS",
    "EXIT_DEFER",
    "EXIT_IN_SCOPE",
    "EXIT_NEEDS_ANSWERS",
    "EXIT_WAVE_NOT_ACTIVE",
    "main",
    "run_feature_gate",
    "write_feature_gate_evidence",
]

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


EXIT_IN_SCOPE = 0
EXIT_DEFER = 0
EXIT_NEEDS_ANSWERS = 100
EXIT_BAD_ARGS = 1
EXIT_WAVE_NOT_ACTIVE = 2

_SCHEMA_VERSION = "signalos.feature_gate.v1"

_STOP_WORDS = {
    "with",
    "from",
    "into",
    "have",
    "this",
    "that",
    "they",
    "them",
    "want",
    "need",
    "make",
    "feature",
    "implement",
    "build",
    "add",
    "support",
    "should",
    "would",
    "could",
    "will",
    "when",
    "where",
    "what",
    "which",
    "while",
    "also",
}

_TOKEN_SPLIT_RE = re.compile(r"[\s,\.\!\?/\\\(\)\[\]]+")
_EXPECTATION_ROW_RE = re.compile(r"^\|\s*(?P<ordinal>\d+)\s*\|(?P<body>.*)\|$")


@dataclass(frozen=True)
class ScopeLookup:
    backlog_matches: int
    expectation_matches: int
    prd_matches: int

    @property
    def total_matches(self) -> int:
        return self.backlog_matches + self.expectation_matches + self.prd_matches


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_root(repo_root: str | Path | None) -> Path:
    return Path(repo_root).expanduser().resolve() if repo_root else Path.cwd().resolve()


def _tokenize(request: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_SPLIT_RE.split(request):
        word = raw.strip().lower()
        if len(word) <= 3 or word in _STOP_WORDS or word in seen:
            continue
        seen.add(word)
        tokens.append(word)
    return tokens


def _read_wave_pointer(root: Path) -> tuple[str, str] | None:
    path = root / ".signalos" / "wave.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    wave = str(data.get("wave") or "").strip()
    status = str(data.get("status") or "").strip()
    if not wave:
        return None
    return wave, status


def _wave_variants(wave: str) -> list[str]:
    raw = wave.strip()
    variants = [raw]
    number = raw[1:] if raw.upper().startswith("W") else raw
    if number:
        variants.append(number)
        try:
            variants.append(str(int(number)))
            variants.append(f"W{int(number):02d}")
            variants.append(f"{int(number):02d}")
        except ValueError:
            pass
    out: list[str] = []
    for item in variants:
        if item and item not in out:
            out.append(item)
    return out


def _candidate_backlog_paths(root: Path, wave: str) -> list[Path]:
    paths = [
        root / ".signalos" / "waves" / wave / "BACKLOG.yaml",
        root / ".signalos" / "BACKLOG.yaml",
    ]
    for variant in _wave_variants(wave):
        paths.append(root / ".signalos" / "backlog" / f"wave-{variant}.yaml")
    return _unique_paths(paths)


def _candidate_expectation_paths(root: Path, wave: str) -> list[Path]:
    paths = [root / ".signalos" / "waves" / wave / "EXPECTATION_MAP.md"]
    for variant in _wave_variants(wave):
        paths.append(root / ".signalos" / "waves" / variant / "EXPECTATION_MAP.md")
    paths.extend([
        root / ".signalos" / "EXPECTATION_MAP.md",
        root / "core" / "strategy" / "EXPECTATION_MAP.md",
    ])
    return _unique_paths(paths)


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _count_keywords_in_file(path: Path, keywords: list[str]) -> int:
    if not keywords or not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return 0
    return sum(1 for keyword in keywords if keyword in text)


def _count_expectation_matches(path: Path, keywords: list[str]) -> int:
    if not keywords or not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    matches = 0
    for line in text.splitlines():
        match = _EXPECTATION_ROW_RE.match(line.strip())
        if not match:
            continue
        body = match.group("body").strip()
        if body.lower().startswith("behavior"):
            continue
        lower = body.lower()
        if any(keyword in lower for keyword in keywords):
            matches += 1
    return matches


def _count_prd_build_matches(path: Path, keywords: list[str]) -> int:
    if not keywords or not path.is_file():
        return 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    count = 0
    for line in lines:
        lower = line.lower()
        if "build" not in lower:
            continue
        if any(keyword in lower for keyword in keywords):
            count += 1
    return count


def _run_scope_lookup(root: Path, wave: str, request: str) -> ScopeLookup:
    keywords = _tokenize(request)
    backlog_matches = sum(
        _count_keywords_in_file(path, keywords)
        for path in _candidate_backlog_paths(root, wave)
    )
    expectation_matches = sum(
        _count_expectation_matches(path, keywords)
        for path in _candidate_expectation_paths(root, wave)
    )
    prd_matches = _count_prd_build_matches(
        root / ".signalos" / "PRD_TRACEABILITY.md",
        keywords,
    )
    return ScopeLookup(
        backlog_matches=backlog_matches,
        expectation_matches=expectation_matches,
        prd_matches=prd_matches,
    )


def _parse_yes_no(value: str | None) -> tuple[bool, bool]:
    if value is None or not str(value).strip():
        return False, False
    normalized = str(value).strip().lower()
    if normalized in {"yes", "y", "true", "1"}:
        return True, True
    if normalized in {"no", "n", "false", "0"}:
        return True, False
    return False, False


def _base_payload(
    *,
    request: str,
    wave: str,
    lookup: ScopeLookup,
    verdict: str,
    q1: str | None = None,
    q2: str | None = None,
    reasoning: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "request": request,
        "wave": wave,
        "verdict": verdict,
        "q1": q1,
        "q2": q2,
        "backlog_matches": lookup.backlog_matches,
        "expectation_matches": lookup.expectation_matches,
        "prd_matches": lookup.prd_matches,
        "total_matches": lookup.total_matches,
    }
    if reasoning:
        payload["reasoning"] = reasoning
    return payload


def run_feature_gate(
    request: str,
    *,
    q1: str | None = None,
    q2: str | None = None,
    repo_root: str | Path | None = None,
) -> tuple[int, dict[str, object]]:
    root = _resolve_root(repo_root)
    request = str(request or "").strip()
    if not request:
        return EXIT_BAD_ARGS, {
            "schema_version": _SCHEMA_VERSION,
            "verdict": "BAD_ARGS",
            "error": "request must be non-empty",
            "repo_root": str(root),
        }
    if not root.exists():
        return EXIT_BAD_ARGS, {
            "schema_version": _SCHEMA_VERSION,
            "request": request,
            "verdict": "BAD_ARGS",
            "error": f"repo-root not found: {root}",
            "repo_root": str(root),
        }

    pointer = _read_wave_pointer(root)
    if pointer is None:
        return EXIT_WAVE_NOT_ACTIVE, {
            "schema_version": _SCHEMA_VERSION,
            "request": request,
            "verdict": "WAVE_NOT_ACTIVE",
            "error": ".signalos/wave.json is missing or invalid",
            "repo_root": str(root),
        }
    wave, status = pointer
    if status.upper() != "ACTIVE":
        return EXIT_WAVE_NOT_ACTIVE, {
            "schema_version": _SCHEMA_VERSION,
            "request": request,
            "wave": wave,
            "status": status,
            "verdict": "WAVE_NOT_ACTIVE",
            "error": f"wave {wave} status is {status or '<empty>'}, not ACTIVE",
            "repo_root": str(root),
        }

    lookup = _run_scope_lookup(root, wave, request)
    if lookup.total_matches > 0:
        return EXIT_IN_SCOPE, _base_payload(
            request=request,
            wave=wave,
            lookup=lookup,
            verdict="BUILD",
            q1=q1,
            q2=q2,
            reasoning=(
                "in-scope match "
                f"(backlog={lookup.backlog_matches}, "
                f"expectation={lookup.expectation_matches}, "
                f"prd={lookup.prd_matches})"
            ),
        )

    has_q1, q1_yes = _parse_yes_no(q1)
    has_q2, q2_yes = _parse_yes_no(q2)
    if not has_q1 or not has_q2:
        return EXIT_NEEDS_ANSWERS, _base_payload(
            request=request,
            wave=wave,
            lookup=lookup,
            verdict="NEEDS_ANSWERS",
        )

    if q1_yes:
        verdict = "BUILD"
        reasoning = "Q1=YES: required for active Belief to be testable."
    elif q2_yes:
        verdict = "BUILD"
        reasoning = "Q2=YES: safety/security cannot wait for next wave."
    else:
        verdict = "DEFER"
        reasoning = "Q1=NO and Q2=NO: not testability-required and not undeferrable."

    return (
        EXIT_IN_SCOPE if verdict == "BUILD" else EXIT_DEFER,
        _base_payload(
            request=request,
            wave=wave,
            lookup=lookup,
            verdict=verdict,
            q1=q1,
            q2=q2,
            reasoning=reasoning,
        ),
    )


def write_feature_gate_evidence(payload: dict[str, object], repo_root: Path) -> Path:
    evidence = {
        "schema_version": _SCHEMA_VERSION,
        "created_at": _now_iso(),
        **payload,
    }
    path = repo_root / ".signalos" / "product" / "FEATURE_GATE.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _write_human(payload: dict[str, object]) -> None:
    verdict = str(payload.get("verdict") or "")
    if verdict == "NEEDS_ANSWERS":
        sys.stdout.write(
            f"Feature Gate triggered - \"{payload.get('request')}\" is not in "
            "active wave scope.\n\n"
        )
        sys.stdout.write(f"Active wave: {payload.get('wave')}\n")
        sys.stdout.write("Three-source scope lookup:\n")
        sys.stdout.write(f"  - BACKLOG.yaml tickets:           {payload.get('backlog_matches', 0)} matched\n")
        sys.stdout.write(f"  - EXPECTATION_MAP.md rows:        {payload.get('expectation_matches', 0)} matched\n")
        sys.stdout.write(f"  - PRD_TRACEABILITY.md BUILD rows: {payload.get('prd_matches', 0)} matched\n")
        sys.stdout.write(f"  Total: {payload.get('total_matches', 0)}\n\n")
        sys.stdout.write("Q1: Required for the active wave's Belief to be testable? -> pending\n")
        sys.stdout.write("Q2: Safety/security/audit concern that cannot wait? -> pending\n\n")
        sys.stdout.write("Rerun with `--q1 yes|no --q2 yes|no` to receive a verdict.\n")
        return

    sys.stdout.write(f"Feature Gate verdict: {verdict}\n")
    if payload.get("request"):
        sys.stdout.write(f"Request: {payload.get('request')}\n")
    if payload.get("wave"):
        sys.stdout.write(f"Active wave: {payload.get('wave')}\n")
    if payload.get("reasoning"):
        sys.stdout.write(f"Reasoning: {payload.get('reasoning')}\n")
    if verdict == "DEFER":
        sys.stdout.write("\nTwo paths:\n")
        sys.stdout.write(
            "  - DEFER (default): write `// DEFER: W<NN+1>+` at the source "
            "location and add a PRD DEFER row.\n"
        )
        sys.stdout.write(
            "  - OVERRIDE: add an approved backlog ticket, then rerun "
            "feature-gate.\n"
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos feature-gate",
        description="Evaluate a request against active wave scope.",
    )
    parser.add_argument("request", help="User request to evaluate.")
    parser.add_argument("--q1", default=None, help="yes|no: required for active Belief testability?")
    parser.add_argument("--q2", default=None, help="yes|no: safety/security/audit cannot wait?")
    parser.add_argument("--repo-root", default=None, metavar="PATH")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--no-evidence", action="store_true")
    args = parser.parse_args(argv)

    code, payload = run_feature_gate(
        args.request,
        q1=args.q1,
        q2=args.q2,
        repo_root=args.repo_root,
    )
    root = _resolve_root(args.repo_root)
    if not args.no_evidence:
        try:
            write_feature_gate_evidence({**payload, "exit_code": code}, root)
        except OSError:
            pass

    if args.as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    elif code == EXIT_BAD_ARGS or code == EXIT_WAVE_NOT_ACTIVE:
        sys.stderr.write(f"feature-gate: {payload.get('error', 'failed')}\n")
    else:
        _write_human(payload)
    return code
