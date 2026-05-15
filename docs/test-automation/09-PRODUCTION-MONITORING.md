# 09 — Production Monitoring as Testing

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

Production monitoring is the **final test layer** — it continuously validates that the system behaves correctly in the real world, with real users, real data, and real infrastructure. Synthetic monitors execute automated checks against production 24/7, while observability systems detect anomalies that no pre-production test could predict.

**What this layer kills:**
- "Nobody noticed the API was slow for 3 hours" → Synthetic monitor alerts in 60 seconds
- "A user reported a bug we can't reproduce" → Distributed tracing shows exact failure path
- "We deployed and it seemed fine, broke 2 hours later" → Auto-rollback on SLO breach
- "We don't know if the fix actually worked" → Canary deploy proves fix under real load
- "Production has a different behavior than staging" → Production-specific synthetic tests

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                  PRODUCTION MONITORING LAYERS                                 │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ SYNTHETIC MONITORING (Active)                                          │  │
│  │                                                                        │  │
│  │ • Health endpoint checks every 30s                                     │  │
│  │ • Critical user journey replay every 5 min                            │  │
│  │ • Cross-region availability checks                                     │  │
│  │ • API contract verification (subset of contract tests)                 │  │
│  │ • SSL certificate expiry monitoring                                    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ SLO/SLI MONITORING (Passive)                                           │  │
│  │                                                                        │  │
│  │ • Error budget tracking (99.9% availability = 43 min/month downtime)  │  │
│  │ • Latency SLI: p50, p95, p99 per endpoint                            │  │
│  │ • Availability SLI: successful requests / total requests              │  │
│  │ • Burn rate alerts (error budget consumed too fast)                    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ OBSERVABILITY (Diagnostic)                                             │  │
│  │                                                                        │  │
│  │ • Distributed tracing (OpenTelemetry → Jaeger/Tempo)                  │  │
│  │ • Structured logging (Serilog → Seq/Elastic)                          │  │
│  │ • Metrics (Prometheus → Grafana)                                      │  │
│  │ • Exception tracking (Sentry/Application Insights)                    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ AUTOMATED RESPONSE                                                     │  │
│  │                                                                        │  │
│  │ • Auto-rollback on error rate spike                                    │  │
│  │ • Auto-scale on load threshold                                        │  │
│  │ • Circuit breaker dashboard                                            │  │
│  │ • Incident auto-creation                                               │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  Gate: SLO met | Synthetics green | Error budget > 0 | No P1 alerts        │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Synthetic Monitoring

### 1.1 Health Check Configuration

```csharp
// HealthCheckConfiguration.cs — Production health checks
public static class HealthCheckConfiguration
{
    public static IServiceCollection AddProductionHealthChecks(
        this IServiceCollection services, IConfiguration config)
    {
        services.AddHealthChecks()
            // Database connectivity
            .AddSqlServer(
                config.GetConnectionString("Default")!,
                name: "database",
                tags: new[] { "db", "critical" },
                timeout: TimeSpan.FromSeconds(5))
            // Redis connectivity  
            .AddRedis(
                config["Redis:Configuration"]!,
                name: "redis",
                tags: new[] { "cache", "important" },
                timeout: TimeSpan.FromSeconds(3))
            // External service (NIC)
            .AddUrlGroup(
                new Uri(config["ExternalServices:NIC:HealthUrl"]!),
                name: "nic-service",
                tags: new[] { "external" },
                timeout: TimeSpan.FromSeconds(10))
            // Disk space
            .AddDiskStorageHealthCheck(
                setup => setup.AddDrive("/", 1024),  // Minimum 1GB free
                name: "disk-space",
                tags: new[] { "infrastructure" })
            // Memory
            .AddProcessAllocatedMemoryHealthCheck(
                maximumMegabytesAllocated: 512,
                name: "memory",
                tags: new[] { "infrastructure" });

        return services;
    }
}

// Health check endpoint mapping
app.MapHealthChecks("/health", new HealthCheckOptions
{
    ResponseWriter = UIResponseWriter.WriteHealthCheckUIResponse,
    Predicate = _ => true  // All checks
});

app.MapHealthChecks("/health/ready", new HealthCheckOptions
{
    Predicate = check => check.Tags.Contains("critical")
});

app.MapHealthChecks("/health/live", new HealthCheckOptions
{
    Predicate = _ => false  // Just responds 200 if process is alive
});
```

