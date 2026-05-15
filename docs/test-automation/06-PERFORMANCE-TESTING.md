# 06 — Performance Testing

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

Performance testing validates that the system meets **speed, throughput, and stability** requirements under realistic and extreme loads — before users experience degradation. It answers: "Will this survive production traffic?" without waiting for production to tell you.

**What this layer kills:**
- "The page is slow after we deployed" → Load test caught the N+1 query in CI
- "System crashed under Black Friday traffic" → Capacity test proved limits weeks before
- "Memory leak after 3 days" → Soak test detected it in 4 hours
- "API response went from 200ms to 3s" → Regression detected against SLA baseline
- "We think it'll handle 1000 users but never tested" → Proven, measured, documented

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PERFORMANCE TESTING LAYERS                                 │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ LAYER 1: Unit Benchmarks (per-commit, < 10s)                          │  │
│  │                                                                        │  │
│  │ • BenchmarkDotNet for critical algorithms                              │  │
│  │ • Assert: no regression > 20% from baseline                           │  │
│  │ • Serialization, hashing, query building                              │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ LAYER 2: API Load Tests (per-PR, < 5 min)                             │  │
│  │                                                                        │  │
│  │ • k6 scripts against test environment                                  │  │
│  │ • Scenarios: smoke, average load, stress                               │  │
│  │ • Assert: p95 < SLA, error rate < 1%, throughput > baseline           │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ LAYER 3: Capacity & Soak Tests (nightly/weekly, 1-8 hours)            │  │
│  │                                                                        │  │
│  │ • Sustained load for memory leak detection                             │  │
│  │ • Ramp-up to breaking point (find ceiling)                            │  │
│  │ • Assert: no degradation over time, no OOM                            │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ LAYER 4: Frontend Performance (per-PR, Lighthouse CI)                  │  │
│  │                                                                        │  │
│  │ • Core Web Vitals: LCP < 2.5s, FID < 100ms, CLS < 0.1               │  │
│  │ • Bundle size budget: main < 250KB gzipped                            │  │
│  │ • Time to interactive < 3s                                            │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  Gate: p95 < SLA | Error rate < 1% | No memory leak | Core Web Vitals pass │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. API Load Testing (k6)

### 1.1 Project Structure

```
performance/
├── k6/
│   ├── config/
│   │   ├── thresholds.json        # SLA definitions
│   │   └── environments.json      # Target URLs
│   ├── scenarios/
│   │   ├── smoke.js               # Minimal validation (1 VU, 30s)
│   │   ├── average-load.js        # Normal traffic (50 VU, 5 min)
│   │   ├── stress.js              # Peak traffic (200 VU, 10 min)
│   │   ├── spike.js               # Sudden burst (0→500 VU instant)
│   │   ├── soak.js                # Endurance (50 VU, 4 hours)
│   │   └── breakpoint.js          # Find limit (ramp until failure)
│   ├── helpers/
│   │   ├── auth.js                # JWT token generation
│   │   ├── data-generators.js     # Random test data
│   │   └── checks.js              # Reusable assertions
│   └── results/
│       └── .gitkeep
└── lighthouse/
    ├── lighthouserc.js
    └── budgets.json
```

### 1.2 SLA Thresholds

```json
// performance/k6/config/thresholds.json
{
  "api": {
    "http_req_duration": {
      "p50": 200,
      "p90": 500,
      "p95": 800,
      "p99": 2000,
      "max": 5000
    },
    "http_req_failed": {
      "rate": 0.01
    },
    "http_reqs": {
      "rate_min": 100
    },
    "iteration_duration": {
      "p95": 3000
    }
  },
  "endpoints": {
    "GET /api/app/user-request-exemption": {
      "p95": 500,
      "p99": 1000
    },
    "POST /api/app/user-request-exemption": {
      "p95": 800,
      "p99": 1500
    },
    "GET /api/app/real-estate-ownership-request": {
      "p95": 600,
      "p99": 1200
    }
  }
}
```

### 1.3 Load Test Scripts

