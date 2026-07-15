"""`signalos handoff` - package operator handoff evidence."""

from __future__ import annotations

__all__ = ["EXIT_BAD_ARGS", "EXIT_OK", "EXIT_WRITE_FAILED", "main", "prepare_handoff"]

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signalos_lib.git_process import GitProcessPolicyError, run_git
from signalos_lib.product.closeout import load_closeout


EXIT_OK = 0
EXIT_BAD_ARGS = 1
EXIT_WRITE_FAILED = 2
SCHEMA_VERSION = "signalos.handoff.v1"
AGENT_SELF_SIGNATURES = {"agent", "claude", "cursor", "copilot", "windsurf", "llm", "ai"}


@dataclass(frozen=True)
class HandoffContext:
    repo_root: Path
    generated_at: str
    actor: str
    live_url: str | None
    release_tag: str
    git_head: str
    profile: str
    product_name: str
    seeded_demo_data_note: str
    test_evidence_note: str
    known_limitations_note: str
    deploy_targets: list[str]
    audit_line_count: int
    latest_audit_action: str
    recent_audit_actions: list[str]
    test_audit_lines: list[str]
    soul_state: str
    constitution_state: str
    belief_state: str
    closeout: dict[str, Any] | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _resolve_root(repo_root: str | Path | None) -> Path:
    return Path(repo_root).expanduser().resolve() if repo_root else Path.cwd().resolve()


def _git(root: Path, args: list[str]) -> str | None:
    try:
        proc = run_git(
            args,
            cwd=root,
            runner=subprocess.run,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError, GitProcessPolicyError):
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _git_head(root: Path) -> str:
    return _git(root, ["rev-parse", "--short", "HEAD"]) or "unknown"


def _git_user(root: Path) -> str:
    return _git(root, ["config", "user.name"]) or "unknown"


def _release_tag(root: Path, release_tag: str | None, git_head: str) -> str:
    if release_tag and release_tag.strip():
        return release_tag.strip()
    latest = _git(root, ["describe", "--tags", "--abbrev=0"])
    if latest:
        return latest
    return "unreleased-local" if git_head == "unknown" else f"unreleased-{git_head}"


def _read_closeout(root: Path) -> dict[str, Any] | None:
    return load_closeout(root / ".signalos")


def _deploy_targets(root: Path) -> list[str]:
    deploy_dir = root / ".signalos" / "deploy"
    if not deploy_dir.is_dir():
        return []
    return sorted(path.name for path in deploy_dir.iterdir() if path.is_dir())


def _read_audit_summary(root: Path) -> tuple[int, str, list[str], list[str]]:
    path = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not path.is_file():
        return 0, "(missing)", [], []
    lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    actions: list[str] = []
    test_lines: list[str] = []
    for line in lines:
        action = _extract_action(line)
        if action:
            actions.append(action)
            if action.lower().startswith("test."):
                test_lines.append(line)
        elif '"event":"test.' in line.lower():
            test_lines.append(line)
    latest = actions[-1] if actions else "(unknown)"
    return len(lines), latest, actions[-8:], test_lines[-8:]


def _extract_action(line: str) -> str | None:
    match = re.search(r'"(?:action|event)"\s*:\s*"([^"]+)"', line)
    return match.group(1) if match else None


def _artifact_state(root: Path, candidates: list[str]) -> str:
    for rel in candidates:
        path = root / rel
        if path.is_file():
            return _describe_artifact(path)
    return "missing"


