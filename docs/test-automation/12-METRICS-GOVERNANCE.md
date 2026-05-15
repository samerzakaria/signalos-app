# 12 — Metrics & Governance

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

Metrics and governance establish **measurable standards** for test quality, track progress toward zero manual testing, and provide clear accountability for test health. Without metrics, "zero manual testing" is an aspiration. With metrics, it's a measurable fact.

**What this layer kills:**
- "We think our tests are good but have no data" → Dashboard shows exactly where we are
- "Flaky tests erode trust in automation" → Flake rate tracked, owners assigned, SLA enforced
- "Test coverage is 80% but all the critical paths are untested" → Weighted coverage by risk
- "Nobody maintains the tests" → Owner accountability with response SLAs
- "We don't know if we're getting better or worse" → Trend analysis over time

---

## 1. Key Performance Indicators (KPIs)

### 1.1 Primary KPIs

| KPI | Definition | Target | Alert Threshold |
|-----|-----------|--------|-----------------|
| **Test Pass Rate** | Passing tests / Total tests (main branch) | > 99.5% | < 98% |
| **Flake Rate** | Tests needing retry / Total tests | < 1% | > 3% |
| **Mean Time to Fix** | Time from test failure to fix merged | < 4 hours | > 24 hours |
| **Coverage (Backend)** | Line coverage on .NET code | > 80% | < 70% |
| **Coverage (Frontend)** | Branch coverage on Angular code | > 75% | < 65% |
| **Pipeline Duration** | PR validation total time | < 20 min | > 30 min |
| **Security Gate Pass Rate** | Clean SAST/SCA on first try | > 95% | < 85% |
| **Manual Test Count** | Remaining manual test cases | 0 | > 5 |
| **Escape Rate** | Production bugs not caught by tests | < 2/month | > 5/month |
| **SLO Compliance** | Time SLOs are met / Total time | > 99.9% | < 99.5% |

### 1.2 Secondary KPIs

| KPI | Definition | Target |
|-----|-----------|--------|
| Test execution speed (unit) | Total unit test suite time | < 3 min |
| Test execution speed (integration) | Total integration suite time | < 5 min |
| Test execution speed (E2E) | Total E2E suite time | < 10 min |
| Container scan clean rate | Images with zero Critical CVEs | 100% |
| Contract verification pass rate | All consumer contracts verified | 100% |
| Visual regression false positive rate | False diffs / Total diffs | < 5% |
| Mutation testing score | Mutations killed / Total mutations | > 70% |
| Performance regression rate | SLA breaches per deploy | < 5% |
| Chaos experiment pass rate | Hypotheses confirmed | > 90% |

---

## 2. Dashboard Design

### 2.1 Team Dashboard (Grafana)

```json
// grafana-dashboard.json (simplified structure)
{
  "title": "Test Automation — Test Health Dashboard",
  "panels": [
    {
      "title": "Pipeline Success Rate (7d)",
      "type": "stat",
      "targets": [{ "expr": "sum(pipeline_runs_success) / sum(pipeline_runs_total) * 100" }],
      "thresholds": [
        { "value": 95, "color": "green" },
        { "value": 90, "color": "yellow" },
        { "value": 0, "color": "red" }
      ]
    },
    {
      "title": "Flaky Test Trend (30d)",
      "type": "timeseries",
      "targets": [{ "expr": "test_flake_rate{branch='main'}" }]
    },
    {
      "title": "Coverage Trend",
      "type": "timeseries",
      "targets": [
        { "expr": "code_coverage_percent{project='backend'}", "legendFormat": "Backend" },
        { "expr": "code_coverage_percent{project='frontend'}", "legendFormat": "Frontend" }
      ]
    },
    {
      "title": "Escape Rate (Bugs in Prod)",
      "type": "stat",
      "targets": [{ "expr": "production_bugs_this_month" }],
      "thresholds": [
        { "value": 0, "color": "green" },
        { "value": 2, "color": "yellow" },
        { "value": 5, "color": "red" }
      ]
    },
    {
      "title": "Quality Gates Status",
      "type": "table",
      "targets": [{
        "expr": "quality_gate_status",
        "columns": ["Gate", "Last Status", "Last Run", "Avg Duration"]
      }]
    },
    {
      "title": "Test Execution Time Breakdown",
      "type": "barchart",
      "targets": [
        { "expr": "test_suite_duration_seconds{suite='unit'}", "legendFormat": "Unit" },
        { "expr": "test_suite_duration_seconds{suite='integration'}", "legendFormat": "Integration" },
        { "expr": "test_suite_duration_seconds{suite='e2e'}", "legendFormat": "E2E" },
        { "expr": "test_suite_duration_seconds{suite='visual'}", "legendFormat": "Visual" }
      ]
    }
  ]
}
```

