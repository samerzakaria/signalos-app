# 08 — Chaos & Resilience Testing

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

Chaos engineering **proactively injects failures** into the system to verify it handles them gracefully. Instead of waiting for production incidents to reveal weaknesses, we deliberately break things in controlled environments to prove the system recovers correctly.

**What this layer kills:**
- "The app crashed when the database was temporarily unavailable" → Proven DB failover
- "One pod dying caused cascading failures" → Pod kill tests prove recovery
- "Network partition between services caused data corruption" → Network chaos validated
- "Nobody knew the system would do X when Y fails" → Documented failure modes
- "Our retry logic actually makes things worse under load" → Thundering herd prevention verified

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CHAOS TESTING FRAMEWORK                                    │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  HYPOTHESIS                                                            │  │
│  │  "The system can handle [failure] and recover within [time]"          │  │
│  └───────────────────────────────────┬───────────────────────────────────┘  │
│                                      │                                       │
│  ┌───────────────────────────────────▼───────────────────────────────────┐  │
│  │  EXPERIMENT                                                            │  │
│  │                                                                        │  │
│  │  1. Steady State: Define normal behavior metrics                       │  │
│  │  2. Inject: Apply failure condition                                    │  │
│  │  3. Observe: Measure system behavior during failure                    │  │
│  │  4. Recover: Remove failure condition                                  │  │
│  │  5. Verify: Confirm system returns to steady state                    │  │
│  └───────────────────────────────────┬───────────────────────────────────┘  │
│                                      │                                       │
│  ┌───────────────────────────────────▼───────────────────────────────────┐  │
│  │  FAILURE INJECTION TYPES                                               │  │
│  │                                                                        │  │
│  │  • Pod Kill: Terminate random/specific pods                            │  │
│  │  • Network: Partition, latency, packet loss, DNS failure              │  │
│  │  • Resource: CPU stress, memory pressure, disk fill                   │  │
│  │  • Dependency: Database down, Redis down, external API timeout        │  │
│  │  • Application: Exception injection, slow responses                    │  │
│  └───────────────────────────────────┬───────────────────────────────────┘  │
│                                      │                                       │
│  ┌───────────────────────────────────▼───────────────────────────────────┐  │
│  │  BLAST RADIUS CONTROL                                                  │  │
│  │                                                                        │  │
│  │  • Staging/Test only (never production without approval)               │  │
│  │  • Time-limited (auto-revert after duration)                          │  │
│  │  • Scope-limited (single pod, single namespace)                       │  │
│  │  • Abort conditions (if error rate > 50%, stop immediately)           │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  Gate: All hypotheses pass | Recovery < SLA | No data loss                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Chaos Testing with LitmusChaos (Kubernetes)

### 1.1 Installation

```yaml
# litmus-operator.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: litmus
---
# Install via Helm
# helm repo add litmuschaos https://litmuschaos.github.io/litmus-helm/
# helm install chaos litmuschaos/litmus --namespace=litmus
```

### 1.2 Experiment: Pod Kill

```yaml
# experiments/pod-kill.yaml
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: backend-pod-kill
  namespace: ehkaam
spec:
  engineState: active
  appinfo:
    appns: ehkaam
    applabel: app=ehkaam-backendapis
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-delete
      spec:
        components:
          env:
            - name: TOTAL_CHAOS_DURATION
              value: '60'           # 60 seconds of chaos
            - name: CHAOS_INTERVAL
              value: '10'           # Kill a pod every 10s
            - name: FORCE
              value: 'true'         # Force kill (SIGKILL)
            - name: PODS_AFFECTED_PERC
              value: '50'           # Kill 50% of pods
        probe:
          - name: api-availability
            type: httpProbe
            mode: Continuous
            httpProbe/inputs:
              url: http://ehkaam-backendapis.ehkaam.svc.cluster.local/health
              method:
                get:
                  criteria: ==
                  responseCode: '200'
            runProperties:
              probeTimeout: 5s
              retry: 3
              interval: 5s
              probePollingInterval: 2s
---
# Expected result: API remains available because Kubernetes reschedules pods
# Success criteria: Health check probe passes throughout chaos duration
```

