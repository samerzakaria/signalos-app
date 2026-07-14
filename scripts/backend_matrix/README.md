# SignalOS backend matrix

This harness measures whether each configured model can take the same prompt through the real SignalOS backend journey and deliver a working product. It is intentionally checked into the repository so that results can be reproduced against a specific SignalOS commit, scenario, oracle, and model catalog.

## What the benchmark exercises

For every selected model, the driver creates a new, isolated workspace and starts the repository's long-lived Python sidecar. It then uses the backend protocol to initialize the project, persists an explicitly labelled simulated-founder identity (the desktop host normally owns that step), starts delivery with an explicit orchestrator profile, and at the G0 review checkpoint runs the exact project-bound `gate0:approve` consent transaction before progressing through G1 to G5. Each row also gets its own offline bare Git origin so G5 performs a real seal/commit/push without GitHub credentials or an external repository. A row passes only when the requested profile is the profile persisted by the backend, the expected gates are completed and strictly signed, release finalization succeeds, the generated product can be installed and built in a clean room, and the independent browser oracle passes against the production build.

The checked-in expense-tracker scenario covers adding and deleting expenses, reconciliation, category filtering, persistence across refresh, required expense fields, and accessibility expectations. Its requirement IDs are part of the prompt so their presence can be traced through gate artifacts.

The primary calibration cohort is intentionally a capability/value frontier,
not a price ranking: Claude Fable 5, GPT-5.6 Sol Pro, Grok 4.5, GLM 5.2,
DeepSeek V4 Pro, Qwen3.7 Max, and GPT-OSS-120B. Qwen is part of the primary
wave, while GPT-OSS remains the open-weight low-cost boundary control. Any
expected grade is a pre-run hypothesis; only repeated accepted products can
establish an allowlist tier.

This is a **backend journey benchmark**, not proof that SignalOS is production- or enterprise-ready. It does not exercise the desktop UI or Tauri boundary, SaaS tenant isolation, SSO, deployment controls, compliance operations, production sandboxing, or real-world availability. The matrix explicitly requests the stable `benchmark` orchestrator profile by default; it never relies on the desktop sidecar's `production` default. Passing this matrix must not be represented as production runtime or security assurance. It also proves only the checked-in scenario and oracle, not arbitrary product generation.

## Commands

Run commands from the repository root:

```powershell
# Inspect the versioned aliases and provider model IDs. No API request is made.
python scripts/backend_matrix/driver.py --list-models

# Validate local tools, Playwright/Chromium, a disposable source-sidecar init,
# the API key, and configured model availability.
# Preflight does not ask a model to generate a product.
python scripts/backend_matrix/driver.py --preflight

# Use an existing environment file without copying its secret into this repository.
python scripts/backend_matrix/driver.py --preflight --env-file C:\path\to\benchmark.env

# Run one paid benchmark row. Live model calls always require an explicit opt-in.
python scripts/backend_matrix/driver.py --live --models gpt56solpro `
  --orchestrator-profile benchmark `
  --max-cost-per-model 2.00 --acknowledge-key-exposure

# Run the complete configured matrix.
python scripts/backend_matrix/driver.py --live --models all `
  --orchestrator-profile benchmark `
  --max-cost-per-model 2.00 --acknowledge-key-exposure
```

Use `--list-models` to see the authoritative aliases rather than guessing provider IDs. Unknown aliases and an empty selection are errors. Run `--preflight` immediately before a paid run; model availability, account limits, and the local browser installation can change independently of this repository. `--live` is deliberately required because it makes paid external API calls. `--orchestrator-profile` accepts only `benchmark` or `production`; the default is the explicit, deterministic `benchmark` comparison contract. Use `production` only when you intentionally want the extra release-safety stages included in the measured journey.

## API key handling

The model catalog uses OpenRouter and names only the environment variable `OPENROUTER_API_KEY`; no key is stored in either JSON file. An explicit `--env-file PATH` takes precedence, followed by `SIGNALOS_MATRIX_ENV_FILE`, then the current process environment. This prevents a stale ambient key from silently overriding the account you selected for a benchmark. The driver never auto-loads the repository's `.env`. Keep environment files out of version control. The key is not placed in request payloads, command-line arguments, or result files.

Use a dedicated, revocable benchmark key with a low provider-side credit or spending limit. Do not use a broad personal, production, or application key merely because it is already available on the machine. Give the key only the access needed for this matrix and rotate it if a generated process or artifact may have exposed it. A live run refuses a key whose provider reports no spending limit, and requires the reported remaining limit to cover the requested per-model cap multiplied by the number of selected rows. A key without a provider-side limit can still run non-generating `--preflight`, which reports `safe_for_live: false`.

The driver gives the trusted provider sidecar a minimal allowlisted environment, binds its home/config/cache directories to the individual result row, and removes every ambient provider credential before adding back only the selected OpenRouter key. The backend then strips API keys, tokens, passwords, credential variables, and env-file pointers from model-authored commands, preview servers, browser proof, container-runtime processes, and tooling bootstrap children. Non-secret environment values needed for execution remain available. `--acknowledge-key-exposure` remains mandatory because the live provider process necessarily holds the selected key and no local process boundary can replace a dedicated, revocable key with a provider-side spending limit.

Never paste a key into `models.json`, a scenario, the prompt, a terminal argument, an issue, or a result bundle. If preflight cannot verify the key and cost information without revealing it, treat that as a failed preflight rather than weakening the check.

## Reproducibility and evidence

Every model row receives a fresh workspace, isolated runtime home, and new run identifier. Gate state, generated sources, dependency installation, build output, and oracle evidence are evaluated from that row only; prior workspaces, caches, pre-signed fixtures, links/reparse points, and another model's output are not accepted as evidence. The sidecar process tree is stopped before final product bytes are snapshotted. The final product check is run from a clean copy with a lockfile and a fresh dependency install so undeclared or stale local dependencies cannot produce a false pass.

The driver writes a machine-readable result bundle under its output root and prints the exact location. Results identify the engine commit and Git tree, model and provider, scenario/config/oracle hashes, event and response evidence, gate outcomes, source-tree change, clean-room build, and browser checks. A paid `--live` run refuses a dirty checkout before provider preflight, so every result maps to one reconstructable committed engine tree. Workspaces and failure artifacts are retained for diagnosis. A successful process exit means every selected row passed; any failed, blocked, incomplete, timed-out, or infrastructure-error row makes the command fail.

The browser oracle is external to the generated workspace and is not included in the model prompt. It serves the built application on an ephemeral loopback port and checks observable user behavior in fresh browser contexts. Missing Chromium or another oracle infrastructure failure is an error, never a product pass.

Result evidence records the application stack profile (`react-vite`) separately from the requested and backend-persisted orchestrator safety profile. The driver fails a row if those profile values differ. Treat a result as comparable only when its SignalOS commit/tree and its model, scenario, oracle, stack profile, and orchestrator profile match. Dirty checkouts may run the non-generating preflight, but cannot start paid matrix rows.