### 2.2 Executive Summary View

| Metric | This Week | Last Week | Trend | Status |
|--------|-----------|-----------|-------|--------|
| Pipeline Success | 97.2% | 95.8% | ↑ | ✅ |
| Avg PR Validation | 18 min | 22 min | ↓ (good) | ✅ |
| Test Coverage (Backend) | 82.3% | 81.1% | ↑ | ✅ |
| Test Coverage (Frontend) | 76.8% | 76.5% | → | ✅ |
| Flake Rate | 0.8% | 1.2% | ↓ (good) | ✅ |
| Production Escapes | 1 | 3 | ↓ (good) | ✅ |
| Manual Tests Remaining | 3 | 7 | ↓ (good) | ⚠️ |
| SLO Compliance | 99.95% | 99.91% | ↑ | ✅ |
| Security Vulnerabilities | 0 Critical | 0 Critical | → | ✅ |
| Error Budget Remaining | 87% | 72% | ↑ | ✅ |

---

## 3. Flaky Test Governance

### 3.1 Flake Classification

| Category | Definition | SLA to Fix | Action |
|----------|-----------|-----------|--------|
| **Timing flake** | Test depends on timing/order | 48 hours | Add explicit wait/sync |
| **Data flake** | Test shares state with another test | 24 hours | Isolate data |
| **Environment flake** | Fails only in CI (works locally) | 72 hours | Fix CI setup |
| **Intermittent** | Fails randomly, cause unknown | 1 week | Quarantine → investigate |
| **Infrastructure** | External service/network issue | Fix infra | Add retry/mock |

### 3.2 Flake Management Process

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. DETECTION: Test fails on retry → auto-tagged as "flaky"     │
│                                                                  │
│ 2. LOGGING: Flake counter incremented, added to tracking board  │
│                                                                  │
│ 3. QUARANTINE (if flake rate > 3x in 7 days):                  │
│    - Test moved to quarantine suite                              │
│    - Still runs but doesn't block pipeline                       │
│    - Owner assigned automatically (last modifier)                │
│                                                                  │
│ 4. FIX: Owner fixes root cause within SLA                       │
│                                                                  │
│ 5. RESTORE: After 10 consecutive passes → unquarantine          │
│                                                                  │
│ 6. DELETE: If unfixable after 30 days → delete test + write     │
│    replacement from scratch                                      │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 Quarantine Implementation

```typescript
// playwright.config.ts — Quarantine project
{
  name: 'quarantine',
  testMatch: /.*\.quarantine\.spec\.ts/,
  use: { ...devices['Desktop Chrome'] },
  retries: 3,
  // Does NOT block pipeline
  metadata: { blocking: false }
}
```

```yaml
# Pipeline: quarantine tests don't fail the build
- bash: |
    npx playwright test --project=quarantine || true
    # Report results but don't fail
  displayName: 'Quarantine Tests (non-blocking)'
  continueOnError: true
```

---

## 4. Coverage Governance

### 4.1 Weighted Coverage Model

Not all code is equally important. Weight coverage requirements by risk:

| Module Category | Min Line Coverage | Min Branch Coverage | Rationale |
|----------------|-------------------|---------------------|-----------|
| Auth/Security | 95% | 90% | T3 — AUDIT: highest risk |
| Business Logic (Services) | 85% | 80% | Core value delivery |
| API Controllers | 80% | 75% | Input validation paths |
| Data Access (Repositories) | 75% | 70% | Integration tests cover more |
| UI Components (Angular) | 75% | 70% | Visual tests supplement |
| Configuration/Startup | 50% | N/A | Low risk, hard to unit test |
| DTOs/Models | N/A | N/A | No logic to test |