### 1.3 Experiment: Network Latency

```yaml
# experiments/network-latency.yaml
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: backend-network-latency
  namespace: ehkaam
spec:
  engineState: active
  appinfo:
    appns: ehkaam
    applabel: app=ehkaam-backendapis
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-network-latency
      spec:
        components:
          env:
            - name: TOTAL_CHAOS_DURATION
              value: '120'
            - name: NETWORK_LATENCY
              value: '2000'         # Add 2000ms latency
            - name: DESTINATION_IPS
              value: ''             # All outbound traffic
            - name: DESTINATION_HOSTS
              value: ''
            - name: NETWORK_INTERFACE
              value: eth0
            - name: JITTER
              value: '500'          # ±500ms jitter
        probe:
          - name: api-response-time
            type: httpProbe
            mode: Continuous
            httpProbe/inputs:
              url: http://ehkaam-backendapis.ehkaam.svc.cluster.local/api/app/user-request-exemption?MaxResultCount=1
              method:
                get:
                  criteria: <=
                  responseCode: '200'
              responseTimeout: 10000  # 10s timeout (must handle 2s latency)
            runProperties:
              probeTimeout: 15s
              retry: 2
              interval: 10s
---
# Hypothesis: API responds within 10s even with 2s network latency
# Validates: timeout configuration, circuit breaker, retry policies
```

### 1.4 Experiment: Database Unavailability

```yaml
# experiments/db-unavailable.yaml
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: db-connection-chaos
  namespace: ehkaam
spec:
  engineState: active
  appinfo:
    appns: ehkaam
    applabel: app=ehkaam-backendapis
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-network-loss
      spec:
        components:
          env:
            - name: TOTAL_CHAOS_DURATION
              value: '30'           # 30 seconds DB unavailability
            - name: NETWORK_PACKET_LOSS_PERCENTAGE
              value: '100'          # 100% packet loss
            - name: DESTINATION_IPS
              value: '10.0.0.50'    # Database IP
            - name: NETWORK_INTERFACE
              value: eth0
        probe:
          - name: graceful-degradation
            type: httpProbe
            mode: Continuous
            httpProbe/inputs:
              url: http://ehkaam-backendapis.ehkaam.svc.cluster.local/api/app/user-request-exemption
              method:
                get:
                  criteria: '!='
                  responseCode: '500'  # Must NOT return 500
            runProperties:
              probeTimeout: 10s
              retry: 2
              interval: 5s
          - name: recovery-check
            type: httpProbe
            mode: EOT  # End of Test
            httpProbe/inputs:
              url: http://ehkaam-backendapis.ehkaam.svc.cluster.local/api/app/user-request-exemption
              method:
                get:
                  criteria: '=='
                  responseCode: '200'
            runProperties:
              probeTimeout: 30s
              retry: 5
              interval: 5s
---
# Hypothesis: When DB is unreachable for 30s:
# 1. API returns graceful error (503) not crash (500)
# 2. No data corruption
# 3. After DB recovers, API returns to normal within 30s
```

### 1.5 Experiment: Memory Pressure

```yaml
# experiments/memory-stress.yaml
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: backend-memory-stress
  namespace: ehkaam
spec:
  engineState: active
  appinfo:
    appns: ehkaam
    applabel: app=ehkaam-backendapis
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-memory-hog
      spec:
        components:
          env:
            - name: TOTAL_CHAOS_DURATION
              value: '120'
            - name: MEMORY_CONSUMPTION
              value: '500'          # Consume 500Mi (pod limit is 700Mi)
            - name: NUMBER_OF_WORKERS
              value: '1'
        probe:
          - name: oom-check
            type: cmdProbe
            mode: EOT
            cmdProbe/inputs:
              command: kubectl get pods -n ehkaam -l app=ehkaam-backendapis -o json | python3 -c "import json,sys; pods=json.load(sys.stdin)['items']; oom=[p for p in pods if any(cs.get('lastState',{}).get('terminated',{}).get('reason')=='OOMKilled' for cs in p['status'].get('containerStatuses',[]))]; sys.exit(1 if oom else 0)"
              source: inline
              comparator:
                type: int
                criteria: '=='
                value: '0'
            runProperties:
              probeTimeout: 10s
              retry: 1
---
# Hypothesis: Under memory pressure (500Mi consumed, limit 700Mi):
# 1. Pod does NOT get OOMKilled
# 2. GC handles memory pressure gracefully
# 3. Response times increase but service remains available
```

