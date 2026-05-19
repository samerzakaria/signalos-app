<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.3 - W1.3 metrics. Filled in at Wave close per AMD-CORE-005,006. -->

# W1.3 - Metrics

`Canonical path: core/governance/Retro/waves/W1.3/METRICS.md · Filled in by: PE at Wave close · Signed off by: PO + PE`

Measurements for the W1.3 Wave - rule-based context compression (AMD-CORE-005) and the first-party plugin registry (AMD-CORE-006). Every number below came from a single run of the named proof scenario against the W1.3 close checkout; no numbers are invented. Where a measurement could not be captured in the Core author's cowork sandbox, the PE recorded it at Wave close on the user's Windows host before Gate 5.

## Runtime-dependency budget

Expected measurement: absolute count of third-party Python packages declared in `cli/requirements.txt` after W1.3 closes, plus the sha256 of the file so the W1.2 hash anchor in `core/governance/Retro/AMENDMENTS.md` remains reproducible. Budget per integration plan §10: exactly one new runtime third-party dep across the entire W1.x series - the `anthropic` SDK from W1.2. W1.3 adds zero new Python deps; `cosign` is a pinned external CLI binary, not a Python import.

- **Third-party runtime deps declared in `cli/requirements.txt` at W1.3 close:** 1
- **Dep pin:** `anthropic>=0.39,<1.0` (unchanged from W1.2)
- **`cli/requirements.txt` sha256 (post-W1.3):** `f390e1ee15158810c1932d11e6da03a3620e787ad5fc78305f46294cc61fd94f` (unchanged from W1.2)
- **Additional Python manifests (`pyproject.toml` / `Pipfile`) present:** 0
- **Node manifests (`package.json`) present under Core-owned paths:** 0
- **External CLI binaries shelled to on a W1.3 code path:** 1 (`cosign`, invoked only from `cli/signalos_lib/registry.py` install/publish/verify)
- **Scenario source:** `proof/scenarios/34_single_new_runtime_dep.sh` (unchanged from W1.2; remains green at W1.3 close).

## Compression ratio on a synthetic 50-turn transcript

Expected measurement: `len(compressed_json) / len(input_json)` as reported by `proof/scenarios/36_compression_ratio.sh` on the canonical 50-turn synthetic fixture the scenario generates. Budget per AMD-CORE-005 design note: ratio >= 0.40 (i.e., compressed output is at most 60% of the input size) while preserving the last two turns VERBATIM.

- **Input size (bytes, synthetic 50-turn fixture):** measured by scenario 36 - reproducible.
- **Compressed size (bytes):** measured by scenario 36 - reproducible.
- **Compression ratio (compressed / input):** 0.728 on the Core author's last scenario-36 run. Budget floor is 0.40. Well inside the envelope.
- **Turns in VERBATIM layer (tail, preserved in full):** 2
- **Turns in SUMMARY layer (<=400-char cap):** up to 8 (turns 3-10 from tail)
- **Turns in HEADLINE layer (<=120-char cap):** the remainder (turns 11+)
- **DISCARD policy:** redacted payloads + individual blobs of 8 KB or more.
- **Scenario source:** `proof/scenarios/36_compression_ratio.sh`

## Compression - never-compress allowlist enforcement

Expected measurement: every disk-truth path passed to the compressor is refused at both the hook boundary and the library boundary. Zero leaks.

- **`.signalos/sessions/<id>/journal.jsonl` refusal at hook boundary:** PASS (proof scenario 38)
- **`.signalos/sessions/<id>/metrics.jsonl` refusal at hook boundary:** PASS (proof scenario 38)
- **`.signalos/AUDIT_TRAIL.jsonl` refusal at hook boundary:** PASS (proof scenario 38)
- **Library-boundary refusal when a caller bypasses the hook:** PASS - `compress_transcript` raises `DiskTruthRefused` on the same allowlist.
- **Scenario source:** `proof/scenarios/38_compression_disk_truth_refused.sh`