```javascript
// performance/k6/scenarios/average-load.js
import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { generateToken } from '../helpers/auth.js';
import { randomNationalId, randomCount } from '../helpers/data-generators.js';

// Custom metrics
const errorRate = new Rate('errors');
const exemptionDuration = new Trend('exemption_api_duration');
const requestsCreated = new Counter('exemptions_created');

export const options = {
  scenarios: {
    average_load: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '1m', target: 20 },   // Ramp up
        { duration: '3m', target: 50 },   // Hold average
        { duration: '1m', target: 0 }     // Ramp down
      ],
      gracefulRampDown: '30s'
    }
  },
  
  thresholds: {
    http_req_duration: ['p(95)<800', 'p(99)<2000'],
    http_req_failed: ['rate<0.01'],
    errors: ['rate<0.01'],
    'exemption_api_duration': ['p(95)<500'],
    http_reqs: ['rate>50']
  },

  // Cloud output (optional)
  ext: {
    loadimpact: {
      projectID: __ENV.K6_PROJECT_ID,
      name: 'Average Load Test'
    }
  }
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:5000';
const TOKEN = generateToken('Admin');

const headers = {
  'Content-Type': 'application/json',
  'Authorization': `Bearer ${TOKEN}`
};

export default function () {
  group('List Exemptions', () => {
    const listRes = http.get(
      `${BASE_URL}/api/app/user-request-exemption?MaxResultCount=10&SkipCount=0`,
      { headers, tags: { endpoint: 'GET_exemptions' } }
    );

    const listCheck = check(listRes, {
      'list returns 200': (r) => r.status === 200,
      'list has items array': (r) => JSON.parse(r.body).items !== undefined,
      'list responds under 500ms': (r) => r.timings.duration < 500
    });

    errorRate.add(!listCheck);
    exemptionDuration.add(listRes.timings.duration);
  });

  sleep(1); // Think time between actions

  group('Create Exemption', () => {
    const payload = JSON.stringify({
      nationalId: randomNationalId(),
      exemptedRequestCount: randomCount(1, 15),
      notes: `k6 load test ${Date.now()}`
    });

    const createRes = http.post(
      `${BASE_URL}/api/app/user-request-exemption`,
      payload,
      { headers, tags: { endpoint: 'POST_exemption' } }
    );

    const createCheck = check(createRes, {
      'create returns 200': (r) => r.status === 200,
      'create has id': (r) => JSON.parse(r.body).id !== undefined,
      'create responds under 800ms': (r) => r.timings.duration < 800
    });

    if (createRes.status === 200) {
      requestsCreated.add(1);
      
      // Cleanup: delete what we created
      const created = JSON.parse(createRes.body);
      http.del(`${BASE_URL}/api/app/user-request-exemption/${created.id}`, null, { headers });
    }

    errorRate.add(!createCheck);
  });

  sleep(Math.random() * 3 + 1); // Random think time 1-4s
}

export function handleSummary(data) {
  return {
    'results/average-load-summary.json': JSON.stringify(data, null, 2),
    stdout: textSummary(data, { indent: ' ', enableColors: true })
  };
}
```

### 1.4 Stress Test

```javascript
// performance/k6/scenarios/stress.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { generateToken } from '../helpers/auth.js';

export const options = {
  scenarios: {
    stress: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: 50 },    // Normal load
        { duration: '2m', target: 100 },   // Above normal
        { duration: '2m', target: 200 },   // Peak load
        { duration: '2m', target: 300 },   // Beyond peak
        { duration: '2m', target: 0 }      // Recovery
      ]
    }
  },
  
  thresholds: {
    http_req_duration: ['p(95)<2000'],     // Relaxed for stress
    http_req_failed: ['rate<0.05'],         // Allow 5% errors under stress
    http_reqs: ['rate>20']                  // At least handling some requests
  }
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:5000';
const TOKEN = generateToken('Admin');

export default function () {
  const headers = { Authorization: `Bearer ${TOKEN}` };
  
  const res = http.get(
    `${BASE_URL}/api/app/user-request-exemption?MaxResultCount=10`,
    { headers }
  );
  
  check(res, {
    'status is not 500+': (r) => r.status < 500,
    'responds within 5s': (r) => r.timings.duration < 5000
  });
  
  sleep(0.5);
}
```

### 1.5 Breakpoint Test (Find the Ceiling)