---

## 2. Application-Level Resilience Tests

### 2.1 Circuit Breaker Verification

```csharp
// ResilienceTests.cs
public class CircuitBreakerTests : ApiTestBase
{
    private readonly WireMockFixture _wireMock;

    public CircuitBreakerTests(TestWebApplicationFactory factory) : base(factory)
    {
        _wireMock = factory.Services.GetRequiredService<WireMockFixture>();
    }

    [Fact]
    public async Task ExternalService_FailsMultipleTimes_CircuitOpens()
    {
        // Configure external service to always fail
        _wireMock.SimulateServerError("/api/nic/citizen/*");
        await AuthenticateAs("Admin");

        // Make enough requests to trip the circuit breaker
        var responses = new List<HttpStatusCode>();
        for (int i = 0; i < 10; i++)
        {
            var response = await Client.GetAsync("/api/app/user-info/1234567890");
            responses.Add(response.StatusCode);
        }

        // First few should be 502/503 (upstream failure)
        // After circuit opens, should get fast failure (no upstream call)
        var lastFew = responses.Skip(5).ToList();
        lastFew.Should().AllBeEquivalentTo(HttpStatusCode.ServiceUnavailable,
            "circuit breaker should open and return fast failure");
    }

    [Fact]
    public async Task CircuitBreaker_RecoverAfterTimeout()
    {
        // Start with failure
        _wireMock.SimulateServerError("/api/nic/citizen/*");
        await AuthenticateAs("Admin");

        // Trip the breaker
        for (int i = 0; i < 10; i++)
        {
            await Client.GetAsync("/api/app/user-info/1234567890");
        }

        // Fix the service
        _wireMock.Server.Reset();
        _wireMock.Server.Given(Request.Create().WithPath("/api/nic/citizen/*").UsingGet())
            .RespondWith(Response.Create().WithStatusCode(200)
                .WithBodyAsJson(new { nationalId = "1234567890", fullNameAr = "تست" }));

        // Wait for half-open state (depends on circuit breaker config)
        await Task.Delay(TimeSpan.FromSeconds(5));

        // Should recover
        var response = await Client.GetAsync("/api/app/user-info/1234567890");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task RetryPolicy_RetriesTransientFailures()
    {
        var callCount = 0;
        _wireMock.Server.Given(Request.Create().WithPath("/api/nic/citizen/*").UsingGet())
            .RespondWith(Response.Create()
                .WithCallback(_ =>
                {
                    callCount++;
                    return callCount < 3
                        ? new ResponseMessage { StatusCode = 500 }
                        : new ResponseMessage { StatusCode = 200, BodyData = new BodyData() };
                }));

        await AuthenticateAs("Admin");
        var response = await Client.GetAsync("/api/app/user-info/1234567890");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        callCount.Should().Be(3, "should have retried 2 times before succeeding");
    }

    [Fact]
    public async Task BulkheadIsolation_FailureInOneServiceDoesntAffectOthers()
    {
        // Make NIC service fail
        _wireMock.SimulateTimeout("/api/nic/citizen/*");
        await AuthenticateAs("Admin");

        // NIC-dependent endpoint should fail
        var nicResponse = await Client.GetAsync("/api/app/user-info/1234567890");
        nicResponse.StatusCode.Should().NotBe(HttpStatusCode.OK);

        // Non-NIC endpoint should still work
        var exemptionResponse = await Client.GetAsync("/api/app/user-request-exemption");
        exemptionResponse.StatusCode.Should().Be(HttpStatusCode.OK,
            "failure in NIC service should not affect exemption queries");
    }
}
```

