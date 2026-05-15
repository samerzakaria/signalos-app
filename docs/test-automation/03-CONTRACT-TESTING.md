# 03 — Contract Testing

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

Contract testing verifies that **consumers and providers agree on the interface** between them. When a frontend expects `{ id: string, count: number }` from an API, and the backend changes `count` to `quantity`, contract tests catch this **before deployment** — without requiring both systems to be running together.

**What this layer kills:**
- "Frontend broke after backend deploy" → Contract catches breaking changes in CI
- "We need to regression test all consumers after API change" → Only affected contracts fail
- "Integration testing between teams takes forever" → Each team tests independently
- "API documentation is outdated" → Contract IS the documentation (living, verified)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CONTRACT TESTING MODEL                                 │
│                                                                              │
│   CONSUMER (Frontend)              PROVIDER (Backend)                        │
│   ┌──────────────────┐            ┌──────────────────┐                      │
│   │ Angular Admin    │            │ .NET API          │                      │
│   │                  │            │                   │                      │
│   │ "When I call     │  contract  │ "I verify I can   │                      │
│   │  GET /exemptions │───────────►│  fulfill what the │                      │
│   │  I expect:       │   (pact)   │  consumer expects"│                      │
│   │  {items:[{id,    │            │                   │                      │
│   │    nid, count}]} │            │                   │                      │
│   └──────────────────┘            └──────────────────┘                      │
│          │                                  │                                │
│          ▼                                  ▼                                │
│   ┌──────────────────┐            ┌──────────────────┐                      │
│   │ Generates Pact   │            │ Verifies Pact    │                      │
│   │ (consumer test)  │            │ (provider test)  │                      │
│   └────────┬─────────┘            └────────┬─────────┘                      │
│            │                                │                                │
│            ▼                                ▼                                │
│   ┌─────────────────────────────────────────────────────┐                   │
│   │              PACT BROKER (central registry)          │                   │
│   │                                                      │                   │
│   │  • Stores all contracts                              │                   │
│   │  • Tracks verification status                        │                   │
│   │  • "Can I deploy?" API                               │                   │
│   │  • Dependency graph visualization                    │                   │
│   └─────────────────────────────────────────────────────┘                   │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  Gate: All consumer contracts verified by provider before deploy             │
│  Gate: "can-i-deploy" check passes before environment promotion              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Consumer-Side Tests (Frontend → Pact)

### 1.1 Setup (Angular + Pact-JS)

```typescript
// pact.setup.ts
import { PactV4, MatchersV3 } from '@pact-foundation/pact';
import path from 'path';

export const provider = new PactV4({
  consumer: 'AdminPortal',
  provider: 'BackendApis',
  dir: path.resolve(process.cwd(), 'pacts'),
  logLevel: 'warn'
});

// Shared matchers
export const M = MatchersV3;
```

### 1.2 Consumer Contract Tests

