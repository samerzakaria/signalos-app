# 11 — Pipeline Integration

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

Pipeline integration defines **exactly when, where, and how** each test layer executes in the CI/CD process. It enforces quality gates that prevent defective code from progressing through environments — making it impossible to deploy untested or failing code.

**What this layer kills:**
- "We forgot to run tests before deploying" → Tests are mandatory gates
- "Tests are too slow so developers skip them" → Parallelized, cached, optimized
- "Different test types run at different times and we lose track" → Single pipeline orchestrates all
- "A flaky test blocked deployment for 3 hours" → Quarantine + retry + owner assignment
- "We deployed to production without security scan" → Security is a blocking gate

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CI/CD PIPELINE ARCHITECTURE                                │
│                                                                              │
│  ┌──── COMMIT ──────────────────────────────────────────────────────────┐   │
│  │  Pre-commit hooks: secrets scan, lint, format                        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│           │                                                                  │
│           ▼                                                                  │
│  ┌──── PR / CI (every push) ────────────────────────────────────────────┐   │
│  │                                                                       │   │
│  │  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────────┐        │   │
│  │  │ Build   │  │ Unit     │  │ SAST      │  │ SCA          │        │   │
│  │  │ + Lint  │→ │ Tests    │→ │ (Semgrep) │→ │ (Vuln Check) │        │   │
│  │  │         │  │          │  │           │  │              │        │   │
│  │  │ < 2 min │  │ < 3 min  │  │ < 2 min   │  │ < 1 min      │        │   │
│  │  └─────────┘  └──────────┘  └───────────┘  └──────────────┘        │   │
│  │       │              │             │              │                   │   │
│  │       └──────────────┴─────────────┴──────────────┘                  │   │
│  │                            │                                          │   │
│  │                            ▼                                          │   │
│  │  ┌───────────────┐  ┌─────────────────┐  ┌────────────────────┐     │   │
│  │  │ Integration   │  │ Contract Tests  │  │ Visual Regression  │     │   │
│  │  │ Tests         │→ │ (Pact)          │→ │ (Playwright)       │     │   │
│  │  │               │  │                 │  │                    │     │   │
│  │  │ < 5 min       │  │ < 3 min         │  │ < 3 min            │     │   │
│  │  └───────────────┘  └─────────────────┘  └────────────────────┘     │   │
│  │                                                                       │   │
│  │  GATE: All pass → PR mergeable                                       │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│           │                                                                  │
│           ▼                                                                  │
│  ┌──── MERGE TO MAIN ──────────────────────────────────────────────────┐   │
│  │                                                                       │   │
│  │  ┌────────────┐  ┌─────────────┐  ┌───────────────────────────┐     │   │
│  │  │ Full Build │  │ Container   │  │ Performance (Smoke)       │     │   │
│  │  │ + Package  │→ │ Scan        │→ │ (k6 average load)         │     │   │
│  │  │            │  │ (Trivy)     │  │                           │     │   │
│  │  │ < 5 min    │  │ < 3 min     │  │ < 5 min                   │     │   │
│  │  └────────────┘  └─────────────┘  └───────────────────────────┘     │   │
│  │                                                                       │   │
│  │  GATE: All pass → Deploy to Test                                     │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│           │                                                                  │
│           ▼                                                                  │
│  ┌──── DEPLOY TO TEST ─────────────────────────────────────────────────┐   │
│  │                                                                       │   │
│  │  ┌──────────┐  ┌───────────────┐  ┌─────────────────────────────┐   │   │
│  │  │ E2E      │  │ DAST          │  │ Can-I-Deploy?               │   │   │
│  │  │ Tests    │→ │ (OWASP ZAP)   │→ │ (Pact Broker)              │   │   │
│  │  │          │  │               │  │                             │   │   │
│  │  │ < 10 min │  │ < 15 min      │  │ < 1 min                     │   │   │
│  │  └──────────┘  └───────────────┘  └─────────────────────────────┘   │   │
│  │                                                                       │   │
│  │  GATE: All pass → Promote to Staging                                 │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│           │                                                                  │
│           ▼                                                                  │
│  ┌──── STAGING ────────────────────────────────────────────────────────┐   │
│  │                                                                       │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────┐   │   │
│  │  │ Chaos Tests  │  │ Load Test    │  │ Manual Approval         │   │   │
│  │  │ (Optional)   │→ │ (Full)       │→ │ (for Production)        │   │   │
│  │  │              │  │              │  │                         │   │   │
│  │  │ < 30 min     │  │ < 15 min     │  │ Human decision          │   │   │
│  │  └──────────────┘  └──────────────┘  └─────────────────────────┘   │   │
│  │                                                                       │   │
│  │  GATE: All pass + approval → Deploy to Production                    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│           │                                                                  │
│           ▼                                                                  │
│  ┌──── PRODUCTION ─────────────────────────────────────────────────────┐   │
│  │                                                                       │   │
│  │  ┌──────────────┐  ┌──────────────────┐  ┌────────────────────┐    │   │
│  │  │ Canary       │  │ Smoke Test       │  │ SLO Monitoring     │    │   │
│  │  │ (10% traffic)│→ │ (Post-deploy)    │→ │ (Continuous)       │    │   │
│  │  │              │  │                  │  │                    │    │   │
│  │  │ 5 min        │  │ < 2 min          │  │ 24/7               │    │   │
│  │  └──────────────┘  └──────────────────┘  └────────────────────┘    │   │
│  │                                                                       │   │
│  │  AUTO-ROLLBACK: If SLO breached within 30 min of deploy              │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Azure DevOps Pipeline Definition