### 1.2 Synthetic Test Scripts

```typescript
// synthetic-monitors/critical-journey.ts
// Runs every 5 minutes in production via external monitoring service

import { check } from 'k6';
import http from 'k6/http';

const BASE_URL = __ENV.PRODUCTION_URL;
const TOKEN = __ENV.SYNTHETIC_AUTH_TOKEN;

export const options = {
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    checks: ['rate==1.0']
  }
};

export default function () {
  const headers = { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

  // Step 1: Health check
  const healthRes = http.get(`${BASE_URL}/health`);
  check(healthRes, {
    'health returns 200': (r) => r.status === 200,
    'health check passes': (r) => JSON.parse(r.body).status === 'Healthy'
  });

  // Step 2: List exemptions (read path)
  const listRes = http.get(`${BASE_URL}/api/app/user-request-exemption?MaxResultCount=1`, { headers });
  check(listRes, {
    'list returns 200': (r) => r.status === 200,
    'list has valid structure': (r) => {
      const body = JSON.parse(r.body);
      return body.hasOwnProperty('totalCount') && body.hasOwnProperty('items');
    },
    'list responds under 1s': (r) => r.timings.duration < 1000
  });

  // Step 3: Authentication verification
  const authRes = http.get(`${BASE_URL}/api/app/user-request-exemption`, {
    headers: { Authorization: 'Bearer invalid-token' }
  });
  check(authRes, {
    'invalid token returns 401': (r) => r.status === 401
  });
}
```

### 1.3 Multi-Region Synthetic Checks

```yaml
# Azure Monitor availability tests (or equivalent)
synthetic_tests:
  - name: "API Availability - Primary Region"
    url: "https://api.production.com/health"
    frequency: 30s
    locations:
      - "riyadh-1"
      - "jeddah-1"
    success_criteria:
      status_code: 200
      response_time_ms: 3000
      content_match: '"status":"Healthy"'
    alert:
      severity: P1
      channels: ["sms", "teams", "pagerduty"]
      escalation_after: 5m

  - name: "Critical Journey - Admin Login + List"
    url: "https://api.production.com/api/app/user-request-exemption"
    frequency: 5m
    locations:
      - "riyadh-1"
    auth:
      type: bearer
      token_endpoint: "https://auth.production.com/connect/token"
    success_criteria:
      status_code: 200
      response_time_ms: 5000
    alert:
      severity: P2
      channels: ["teams", "email"]
```

---

## 2. SLO/SLI Framework

### 2.1 SLO Definitions

```yaml
# slo-definitions.yaml
slos:
  - name: "API Availability"
    sli: "successful_requests / total_requests"
    target: 99.9%
    window: 30d
    error_budget: 43.2m  # 30 days × 0.1% = 43.2 minutes
    burn_rate_alerts:
      - severity: P1
        short_window: 5m
        long_window: 1h
        burn_rate: 14.4  # Budget consumed in 2 hours at this rate
      - severity: P2
        short_window: 30m
        long_window: 6h
        burn_rate: 6.0   # Budget consumed in 5 hours

  - name: "API Latency"
    sli: "requests_under_800ms / total_requests"
    target: 99.0%
    window: 30d
    error_budget: 432m   # 30 days × 1% = 432 minutes
    
  - name: "Critical Endpoints Latency"
    sli: "requests_under_500ms / total_requests"
    target: 95.0%
    window: 7d
    endpoints:
      - "GET /api/app/user-request-exemption"
      - "GET /api/app/real-estate-ownership-request"
```

### 2.2 Prometheus Recording Rules

