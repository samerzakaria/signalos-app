# core/governance/QA/

QA artefact tree for SignalOS — created W7 Sprint QA.

```
QA/
├── scenarios/        — YAML scenario files for /signal-qa and /signal-qa-only
├── regressions/      — Auto-generated regression scenarios (signalos qa regression --generate)
├── evidence/         — JSON evidence packs + screenshot archive per wave
│   └── screenshots/  — PNG screenshots captured by SBrowser during runs
└── findings/         — QA finding documents (CRITICAL / MAJOR / MINOR)
```

## Scenario YAML schema

See `core/execution/commands/signal-qa.md` for the full schema reference.

## Running QA

```bash
# Gating run (Gate 5 entry) — populates QUALITY_CHECK.md:
signalos harness call --step signal-qa

# Fast feedback during Build — no gate ceremony:
signalos qa-only

# Generate regression scenario after a bug fix:
signalos qa regression --generate --bug-id BUG-042 --name "..." --url "https://..."

# Check wiring before running:
bash core/governance/Validators/wiring-guard.sh --check C11
bash core/governance/Validators/wiring-guard.sh --check C12
```