### 1.1 CI Pipeline (PR Validation)

```yaml
# pipelines/ci-validation.yaml
trigger: none

pr:
  branches:
    include: [main, develop, release/*]
  paths:
    exclude:
      - '**/*.md'
      - 'docs/**'
      - '.signal/**'

pool:
  vmImage: 'ubuntu-latest'

variables:
  - group: 'test-credentials'
  - name: DOTNET_VERSION
    value: '8.0.x'
  - name: NODE_VERSION
    value: '20.x'

stages:
  # ═══════════════════════════════════════════════════════════
  # STAGE 1: Build + Static Analysis (parallel)
  # ═══════════════════════════════════════════════════════════
  - stage: BuildAndAnalyze
    displayName: 'Build & Static Analysis'
    jobs:
      - job: Build
        displayName: 'Build Solution'
        steps:
          - task: UseDotNet@2
            inputs:
              version: $(DOTNET_VERSION)
          - bash: dotnet restore
          - bash: dotnet build --no-restore --configuration Release
            displayName: 'Build'
          - bash: dotnet publish --no-build -c Release -o $(Build.ArtifactStagingDirectory)
            displayName: 'Publish'

      - job: Lint
        displayName: 'Lint & Format'
        steps:
          - bash: dotnet format --verify-no-changes --severity warn
            displayName: '.NET Format Check'
          - task: UseNode@1
            inputs:
              version: $(NODE_VERSION)
          - bash: |
              cd src/frontend
              npm ci
              npm run lint -- --max-warnings=0
            displayName: 'Angular Lint'

      - job: SAST
        displayName: 'SAST (Semgrep)'
        steps:
          - bash: |
              pip install semgrep
              semgrep scan --config=auto --config=.semgrep/ \
                --error --severity=ERROR \
                --sarif --output=sast-results.sarif \
                src/
            displayName: 'Semgrep Scan'
          - task: PublishPipelineArtifact@1
            condition: always()
            inputs:
              targetPath: 'sast-results.sarif'
              artifactName: 'sast'

      - job: SCA
        displayName: 'Dependency Scan'
        steps:
          - bash: |
              dotnet list package --vulnerable --include-transitive 2>&1 | tee vuln.txt
              if grep -qi "Critical\|High" vuln.txt; then exit 1; fi
            displayName: 'NuGet Vulnerability Check'
          - bash: |
              cd src/frontend
              npm ci
              npm audit --production --audit-level=high
            displayName: 'npm Audit'

  # ═══════════════════════════════════════════════════════════
  # STAGE 2: Unit + Integration Tests
  # ═══════════════════════════════════════════════════════════
  - stage: Test
    displayName: 'Tests'
    dependsOn: BuildAndAnalyze
    jobs:
      - job: UnitTests
        displayName: 'Unit Tests'
        steps:
          - bash: |
              dotnet test tests/Unit/ \
                --configuration Release \
                --no-build \
                --logger "trx;LogFileName=unit-results.trx" \
                --collect:"XPlat Code Coverage" \
                -- DataCollectionRunSettings.DataCollectors.DataCollector.Configuration.Format=cobertura
            displayName: 'Run Unit Tests'
          - task: PublishTestResults@2
            inputs:
              testResultsFormat: 'VSTest'
              testResultsFiles: '**/unit-results.trx'
          - task: PublishCodeCoverageResults@2
            inputs:
              summaryFileLocation: '**/coverage.cobertura.xml'

      - job: IntegrationTests
        displayName: 'Integration Tests'
        services:
          sqlserver:
            image: mcr.microsoft.com/mssql/server:2022-latest
            env:
              ACCEPT_EULA: Y
              SA_PASSWORD: Test@Password123!
          redis:
            image: redis:7-alpine
        steps:
          - bash: |
              dotnet test tests/Integration/ \
                --configuration Release \
                --logger "trx;LogFileName=integration-results.trx" \
                --blame-crash \
                --blame-hang-timeout 5m
            displayName: 'Run Integration Tests'
            env:
              ConnectionStrings__Default: "Server=sqlserver;Database=test;User=sa;Password=Test@Password123!;TrustServerCertificate=true"
              Redis__Configuration: "redis:6379"

      - job: FrontendTests
        displayName: 'Frontend Unit Tests'
        steps:
          - bash: |
              cd src/frontend
              npm ci
              npm run test:ci -- --coverage --coverageReporters=cobertura
            displayName: 'Jest Tests'
          - task: PublishTestResults@2
            inputs:
              testResultsFormat: 'JUnit'
              testResultsFiles: '**/junit.xml'

      - job: ContractTests
        displayName: 'Contract Tests'
        steps:
          - bash: |
              cd src/frontend
              npm ci
              npm run test:pact
            displayName: 'Generate Consumer Contracts'
          - bash: |
              pact-broker publish ./pacts \
                --consumer-app-version "$(Build.BuildNumber)" \
                --branch "$(System.PullRequest.SourceBranch)" \
                --broker-base-url "$(PACT_BROKER_URL)" \
                --broker-token "$(PACT_BROKER_TOKEN)"
            displayName: 'Publish Contracts'
            condition: succeeded()

  # ═══════════════════════════════════════════════════════════
  # STAGE 3: Visual + E2E (after deploy to ephemeral env)
  # ═══════════════════════════════════════════════════════════
  - stage: E2E
    displayName: 'E2E & Visual'
    dependsOn: Test
    jobs:
      - job: VisualRegression
        displayName: 'Visual Regression'
        steps:
          - bash: |
              cd src/frontend
              npm ci
              npx playwright install --with-deps chromium
              npx playwright test --project=visual-chromium
            displayName: 'Visual Tests'
          - task: PublishPipelineArtifact@1
            condition: failed()
            inputs:
              targetPath: 'test-results'
              artifactName: 'visual-diffs'
```