def _describe_artifact(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    signed_by = re.search(r"(?im)^\s*-?\s*signed_by\s*:\s*['\"]?([^\r\n'\"]+)", text)
    if signed_by:
        signer = signed_by.group(1).strip()
        if signer and not signer.startswith("<") and signer.lower() not in AGENT_SELF_SIGNATURES:
            return f"signed by {signer}"
    if re.search(r"(?im)^gate_[0-9]+_status\s*:\s*stub\s*$", text) or "STATUS: STUB" in text.upper():
        return "stub"
    if "## Signatures" in text:
        return "signed"
    return "present unsigned"


def _context(
    root: Path,
    *,
    live_url: str | None,
    release_tag: str | None,
    seeded_demo_data_note: str | None,
    test_evidence: str | None,
    known_limitations: str | None,
    actor: str | None,
) -> HandoffContext:
    closeout = _read_closeout(root)
    git_head = _git_head(root)
    audit_count, latest_action, recent_actions, test_audit = _read_audit_summary(root)
    product_name = str((closeout or {}).get("product_name") or root.name)
    profile = str((closeout or {}).get("profile") or "unknown")
    closeout_limitations = (closeout or {}).get("known_limitations") or []
    limitation_note = (
        known_limitations.strip()
        if known_limitations and known_limitations.strip()
        else (
            "\n".join(f"- {item}" for item in closeout_limitations)
            if closeout_limitations
            else "No known limitations were supplied. Operator must verify open release blockers before promotion."
        )
    )
    return HandoffContext(
        repo_root=root,
        generated_at=_utc_now(),
        actor=(actor.strip() if actor and actor.strip() else _git_user(root)),
        live_url=live_url.strip() if live_url and live_url.strip() else None,
        release_tag=_release_tag(root, release_tag, git_head),
        git_head=git_head,
        profile=profile,
        product_name=product_name,
        seeded_demo_data_note=(
            seeded_demo_data_note.strip()
            if seeded_demo_data_note and seeded_demo_data_note.strip()
            else "No seeded demo data was declared. Treat demo data as absent until an operator confirms or seeds it explicitly."
        ),
        test_evidence_note=(
            test_evidence.strip()
            if test_evidence and test_evidence.strip()
            else "No additional test evidence note was supplied. Use the audit-derived evidence below and attach release test output before production promotion."
        ),
        known_limitations_note=limitation_note,
        deploy_targets=_deploy_targets(root),
        audit_line_count=audit_count,
        latest_audit_action=latest_action,
        recent_audit_actions=recent_actions,
        test_audit_lines=test_audit,
        soul_state=_artifact_state(root, [".signalos/SOUL-DOCUMENT.md", "core/governance/Governance/SOUL-DOCUMENT.md"]),
        constitution_state=_artifact_state(root, [".signalos/CONSTITUTION.md", "core/governance/Governance/CONSTITUTION.md"]),
        belief_state=_artifact_state(root, [".signalos/BELIEF_MAP.md", "core/governance/Beliefs/BELIEF_MAP.md"]),
        closeout=closeout,
    )


def prepare_handoff(
    repo_root: str | Path | None = None,
    *,
    live_url: str | None = None,
    release_tag: str | None = None,
    seeded_demo_data_note: str | None = None,
    test_evidence: str | None = None,
    known_limitations: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    root = _resolve_root(repo_root)
    if not root.is_dir():
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "status": "bad_args",
            "error": f"repo-root not found: {root}",
            "repo_root": str(root),
        }
    context = _context(
        root,
        live_url=live_url,
        release_tag=release_tag,
        seeded_demo_data_note=seeded_demo_data_note,
        test_evidence=test_evidence,
        known_limitations=known_limitations,
        actor=actor,
    )
    out_dir = root / ".signalos" / "handoff"
    files = _write_handoff_package(context, out_dir)
    manifest_path = out_dir / "handoff-manifest.json"
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "status": "handoff-packaged",
        "repo_root": str(root),
        "output_dir": str(out_dir),
        "generated_at": context.generated_at,
        "actor": context.actor,
        "product_name": context.product_name,
        "profile": context.profile,
        "release_tag": context.release_tag,
        "git_head": context.git_head,
        "live_url": context.live_url,
        "deploy_targets": context.deploy_targets,
        "audit_rows": context.audit_line_count,
        "latest_audit_action": context.latest_audit_action,
        "package_files": [_display(path, root) for path in files],
        "manifest_path": _display(manifest_path, root),
    }
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _append_audit(root, payload)
    return payload


