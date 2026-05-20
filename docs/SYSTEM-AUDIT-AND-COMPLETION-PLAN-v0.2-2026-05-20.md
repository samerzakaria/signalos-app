# SignalOS — System Audit and Completion Plan (v0.2)

**Status:** Work-in-progress — verified from direct grep, file:line citations on every claim.
**Started:** 2026-05-20
**Method:** Direct enumeration + grep cross-reference. No agent summaries. Every assertion below is backed by a file:line that you can open and verify.
**Repo HEAD:** `538d596` (main, post sandbox + P0-fix merge)
**Supersedes:** `SYSTEM-AUDIT-AND-COMPLETION-PLAN-2026-05-20.md` (v0.1 — unverified)

This document is built **batch by batch**. Each batch is a verification pass over one subsystem, and each section below is added only after every line in it has been confirmed by direct read. If a section is not yet written, that batch has not yet been verified.

---

## Table of contents

1. [Method and trust model](#1-method-and-trust-model)
2. [Subsystem audit — verified facts](#2-subsystem-audit--verified-facts)
   - 2.1 [Python sidecar (Batch 1 — DONE)](#21-python-sidecar)
   - 2.2 [Rust IPC commands (Batch 2 — DONE)](#22-rust-ipc-commands)
   - 2.3 [Slash commands (Batch 3 — DONE)](#23-slash-commands)
   - 2.4 [Hook scripts (Batch 4 — DONE)](#24-hook-scripts)
   - 2.5 [SKILL.md routing (Batch 5 — DONE)](#25-skillmd-routing)
   - 2.6 [Frontend signals (Batch 6 — DONE)](#26-frontend-signals)
   - 2.7 [Legacy JS (Batch 7 — DONE)](#27-legacy-js)
   - 2.8 [Governance docs (Batch 8 — DONE)](#28-governance-docs)
   - 2.9 [Tool-adapter emitters (Batch 9 — DONE)](#29-tool-adapter-emitters)
3. [Truly orphaned items — consolidated](#3-truly-orphaned-items--consolidated)
4. [Completion plan (per no-delete principle)](#4-completion-plan-per-no-delete-principle)
5. [Verification status](#5-verification-status)
6. [Independent verification pass (2026-05-20, evening)](#6-independent-verification-pass-2026-05-20-evening)
   - 6.1 [Counts spot-checked](#61-counts-spot-checked)
   - 6.2 [Orphan claims spot-checked](#62-orphan-claims-spot-checked)
   - 6.3 [Vision-progress estimate](#63-vision-progress-estimate-new--not-in-original-v02)
   - 6.4 [Highest-leverage two items (re-prioritization)](#64-highest-leverage-two-items-re-prioritization-from-4)
   - 6.5 [Credibility risk worth naming](#65-credibility-risk-worth-naming)
   - 6.6 [Principle: Enforcement universality (AMD-CORE-110)](#66-principle-enforcement-universality-proposed-amd-core-110)
     - 6.6.1 [Enforcement ≠ always block — override-with-audit](#661-enforcement--always-block--the-override-with-audit-extension)
   - 6.7 [Implication: G3 produces doc AND UI prototype](#67-implication-g3-design-produces-doc-and-ui-prototype)
   - 6.8 [Refined violator inventory (post-feedback)](#68-refined-violator-inventory-post-feedback-2026-05-20)
7. [Definition of done + first PR](#7-definition-of-done--first-pr)

---

## 1. Method and trust model

**Why v0.1 was wrong.** v0.1 took 3 parallel exploration agents' summaries at face value. The agents read excerpts and pattern-matched — they reported `pause.py` as dead because they didn't find a *direct* import, missing that `cli.py:472` lazy-imports `signalos_lib.commands.pause` on the `pause` subcommand. That mistake compounded: an "unused module" claim becomes a deletion candidate in the completion plan, which would have removed *working wiring* from the codebase.

**What this version does instead.** For each module/file/command/hook claim, I enumerate the file tree directly (Glob), then grep with the exact symbol name across the *entire* repo (not just `python/` or `src-tauri/`). Each "this is wired" or "this is orphaned" verdict cites at least one file:line.

**What I will not do.**
- Trust agent summaries.
- Recommend deletion of any code or doc. Per AMD-CORE-100 (no-delete principle): unused = unfinished commitment, not garbage. The completion plan tells you what wiring is missing, never what to remove.
- Mark something "wired and working" when only the wiring is verified — wiring at byte level ≠ working at runtime. When this document says "wired", it means the call chain exists in the source; runtime verification (a real LLM run reaching the code path) is tracked separately.

---

## 2. Subsystem audit — verified facts

### 2.1 Python sidecar

**Inventory (verified by Glob 2026-05-20):**

| Location | Count |
|---|---|
| `python/signalos_lib/*.py` (top-level) | 40 |
| `python/signalos_lib/commands/*.py` (CLI wrappers) | 32 |
| `python/test_*.py` (tests, root) | 11 |

#### 2.1.1 Framework / dispatch files

| File | Role | Verification |
|---|---|---|
| `python/signalos_lib/__init__.py` | Package init | n/a |
| `python/signalos_lib/cli.py` | Subcommand dispatcher; called as `signalos <cmd>` via `[project.scripts]` shim `main_cli` ([cli.py:943-951](python/signalos_lib/cli.py#L943-L951)) | n/a |
| `python/signalos_lib/_worktree_state.py` | Standalone helper invoked as `python3 _worktree_state.py …` from bundle shell scripts | Referenced at [_bundle/core/execution/build/worktree-manager.sh:30](python/signalos_lib/_bundle/core/execution/build/worktree-manager.sh#L30) |
| `python/signalos_lib/commands/__init__.py` | Subpackage init | n/a |

#### 2.1.2 Modules with commands/<X>.py wrapper AND cli.py dispatch

All 27 verified by reading [cli.py lines 450-940](python/signalos_lib/cli.py#L450-L940). Each row shows the cli.py line that lazy-imports the wrapper.

| Top-level module | CLI subcommand(s) | cli.py dispatch line |
|---|---|---|
| `brain.py` | `brain`, `signal-learn` | [cli.py:756-772](python/signalos_lib/cli.py#L756-L772) |
| `campaign.py` | `campaign` | [cli.py:636-656](python/signalos_lib/cli.py#L636-L656) |
| `catalog.py` | `search`, `info` | [cli.py:658-674](python/signalos_lib/cli.py#L658-L674) |
| `context.py` | `context` | [cli.py:504-515](python/signalos_lib/cli.py#L504-L515) |
| `data_privacy.py` | `data` | [cli.py:676-689](python/signalos_lib/cli.py#L676-L689) |
| `deploy.py` | `signal-setup-deploy`, `signal-land-deploy`, `signal-canary-deploy`, `signal-benchmark` | [cli.py:804-837](python/signalos_lib/cli.py#L804-L837) |
| `design.py` | `pre-design`, `design`, `design-review`, `design-html` | [cli.py:705-753](python/signalos_lib/cli.py#L705-L753) |
| `devex.py` | `signal-devex-plan`, `signal-devex`, `signal-retro-global` | [cli.py:840-862](python/signalos_lib/cli.py#L840-L862) |
| `diagnose.py` | `diagnose` | [cli.py:591-602](python/signalos_lib/cli.py#L591-L602) |
| `harness.py` | `harness` | [cli.py:476-487](python/signalos_lib/cli.py#L476-L487) |
| `health.py` | `health` | [cli.py:583-590](python/signalos_lib/cli.py#L583-L590) |
| `intent.py` | `intent` | [cli.py:559-570](python/signalos_lib/cli.py#L559-L570) |
| `investigate.py` | `signal-investigate` | [cli.py:920-927](python/signalos_lib/cli.py#L920-L927) |
| `pause.py` | `pause` | [cli.py:472-475](python/signalos_lib/cli.py#L472-L475) |
| `plan.py` | `plan` | [cli.py:571-582](python/signalos_lib/cli.py#L571-L582) |
| `preamble.py` | `session-preamble` | [cli.py:930-933](python/signalos_lib/cli.py#L930-L933) |
| `registry.py` | `install`, `verify`, `list`, `uninstall`, `publish` | [cli.py:488-503](python/signalos_lib/cli.py#L488-L503) |
| `safety.py` | `signal-careful`, `signal-freeze`, `signal-guard`, `signal-unfreeze` | [cli.py:865-899](python/signalos_lib/cli.py#L865-L899) |
| `second_opinion.py` | `signal-second-opinion`, `signal-second-opinion-record` | [cli.py:902-918](python/signalos_lib/cli.py#L902-L918) |
| `security.py` | `signal-cso` | [cli.py:775-782](python/signalos_lib/cli.py#L775-L782) |
| `serve.py` | `serve` | [cli.py:632-634](python/signalos_lib/cli.py#L632-L634) |
| `session.py` | `session` | [cli.py:466-471](python/signalos_lib/cli.py#L466-L471) |
| `sign.py` | `sign` | [cli.py:541-558](python/signalos_lib/cli.py#L541-L558) |
| `status.py` | `status` | [cli.py:527-540](python/signalos_lib/cli.py#L527-L540) |
| `tenant.py` | `tenant` | [cli.py:691-702](python/signalos_lib/cli.py#L691-L702) |
| `validate_cmd.py` | `validate` | [cli.py:603-612](python/signalos_lib/cli.py#L603-L612) |
| `velocity.py` | `signal-autoplan`, `signal-context-restore` | [cli.py:785-801](python/signalos_lib/cli.py#L785-L801) |

#### 2.1.3 Modules without commands/<X>.py but with internal callers

| Module | Called from | Verification line |
|---|---|---|
| `browser.py` (W7 SBrowser) | `qa_runner.py` | [qa_runner.py:299](python/signalos_lib/qa_runner.py#L299) |
| `e2e_runner.py` | `orchestrator.py` | [orchestrator.py:966](python/signalos_lib/orchestrator.py#L966) |
| `harness.py` (also has wrapper) | uses `sandbox` 4× | [harness.py:439,488,582,889](python/signalos_lib/harness.py) |
| `ide.py` | `commands/init.py`, `status.py` | [init.py:42](python/signalos_lib/commands/init.py#L42), [status.py:29](python/signalos_lib/status.py#L29) |
| `oidc_provider.py` | `commands/sign.py` (guarded by `--oidc` flag) | [sign.py:157](python/signalos_lib/commands/sign.py#L157) |
| `orchestrator.py` | `commands/orchestrate.py` + tests | [orchestrator.py:849](python/signalos_lib/orchestrator.py#L849) (imports tdd_runner) |
| `qa_runner.py` | `cli.py` (`signal-qa`, `signal-qa-only`) | [cli.py:416](python/signalos_lib/cli.py#L416) |
| `sandbox.py` | `signalos_ipc_server.py`, `e2e_runner.py`, `harness.py` ×4, `tdd_runner.py` | [signalos_ipc_server.py:704](python/signalos_ipc_server.py#L704), [tdd_runner.py:134](python/signalos_lib/tdd_runner.py#L134) |
| `skill_validators.py` | `orchestrator.py` | [orchestrator.py:998](python/signalos_lib/orchestrator.py#L998) |
| `tdd_runner.py` | `orchestrator.py` | [orchestrator.py:849](python/signalos_lib/orchestrator.py#L849) |

#### 2.1.4 commands/<X>.py wrappers with no top-level module (internal CLI-only)

| Wrapper | Role | Verification |
|---|---|---|
| `commands/hooks.py` | `signalos hooks {install,verify,list}` | [cli.py:613-620](python/signalos_lib/cli.py#L613-L620) |
| `commands/init.py` | `signalos init <PATH>` (b1 release bootstrap) | [cli.py:936-938](python/signalos_lib/cli.py#L936-L938) |
| `commands/orchestrate.py` | Wraps `orchestrator.py` | [cli.py:516-526](python/signalos_lib/cli.py#L516-L526) |
| `commands/recover.py` | `signalos recover` | [cli.py:621-630](python/signalos_lib/cli.py#L621-L630) |

#### 2.1.5 TRULY ORPHANED Python modules

After full enumeration of all 40 top-level modules + all 32 wrappers + cli.py dispatch:

**`regression.py`** is the only Python sidecar module with no caller.

- Exports: `BugDescription`, `generate_regression`, `generate_regression_from_dict` ([regression.py:39-42](python/signalos_lib/regression.py#L39-L42))
- Module docstring at [regression.py:20-29](python/signalos_lib/regression.py#L20-L29) documents the intended CLI surface:
  ```
  signalos qa regression --generate ...
  signalos qa regression --run
  ```
- Grep for `signalos qa regression` in `cli.py`: **zero matches**. No `regression` subcommand parser exists. Only `--regressions` (an argument *to* `signal-qa`) exists at [cli.py:357,372](python/signalos_lib/cli.py#L357-L372).
- Grep for `from signalos_lib.regression` across `python/`: **zero matches** (verified above).
- Documented externally as if it were live in:
  - [signal-investigate.md:2,20,32,44](python/signalos_lib/_bundle/core/execution/commands/signal-investigate.md)
  - [QA/README.md:8,27,28](python/signalos_lib/_bundle/core/governance/QA/README.md)
  - [Proof/wave-07-proof-scenarios.md:18,58,59,61](python/signalos_lib/_bundle/core/governance/Proof/wave-07-proof-scenarios.md) — W7 proof scenario 102 claims this is "importable" and generates a file. Today it is importable but no command invokes it.

**Per AMD-CORE-100 (no-delete):** `regression.py` is an unfinished commitment from W7. Completion is the only acceptable action. Completion = wire a `signalos qa regression --generate / --run` subcommand in `cli.py` that calls `generate_regression_from_dict` and `qa_runner.run_scenario_suite(regression_pattern=...)`, plus a hook so a failing scenario in `/signal-qa` auto-generates a regression entry. See §4 (completion plan, TBD).

#### 2.1.6 Python sidecar — net verdict

- 40 top-level modules: **39 wired, 1 orphaned** (`regression.py`)
- 32 command wrappers: all 32 dispatched from `cli.py`
- v0.1 errors corrected: `context.py`, `pause.py`, `registry.py`, `serve.py` all have full chains (table 2.1.2 rows 4, 14, 17, 21).

**Runtime verification status:** Not done. CI runs the test suite (which exercises orchestrator.py, sandbox.py, tdd_runner.py, e2e_runner.py, skill_validators.py via the test_*.py files at `python/`), but the long-tail commands (`signalos brain …`, `signalos signal-cso …`, `signalos campaign …`) have no automated runtime verification beyond their unit tests. A real LLM session that exercises them end-to-end has not been recorded.

---

### 2.2 Rust IPC commands

**Inventory (verified by Grep `#[tauri::command]` 2026-05-20):**

| File | Count |
|---|---|
| `src-tauri/src/enforcement.rs` | 6 |
| `src-tauri/src/ipc.rs` | 29 |
| `src-tauri/src/keychain.rs` | 3 |
| `src-tauri/src/provider.rs` | 13 |
| `src-tauri/src/runtime.rs` | 5 |
| `src-tauri/src/sidecar.rs` | 2 |
| `src-tauri/src/test_automation.rs` | 6 |
| **Total** | **64** |

All 64 are registered in [main.rs:66-144](src-tauri/src/main.rs#L66-L144) inside `tauri::generate_handler!`.

#### 2.2.1 The single bridge file

Across `src/` only **one** file calls `invoke(...)` directly:

- [src/js/ipc.js](src/js/ipc.js) — defines the JS bridge using either `core.invoke` or `invoke`:
  - [ipc.js:9](src/js/ipc.js#L9): `const invokeTauri = TAURI?.core?.invoke || TAURI?.invoke;`
  - [ipc.js:46](src/js/ipc.js#L46): `async function invokeSidecar(cmd, args = {}, …)` — wraps `invoke` with request-tracking for sidecar commands that stream back via events.

The TS services additionally each define their own `tauriInvoke(cmd, args)` helper (`approvePlan.ts:11-14`, `fileTree.ts:5-9`, `protocolContext.ts:24-27`, `preview.ts:10-13`, `providerModels.ts:5-8`, `workspace.ts:5-9`) which independently calls `invoke<T>(cmd, args)` — these are the TS-side callers parallel to the JS-side `ipc.js` bridge.

#### 2.2.2 Verified caller table — 63 of 64

| Rust command | Defined | Registered | JS caller |
|---|---|---|---|
| `set_workspace` | [ipc.rs:104](src-tauri/src/ipc.rs#L104) | main.rs:68 | [ipc.js:89](src/js/ipc.js#L89), [workspace.ts:31](src/services/workspace.ts#L31) |
| `get_workspace` | ipc.rs:125 | main.rs:69 | [ipc.js:90](src/js/ipc.js#L90) |
| `validate_workspace_write` | ipc.rs:137 | main.rs:70 | [ipc.js:91](src/js/ipc.js#L91) |
| `get_project_artifacts` | ipc.rs:204 | main.rs:71 | [ipc.js:96](src/js/ipc.js#L96) |
| `open_workspace_path` | ipc.rs:343 | main.rs:72 | [ipc.js:97](src/js/ipc.js#L97) |
| `write_workspace_export` | ipc.rs:507 | main.rs:73 | [ipc.js:99](src/js/ipc.js#L99) |
| `write_workspace_files` | ipc.rs:638 | main.rs:74 | [ipc.js:101](src/js/ipc.js#L101), `approvePlan.ts:131,270`, `preview.ts:64`, `workspace.ts:57` |
| `preview_workspace_files` | ipc.rs:574 | main.rs:75 | [ipc.js:103](src/js/ipc.js#L103) |
| `read_workspace_file` | ipc.rs:380 | main.rs:76 | [ipc.js:106](src/js/ipc.js#L106) |
| `list_workspace_dir` | ipc.rs:422 | main.rs:77 | [ipc.js:108](src/js/ipc.js#L108) |
| `upsert_workspace_secret` | ipc.rs:1172 | main.rs:78 | [ipc.js:113](src/js/ipc.js#L113) |
| `list_workspace_secrets` | ipc.rs:843 | main.rs:80 | [ipc.js:116](src/js/ipc.js#L116) |
| `reveal_workspace_secret` | ipc.rs:904 | main.rs:81 | [ipc.js:118](src/js/ipc.js#L118) |
| `delete_workspace_secret` | ipc.rs:938 | main.rs:82 | [ipc.js:120](src/js/ipc.js#L120) |
| `apply_workspace_env_diff` | ipc.rs:985 | main.rs:83 | [ipc.js:122](src/js/ipc.js#L122) |
| `set_identity` | ipc.rs:736 | main.rs:85 | [ipc.js:221](src/js/ipc.js#L221) |
| `get_identity` | ipc.rs:777 | main.rs:86 | [ipc.js:222](src/js/ipc.js#L222) |
| `check_role_for_gate` | ipc.rs:806 | main.rs:87 | [ipc.js:223](src/js/ipc.js#L223) |
| `get_git_status` | ipc.rs:1444 | main.rs:88 | [ipc.js:183](src/js/ipc.js#L183) |
| `start_workspace_watch` | ipc.rs:1608 | main.rs:89 | [ipc.js:92](src/js/ipc.js#L92) |
| `check_for_updates` | ipc.rs:1529 | main.rs:91 | [ipc.js:171](src/js/ipc.js#L171) |
| `run_signal_command` | ipc.rs:1246 | main.rs:93 | [ipc.js:134](src/js/ipc.js#L134), `approvePlan.ts:30`, `workspace.ts:35,139` |
| `get_sidecar_status` | sidecar.rs:101 | main.rs:94 | [ipc.js:142](src/js/ipc.js#L142) |
| `restart_python_sidecar` | sidecar.rs:106 | main.rs:95 | [ipc.js:143](src/js/ipc.js#L143) |
| `get_wave_state` | ipc.rs:1272 | main.rs:97 | [ipc.js:177](src/js/ipc.js#L177) (via `invokeSidecar`) |
| `get_gate_status` | ipc.rs:1293 | main.rs:98 | [ipc.js:189](src/js/ipc.js#L189) (via `invokeSidecar`) |
| `sign_gate` | ipc.rs:1312 | main.rs:99 | [ipc.js:190](src/js/ipc.js#L190) (via `invokeSidecar`) |
| `get_brain_entries` | ipc.rs:1337 | main.rs:101 | [ipc.js:196](src/js/ipc.js#L196) (via `invokeSidecar`) |
| `add_brain_entry` | ipc.rs:1359 | main.rs:102 | [ipc.js:197](src/js/ipc.js#L197) (via `invokeSidecar`) |
| `get_audit_trail` | ipc.rs:1384 | main.rs:104 | [ipc.js:203](src/js/ipc.js#L203) (via `invokeSidecar`) |
| **`get_cost_summary`** | **ipc.rs:1405** | **main.rs:105** | **⚠️ NO CALLER (see 2.2.3)** |
| `store_api_key` | keychain.rs:12 | main.rs:107 | [ipc.js:303](src/js/ipc.js#L303) |
| `delete_api_key` | keychain.rs:46 | main.rs:108 | [ipc.js:305](src/js/ipc.js#L305) |
| `has_api_key` | keychain.rs:33 | main.rs:109 | [ipc.js:304](src/js/ipc.js#L304) |
| `list_providers` | provider.rs:499 | main.rs:111 | [ipc.js:267](src/js/ipc.js#L267) |
| `get_active_provider` | provider.rs:522 | main.rs:112 | [ipc.js:268](src/js/ipc.js#L268) |
| `set_active_provider` | provider.rs:527 | main.rs:113 | [ipc.js:269](src/js/ipc.js#L269) |
| `set_provider_model` | provider.rs:547 | main.rs:114 | [ipc.js:271](src/js/ipc.js#L271) |
| `set_provider_pricing` | provider.rs:574 | main.rs:115 | [ipc.js:272](src/js/ipc.js#L272) |
| `get_cost_state` | provider.rs:593 | main.rs:116 | [ipc.js:273](src/js/ipc.js#L273) |
| `record_token_usage` | provider.rs:598 | main.rs:117 | [ipc.js:274](src/js/ipc.js#L274) |
| `reset_session_cost` | provider.rs:620 | main.rs:118 | [ipc.js:275](src/js/ipc.js#L275) |
| `set_monthly_budget` | provider.rs:625 | main.rs:119 | [ipc.js:276](src/js/ipc.js#L276) |
| `fetch_provider_models` | provider.rs:1321 | main.rs:120 | [ipc.js:278](src/js/ipc.js#L278) |
| `test_provider_connection` | provider.rs:630 | main.rs:121 | [ipc.js:280](src/js/ipc.js#L280) |
| `send_provider_message` | provider.rs:1218 | main.rs:122 | [ipc.js:282](src/js/ipc.js#L282) |
| `send_provider_message_stream` | provider.rs:791 | main.rs:123 | [ipc.js:285](src/js/ipc.js#L285) |
| `probe_node` | runtime.rs:132 | main.rs:125 | [ipc.js:252](src/js/ipc.js#L252) |
| `start_preview` | runtime.rs:188 | main.rs:126 | [ipc.js:253](src/js/ipc.js#L253) |
| `stop_preview` | runtime.rs:529 | main.rs:127 | [ipc.js:254](src/js/ipc.js#L254), `preview.ts:107` |
| `list_previews` | runtime.rs:551 | main.rs:128 | [ipc.js:255](src/js/ipc.js#L255) |
| `get_preview` | runtime.rs:562 | main.rs:129 | [ipc.js:256](src/js/ipc.js#L256) |
| `get_enforcement_state` | enforcement.rs:102 | main.rs:131 | [ipc.js:241](src/js/ipc.js#L241) |
| `build_precheck` | enforcement.rs:148 | main.rs:132 | [ipc.js:242](src/js/ipc.js#L242) |
| `override_rule` | enforcement.rs:208 | main.rs:133 | [ipc.js:243](src/js/ipc.js#L243) |
| `set_rule_mode` | enforcement.rs:243 | main.rs:134 | [ipc.js:244](src/js/ipc.js#L244) |
| `freeze_wave` | enforcement.rs:268 | main.rs:135 | [ipc.js:245](src/js/ipc.js#L245) |
| `unfreeze_wave` | enforcement.rs:287 | main.rs:136 | [ipc.js:246](src/js/ipc.js#L246) |
| `list_test_debt` | test_automation.rs:38 | main.rs:138 | [ipc.js:229](src/js/ipc.js#L229) |
| `add_test_debt` | test_automation.rs:75 | main.rs:139 | [ipc.js:230](src/js/ipc.js#L230) |
| `resolve_test_debt` | test_automation.rs:109 | main.rs:140 | [ipc.js:231](src/js/ipc.js#L231) |
| `check_mutation_threshold` | test_automation.rs:164 | main.rs:141 | [ipc.js:232](src/js/ipc.js#L232) |
| `check_test_first` | test_automation.rs:199 | main.rs:142 | [ipc.js:233](src/js/ipc.js#L233) |
| `read_mutation_score` | test_automation.rs:323 | main.rs:143 | [ipc.js:235](src/js/ipc.js#L235) |

#### 2.2.3 TRULY ORPHANED Rust commands

**`get_cost_summary`** ([ipc.rs:1405-1442](src-tauri/src/ipc.rs#L1405-L1442), registered at [main.rs:105](src-tauri/src/main.rs#L105)) — no JS / TS caller anywhere in `src/`.

Verification:
- Grep `invoke\("get_cost_summary"` across `src/`: 0 matches.
- Grep `cost_summary|costSummary|cost-summary|CostSummary` across `src/`: 0 matches.
- Only documented references are in [PORTING_MAP.md:196,249,261](PORTING_MAP.md#L196) (an intent document describing what the port was supposed to wire) and the v0.1 audit document itself.

Note that `get_cost_state` (provider.rs:593) IS wired and is the command actually used for cost display in the settings/cost UI. `get_cost_summary` was apparently planned as an audit-history sibling but never connected.

**Per AMD-CORE-100 (no-delete):** unfinished commitment. Completion = surface `get_cost_summary` data either as an audit-trail enrichment (combine with `get_audit_trail` per the HistoryView pattern in `PORTING_MAP.md:249-261`) or as a dedicated panel in `HistoryView.tsx` / `SettingsView.tsx`.

#### 2.2.4 Rust IPC — net verdict

- 64 commands defined and registered.
- **63 wired** to at least one JS or TS caller.
- **1 truly orphaned**: `get_cost_summary`.

v0.1 errors corrected:
- v0.1 claimed all 6 enforcement.rs commands unwired — **all 6 are wired** ([ipc.js:241-246](src/js/ipc.js#L241-L246)).
- v0.1 claimed 5 in test_automation.rs unwired — **all 6 are wired** ([ipc.js:229-235](src/js/ipc.js#L229-L235)).
- v0.1's only correct claim in this section was `get_cost_summary`.

**Runtime verification status:** Not done. The CI build proves these commands compile and the handler list type-checks. Whether each one is exercised at runtime by the user's actual flows requires UI-driven verification (open the relevant view, trigger the action, observe the IPC call in devtools). That is a separate verification pass, not a static analysis.

---

### 2.3 Slash commands

**Inventory (verified by Glob 2026-05-20; recount 2026-05-20 independent pass = 49):**

| Location | Count |
|---|---|
| `_bundle/core/execution/commands/*.md` | 49 |

Of these 49, **35 are `/signal-*` user-facing commands** (the names this section enumerates). The remaining 14 — `context-expand.md`, `diagnose.md`, `harness-call.md`, `health.md`, `intent.md`, `plan-schema.md`, `validate-cmd.md`, plus the `signalos-*` namespace (`signalos-brain`, `signalos-install`, `signalos-orchestrate`, `signalos-publish`, `signalos-session`, `signalos-status`, `signalos-verify`) — are reference docs / internal CLI surfaces, not slash commands the user types in chat.

#### 2.3.1 The two dispatch paths

Every slash command typed in chat hits **`sendMsg()`** at [src/js/ui/chat.js:38](src/js/ui/chat.js#L38). The branch at [chat.js:50](src/js/ui/chat.js#L50) routes:

```javascript
if (val.startsWith('/signal-') || val.startsWith('/')) {
    const output = await ipc.signal.runAndWait(command, args, 60000);
    addAIBubble(...);
}
```

So every `/...` command goes through `run_signal_command` → Rust sidecar → `signalos_ipc_server.py:_handle_run_command` → **`map_slash_command(command, args, cwd)`** at [signalos_ipc_server.py:230](python/signalos_ipc_server.py#L230).

`map_slash_command` is the only dispatch table. There is no second router. If it returns `None`, [_handle_run_command:254-260](python/signalos_ipc_server.py#L254-L260) falls back to `read_command_spec(command)`, which dumps the markdown spec file with the hedging text *"This beta shows the command brief here; conversational execution is next."*

#### 2.3.2 Routed slash commands (verified by reading map_slash_command)

These 27 commands have **real execution** wired in [map_slash_command:265-359](python/signalos_ipc_server.py#L265-L359):

| Slash command | Routed via | Dispatch line |
|---|---|---|
| `/signal-status` | `signalos status --repo-root <cwd>` | [signalos_ipc_server.py:268](python/signalos_ipc_server.py#L268) |
| `/signal-init` | `signalos init <cwd> [--keep-existing/--minimal/--force]` | [signalos_ipc_server.py:271-295](python/signalos_ipc_server.py#L271-L295) |
| `/signal-brain` | `signalos brain <action> ...` | [signalos_ipc_server.py:297-307](python/signalos_ipc_server.py#L297-L307) |
| `/signal-plan` | `signalos plan {render,validate,list}` | [signalos_ipc_server.py:309-312](python/signalos_ipc_server.py#L309-L312) |
| `/signal-qa` | `signalos signal-qa ...` | [signalos_ipc_server.py:314](python/signalos_ipc_server.py#L314) |
| `/signal-qa-only` | same | [signalos_ipc_server.py:314](python/signalos_ipc_server.py#L314) |
| `/signal-orchestrate` | `signalos orchestrate ...` | [signalos_ipc_server.py:323](python/signalos_ipc_server.py#L323) |
| `/signal-build` | alias → `signalos orchestrate ...` | [signalos_ipc_server.py:323](python/signalos_ipc_server.py#L323) |
| `/signal-sign` | `signalos sign G<n>` | [signalos_ipc_server.py:327](python/signalos_ipc_server.py#L327) |
| `/signal-harness` | `signalos harness {call,status,abort}` | [signalos_ipc_server.py:331](python/signalos_ipc_server.py#L331) |
| `/signal-learn` | direct passthrough | [signalos_ipc_server.py:337](python/signalos_ipc_server.py#L337) |
| `/signal-cso` | direct passthrough | [signalos_ipc_server.py:338](python/signalos_ipc_server.py#L338) |
| `/signal-autoplan` | direct passthrough | [signalos_ipc_server.py:339](python/signalos_ipc_server.py#L339) |
| `/signal-context-restore` | direct passthrough | [signalos_ipc_server.py:340](python/signalos_ipc_server.py#L340) |
| `/signal-setup-deploy` | direct passthrough | [signalos_ipc_server.py:341](python/signalos_ipc_server.py#L341) |
| `/signal-land-deploy` | direct passthrough | [signalos_ipc_server.py:342](python/signalos_ipc_server.py#L342) |
| `/signal-canary-deploy` | direct passthrough | [signalos_ipc_server.py:343](python/signalos_ipc_server.py#L343) |
| `/signal-benchmark` | direct passthrough | [signalos_ipc_server.py:344](python/signalos_ipc_server.py#L344) |
| `/signal-devex-plan` | direct passthrough | [signalos_ipc_server.py:345](python/signalos_ipc_server.py#L345) |
| `/signal-devex` | direct passthrough | [signalos_ipc_server.py:346](python/signalos_ipc_server.py#L346) |
| `/signal-retro-global` | direct passthrough | [signalos_ipc_server.py:347](python/signalos_ipc_server.py#L347) |
| `/signal-careful` | direct passthrough | [signalos_ipc_server.py:348](python/signalos_ipc_server.py#L348) |
| `/signal-freeze` | direct → `signalos signal-freeze` (writes a freeze record, does NOT flip Rust enforcement state — see 2.3.4) | [signalos_ipc_server.py:349](python/signalos_ipc_server.py#L349) |
| `/signal-guard` | direct passthrough | [signalos_ipc_server.py:350](python/signalos_ipc_server.py#L350) |
| `/signal-unfreeze` | direct → `signalos signal-unfreeze` (see 2.3.4) | [signalos_ipc_server.py:351](python/signalos_ipc_server.py#L351) |
| `/signal-second-opinion` | direct passthrough | [signalos_ipc_server.py:352](python/signalos_ipc_server.py#L352) |
| `/signal-investigate` | direct passthrough | [signalos_ipc_server.py:354](python/signalos_ipc_server.py#L354) |

#### 2.3.3 SPEC-DUMP slash commands (fall through to spec-only fallback)

These 14 slash commands have an `.md` file but **no entry in `map_slash_command`**. Typing one in chat returns the markdown spec text prefixed with "This beta shows the command brief here; conversational execution is next."

| Slash command | .md file | Verdict |
|---|---|---|
| `/signal-pause` | [signal-pause.md](python/signalos_lib/_bundle/core/execution/commands/signal-pause.md) | Spec-dump |
| `/signal-observe` | [signal-observe.md](python/signalos_lib/_bundle/core/execution/commands/signal-observe.md) | Spec-dump |
| `/signal-onboard` | [signal-onboard.md](python/signalos_lib/_bundle/core/execution/commands/signal-onboard.md) | Spec-dump |
| `/signal-discovery` | [signal-discovery.md](python/signalos_lib/_bundle/core/execution/commands/signal-discovery.md) | Spec-dump |
| `/signal-debrief` | [signal-debrief.md](python/signalos_lib/_bundle/core/execution/commands/signal-debrief.md) | Spec-dump |
| `/signal-design` | [signal-design.md](python/signalos_lib/_bundle/core/execution/commands/signal-design.md) | Spec-dump (but `signalos design` CLI exists at [cli.py:716-733](python/signalos_lib/cli.py#L716-L733)) |
| `/signal-design-html` | [signal-design-html.md](python/signalos_lib/_bundle/core/execution/commands/signal-design-html.md) | Spec-dump (CLI exists at [cli.py:744-753](python/signalos_lib/cli.py#L744-L753)) |
| `/signal-design-review` | [signal-design-review.md](python/signalos_lib/_bundle/core/execution/commands/signal-design-review.md) | Spec-dump (CLI exists at [cli.py:735-742](python/signalos_lib/cli.py#L735-L742)) |
| `/signal-pre-design` | [signal-pre-design.md](python/signalos_lib/_bundle/core/execution/commands/signal-pre-design.md) | Spec-dump (CLI exists at [cli.py:705-714](python/signalos_lib/cli.py#L705-L714)) |
| `/signal-pre-wave` | [signal-pre-wave.md](python/signalos_lib/_bundle/core/execution/commands/signal-pre-wave.md) | Spec-dump |
| `/signal-review` | [signal-review.md](python/signalos_lib/_bundle/core/execution/commands/signal-review.md) | Spec-dump |
| `/signal-ship` | [signal-ship.md](python/signalos_lib/_bundle/core/execution/commands/signal-ship.md) | Spec-dump |
| `/signal-wave-review` | [signal-wave-review.md](python/signalos_lib/_bundle/core/execution/commands/signal-wave-review.md) | Spec-dump |
| `/signal-second-opinion-record` | (no .md found; CLI exists at [cli.py:911-918](python/signalos_lib/cli.py#L911-L918) but no `direct` set entry — falls through) | Spec-dump or unknown-command |

#### 2.3.4 The `wired-commands.js` orphan (CRITICAL)

[src/js/wired-commands.js](src/js/wired-commands.js) — 335 lines — implements every command in §2.3.3. It exports:

- [wired-commands.js:21-27](src/js/wired-commands.js#L21-L27): `STATE_COMMANDS = {/signal-pause, /signal-freeze, /signal-unfreeze, /signal-observe, /signal-onboard}`
- [wired-commands.js:29-40](src/js/wired-commands.js#L29-L40): `DOC_COMMANDS = {/signal-discovery, /signal-debrief, /signal-design, /signal-design-html, /signal-design-review, /signal-pre-design, /signal-pre-wave, /signal-review, /signal-ship, /signal-wave-review}`
- [wired-commands.js:42](src/js/wired-commands.js#L42): `isWired(command)`
- [wired-commands.js:53](src/js/wired-commands.js#L53): `runStateCommand(command, args, context)` — runs `ipc.enforcement.freeze()`, exports pause records, builds observation snapshot from `ipc.{wave,gates,audit}.get/list`, walks onboarding checklist.
- [wired-commands.js:281](src/js/wired-commands.js#L281): `runDocCommand(command, args, context)` — runs an LLM call with command-specific prompt templates and persists output via `ipc.project.exportFile`.

**These five exports are not imported anywhere.** Grep `isWired|runStateCommand|runDocCommand|STATE_COMMANDS|DOC_COMMANDS` across `src/` returns matches only inside `wired-commands.js` itself (verified).

Grep `wired-commands|wiredCommands` across `src/` returns 1 match — the docstring at [wired-commands.js:2](src/js/wired-commands.js#L2).

`sendMsg()` at [chat.js:38-64](src/js/ui/chat.js#L38) routes all `/...` through `ipc.signal.runAndWait`. There is no early-out for state/doc commands. So:

- A user clicking the `/signal-design` button at [BuildView.tsx:259](src/components/views/BuildView.tsx#L259) → fires `window.runCmd('/signal-design')` → `chat.js:runCmd` → `sendMsg` → `ipc.signal.runAndWait('signal-design', [], 60000)` → `run_signal_command` → `_handle_run_command` → `map_slash_command` returns None → `read_command_spec` returns `_bundle/.../signal-design.md` → user sees markdown spec, not a generated design.

This is the central UX failure point: **the frontend has a fully-built implementation for 13 slash commands that no router ever calls.**

#### 2.3.5 The freeze/unfreeze divergence

`/signal-freeze` and `/signal-unfreeze` are in `map_slash_command`'s `direct` set ([signalos_ipc_server.py:349,351](python/signalos_ipc_server.py#L349-L351)) — they go to `signalos signal-freeze` (commands/safety.py) which writes a freeze record. **The Rust enforcement state at [enforcement.rs:268,287](src-tauri/src/enforcement.rs#L268-L287) is NOT touched by this path.**

But the Rust enforcement state IS what `ipc.enforcement.state()` reads, which IS what `wired-commands.js:runFreeze` would flip (calling `ipc.enforcement.freeze()` → `invoke('freeze_wave')`). Since `runFreeze` is never invoked, `/signal-freeze` writes a record but the Rust state stays `wave_frozen: false`. The "wave is frozen" feedback the user sees in observation panels disagrees with what `/signal-freeze` actually did.

#### 2.3.6 BuildView's 8 command buttons — verified outcomes

[BuildView.tsx:244-281](src/components/views/BuildView.tsx#L244-L281) renders 8 clickable command tiles. Each fires `window.runCmd('/signal-X')`. Verified outcomes:

| Button | Expected effect | Actual effect |
|---|---|---|
| `/signal-status` | Show wave status | ✅ Calls `signalos status` |
| `/signal-build` | Run the build/orchestrate pipeline | ✅ Calls `signalos orchestrate` |
| `/signal-review` | AI code review | ❌ Spec dump |
| `/signal-design` | AI design generation | ❌ Spec dump |
| `/signal-debrief` | Wave retrospective | ❌ Spec dump |
| `/signal-ship` | Release readiness checklist | ❌ Spec dump |
| `/signal-freeze` | Freeze wave | ⚠️ Writes freeze record but Rust enforcement state stays `wave_frozen: false` |
| `/signal-brain` | List/add brain entries | ✅ Calls `signalos brain list` |

**4 of 8 buttons return markdown text instead of doing the command. 1 button has a divergence between two freeze mechanisms.**

#### 2.3.7 Slash commands — net verdict

- 48 `.md` spec files exist.
- **27 are routed** by `map_slash_command` to a working CLI.
- **14 are spec-dump** — chat returns the markdown text with a hedging line.
- **`wired-commands.js` (5 exports, 13 commands' worth of working frontend implementations) is entirely orphaned.**
- Of the 13 commands that wired-commands.js implements, **4 have user-clickable buttons in BuildView** that fail to do what the label says.

**Per AMD-CORE-100 (no-delete):** the completion for §2.3 is wiring `wired-commands.js` into chat.js's `sendMsg` (or a sibling dispatcher in front of it). Concretely: import `isWired`, `runStateCommand`, `runDocCommand` from `./wired-commands.js` in `chat.js`, and before the `ipc.signal.runAndWait` call at [chat.js:55](src/js/ui/chat.js#L55), branch on `isWired(command)` to call the local implementation instead. This re-uses the existing wired-commands.js file in full and turns 13 spec-dump commands into working features.

---

### 2.4 Hook scripts

**Inventory (verified by Glob 2026-05-20):**

| Location | Count |
|---|---|
| `_bundle/core/execution/hooks/*.sh` (top-level) | 2 (`pre-tool-use-guard.sh`, `exception-router.sh`) |
| `_bundle/core/execution/hooks/<event>/<event>.sh` | 4 (`step-started`, `step-completed`, `step-failed`, `pre-session-compress`) |
| `_bundle/core/execution/hooks/session-start` (no extension) | 1 |
| `_bundle/core/execution/hooks/_lib/*.sh` | 5 (`brain-auto-ingest`, `brain-session-inject`, `journal-append`, `metrics-append`, `step-pause-check`) |
| `_bundle/core/execution/hooks/_lib/*.py` | 1 (`redact.py`) |
| **Total hook scripts** | **13** |

**Hook registry / declarative configs:**

| File | Purpose |
|---|---|
| `_bundle/core/tool-adapters/_shared/hooks.json` | Static registry of 10 hook events. **Not loaded at runtime** by any Python or Rust code; used by `hook-registration-helper.sh` (an installer script) to populate IDE configs. |
| `_bundle/integrations/hooks/claude-hooks.json` | Maps Claude Code lifecycle events (SessionStart, PreToolUse Write/Edit, PostToolUse Bash) to hook scripts. **Loaded by Claude Code at runtime** when SignalOS is installed as a Claude Code plugin. |
| `_bundle/integrations/hooks/cursor-hooks.json` | Same shape for Cursor. |

#### 2.4.1 Hook scripts invoked by SignalOS's own runtime

| Hook | Invoker | Verification |
|---|---|---|
| `step-started/step-started.sh` | `harness.py:_fire_hook` | [harness.py:430,679](python/signalos_lib/harness.py#L430) — `subprocess.run(["bash", hook_script.relative_to(root).as_posix(), …])` |
| `step-completed/step-completed.sh` | `harness.py:_fire_hook` | [harness.py:739](python/signalos_lib/harness.py#L739) |
| `step-failed/step-failed.sh` | `harness.py:_fire_hook` | [harness.py:746](python/signalos_lib/harness.py#L746) |
| `_lib/step-pause-check.sh` | sourced by `step-started.sh` | [step-started.sh:128-134](python/signalos_lib/_bundle/core/execution/hooks/step-started/step-started.sh#L128-L134) — `source "$PAUSE_CHECK"` when `SIGNALOS_PLAN_STEP_JSON` env-var present |
| `_lib/journal-append.sh` | step-started/completed/failed + pause.py + step-pause-check + worktree-manager + exception-router | [step-started.sh:80,137](python/signalos_lib/_bundle/core/execution/hooks/step-started/step-started.sh#L80), [step-completed.sh:94](python/signalos_lib/_bundle/core/execution/hooks/step-completed/step-completed.sh#L94), [step-failed.sh:92](python/signalos_lib/_bundle/core/execution/hooks/step-failed/step-failed.sh#L92), [pause.py:62-90](python/signalos_lib/pause.py#L62-L90), [step-pause-check.sh:84,120](python/signalos_lib/_bundle/core/execution/hooks/_lib/step-pause-check.sh#L84), [worktree-manager.sh:127](python/signalos_lib/_bundle/core/execution/build/worktree-manager.sh#L127) |
| `_lib/metrics-append.sh` | `harness.py:_append_metric` | [harness.py:477](python/signalos_lib/harness.py#L477) |
| `_lib/redact.py` | `harness.py:_redact_text` + journal-append.sh redaction filter | [harness.py:872-884](python/signalos_lib/harness.py#L872), [journal-append.sh:109](python/signalos_lib/_bundle/core/execution/hooks/_lib/journal-append.sh#L109) |
| `_lib/brain-auto-ingest.sh` | `sign.py` fire-and-forget after gate signature | [sign.py:234-248](python/signalos_lib/sign.py#L234-L248) |
| `exception-router.sh` | `worktree-manager.sh` | [worktree-manager.sh:352](python/signalos_lib/_bundle/core/execution/build/worktree-manager.sh#L352) — `bash exception-router.sh …` |

**9 hooks invoked from SignalOS's own runtime.**

#### 2.4.2 Hook scripts invoked only via Claude Code's runtime

These are wired into [claude-hooks.json](python/signalos_lib/_bundle/integrations/hooks/claude-hooks.json). They run when SignalOS is installed as a Claude Code plugin and Claude Code fires the corresponding lifecycle event.

| Hook | Claude Code event | claude-hooks.json line |
|---|---|---|
| `session-start` (no extension; bash script) | `SessionStart` | [claude-hooks.json:8](python/signalos_lib/_bundle/integrations/hooks/claude-hooks.json#L8) |
| `pre-commit` (file referenced but not in current bundle) | `PreToolUse` matcher `Write\|Edit` | [claude-hooks.json:19](python/signalos_lib/_bundle/integrations/hooks/claude-hooks.json#L19) |
| `pre-tool-use-guard.sh` | `PreToolUse` matcher `Write\|Edit` | [claude-hooks.json:23](python/signalos_lib/_bundle/integrations/hooks/claude-hooks.json#L23) |
| `pre-merge` (file referenced but not in current bundle) | `PostToolUse` matcher `Bash` | [claude-hooks.json:34](python/signalos_lib/_bundle/integrations/hooks/claude-hooks.json#L34) |

Cascade chain from `session-start`:
- [session-start:236-241](python/signalos_lib/_bundle/core/execution/hooks/session-start#L236-L241): invokes `python3 cli/signalos session-preamble …`
- [session-start:245-248](python/signalos_lib/_bundle/core/execution/hooks/session-start#L245-L248): invokes `_lib/brain-session-inject.sh`
- [session-start:254-258](python/signalos_lib/_bundle/core/execution/hooks/session-start#L254-L258): invokes `python3 cli/signalos status`

So `brain-session-inject.sh` IS invoked, but only at Claude Code session start, not at SignalOS desktop-app boot.

#### 2.4.3 `pre-tool-use-guard.sh` — security gap

`pre-tool-use-guard.sh` is the agent-write guard. It claims to block T3 surface writes and secret patterns in the write content; writes an `AUDIT_TRAIL.jsonl` entry on block ([hooks.json:120-129](python/signalos_lib/_bundle/core/tool-adapters/_shared/hooks.json#L120-L129)).

Where it fires today:
- ✅ When SignalOS is installed as a Claude Code plugin, on every Write/Edit (declarative in claude-hooks.json).

Where it does NOT fire today:
- ❌ SignalOS's own desktop runtime. The Tauri shell + Rust IPC + Python orchestrator spawn subprocesses (npm install, dev servers, test runners, generated code execution) **without invoking `pre-tool-use-guard.sh`**.
- ❌ The orchestrator's own write path. When `orchestrator.py` writes a generated file to the workspace, no pre-write guard runs.

Verification:
- Grep `pre-tool-use-guard` across the whole repo: 8 hits, all in v0.1 audit, this v0.2 audit, the hook file itself, claude-hooks.json:23, and hooks.json registry. **Zero subprocess invocations.**

**Per AMD-CORE-100 (no-delete):** unfinished SECURITY commitment. The protocol promised a write guard; SignalOS's own runtime does not honor it. Completion = wire `pre-tool-use-guard.sh` into the orchestrator's pre-write path (`ipc.rs::write_workspace_files` → call the guard before applying) and into preview/tdd-runner/e2e-runner pre-subprocess paths.

#### 2.4.4 `pre-session-compress.sh` — duplicated, not invoked

`pre-session-compress.sh` exists at [_bundle/core/execution/hooks/pre-session-compress/pre-session-compress.sh](python/signalos_lib/_bundle/core/execution/hooks/pre-session-compress/pre-session-compress.sh). Its purpose: refuse compression if the input path includes disk-truth files (journal.jsonl, metrics.jsonl, AUDIT_TRAIL.jsonl).

Status:
- `context.py` MIRRORS this contract in Python. [context.py:86,97,581](python/signalos_lib/context.py) describe the mirroring and reference the .sh script as the spec source. They do NOT subprocess-invoke it.
- Not in claude-hooks.json. Not in cursor-hooks.json.
- Not invoked by any other shell script or Python module.

**Verdict:** **`pre-session-compress.sh` is not invoked at runtime.** Two implementations of the same contract exist (Python mirror in `context.py`, bash original in the bundle). The Python mirror IS active because `context.py` is wired through `signalos context` CLI ([cli.py:504-515](python/signalos_lib/cli.py#L504-L515)), but the `.sh` file is dead.

**Per AMD-CORE-100 (no-delete):** unfinished commitment — either complete the wiring (claude-hooks.json should fire `pre-session-compress` on the equivalent Claude lifecycle event; Tauri shell should invoke the guard before any in-memory session-compress action) OR formally retract via G5-signed DECISION-DNA entry that the Python mirror at `context.py` supersedes the shell script. The latter requires a constitution amendment.

#### 2.4.5 `hooks.json` registry — declarative-only

[_bundle/core/tool-adapters/_shared/hooks.json](python/signalos_lib/_bundle/core/tool-adapters/_shared/hooks.json) is a 130-line JSON registry of 10 hook events with `source`, `validators`, `description`, `status` fields.

- Grep `hooks.json` consumers in `python/` and `src/`: not loaded by any module at runtime.
- The only consumer is [_bundle/core/tool-adapters/_shared/hook-registration-helper.sh](python/signalos_lib/_bundle/core/tool-adapters/_shared/hook-registration-helper.sh), which is an INSTALLER that generates IDE-specific hook configs from this registry. Not invoked during normal app operation.

**Status:** registry is a documentation + install-time artifact. Not used at decision time. Acceptable as-is, but the source-of-truth pattern means changes to runtime behavior must be propagated to BOTH the registry and the per-IDE JSON files.

#### 2.4.6 Hook scripts — net verdict

- 13 hook scripts in `_bundle/core/execution/hooks/`.
- **9 invoked at runtime by SignalOS's own Python/shell code** (the harness step-* lifecycle, journal append, metrics append, redaction, brain auto-ingest, exception routing, step-pause-check via source).
- **3 invoked by Claude Code** when SignalOS is installed as a plugin: `session-start`, `pre-commit` (referenced but file absent from current bundle), `pre-tool-use-guard.sh`, `pre-merge` (referenced but file absent).
- **1 cascade-invoked by `session-start`**: `brain-session-inject.sh`.
- **`pre-session-compress.sh` is not invoked anywhere at runtime**; its contract is mirrored in Python (`context.py`).

**Critical security gap:** `pre-tool-use-guard.sh` exists and is wired into Claude Code's lifecycle BUT is NOT invoked by SignalOS's own Tauri runtime, even though SignalOS's runtime is what actually executes LLM-generated code on the user's host. This is the highest-impact unfinished commitment.

v0.1 errors corrected:
- v0.1 said `pre-tool-use-guard.sh` is dead. **Partially wrong.** It IS invoked when SignalOS runs as a Claude Code plugin. **Confirmed wrong claim:** SignalOS's own runtime does not invoke it. So the security gap is real, but the framing "dead script" was inaccurate — it's a half-wired security guard.

---

### 2.5 SKILL.md routing

**Inventory (verified by Glob 2026-05-20):**

| Location | Count |
|---|---|
| `_bundle/core/execution/**/SKILL.md` | 32 |
| `_bundle/core/governance/**/SKILL.md` | 3 |
| **Total SKILL.md files** | **35** |

The orchestrator's skill catalog at [orchestrator.py:459-502](python/signalos_lib/orchestrator.py#L459-L502) (`_SKILL_KEY_TO_PATH`) contains **35 entries**.

#### 2.5.1 Per-file routing — verified

Cross-referenced each Glob hit against the catalog. **All 35 SKILL.md files map 1-to-1 to a catalog entry.** Selected verification:

| Catalog key | Catalog path ([orchestrator.py:459-502](python/signalos_lib/orchestrator.py#L459)) | File exists |
|---|---|---|
| `test-driven-development` | `core/execution/build/test-driven-development/SKILL.md` | ✅ |
| `test-generation` | `core/execution/build/test-generation/SKILL.md` | ✅ |
| `e2e-testing` | `core/execution/build/e2e-testing/SKILL.md` | ✅ |
| `systematic-debugging` | `core/execution/build/systematic-debugging/SKILL.md` | ✅ |
| `verification-before-completion` | `core/execution/build/verification-before-completion/SKILL.md` | ✅ |
| `writing-plans` | `core/execution/plan/writing-plans/SKILL.md` | ✅ |
| `executing-plans` | `core/execution/plan/executing-plans/SKILL.md` | ✅ |
| `comprehensive-code-review` | `core/execution/review/comprehensive-code-review/SKILL.md` | ✅ |
| `receiving-code-review` | `core/execution/review/receiving-code-review/SKILL.md` | ✅ |
| `requesting-code-review` | `core/execution/review/requesting-code-review/SKILL.md` | ✅ |
| `security-audit` | `core/governance/SecurityAudit/SKILL.md` | ✅ |
| `retro-run` | `core/governance/Retro/retro-run/SKILL.md` | ✅ |
| `retrospective-analyze` | `core/governance/Retro/retrospective-analyze/SKILL.md` | ✅ |
| `subagent-driven-development` | `core/execution/subagents/subagent-driven-development/SKILL.md` | ✅ |
| `dispatching-parallel-agents` | `core/execution/subagents/dispatching-parallel-agents/SKILL.md` | ✅ |
| `using-git-worktrees` | `core/execution/worktree/using-git-worktrees/SKILL.md` | ✅ |
| `finishing-a-development-branch` | `core/execution/worktree/finishing-a-development-branch/SKILL.md` | ✅ |
| Cognitive: `belief-seed-generation`, `brainstorming`, `compress-context`, `context`, `design`, `existing-product-kit`, `headless-execution`, `intent-router`, `memory`, `observability-dashboard`, `operator-tooling`, `parallel-orchestration`, `plugin-registry`, `product-surface-mapping`, `review`, `session-journal`, `stakeholder-interview`, `task-schema` | `core/execution/skills/<key>/SKILL.md` | ✅ (18 files) |

#### 2.5.2 Two-tier enforcement model

Per orchestrator's design ([orchestrator.py:452-458 comment](python/signalos_lib/orchestrator.py#L452-L458)) skills fall into two tiers:

**Tier A — Enforced (artifact validators in `skill_validators.py`):** these have a post-write check that runs after the LLM produces files. A violation feeds back into the task's `previous_failure` field for smart retry. Catalog from [skill_validators.py:399-415](python/signalos_lib/skill_validators.py#L399-L415):

| Skill key | Validator function |
|---|---|
| `security-audit` | `_validate_security_audit` ([skill_validators.py:90](python/signalos_lib/skill_validators.py#L90)) — lint patterns + extracted-files scan |
| `test-generation` | `_validate_test_generation` ([:112](python/signalos_lib/skill_validators.py#L112)) |
| `comprehensive-code-review` | `_validate_comprehensive_code_review` ([:154](python/signalos_lib/skill_validators.py#L154)) |
| `systematic-debugging` | `_validate_systematic_debugging` ([:180](python/signalos_lib/skill_validators.py#L180)) |
| `writing-plans` | `_validate_writing_plans` ([:201](python/signalos_lib/skill_validators.py#L201)) |
| `executing-plans` | `_validate_executing_plans` ([:214](python/signalos_lib/skill_validators.py#L214)) |
| `using-git-worktrees` | `_validate_using_git_worktrees` ([:227](python/signalos_lib/skill_validators.py#L227)) |
| `receiving-code-review` | `_validate_receiving_code_review` ([:244](python/signalos_lib/skill_validators.py#L244)) |
| `requesting-code-review` | `_validate_requesting_code_review` ([:269](python/signalos_lib/skill_validators.py#L269)) |
| `finishing-a-development-branch` | `_validate_finishing_a_branch` ([:290](python/signalos_lib/skill_validators.py#L290)) |
| `verification-before-completion` | `_validate_verification_before_completion` ([:321](python/signalos_lib/skill_validators.py#L321)) |
| `retro-run` | `_validate_retro_run` ([:344](python/signalos_lib/skill_validators.py#L344)) |
| `retrospective-analyze` | `_validate_retrospective_analyze` ([:373](python/signalos_lib/skill_validators.py#L373)) |

**13 validated skills.** Invocation: [orchestrator.py:998](python/signalos_lib/orchestrator.py#L998) calls `validate_skill_artifacts(skills, task, root, written_files, task_response)`.

Two additional skills have enforcement outside `skill_validators.py`:

| Skill | Enforcement mechanism |
|---|---|
| `test-driven-development` | TDD loop in orchestrator ([orchestrator.py:849](python/signalos_lib/orchestrator.py#L849) calls `tdd_runner.run_tdd_task`) — handled multi-phase, not as a single post-write check. Comment at [skill_validators.py:402-403](python/signalos_lib/skill_validators.py#L402-L403) documents the exception. |
| `e2e-testing` | E2E enforcement in orchestrator ([orchestrator.py:966](python/signalos_lib/orchestrator.py#L966) calls `e2e_runner.run_e2e_task`) — Playwright-driven. |

**15 enforced skills total** (13 via skill_validators + 2 via dedicated runners).

**Tier B — Advisory (prompt injection only):** the remaining 20 skills get their SKILL.md content injected into the task prompt at [orchestrator.py:646-648](python/signalos_lib/orchestrator.py#L646) but have no post-write check. The LLM is supposed to follow the guidance; nothing catches it if it doesn't.

| Advisory skill | Has SKILL.md | Has validator | Status |
|---|---|---|---|
| `subagent-driven-development` | ✅ | ❌ | Advisory |
| `dispatching-parallel-agents` | ✅ | ❌ | Advisory |
| `belief-seed-generation` | ✅ | ❌ | Advisory |
| `brainstorming` | ✅ | ❌ | Advisory |
| `compress-context` | ✅ | ❌ | Advisory |
| `context` | ✅ | ❌ | Advisory |
| `design` | ✅ | ❌ | Advisory |
| `existing-product-kit` | ✅ | ❌ | Advisory |
| `headless-execution` | ✅ | ❌ | Advisory |
| `intent-router` | ✅ | ❌ | Advisory |
| `memory` | ✅ | ❌ | Advisory |
| `observability-dashboard` | ✅ | ❌ | Advisory |
| `operator-tooling` | ✅ | ❌ | Advisory |
| `parallel-orchestration` | ✅ | ❌ | Advisory |
| `plugin-registry` | ✅ | ❌ | Advisory |
| `product-surface-mapping` | ✅ | ❌ | Advisory |
| `review` | ✅ | ❌ | Advisory (lightweight; see `comprehensive-code-review` for the validated path) |
| `session-journal` | ✅ | ❌ | Advisory |
| `stakeholder-interview` | ✅ | ❌ | Advisory |
| `task-schema` | ✅ | ❌ | Advisory |

#### 2.5.3 Tag-resolution chain — two paths

Each task picks up applicable skills from two sources (unioned, explicit wins on order):

1. **Explicit task tags** — the planner LLM tags tasks via `task["skills"]: ["test-generation", "security-audit", ...]`. The orchestrator iterates that list at [orchestrator.py:646](python/signalos_lib/orchestrator.py#L646), looks up `_SKILL_KEY_TO_PATH`, and injects the SKILL.md content.

2. **Keyword regex fallback** — for plans that don't carry explicit tags, [orchestrator.py:521-540+ candidates list](python/signalos_lib/orchestrator.py#L521) maps haystack (title + description) against regex patterns and adds matching skills. Examples:
   - `tdd|red.green.refactor|write tests? first` → Test-Driven Development
   - `e2e|playwright|cypress|browser test` → End-to-End Browser Testing
   - `security|vulnerab|injection|xss|csrf|owasp|stride|threat|auth|password|secret` → Security Audit
   - `regression|debug|investigate|reproduce|bug|crash` → Systematic Debugging

[orchestrator.py:521 comment](python/signalos_lib/orchestrator.py#L521) claims 34 routable skills covered. The catalog has 35; the cognitive-only skills (`belief-seed-generation`, `brainstorming`, etc.) typically don't have a keyword pattern — they get attached only when the planner explicitly tags them.

#### 2.5.4 SKILL.md routing — net verdict

- 35 SKILL.md files. Every file maps to a catalog entry.
- 15 skills are enforced (13 validators + TDD + E2E).
- 20 skills are advisory (prompt injection only).
- Both tag sources work: explicit planner tags AND regex fallback.

**Per AMD-CORE-100 (no-delete):** advisory-only skills are not "unfinished" by themselves — the protocol intentionally has a two-tier model. But the gap worth flagging: the 18 cognitive skills (`belief-seed-generation`, `brainstorming`, `design`, etc.) cover protocol concepts like "stakeholder interview" and "operator tooling" that have no artifact shape to validate. The completion question is whether each cognitive skill should remain advisory or whether some — e.g., `design`, which the protocol gates at G3 — should have an artifact contract (presence of a `.signalos/designs/<wave>/<variant>.md` with required sections) and a validator. That's a per-skill design decision, not a blanket completion item.

**v0.1 errors corrected:** v0.1 didn't explicitly mis-state anything about skills count (it referenced 35 skills correctly elsewhere), but the v0.2 verification confirms the two-tier model and the exact 13+2 enforcement boundary with file:line citations.

---

### 2.6 Frontend signals

**Inventory:** [src/state.ts](src/state.ts) — single source of truth — exports **55 Preact signals** (verified by reading the full file).

#### 2.6.1 Two writer paths

Modern TS/TSX components write directly: `signal.value = newValue`. Verified writers in TS for:

| Signal | Writer location |
|---|---|
| `modalOpen` | read in `AddSecretModal.tsx:4`, `NewProjectModal.tsx:4`, `ExitModal.tsx:4`, `OverrideModal.tsx:4` — write via `Toolbar.tsx` / `app-v2.js` |
| `apiKeyInput`, `budgetInputValue`, `userName`, `userRole`, `workspacePath` | [Onboarding.tsx:123-190](src/components/Onboarding.tsx#L123) input handlers |
| `fileTreeEntries`, `recentlyChangedFiles` | [fileTree.ts:14,17,31,36,60](src/services/fileTree.ts) |
| `chatBubbles` | [approvePlan.ts:22,26,251](src/services/approvePlan.ts), [orchestratorEvents.ts:33,35,40,49](src/services/orchestratorEvents.ts) |
| `previewStatus`, `previewUrl`, `previewKey`, `previewDevice` | [preview.ts:74-162](src/services/preview.ts) |
| `providerModels` | [providerModels.ts:16,20](src/services/providerModels.ts) |
| `govGatesList`, `currentWaveSummary`, `currentGateInfo`, `gateActivities`, `gateCriteria` | [DashboardView.test.tsx:22-96](src/components/views/DashboardView.test.tsx); read in `DashboardView.tsx` |
| `chatInputValue`, `cmdPaletteOpen` | [BuildView.tsx:290](src/components/views/BuildView.tsx#L290), `chat.js` |
| `signFormOpen` | [DashboardView.tsx:195](src/components/views/DashboardView.tsx#L195) |
| `ai`, `aiModel`, `monthlyCap`, `updateChannel`, `userName`, `userRole` | [SettingsView.tsx:57-152](src/components/views/SettingsView.tsx) |
| `termInputValue` | [TerminalView.tsx:67](src/components/views/TerminalView.tsx#L67) |

Legacy JS components write via the [src/js/state.js proxy](src/js/state.js):

```javascript
export const state = new Proxy({}, {
  get(target, prop) {
    if (signals[prop]) return signals[prop].value;  // any signal name routes
    if (prop === 'secrets') return signals.secretsList.value;  // 6 aliases
    ...
  },
  set(target, prop, value) {
    if (signals[prop]) { signals[prop].value = value; return true; }
    ...
  }
});
```

So `state.tab = 'build'` (in app-v2.js) writes `signals.tab.value`. This bridges every Preact signal to the legacy `state` object. Verified writers via the proxy:

| Signal | Writer via legacy state proxy |
|---|---|
| `tab` | [app-v2.js:154](src/js/app-v2.js#L154) |
| `sbTab` | [app-v2.js:205](src/js/app-v2.js#L205) |
| `enforcementRules` | [app-v2.js:230](src/js/app-v2.js#L230) |
| `waveFrozen` | [app-v2.js:231,240,252](src/js/app-v2.js#L231) |
| `enfOpen` | [app-v2.js:262,269,274](src/js/app-v2.js#L262) |
| `gateOpen` | [app-v2.js:331](src/js/app-v2.js#L331) |
| `brainFilter` | [app-v2.js:484](src/js/app-v2.js#L484) |
| `copiedSecret` | [app-v2.js:538,540](src/js/app-v2.js#L538) |
| `engineRunning` | [app-v2.js:708,818,848](src/js/app-v2.js#L708) |
| `updateCheck` | [app-v2.js:827,831,838](src/js/app-v2.js#L827) |
| `terminalLines` | [app-v2.js:855,871,882,884](src/js/app-v2.js#L855) |
| `termHistIdx` | [app-v2.js:868,892,897,906](src/js/app-v2.js#L868) |
| `obStep` | [app-v2.js:1113,1118,1124](src/js/app-v2.js#L1113) |
| `keyLabel` | [app-v2.js:1131](src/js/app-v2.js#L1131) |
| `provMoreOpen` | [app-v2.js:1137](src/js/app-v2.js#L1137) |
| `keyVisible` | [app-v2.js:1142](src/js/app-v2.js#L1142) |
| `busy` | [chat.js:41,61,136](src/js/ui/chat.js#L41) |

#### 2.6.2 Per-signal verification — readers AND writers

Of the 55 signals exported from `state.ts`:

- **55 have at least one reader** in `src/`.
- **55 have at least one writer** (via direct TS access OR the legacy state proxy).
- **No orphaned signals.**

Three specific signals worth flagging:

| Signal | Status |
|---|---|
| `previewStack` | Default value `'react-vite'`. Read in [preview.ts](src/services/preview.ts) but **never reassigned** — there's no UI for the user to change the stack. This is functional (the preview hardcodes react-vite) but it's a single-value config masquerading as state. |
| `currentGateId` | Default `null`. Read in modal/sign flow, written via the legacy state proxy when the user clicks Sign Gate. Verified through proxy. |
| `engineTestState`, `engineRestartState` | Track sidecar boot tests. Written via the legacy state proxy from `app-v2.js`. Read in `SettingsView.tsx`. Verified. |

#### 2.6.3 Hybrid TS+JS state model

[src/js/state.js](src/js/state.js) is itself heavily used:

| Import location | Purpose |
|---|---|
| `src/js/app-v2.js` | Legacy main app entrypoint |
| `src/js/ui/chat.js`, `src/js/ui/dashboard.js` | Legacy view modules |
| `src/components/Sidebar.tsx`, `src/components/Toolbar.tsx`, `src/components/views/BuildView.tsx`, etc. | Modern TSX components that need to call legacy command handlers (`window.runCmd`, `window.saveIdentity`, etc.) defined in app-v2.js |

This means the codebase has TWO co-existing state models bridged by the proxy at `src/js/state.js`. Both work; neither is orphaned. The migration cleanup is a separate question from "is this code used".

#### 2.6.4 Frontend signals — net verdict

- 55 signals exported. All have readers and writers.
- The legacy `state` proxy at [src/js/state.js](src/js/state.js) is the bridge that explains the apparent gap between "modern TSX wrote nothing to signal X" and "signal X is still updated at runtime."

**No orphans. This subsystem is fully wired.**

v0.1 errors corrected: v0.1 said "DashboardView's gate activities/criteria UI exists but the backend never emits real data." Direct verification: the gate activities/criteria signals (`gateActivities`, `gateCriteria`) ARE written. The question of "is the source data real" is about whether the *backend command* populates them with real data — separate from signal wiring. See §2.2 (Rust IPC) for the backend half; the frontend signal half is wired.

---

### 2.7 Legacy JS

**Inventory (verified by Glob 2026-05-20):**

| Location | Count |
|---|---|
| `src/js/*.js` | 16 |
| `src/js/ui/*.js` | 2 (`chat.js`, `dashboard.js`) |
| **Total** | **18** |

(There is also a `src_old/` directory with duplicates — explicitly old, not in the live build tree, excluded from this audit.)

#### 2.7.1 The entrypoint chain

[index.html:10](index.html#L10) loads only `/src/main.tsx`. Verified by reading the full file:

```html
<script type="module" src="/src/main.tsx"></script>
```

[main.tsx:3](src/main.tsx#L3): `import './js/app-v2.js';` — side-effect import. From here, app-v2.js's `import` statements define the entire active legacy-JS dependency graph.

#### 2.7.2 Files in the live import chain (8 of 18)

| File | Imported by | Verification |
|---|---|---|
| `app-v2.js` | `main.tsx` | [main.tsx:3](src/main.tsx#L3) |
| `ipc.js` | 11 modules (app-v2, conversation, enforcement, file-tree, left-tabs, preview, progress, secrets, test-debt, wired-commands, wizard) + ui/chat + ui/dashboard | shown earlier |
| `wizard.js` | `app-v2.js` | [app-v2.js:17](src/js/app-v2.js#L17) |
| `conversation.js` | `app-v2.js` | [app-v2.js:18](src/js/app-v2.js#L18) |
| `ui/dashboard.js` | `app-v2.js` | [app-v2.js:19](src/js/app-v2.js#L19) |
| `ui/chat.js` | `app-v2.js` | [app-v2.js:20](src/js/app-v2.js#L20) |
| `state.js` | `app-v2.js` + TS-state-bridge consumers (`Sidebar.tsx`, `Toolbar.tsx`, `BuildView.tsx`, etc.) | [app-v2.js:24](src/js/app-v2.js#L24) |
| `util.js` | `app-v2.js` | [app-v2.js:26](src/js/app-v2.js#L26) |

`ipc.js` is the universally-imported bridge — every other JS file in the legacy tree imports it. But `ipc.js` is only itself reachable from main.tsx's chain through `app-v2.js → ui/chat.js → ipc.js` and the direct `app-v2.js → ipc.js` import.

#### 2.7.3 Orphaned legacy JS files (10 of 18)

These files exist but no live import chain reaches them.

| File | Stated purpose | Status |
|---|---|---|
| `csp-bootstrap.js` | "Runs before app-v2.js" for CSP nonce reconciliation. Reads inline `style=` into a `<style nonce>` block; rewrites inline `onclick=` into addEventListener bindings. ([csp-bootstrap.js:2](src/js/csp-bootstrap.js#L2)) | **Orphaned.** index.html does not include `<script src="csp-bootstrap.js">`. No import elsewhere. |
| `enforcement.js` | Topbar enforcement pill + override modal (Wave 3 / G2-21..25). ([enforcement.js:2](src/js/enforcement.js#L2)) | **Orphaned.** No importer. The modern equivalent is the Toolbar + OverrideModal TSX components which use `ipc.enforcement` directly. |
| `file-tree.js` | Workspace file tree with diff badges. ([file-tree.js:2](src/js/file-tree.js#L2)) | **Transitively orphaned.** Only importer is `left-tabs.js`, which is itself orphaned. The modern equivalent is `src/services/fileTree.ts`. |
| `left-tabs.js` | Files / Gov / Mem tabs in the left sidebar. ([left-tabs.js:2](src/js/left-tabs.js#L2)) | **Orphaned.** No importer. The modern equivalent is `Sidebar.tsx`. |
| `plan-reader.js` | PLAN.md parser for the SignalOS desktop app. ([plan-reader.js:2](src/js/plan-reader.js#L2)) | **Orphaned.** No importer. v0.1 was correct on this one. |
| `preview.js` | Iframe preview pane + Run controller (Wave 2 / G1-11). ([preview.js:2](src/js/preview.js#L2)) | **Orphaned.** No importer. The modern equivalent is `src/services/preview.ts` + `PreviewView.tsx`. |
| `progress.js` | Style A progress renderer driven by `sidecar:progress` events. ([progress.js:2](src/js/progress.js#L2)) | **Orphaned.** No importer. Modern equivalent: `src/services/orchestratorEvents.ts` + chat bubble streaming. |
| `secrets.js` | Replit-style secrets manager (Wave 1 / G0-6). ([secrets.js:2](src/js/secrets.js#L2)) | **Orphaned.** No importer. Modern equivalent: `VaultView.tsx` + `AddSecretModal.tsx`. |
| `test-debt.js` | Test Debt UI (Wave 5 / G4 rule 11). ([test-debt.js:2](src/js/test-debt.js#L2)) | **Orphaned.** No importer. The Rust `test_automation` commands ARE wired ([ipc.js:229-235](src/js/ipc.js#L229)) but no UI surface uses them. |
| `wired-commands.js` | Real implementations of state/doc slash commands. (See §2.3.4.) | **Orphaned.** Confirmed in Batch 3. |

Each "Orphaned" verdict was verified by Grep for the filename across `src/` and excluding the file itself.

#### 2.7.4 Pattern: feature parity in modern TSX, legacy code stuck

The 10 orphaned JS files reflect a **stalled migration**, not random dead code. Every orphan has a modern TS/TSX equivalent that took over:

| Orphaned legacy JS | Modern replacement |
|---|---|
| `enforcement.js` | Toolbar's enforcement pill + `OverrideModal.tsx` + `ipc.enforcement` |
| `file-tree.js` | `src/services/fileTree.ts` |
| `left-tabs.js` | `Sidebar.tsx` |
| `preview.js` | `src/services/preview.ts` + `PreviewView.tsx` |
| `progress.js` | `src/services/orchestratorEvents.ts` + chat bubble streaming |
| `secrets.js` | `VaultView.tsx` + `AddSecretModal.tsx` |
| `test-debt.js` | (No modern equivalent — `test_automation` IPC works but no UI surface uses it; see 2.7.5) |
| `csp-bootstrap.js` | (No equivalent — gap, see 2.7.5) |
| `plan-reader.js` | Plan rendering inlined into chat bubble (`BuildView.tsx` plan-kind bubbles) |
| `wired-commands.js` | (No equivalent — see §2.3.4) |

**Per AMD-CORE-100 (no-delete):** these are unfinished migrations. The completion question is per-file:
- If the modern TSX equivalent is feature-complete, the orphaned JS represents a stale parallel implementation — completion = formally retract via G5-signed DECISION-DNA entry that the TSX version supersedes the JS version. After retraction the file can be removed.
- If the modern equivalent is NOT feature-complete (test-debt.js → no UI, csp-bootstrap.js → no equivalent), the legacy JS represents an unfinished commitment that needs to be either re-wired or recreated in TSX.

#### 2.7.5 Specific gaps the orphans surface

- **`test-debt.js`**: The Rust IPC for test debt management is wired (`list_test_debt`, `add_test_debt`, `resolve_test_debt`, `check_mutation_threshold`, `check_test_first`, `read_mutation_score` — see §2.2.2). The frontend bridge `ipc.testAutomation` is defined ([ipc.js:228-236](src/js/ipc.js#L228)). But **no UI component imports `ipc.testAutomation`**. Verified by Grep `testAutomation` across `src/` — only the export itself. So the backend works but nothing surfaces test debt to the user.
- **`csp-bootstrap.js`**: index.html does not load it, so its CSP nonce / addEventListener rewriting never runs. Whether this matters depends on whether index.html still has inline `style=`/`onclick=` attributes. Quick verification: read the full index.html — there are NO inline attributes (only one script tag). So csp-bootstrap.js's purpose is moot today; the modern TSX has no inline attributes to reconcile.
- **`plan-reader.js`**: Functionally replaced by inline plan-kind chat bubbles. Genuinely orphaned with no gap.

#### 2.7.6 Legacy JS — net verdict

- 18 legacy JS files.
- 8 in the live import chain (main.tsx → app-v2.js → wizard, conversation, ui/dashboard, ui/chat, state, util, ipc).
- **10 orphaned.**
- Each orphan has either a TS replacement (8) or surfaces a real gap (2: `test-debt.js` → unsurfaced backend, `wired-commands.js` → unsurfaced slash commands).

v0.1 errors corrected:
- v0.1 said "11 dead legacy JS files." v0.2: **10**, with `csp-bootstrap.js` belonging to the orphan list, contrary to one v0.1 cross-check that flagged it as not on the list.
- v0.1 said `plan-reader.js` is orphaned — confirmed.
- v0.1 said `wired-commands.js` is orphaned — confirmed in detail in §2.3.4.

---

### 2.8 Governance docs

**Inventory (verified by Glob 2026-05-20):** **~67 markdown files** under `_bundle/core/governance/`. The audit question for each: *is it read at decision time, written at runtime, used at scaffold time, or display-only?*

#### 2.8.1 The four categories

| Category | Definition |
|---|---|
| **READ at decision time** | Code path opens the file and uses its content to drive runtime behavior (e.g., LLM-prompt injection, validator pass/fail logic). |
| **WRITTEN at runtime** | The file is the *output* of a command (e.g., `/signal-qa` writes QUALITY_CHECK.md). |
| **SCAFFOLD-TIME templates** | Read once by `signalos init` to copy into the user's workspace. Never re-read after install. |
| **Display-only / historical** | Never read by code; exists as a record (proof scenarios, per-wave retro docs) or as documentation. |

#### 2.8.2 READ at decision time

| Doc | Reader | Verification |
|---|---|---|
| `CONSTITUTION.md` | `protocolContext.ts:51` → injected into LLM prompt via `buildContextBlock()` ([signalosPrompt.ts:30,37](src/services/signalosPrompt.ts#L30)) | [protocolContext.ts:51](src/services/protocolContext.ts#L51) |
| `DECISION-DNA.md` | `protocolContext.ts:52` → same prompt-injection path | [protocolContext.ts:52](src/services/protocolContext.ts#L52) |
| `SOUL-DOCUMENT.md` | `protocolContext.ts:50` → same prompt-injection path | [protocolContext.ts:50](src/services/protocolContext.ts#L50) |
| `Templates/plan-template.md` | `protocolContext.ts:53` → same prompt-injection path | [protocolContext.ts:53](src/services/protocolContext.ts#L53) |
| `AMENDMENTS.md` | `context.py:540-549` — read row-by-row when `/signal-context` resolves an amendment scope | [context.py:540](python/signalos_lib/context.py#L540) |
| Per-wave Retro files (W1.1/, W1.2/, W1.3/, W2.1/) | `context.py:520-532` — read when `/signal-context --scope <wave>` is invoked | [context.py:520](python/signalos_lib/context.py#L520) |
| `CLIENT-SIGNAL-LOG.md` | `client-signal-verbatim-guard.sh` — checks that customer signals are recorded verbatim | [grep verified](python/signalos_lib/_bundle/core/governance/Validators/client-signal-verbatim-guard.sh) |
| `DATA_PROCESSING_RECORD.md` | `data-protection-guard.sh` — checks data-handling claims match validators | [grep verified](python/signalos_lib/_bundle/core/governance/Validators/data-protection-guard.sh) |

**8 docs are read to drive runtime behavior.** The validator shell scripts (e.g., `constitution-amendment-guard.sh`, `decision-dna-guard.sh`, etc.) run via `signalos validate` ([validate_cmd.py:70](python/signalos_lib/validate_cmd.py#L70)), via `signalos health` ([health.py:104](python/signalos_lib/health.py#L104) runs `wiring-guard.sh`), via the `session-start` hook ([session-start:185-189](python/signalos_lib/_bundle/core/execution/hooks/session-start#L185)), and via CI ([Validators/ci/gitlab-ci.yml](python/signalos_lib/_bundle/core/governance/Validators/ci/gitlab-ci.yml)). All four invokers are real.

#### 2.8.3 WRITTEN at runtime

| Doc | Writer | Verification |
|---|---|---|
| `QUALITY_CHECK.md` | `qa_runner.py` after gating QA run | [qa_runner.py](python/signalos_lib/qa_runner.py) |
| `AUDIT_TRAIL.jsonl` (`.signalos/`, not the bundle) | `governance.rs::append_audit` ([governance.rs:148](src-tauri/src/governance.rs#L148)) and Python sidecar via `journal-append.sh` | Verified in Batch 4 |

The `_bundle/core/governance/QUALITY_CHECK.md` file in the bundle is the **template** baseline that `signal-init` copies; the actual workspace's `QUALITY_CHECK.md` is what `/signal-qa` updates.

#### 2.8.4 SCAFFOLD-TIME templates (Templates/)

[Templates/*.md](python/signalos_lib/_bundle/core/governance/Templates/) — 21 files. Verified via the `signalos init` flow ([cli.py:936-938](python/signalos_lib/cli.py#L936) → `commands/init.py`): these are copied into the user's workspace at `signal-init` time, never re-read.

Quick inventory:
- `acceptance-criteria-template.md`, `agent-file-template.md`, `analytics-activation-card-template.md`, `checklist-template.md`, `client-signal-log-template.md`, `cr-classifier.md`, `cr-log.md`, `decision-dna-template.md`, `example-product-constitution.md`, `gotcha-skill-template.md`, `plan-template.md`, `po-brief-template.md`, `pr-checklist.md`, `prompt-library-template.md`, `qa-activation-card-template.md`, `quality-check-template.md`, `requirement-dna-template.md`, `signal-log-template.md`, `soul-document-template.md`, `spec-template.md`, `tasks-template.md`, `trust-tier-scoring.md`

`plan-template.md` is the one exception — it's BOTH a template (copied by init) AND read at decision time by `protocolContext.ts:53` (listed in §2.8.2).

#### 2.8.5 Display-only / historical (~34 docs)

These exist as documentation, records, or historical evidence. Never read by runtime code at decision time.

| Doc | Purpose | Read by code? |
|---|---|---|
| `ARTIFACT_MAP.md` | Documents the gate-to-artifact mapping for humans | No |
| `AUDIT_TRAIL_SPEC.md` | Documents the audit trail format | No (the format is enforced by code, not by reading the spec) |
| `CAPABILITY_AUDIT_v1.0.1.md` | Versioned capability snapshot | No |
| `PHASE-DEBT-PROTOCOL.md` | Process documentation | No |
| `PROMPT-LIBRARY.md` | Reference for canonical prompts | No (prompts are inlined in code, not read from this file) |
| `SIGNATURE_SPEC.md` | Documents the gate signature format | No (format is enforced by `sign.py`) |
| `ENFORCEMENT.md` | Documents the enforcement model | No |
| Per-wave `WAVE_REVIEW.md`, `docs-delta.md` (W1.1–W2.1) | Wave retrospectives | No (read by humans / `/signal-context` may surface them) |
| Per-wave `METRICS.md` | Wave metrics snapshot | Read at decision time? Only via `/signal-context` wave-scope path |
| `Proof/wave-07-proof-scenarios.md`, `wave-08`, `wave-09` | Proof scenarios = test specs | No (the proof scenarios themselves are not parsed by code; the .sh scenario runners are separate) |
| `Retro/README.md`, `retro-template.md`, `retrospective.md` | Retro docs | No |
| `SecurityAudit/references/owasp-checklist.md`, `stride-model.md`, `webview-threats.md`, `report-template.md` | Reference material attached to security-audit skill via SKILL.md | Indirect (loaded as part of the security-audit SKILL.md prompt injection in orchestrator) |
| `conversations/README.md`, `incidents/README.md`, `signal-logs/README.md` | Scaffolding placeholders | No |
| `QA/README.md`, `Validators/README.md`, `Worktree-sync/HANDOFFS.md` | Documentation | No |

#### 2.8.6 Governance docs — net verdict

- ~67 markdown files in `_bundle/core/governance/`.
- **8 read at decision time** to drive runtime behavior (LLM prompt injection + validators).
- **2 written at runtime** as workspace state (QUALITY_CHECK, AUDIT_TRAIL).
- **21 templates** copied into the workspace by `signalos init` (one of which doubles as a decision-time read).
- **~34 display-only / historical** — read by humans, not by code at decision time.

**Per AMD-CORE-100 (no-delete):** the display-only docs are not "dead" — they are governance evidence and process documentation. The completion question is whether some of them ought to graduate from display-only to decision-time readers:
- `PROMPT-LIBRARY.md` is a clear candidate. The protocol claims to have a canonical prompt library; today, prompts are inlined in `wired-commands.js` (§2.3.4) and `signalosPrompt.ts`. A completion would have prompts read FROM `PROMPT-LIBRARY.md` so the doc is the source of truth.
- `SIGNATURE_SPEC.md` documents the gate signature format; the format is also enforced by `sign.py`. Either the spec drives the code or the code drives the spec — currently neither. A completion adds a test that round-trips signatures against the spec's stated grammar.
- `ARTIFACT_MAP.md` documents gate→artifact mapping; `governance.rs:75-134` hardcodes the same mapping in Rust. Either the Rust reads from the .md or both have a drift risk.

v0.1 errors corrected:
- v0.1 said "~15 governance documents are copied to the workspace by `signal-init` but never read back by any decision-time code path." v0.2 narrows: **8 are read at decision time** (5 from the bundle directly, 3 via the validator shells). The "never read" framing missed `CONSTITUTION.md`, `SOUL-DOCUMENT.md`, `DECISION-DNA.md`, and `AMENDMENTS.md` which ARE read by `protocolContext.ts` and `context.py`.

---

### 2.9 Tool-adapter emitters

**Inventory (verified by Glob 2026-05-20):**

| Location | Count | Notes |
|---|---|---|
| `_bundle/core/tool-adapters/_shared/` | 5 | `commands.json`, `skills.json`, `hooks.json`, `session-preamble.md`, `hook-registration-helper.sh` |
| `_bundle/core/tool-adapters/emitters/<ide>/` | 8 IDEs × ~3 files | antigravity, claude-code, codex, cursor, github-copilot, harness, vs-code, windsurf |

Each IDE folder typically contains: `emit.sh`, `register-hooks.sh`, `README.md` (except `harness/`, which has only `emit.sh`).

#### 2.9.1 `register-hooks.sh` — invoked at runtime

[commands/init.py:291-313](python/signalos_lib/commands/init.py#L291-L313) calls the detected IDE's `register-hooks.sh` after `signalos init` finishes scaffolding:

```python
def _register_ide_hooks(target: Path, ide: str) -> None:
    if not ide:
        return
    script = (target / "core" / "tool-adapters" / "emitters"
              / ide / "register-hooks.sh")
    if not script.is_file():
        return
    subprocess.run(
        ["bash", script.relative_to(target).as_posix()],
        cwd=str(target), check=False,
        ...
    )
```

So `register-hooks.sh` runs once per init, for the detected IDE only. The 6 other IDEs' `register-hooks.sh` files are dormant until that IDE is detected on a future workspace.

**7 IDEs have `register-hooks.sh`** (excluding `harness/` which has none). All 7 are reachable via the same code path; whichever IDE is detected gets invoked.

#### 2.9.2 `emit.sh` — NOT invoked at runtime

[emit.sh](python/signalos_lib/_bundle/core/tool-adapters/emitters/claude-code/emit.sh) is documented as the per-IDE config emitter: takes `--commands-json`, `--skills-json`, `--hooks-json`, `--preamble`, `--output-dir` and writes IDE-specific config files into `.signalos/<ide>/`.

Status of invocation:
- Grep `bash.*emit\.sh|subprocess.*emit\.sh|\.\/emit\.sh|emitters/.+/emit` across the whole repo: **0 invocations** by any Python or Rust code.
- Only matches are:
  - Documentation in [WAVE_REVIEW W1.2](python/signalos_lib/_bundle/core/governance/Retro/waves/W1.2/WAVE_REVIEW.md): "8th emitter `harness/emit.sh` … Proved by proof scenario 32"
  - [CAPABILITY_AUDIT_v1.0.1.md:92,106](python/signalos_lib/_bundle/core/governance/Governance/CAPABILITY_AUDIT_v1.0.1.md): "All 7 parse args correctly"
  - [wiring-guard.sh:373-397](python/signalos_lib/_bundle/core/governance/Validators/wiring-guard.sh#L373) — checks `emit.sh` *exists* in each emitter dir but doesn't invoke it

So `emit.sh` is **tested for existence + argument parsing** but **never runs** during normal app operation.

**8 emit.sh scripts are dormant** (7 IDEs + harness).

#### 2.9.3 Shared registry files

| File | Read at runtime? |
|---|---|
| `_shared/commands.json` | Read by `hook-registration-helper.sh` during install. Not at decision time. |
| `_shared/skills.json` | Same — install-time only. |
| `_shared/hooks.json` | Same (verified in §2.4.5). |
| `_shared/session-preamble.md` | Read by the `session-start` hook ([session-start:230-241](python/signalos_lib/_bundle/core/execution/hooks/session-start#L230)) — IS read at runtime. |
| `_shared/hook-registration-helper.sh` | Invoked by register-hooks.sh during install. |

#### 2.9.4 Tool-adapter emitters — net verdict

- 13+ tool-adapter files in the bundle.
- **7 `register-hooks.sh` scripts are reachable** (whichever matches the detected IDE runs at init).
- **8 `emit.sh` scripts are dormant** — they pass existence checks and arg-parse tests in proof scenarios but no runtime code path invokes them.
- `_shared/` files: `session-preamble.md` is read at session-start; the JSON registries are install-time artifacts.

**Per AMD-CORE-100 (no-delete):** the dormant `emit.sh` story is the "multi-IDE adapter" commitment — the protocol promised that switching IDEs would auto-generate the right per-IDE config. Today the IDE-specific `register-hooks.sh` runs but the per-IDE config files that `emit.sh` would produce are never generated.

Completion: extend [_register_ide_hooks](python/signalos_lib/commands/init.py#L291) to invoke `emit.sh` after `register-hooks.sh` with the right argument set:

```python
emit_script = (target / "core" / "tool-adapters" / "emitters" / ide / "emit.sh")
if emit_script.is_file():
    subprocess.run([
        "bash", emit_script.relative_to(target).as_posix(),
        "--commands-json", "core/tool-adapters/_shared/commands.json",
        "--skills-json",   "core/tool-adapters/_shared/skills.json",
        "--hooks-json",    "core/tool-adapters/_shared/hooks.json",
        "--preamble",      "core/tool-adapters/_shared/session-preamble.md",
        "--output-dir",    f".signalos/{ide}",
    ], cwd=str(target), check=False)
```

v0.1 errors corrected: v0.1 said "7 IDE adapter `emit.sh` scripts present for the multi-IDE story but never invoked." v0.2: **8** emit.sh scripts (v0.1 missed `harness/emit.sh`).

---

## 3. Truly orphaned items — consolidated

Items identified in batches 1-9 that have **no live invoker** in the current codebase, ordered by domain. Each entry is an unfinished commitment per AMD-CORE-100; the completion plan in §4 maps each one to a concrete fix.

| # | Item | Domain | First identified | Completion approach (summary) |
|---|---|---|---|---|
| 1 | `python/signalos_lib/regression.py` | Python sidecar | §2.1.5 | Wire `signalos qa regression --generate/--run` subcommands in cli.py |
| 2 | `get_cost_summary` Rust command | Rust IPC | §2.2.3 | Surface in HistoryView or SettingsView (combine with `get_audit_trail`) |
| 3 | `wired-commands.js` (5 exports, 13 commands' implementations) | Slash commands | §2.3.4 | Import into `chat.js:sendMsg` and branch on `isWired(command)` before the CLI passthrough |
| 4 | 13 slash commands hit spec-dump fallback (signal-pause, observe, onboard, discovery, debrief, design, design-html, design-review, pre-design, pre-wave, review, ship, wave-review) | Slash commands | §2.3.3 | Wiring #3 above resolves all 13 |
| 5 | `pre-tool-use-guard.sh` not invoked by SignalOS's own runtime | Hook scripts | §2.4.3 | Wire into orchestrator's pre-write path + preview/tdd-runner/e2e-runner pre-subprocess paths |
| 6 | `pre-session-compress.sh` not invoked at runtime | Hook scripts | §2.4.4 | Either wire OR formally retract via G5-signed DECISION-DNA entry that the `context.py` Python mirror supersedes it |
| 7 | `_bundle/core/tool-adapters/_shared/hooks.json` not loaded at runtime | Hook scripts | §2.4.5 | Acceptable as install-time registry; flag as drift risk |
| 8 | `csp-bootstrap.js` orphaned (not loaded by index.html) | Legacy JS | §2.7.3 | Retract — no inline attributes in index.html means csp-bootstrap.js has no purpose today |
| 9 | `enforcement.js` orphaned — superseded by Toolbar + OverrideModal | Legacy JS | §2.7.3 | Retract via G5-signed DECISION-DNA |
| 10 | `file-tree.js` transitively orphaned — superseded by `fileTree.ts` | Legacy JS | §2.7.3 | Retract |
| 11 | `left-tabs.js` orphaned — superseded by `Sidebar.tsx` | Legacy JS | §2.7.3 | Retract |
| 12 | `plan-reader.js` orphaned — superseded by inline plan-kind bubbles | Legacy JS | §2.7.3 | Retract |
| 13 | `preview.js` (legacy) orphaned — superseded by `services/preview.ts` | Legacy JS | §2.7.3 | Retract |
| 14 | `progress.js` orphaned — superseded by `orchestratorEvents.ts` | Legacy JS | §2.7.3 | Retract |
| 15 | `secrets.js` orphaned — superseded by `VaultView.tsx` + `AddSecretModal.tsx` | Legacy JS | §2.7.3 | Retract |
| 16 | `test-debt.js` orphaned — backend IPC works but no UI surface | Legacy JS | §2.7.5 | Build a TSX UI surface for test debt (the Rust commands at §2.2.2 last 6 rows already work) |
| 17 | `PROMPT-LIBRARY.md`, `SIGNATURE_SPEC.md`, `ARTIFACT_MAP.md` are display-only | Governance docs | §2.8.6 | Either graduate to runtime sources of truth or accept as documentation |
| 18 | 8 `emit.sh` scripts dormant (multi-IDE adapter half-built) | Tool-adapter emitters | §2.9.4 | Extend `_register_ide_hooks` in `commands/init.py` to invoke emit.sh after register-hooks.sh |

**Total: 18 categories of unfinished commitments**, of which:
- **2 are security-relevant** (#5 pre-tool-use-guard, #6 pre-session-compress).
- **10 are stalled migrations** (#3, #4, #8-#15) — the modern replacement exists or the implementation exists but is unwired.
- **6 are structural / observability gaps** (#1, #2, #16, #17, #18, #7).

(The legacy JS retractions are the only items in this list that involve actual deletion. Per AMD-CORE-100, deletion requires a G5-signed DECISION-DNA entry documenting WHY the superseding implementation took over. Until that entry is written, the legacy file stays.)

### 3.1 Reconciliation with §6.8 violator list

§3 catches "code with no caller." §6.8 (added after the enforcement-universality principle landed) catches "output channel that bypasses the framework." Some items overlap; some are unique to one list. Single unified view:

| §3 item | §6.8 item | Status |
|---|---|---|
| #1 `regression.py` orphan | — | §3 only — planning-risk in §6.8.4 (auto-fire on QA failure, not opt-in) |
| #2 `get_cost_summary` orphan | — | §3 only |
| #3 `wired-commands.js` orphan | #9-#13 (5 reframed BuildView tiles) | **Overlapping** — wire wired-commands.js INTO orchestrator's gate-transition hooks (not into chat.js); tiles become "regenerate" |
| #4 13 spec-dump slash commands | #9-#13 + auto-fire wiring | **Overlapping** — same fix as #3 |
| #5 `pre-tool-use-guard.sh` half-wired | #3 (orchestrator file writes skip guard) | **Overlapping** — same fix, counted once |
| #6 `pre-session-compress.sh` | — | §3 only |
| #7 `hooks.json` install-only registry | — | §3 only — accepted |
| #8-#15 8 stalled-migration legacy JS | — | §3 only |
| #16 `test-debt.js` (no UI surface) | — | §3 only — planning-risk in §6.8.4 (must be system-emitted, not user-clicked) |
| #17 `PROMPT-LIBRARY.md` etc. display-only | — | §3 only |
| #18 8 `emit.sh` dormant | — | §3 only |
| — | #1 BuildView `/signal-build` duplicate | **§6.8 only** — delete tile |
| — | #2 BuildView `/signal-freeze` duplicate | **§6.8 only** — delete tile (also #8) |
| — | #4 Skill validators run post-write | **§6.8 only** — move to pre-write |
| — | #5 Chat replies have no guard | **§6.8 only** — new validator |
| — | #6 No git auto-commit at wave end | **§6.8 only** — new output channel |
| — | #7 `status.py` doesn't emit activities/criteria | **§6.8 only** — output channel dry |
| — | #8 Freeze state divergence (Python vs Rust) | **§6.8 only** — pick one store |

**Unified count:** 18 (§3) + 7 (§6.8 unique) − 3 (overlap) = **22 distinct items.**

---

## 4. Completion plan (per no-delete principle)

This section sequences the work for the 18 items in §3 by **risk class** rather than by file. Items are grouped so the highest-impact-per-effort work lands first.

### 4.1 Phase 1 — Security commitments (highest priority)

| Item | Effort | Risk | Acceptance test |
|---|---|---|---|
| #5: Wire `pre-tool-use-guard.sh` into SignalOS's own runtime | 3-5 days | Medium-high (security correctness — a buggy guard blocks legitimate writes; a missing guard is the current security gap) | Integration test: a malicious payload (e.g., a secret-shaped string in a Write/Edit call, an `rm -rf /` in a Bash call) is rejected with an `AUDIT_TRAIL.jsonl` entry. |
| #6: Decide pre-session-compress.sh — wire or retract | 1-2 days | Low (the Python mirror handles the contract) | Either: integration test that compressing a path containing `journal.jsonl` is refused at runtime. Or: a G5-signed DECISION-DNA entry that records the supersedence. |

### 4.2 Phase 2 — Stalled-migration completions (highest UX impact)

| Item | Effort | Risk | Acceptance test |
|---|---|---|---|
| #3, #4: Wire `wired-commands.js` into `chat.js:sendMsg` | 1-2 days | Low — the implementation already exists | Click the `/signal-design` button in BuildView. Result must be a generated design markdown file in `.signalos/designs/`, not a spec-dump. Apply to all 13 commands. |
| #16: Build a TSX UI surface for test debt | 2-3 days | Low — the Rust IPC works ([§2.2.2](#22-rust-ipc-commands)) | A view in the Build pane lists test debt, lets the user resolve entries, and shows mutation score. |
| #18: Wire `emit.sh` invocation into `_register_ide_hooks` | 1 day | Low (existence + arg-parsing tests already pass) | After `signalos init` on a workspace with Cursor detected, `.signalos/cursor/` contains generated config files. |

### 4.3 Phase 3 — Structural completions

| Item | Effort | Risk | Acceptance test |
|---|---|---|---|
| #1: Wire `signalos qa regression --generate/--run` in cli.py | 1 day | Low | `/signal-qa` finds a failing scenario, auto-generates a regression entry, `/signal-qa --run-regressions` re-runs it. |
| #2: Surface `get_cost_summary` in HistoryView | 0.5 day | Low | History tab shows cost per audit entry. |
| #17: Decide on `PROMPT-LIBRARY.md`, `SIGNATURE_SPEC.md`, `ARTIFACT_MAP.md` | 2-3 days | Medium — these involve protocol contracts | Either each doc graduates to runtime source of truth (code reads from it) OR each is formally categorized as documentation in an updated CAPABILITY_AUDIT. |

### 4.4 Phase 4 — Migration retractions (delete with G5)

The 8 legacy JS files in §3 items #8-#15 represent stalled migrations. Per AMD-CORE-100, deletion requires a G5-signed DECISION-DNA entry. The sequence:

1. **Per-file retraction record.** For each file: write a DECISION-DNA entry: "On 2026-05-20, file `src/js/X.js` is retracted; superseded by `src/Y.tsx`. Verification: grep shows zero importers in `src/`. Last meaningful change: `git log -1 src/js/X.js`."
2. **G5 sign** the retraction record. Per the user-signs-every-gate-by-default policy ([AMD-CORE-101](python/signalos_lib/_bundle/core/governance/Retro/AMENDMENTS.md)), this is a user action, not an auto-sign.
3. **Delete the file** in the same commit that adds the DECISION-DNA entry.

Estimated 0.5 day per file = 4 days total for items #8-#15.

### 4.5 Phase 5 — Acceptance: anti-regression CI

After all phases land, add a CI check that re-runs the §3 verification:

- **Skill-count gate:** `len(_SKILL_KEY_TO_PATH) == count(SKILL.md)`.
- **Rust-command gate:** for each `#[tauri::command]`, grep for `invoke("<name>"` somewhere in `src/`. Fail CI if any command has no caller.
- **Slash-command gate:** for each `.md` in `_bundle/core/execution/commands/`, verify `map_slash_command` routes it OR `wired-commands.js:DOC_COMMANDS/STATE_COMMANDS` handles it.
- **Hook-script gate:** for each `.sh` in `_bundle/core/execution/hooks/`, verify subprocess invocation OR claude-hooks.json entry exists.
- **Emitter gate:** for each `core/tool-adapters/emitters/<ide>/emit.sh`, verify invocation chain exists.

Estimated 1 day to write + 0.5 day per gate × 5 gates = 3.5 days.

### 4.6 Total effort estimate

| Phase | Scope | Effort |
|---|---|---|
| Phase 1 — Security (orphan list only) | `pre-tool-use-guard` wiring + `pre-session-compress` decision | 4-7 days |
| Phase 2 — Stalled migrations | wired-commands import + test-debt UI + emit.sh invocation | 4-6 days |
| Phase 3 — Structural | regression CLI + cost summary surface + governance-doc decisions | 3.5-4.5 days |
| Phase 4 — Retractions | 8 legacy JS files via G5-signed Decision-DNA | 4 days |
| Phase 5 — CI gates | anti-regression checks | 3.5 days |
| **Orphan-list subtotal** | | **~19-25 days** |
| Phase 6 — Enforcement universality (§6.8) | the 6 silent runtime violations + 5 UI reframings + planning-risk guardrails | **+11-15 days** |
| **Total to OS-grade outcome** | | **~30-40 days of focused engineering** |

**Why the §6 additions add 11-15 days and not zero:** the original §3 orphan list catches "code with no caller." §6.8 adds a stricter criterion ("output channels that bypass the enforcement framework") that surfaces 6 new items not in §3:

| New item from §6.8 | Effort |
|---|---|
| Move skill validators pre-write (currently post-write) | 3-5 days |
| Chat-response guard (parallel to IPC secret-redact) | ~2 days |
| `signalos status --json` emits gate activities + criteria | ~2 days |
| Git auto-commit at wave end + G5-gated push | 5-7 days (per v0.1 §7.3.3) |
| Freeze-state consolidation per AMD-CORE-107 | 1-2 days |
| BuildView tile reframing + 2 deletions | already in Phase 2 above |

The pre-write-guard item (§3 #5) overlaps with §6.8's "skip pre-tool-use-guard"; counted once in Phase 1.

This is still much smaller than v0.1's "6-10 months" because v0.1 over-counted unfinished commitments by ~10× (most of the 60+ items v0.1 listed were verified as already wired in v0.2). The real list is 18 orphan items + 6 enforcement-universality items = ~24 substantive tickets, each well-scoped.

---

## 5. Verification status

This v0.2 document was built from direct grep + read verification on 2026-05-20.

**What is verified:** every claim has a file:line citation. The reader can open any cited file and confirm the line says what the document claims.

**What is NOT verified:** *runtime* behavior. Code that compiles and has a static call chain may still fail at runtime due to data shape mismatches, missing dependencies, or edge cases. A real LLM session that exercises each wired path end-to-end is the next verification pass (separate from this static audit).

**Errors flagged in v0.1 that v0.2 corrects:**
- v0.1: 5 dead Python modules — v0.2: 1 (`regression.py`)
- v0.1: 11 unwired Rust commands — v0.2: 1 (`get_cost_summary`)
- v0.1: `pre-tool-use-guard.sh` is dead — v0.2: half-wired (Claude Code yes, SignalOS desktop no)
- v0.1: 9 dead legacy JS — v0.2: 10
- v0.1: ~15 governance docs never read — v0.2: 8 are read at decision time
- v0.1: 7 emit.sh dormant — v0.2: 8 dormant
- v0.1: 6-10 months of work — v0.2: ~19-25 focused days
- v0.1: relied on agent summaries — v0.2: direct verification, file:line citations
- **Batch 4:** Every hook script in `_bundle/core/execution/hooks/` against runtime callers (not just JSON declarations).
- **Batch 5:** Every `_bundle/.../SKILL.md` against the orchestrator's skill catalog routing.
- **Batch 6:** Every signal in `src/js/store.ts` (or equivalent) — readers vs writers.
- **Batch 7:** Every `src/js/*.js` (legacy) file — imported from somewhere or orphaned.
- **Batch 8:** Every doc in `_bundle/core/governance/` — read at decision time or display-only.
- **Batch 9:** Every `emit.sh` / `register-hooks.sh` in `_bundle/core/tool-adapters/` against runtime invocation.

---

## 6. Independent verification pass (2026-05-20, evening)

Separate from the original v0.2 batches, a fresh code-only verification pass spot-checked the most damaging claims by direct grep + file read (no doc reuse). Results:

### 6.1 Counts spot-checked

| Item | v0.2 claim | Recount | Verdict |
|---|---:|---:|---|
| `python/signalos_lib/*.py` (top-level) | 40 | 40 | ✅ |
| `python/signalos_lib/commands/*.py` | 32 | 32 | ✅ |
| `src-tauri/src/**/*.rs` `#[tauri::command]` | 64 | 64 | ✅ |
| `_bundle/**/SKILL.md` | 35 | 35 | ✅ |
| `_bundle/core/execution/hooks/**/*.sh` | (varied) | 11 `.sh` + 1 `redact.py` + 1 `session-start` no-ext | ✅ matches §2.4 inventory |
| `_bundle/core/execution/commands/*.md` | **48** | **49** | ❌ corrected above in §2.3 |

### 6.2 Orphan claims spot-checked

| Claim | Verification | Verdict |
|---|---|---|
| `wired-commands.js` orphaned | Grep across `src/` for the 5 export names returns matches only inside the file itself; [chat.js:50-64](src/js/ui/chat.js#L50-L64) routes every `/` command through `ipc.signal.runAndWait` with no `isWired()` branch | ✅ confirmed |
| `pre-tool-use-guard.sh` not in SignalOS's own runtime | 8 hits across repo: declarative `claude-hooks.json:23`, registry `hooks.json:120-129`, comment in `redact.py:192`, the file itself, audit docs. **Zero `subprocess.run` / `bash …` invocations from any Python or Rust source** | ✅ confirmed |
| `regression.py` orphan | Zero `import regression` / `from signalos_lib.regression` / `signalos qa regression` in `python/` or `src/` outside the module's own docstring and bundle README docs | ✅ confirmed |
| `get_cost_summary` orphan | Zero matches for `get_cost_summary` / `getCostSummary` / `cost-summary` across `src/` | ✅ confirmed |
| BuildView 8 buttons, 4 broken | [BuildView.tsx:244-283](src/components/views/BuildView.tsx#L244-L283) renders status/build/review/design/debrief/ship/freeze/brain. Cross-checked against [map_slash_command:265-359](python/signalos_ipc_server.py#L265-L359): review/design/debrief/ship are NOT in the dispatch table — they fall through to the spec-dump fallback at [signalos_ipc_server.py:254-260](python/signalos_ipc_server.py#L254-L260) | ✅ confirmed |
| `/signal-freeze` Python vs Rust divergence | Grep `freeze_wave|wave_frozen|wave-frozen` across `python/signalos_lib/`: **zero matches**. So Python's `signalos signal-freeze` (which `/signal-freeze` routes to) has no path that touches the Rust `enforcement.wave_frozen` mutex at [enforcement.rs:268-285](src-tauri/src/enforcement.rs#L268-L285) | ✅ confirmed |
| SOUL/CONSTITUTION/DECISION-DNA injected into prompts | [protocolContext.ts:44-61](src/services/protocolContext.ts#L44-L61) reads all three from the workspace, trims (4000/4000/2500 chars), exposes via `buildContextBlock()` which `signalosPrompt.ts:wrapWithSignalosContext` calls. So §2.8.2 row 1-3 stand: these are real runtime constraints, not files-on-disk | ✅ confirmed |

**Net:** every spot-checked v0.2 claim survived. Only the one count drift (48 → 49 bundle commands).

### 6.3 Vision-progress estimate (new — not in original v0.2)

Translating the verified §3 orphan list into "how close are we to the product the README + protocol describe":

- **Foundation layer: ~70-75% to vision.** Tauri + sidecar + UI boot; Soul/Constitution/Decision-DNA truly inject into every chat prompt; 35/35 skills route; 63/64 Rust commands wired; orchestrator file-extraction works end-to-end after `538d596`; CI green across 3 OSes + real Docker.
- **User-perceived completeness: lower** — because four of eight visible BuildView buttons return markdown text instead of doing the action they're labeled with, the UX impression undersells the underlying state.

### 6.4 Highest-leverage two items (re-prioritization from §4)

§4 already lists these; flagging them here because the impact/effort ratio dwarfs everything else. **Both reframed per §6.6 — the completion is not "let the user click" but "let the system enforce."**

1. **Drive the 13 unwired commands from the wave executor at gate transitions.** `wired-commands.js` has the implementations. Wire them into the orchestrator so `runDocCommand('/signal-design')` auto-fires on G2→G3 entry, `runDocCommand('/signal-debrief')` auto-fires on wave end, `runDocCommand('/signal-review')` auto-fires post-G4, etc. The corresponding BuildView tiles become "regenerate this artifact" buttons (manual regen of system-generated output) rather than "run this command" buttons. Only `/signal-build` and `/signal-freeze` tiles get deleted — they duplicate existing legitimate user actions (G2 sign and Toolbar Freeze respectively). ~2-3 days including the auto-trigger hooks. (§3 item #3 + #4.)
2. **Wire `pre-tool-use-guard.sh` into the orchestrator's pre-write path** — 3-5 days. Closes the "governed agent" credibility gap. Without it, LLM-generated code lands on disk with no pre-write check in SignalOS's own runtime, undermining the central protocol promise. (§3 item #5.) **Like TDD, this is non-skippable: every write through the orchestrator must pass the guard.**

After these two: ~85% to vision. The remaining 15% is the IDE adapter emitters, gate activities/criteria emission, git automation, retracting the stalled legacy JS migrations — all of which §4 sequences correctly.

### 6.5 Credibility risk worth naming

The vision sells SignalOS as a **governed** agent. The single biggest gap between vision and code today is **#6.4 item 2**: there is no security guard on SignalOS's own write/exec paths. The protocol promises one (`pre-tool-use-guard.sh` exists with deny rules); the protocol wires it into Claude Code lifecycle when SignalOS is installed as a plugin; but SignalOS's own Tauri runtime — where most users will actually run the app — does not invoke it.

This is not a v0.1-style speculative finding. It is verified by exhaustive grep: zero invocations from any Python or Rust source in the project.

Closing this gap should be the next merged PR.

---

### 6.6 Principle: Enforcement universality (proposed AMD-CORE-110)

> **If SignalOS produces any output through any path that bypasses its own gates, validators, or hooks, SignalOS is not an operating system — it is a CLI wrapper.**
>
> Every output channel — chat replies, file writes, subprocess execution, gate transitions, design artifacts, deploy actions — must pass through the same enforcement framework as user-written code. Enforcement is not opt-in per channel; it is the framework.

This is the principle that should retroactively classify the §3 orphan list. An orphan is not just "code with no caller" — it is **an output channel that exists outside the enforcement framework**. By that definition:

| Orphan | Real diagnosis |
|---|---|
| `pre-tool-use-guard.sh` not invoked by Tauri runtime | The Tauri runtime is an *output channel bypassing the framework*. The guard exists; the channel doesn't go through it. |
| BuildView's 8 command buttons | Each button is a *user-initiated entry point into the framework*. The framework is supposed to be system-initiated. The buttons advertise opt-in semantics that contradict enforcement. |
| `/signal-freeze` writes a Python record but Rust state doesn't flip | Two output channels expressing the same fact, neither authoritative. The framework must have a single source of truth. |
| 14 spec-dump slash commands | When the framework can't execute a command, the fallback is *plain markdown output* — a channel that's neither gated nor validated. The right fallback is "wave cannot proceed; the operator must complete the missing implementation," not "here is some markdown." |

**Operational consequence:** the completion model in §4 must include, for each item, the answer to *"what is the system-initiated trigger?"* — not just *"how does the user click it?"*

| Output | Wrong completion (user-driven) | Right completion (system-enforced) |
|---|---|---|
| Design artifact | Click `/signal-design` | On G3 entry, orchestrator auto-runs `runDocCommand('/signal-design')` and `runUIPrototype(...)` (§6.7) |
| Test execution | Click "run tests" | TDD-tagged tasks invoke `tdd_runner` automatically inside the wave loop (already the case) |
| Pre-write guard | (none today) | Orchestrator invokes guard on every file write, no opt-out (§6.4 item 2) |
| Freeze wave | Click `/signal-freeze` in BuildView | A guard-detected violation, an unsigned G-gate after timeout, or a wave-policy breach all auto-call the freeze (single Rust mutex is the only freeze state) |
| Deploy | Click `/signal-land-deploy` | G5 sign IS the deploy trigger; nothing else fires it |

**Action:** add this as AMD-CORE-110 in `DECISION-DNA.md` alongside the existing AMD-CORE-100..109. §4's completion phases re-sequence with this principle in mind: anything that requires a user button to enforce a protocol guarantee is not actually enforced.

#### 6.6.1 Enforcement ≠ always block — the override-with-audit extension

Enforcement is **block by default + allow override-with-logged-violation for authorized roles**. The audit trail is the integrity layer; the gate is the recommendation.

| Role | Default gate behavior | Override available? |
|---|---|---|
| Anonymous / unauthenticated | Sign required, no override | No |
| Operator | Sign required; override available via Toolbar Override button, every use logged | Yes (per-gate, audited) |
| **Solo owner / proven sole stakeholder** | Sign required by default; can opt to skip with each skip recorded as a violation in audit trail | Yes (per-gate, audited as violation, not as override) |
| Service account / CI | Sign required; override available only with valid OIDC token + matching role policy | Yes (per-gate, OIDC-bound) |

**Key distinction:** the override and the violation BOTH leave audit-trail evidence. The difference is semantic — an override is "I am authorized to make this call"; a violation is "I am skipping a protection I'm supposed to honor, and accept that this is recorded as such." A user who repeatedly skips signs will see the violations stack up in audit; a reviewer can decide what that means.

**Why this matters for the button inventory in §6.8:** buttons like `/signal-ship` are not "delete entirely." They become "skip G5 sign and ship now (logged violation)" for solo-owner role, and "unauthorized — sign required" for everyone else. The button stays; the semantics change; the audit-trail integrity is preserved.

**Implication for `_validate_*` skill validators:** every validator that returns "violation" must also emit an audit-trail entry that names the violation, the role at the time, and the artifact context. The validator's verdict is enforceable; the response to a violation is role-dependent.

### 6.7 Implication: G3 design produces doc AND UI prototype

A design gate that produces only markdown is not a design gate. It is a wishlist. To be reviewable for the things design exists to catch — layout, information density, accessibility, navigation, state transitions, error states — G3 must produce a **visually inspectable prototype** alongside the doc.

| G3 artifact | What it is | Where it lives |
|---|---|---|
| `design-doc.md` | The decisions: information architecture, constraints, alternatives considered, chosen approach | `.signalos/designs/<wave>/design-doc.md` |
| UI prototype | The rendering: minimum-viable visual proof. One of: Storybook stories, a static HTML mock, or a React component behind a feature flag in the live build | `.signalos/designs/<wave>/prototype/` (the orchestrator picks the format per task type) |
| Prototype audit-trail entry | Record of which prototype shape was chosen and why | Audit trail, plus appended to design-doc.md |

**Validator (matching the §2.5.2 enforcement pattern):** `_validate_design` (new) accepts any one of three valid shapes for the prototype half:

| Acceptable shape | When this is the right answer |
|---|---|
| `doc + prototype/` directory | Normal case — task has a UI surface and the agent renders it |
| `doc + external-design-ref` (Figma URL, attached image, mockup file) | User supplied the design externally; the validator records the reference rather than regenerating |
| `doc + no-UI-attestation` | Task is backend-only / schema migration / CLI command / observability tweak. The attestation is a one-line statement in design-doc.md (`UI surface: none — see attestation`) plus a validator check that the task's file list contains no `.tsx` / `.html` / `.css` writes |

Failure of all three shapes feeds back into `previous_failure` for smart retry — same loop as `_validate_security_audit` and `_validate_test_generation`. Per §6.6.1, a solo-owner role can still skip the gate sign with the skip logged as a violation; the validator's verdict and the user's response to it are separate concerns.

**Trigger (matching §6.6):** the orchestrator emits a G3 sub-task into the plan whenever the wave's `phase` transitions from G2 → G3. The user signs G3 after reviewing the chosen artifact shape. The user does not initiate G3.

**Effort impact on §4:** add a new Phase 1.5 item "Build G3 design+prototype enforcement loop" — ~5-7 days because the prototype renderer is the new piece. The doc-generation half is what `wired-commands.js:runDocCommand` already does for `/signal-design`. The external-ref and no-UI-attestation shapes are validator-only additions (~0.5 day each).

---

### 6.8 Refined violator inventory (post-feedback 2026-05-20)

Combining §6.6 (universality), §6.6.1 (override-with-audit), §6.7 (G3 prototype shapes), and the direct UI scan in [BuildView.tsx:244-283](src/components/views/BuildView.tsx#L244-L283) + [Toolbar.tsx](src/components/Toolbar.tsx). Final classification of all click surfaces against the principle:

#### 6.8.1 True violations — fix required

**UI surface (2 — these are real deletions):**

| # | Button | File | Why it violates | Fix |
|---|---|---|---|---|
| 1 | BuildView `/signal-build` tile | [BuildView.tsx:249](src/components/views/BuildView.tsx#L249) | Duplicates G2 sign (Approve & run on plan card) — the tile lets the user trigger orchestrate without signing G2 | Delete tile |
| 2 | BuildView `/signal-freeze` tile | [BuildView.tsx:274](src/components/views/BuildView.tsx#L274) | Duplicates Toolbar Freeze, AND hits the Python record path while Toolbar hits the Rust mutex → state divergence | Delete tile + fix dual-state bug (#13 below) |

**Silent runtime violations (6 — no button, output bypasses framework):**

| # | Path | File | Why it violates | Fix |
|---|---|---|---|---|
| 3 | Orchestrator file writes skip `pre-tool-use-guard.sh` | [orchestrator.py](python/signalos_lib/orchestrator.py) `_write_extracted_files` | LLM output lands on disk with no pre-write check in SignalOS's own runtime | Wire guard into the write path |
| 4 | Skill validators run **post**-write | [orchestrator.py:998](python/signalos_lib/orchestrator.py#L998) | Framework can flag but can't prevent. Malicious/broken file is already on disk when validation runs | Move content-based validators to pre-write where possible |
| 5 | Chat replies have no post-response guard | [chat.js](src/js/ui/chat.js) | LLM-generated chat output renders unfiltered: hallucinated paths, secret-shaped strings, dangerous bash snippets | Add chat-response validator (secret-redact already exists for IPC, not chat) |
| 6 | No git commit/push at wave end | (no code today) | "Agent ships your work" promise stops short; user must shell out | Auto-commit at wave end; push gated on G5 sign |
| 7 | `signalos status --json` omits gate activities/criteria | [status.py](python/signalos_lib/status.py) | DashboardView reads empty arrays — UI silently lies | Make `status.py` derive activities from PLAN tasks and criteria from skill validators |
| 8 | `/signal-freeze` (Python record) and `freeze_wave` (Rust mutex) are two separate states | [enforcement.rs:268](src-tauri/src/enforcement.rs#L268) + [commands/safety.py](python/signalos_lib/commands/safety.py) | Same fact, two answers — framework-integrity bug | Per AMD-CORE-107: pick one store, route the other path to it |

#### 6.8.2 Reframed — buttons stay, semantics change (5)

Per §6.6.1 + §6.7. These buttons survive but their **meaning** changes from "run this command on demand" to "regenerate the system-generated artifact" or "skip the gate sign with logged violation."

| # | Button | New semantics |
|---|---|---|
| 9 | BuildView `/signal-status` tile | Force-refresh of an always-displayed status indicator. The status itself must be continuously rendered somewhere in the UI (not pulled by click). |
| 10 | BuildView `/signal-review` tile | "Regenerate review" — the comprehensive-code-review skill validator already runs post-G4 automatically; this button lets the user request another pass |
| 11 | BuildView `/signal-design` tile | "Regenerate design" — auto-fires on G2→G3 transition per §6.7; this button lets the user request a fresh take if the auto-generated design is unsatisfying |
| 12 | BuildView `/signal-debrief` tile | "Regenerate debrief" — auto-fires on wave end; manual button is for re-running with different scope |
| 13 | BuildView `/signal-ship` tile | "Skip G5 sign and ship now (logged violation)" — gated by role per §6.6.1. Solo-owner: skip+audit. Operator without authorization: button is disabled with "G5 sign required" tooltip |

#### 6.8.3 Already legitimate (no change)

Per the user feedback that soft + safe items are accepted:

- **Soft violators** retained: BuildView Retry task, PreviewView Run preview, Settings Check for updates, BuildView `/signal-brain` — all legitimate user actions with system-triggered counterparts already in place or planned
- **All other 🟢 items** in §6.7 (above) and the original button table: gate signs (Approve & run, Sign gate, Confirm), human stops (Cancel wave, Rollback wave, Stop preview), Toolbar Freeze/Unfreeze (per §6.6 carve-out — control inputs, not outputs), Override (per §6.6.1), user-owned data (Vault, Brain, identity, secrets, API key, settings), navigation (tabs, sidebar, modals), Onboarding wizard, exports, chat send

#### 6.8.4 Planning-risk register (items in §4 that could become violations if built wrong)

For each, the failure mode and the correct shape:

| Planned item | Failure mode | Correct shape |
|---|---|---|
| Test debt UI (Phase 2, §4 item #16) | Built as "user clicks 'defer test' to create entries" → debt becomes opt-in | Validator detects failing/missing tests and auto-creates entries; UI lets user **review/dismiss with audit**, not **create** |
| `signalos qa regression --generate` (Phase 3, §4 item #1) | Manual `--generate` invocation → regressions become opt-in | Failing QA scenario auto-generates the regression entry; `--run` is part of the wave loop, not a separate command |
| Plugin registry (`registry.py`) | Install skips cosign trust-tier check on user request → registry becomes "trust me" | Signature check is non-skippable except via override-with-audit per §6.6.1 |
| G3 prototype enforcement (§6.7) | Validator allows doc-only as soft pass → design becomes doc-only again | Three shapes accepted (doc+prototype, doc+external-ref, doc+no-UI-attestation); no soft-pass for "missing entirely" |
| Per-IDE `emit.sh` (Phase 2, §4 item #18) | None — invoked from `_register_ide_hooks` automatically | Safe by spec |
| Anti-regression CI (Phase 5) | None — CI is system-enforced by definition | Safe by spec |

#### 6.8.5 Summary scorecard (refined)

| Category | Count |
|---|---:|
| True violations to fix (UI + runtime) | **8** (2 deletions + 6 runtime) |
| Buttons reframed (kept with new semantics) | **5** |
| Soft + safe already legitimate | **~52** |
| Planning-risk items to watch | **4** (test debt, regression, registry, prototype validator) |

**Effort impact on §4 (refined from §6.4):**
- Original §6.4 item 1 (~2-3 days) stands — auto-trigger hooks + tile reframing
- 2 tile deletions are trivial (~30 min) — collapsed into item 1
- The 6 silent runtime violations are the substantial work: pre-write guard (3-5 days per §4.1), chat-response guard (~2 days, new), git automation (per §3.3 in v0.1 plan ~5-7 days), status emission (~2 days), validation timing migration (~3-5 days), freeze-state consolidation per AMD-CORE-107 (~1-2 days)

**Updated total estimate:** ~30-40 days of focused engineering for the OS-grade outcome (vs §4.6's orphan-list-only 19-25 days). See §4.6 for the reconciled breakdown.

---

## 7. Definition of done + first PR

### 7.1 Definition of done

The plan is complete when ALL of the following are true (verified by §4.5's anti-regression CI plus manual review):

| # | Check | How to verify |
|---|---|---|
| 1 | Every `python/signalos_lib/*.py` module has at least one caller | `test_no_dead_code.py::test_python_modules_wired` — AST walk vs cli.py dispatch table |
| 2 | Every `#[tauri::command]` is invoked by at least one `src/` file | grep `invoke("<name>"` against the Rust command registry in main.rs |
| 3 | Every `_bundle/.../commands/*.md` has either `map_slash_command` routing OR is in an explicit reference-only allow-list | grep dispatch table vs file enumeration |
| 4 | Every `_bundle/.../hooks/*.sh` is either invoked at runtime OR declared in `claude-hooks.json`/`cursor-hooks.json` with a corresponding IDE-side invocation | grep `subprocess.run.*\.sh` + JSON declarative parse |
| 5 | Every `core/tool-adapters/emitters/<ide>/emit.sh` is invoked by `_register_ide_hooks` for the detected IDE | grep + integration test |
| 6 | `pre-tool-use-guard.sh` is invoked on every orchestrator file write | integration test: malicious payload (secret-shaped string, path outside workspace, dangerous bash) is rejected with AUDIT_TRAIL.jsonl entry |
| 7 | Chat-response guard runs on every LLM reply rendered into a bubble | unit test: hallucinated path / secret-shaped string is redacted before render |
| 8 | `signalos status --json` emits `activities` and `criteria` arrays for every gate | snapshot test: DashboardView renders non-empty stepper |
| 9 | Wave end auto-commits to git; G5 sign auto-pushes to remote | integration test: complete a wave, observe `git log` shows the commit; sign G5, observe `git push` succeeds |
| 10 | Freeze state has a single source of truth | grep `wave_frozen|freeze_wave` returns matches only on the chosen path (Python OR Rust, not both); both UI surfaces (Toolbar Freeze + BuildView tile decision) reflect the same state |
| 11 | G3 transition auto-emits a design+prototype task; `_validate_design` enforces one of three shapes (prototype dir / external-ref / no-UI-attestation) | integration test per shape |
| 12 | Solo-owner role can skip gate signs with each skip recorded as a violation in audit trail | role test: skip G3 sign as solo-owner → AUDIT_TRAIL.jsonl has a `violation:gate-skip` entry |
| 13 | BuildView `/signal-build` and `/signal-freeze` tiles are deleted; remaining 5 tiles are "regenerate" semantics | screenshot review |
| 14 | All CI workflows green across 3 OSes + real Docker + the new `test_no_dead_code.py` | green build on `main` |
| 15 | One end-to-end run from a clean install: install → onboard → "build me a financial dashboard" → wave completes → preview launches → G5 sign → git push → tagged release | manual beta-tester checklist |

When 15/15 pass, the plan is executed and the next audit version (v0.3) can begin.

### 7.2 First PR — start here

The single most-leveraged opening move (per §6.4 + §6.8):

**PR-1: "Enforcement universality foundation"** — ~1 week, one PR.

| File | Change |
|---|---|
| `python/signalos_lib/_bundle/core/governance/Governance/DECISION-DNA.md` | Append AMD-CORE-110 (§6.6 principle) + AMD-CORE-111 (§6.6.1 override-with-audit role model) |
| `python/signalos_lib/orchestrator.py::_write_extracted_files` | Invoke `pre-tool-use-guard.sh` on each extracted file before writing. Reject + record `AUDIT_TRAIL.jsonl` entry on guard refusal |
| `python/signalos_lib/orchestrator.py::run_wave` | After harness response, call `runDocCommand` chain at gate transitions (G2→G3 auto-fires design, wave-end auto-fires debrief) — implementation reused from `wired-commands.js` ported to Python |
| `src/components/views/BuildView.tsx:249,274` | Delete `/signal-build` and `/signal-freeze` tiles. Keep remaining 6 tiles with relabeled descriptions ("Regenerate design", "Regenerate debrief", "Force refresh status", "Request another review", "Skip G5 sign (logged violation)", "Show notes") |
| `python/test_orchestrator_core.py` | Add `test_pre_write_guard_blocks_malicious_payload` and `test_g3_auto_fires_on_transition` |
| `docs/PR-1-RELEASE-NOTES.md` | New file: lists the AMD-CORE additions, the guarded write path, the auto-trigger hooks, and the deleted/relabeled tiles |

**Acceptance:**
- `test_no_dead_code.py` skeleton passes for items #6, #11, #13 above
- A test that simulates a malicious LLM payload (secret-shaped string in a Write call) is rejected with audit-trail evidence
- BuildView screenshots show 6 tiles instead of 8, with renamed labels

**Why this PR first:**
- Lands the principle (AMD-CORE-110/111) and the most credibility-critical fix (pre-write guard) in one go
- Reframes the BuildView tiles per §6.8.2 — the most visible UX gap
- Cheapest path to "SignalOS is now a real OS, not a CLI wrapper" — closes 4 of 8 done-list items in one PR
- After PR-1 merges, the rest of the work (Phases 2-6 in §4.6) can land in parallel by subsystem

### 7.3 Subsequent PR sequence (high level)

| PR | Scope | Days | Done-list items closed |
|---|---|---|---|
| PR-1 | Enforcement foundation (above) | ~5-7 | #6, #11, #13 (partial) |
| PR-2 | Chat-response guard + freeze-state consolidation | ~3-4 | #7, #10 |
| PR-3 | `status.py` emits activities + criteria; DashboardView shows real data | ~2-3 | #8 |
| PR-4 | Git auto-commit at wave end + G5-gated push + GitHub repo creation flow | ~5-7 | #9 |
| PR-5 | Test debt UI (read-only review surface) + regression auto-generation | ~3-4 | (Phase 3 items) |
| PR-6 | `emit.sh` invocation in `_register_ide_hooks` + 8 stalled-migration retractions | ~4-5 | #5, #1-#5 anti-regression CI seeds |
| PR-7 | Anti-regression CI: `test_no_dead_code.py` complete | ~3-4 | #1, #2, #3, #4, #5 |
| PR-8 | Beta tester run — clean machine, end-to-end | ~5 + cycle | #15 |

Each PR is independently mergeable. The end-state is reached after PR-8.

---

When all batches are complete, §3 consolidates the truly orphaned set and §4 gives the per-item completion plan.