### 1.2 CD Pipeline (Deploy + Verify)

```yaml
# pipelines/cd-deploy.yaml
trigger:
  branches:
    include: [main]
  paths:
    exclude: ['**/*.md', 'docs/**']

stages:
  # ═══════════════════════════════════════════════════════════
  # STAGE 1: Build + Package
  # ═══════════════════════════════════════════════════════════
  - stage: Package
    jobs:
      - job: BuildImage
        steps:
          - bash: |
              docker build -t $(HARBOR_REGISTRY)/$(IMAGE):$(Build.BuildNumber) .
              docker push $(HARBOR_REGISTRY)/$(IMAGE):$(Build.BuildNumber)
            displayName: 'Build & Push Image'
          
          - bash: |
              trivy image --severity CRITICAL,HIGH --exit-code 1 \
                $(HARBOR_REGISTRY)/$(IMAGE):$(Build.BuildNumber)
            displayName: 'Container Security Scan'

  # ═══════════════════════════════════════════════════════════
  # STAGE 2: Deploy to Test + Verify
  # ═══════════════════════════════════════════════════════════
  - stage: DeployTest
    displayName: 'Deploy to Test'
    dependsOn: Package
    jobs:
      - deployment: DeployToTest
        environment: 'test'
        strategy:
          runOnce:
            deploy:
              steps:
                - bash: |
                    argocd login $(ARGOCD_SERVER) --username $(ARGOCD_USER) --password $(ARGOCD_PASS)
                    argocd app set ehkaam -p image.tag=$(Build.BuildNumber)
                    argocd app sync ehkaam --force --prune
                    argocd app wait ehkaam --timeout 300
                  displayName: 'ArgoCD Sync'

      - job: E2ETests
        displayName: 'E2E Validation'
        dependsOn: DeployToTest
        steps:
          - bash: |
              npx playwright install --with-deps
              npx playwright test --project=chromium
            displayName: 'E2E Tests (Chromium)'
            env:
              BASE_URL: $(TEST_ENVIRONMENT_URL)
              E2E_ADMIN_USER: $(E2E_ADMIN_USER)
              E2E_ADMIN_PASS: $(E2E_ADMIN_PASS)
          - task: PublishTestResults@2
            inputs:
              testResultsFormat: 'JUnit'
              testResultsFiles: '**/e2e-results.xml'

      - job: DAST
        displayName: 'DAST (OWASP ZAP)'
        dependsOn: DeployToTest
        container:
          image: ghcr.io/zaproxy/zaproxy:stable
        steps:
          - bash: |
              zap-api-scan.py \
                -t $(TEST_ENVIRONMENT_URL)/swagger/v1/swagger.json \
                -f openapi \
                -r zap-report.html \
                -c zap-config.conf
            displayName: 'ZAP API Scan'

      - job: PerformanceTest
        displayName: 'Performance (Load)'
        dependsOn: DeployToTest
        steps:
          - bash: |
              k6 run performance/k6/scenarios/average-load.js \
                --env BASE_URL=$(TEST_ENVIRONMENT_URL) \
                --summary-export=perf-summary.json
              python3 scripts/check-perf-sla.py perf-summary.json
            displayName: 'k6 Load Test'

      - job: ContractGate
        displayName: 'Can I Deploy?'
        dependsOn: [E2ETests, DAST, PerformanceTest]
        steps:
          - bash: |
              pact-broker can-i-deploy \
                --pacticipant "BackendApis" \
                --version "$(Build.BuildNumber)" \
                --to-environment "Staging" \
                --broker-base-url "$(PACT_BROKER_URL)"
            displayName: 'Contract Verification Gate'

  # ═══════════════════════════════════════════════════════════
  # STAGE 3: Staging + Production
  # ═══════════════════════════════════════════════════════════
  - stage: DeployStaging
    displayName: 'Deploy to Staging'
    dependsOn: DeployTest
    condition: succeeded()
    jobs:
      - deployment: StagingDeploy
        environment: 'staging'
        strategy:
          runOnce:
            deploy:
              steps:
                - bash: echo "Deploy to staging..."

      - job: StagingSmoke
        dependsOn: StagingDeploy
        steps:
          - bash: |
              k6 run synthetic-monitors/critical-journey.ts \
                --env PRODUCTION_URL=$(STAGING_URL) \
                --env SYNTHETIC_AUTH_TOKEN=$(STAGING_TOKEN)
            displayName: 'Staging Smoke Test'

  - stage: DeployProduction
    displayName: 'Deploy to Production'
    dependsOn: DeployStaging
    condition: succeeded()
    jobs:
      - deployment: ProductionDeploy
        environment: 'production'  # Requires manual approval
        strategy:
          runOnce:
            deploy:
              steps:
                - bash: echo "Deploy to production (canary)..."

      - job: PostDeployVerification
        dependsOn: ProductionDeploy
        steps:
          - bash: |
              # Verify canary health
              sleep 300  # 5 min observation
              
              # Check error rate
              ERROR_RATE=$(curl -s "http://prometheus:9090/api/v1/query?query=..." | jq '.data.result[0].value[1]')
              if (( $(echo "$ERROR_RATE > 0.01" | bc -l) )); then
                echo "##[error]Error rate too high. Rolling back."
                argocd app rollback ehkaam
                exit 1
              fi
            displayName: 'Canary Health Check'
```