```javascript
// performance/k6/scenarios/breakpoint.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { generateToken } from '../helpers/auth.js';

export const options = {
  scenarios: {
    breakpoint: {
      executor: 'ramping-arrival-rate',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: 500,
      maxVUs: 1000,
      stages: [
        { duration: '2m', target: 50 },
        { duration: '2m', target: 100 },
        { duration: '2m', target: 200 },
        { duration: '2m', target: 400 },
        { duration: '2m', target: 600 },
        { duration: '2m', target: 800 },
        { duration: '2m', target: 1000 }
      ]
    }
  },
  
  thresholds: {
    // These will fail — that's the point. We find where they fail.
    http_req_duration: ['p(95)<3000'],
    http_req_failed: ['rate<0.10']
  }
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:5000';
const TOKEN = generateToken('Admin');

export default function () {
  const res = http.get(`${BASE_URL}/api/app/user-request-exemption?MaxResultCount=5`, {
    headers: { Authorization: `Bearer ${TOKEN}` }
  });
  
  check(res, {
    'not server error': (r) => r.status < 500,
    'under 3s': (r) => r.timings.duration < 3000
  });
}

// Output shows where the system breaks:
// At 200 req/s: p95=400ms ✓
// At 400 req/s: p95=900ms ✓
// At 600 req/s: p95=2100ms ✓
// At 800 req/s: p95=4500ms ✗ ← CEILING FOUND
```

### 1.6 Soak Test (Memory Leak Detection)

```javascript
// performance/k6/scenarios/soak.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';
import { generateToken } from '../helpers/auth.js';

const responseTimeTrend = new Trend('response_time_over_time');

export const options = {
  scenarios: {
    soak: {
      executor: 'constant-vus',
      vus: 50,
      duration: '4h'
    }
  },
  
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    http_req_failed: ['rate<0.01'],
    // Key soak metric: response time should not increase over time
    'response_time_over_time': ['p(95)<1000']
  }
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:5000';
const TOKEN = generateToken('Admin');

export default function () {
  const res = http.get(`${BASE_URL}/api/app/user-request-exemption?MaxResultCount=10`, {
    headers: { Authorization: `Bearer ${TOKEN}` }
  });
  
  responseTimeTrend.add(res.timings.duration);
  
  check(res, {
    'no degradation': (r) => r.status === 200 && r.timings.duration < 1000
  });
  
  sleep(2);
}

// Post-soak analysis: compare first-hour p95 vs last-hour p95
// If last hour > first hour * 1.5 → MEMORY LEAK suspected
```

---

## 2. Unit Benchmarks (BenchmarkDotNet)

```csharp
// Benchmarks/SerializationBenchmarks.cs
[MemoryDiagnoser]
[SimpleJob(RuntimeMoniker.Net80)]
public class SerializationBenchmarks
{
    private readonly ExemptionDto _sampleDto;
    private readonly string _sampleJson;

    public SerializationBenchmarks()
    {
        _sampleDto = new ExemptionDto
        {
            Id = Guid.NewGuid(),
            NationalId = "1234567890",
            ExemptedRequestCount = 5,
            SubmittedRequests = 2,
            CreationTime = DateTime.UtcNow
        };
        _sampleJson = JsonSerializer.Serialize(_sampleDto);
    }

    [Benchmark(Baseline = true)]
    public string Serialize_SystemTextJson()
        => JsonSerializer.Serialize(_sampleDto);

    [Benchmark]
    public ExemptionDto Deserialize_SystemTextJson()
        => JsonSerializer.Deserialize<ExemptionDto>(_sampleJson)!;

    [Benchmark]
    public PagedResultDto<ExemptionDto> Deserialize_PagedResult()
    {
        var json = JsonSerializer.Serialize(new PagedResultDto<ExemptionDto>
        {
            TotalCount = 1000,
            Items = Enumerable.Range(0, 50).Select(_ => _sampleDto).ToList()
        });
        return JsonSerializer.Deserialize<PagedResultDto<ExemptionDto>>(json)!;
    }
}

// Benchmarks/QueryBenchmarks.cs
[MemoryDiagnoser]
public class QueryBenchmarks
{
    private MainDbContext _db = null!;

    [GlobalSetup]
    public void Setup()
    {
        // Setup in-memory or Testcontainer database with 10,000 records
        var options = new DbContextOptionsBuilder<MainDbContext>()
            .UseInMemoryDatabase("benchmarks")
            .Options;
        _db = new MainDbContext(options);

        _db.UserRequestExemptions.AddRange(
            Enumerable.Range(0, 10_000).Select(i => new UserRequestExemption
            {
                NationalId = $"{i:D10}",
                ExemptedRequestCount = (i % 15) + 1
            }));
        _db.SaveChanges();
    }

    [Benchmark]
    public async Task<List<UserRequestExemption>> FilterAndPaginate()
    {
        return await _db.UserRequestExemptions
            .Where(e => !e.IsDeleted)
            .OrderByDescending(e => e.CreationTime)
            .Skip(0).Take(50)
            .ToListAsync();
    }

    [Benchmark]
    public async Task<int> CountActive()
    {
        return await _db.UserRequestExemptions
            .Where(e => !e.IsDeleted)
            .CountAsync();
    }
}
```