### 4.2 Coverage Enforcement

```yaml
# Pipeline coverage gate
- bash: |
    # Extract coverage from report
    BACKEND_COVERAGE=$(dotnet-coverage merge -f cobertura *.xml | grep -oP 'line-rate="\K[^"]+')
    FRONTEND_COVERAGE=$(cat coverage/coverage-summary.json | jq '.total.lines.pct')
    
    echo "Backend coverage: $BACKEND_COVERAGE"
    echo "Frontend coverage: $FRONTEND_COVERAGE"
    
    # Check thresholds
    if (( $(echo "$BACKEND_COVERAGE < 0.70" | bc -l) )); then
      echo "##[error]Backend coverage $BACKEND_COVERAGE below minimum 70%"
      exit 1
    fi
    
    if (( $(echo "$FRONTEND_COVERAGE < 65" | bc -l) )); then
      echo "##[error]Frontend coverage $FRONTEND_COVERAGE% below minimum 65%"
      exit 1
    fi
    
    # Check coverage DIFF (new code must be covered)
    NEW_CODE_COVERAGE=$(python3 scripts/coverage-diff.py)
    if (( $(echo "$NEW_CODE_COVERAGE < 80" | bc -l) )); then
      echo "##[error]New code coverage $NEW_CODE_COVERAGE% below minimum 80%"
      exit 1
    fi
  displayName: 'Coverage Gate'
```

### 4.3 Coverage Ratchet (Never Go Down)

```python
# scripts/coverage-ratchet.py
"""
Coverage ratchet: coverage can only go up, never down.
Reads current coverage, compares to stored baseline.
If coverage decreased → fail.
If coverage increased → update baseline.
"""
import json, sys

BASELINE_FILE = '.coverage-baseline.json'

def check_ratchet(current_coverage: float, project: str):
    with open(BASELINE_FILE) as f:
        baselines = json.load(f)
    
    baseline = baselines.get(project, 0)
    
    if current_coverage < baseline - 0.5:  # Allow 0.5% tolerance for refactoring
        print(f"❌ Coverage DECREASED: {current_coverage:.1f}% < {baseline:.1f}% (baseline)")
        print(f"   Coverage can only go UP. Add tests for uncovered code.")
        sys.exit(1)
    
    if current_coverage > baseline:
        # Update baseline
        baselines[project] = round(current_coverage, 1)
        with open(BASELINE_FILE, 'w') as f:
            json.dump(baselines, f, indent=2)
        print(f"✅ Coverage INCREASED: {baseline:.1f}% → {current_coverage:.1f}%")
    else:
        print(f"✅ Coverage maintained: {current_coverage:.1f}% (baseline: {baseline:.1f}%)")
```

---

## 5. Test Ownership Model

### 5.1 Ownership Assignment

| Test Category | Owner | Escalation |
|--------------|-------|------------|
| Unit tests for module X | Developer who wrote module X | Tech Lead |
| Integration tests | Backend team | Tech Lead |
| E2E tests | Full-stack developer | Product Owner |
| Visual tests | Frontend developer | Design Lead |
| Performance tests | DevOps/Platform team | Tech Lead |
| Security tests | Security champion | Security team |
| Contract tests | Both consumer + provider teams | Architect |

### 5.2 Responsibility SLAs

| Event | Response SLA | Resolution SLA |
|-------|-------------|----------------|
| Test failure on main branch | 1 hour (acknowledge) | 4 hours (fix or revert) |
| Flaky test detected | 24 hours (acknowledge) | 1 week (root cause fix) |
| Coverage drop below threshold | Same PR (block merge) | Immediate |
| Security scan finding | 4 hours (triage) | 24h (Critical), 1 week (High) |
| Performance SLA breach | 1 hour (acknowledge) | 8 hours (fix or rollback) |
| New escape (prod bug) | Post-mortem within 48h | Add missing test within 1 week |

---

## 6. Maturity Model

### 6.1 Zero Manual Testing Maturity Levels