---

## 2. Quality Gates Summary

| Gate | Location | Criteria | Failure Action |
|------|----------|----------|----------------|
| **G1: PR Merge** | PR validation | All unit/integration pass, SAST clean, SCA clean | Block merge |
| **G2: Container Push** | Build pipeline | Trivy no Critical/High, Hadolint pass | Block push |
| **G3: Deploy to Test** | After build | Package successful, container scanned | Block deploy |
| **G4: Promote to Staging** | After test validation | E2E pass, DAST clean, Perf SLA met, Can-I-Deploy pass | Block promotion |
| **G5: Deploy to Production** | After staging | Staging smoke pass + human approval | Block deploy |
| **G6: Keep in Production** | Post-deploy (30 min) | Error rate < SLO, latency < SLO | Auto-rollback |

---

## 3. Test Optimization for Speed

### 3.1 Selective Test Execution

```yaml
# Only run tests related to changed files
- bash: |
    CHANGED_FILES=$(git diff --name-only origin/main...HEAD)
    
    # Backend changes → run backend tests
    if echo "$CHANGED_FILES" | grep -q "^src/backend/"; then
      dotnet test tests/ --filter "Category!=E2E"
    fi
    
    # Frontend changes → run frontend tests
    if echo "$CHANGED_FILES" | grep -q "^src/frontend/"; then
      cd src/frontend && npm run test:ci
    fi
    
    # API contract changes → run contract tests
    if echo "$CHANGED_FILES" | grep -q "Controllers\|Dto\|swagger"; then
      dotnet test tests/Pact.Verification/
    fi
  displayName: 'Selective Test Execution'
```