---

## 3. Frontend Performance (Lighthouse CI)

### 3.1 Configuration

```javascript
// performance/lighthouse/lighthouserc.js
module.exports = {
  ci: {
    collect: {
      url: [
        'http://localhost:4200/login',
        'http://localhost:4200/dashboard',
        'http://localhost:4200/admin/exemptions',
        'http://localhost:4200/admin/exemptions/create'
      ],
      numberOfRuns: 3,
      settings: {
        preset: 'desktop',
        chromeFlags: '--no-sandbox --disable-gpu',
        locale: 'ar'
      },
      puppeteerScript: './lighthouse-auth.js' // Handle login
    },
    
    assert: {
      assertions: {
        // Core Web Vitals
        'largest-contentful-paint': ['error', { maxNumericValue: 2500 }],
        'first-contentful-paint': ['error', { maxNumericValue: 1800 }],
        'cumulative-layout-shift': ['error', { maxNumericValue: 0.1 }],
        'total-blocking-time': ['error', { maxNumericValue: 200 }],
        'speed-index': ['warn', { maxNumericValue: 3400 }],
        
        // Performance score
        'categories:performance': ['error', { minScore: 0.85 }],
        'categories:accessibility': ['error', { minScore: 0.90 }],
        'categories:best-practices': ['warn', { minScore: 0.90 }],
        
        // Resource budgets
        'resource-summary:script:size': ['error', { maxNumericValue: 500000 }],  // 500KB
        'resource-summary:total:size': ['error', { maxNumericValue: 2000000 }],  // 2MB
        
        // Specific audits
        'unused-javascript': ['warn', { maxNumericValue: 100000 }],
        'unminified-javascript': ['error', { maxNumericValue: 0 }],
        'uses-text-compression': ['error', { minScore: 1 }],
        'uses-responsive-images': ['warn', { minScore: 0.9 }]
      }
    },
    
    upload: {
      target: 'filesystem',
      outputDir: './lighthouse-results'
    }
  }
};
```

### 3.2 Bundle Size Budget

```json
// performance/lighthouse/budgets.json
[
  {
    "path": "/*",
    "resourceSizes": [
      { "resourceType": "script", "budget": 300 },
      { "resourceType": "stylesheet", "budget": 100 },
      { "resourceType": "image", "budget": 500 },
      { "resourceType": "font", "budget": 200 },
      { "resourceType": "total", "budget": 1500 }
    ],
    "resourceCounts": [
      { "resourceType": "script", "budget": 15 },
      { "resourceType": "stylesheet", "budget": 5 },
      { "resourceType": "third-party", "budget": 5 }
    ],
    "timings": [
      { "metric": "interactive", "budget": 3000 },
      { "metric": "first-contentful-paint", "budget": 1800 },
      { "metric": "largest-contentful-paint", "budget": 2500 }
    ]
  }
]
```

### 3.3 Angular Bundle Analysis

```json
// angular.json — budget configuration
{
  "budgets": [
    {
      "type": "initial",
      "maximumWarning": "400kb",
      "maximumError": "600kb"
    },
    {
      "type": "anyComponentStyle",
      "maximumWarning": "6kb",
      "maximumError": "10kb"
    },
    {
      "type": "anyScript",
      "maximumWarning": "100kb",
      "maximumError": "200kb"
    }
  ]
}
```

---

## 4. Pipeline Integration