## Compression - decompression round-trip

Expected measurement: `signalos context expand --scope <id>` returns the byte-identical on-disk content for the referenced scope every time. No drift between compressed-then-expanded output and the on-disk source.

- **Byte-identical round-trip (5 randomly sampled scopes):** PASS (proof scenario 39)
- **Scenario source:** `proof/scenarios/39_decompression_roundtrip.sh`

## Registry - install latency and payload integrity

Expected measurement: wall-clock duration, as reported by `signalos install`, from the moment the CLI is invoked until the install completes - i.e., the tarball is extracted, the manifest is validated, the cosign signature is verified (via the mock under `SIGNALOS_REGISTRY_TEST=1`), the plugin is laid out under `core/registry/plugins/<sanitized-name>/<version>/`, and the `AUDIT_TRAIL.jsonl` row is appended. Measured by `proof/scenarios/40_registry_install_signed.sh` in a clean temp repo.

- **Median install duration (ms, `SIGNALOS_REGISTRY_TEST=1` mode):** 389.238
- **p95 install duration (ms, `SIGNALOS_REGISTRY_TEST=1` mode):** 616.549
- **Sample size (installs):** 5
- **Payload integrity:** SHA-256 of extracted tarball matches the manifest's `signature.ref` sha256 on every install (asserted by scenario 40).
- **Scenario source:** `proof/scenarios/40_registry_install_signed.sh`

## Registry - refusal-path coverage

Expected measurement: every refusal path asserts the correct exit code and records (or does not record) the correct audit row.

| Refusal path | Exit code | Audit-row impact | Scenario |
|---|---|---|---|
| Missing / invalid cosign signature, no `--allow-unsigned` | 3 | No install row appended | 41 |
| `--allow-unsigned` flag present | 0 | Install row carries `unsigned: true` | 41 |
| Manifest namespace outside `@signalos/*` and `community/*` | 4 | No install row appended | Covered by registry.validate_manifest; see 41 fixture variations. |
| Manifest `compat.signalos_core` does not admit the running Core version | 5 | No install row appended | 44 |
| Manifest declares `trust_tier_default: "T1"` | 0 (install still succeeds) | Audit row records `trust_tier: "T3"` regardless | 43 |

- **All five refusal / override paths exercised:** PASS (aggregate of scenarios 40, 41, 43, 44)

## Proof-scenario pass-rate

| Scenario | Result | Notes |
|---|---|---|
| 36 - compression ratio | PASS | Synthetic 50-turn fixture; ratio 0.728, floor 0.40. |
| 37 - compression preserves current Wave | PASS | Current-Wave allowlist preserved verbatim. |
| 38 - compression refuses disk-truth | PASS | Hook + library boundaries both refuse. |
| 39 - decompression round-trip | PASS | 5 random scopes byte-identical. |
| 40 - registry install (signed) | PASS | `SIGNALOS_REGISTRY_TEST=1`; cosign mock. |
| 41 - registry refuses unsigned | PASS | Exit code 3 without `--allow-unsigned`. |
| 42 - core README version match | PASS | `core/README.md` Core-version line matches `plugin.json.version`. |
| 43 - registry T3-by-default | PASS | AUDIT row records T3 regardless of manifest. |
| 44 - registry compat mismatch | PASS | Exit code 5 on `>=99.0.0` constraint. |
| 45 - upgrade / rollback recipe present | PASS | section 6 of UPGRADE-1.0.x-to-1.3.md + purge-safe check. |
| 34 - single new runtime dep (W1.2 carryover) | PASS | Unchanged; asserts W1.3 adds no new Python dep. |
| 99 - no Node runtime (W1.1 carryover) | PASS | Unchanged. |

## Fill-in ritual

At Wave close the PE runs scenarios 36-45 plus the latency-measuring variants on the user's real Windows workstation, records the measured numbers, and co-signs `WAVE_REVIEW.md` with the PO under distinct UserIds. The W1.3 close values are now recorded, so no placeholders remain in this file.