def _write_handoff_package(context: HandoffContext, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    renderers = {
        "HANDOFF.md": _render_handoff,
        "live-url.md": _render_live_url,
        "local-run.md": _render_local_run,
        "env-requirements.md": _render_env_requirements,
        "seeded-demo-data.md": _render_seeded_demo_data,
        "test-evidence.md": _render_test_evidence,
        "known-limitations.md": _render_known_limitations,
        "audit-gate-summary.md": _render_audit_gate_summary,
        "operator-runbook.md": _render_operator_runbook,
    }
    for filename, renderer in renderers.items():
        path = out_dir / filename
        path.write_text(renderer(context), encoding="utf-8")
        files.append(path)
    return files


def _render_handoff(context: HandoffContext) -> str:
    deploy_targets = ", ".join(context.deploy_targets) if context.deploy_targets else "(none found)"
    return "\n".join([
        "# SignalOS Operator Handoff",
        "",
        f"- Generated at: {context.generated_at}",
        f"- Actor: {context.actor}",
        f"- Product: {context.product_name}",
        f"- Profile: {context.profile}",
        f"- Release tag: {context.release_tag}",
        f"- Git HEAD: {context.git_head}",
        f"- Live URL: {context.live_url or '(not provided)'}",
        f"- Deploy packages: {deploy_targets}",
        "",
        "## Bundle Contents",
        "- live-url.md",
        "- local-run.md",
        "- env-requirements.md",
        "- seeded-demo-data.md",
        "- test-evidence.md",
        "- known-limitations.md",
        "- audit-gate-summary.md",
        "- operator-runbook.md",
        "- handoff-manifest.json",
        "",
    ])


def _render_live_url(context: HandoffContext) -> str:
    lines = ["# Live URL", ""]
    if context.live_url:
        lines.append(f"- URL: {context.live_url}")
        lines.append("- Verify TLS, redirect/callback origins, CORS, and health checks against this exact origin.")
    else:
        lines.append("No live URL was provided. Treat this handoff as local/staging-only until an operator supplies the production URL.")
    lines.append("")
    return "\n".join(lines)


def _render_local_run(context: HandoffContext) -> str:
    closeout = context.closeout or {}
    how_to_run = [str(step) for step in closeout.get("how_to_run") or []]
    lines = ["# Local Run Instructions", ""]
    if how_to_run:
        lines.append("```bash")
        lines.extend(how_to_run)
        lines.append("```")
    else:
        lines.append("No closeout run instructions were found. Operator must fill the product-specific start command before external handoff.")
    lines.extend([
        "",
        "## Local Dependencies",
        "- Start database/cache dependencies declared by the product profile or deploy package.",
        "- Apply migrations before exercising authenticated or data-backed flows.",
        "- Do not invent successful local-run evidence; attach actual command output before production promotion.",
        "",
    ])
    return "\n".join(lines)


def _render_env_requirements(context: HandoffContext) -> str:
    env_names = _detect_env_names(context.repo_root)
    lines = [
        "# Environment Requirements",
        "",
        "| Name | Required For | Secret |",
        "| --- | --- | --- |",
    ]
    if env_names:
        for name in env_names:
            secret = "yes" if re.search(r"(SECRET|TOKEN|KEY|PASSWORD|CONNECTION|DATABASE_URL|REDIS_URL)", name, re.I) else "review"
            lines.append(f"| {name} | Product runtime/configuration | {secret} |")
    else:
        lines.append("| PRODUCT_BASE_URL | Public app/API origin when deployed. | no |")
        lines.append("| DATABASE_URL / Connection string | Primary database when the selected product stack requires one. | yes |")
        lines.append("| CACHE_URL / Redis URL | Cache/background jobs when the selected product stack requires one. | yes |")
    lines.extend([
        "",
        "Deploy target env contracts, when prepared, live under `.signalos/deploy/<target>/env-contract.md`.",
        "",
    ])
    return "\n".join(lines)


def _detect_env_names(root: Path) -> list[str]:
    names: set[str] = set()
    for rel in (".env.example", ".env.sample", "env.example", ".signalos/deploy/env-contract.md"):
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"(?m)^\s*([A-Z][A-Z0-9_]{2,})\s*=", text):
            names.add(match.group(1))
        for match in re.finditer(r"\b([A-Z][A-Z0-9_]{2,})\b", text):
            if "_" in match.group(1):
                names.add(match.group(1))
    return sorted(names)


def _render_seeded_demo_data(context: HandoffContext) -> str:
    return "\n".join([
        "# Seeded Demo Data",
        "",
        context.seeded_demo_data_note,
        "",
        "Operator rule: demo credentials, demo tenants, and seeded business data must be explicitly labeled and removable.",
        "",
    ])