```yaml
# Performance test pipeline stages
stages:
- stage: Smoke
  displayName: 'Performance Smoke Test'
  jobs:
  - job: k6_smoke
    pool: 'linux-agents'
    steps:
    - bash: |
        k6 run performance/k6/scenarios/smoke.js \
          --env BASE_URL=$(TEST_URL) \
          --out json=results/smoke.json
      displayName: 'k6 Smoke Test (1 VU, 30s)'
    - task: PublishPipelineArtifact@1
      inputs:
        targetPath: 'results'
        artifactName: 'perf-smoke'

- stage: LoadTest
  displayName: 'Load Test'
  dependsOn: Smoke
  condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'))
  jobs:
  - job: k6_load
    timeoutInMinutes: 15
    steps:
    - bash: |
        k6 run performance/k6/scenarios/average-load.js \
          --env BASE_URL=$(TEST_URL) \
          --out json=results/load.json \
          --summary-export=results/load-summary.json
      displayName: 'k6 Average Load Test (50 VU, 5 min)'
      
    - bash: |
        # Parse results and fail if SLA breached
        python3 scripts/check-perf-sla.py results/load-summary.json
      displayName: 'Verify SLA Compliance'

- stage: Lighthouse
  displayName: 'Frontend Performance'
  jobs:
  - job: lighthouse_ci
    steps:
    - bash: |
        npx @lhci/cli autorun --config=performance/lighthouse/lighthouserc.js
      displayName: 'Lighthouse CI'
    - task: PublishPipelineArtifact@1
      condition: always()
      inputs:
        targetPath: 'lighthouse-results'
        artifactName: 'lighthouse-report'
```

---

## 5. Performance Regression Detection

### 5.1 Baseline Comparison Script

```python
# scripts/check-perf-sla.py
import json
import sys

def check_sla(results_file):
    with open(results_file) as f:
        data = json.load(f)
    
    metrics = data.get('metrics', {})
    violations = []
    
    # Check response time SLA
    p95 = metrics.get('http_req_duration', {}).get('values', {}).get('p(95)', 0)
    if p95 > 800:
        violations.append(f"p95 response time {p95:.0f}ms exceeds SLA of 800ms")
    
    # Check error rate
    failed = metrics.get('http_req_failed', {}).get('values', {}).get('rate', 0)
    if failed > 0.01:
        violations.append(f"Error rate {failed*100:.2f}% exceeds SLA of 1%")
    
    # Check throughput
    reqs_rate = metrics.get('http_reqs', {}).get('values', {}).get('rate', 0)
    if reqs_rate < 50:
        violations.append(f"Throughput {reqs_rate:.1f} req/s below minimum 50 req/s")
    
    if violations:
        print("❌ PERFORMANCE SLA VIOLATIONS:")
        for v in violations:
            print(f"  • {v}")
        sys.exit(1)
    else:
        print("✅ All performance SLAs met")
        print(f"  • p95: {p95:.0f}ms (SLA: 800ms)")
        print(f"  • Error rate: {failed*100:.3f}% (SLA: 1%)")
        print(f"  • Throughput: {reqs_rate:.1f} req/s (min: 50)")
        sys.exit(0)

if __name__ == '__main__':
    check_sla(sys.argv[1])
```

---

## 6. SLA Reference Table

| Endpoint Category | p50 | p95 | p99 | Error Rate | Throughput |
|-------------------|-----|-----|-----|-----------|-----------|
| List endpoints (paginated) | 100ms | 500ms | 1000ms | < 0.1% | > 200 rps |
| Single record GET | 50ms | 200ms | 500ms | < 0.1% | > 500 rps |
| Create/Update (write) | 150ms | 800ms | 1500ms | < 0.5% | > 100 rps |
| Delete | 100ms | 500ms | 1000ms | < 0.1% | > 200 rps |
| File upload (< 5MB) | 500ms | 2000ms | 5000ms | < 1% | > 20 rps |
| Report generation | 1000ms | 5000ms | 10000ms | < 2% | > 5 rps |
| Authentication | 200ms | 500ms | 1000ms | < 0.1% | > 100 rps |

---

## 7. Capacity Planning Output

After breakpoint test, produce:

| Metric | Value |
|--------|-------|
| **Comfortable load** | 400 concurrent users (p95 < 500ms) |
| **Max sustainable** | 700 concurrent users (p95 < 2000ms) |
| **Breaking point** | 850 concurrent users (error rate > 10%) |
| **Recovery time** | 45 seconds (after load removed) |
| **Bottleneck** | Database connection pool (max 100) |
| **Recommended scaling trigger** | > 500 concurrent → add pod replica |

---

*Previous: [05-VISUAL-REGRESSION-TESTING.md](05-VISUAL-REGRESSION-TESTING.md) · Next: [07-SECURITY-TESTING.md](07-SECURITY-TESTING.md)*