```yaml
# prometheus-rules.yaml
groups:
  - name: slo_recording_rules
    interval: 30s
    rules:
      # Total requests
      - record: sli:http_requests_total:rate5m
        expr: sum(rate(http_server_requests_seconds_count[5m])) by (service)

      # Failed requests (5xx)
      - record: sli:http_errors_total:rate5m
        expr: sum(rate(http_server_requests_seconds_count{status=~"5.."}[5m])) by (service)

      # Availability SLI
      - record: sli:availability:ratio5m
        expr: 1 - (sli:http_errors_total:rate5m / sli:http_requests_total:rate5m)

      # Latency SLI (under 800ms)
      - record: sli:latency_good:ratio5m
        expr: |
          sum(rate(http_server_requests_seconds_bucket{le="0.8"}[5m])) by (service)
          /
          sum(rate(http_server_requests_seconds_count[5m])) by (service)

  - name: slo_alerts
    rules:
      # Multi-window burn rate alert (Page)
      - alert: SLOBurnRateHigh
        expr: |
          sli:http_errors_total:rate5m / (1 - 0.999) > 14.4
          and
          sli:http_errors_total:rate1h / (1 - 0.999) > 14.4
        for: 2m
        labels:
          severity: P1
          slo: availability
        annotations:
          summary: "High error budget burn rate"
          description: "Error budget will be exhausted in < 2 hours at current rate"

      # Multi-window burn rate alert (Ticket)
      - alert: SLOBurnRateMedium
        expr: |
          sli:http_errors_total:rate30m / (1 - 0.999) > 6.0
          and
          sli:http_errors_total:rate6h / (1 - 0.999) > 6.0
        for: 5m
        labels:
          severity: P2
          slo: availability
        annotations:
          summary: "Elevated error budget burn rate"
          description: "Error budget will be exhausted in < 5 hours at current rate"
```

### 2.3 Error Budget Dashboard

```yaml
# Grafana dashboard definition (simplified)
panels:
  - title: "Error Budget Remaining"
    type: gauge
    query: |
      1 - (
        sum(increase(http_server_requests_seconds_count{status=~"5.."}[30d]))
        /
        sum(increase(http_server_requests_seconds_count[30d]))
      ) / (1 - 0.999)
    thresholds:
      - value: 0
        color: red
      - value: 0.25
        color: orange
      - value: 0.5
        color: yellow
      - value: 0.75
        color: green

  - title: "SLO Compliance (30d rolling)"
    type: stat
    query: |
      1 - (
        sum(increase(http_server_requests_seconds_count{status=~"5.."}[30d]))
        /
        sum(increase(http_server_requests_seconds_count[30d]))
      )
    format: percentunit
    thresholds:
      - value: 0.999
        color: green
      - value: 0.995
        color: yellow
      - value: 0
        color: red
```

---

## 3. Distributed Tracing

### 3.1 OpenTelemetry Configuration

```csharp
// Program.cs — OpenTelemetry setup
builder.Services.AddOpenTelemetry()
    .WithTracing(tracing => tracing
        .SetResourceBuilder(ResourceBuilder.CreateDefault()
            .AddService("ehkaam-backendapis", serviceVersion: "1.0.0"))
        .AddAspNetCoreInstrumentation(opts =>
        {
            opts.RecordException = true;
            opts.Filter = context => !context.Request.Path.StartsWithSegments("/health");
        })
        .AddHttpClientInstrumentation()
        .AddEntityFrameworkCoreInstrumentation(opts =>
        {
            opts.SetDbStatementForText = true;
            opts.SetDbStatementForStoredProcedure = true;
        })
        .AddSqlClientInstrumentation(opts =>
        {
            opts.SetDbStatementForText = true;
            opts.RecordException = true;
        })
        .AddSource("ExemptionService", "ReportService")
        .AddOtlpExporter(opts =>
        {
            opts.Endpoint = new Uri(builder.Configuration["OpenTelemetry:Endpoint"]!);
        }))
    .WithMetrics(metrics => metrics
        .AddAspNetCoreInstrumentation()
        .AddHttpClientInstrumentation()
        .AddRuntimeInstrumentation()
        .AddProcessInstrumentation()
        .AddMeter("ExemptionService.Metrics")
        .AddOtlpExporter());
```