```typescript
// exemption.pact.spec.ts
import { provider, M } from '../pact.setup';
import { ExemptionService } from '../../services/exemption.service';
import { HttpClient, HttpClientModule } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';

describe('Exemption API Contract (Consumer)', () => {

  describe('GET /api/app/user-request-exemption', () => {
    it('returns paginated list of exemptions', async () => {
      await provider
        .addInteraction()
        .given('exemptions exist')
        .uponReceiving('a request to list exemptions')
        .withRequest('GET', '/api/app/user-request-exemption', (builder) => {
          builder.query({ MaxResultCount: '10', SkipCount: '0' });
          builder.headers({ Authorization: M.regex(/Bearer .+/, 'Bearer test-token') });
        })
        .willRespondWith(200, (builder) => {
          builder.headers({ 'Content-Type': 'application/json' });
          builder.jsonBody({
            totalCount: M.integer(5),
            items: M.eachLike({
              id: M.uuid(),
              nationalId: M.regex(/^\d{10}$/, '1234567890'),
              exemptedRequestCount: M.integer(5),
              submittedRequests: M.integer(2),
              creationTime: M.iso8601DateTimeWithMillis(),
              creatorId: M.uuid(),
              notes: M.like('optional notes')
            })
          });
        })
        .executeTest(async (mockServer) => {
          TestBed.configureTestingModule({
            imports: [HttpClientModule],
            providers: [
              ExemptionService,
              { provide: 'API_BASE_URL', useValue: mockServer.url }
            ]
          });

          const service = TestBed.inject(ExemptionService);
          const result = await firstValueFrom(
            service.getAll({ maxResultCount: 10, skipCount: 0 })
          );

          expect(result.totalCount).toBeGreaterThanOrEqual(0);
          expect(result.items).toBeInstanceOf(Array);
          expect(result.items[0]).toHaveProperty('id');
          expect(result.items[0]).toHaveProperty('nationalId');
          expect(result.items[0]).toHaveProperty('exemptedRequestCount');
        });
    });
  });

  describe('POST /api/app/user-request-exemption', () => {
    it('creates a new exemption', async () => {
      await provider
        .addInteraction()
        .given('no existing exemption for NID 9876543210')
        .uponReceiving('a request to create an exemption')
        .withRequest('POST', '/api/app/user-request-exemption', (builder) => {
          builder.headers({
            'Content-Type': 'application/json',
            Authorization: M.regex(/Bearer .+/, 'Bearer test-token')
          });
          builder.jsonBody({
            nationalId: M.regex(/^\d{10}$/, '9876543210'),
            exemptedRequestCount: M.integer(5),
            notes: M.like('test notes')
          });
        })
        .willRespondWith(200, (builder) => {
          builder.jsonBody({
            id: M.uuid(),
            nationalId: '9876543210',
            exemptedRequestCount: 5,
            submittedRequests: 0,
            creationTime: M.iso8601DateTimeWithMillis(),
            creatorId: M.uuid()
          });
        })
        .executeTest(async (mockServer) => {
          TestBed.configureTestingModule({
            imports: [HttpClientModule],
            providers: [
              ExemptionService,
              { provide: 'API_BASE_URL', useValue: mockServer.url }
            ]
          });

          const service = TestBed.inject(ExemptionService);
          const result = await firstValueFrom(
            service.create({ nationalId: '9876543210', exemptedRequestCount: 5, notes: 'test' })
          );

          expect(result.id).toBeDefined();
          expect(result.nationalId).toBe('9876543210');
          expect(result.exemptedRequestCount).toBe(5);
        });
    });

    it('returns error for duplicate active exemption', async () => {
      await provider
        .addInteraction()
        .given('active exemption exists for NID 1111111111')
        .uponReceiving('a request to create duplicate exemption')
        .withRequest('POST', '/api/app/user-request-exemption', (builder) => {
          builder.headers({
            'Content-Type': 'application/json',
            Authorization: M.regex(/Bearer .+/, 'Bearer test-token')
          });
          builder.jsonBody({
            nationalId: '1111111111',
            exemptedRequestCount: M.integer(3)
          });
        })
        .willRespondWith(400, (builder) => {
          builder.jsonBody({
            error: {
              code: M.like(null),
              message: M.regex(/.*استثناء فعّال.*/, 'يوجد استثناء فعّال لهذا المستخدم'),
              details: M.like(null),
              validationErrors: M.like(null)
            }
          });
        })
        .executeTest(async (mockServer) => {
          TestBed.configureTestingModule({
            imports: [HttpClientModule],
            providers: [
              ExemptionService,
              { provide: 'API_BASE_URL', useValue: mockServer.url }
            ]
          });

          const service = TestBed.inject(ExemptionService);
          try {
            await firstValueFrom(
              service.create({ nationalId: '1111111111', exemptedRequestCount: 3 })
            );
            fail('Should have thrown');
          } catch (error: any) {
            expect(error.status).toBe(400);
            expect(error.error.error.message).toContain('استثناء');
          }
        });
    });
  });

  describe('DELETE /api/app/user-request-exemption/{id}', () => {
    it('deletes an exemption', async () => {
      const exemptionId = '550e8400-e29b-41d4-a716-446655440000';

      await provider
        .addInteraction()
        .given(`exemption ${exemptionId} exists`)
        .uponReceiving('a request to delete an exemption')
        .withRequest('DELETE', `/api/app/user-request-exemption/${exemptionId}`, (builder) => {
          builder.headers({ Authorization: M.regex(/Bearer .+/, 'Bearer test-token') });
        })
        .willRespondWith(204)
        .executeTest(async (mockServer) => {
          TestBed.configureTestingModule({
            imports: [HttpClientModule],
            providers: [
              ExemptionService,
              { provide: 'API_BASE_URL', useValue: mockServer.url }
            ]
          });

          const service = TestBed.inject(ExemptionService);
          await firstValueFrom(service.delete(exemptionId));
          // No error = success
        });
    });
  });
});
```

---

## 2. Provider-Side Verification (.NET)

### 2.1 Provider Verification Test