```
LEVEL 0: REACTIVE
├── Tests exist but are run manually
├── No CI/CD integration
├── No coverage tracking
└── Fixes happen after production incidents

LEVEL 1: FOUNDATIONAL
├── CI runs unit tests on every PR
├── Coverage tracked (> 50%)
├── Basic SAST scanning
└── Manual E2E testing still required

LEVEL 2: STRUCTURED
├── Integration tests with real dependencies (Testcontainers)
├── Coverage gated (> 70%)
├── SAST + SCA in pipeline
├── Basic E2E automation (critical paths only)
└── Some manual regression testing still needed

LEVEL 3: AUTOMATED
├── Full E2E automation (all paths)
├── Contract testing in place
├── Visual regression automated
├── Performance testing in CI
├── Security scanning (SAST + DAST)
├── No routine manual testing
└── Manual testing only for exploratory/new features

LEVEL 4: PREVENTIVE
├── Mutation testing validates test quality
├── Chaos engineering proves resilience
├── Production monitoring as testing
├── Auto-rollback on SLO breach
├── Zero manual test requirement
├── All quality gates automated
└── Bugs are caught before they reach any environment

LEVEL 5: OPTIMIZED
├── AI-assisted test generation
├── Self-healing tests
├── Predictive quality (ML-based risk scoring)
├── Continuous optimization of test suite
├── Zero escapes (all bugs caught before production)
└── Tests drive architecture decisions
```

### 6.2 Assessment Checklist

```markdown
## Monthly Maturity Assessment

### Level 1 Checks
- [ ] All unit tests pass on every PR
- [ ] CI pipeline runs automatically
- [ ] Coverage is measured and reported
- [ ] SAST tool is configured

### Level 2 Checks  
- [ ] Integration tests use real databases (not mocks)
- [ ] Coverage gate blocks PRs below threshold
- [ ] SCA runs on every build
- [ ] At least critical E2E paths are automated

### Level 3 Checks
- [ ] Zero manual test cases required for regression
- [ ] Contract tests prevent breaking changes
- [ ] Visual regression prevents UI drift
- [ ] Performance tests prevent SLA violations
- [ ] DAST runs against deployed application

### Level 4 Checks
- [ ] Mutation testing validates test effectiveness
- [ ] Chaos experiments prove resilience quarterly
- [ ] Production synthetics detect issues before users
- [ ] Auto-rollback works and has been triggered
- [ ] Flake rate < 1%

### Level 5 Checks
- [ ] Test generation is partially automated
- [ ] Zero production escapes for 3+ months
- [ ] Test suite execution time optimized (under budget)
- [ ] Quality metrics drive sprint planning
```

---

## 7. Reporting Cadence

| Report | Frequency | Audience | Content |
|--------|-----------|----------|---------|
| Test Health Dashboard | Real-time | Developers | Pass rate, flakes, coverage |
| Weekly Quality Summary | Weekly | Tech Lead | Trends, escapes, SLA compliance |
| Monthly Maturity Report | Monthly | Engineering Manager | Level assessment, gaps, plan |
| Quarterly Security Report | Quarterly | CISO/Stakeholders | Vulnerability stats, OWASP compliance |
| Escape Post-Mortem | Per incident | Full team | Root cause, missing test, fix |

---

## 8. Continuous Improvement Loop

```
┌─────────────┐     ┌──────────────┐     ┌────────────────┐
│  MEASURE    │────►│   ANALYZE    │────►│    IMPROVE     │
│             │     │              │     │                │
│ • KPIs      │     │ • Trends     │     │ • Fix flakes   │
│ • Coverage  │     │ • Root cause │     │ • Add coverage │
│ • Escapes   │     │ • Patterns   │     │ • Optimize CI  │
│ • Flakes    │     │ • Gaps       │     │ • Add layers   │
└─────────────┘     └──────────────┘     └────────────────┘
       ▲                                         │
       │                                         │
       └─────────────────────────────────────────┘
                    REPEAT WEEKLY
```

---

*Previous: [11-PIPELINE-INTEGRATION.md](11-PIPELINE-INTEGRATION.md) · Next: [13-MIGRATION-ROADMAP.md](13-MIGRATION-ROADMAP.md)*
