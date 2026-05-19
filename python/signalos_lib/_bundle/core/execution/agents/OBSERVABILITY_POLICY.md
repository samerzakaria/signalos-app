<!-- SignalOS v1.0.3 — Locked 2026-04-18 -->

# Observability Policy — Core vs Extended

`Canonical path: core/execution/agents/OBSERVABILITY_POLICY.md`

This document defines what observability capabilities are required for SignalOS itself versus what is optional for production integration.

---

## Core (required for SignalOS)

These capabilities are mandatory. Every SignalOS deployment, including CI, demos, audits, and offline testing, must have them.

| Requirement | What it provides | Implementation |
|---|---|---|
| Audit trail | Append-only JSONL record of every action | AUDIT_TRAIL.jsonl (always writes, never optional) |
| Canonical verbs | signal_metric_read, signal_metric_push, signal_check_staleness | Layer 1 event contract in metrics-adapter.sh |
| Config schema | Validated YAML for mode, backends, metrics | metrics-config.example.yaml + metrics-config-validator.sh |
| Metrics map | Backend-neutral metric definitions (meaning, thresholds, staleness) | metrics-map.yaml |
| File/mock backend | Local JSON read/write with no network | file backend in metrics-adapter.sh |
| Normalized result | Standard output schema for all consumers | METRICS_RESULT_SCHEMA.md |

A SignalOS deployment with only Core observability is fully functional: it can run capability audits, validate gate checks, produce evidence packs, and verify metric thresholds — all without any external backend.

## Extended (optional for production)

These capabilities connect SignalOS to production monitoring infrastructure. They are valuable for live deployments but not required for SignalOS to operate.

| Capability | What it provides | Implementation |
|---|---|---|
| OTLP export | Push metrics to any OpenTelemetry collector | Standard mode in metrics-adapter.sh |
| Prometheus direct | PromQL query + PushGateway push | Direct backend in metrics-adapter.sh |
| Grafana direct | Datasource proxy query API | Direct backend in metrics-adapter.sh |
| Datadog direct | Metrics query + Series submit | Direct backend in metrics-adapter.sh |
| CloudWatch direct | AWS CloudWatch get/put metric data | Direct backend in metrics-adapter.sh |
| Backend-specific queries | PromQL, Datadog query, CloudWatch JSON | queries/<backend>/<metric>.yaml files |

## Boundary

SignalOS owns the meaning of metrics (what to measure, what thresholds matter, when data is stale). The SRE/platform team owns the monitoring stack (where metrics are stored, how dashboards are built, how alerts fire).

The handoff point is the metrics-adapter: SignalOS writes to it using canonical verbs, and the adapter translates to the team's backend. If no backend is configured, the file backend provides the same interface with local JSON files.

## Why this split matters

Without the core/extended distinction, every test, audit, and demo requires a live observability stack. With it, SignalOS can validate its own governance pipeline end-to-end using the file backend, and teams add production backends when they're ready.

The file backend is not a lesser mode — it is the environment-neutral baseline that makes SignalOS portable.