```csharp
// ExemptionPactVerificationTests.cs
public class ExemptionPactVerificationTests : IClassFixture<TestWebApplicationFactory>
{
    private readonly TestWebApplicationFactory _factory;
    private readonly ITestOutputHelper _output;

    public ExemptionPactVerificationTests(
        TestWebApplicationFactory factory,
        ITestOutputHelper output)
    {
        _factory = factory;
        _output = output;
    }

    [Fact]
    public async Task VerifyAdminPortalContract()
    {
        // Setup provider states
        var config = new PactVerifierConfig
        {
            Outputters = new List<IOutput> { new XUnitOutput(_output) },
            LogLevel = PactLogLevel.Information
        };

        using var server = _factory.CreateDefaultClient().BaseAddress;

        var verifier = new PactVerifier("BackendApis", config);

        verifier
            .WithHttpEndpoint(server)
            .WithPactBrokerSource(new Uri(Environment.GetEnvironmentVariable("PACT_BROKER_URL")
                ?? "http://localhost:9292"), options =>
            {
                options.ConsumerVersionSelectors(new ConsumerVersionSelector
                {
                    MainBranch = true,
                    DeployedOrReleased = true
                });
                options.PublishResults(
                    Environment.GetEnvironmentVariable("GIT_COMMIT") ?? "local",
                    Environment.GetEnvironmentVariable("GIT_BRANCH") ?? "local");
                options.EnablePending();
                options.IncludeWipPactsSince(new DateTime(2024, 1, 1));
            })
            .WithProviderStateUrl(new Uri(server, "/api/pact/provider-states"))
            .Verify();
    }
}
```

### 2.2 Provider State Handler

```csharp
// PactProviderStatesController.cs
[ApiController]
[Route("api/pact/provider-states")]
public class PactProviderStatesController : ControllerBase
{
    private readonly MainDbContext _db;

    public PactProviderStatesController(MainDbContext db) => _db = db;

    [HttpPost]
    public async Task<IActionResult> SetupState([FromBody] ProviderState state)
    {
        switch (state.State)
        {
            case "exemptions exist":
                await SeedExemptions();
                break;

            case "no existing exemption for NID 9876543210":
                await ClearExemptionsForNid("9876543210");
                break;

            case "active exemption exists for NID 1111111111":
                await SeedActiveExemption("1111111111");
                break;

            case var s when s.StartsWith("exemption ") && s.EndsWith(" exists"):
                var id = Guid.Parse(s.Replace("exemption ", "").Replace(" exists", ""));
                await SeedExemptionWithId(id);
                break;

            default:
                return BadRequest($"Unknown provider state: {state.State}");
        }

        return Ok();
    }

    private async Task SeedExemptions()
    {
        if (!await _db.UserRequestExemptions.AnyAsync())
        {
            _db.UserRequestExemptions.AddRange(
                Enumerable.Range(1, 5).Select(i => new UserRequestExemption
                {
                    Id = Guid.NewGuid(),
                    NationalId = $"100000000{i}",
                    ExemptedRequestCount = i + 1,
                    SubmittedRequests = i > 2 ? 1 : 0,
                    CreatedBy = Guid.NewGuid()
                }));
            await _db.SaveChangesAsync();
        }
    }

    private async Task ClearExemptionsForNid(string nid)
    {
        var existing = await _db.UserRequestExemptions
            .Where(e => e.NationalId == nid)
            .ToListAsync();
        _db.UserRequestExemptions.RemoveRange(existing);
        await _db.SaveChangesAsync();
    }

    private async Task SeedActiveExemption(string nid)
    {
        if (!await _db.UserRequestExemptions.AnyAsync(e => e.NationalId == nid))
        {
            _db.UserRequestExemptions.Add(new UserRequestExemption
            {
                Id = Guid.NewGuid(),
                NationalId = nid,
                ExemptedRequestCount = 5,
                SubmittedRequests = 2,
                CreatedBy = Guid.NewGuid()
            });
            await _db.SaveChangesAsync();
        }
    }

    private async Task SeedExemptionWithId(Guid id)
    {
        if (!await _db.UserRequestExemptions.AnyAsync(e => e.Id == id))
        {
            _db.UserRequestExemptions.Add(new UserRequestExemption
            {
                Id = id,
                NationalId = "5550000001",
                ExemptedRequestCount = 3,
                CreatedBy = Guid.NewGuid()
            });
            await _db.SaveChangesAsync();
        }
    }
}

public class ProviderState
{
    public string State { get; set; } = string.Empty;
    public Dictionary<string, object> Params { get; set; } = new();
}
```

---

## 3. Pact Broker Integration

### 3.1 Broker Setup (Docker Compose)