### 3.2 Custom Instrumentation

```csharp
// ExemptionAppService.cs — Business operation tracing
public class ExemptionAppService : ApplicationService
{
    private static readonly ActivitySource ActivitySource = new("ExemptionService");
    private static readonly Meter Meter = new("ExemptionService.Metrics");
    private static readonly Counter<long> ExemptionsCreated = Meter.CreateCounter<long>("exemptions.created");
    private static readonly Histogram<double> CreateDuration = Meter.CreateHistogram<double>("exemptions.create.duration.ms");

    public async Task<ExemptionDto> CreateAsync(CreateExemptionInput input)
    {
        using var activity = ActivitySource.StartActivity("CreateExemption");
        activity?.SetTag("nationalId.prefix", input.NationalId[..3] + "***");
        activity?.SetTag("requestCount", input.ExemptedRequestCount);

        var sw = Stopwatch.StartNew();
        try
        {
            var result = await InternalCreateAsync(input);
            
            activity?.SetStatus(ActivityStatusCode.Ok);
            ExemptionsCreated.Add(1, new("status", "success"));
            return result;
        }
        catch (Exception ex)
        {
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            activity?.RecordException(ex);
            ExemptionsCreated.Add(1, new("status", "error"));
            throw;
        }
        finally
        {
            sw.Stop();
            CreateDuration.Record(sw.ElapsedMilliseconds);
        }
    }
}
```

---

## 4. Auto-Rollback on Failure

### 4.1 ArgoCD Progressive Delivery

```yaml
# ArgoCD Rollout with auto-rollback
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: ehkaam-backendapis
  namespace: ehkaam
spec:
  replicas: 3
  strategy:
    canary:
      steps:
        - setWeight: 10          # 10% traffic to new version
        - pause: { duration: 5m } # Observe for 5 minutes
        - analysis:
            templates:
              - templateName: success-rate
              - templateName: latency-check
        - setWeight: 50          # If analysis passes, scale to 50%
        - pause: { duration: 5m }
        - analysis:
            templates:
              - templateName: success-rate
              - templateName: latency-check
        - setWeight: 100         # Full rollout
      
      # Auto-rollback on failure
      rollbackWindow:
        revisions: 3
      
      analysis:
        successfulRunHistoryLimit: 3
        unsuccessfulRunHistoryLimit: 3

---
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: success-rate
spec:
  metrics:
    - name: success-rate
      interval: 30s
      count: 10
      successCondition: result[0] >= 0.99
      failureLimit: 3
      provider:
        prometheus:
          address: http://prometheus.monitoring:9090
          query: |
            sum(rate(http_server_requests_seconds_count{
              status!~"5..",
              app="ehkaam-backendapis",
              rollouts_pod_template_hash="{{args.canary-hash}}"
            }[2m])) /
            sum(rate(http_server_requests_seconds_count{
              app="ehkaam-backendapis",
              rollouts_pod_template_hash="{{args.canary-hash}}"
            }[2m]))

---
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: latency-check
spec:
  metrics:
    - name: p95-latency
      interval: 30s
      count: 10
      successCondition: result[0] < 0.8
      failureLimit: 3
      provider:
        prometheus:
          address: http://prometheus.monitoring:9090
          query: |
            histogram_quantile(0.95,
              sum(rate(http_server_requests_seconds_bucket{
                app="ehkaam-backendapis",
                rollouts_pod_template_hash="{{args.canary-hash}}"
              }[2m])) by (le)
            )
```

---

## 5. Alerting Strategy

### 5.1 Alert Priority Matrix

| Severity | Condition | Response Time | Channel | Example |
|----------|-----------|---------------|---------|---------|
| P1 (Critical) | Service down, data loss risk | 5 min | SMS + PagerDuty + Teams | All pods CrashLoopBackOff |
| P2 (High) | Degraded, SLO burning fast | 30 min | Teams + Email | Error rate > 5% for 5 min |
| P3 (Medium) | Anomaly, needs investigation | 4 hours | Teams | p95 latency doubled |
| P4 (Low) | Informational, trend | Next business day | Email | Error budget < 50% |

