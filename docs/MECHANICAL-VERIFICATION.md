# Mechanical Verification Layers

SignalOS's promise is CONSTANT quality per contract, and constancy comes only
from mechanical verification. Before these layers the pipeline proved "we
built something that runs"; these three layers move it to "we built what was
signed, the tests actually test it, and the evidence was still true at
delivery." All three extend the existing review-gate channel
(`gate-compliance` rule: strict blocks, warn records) — no parallel
enforcement mechanism.

## Layer 1 — Verifiability tiers + contract-verification metric

`acceptance.classify_criterion_verifiability` deterministically classifies
every criterion from its own fields (executable `profile_target` +
subjective-wording heuristics: "should look", "feel", "intuitive", ...):

| Tier | Meaning |
|---|---|
| `mechanical` | executable test target + objective wording — build/test evidence alone proves it |
| `partial` | target exists but wording is subjective, OR objective wording with no executable target yet |
| `human` | pure judgment (look/feel/tone), no executable target |

`mechanical_pct` = `100 * mechanical / total` (1 decimal; `0.0` when no
criteria) — **the fraction of the contract that is machine-proven**.
Purely additive: tiers never change blocking semantics.

## Layer 2 — Evidence freshness binding

`evidence_freshness.snapshot_workspace` hashes (sha256) the generated files
(trace-manifest files + package.json etc.; never `.signalos/**`). Snapshots
are captured at exactly two points in `run_delivery`: after the FINAL
validation verdict (i.e. after the repair loop, whose legitimate rewrites
must not read as drift) and after runtime/UX proof (the LATEST snapshot —
nothing later may rewrite generated files). At closeout, before
`write_closeout`, `verify_workspace_snapshot` re-hashes and reports
changed/added/removed files.

Added-file detection is scan-widened (`workspace_scan_files`): the snapshot
also records a presence baseline of everything under the generated source
roots (`src/`, `tests/`, `test/`, `public/` + the root config files), and
verification flags any file the scan finds beyond both the hashed set and
that baseline — so an arbitrary post-proof file OUTSIDE the manifest-derived
candidate set is no longer invisible. Exclusions are documented inline
(`node_modules`, `dist`/`build`, `coverage`, `.signalos`, `.git`, tool
caches, `*.map`) — each exists to prevent a specific false positive from
the proof run's own side effects. Changed/removed semantics are unchanged
(hash set = manifest-derived candidates only); snapshots without a baseline
(pre-widening) keep the original narrower added semantics rather than
false-positive on pre-existing files.

## Layer 3 — Deterministic test-quality gate (first cut)

`test_quality.analyze_test_quality` scans every generated test file on disk
(manifest/trace-driven) and flags only CLEAR vacuity, never style. Two
analyzers share the report shape and blocking semantics:

- JS/TS `*.test.*` (regex, expect-style): vacuous `it(`/`test(` blocks with
  no assertion, assertion-free test files. `it.todo`/`it.skip` are never
  flagged.
- Python `test_*.py` / `*_test.py` (stdlib `ast`, precise — fastapi-api
  products generate pytest suites): a test function (module-level `test_*`
  or a `test_*` method of a `Test*` class) with no `assert` statement, no
  `pytest.raises`/`warns` usage, no `self.assert*`/`self.fail` call and no
  `pytest.fail/skip/xfail` is vacuous; a file whose collected tests are ALL
  vacuous is assertion-free. `@pytest.fixture` functions are never tests;
  parametrized tests are plain tests. A syntax-error file is reported under
  `unanalyzable_files` (never a crash, never a guess).

The (advisory) weak-criterion-link check applies to both: a criterion-linked
test file that never mentions the traced entity/operation words.

## Artifact locations

| Artifact | Location |
|---|---|
| Tiers + `verifiability_summary` | `.signalos/product/ACCEPTANCE_MATRIX.json` (per criterion + summary block) |
| Metric surfaced | `REVIEW_RESULT.json` (`verifiability`), `CLOSEOUT.json` (`verifiability_summary`), `handoffs/product-summary.md` ("N% of acceptance criteria are mechanically verified") |
| Validation snapshot | `.signalos/product/VALIDATION_RESULT.json` → `workspace_snapshot` |
| Proof snapshot (latest) | `.signalos/product/proof/runtime/smoke.json` → `workspace_snapshot` |
| Freshness verdict | `.signalos/product/EVIDENCE_FRESHNESS.json` + `CLOSEOUT.json` → `evidence_freshness` |
| Test-quality report | `.signalos/product/TEST_QUALITY.json` + folded into `REVIEW_RESULT.json` |

## Blocking semantics

| Finding | strict | warn | any mode |
|---|---|---|---|
| Verifiability tier / `mechanical_pct` | informational | informational | never blocks |
| Evidence drift after proof (changed/added/removed) | blocks: `closure_level` → `partial`, drifted files listed in `known_limitations` | recorded in `known_limitations` | — |
| Vacuous test / assertion-free test file | blocks: review `blocked`, closeout fails closed | recorded as review finding | — |
| Weak criterion link (test never mentions traced entity words) | — | — | ADVISORY ONLY, never blocks (coarse first-cut heuristic) |

Mode resolution is the review gate's `_resolve_gate_mode` (`gate-compliance`
is a core invariant: `strict`/`warn`, never `off`; any resolution failure
defaults to `strict`).

Modules: `python/signalos_lib/product/{acceptance,evidence_freshness,test_quality,delivery,closeout}.py`.
Tests: `python/test_{acceptance_verifiability,evidence_freshness,test_quality,mechanical_verification_delivery}.py`.