```yaml
# docker-compose.pact-broker.yaml
version: '3.8'
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: pact
      POSTGRES_PASSWORD: pact_password
      POSTGRES_DB: pact_broker
    volumes:
      - pact-db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pact"]
      interval: 5s
      timeout: 5s
      retries: 5

  pact-broker:
    image: pactfoundation/pact-broker:latest
    ports:
      - "9292:9292"
    environment:
      PACT_BROKER_DATABASE_URL: postgres://pact:pact_password@postgres/pact_broker
      PACT_BROKER_BASE_URL: https://pact-broker.internal.company.com
      PACT_BROKER_LOG_LEVEL: INFO
      PACT_BROKER_ALLOW_PUBLIC_READ: "true"
      PACT_BROKER_WEBHOOK_RETRY_SCHEDULE: "10 60 120 300 600"
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  pact-db:
```

### 3.2 "Can I Deploy?" Gate

```yaml
# Pipeline step: Can I Deploy check
- bash: |
    # Check if this version can be safely deployed
    pact-broker can-i-deploy \
      --pacticipant "BackendApis" \
      --version "$(Build.BuildNumber)" \
      --to-environment "Test" \
      --broker-base-url "$(PACT_BROKER_URL)" \
      --broker-token "$(PACT_BROKER_TOKEN)" \
      --retry-while-unknown=12 \
      --retry-interval=10

    if [ $? -ne 0 ]; then
      echo "##[error]Contract verification failed. Cannot deploy."
      echo "##[error]Check Pact Broker for details: $(PACT_BROKER_URL)"
      exit 1
    fi
  displayName: 'Can I Deploy? (Contract Gate)'
  failOnStderr: false
```

### 3.3 Webhook Configuration

```json
// Pact Broker webhook: trigger provider verification on contract change
{
  "description": "Trigger provider verification when consumer publishes new pact",
  "provider": { "name": "BackendApis" },
  "events": [
    { "name": "contract_content_changed" },
    { "name": "contract_requiring_verification_published" }
  ],
  "request": {
    "method": "POST",
    "url": "https://dev.azure.com/{org}/{project}/_apis/pipelines/{pipelineId}/runs?api-version=7.0",
    "headers": {
      "Content-Type": "application/json",
      "Authorization": "Basic ${AZURE_DEVOPS_TOKEN}"
    },
    "body": {
      "templateParameters": {
        "PACT_CONSUMER": "${pactbroker.consumerName}",
        "PACT_PROVIDER": "${pactbroker.providerName}",
        "PACT_CONSUMER_VERSION": "${pactbroker.consumerVersionNumber}",
        "PACT_URL": "${pactbroker.pactUrl}"
      }
    }
  }
}
```

---

## 4. Bi-Directional Contract Testing (OpenAPI-Based)

For simpler setups without full Pact broker, use **bi-directional** contracts:

```
┌─────────────────┐              ┌─────────────────┐
│   Consumer      │              │   Provider      │
│                 │              │                 │
│  Records actual │              │  Generates      │
│  API calls as   │              │  OpenAPI spec   │
│  "consumer      │              │  from code      │
│   contract"     │              │  (Swashbuckle)  │
└────────┬────────┘              └────────┬────────┘
         │                                │
         ▼                                ▼
┌─────────────────────────────────────────────────────┐
│              PACT BROKER                             │
│                                                      │
│  Compares consumer expectations against provider    │
│  OpenAPI spec. If consumer expects a field that     │
│  doesn't exist in spec → FAIL.                      │
└─────────────────────────────────────────────────────┘
```

### 4.1 Provider: Publish OpenAPI Spec

```yaml
# In provider CI pipeline
- bash: |
    # Start app, extract swagger
    dotnet run --project src/Api.Host &
    sleep 10
    curl -o swagger.json http://localhost:5000/swagger/v1/swagger.json
    kill %1

    # Publish to Pact Broker
    pact-broker publish-provider-contract \
      swagger.json \
      --provider "BackendApis" \
      --provider-app-version "$(Build.BuildNumber)" \
      --branch "$(Build.SourceBranchName)" \
      --content-type "application/json" \
      --verification-exit-code=0 \
      --verification-results="swagger-self-verification.txt" \
      --verification-results-content-type="text/plain" \
      --broker-base-url "$(PACT_BROKER_URL)" \
      --broker-token "$(PACT_BROKER_TOKEN)"
  displayName: 'Publish Provider Contract (OpenAPI)'
```

### 4.2 Consumer: Record Interactions

