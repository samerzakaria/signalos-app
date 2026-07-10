"""Gate orchestration (Phase 5 / T26-T44) - the gate-aware supervisor.

Per architecture decision Q4 the AgentLoop is gate-UNAWARE: it runs a budgeted
conversation and returns. This module is the supervisor that walks G0->G5:
run the gate agent, pause for review (gate event), and on the user's verdict
sign via sign.py (INV-3) and advance. Budgeted rework / reject (2); waive
advances without signing and marks the delivery not-"ready" (INV-1).
"""
from __future__ import annotations

import functools
import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .. import agent_loader, wave_engine, sign
from .agent_loop import AgentLoop
from .wiring_check import CODE_SUFFIXES, SCAFFOLD_NAMES, find_unwired_modules
from .budgets import resolve_gate_reopen_budget, resolve_gate_rework_budget
from .enforcement_state import EnforcementProvider

__all__ = ["GateOrchestrator", "GATE_SPECIALISTS", "GATE_QUESTIONS", "GATE_ROLES",
           "resume_delivery"]

GATE_ORDER = ["G0", "G1", "G2", "G3", "G4", "G5"]

GATE_SPECIALISTS = {
    "G0": "Product Strategist",
    "G1": "Domain Analyst",
    "G2": "Solution Architect",
    "G3": "UX Designer",
    "G4": "Full-Stack Engineer",
    "G5": "Release Manager",
}

GATE_QUESTIONS = {
    "G0": "Is this what you want built?",
    "G1": "Does this capture the domain correctly?",
    "G2": "Approve the build plan?",
    "G3": "Does this design look right?",
    "G4": "Is the build acceptable?",
    "G5": "Ready to ship?",
}

# Role authorised to sign each gate. Must be valid for EVERY artifact in the
# gate (sign_gate rejects a role not authorised for any present artifact).
# G3 mixes PO+PE artifacts -> co-signed per-artifact in _default_sign.
GATE_ROLES = {
    "G0": "PE", "G1": "PO", "G2": "PO", "G3": "PE", "G4": "PE", "G5": "QA",
}

_SIGN_VERDICT = {
    "approve": "APPROVED",
    "approve-with-conditions": "APPROVED-WITH-CONDITIONS",
}
_KNOWN_VERDICTS = {"approve", "approve-with-conditions", "request-changes", "reject", "waive"}


def _is_real_product_src(rel_path: str) -> bool:
    """A real product source/test file the G4 build must have produced — not
    governance bookkeeping (.signalos/core/) or lockfiles."""
    p = str(rel_path).replace("\\", "/").lstrip("/")
    if not p or p.startswith((".signalos/", ".git/", "node_modules/", "core/")):
        return False
    return p.startswith((
        "src/", "app/", "components/", "lib/", "pages/",
        "tests/", "test/", "server/", "api/",
    ))


@dataclass
class DeliveryState:
    run_id: str
    prompt: str
    current_gate: str = "G0"
    status: str = "active"
    # §3.2: the project namespace this delivery signs/generates artifacts in.
    # Persisted so resume_delivery restores the SAME namespace binding (an
    # older persisted state without the field resumes as "default").
    project_id: str = "default"
    rework: dict = field(default_factory=dict)
    rejections: dict = field(default_factory=dict)
    signed: list = field(default_factory=list)
    waived: list = field(default_factory=list)
    # Reviewer feedback per gate, in verdict order. Each entry is
    # {"verdict": "request-changes"|"reject"|"reopen", "cycle": int,
    # "feedback": str}. Persisted with the rest of the state so a resumed
    # delivery still knows what the reviewer actually asked for. Older
    # persisted states lack this field; resume_delivery tolerates its
    # absence (defaults to {}).
    feedback: dict = field(default_factory=dict)
    # Gate-reopen bookkeeping (#4). `reopens` counts reopen cycles per gate
    # (budgeted like rework/rejections). `invalidated` is the append-only
    # record of every signature/waiver removed by a reopen cascade:
    # {"gate", "by", "reason", "cascade": bool[, "waived": True]}.
    # Both are absent from older persisted states; resume_delivery defaults.
    reopens: dict = field(default_factory=dict)
    invalidated: list = field(default_factory=list)


# High-confidence unfilled-template markers that block a gate signature (0.6).
# The noisy single-brace pattern is deliberately excluded so legitimate prose or
# code containing ``{word}`` is not flagged -- only unambiguous leftovers block.
_BLOCKING_PLACEHOLDER_KINDS = frozenset({
    "double-brace", "date-token", "link-token",
    "feature-token", "fill-token", "todo-token",
})