def _render_test_evidence(context: HandoffContext) -> str:
    lines = ["# Test Evidence", "", context.test_evidence_note, ""]
    closeout = context.closeout or {}
    tests = closeout.get("tests_executed") or []
    if tests:
        lines.extend(["## Closeout Check Results", "", "| Category | Status | Duration |", "| --- | --- | --- |"])
        for item in tests:
            lines.append(f"| {item.get('category', '')} | {item.get('status', '')} | {item.get('duration_s', 0.0)}s |")
        lines.append("")
    lines.append("## Audit-Derived Evidence")
    if context.test_audit_lines:
        for line in context.test_audit_lines:
            lines.append(f"- `{line[:240]}`")
    else:
        lines.append("No `test.*` audit rows were found in `.signalos/AUDIT_TRAIL.jsonl`.")
    lines.extend([
        "",
        "## Suggested Final Checks",
        "- Run the product stack's build/test commands from `local-run.md`.",
        "- Run release-readiness and release-proof checks when artifact handoff is claimed.",
        "- Run provider health checks and smoke tests against the live URL.",
        "",
    ])
    return "\n".join(lines)


def _render_known_limitations(context: HandoffContext) -> str:
    return "\n".join(["# Known Limitations", "", context.known_limitations_note, ""])


def _render_audit_gate_summary(context: HandoffContext) -> str:
    deploy_targets = ", ".join(context.deploy_targets) if context.deploy_targets else "(none)"
    lines = [
        "# Audit and Gate Summary",
        "",
        f"- Audit rows: {context.audit_line_count}",
        f"- Latest audit action: {context.latest_audit_action}",
        f"- Soul Document: {context.soul_state}",
        f"- Constitution: {context.constitution_state}",
        f"- Belief Map: {context.belief_state}",
        f"- Deploy packages found: {deploy_targets}",
        "",
        "## Recent Audit Actions",
    ]
    if context.recent_audit_actions:
        lines.extend(f"- {action}" for action in context.recent_audit_actions)
    else:
        lines.append("- (none)")
    lines.append("")
    return "\n".join(lines)


def _render_operator_runbook(context: HandoffContext) -> str:
    return "\n".join([
        "# Operator Runbook",
        "",
        "1. Review `HANDOFF.md`, `known-limitations.md`, and the release ticket.",
        "2. Review deploy packages under `.signalos/deploy/<target>/` if they exist.",
        "3. Provision env vars and secrets from `env-requirements.md` and the deploy target contract.",
        "4. Run the local instructions and confirm the app starts cleanly.",
        "5. Run migrations and approved seed steps from the deploy package.",
        "6. Run product tests and record results in the release ticket or audit trail.",
        "7. Trigger provider deployment outside `signalos handoff`; this command does not deploy.",
        "8. Verify `/health`, auth redirects, critical API routes, and the live URL.",
        "9. If checks fail, roll back to the previous release and preserve provider logs as audit evidence.",
        "",
        f"Release tag for this handoff: `{context.release_tag}`",
        "",
    ])


def _append_audit(root: Path, payload: dict[str, Any]) -> None:
    path = root / ".signalos" / "AUDIT_TRAIL.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": payload["generated_at"],
        "actor": payload["actor"],
        "role": "system",
        "action": "handoff-packaged",
        "release_tag": payload["release_tag"],
        "live_url": payload["live_url"],
        "output_dir": ".signalos/handoff",
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _display(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="signalos handoff",
        description="Package operator handoff evidence without deploying.",
    )
    parser.add_argument("--repo-root", default=None, metavar="PATH")
    parser.add_argument("--live-url", default=None)
    parser.add_argument("--release-tag", default=None)
    parser.add_argument("--seeded-demo-data-note", default=None)
    parser.add_argument("--test-evidence", default=None)
    parser.add_argument("--known-limitations", default=None)
    parser.add_argument("--actor", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        payload = prepare_handoff(
            args.repo_root,
            live_url=args.live_url,
            release_tag=args.release_tag,
            seeded_demo_data_note=args.seeded_demo_data_note,
            test_evidence=args.test_evidence,
            known_limitations=args.known_limitations,
            actor=args.actor,
        )
    except OSError as exc:
        sys.stderr.write(f"handoff: failed to write package: {exc}\n")
        return EXIT_WRITE_FAILED

    if args.as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    elif payload.get("ok"):
        sys.stdout.write("signalos handoff package prepared\n")
        sys.stdout.write(f"  repo-root   : {payload['repo_root']}\n")
        sys.stdout.write(f"  output      : {payload['output_dir']}\n")
        sys.stdout.write(f"  release tag : {payload['release_tag']}\n")
        sys.stdout.write(f"  live URL    : {payload.get('live_url') or '(not provided)'}\n")
    else:
        sys.stderr.write(f"handoff: {payload.get('error', 'failed')}\n")
    return EXIT_OK if payload.get("ok") else EXIT_BAD_ARGS