### 3.2 Test Caching

```yaml
# Cache NuGet packages
- task: Cache@2
  inputs:
    key: 'nuget | "$(Agent.OS)" | **/packages.lock.json'
    path: '$(NUGET_PACKAGES)'
    restoreKeys: 'nuget | "$(Agent.OS)"'

# Cache npm
- task: Cache@2
  inputs:
    key: 'npm | "$(Agent.OS)" | src/frontend/package-lock.json'
    path: 'src/frontend/node_modules'

# Cache Playwright browsers
- task: Cache@2
  inputs:
    key: 'playwright | "$(Agent.OS)" | src/frontend/package-lock.json'
    path: '~/.cache/ms-playwright'

# Cache Docker layers
- task: Cache@2
  inputs:
    key: 'docker | Dockerfile'
    path: '/tmp/.buildx-cache'
```

### 3.3 Parallel Execution

```yaml
# Shard E2E tests across agents
strategy:
  matrix:
    shard1:
      SHARD: '1/4'
    shard2:
      SHARD: '2/4'
    shard3:
      SHARD: '3/4'
    shard4:
      SHARD: '4/4'

steps:
  - bash: npx playwright test --shard=$(SHARD)
```

---

## 4. Pipeline Timing Budget

| Stage | Time Budget | Actual Target | Failure = |
|-------|-------------|---------------|-----------|
| Build + Lint | 3 min | 2 min | PR blocked |
| Unit Tests | 5 min | 3 min | PR blocked |
| SAST + SCA | 3 min | 2 min | PR blocked |
| Integration Tests | 7 min | 5 min | PR blocked |
| Contract Tests | 3 min | 2 min | PR blocked |
| Visual Regression | 4 min | 3 min | PR blocked |
| **Total PR Validation** | **25 min** | **17 min** | — |
| Container Build + Scan | 5 min | 4 min | Deploy blocked |
| E2E Tests | 12 min | 8 min | Promotion blocked |
| DAST | 15 min | 12 min | Promotion blocked |
| Performance | 7 min | 5 min | Promotion blocked |
| **Total Deploy Validation** | **39 min** | **29 min** | — |

---

## 5. Flaky Test Management

```yaml
# Automatic flaky test detection and quarantine
- bash: |
    # Run tests with retry
    npx playwright test --retries=2
    
    # If any test needed retry to pass → mark as flaky
    FLAKY=$(cat test-results/*.json | jq '[.suites[].specs[] | select(.tests[].results | length > 1)] | length')
    
    if [ "$FLAKY" -gt "0" ]; then
      echo "##[warning]$FLAKY flaky test(s) detected. Creating tracking issue."
      # Auto-create work item for flaky tests
      az boards work-item create \
        --type "Bug" \
        --title "[Flaky] $FLAKY tests needed retry on $(Build.BuildNumber)" \
        --assigned-to "$(Build.RequestedFor)"
    fi
  displayName: 'Detect & Report Flaky Tests'
```

---

*Previous: [10-TEST-DATA-MANAGEMENT.md](10-TEST-DATA-MANAGEMENT.md) · Next: [12-METRICS-GOVERNANCE.md](12-METRICS-GOVERNANCE.md)*
