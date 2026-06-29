---
description: "App-native observability: listening windows, deployment signals, and structured journal events."
---

# observe - App-Native Observability

Provides a technology-independent observability surface for delivered products.
It is the app command counterpart to `/signal-observe`.

## Usage

```text
signalos observe window create --wave W01 --belief-id B-N1 --opens-at <iso> --closes-at <iso> --expected-outcome <text> --metric <name> --threshold <n> --direction up|down
signalos observe window open --wave W01
signalos observe window reading --wave W01 --value <n> [--cohort <n>] [--slo-breach]
signalos observe window evaluate --wave W01 [--json]
signalos observe window close --wave W01 [--reason <text>]

signalos observe signal record --belief-id B-N1 --reading <text> --outcome NoSignal|MetPositive|MetNegative
signalos observe signal get <signal-id>
signalos observe signal list [--belief-id B-N1] [--listening-window-id <id>]

signalos observe journal append <event-type> [--payload '{"key":"value"}']
signalos observe journal list [--event-type <event-type>]
```

## What It Owns

- Listening-window lifecycle: `pending -> active -> closed`.
- DeploymentSignal records for belief met/invalidated/no-signal evidence.
- Structured observability journal rows for fleeting telemetry such as
  `ListeningWindowOpened`, `ListeningWindowClosed`, `DeploymentSignalRecorded`,
  `BeliefStateChanged`, and `WaveGateSigned`.
- Draft-only Keep/Kill/Iterate proposals; final decisions still require the
  human owner and governance signatures.

## Storage

- Windows: `.signalos/observability/listening-windows/`
- Deployment signals: `.signalos/observability/deployment-signals.jsonl`
- Journal: `.signalos/observability/journal.jsonl`
- Evaluation evidence: `.signalos/evidence/observability/`

All storage is local filesystem JSON/JSONL so the behavior stays independent of
ABP, .NET, SQL, Redis, Postgres, or any one product runtime.