def _artifact_placeholder_violations(path: Path) -> list[str]:
    """Return unambiguous unresolved-template markers in a gate artifact."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    from ..profiles.validation import find_unresolved_placeholders
    out: list[str] = []
    for finding in find_unresolved_placeholders(content):
        if finding.get("kind") in _BLOCKING_PLACEHOLDER_KINDS:
            out.append(f"line {finding.get('line')}: {finding.get('token')!r}")
    return out


def _default_sign(repo_root: Path, gate: str, signer: str, role: str,
                  verdict: str, conditions: str,
                  project_id: str = "default") -> list:
    """Production signing path - INV-3: sign.py is the ONLY signer.

    Writes the audit trail (T38) and co-signs gates whose artifacts require
    different roles (e.g. G3) by signing each artifact with an authorised role.
    *project_id* routes artifact paths through the shared §3.2 governance
    resolver so the signature lands where this delivery's inspect()/status
    reads (default: workspace root, byte-identical)."""
    from .. import artifacts
    audit_log = repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    wave = _current_wave_id(repo_root, project_id)
    expected = artifacts.resolve_gate_artifacts(repo_root, gate, project_id=project_id)
    present = [a for a in expected if a.path.is_file()]
    # Fail-closed (0.1): a gate that declares required artifacts cannot be
    # signed until at least one of them exists on disk. Previously an empty
    # `present` signed nothing, raised nothing, and the gate advanced anyway
    # -- a silent fail-open where a founder could approve a gate whose agent
    # produced no artifact. Gates with no declared artifacts are unaffected.
    if expected and not present:
        raise ValueError(
            f"gate {gate} cannot be signed: none of its {len(expected)} "
            "required artifact(s) exist yet ("
            + ", ".join(a.rel_path for a in expected) + ")"
        )
    # Content check (0.6): a present artifact that is still unfilled template
    # boilerplate is not signable -- a valid hash over placeholder text is not a
    # valid artifact. Reuses the existing placeholder scanner.
    for a in present:
        stale = _artifact_placeholder_violations(a.path)
        if stale:
            raise ValueError(
                f"gate {gate} artifact {a.rel_path} has unresolved template "
                f"placeholders: {'; '.join(stale[:5])}"
            )
    if present and all(role in a.required_roles for a in present):
        return sign.sign_gate(repo_root, gate, signer, role, verdict,
                              conditions, audit_log=audit_log, wave=wave,
                              project_id=project_id)
    signed = []
    for a in present:
        r = role if role in a.required_roles else a.required_roles[0]
        sign.sign_artifact(a.path, signer, r, gate, verdict, conditions)
        sign._append_audit(audit_log, signer, r, gate, a.rel_path, a.path, verdict, wave=wave)
        signed.append(a.rel_path)
    return signed


class _CriticChat:
    """Adapts a ProviderAdapter's chat(messages, model, ...) into the simple
    chat(messages) interface briefs.author_brief expects."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def chat(self, messages):
        return self._adapter.chat(messages=messages, model=getattr(self._adapter, "model", ""))


def _current_wave_id(repo_root: Path, project_id: str = "default") -> str | None:
    try:
        from ..status import get_wave_status

        wave = str(
            get_wave_status(repo_root, project_id=project_id).get("wave_id") or ""
        ).strip()
    except Exception:
        return None
    return None if not wave or wave == "\u2014" else wave