### 2.2 Graceful Degradation Tests

```csharp
public class GracefulDegradationTests : ApiTestBase
{
    public GracefulDegradationTests(TestWebApplicationFactory factory) : base(factory) { }

    [Fact]
    public async Task CacheDown_FallsBackToDatabase()
    {
        // Simulate Redis failure
        var redis = Factory.Services.GetRequiredService<IConnectionMultiplexer>();
        // Disconnect Redis
        await redis.GetDatabase().ExecuteAsync("CLIENT", "KILL", "ID", "self");

        await AuthenticateAs("Admin");
        var response = await Client.GetAsync("/api/app/user-request-exemption");

        // Should still work (with slower response from DB)
        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task HealthCheck_ReportsPartialDegradation()
    {
        // Kill Redis connection
        // ...

        var response = await Client.GetAsync("/health");
        var body = await response.Content.ReadAsStringAsync();

        // Health check should report degraded, not unhealthy
        response.StatusCode.Should().Be(HttpStatusCode.OK)
            .Or.Be((HttpStatusCode)218); // 218 = partial failure

        body.Should().Contain("degraded").Or.Contain("Degraded");
    }
}
```

---

## 3. Chaos Experiment Registry

### 3.1 Experiment Catalog

| ID | Hypothesis | Injection | Duration | Blast Radius | Recovery SLA |
|----|-----------|-----------|----------|-------------|--------------|
| CH-001 | API survives pod kill | Kill 50% pods | 60s | Namespace | 30s |
| CH-002 | API handles DB failover | Network partition to DB | 30s | Single pod | 60s |
| CH-003 | API handles high latency | 2s network delay | 120s | All pods | Immediate |
| CH-004 | No OOM under memory pressure | Consume 70% memory limit | 120s | Single pod | 30s |
| CH-005 | Circuit breaker protects system | External service 500s | 60s | Service | 5s open, 30s recover |
| CH-006 | Retry handles transient errors | 50% packet loss | 60s | Single pod | 10s |
| CH-007 | No data loss during crash | SIGKILL during write | 30s | Single pod | Transaction rollback |
| CH-008 | DNS failure handled | DNS poisoning | 30s | Single pod | 15s |
| CH-009 | CPU saturation degradation | 90% CPU stress | 120s | Single pod | 30s |
| CH-010 | Disk full handling | Fill ephemeral disk | 60s | Single pod | Pod restart |

### 3.2 Experiment Schedule

| Experiment Set | Frequency | Environment | Approval Required |
|---------------|-----------|-------------|-------------------|
| Pod Kill (CH-001) | Every deploy | Test | No |
| Network Chaos (CH-002, 003, 006, 008) | Nightly | Test | No |
| Resource Chaos (CH-004, 009, 010) | Weekly | Test | No |
| All experiments | Before production release | Staging | Yes |
| Game Day (all at once) | Quarterly | Staging | Team lead |

---

## 4. Steady State Metrics

Define what "normal" looks like before injecting chaos:

```yaml
# steady-state-definition.yaml
steady_state:
  metrics:
    - name: api_availability
      query: "avg(up{namespace='ehkaam'}) by (service)"
      threshold: ">= 0.99"  # 99% availability
      
    - name: error_rate
      query: "sum(rate(http_server_requests_seconds_count{status=~'5..'}[5m])) / sum(rate(http_server_requests_seconds_count[5m]))"
      threshold: "< 0.01"   # Less than 1% errors
      
    - name: p95_latency
      query: "histogram_quantile(0.95, sum(rate(http_server_requests_seconds_bucket[5m])) by (le))"
      threshold: "< 0.8"    # Less than 800ms
      
    - name: active_pods
      query: "count(kube_pod_status_ready{namespace='ehkaam', condition='true'})"
      threshold: ">= 2"     # At least 2 healthy pods

  verification:
    # Check these again AFTER chaos ends + recovery window
    recovery_window: "60s"
    all_metrics_must_pass: true
```

