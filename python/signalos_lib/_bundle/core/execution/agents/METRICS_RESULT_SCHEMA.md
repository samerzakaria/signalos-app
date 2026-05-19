<!-- SignalOS v1.0.3 — Locked 2026-04-18 -->

# Normalized Metric Result Schema

`Canonical path: core/execution/agents/METRICS_RESULT_SCHEMA.md`

Every read, push, check, and poll operation in `metrics-adapter.sh` produces output conforming to this schema. All downstream consumers (audit writers, release checks, observability agent, dashboards, qa-evidence-pack) consume this structure.

---

## Result object

```json
{
  "metric_id": "register_daily",
  "value": 42,
  "unit": "registrations",
  "timestamp": "2026-04-17T10:15:00Z",
  "backend": "prometheus",
  "mode": "direct",
  "source_status": "ok",
  "staleness_seconds": 120,
  "threshold": 50,
  "direction": "above",
  "verdict": "above_threshold"
}
```

## Field reference

| Field | Type | Required | Description |
|---|---|---|---|
| metric_id | string | yes | Snake_case identifier matching metrics-map.yaml |
| value | number or null | yes | The metric reading. Null when source_status is not "ok" |
| unit | string | no | Human-readable unit (registrations, percent, milliseconds) |
| timestamp | ISO 8601 | yes | When the reading was taken (UTC) |
| backend | string | yes | Which backend produced this reading (prometheus, grafana, datadog, cloudwatch, file, otlp) |
| mode | string | yes | "otlp" or "direct" |
| source_status | string | yes | "ok", "stale", "no_data", "error", "transport_failure" |
| staleness_seconds | integer | no | Age of the data in seconds at read time |
| threshold | number | no | From metrics-map.yaml or config |
| direction | string | no | "above" or "below" |
| verdict | string | no | "above_threshold", "below_threshold", "no_data", "stale" |

## source_status values

| Status | Meaning | Exit code |
|---|---|---|
| ok | Value read successfully, within staleness SLA | 0 |
| stale | Value read but older than staleness_sla | 2 |
| no_data | Backend returned no data for this metric | 0 |
| error | Query or connection failed | 1 |
| transport_failure | Backend unreachable or auth failed | 3 |

## Consumers

| Consumer | What it reads | How it uses the result |
|---|---|---|
| AUDIT_TRAIL.jsonl | Full result object | Appended as audit entry detail |
| qa-evidence-pack.sh | value, source_status, verdict | Signal window readings in evidence pack |
| observability agent | value, threshold, verdict, staleness_seconds | Hourly readings for signal log |
| deliver.sh poll | verdict, source_status | Gate 5 metric verification |
| Dashboards (external) | Full result object | Display via collector or direct query |

## Push result

Push operations return the same schema with `source_status` reflecting the transport outcome. The `value` field echoes the pushed value.

## OTLP mode note

In OTLP mode, reads default to direct (OTLP is push-only). The result schema is the same regardless of mode — the `mode` field distinguishes the transport used.