class GateOrchestrator:
    def __init__(
        self,
        repo_root: Path,
        adapter: Any,
        emit: Callable[[dict], None],
        *,
        enforcement_provider: Optional[EnforcementProvider] = None,
        signer: str = "foundry-agent",
        sign_fn: Optional[Callable[..., list]] = None,
        project_id: str = "default",
        max_rework: int | None = None,
        max_rejections: int = 2,
        max_reopens: int | None = None,
        run_id: Optional[str] = None,
        prompt: str = "",
        critic_adapter: Any = None,
        finalize_closeout: bool = True,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.adapter = adapter
        # 1.3 + 1.8: an optional second adapter used to author the plain-words
        # gate brief. When configured (and its vendor differs), the brief is
        # genuinely cross-vendor (1.4's independence guarantee). When absent,
        # falls back to the primary adapter -- still a real 4-field brief,
        # honestly recorded as same-vendor in its provenance.
        self.critic_adapter = critic_adapter
        # Convergence with run_delivery's CLOSEOUT phase (Claim 2): when the
        # walk COMPLETES, produce the SAME closeout evidence run_delivery writes
        # via the shared closeout.* functions, so a fix to closeout reaches BOTH
        # engines. Runs strictly AFTER G5 (post-G4) and only writes .signalos
        # governance evidence, so it cannot change the G4 build. Opt-out flag so
        # a driver (e.g. a build benchmark) that wants zero completion writes can
        # disable it; see _finalize_closeout.
        self.finalize_closeout = finalize_closeout
        self.emit = emit
        self.enforcement_provider = enforcement_provider
        self.signer = signer
        self.project_id = project_id
        # §3.2: bind the delivery's project namespace into the default sign
        # path so signatures land under the SAME governance dir inspect()
        # reads. Custom sign_fn callables (tests, IPC overrides) keep their
        # historical 6-arg signature untouched.
        if sign_fn is not None:
            self._sign = sign_fn
        else:
            self._sign = functools.partial(_default_sign, project_id=project_id)
        self.max_rework = resolve_gate_rework_budget(max_rework)
        self.max_rejections = max_rejections
        self.max_reopens = resolve_gate_reopen_budget(max_reopens)
        # run_id must be unique per delivery - it keys the persisted state dir
        # (.signalos/agent-runs/<run_id>/delivery.json). An explicit run_id is
        # honored verbatim (resume path); the fallback adds a timestamp + uuid
        # suffix so two deliveries with the same prompt prefix never collide.
        rid = run_id or (
            f"delivery-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        )
        self.state = DeliveryState(run_id=rid, prompt=prompt,
                                   project_id=project_id,
                                   current_gate=self._current_gate())
        # Seed the signed set from the repo's ACTUAL on-disk signatures so a
        # fresh or resumed orchestrator that reaches G4 with G0-G3 already signed
        # KNOWS they are signed. Without this the in-memory signed set is empty,
        # and the AgentLoop's plan-gating / gate-gating see zero signed gates and
        # DENY every product-source implementation write at G4 -- the build agent
        # writes real source, governance throws it away, and only the (allowed)
        # test files survive: the "tests without code" failure. The signed set is
        # derived from next_gate (the first unsigned gate) since the walk signs
        # gates strictly in order; a continuous walk still starts empty at G0 and
        # accumulates as normal.
        if not self.state.signed:
            self.state.signed = self._signed_gates_on_disk()
        # G4 build verification (INV-2 hard completion): the walk must never
        # sign a stub. Set after G4's AgentLoop runs; apply_verdict refuses to
        # sign G4 until this is ok. None = not yet verified.
        self._g4_verify: Optional[dict] = None

    def _current_gate(self) -> str:
        try:
            insp = wave_engine.inspect(self.repo_root, self.project_id)
            return insp.get("next_gate") or "G0"
        except Exception:
            return "G0"

    def _signed_gates_on_disk(self) -> list:
        """The gates already signed in the repo, as ordered gate labels
        (['G0','G1',...]). Derived from next_gate (the first unsigned gate) since
        the walk signs strictly in order; falls back to all-signed / empty."""
        try:
            insp = wave_engine.inspect(self.repo_root, self.project_id)
        except Exception:
            return []
        if insp.get("all_signed"):
            return list(GATE_ORDER)
        nxt = insp.get("next_gate")
        if nxt in GATE_ORDER:
            return list(GATE_ORDER[:GATE_ORDER.index(nxt)])
        return []

    def _next_after(self, gate: str) -> Optional[str]:
        idx = GATE_ORDER.index(gate)
        return GATE_ORDER[idx + 1] if idx + 1 < len(GATE_ORDER) else None

    def _role_for(self, gate: str) -> str:
        return GATE_ROLES.get(gate, "PO")

    def _g4_build_directive(self, base: str) -> str:
        """The BUILD gate's contract, stated stack- and layout-agnostically.

        The subagent-driven build (subagent_build.py) resolves the signed
        artifacts via the gate-artifact manifest and the stack's commands via
        the adapter registry itself, so this directive carries NO paths, NO
        stack commands, and NO inlined artifact text -- just the gate's
        non-negotiable outcome."""
        directive = (
            "You are at the Build gate. Implement the product for REAL: the "
            "signed plan's acceptance tests are the spec, and this gate cannot "
            "be signed until the product actually builds and its tests pass "
            "green on an independent run. Writing evidence without real, "
            "working implementation is a stub and will be refused."
        )
        return "\n".join([base, "", directive])

    def _gate_message(self, gate: str) -> str:
        base = self.state.prompt or "Proceed with the delivery."
        if gate == "G4":
            base = self._g4_build_directive(base)
        if gate != self.state.current_gate:
            return base
        cyc = self.state.rework.get(gate, 0)
        rej = self.state.rejections.get(gate, 0)
        reo = self.state.reopens.get(gate, 0)
        if not cyc and not rej and not reo:
            return base
        entries = [e for e in self.state.feedback.get(gate, [])
                   if str(e.get("feedback") or "").strip()]
        if not entries:
            # Legacy persisted state (pre-feedback field) or an empty feedback
            # string: keep the old generic nudge rather than fabricating text.
            return base + f"\n\n[Rework cycle {cyc or rej or reo} - address the prior feedback.]"
        latest = entries[-1]
        if latest.get("verdict") == "reject":
            header = (f"[Rejection {rej} - the reviewer rejected the previous "
                      "output; regenerate it, addressing this feedback:]")
        elif latest.get("verdict") == "reopen":
            header = (f"[Reopen {reo} - the reviewer reopened this previously "
                      "signed gate; rework it, addressing this reason:]")
        else:
            header = f"[Rework cycle {cyc} - the reviewer requested these changes:]"
        parts = [base, header + "\n" + str(latest.get("feedback"))]
        prior = entries[:-1]
        if prior:
            parts.append("[Feedback from earlier review cycles:]\n"
                         + "\n".join(f"- {e.get('feedback')}" for e in prior))
        return "\n\n".join(parts)

    def _record_review(self, gate: str, verdict: str, feedback: str, cycle: int,
                       *, store: bool = True) -> None:
        """Store reviewer feedback in the delivery state and append the same
        gate_review audit event the standalone verdict path writes (one audit
        format for both codepaths)."""
        if store:
            self.state.feedback.setdefault(gate, []).append(
                {"verdict": verdict, "cycle": cycle, "feedback": feedback})
        try:
            from .gate_review import record_review_event
            audit_verdict = {"request-changes": "REQUEST-CHANGES",
                             "reject": "REJECTED"}.get(verdict, verdict.upper())
            record_review_event(self.repo_root, gate, audit_verdict, feedback, cycle)
        except OSError:
            pass

    def _run_gate(self, gate: str) -> Any:
        self.state.current_gate = gate
        try:
            agent = agent_loader.load_agent(gate)
            system_prompt = agent.get("content") or (
                f"You are the {GATE_SPECIALISTS[gate]} for gate {gate}."
            )
        except KeyError as exc:
            self.emit({"type": "error", "error": f"Unknown gate {gate}: {exc}"})
            raise
        signed_ints = [
            int(str(g).lstrip("G"))
            for g in self.state.signed
            if str(g).lstrip("G").isdigit()
        ]
        executor = self._gate_executor(gate)
        result = executor(gate, system_prompt, signed_ints)
        if gate == "G3":
            self._emit_preview(gate)
        self.emit({
            "type": "gate",
            "gate": gate,
            "title": f"{GATE_SPECIALISTS[gate]} - {gate}",
            "question": GATE_QUESTIONS[gate],
            "specialist": GATE_SPECIALISTS[gate],
        })
        self._emit_brief(gate)
        self._emit_completeness(gate)
        self.state.status = "awaiting-verdict"
        self._persist()
        return result

    def _gate_executor(self, gate: str):
        """Gate execution strategy: which runner executes this gate's work.
        Default is a single governed AgentLoop; the BUILD gate uses the
        subagent-driven, test-first build. Registered here (not inline in
        _run_gate) so adding a gate-specific executor never edits the walk."""
        executors = {
            "G4": self._execute_build_gate,
        }
        return executors.get(gate, self._execute_default_gate)

    def _execute_default_gate(self, gate: str, system_prompt: str,
                              signed_ints: list) -> Any:
        loop = AgentLoop(
            adapter=self.adapter,
            repo_root=self.repo_root,
            enforcement_provider=self.enforcement_provider,
            emit=self.emit,
            execution_context="delivery",
            active_gate=gate,
            # §3.2 creation side: the loop rebases gate-artifact writes
            # (core/governance|strategy|execution/**) under this project's
            # governance base, so the artifact this gate generates is the
            # one _default_sign/inspect/status resolve at sign time.
            project_id=self.project_id,
            signed_gates=signed_ints,
        )
        return loop.run(system_prompt, self._gate_message(gate))

    def _execute_build_gate(self, gate: str, system_prompt: str,
                            signed_ints: list) -> Any:
        """The BUILD gate runs the SUBAGENT-DRIVEN, test-first build assembled
        from the bundled skills (implementer + spec-compliance reviewer +
        code-quality reviewer per plan task) rather than one free-form loop
        that batches tests and never implements. Reviews run on the
        independent critic_adapter when configured (cross-vendor, not
        double-biased self-review). The _verify_g4_build hard wall is
        unchanged: it still runs the REAL build+test on disk and refuses to
        sign a stub."""
        # Scaffold-first (FIX 2): before the build runs, materialize the
        # SELECTED stack's shell on a greenfield repo so G4 is not deadlocked
        # bootstrapping the root entry/config files governance denies. Strictly
        # idempotent -- a no-op when the shell already exists on disk.
        self._scaffold_shell_if_greenfield()
        from .subagent_build import run_subagent_driven_build
        result = run_subagent_driven_build(
            self.repo_root,
            self.adapter,
            reviewer_adapter=self.critic_adapter,
            enforcement_provider=self.enforcement_provider,
            emit=self.emit,
            project_id=self.project_id,
            signed_gates=signed_ints,
            prompt=self._gate_message(gate),
            governance_frame=system_prompt,
        )
        # INV-2: independently verify the build gate produced a real, passing
        # product before it can be signed. apply_verdict refuses to sign until ok.
        self._g4_verify = self._verify_g4_build(result)
        if not self._g4_verify.get("ok"):
            self.emit({"type": "system",
                       "text": f"G4 build not verified yet: {self._g4_verify.get('reason', '')}"})
        return result

    def _scaffold_shell_if_greenfield(self) -> None:
        """FIX 2: materialize the SELECTED stack's shell before the BUILD gate
        on a GREENFIELD repo, so G4 does not deadlock-by-policy trying to
        bootstrap the root entry/config files governance denies. Strictly
        IDEMPOTENT and SAFE:

          * Resolves the stack from the (profile.json-aware) ``detect_profile``,
            then scaffolds via that profile's adapter.
          * NO-OP when the shell already exists on disk (marker files present):
            it never re-scaffolds and never overwrites an already-scaffolded
            repo. The benchmark fixture ships package.json + a full React/Vitest
            shell, so this does NOTHING there.
          * NO-OP for profiles without a materializable framework shell
            (generic / existing-repo / agent-selected) or an unknown profile.
          * Never raises: a scaffold hiccup is reported and swallowed, so it can
            never fail the walk.
        """
        try:
            from .stacks import (
                adapter_has_greenfield_shell,
                detect_profile,
                get_adapter,
                stack_shell_present,
            )
        except Exception:
            return
        try:
            profile = detect_profile(self.repo_root)
            if not adapter_has_greenfield_shell(profile):
                self.emit({"type": "system",
                           "text": f"Scaffold-first: profile '{profile}' has no stack "
                                   "shell to materialize -- skipped."})
                return
            if stack_shell_present(self.repo_root):
                self.emit({"type": "system",
                           "text": f"Scaffold-first: the '{profile}' shell already exists "
                                   "on disk -- skipped (no files changed)."})
                return
            adapter = get_adapter(profile)
            scaffold = getattr(adapter, "scaffold", None)
            if not callable(scaffold):
                self.emit({"type": "system",
                           "text": f"Scaffold-first: adapter '{profile}' provides no "
                                   "scaffold -- skipped."})
                return
            intent = {"product_name": self.repo_root.name, "prompt": self.state.prompt}
            result = scaffold(self.repo_root, intent)
            created = result.get("created", []) if isinstance(result, dict) else []
            self.emit({"type": "progress",
                       "gate": "G4",
                       "text": f"Scaffolded the {profile} stack shell before build "
                               f"({len(created)} file(s)).",
                       "created": list(created)})
        except Exception as exc:  # never fail the walk on a scaffold hiccup
            self.emit({"type": "system",
                       "text": f"Scaffold-first skipped ({type(exc).__name__}: {exc})."})

    def _verify_g4_build(self, agent_result: Any) -> dict:
        """Independently verify G4 built a real, working product — the walk must
        never sign a stub (INV-2, no fake-green). Two checks, both required:

          1. The build actually WROTE real product source this run (not just
             prose Build Evidence over a scaffold stub), checked in the stack
             adapter's OWN source directory.
          2. The product BUILDS and its TESTS PASS on an independent run of the
             stack adapter's own validation plan.

        Returns {"ok": bool, "reason": str, ...}. Never raises — a failure to
        verify is reported as not-ok so signing is refused, not bypassed.
        """
        src_dir = self._product_source_dir()
        # 1. Real product source must EXIST in the repo (checked on disk -- the
        # LoopResult exposes no files list). A scaffold-only stub is refused.
        if not self._repo_has_real_product_src():
            return {"ok": False,
                    "reason": f"No real product source under {src_dir}/** -- implement the "
                              "product's modules/logic (not just tests or a stub), then rebuild."}
        # 1.5. WIRING gate: every product module must be reachable from the app
        # entry through the import graph. Components that exist but are never
        # composed ("pieces without wiring" -- the dominant observed failure)
        # are refused by machine, with the orphan modules named.
        orphans = self._unwired_modules()
        if orphans:
            return {"ok": False,
                    "reason": ("Build has UNWIRED modules -- written but never imported/"
                               "composed into the app. Wire each into the app's component "
                               "tree (import + render/use it), or remove it if truly "
                               "unneeded:\n" + "\n".join(f"- {o}" for o in orphans))}
        # 2. Independent build + test; surface the ACTUAL errors so rework
        # feedback is actionable (e.g. a missing module the compiler can name).
        try:
            from .stacks import detect_profile
            from .validation import build_validation_plan, run_validation
            profile = detect_profile(self.repo_root)
            plan = build_validation_plan(self.repo_root, profile)
            if not (plan.get("can_validate_build") and plan.get("can_validate_tests")):
                return {"ok": False,
                        "reason": f"profile '{profile}' has no build/test validation — cannot verify a real build."}
            result = run_validation(self.repo_root, plan)
            results = result.get("results", {})
            b, t = results.get("build", {}), results.get("test", {})
            if b.get("status") == "passed" and t.get("status") == "passed":
                return {"ok": True, "profile": profile}
            # Prefer the parsed per-file diagnostics (crisp + actionable); fall
            # back to raw command output.
            errs = []
            for v in (result.get("violations") or [])[:25]:
                f = v.get("file") or v.get("path") or ""
                ln = v.get("line")
                errs.append(f"{f}{f':{ln}' if ln else ''} {v.get('code','')} {v.get('message','')}".strip())
            detail = "\n".join(errs) if errs else (
                ((b.get("output") or "") + "\n" + (t.get("output") or ""))[-2000:])
            # The rebuild hint quotes the stack's OWN validation commands.
            cmds = " && ".join([*plan.get("build", []), *plan.get("test", [])]) \
                or "the project's build and test commands"
            return {"ok": False,
                    "reason": (f"G4 build is not green. Fix EVERY error below, then rebuild "
                               f"({cmds}). If a test imports a module that does not exist, "
                               f"the implementation file MUST be created under {src_dir}/** "
                               "-- never delete or weaken the test:\n" + detail),
                    "build": b.get("status"), "test": t.get("status")}
        except Exception as exc:  # never bypass on error -- fail closed
            return {"ok": False, "reason": f"G4 build verification error: {type(exc).__name__}: {exc}"}

    # (Import-graph shapes + entry names live in wiring_check.py -- the single
    # source for the wiring analysis, shared with the in-loop build reviewer so a
    # module written-but-never-composed is caught DURING the build, not only at
    # this gate.)

    def _unwired_modules(self) -> list:
        """Product-source modules UNREACHABLE from the app entry through the
        import graph -- the dominant 'green but not a product' failure (pieces
        written, never composed). Delegates to the shared wiring check, which the
        in-loop build reviewer also runs, so the same violation is caught DURING
        the build (with a fix pass) rather than only here at the gate."""
        return find_unwired_modules(self.repo_root, self._product_source_dir())

    def _product_source_dir(self) -> str:
        """The stack adapter's own source directory (e.g. src, app, internal/app
        -- never assume one layout). Falls back to 'src' if resolution fails."""
        try:
            from .stacks import detect_profile, get_adapter
            targets = get_adapter(detect_profile(self.repo_root)).resolve_targets(self.repo_root)
            return str(targets.get("source") or "src").strip() or "src"
        except Exception:
            return "src"

    # Single source lives in wiring_check.py (shared with the in-loop reviewer);
    # referenced here for _repo_has_real_product_src.
    _CODE_SUFFIXES = CODE_SUFFIXES
    _SCAFFOLD_NAMES = SCAFFOLD_NAMES

    def _repo_has_real_product_src(self) -> bool:
        """True iff the repo has real product source beyond the scaffold: a
        non-test code file in the stack's source directory that isn't a known
        scaffold entry file or the placeholder App stub."""
        src = self.repo_root / self._product_source_dir()
        if not src.is_dir():
            return False
        for p in src.rglob("*"):
            if not p.is_file() or p.suffix not in self._CODE_SUFFIXES:
                continue
            n = p.name
            if ".test." in n or ".spec." in n or "_test." in n or n.startswith("test_"):
                continue
            if n in self._SCAFFOLD_NAMES:
                continue
            if n == "App.tsx":
                try:
                    if len(p.read_text(encoding="utf-8", errors="replace").splitlines()) <= 8:
                        continue  # the scaffold's placeholder stub
                except OSError:
                    pass
            return True
        return False

    def _emit_brief(self, gate: str) -> None:
        """1.3 + 1.8: author the real 4-field plain-words brief for this gate's
        artifact via the model router's critique routing -- a cross-vendor critic
        when `critic_adapter` is configured (closing 1.3's routing policy and
        1.4's independence guarantee into the live gate walk), honestly falling
        back to the primary adapter (same-vendor) otherwise. Never blocks the
        gate walk; failures are silent, matching the other advisory emits."""
        try:
            from .. import artifacts as artifacts_mod
            from ..model_router import route
            from .briefs import author_brief, validate_brief

            resolved = [a for a in artifacts_mod.resolve_gate_artifacts(
                            self.repo_root, gate, project_id=self.project_id)
                        if a.path.is_file()]
            if not resolved:
                return
            artifact_content = resolved[0].path.read_text(encoding="utf-8", errors="replace")

            author_model = str(getattr(self.adapter, "model", "") or "")
            candidates = [author_model]
            critic_model = ""
            if self.critic_adapter is not None:
                critic_model = str(getattr(self.critic_adapter, "model", "") or "")
                if critic_model:
                    candidates.append(critic_model)

            chosen_model = route("critique", primary_model=author_model,
                                 available=candidates, author_model=author_model)
            use_critic = bool(critic_model) and chosen_model == critic_model
            reviewer_adapter = self.critic_adapter if use_critic else self.adapter
            reviewer_agent = "Critic" if use_critic else GATE_SPECIALISTS.get(gate, gate)
            reviewer_model = critic_model if use_critic else author_model

            brief = author_brief(
                artifact_content, _CriticChat(reviewer_adapter),
                author_agent=GATE_SPECIALISTS.get(gate, gate), author_model=author_model,
                reviewer_agent=reviewer_agent, reviewer_model=reviewer_model,
                artifact=resolved[0].rel_path,
            )
            payload = brief.to_dict()
            payload["type"] = "brief"
            payload["gate"] = gate
            # Honest self-report: when there's no real critic, independence is
            # not met and validate_brief says so here rather than hiding it.
            payload["contract_violations"] = validate_brief(brief)
            self.emit(payload)
        except Exception:
            pass

    def _emit_completeness(self, gate: str) -> None:
        """1.9: advisory inversion pass -- surface concerns a gate artifact may
        silently not address (identity, permissions, onboarding, data lifecycle,
        ops/failure). Advisory only; never blocks the gate."""
        try:
            from .. import artifacts
            from .completeness import completeness_findings
            for a in artifacts.resolve_gate_artifacts(
                    self.repo_root, gate, project_id=self.project_id):
                if not a.path.is_file():
                    continue
                findings = completeness_findings(
                    a.path.read_text(encoding="utf-8", errors="replace"))
                if findings:
                    self.emit({"type": "completeness", "gate": gate,
                               "artifact": a.rel_path, "findings": findings})
        except Exception:
            pass

    def _emit_incident(self, scenario: str, detail: str = "") -> None:
        """1.10: surface a failure as a plain-words incident card with recovery
        options, never just a bare error/stack trace."""
        try:
            from .incidents import build_incident_card
            self.emit(build_incident_card(scenario, detail=detail).to_dict())
        except Exception:
            pass

    def _emit_preview(self, gate: str) -> None:
        try:
            from .design_preview import generate_design_preview_html
            html = generate_design_preview_html({}, {"prompt": self.state.prompt})
        except Exception:
            html = "<!DOCTYPE html><html><body><main>Design preview</main></body></html>"
        self.emit({"type": "preview", "srcDoc": html, "caption": f"{gate} design preview"})
        # 0.7: run the (previously dormant) agentic UX-friction QA on the preview
        # surface and surface it to the founder at the design gate. Deterministic
        # heuristic pass only -- no network -- so it never blocks the gate.
        try:
            from .ux_friction import heuristic_findings
            findings = heuristic_findings(html)
            self.emit({"type": "ux_friction", "gate": gate,
                       "findings": findings, "count": len(findings)})
        except Exception:
            pass

    def start(self) -> dict:
        gate = self.state.current_gate
        self._run_gate(gate)
        return {"run_id": self.state.run_id, "gate": gate, "status": self.state.status}

    def apply_verdict(self, verdict: str, feedback: str = "") -> dict:
        gate = self.state.current_gate
        v = verdict if verdict in _KNOWN_VERDICTS else _classify(verdict)

        if v in ("approve", "approve-with-conditions"):
            # INV-2: G4 cannot be signed until its build is independently verified
            # (real product source written + build/tests pass). Never fake-green.
            if gate == "G4" and not (self._g4_verify or {}).get("ok"):
                reason = (self._g4_verify or {}).get("reason", "build not verified")
                self.emit({"type": "error", "error": f"Gate G4 cannot be signed: {reason}"})
                return {"status": "build-not-verified", "gate": gate, "reason": reason}
            try:
                self._sign(self.repo_root, gate, self.signer, self._role_for(gate),
                           _SIGN_VERDICT[v], feedback)
            except Exception as exc:
                self.emit({"type": "error", "error": f"Gate {gate} signing failed: {exc}"})
                return {"status": "sign-failed", "gate": gate, "error": str(exc)}
            self.state.signed.append(gate)
            self.emit({"type": "gate_signed", "gate": gate, "verdict": v})
            nxt = self._next_after(gate)
            if nxt is None:
                self.state.status = "complete"
                self._persist()
                ready = len(self.state.waived) == 0
                self.emit({"type": "delivery_complete", "run_id": self.state.run_id,
                           "ready": ready, "waived": list(self.state.waived)})
                self._finalize_closeout(ready=ready)
                return {"status": "complete", "ready": ready, "waived": list(self.state.waived)}
            self._run_gate(nxt)
            return {"status": "advanced", "gate": nxt}

        if v == "request-changes":
            cyc = self.state.rework.get(gate, 0) + 1
            if cyc > self.max_rework:
                # Audit the refused verdict too (mirrors handle_request_changes'
                # max_cycles_reached path) but don't store it as actionable
                # feedback -- the gate will not re-run.
                self._record_review(gate, v, feedback, cyc, store=False)
                self.emit({"type": "error",
                           "error": f"Max rework ({self.max_rework}) reached at {gate}."})
                self._emit_incident("gate-deadlock",
                                    detail=f"'{gate}' hit the rework limit.")
                self.state.status = "stopped"
                self._persist()
                return {"status": "max-rework", "gate": gate}
            self.state.rework[gate] = cyc
            self._record_review(gate, v, feedback, cyc)
            self._run_gate(gate)
            return {"status": "reworked", "gate": gate, "cycle": cyc}

        if v == "reject":
            cnt = self.state.rejections.get(gate, 0) + 1
            if cnt > self.max_rejections:
                self._record_review(gate, v, feedback, cnt, store=False)
                self.emit({"type": "error",
                           "error": f"Max rejections ({self.max_rejections}) reached at {gate}."})
                self._emit_incident("gate-deadlock",
                                    detail=f"'{gate}' was rejected too many times.")
                self.state.status = "stopped"
                self._persist()
                return {"status": "max-rejections", "gate": gate}
            self.state.rejections[gate] = cnt
            self._record_review(gate, v, feedback, cnt)
            self._run_gate(gate)
            return {"status": "rejected", "gate": gate, "count": cnt}

        if v == "waive":
            if gate not in self.state.waived:
                self.state.waived.append(gate)
            self._record_review(gate, v, feedback, 0, store=False)
            self.emit({"type": "system",
                       "text": f"{gate} waived (documented): {feedback or 'no reason given'}"})
            nxt = self._next_after(gate)
            if nxt is None:
                self.state.status = "complete"
                self._persist()
                self.emit({"type": "delivery_complete", "run_id": self.state.run_id,
                           "ready": False, "waived": list(self.state.waived)})
                self._finalize_closeout(ready=False)
                return {"status": "complete-waived", "ready": False,
                        "waived": list(self.state.waived)}
            self._run_gate(nxt)
            return {"status": "advanced-waived", "gate": nxt}

        self.emit({"type": "error", "error": f"Unknown verdict: {verdict!r}"})
        return {"status": "unknown-verdict"}

    # -- gate reopen (#4) ---------------------------------------------------

    # Delivery statuses in which a reopen is safe: the walk is parked waiting
    # on a human (awaiting-verdict), deadlocked (stopped), finished (complete)
    # or already reopened. "active" means a gate agent is mid-run - reopening
    # under it would race the run's own state writes, so it is refused.
    _REOPEN_SAFE_STATUSES = frozenset(
        {"awaiting-verdict", "stopped", "complete", "reopened"})

    def _reopen_roles_for(self, gate: str) -> set:
        """Roles authorised to reopen *gate* - the SAME authorisation set
        sign.sign_gate enforces for signing it: the union of required_roles
        across the gate's manifest artifacts (a reopen reverses a signature,
        so it demands the authority that could have produced one)."""
        try:
            from .. import artifacts
            required = {r for a in artifacts.expected_gate_artifacts(gate)
                        for r in a.required_roles}
        except Exception:
            required = set()
        return required or {self._role_for(gate)}

    def _append_reopen_audit(self, entry: dict) -> None:
        """Append one reopen/invalidate row to AUDIT_TRAIL.jsonl.

        Rows that reverse a signature carry an `action` containing a reverse
        marker audit_replay.py recognises ("reopen" / "unsign") plus the plain
        gate id in `gate`, so time-travel replay folds the gate back to
        unsigned. Refusal rows deliberately carry `gate_id` instead of `gate`
        so replay does NOT treat the refused attempt as a reversal.
        Silent on OSError, matching the other audit appenders."""
        audit_path = self.repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
        try:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "run_id": self.state.run_id,
                **entry,
            }
            with audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def reopen_gate(self, gate: str, reason: str, name: str = "",
                    role: str = "") -> dict:
        """Reopen an already-signed gate and invalidate everything after it.

        The reopened gate and every LATER signed gate lose their signature
        (recorded in state.invalidated + audited with replay-legible reverse
        markers); later waived gates are un-waived. current_gate returns to
        the reopened gate and the reason is threaded into the gate's feedback
        so the re-run's _gate_message carries it. Bounded per gate by the
        reopen budget (SIGNALOS_GATE_REOPEN_BUDGET, default 3). Does NOT
        re-run the gate agent - the caller decides when to resume."""
        gate = str(gate or "").strip().upper()
        if gate not in GATE_ORDER:
            self.emit({"type": "error", "error": f"Unknown gate {gate!r}"})
            return {"status": "unknown-gate", "gate": gate}
        if self.state.status not in self._REOPEN_SAFE_STATUSES:
            self.emit({"type": "error",
                       "error": f"Cannot reopen {gate}: delivery is "
                                f"'{self.state.status}' (a gate run is in flight)."})
            return {"status": "delivery-busy", "gate": gate,
                    "delivery_status": self.state.status}
        if gate not in self.state.signed:
            self.emit({"type": "error",
                       "error": f"Cannot reopen {gate}: it is not signed."})
            return {"status": "not-signed", "gate": gate}

        actor = str(name or "").strip() or self.signer
        role = str(role or "").strip() or self._role_for(gate)
        if role not in self._reopen_roles_for(gate):
            self.emit({"type": "error",
                       "error": f"Role {role!r} is not authorised to reopen "
                                f"{gate} (required: "
                                f"{sorted(self._reopen_roles_for(gate))})."})
            return {"status": "role-not-authorized", "gate": gate, "role": role}

        cnt = self.state.reopens.get(gate, 0) + 1
        if cnt > self.max_reopens:
            # Audited refusal (no `gate` key -> replay ignores it), no state change.
            self._append_reopen_audit({
                "action": "gate-reopen-refused", "kind": "reopen-refused",
                "gate_id": gate, "actor": actor, "role": role,
                "reason": reason, "attempt": cnt, "budget": self.max_reopens,
            })
            self.emit({"type": "error",
                       "error": f"Max reopens ({self.max_reopens}) reached at {gate}."})
            return {"status": "max-reopens", "gate": gate}

        # Cascade: the target gate plus every later signed gate lose their
        # signature; later waived gates lose their waiver. All audited.
        idx = GATE_ORDER.index(gate)
        invalidated: list[str] = []
        for g in GATE_ORDER[idx:]:
            cascade = g != gate
            if g in self.state.signed:
                self.state.signed = [s for s in self.state.signed if s != g]
                self.state.invalidated.append(
                    {"gate": g, "by": actor, "reason": reason, "cascade": cascade})
                self._append_reopen_audit({
                    # "reopen"/"unsign" are audit_replay reverse markers.
                    "action": "gate.reopen" if not cascade else "gate.unsign",
                    "kind": "reopen" if not cascade else "invalidate",
                    "gate": g, "actor": actor, "role": role, "reason": reason,
                    "cascade": cascade, "source_gate": gate,
                })
                if cascade:
                    invalidated.append(g)
            elif cascade and g in self.state.waived:
                self.state.waived = [w for w in self.state.waived if w != g]
                self.state.invalidated.append(
                    {"gate": g, "by": actor, "reason": reason,
                     "cascade": True, "waived": True})
                self._append_reopen_audit({
                    "action": "gate.unwaive", "kind": "unwaive",
                    "gate": g, "actor": actor, "role": role, "reason": reason,
                    "cascade": True, "source_gate": gate,
                })
                invalidated.append(g)

        self.state.reopens[gate] = cnt
        # Thread the reason through the same feedback mechanism rework uses,
        # so _gate_message includes it when the gate re-runs. Also writes the
        # gate_review audit event (verdict REOPEN).
        self._record_review(gate, "reopen", reason, cnt)
        self.state.current_gate = gate
        self.state.status = "reopened"
        self._persist()
        self.emit({"type": "gate_reopened", "gate": gate,
                   "invalidated": invalidated, "reason": reason,
                   "by": actor, "role": role, "reopen_count": cnt})
        self.emit({"type": "system",
                   "text": f"{gate} reopened by {actor} ({role}): "
                           f"{reason or 'no reason given'}."
                           + (f" Also invalidated: {', '.join(invalidated)}."
                              if invalidated else "")})
        return {"status": "reopened", "gate": gate, "invalidated": invalidated}

    def _finalize_closeout(self, *, ready: bool) -> None:
        """CONVERGENCE (Claim 2): produce the delivery CLOSEOUT via the SAME
        closeout.build_closeout / write_closeout the full run_delivery pipeline
        uses, so a fix to the closeout service reaches BOTH engines instead of
        only the CLI path. Previously a completed GateOrchestrator walk (the
        desktop `agent:deliver` surface) emitted `delivery_complete` but wrote
        NO CLOSEOUT.json, while run_delivery did -- the exact divergence the
        audit flags.

        Behaviour-preserving for the benchmark's G4 build:
          * Runs ONLY at final completion, strictly AFTER G4 is signed and
            independently verified, so it cannot change the G4 build behaviour,
            the scores, or the verified-build outcome for an already-scaffolded
            React repo.
          * Writes ONLY .signalos governance evidence (CLOSEOUT.json/.md);
            never touches product source or the G4 build outputs.
          * Deliberately does NOT invoke write_handoff_files here: its GTM stage
            is LLM-gated and would add a provider call to the walk. Handoff/GTM
            convergence is left for a design decision (see the workstream note).
          * Opt-out via finalize_closeout=False (a strict no-op), and never
            raises -- a closeout hiccup is reported through emit and swallowed,
            so it can never fail the walk or perturb a driver that does not want
            completion writes.
        """
        if not self.finalize_closeout:
            return
        try:
            from .closeout import build_closeout, write_closeout
            from .stacks import detect_profile
            profile = detect_profile(self.repo_root)
            closeout = build_closeout(self.repo_root, self.repo_root.name,
                                      profile, None)
            write_closeout(closeout, self.repo_root / ".signalos")
            self.emit({"type": "closeout",
                       "closure_level": closeout.get("closure_level"),
                       "ready": ready})
        except Exception as exc:  # never fail the walk on a closeout hiccup
            self.emit({"type": "system",
                       "text": f"Closeout skipped ({type(exc).__name__}: {exc})."})

    def _state_dir(self) -> Path:
        return self.repo_root / ".signalos" / "agent-runs" / self.state.run_id

    def _persist(self) -> None:
        try:
            d = self._state_dir()
            d.mkdir(parents=True, exist_ok=True)
            (d / "delivery.json").write_text(
                json.dumps(asdict(self.state), indent=2), encoding="utf-8")
        except OSError:
            pass


def resume_delivery(
    repo_root: Path,
    run_id: str,
    adapter: Any,
    emit: Callable[[dict], None],
    *,
    enforcement_provider: Optional[EnforcementProvider] = None,
    sign_fn: Optional[Callable[..., list]] = None,
    signer: str = "foundry-agent",
) -> "GateOrchestrator":
    """Reconstruct a GateOrchestrator from its persisted delivery.json (INV-5).

    Used after a sidecar crash/restart to resume from the last checkpoint.
    Raises FileNotFoundError if no state was persisted."""
    state_file = Path(repo_root) / ".signalos" / "agent-runs" / run_id / "delivery.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    orch = GateOrchestrator(
        repo_root, adapter, emit,
        enforcement_provider=enforcement_provider, sign_fn=sign_fn,
        signer=signer, run_id=run_id, prompt=data.get("prompt", ""),
        # §3.2: restore the persisted project binding so a resumed delivery
        # keeps signing/generating in the same namespace it started in.
        # Older persisted states predate the field -> "default".
        project_id=str(data.get("project_id") or "default"),
    )
    st = orch.state
    st.current_gate = data.get("current_gate", "G0")
    st.status = data.get("status", "active")
    st.rework = dict(data.get("rework", {}))
    st.rejections = dict(data.get("rejections", {}))
    st.signed = list(data.get("signed", []))
    st.waived = list(data.get("waived", []))
    # Older persisted states predate the feedback field -- tolerate absence.
    st.feedback = dict(data.get("feedback", {}))
    # Reopen bookkeeping (#4) - also absent from older persisted states.
    st.reopens = dict(data.get("reopens", {}))
    st.invalidated = list(data.get("invalidated", []))
    return orch


def _classify(text: str) -> str:
    from .gate_review import classify_review
    return classify_review(text).get("verdict", "request-changes")