---

## 5. Pipeline Integration

```yaml
# Chaos testing as pipeline gate
- stage: ChaosTest
  displayName: 'Resilience Verification'
  dependsOn: IntegrationTest
  condition: and(succeeded(), eq(variables['runChaos'], 'true'))
  jobs:
  - job: chaos_pod_kill
    displayName: 'Pod Kill Experiment'
    steps:
    - bash: |
        # Apply chaos experiment
        kubectl apply -f experiments/pod-kill.yaml -n ehkaam
        
        # Wait for experiment to complete
        kubectl wait --for=condition=Complete \
          chaosengine/backend-pod-kill -n ehkaam \
          --timeout=300s
        
        # Check result
        VERDICT=$(kubectl get chaosresult -n ehkaam -o jsonpath='{.items[0].status.experimentStatus.verdict}')
        
        if [ "$VERDICT" != "Pass" ]; then
          echo "##[error]Chaos experiment FAILED: Pod kill test"
          kubectl get chaosresult -n ehkaam -o yaml
          exit 1
        fi
        
        echo "✅ Pod kill experiment passed"
      displayName: 'Run Pod Kill Chaos'

    - bash: |
        # Verify recovery
        sleep 60
        
        # Check all pods are running
        READY=$(kubectl get deployment ehkaam-backendapis -n ehkaam -o jsonpath='{.status.readyReplicas}')
        DESIRED=$(kubectl get deployment ehkaam-backendapis -n ehkaam -o jsonpath='{.spec.replicas}')
        
        if [ "$READY" != "$DESIRED" ]; then
          echo "##[error]Recovery failed: $READY/$DESIRED pods ready"
          exit 1
        fi
        
        # Check API is responding
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
          -H "Authorization: Bearer $TOKEN" \
          http://ehkaam-backendapis.ehkaam.svc.cluster.local/api/app/user-request-exemption)
        
        if [ "$HTTP_CODE" != "200" ]; then
          echo "##[error]API not recovered: HTTP $HTTP_CODE"
          exit 1
        fi
        
        echo "✅ System recovered successfully"
      displayName: 'Verify Recovery'
```

---

## 6. Game Day Playbook

For quarterly comprehensive chaos sessions:

```markdown
## Game Day Checklist

### Preparation (1 day before)
- [ ] Notify all team members
- [ ] Verify staging environment matches production config
- [ ] Confirm monitoring dashboards are accessible
- [ ] Review abort procedures
- [ ] Ensure rollback plan is ready

### Execution
- [ ] Start recording (screen capture)
- [ ] Verify steady state metrics
- [ ] Execute experiments in order (least to most disruptive)
- [ ] Document observations in real-time
- [ ] If any experiment causes unexpected behavior → ABORT immediately

### Post-Game Day
- [ ] Write Game Day report
- [ ] Log new hypotheses discovered
- [ ] Create tickets for any failures
- [ ] Update runbooks based on findings
- [ ] Share results with team
```

---

## 7. Abort Conditions

**Immediately stop ALL chaos if:**
- Error rate exceeds 50% for more than 30 seconds
- Data corruption detected (checksum mismatch)
- System cannot recover after chaos duration ends + 5 minutes
- Unintended services affected (blast radius breach)
- Any production traffic is inadvertently affected

```bash
# Emergency abort command
kubectl delete chaosengine --all -n ehkaam
kubectl rollout restart deployment/ehkaam-backendapis -n ehkaam
```

---

*Previous: [07-SECURITY-TESTING.md](07-SECURITY-TESTING.md) · Next: [09-PRODUCTION-MONITORING.md](09-PRODUCTION-MONITORING.md)*