### 5.2 Alert Definitions

```yaml
# alertmanager-config.yaml
groups:
  - name: production_alerts
    rules:
      - alert: ServiceDown
        expr: up{namespace="ehkaam"} == 0
        for: 1m
        labels:
          severity: P1
        annotations:
          summary: "Service {{ $labels.service }} is DOWN"
          runbook: "https://wiki.internal/runbooks/service-down"

      - alert: HighErrorRate
        expr: |
          sum(rate(http_server_requests_seconds_count{status=~"5..", namespace="ehkaam"}[5m]))
          / sum(rate(http_server_requests_seconds_count{namespace="ehkaam"}[5m])) > 0.05
        for: 5m
        labels:
          severity: P2
        annotations:
          summary: "Error rate > 5% for 5 minutes"

      - alert: LatencySpike
        expr: |
          histogram_quantile(0.95,
            sum(rate(http_server_requests_seconds_bucket{namespace="ehkaam"}[5m])) by (le)
          ) > 2.0
        for: 5m
        labels:
          severity: P3
        annotations:
          summary: "p95 latency > 2 seconds"

      - alert: PodCrashLooping
        expr: |
          rate(kube_pod_container_status_restarts_total{namespace="ehkaam"}[15m]) > 0
        for: 5m
        labels:
          severity: P2
        annotations:
          summary: "Pod {{ $labels.pod }} is crash-looping"

      - alert: MemoryUsageHigh
        expr: |
          container_memory_usage_bytes{namespace="ehkaam"}
          / container_spec_memory_limit_bytes{namespace="ehkaam"} > 0.85
        for: 10m
        labels:
          severity: P3
        annotations:
          summary: "Memory usage > 85% of limit"
```

---

## 6. Deployment Verification

### 6.1 Post-Deploy Smoke Test

```yaml
# Automatic post-deploy verification in CD pipeline
- job: PostDeployVerification
  displayName: 'Post-Deploy Smoke Test'
  dependsOn: Deploy
  steps:
  - bash: |
      # Wait for rollout
      kubectl rollout status deployment/ehkaam-backendapis -n ehkaam --timeout=120s
      
      # Run synthetic checks
      k6 run synthetic-monitors/critical-journey.ts \
        --env PRODUCTION_URL=$(PRODUCTION_URL) \
        --env SYNTHETIC_AUTH_TOKEN=$(SYNTHETIC_TOKEN)
      
      if [ $? -ne 0 ]; then
        echo "##[error]Post-deploy verification FAILED"
        echo "##[warning]Initiating rollback..."
        kubectl rollout undo deployment/ehkaam-backendapis -n ehkaam
        exit 1
      fi
      
      echo "✅ Post-deploy verification passed"
    displayName: 'Verify Deployment'
```

---

## 7. Metrics Summary

| What We Monitor | Tool | Alert Threshold | Check Frequency |
|----------------|------|-----------------|-----------------|
| Service availability | Prometheus + Synthetic | < 99.9% over 5 min | 30s |
| API latency (p95) | Prometheus | > 800ms for 5 min | 30s |
| Error rate | Prometheus | > 1% for 5 min | 30s |
| Error budget burn rate | Prometheus | 14.4x for P1, 6x for P2 | 30s |
| Pod health | Kubernetes | CrashLoopBackOff | Real-time |
| Memory usage | cAdvisor | > 85% for 10 min | 15s |
| External deps | Synthetic monitors | Timeout or error | 1 min |
| SSL certificate | External monitor | < 14 days to expiry | Daily |

---

*Previous: [08-CHAOS-RESILIENCE-TESTING.md](08-CHAOS-RESILIENCE-TESTING.md) · Next: [10-TEST-DATA-MANAGEMENT.md](10-TEST-DATA-MANAGEMENT.md)*