```typescript
// In consumer test, use MSW or Playwright to record actual API calls
import { setupRecording } from '@pactflow/bi-directional-consumer';

const recording = setupRecording({
  consumer: 'AdminPortal',
  provider: 'BackendApis',
  outputDir: './pacts'
});

// Your existing E2E or integration tests automatically record
// the actual HTTP requests made by the frontend
```

---

## 5. Multi-Consumer Matrix

For systems with multiple consumers:

```
                    ┌─────────────────┐
                    │   BackendApis   │
                    │   (Provider)    │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼────┐ ┌──────▼──────┐ ┌────▼────────┐
     │ AdminPortal │ │PublicPortal │ │ MobileApp  │
     │ (Consumer)  │ │ (Consumer)  │ │ (Consumer) │
     └─────────────┘ └─────────────┘ └────────────┘
```

Each consumer publishes their own contract. Provider must verify ALL. If provider changes break ANY consumer → deploy blocked.

---

## 6. Contract Evolution & Versioning

### 6.1 Safe Changes (Non-Breaking)

| Change | Safe? | Why |
|--------|:-----:|-----|
| Add new optional field to response | ✅ | Consumers ignore unknown fields |
| Add new endpoint | ✅ | No existing consumer uses it |
| Add new optional query parameter | ✅ | Existing requests still work |
| Relax validation (accept more) | ✅ | Existing valid requests still valid |
| Add new enum value to response | ⚠️ | Only if consumers handle unknown values |

### 6.2 Breaking Changes (Detected by Contracts)

| Change | Breaking? | Detection |
|--------|:---------:|-----------|
| Remove field from response | ❌ | Consumer expects field → contract fails |
| Rename field | ❌ | Consumer expects old name → fails |
| Change field type | ❌ | Matcher type mismatch → fails |
| Remove endpoint | ❌ | Consumer uses endpoint → fails |
| Add required field to request | ❌ | Consumer doesn't send → 400 |
| Tighten validation | ❌ | Consumer's valid input rejected |

### 6.3 Migration Strategy for Breaking Changes

```
1. Add new field alongside old (deprecated) field
2. Publish new provider contract with both fields
3. All consumers verify → still pass (old field exists)
4. Update consumers one by one to use new field
5. Once ALL consumers migrated → remove old field
6. Provider contract drops old field → no consumer references it → pass
```

---

## 7. Pipeline Integration

```yaml
# Consumer CI Pipeline
stages:
- stage: ConsumerTests
  jobs:
  - job: ContractTests
    steps:
    - bash: npm run test:pact
      displayName: 'Generate Consumer Contracts'
    
    - bash: |
        pact-broker publish ./pacts \
          --consumer-app-version "$(Build.BuildNumber)" \
          --branch "$(Build.SourceBranchName)" \
          --broker-base-url "$(PACT_BROKER_URL)" \
          --broker-token "$(PACT_BROKER_TOKEN)" \
          --tag "$(Build.SourceBranchName)"
      displayName: 'Publish Contracts to Broker'

    - bash: |
        pact-broker can-i-deploy \
          --pacticipant "AdminPortal" \
          --version "$(Build.BuildNumber)" \
          --to-environment "Test" \
          --broker-base-url "$(PACT_BROKER_URL)" \
          --broker-token "$(PACT_BROKER_TOKEN)"
      displayName: 'Can I Deploy? Gate'

# Provider CI Pipeline
stages:
- stage: ProviderVerification
  jobs:
  - job: VerifyContracts
    steps:
    - bash: dotnet test tests/Pact.Verification.Tests/
      displayName: 'Verify All Consumer Contracts'

    - bash: |
        pact-broker can-i-deploy \
          --pacticipant "BackendApis" \
          --version "$(Build.BuildNumber)" \
          --to-environment "Test" \
          --broker-base-url "$(PACT_BROKER_URL)" \
          --broker-token "$(PACT_BROKER_TOKEN)"
      displayName: 'Can I Deploy? Gate'
```

---

## 8. Metrics & Monitoring

| Metric | Target | Alert |
|--------|--------|-------|
| Contract verification pass rate | 100% | Any failure blocks deploy |
| Time to verify all contracts | < 5 minutes | > 10 min → investigate |
| Pending contracts (WIP) | < 3 | > 5 → consumer team action needed |
| Contract age without verification | < 7 days | > 14 days → stale alert |
| Breaking changes detected/month | Track (no target) | Trend analysis |

---

*Previous: [02-API-INTEGRATION-TESTING.md](02-API-INTEGRATION-TESTING.md) · Next: [04-E2E-UI-TESTING.md](04-E2E-UI-TESTING.md)*
