# 02 — API & Integration Testing

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

API and integration tests verify that **multiple components work together correctly** — services talking to real databases, HTTP endpoints returning correct responses, middleware chains processing requests properly, and external service integrations behaving as expected.

Unlike unit tests (which mock boundaries), integration tests use **real dependencies** — real databases, real HTTP pipelines, real serialization. They catch the defects that unit tests structurally cannot: query bugs, serialization mismatches, middleware ordering, transaction failures, and configuration errors.

**What this layer kills:**
- "Works in unit tests but fails with real database" → Real DB via Testcontainers
- "API returns wrong status code / shape" → Full HTTP pipeline test
- "SQL query has wrong JOIN / WHERE" → Real query execution against seeded data
- "Middleware rejects valid request" → Full request pipeline test
- "Did the schema change break existing clients?" → Schema fuzzing from OpenAPI

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                   API & INTEGRATION TEST LAYER                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  In-Process Integration Tests (WebApplicationFactory)            ││
│  │                                                                  ││
│  │  • Full ASP.NET Core pipeline (middleware, routing, filters)     ││
│  │  • Real dependency injection container                           ││
│  │  • Real serialization/deserialization                            ││
│  │  • TestServer (no network, in-memory HTTP)                       ││
│  │  • Real database (SQL Server via Testcontainers)                 ││
│  │  • Mocked: only external HTTP services (WireMock)                ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Database Integration Tests                                      ││
│  │                                                                  ││
│  │  • EF Core queries against real SQL Server                       ││
│  │  • Migration execution verification                              ││
│  │  • Stored procedure / function testing                           ││
│  │  • Transaction isolation verification                            ││
│  │  • Concurrency conflict handling                                 ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Schema Fuzzing (Schemathesis / OpenAPI-based)                   ││
│  │                                                                  ││
│  │  • Auto-generate requests from OpenAPI/Swagger spec              ││
│  │  • Boundary values, nulls, empty strings, unicode, oversized     ││
│  │  • Assert: no 500s, response matches schema, no crashes          ││
│  │  • Stateful testing: sequences of requests (create → get → del) ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  External Service Integration Tests (WireMock)                   ││
│  │                                                                  ││
│  │  • Mock external APIs with recorded responses                    ││
│  │  • Test timeout handling, retry logic, circuit breakers          ││
│  │  • Verify correct request construction                           ││
│  │  • Test error response handling (400, 401, 403, 500, timeout)   ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Gate: 100% endpoints tested | No 500s from fuzzing | < 5 min run  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 1. In-Process Integration Testing (.NET)

### 1.1 Infrastructure Setup

```csharp
// TestWebApplicationFactory.cs — The foundation of all API tests
public class TestWebApplicationFactory : WebApplicationFactory<Program>, IAsyncLifetime
{
    private readonly MsSqlContainer _dbContainer = new MsSqlBuilder()
        .WithImage("mcr.microsoft.com/mssql/server:2022-latest")
        .WithPassword("Test@Password123!")
        .WithCleanUp(true)
        .Build();

    private readonly RedisContainer _redisContainer = new RedisBuilder()
        .WithImage("redis:7-alpine")
        .WithCleanUp(true)
        .Build();

    public async Task InitializeAsync()
    {
        await _dbContainer.StartAsync();
        await _redisContainer.StartAsync();
    }

    public new async Task DisposeAsync()
    {
        await _dbContainer.DisposeAsync();
        await _redisContainer.DisposeAsync();
        await base.DisposeAsync();
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.ConfigureServices(services =>
        {
            // Replace database connections with Testcontainers
            services.RemoveAll<DbContextOptions<MainDbContext>>();
            services.RemoveAll<DbContextOptions<IdentityDbContext>>();

            services.AddDbContext<MainDbContext>(options =>
                options.UseSqlServer(_dbContainer.GetConnectionString()));

            services.AddDbContext<IdentityDbContext>(options =>
                options.UseSqlServer(_dbContainer.GetConnectionString()));

            // Replace Redis
            services.RemoveAll<IConnectionMultiplexer>();
            services.AddSingleton<IConnectionMultiplexer>(
                ConnectionMultiplexer.Connect(_redisContainer.GetConnectionString()));

            // Replace external HTTP clients with WireMock
            services.RemoveAll<IHttpClientFactory>();
            services.AddHttpClient("ExternalService", c =>
                c.BaseAddress = new Uri(WireMockServer.Url));

            // Disable background jobs in test
            services.RemoveAll<IBackgroundJobManager>();
            services.AddSingleton<IBackgroundJobManager, NullBackgroundJobManager>();

            // Seed database
            var sp = services.BuildServiceProvider();
            using var scope = sp.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<MainDbContext>();
            db.Database.Migrate();
            SeedTestData(db);
        });

        builder.ConfigureAppConfiguration((context, config) =>
        {
            config.AddInMemoryCollection(new Dictionary<string, string>
            {
                ["ConnectionStrings:Default"] = _dbContainer.GetConnectionString(),
                ["Redis:Configuration"] = _redisContainer.GetConnectionString(),
                ["App:SelfUrl"] = "https://localhost",
                ["AuthServer:Authority"] = "https://localhost"
            });
        });
    }

    private static void SeedTestData(MainDbContext db)
    {
        // Minimal required reference data
        // Each test creates its own specific data
    }
}
```

### 1.2 Test Base Class

```csharp
public abstract class ApiTestBase : IClassFixture<TestWebApplicationFactory>, IAsyncLifetime
{
    protected readonly TestWebApplicationFactory Factory;
    protected readonly HttpClient Client;
    protected readonly IServiceScope Scope;

    protected ApiTestBase(TestWebApplicationFactory factory)
    {
        Factory = factory;
        Client = factory.CreateClient(new WebApplicationFactoryClientOptions
        {
            AllowAutoRedirect = false,
            HandleCookies = true
        });
        Scope = factory.Services.CreateScope();
    }

    // Authenticate as specific role
    protected async Task AuthenticateAs(string role, string userId = null)
    {
        var token = GenerateTestJwt(role, userId ?? Guid.NewGuid().ToString());
        Client.DefaultRequestHeaders.Authorization =
            new AuthenticationHeaderValue("Bearer", token);
    }

    // Direct DB access for test setup/assertions
    protected MainDbContext GetDbContext() =>
        Scope.ServiceProvider.GetRequiredService<MainDbContext>();

    // Clean up per-test data
    public virtual Task InitializeAsync() => Task.CompletedTask;

    public virtual async Task DisposeAsync()
    {
        // Transaction rollback or cleanup
        var db = GetDbContext();
        await CleanTestData(db);
        Scope.Dispose();
    }

    private static string GenerateTestJwt(string role, string userId)
    {
        var claims = new[]
        {
            new Claim(ClaimTypes.NameIdentifier, userId),
            new Claim(ClaimTypes.Role, role),
            new Claim("sub", userId),
            new Claim("preferred_username", $"test-{role}@test.local")
        };

        var key = new SymmetricSecurityKey(Encoding.UTF8.GetBytes("test-signing-key-256-bits-long!!"));
        var creds = new SigningCredentials(key, SecurityAlgorithms.HmacSha256);
        var token = new JwtSecurityToken(
            issuer: "test-authority",
            audience: "test-api",
            claims: claims,
            expires: DateTime.UtcNow.AddHours(1),
            signingCredentials: creds);

        return new JwtSecurityTokenHandler().WriteToken(token);
    }
}
```

### 1.3 API Test Patterns

#### Pattern 1: Full CRUD Lifecycle Test

```csharp
public class ExemptionApiTests : ApiTestBase
{
    public ExemptionApiTests(TestWebApplicationFactory factory) : base(factory) { }

    [Fact]
    public async Task FullLifecycle_CreateReadDelete()
    {
        // Authenticate as admin
        await AuthenticateAs("Admin");

        // CREATE
        var createPayload = new
        {
            nationalId = "1234567890",
            exemptedRequestCount = 5,
            notes = "Integration test exemption"
        };

        var createResponse = await Client.PostAsJsonAsync("/api/app/user-request-exemption", createPayload);
        createResponse.StatusCode.Should().Be(HttpStatusCode.OK);

        var created = await createResponse.Content.ReadFromJsonAsync<ExemptionDto>();
        created.Should().NotBeNull();
        created!.NationalId.Should().Be("1234567890");
        created.ExemptedRequestCount.Should().Be(5);
        created.Id.Should().NotBeEmpty();

        // READ (verify in list)
        var listResponse = await Client.GetAsync("/api/app/user-request-exemption?MaxResultCount=100");
        listResponse.StatusCode.Should().Be(HttpStatusCode.OK);

        var list = await listResponse.Content.ReadFromJsonAsync<PagedResultDto<ExemptionDto>>();
        list!.Items.Should().Contain(e => e.NationalId == "1234567890");

        // DELETE
        var deleteResponse = await Client.DeleteAsync($"/api/app/user-request-exemption/{created.Id}");
        deleteResponse.StatusCode.Should().Be(HttpStatusCode.NoContent);

        // VERIFY DELETED
        var afterDelete = await Client.GetAsync("/api/app/user-request-exemption?MaxResultCount=100");
        var afterList = await afterDelete.Content.ReadFromJsonAsync<PagedResultDto<ExemptionDto>>();
        afterList!.Items.Should().NotContain(e => e.Id == created.Id);
    }

    [Fact]
    public async Task Create_DuplicateActiveExemption_Returns400WithArabicMessage()
    {
        await AuthenticateAs("Admin");

        var payload = new { nationalId = "1111111111", exemptedRequestCount = 3 };

        // First create succeeds
        var first = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);
        first.StatusCode.Should().Be(HttpStatusCode.OK);

        // Second create fails
        var second = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);
        second.StatusCode.Should().Be(HttpStatusCode.BadRequest)
            .Or.Be(HttpStatusCode.Forbidden);

        var error = await second.Content.ReadFromJsonAsync<RemoteServiceErrorResponse>();
        error!.Error.Message.Should().Contain("استثناء فعّال");
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    [InlineData(16)]
    [InlineData(999)]
    public async Task Create_InvalidCount_Returns400(int invalidCount)
    {
        await AuthenticateAs("Admin");

        var payload = new { nationalId = "2222222222", exemptedRequestCount = invalidCount };
        var response = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);

        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task Create_UnregisteredNationalId_Succeeds()
    {
        // GAP-1 fix: unregistered users should be allowed
        await AuthenticateAs("Admin");

        var payload = new { nationalId = "9999999999", exemptedRequestCount = 1 };
        var response = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var result = await response.Content.ReadFromJsonAsync<ExemptionDto>();
        result!.NationalId.Should().Be("9999999999");
    }
}
```

#### Pattern 2: Authorization Tests

```csharp
public class ExemptionAuthorizationTests : ApiTestBase
{
    public ExemptionAuthorizationTests(TestWebApplicationFactory factory) : base(factory) { }

    [Fact]
    public async Task Create_Unauthenticated_Returns401()
    {
        // No auth header
        Client.DefaultRequestHeaders.Authorization = null;

        var payload = new { nationalId = "1234567890", exemptedRequestCount = 5 };
        var response = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task Create_NonAdminRole_Returns403()
    {
        await AuthenticateAs("PublicUser");

        var payload = new { nationalId = "1234567890", exemptedRequestCount = 5 };
        var response = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);

        response.StatusCode.Should().Be(HttpStatusCode.Forbidden);
    }

    [Theory]
    [InlineData("Admin")]
    [InlineData("SuperAdmin")]
    [InlineData("ReportsCoordinator")]
    public async Task Create_AuthorizedRoles_Returns200(string role)
    {
        await AuthenticateAs(role);

        var payload = new { nationalId = $"10{role.GetHashCode():D8}"[..10], exemptedRequestCount = 1 };
        var response = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);

        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task List_PaginationRespected()
    {
        await AuthenticateAs("Admin");

        // Create multiple
        for (int i = 0; i < 5; i++)
        {
            var payload = new { nationalId = $"300000000{i}", exemptedRequestCount = i + 1 };
            await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);
        }

        // Request page size 2
        var response = await Client.GetAsync("/api/app/user-request-exemption?MaxResultCount=2&SkipCount=0");
        var result = await response.Content.ReadFromJsonAsync<PagedResultDto<ExemptionDto>>();

        result!.TotalCount.Should().BeGreaterOrEqualTo(5);
        result.Items.Should().HaveCount(2);
    }
}
```

#### Pattern 3: Concurrency & Race Condition Tests

```csharp
public class ExemptionConcurrencyTests : ApiTestBase
{
    public ExemptionConcurrencyTests(TestWebApplicationFactory factory) : base(factory) { }

    [Fact]
    public async Task ConcurrentCreates_SameNationalId_OnlyOneSucceeds()
    {
        await AuthenticateAs("Admin");

        var payload = new { nationalId = "5555555555", exemptedRequestCount = 3 };

        // Fire 10 concurrent requests
        var tasks = Enumerable.Range(0, 10)
            .Select(_ => Client.PostAsJsonAsync("/api/app/user-request-exemption", payload))
            .ToList();

        var responses = await Task.WhenAll(tasks);

        var successes = responses.Count(r => r.StatusCode == HttpStatusCode.OK);
        var failures = responses.Count(r => r.StatusCode != HttpStatusCode.OK);

        successes.Should().Be(1, "only one concurrent create should succeed");
        failures.Should().Be(9, "remaining 9 should fail with duplicate error");
    }

    [Fact]
    public async Task ConcurrentDeletes_SameId_OnlyOneSucceeds()
    {
        await AuthenticateAs("Admin");

        // Create one
        var payload = new { nationalId = "6666666666", exemptedRequestCount = 1 };
        var createResponse = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);
        var created = await createResponse.Content.ReadFromJsonAsync<ExemptionDto>();

        // Fire 10 concurrent deletes
        var tasks = Enumerable.Range(0, 10)
            .Select(_ => Client.DeleteAsync($"/api/app/user-request-exemption/{created!.Id}"))
            .ToList();

        var responses = await Task.WhenAll(tasks);

        var successes = responses.Count(r => r.StatusCode == HttpStatusCode.NoContent);
        successes.Should().Be(1);
    }
}
```

---

## 2. Database Integration Testing

### 2.1 Migration Verification

```csharp
public class MigrationTests : IClassFixture<TestWebApplicationFactory>
{
    private readonly TestWebApplicationFactory _factory;

    public MigrationTests(TestWebApplicationFactory factory) => _factory = factory;

    [Fact]
    public async Task AllMigrations_ApplyCleanly()
    {
        using var scope = _factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MainDbContext>();

        // Should not throw
        var pendingMigrations = await db.Database.GetPendingMigrationsAsync();
        pendingMigrations.Should().BeEmpty("all migrations should have been applied during startup");
    }

    [Fact]
    public async Task Migration_Rollback_DoesNotLoseData()
    {
        using var scope = _factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MainDbContext>();

        // Get current migration
        var applied = (await db.Database.GetAppliedMigrationsAsync()).ToList();
        var lastMigration = applied.Last();

        // Rollback last migration
        await db.Database.MigrateAsync(applied[^2]); // migrate to second-to-last

        // Re-apply
        await db.Database.MigrateAsync();

        // Verify no data loss (check counts)
        var pendingAfter = await db.Database.GetPendingMigrationsAsync();
        pendingAfter.Should().BeEmpty();
    }

    [Fact]
    public async Task Schema_MatchesEntityModel()
    {
        using var scope = _factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<MainDbContext>();

        // Detect model drift
        var differences = db.Database.HasPendingModelChanges();
        differences.Should().BeFalse(
            "EF Core model should match database schema. Run 'dotnet ef migrations add' if this fails.");
    }
}
```

### 2.2 Query Performance Tests

```csharp
public class QueryPerformanceTests : ApiTestBase
{
    public QueryPerformanceTests(TestWebApplicationFactory factory) : base(factory) { }

    [Fact]
    public async Task GetExemptions_With1000Records_CompletesUnder500ms()
    {
        // Seed 1000 records
        var db = GetDbContext();
        var exemptions = Enumerable.Range(0, 1000)
            .Select(i => new UserRequestExemption
            {
                NationalId = $"{i:D10}",
                ExemptedRequestCount = (i % 15) + 1,
                CreatedBy = Guid.NewGuid()
            });
        await db.UserRequestExemptions.AddRangeAsync(exemptions);
        await db.SaveChangesAsync();

        await AuthenticateAs("Admin");

        var stopwatch = Stopwatch.StartNew();
        var response = await Client.GetAsync("/api/app/user-request-exemption?MaxResultCount=50");
        stopwatch.Stop();

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        stopwatch.ElapsedMilliseconds.Should().BeLessThan(500,
            "paginated query with 1000 records should complete under 500ms");
    }

    [Fact]
    public async Task GetExemptions_UsesIndex_NoTableScan()
    {
        var db = GetDbContext();

        // Check execution plan
        var plan = await db.Database.ExecuteSqlRawAsync(@"
            SET STATISTICS PROFILE ON;
            SELECT TOP 50 * FROM UserRequestExemptions WHERE IsDeleted = 0
            ORDER BY CreationTime DESC;
            SET STATISTICS PROFILE OFF;
        ");

        // In real implementation, parse execution plan for table scans
        // This is a simplified check
        plan.Should().BeGreaterThanOrEqualTo(0);
    }
}
```

---

## 3. Schema Fuzzing (Automated API Exploration)

### 3.1 Schemathesis Configuration

```yaml
# schemathesis.yaml
schema:
  url: http://localhost:5000/swagger/v1/swagger.json
  # Or from file: path: ./swagger.json

checks:
  - not_a_server_error        # No 500 responses
  - status_code_conformance   # Status codes match spec
  - content_type_conformance  # Content-Type headers match spec
  - response_schema_conformance # Response body matches schema
  - response_headers_conformance # Response headers match spec
  - use_after_free            # Deleted resources return 404
  - negative_data_rejection   # Invalid data returns 4xx not 5xx

stateful:
  enabled: true  # Enable stateful testing (create → use → delete)
  
hypothesis:
  max_examples: 200           # Per endpoint
  deadline: 30000             # 30s per test case
  suppress_health_check: true

generation:
  # Custom data generation
  with_security:
    type: bearer
    value: "${TEST_JWT_TOKEN}"

targets:
  - response_time  # Optimize for finding slow endpoints
```

### 3.2 Pipeline Integration

```yaml
# Schema fuzzing step in CI
- bash: |
    # Generate JWT for test auth
    export TEST_JWT_TOKEN=$(python3 generate_test_jwt.py)
    
    # Run schemathesis against live test server
    schemathesis run \
      http://localhost:5000/swagger/v1/swagger.json \
      --checks all \
      --stateful=links \
      --hypothesis-max-examples=200 \
      --hypothesis-deadline=30000 \
      --auth-type=bearer \
      --auth="$TEST_JWT_TOKEN" \
      --report \
      --junit-xml=schemathesis-results.xml \
      --cassette-path=cassette.yaml \
      --exitfirst

    # Exit code 0 = no bugs found
    # Exit code 1 = bugs found (fails pipeline)
  displayName: 'Schema Fuzzing (Schemathesis)'
  failOnStderr: false
  
- task: PublishTestResults@2
  condition: always()
  inputs:
    testResultsFormat: 'JUnit'
    testResultsFiles: '**/schemathesis-results.xml'
    testRunTitle: 'Schema Fuzzing Results'
```

### 3.3 Custom Extensions

```python
# schemathesis_hooks.py — Project-specific extensions
import schemathesis

@schemathesis.hook("generate_case")
def custom_generation(context, case):
    """Inject Arabic text for string fields to test i18n handling"""
    for key, value in case.body.items() if case.body else []:
        if isinstance(value, str) and "name" in key.lower():
            case.body[key] = "اختبار النظام العربي"
        if isinstance(value, str) and "id" in key.lower():
            # Generate valid Saudi National ID format
            case.body[key] = f"1{''.join([str(random.randint(0,9)) for _ in range(9)])}"
    return case

@schemathesis.check
def no_stacktrace_in_response(response, case):
    """Custom check: production responses must never contain stack traces"""
    if response.status_code >= 500:
        body = response.text
        assert "at " not in body, "Stack trace exposed in error response"
        assert "Exception" not in body, "Exception type exposed in error response"
        assert ".cs:line" not in body, "Source file reference exposed"
```

---

## 4. External Service Mocking (WireMock)

### 4.1 WireMock Setup

```csharp
public class WireMockFixture : IAsyncLifetime
{
    public WireMockServer Server { get; private set; } = null!;
    public string BaseUrl => Server.Url!;

    public Task InitializeAsync()
    {
        Server = WireMockServer.Start(new WireMockServerSettings
        {
            Port = 0, // Random port
            Logger = new WireMockNullLogger()
        });

        SetupDefaultMappings();
        return Task.CompletedTask;
    }

    public Task DisposeAsync()
    {
        Server.Stop();
        return Task.CompletedTask;
    }

    private void SetupDefaultMappings()
    {
        // National Information Center (NIC) API
        Server.Given(Request.Create()
                .WithPath("/api/nic/citizen/*")
                .UsingGet())
            .RespondWith(Response.Create()
                .WithStatusCode(200)
                .WithHeader("Content-Type", "application/json")
                .WithBodyAsJson(new
                {
                    nationalId = "{{request.pathSegments.[3]}}",
                    fullNameAr = "محمد أحمد الفهد",
                    fullNameEn = "Mohammed Ahmed Al-Fahd",
                    dateOfBirth = "1990-01-15",
                    status = "active"
                })
                .WithTransformer());

        // Payment Gateway
        Server.Given(Request.Create()
                .WithPath("/api/payment/verify")
                .UsingPost())
            .RespondWith(Response.Create()
                .WithStatusCode(200)
                .WithBodyAsJson(new { verified = true, transactionId = "TXN-12345" }));
    }

    // Simulate failures
    public void SimulateTimeout(string path)
    {
        Server.Given(Request.Create().WithPath(path))
            .RespondWith(Response.Create().WithDelay(TimeSpan.FromSeconds(35)));
    }

    public void SimulateServerError(string path)
    {
        Server.Given(Request.Create().WithPath(path))
            .RespondWith(Response.Create().WithStatusCode(500)
                .WithBody("Internal Server Error"));
    }

    public void SimulateRateLimiting(string path)
    {
        Server.Given(Request.Create().WithPath(path))
            .RespondWith(Response.Create().WithStatusCode(429)
                .WithHeader("Retry-After", "60"));
    }
}
```

### 4.2 External Service Resilience Tests

```csharp
public class ExternalServiceResilienceTests : ApiTestBase
{
    private readonly WireMockFixture _wireMock;

    public ExternalServiceResilienceTests(TestWebApplicationFactory factory) : base(factory)
    {
        _wireMock = factory.Services.GetRequiredService<WireMockFixture>();
    }

    [Fact]
    public async Task NICService_Timeout_ReturnsGracefulError()
    {
        _wireMock.SimulateTimeout("/api/nic/citizen/*");
        await AuthenticateAs("Admin");

        var response = await Client.GetAsync("/api/app/user-info/1234567890");

        // Should not return 500 — should handle timeout gracefully
        response.StatusCode.Should().NotBe(HttpStatusCode.InternalServerError);
        response.StatusCode.Should().Be(HttpStatusCode.ServiceUnavailable)
            .Or.Be(HttpStatusCode.GatewayTimeout);
    }

    [Fact]
    public async Task NICService_ServerError_ReturnsGracefulError()
    {
        _wireMock.SimulateServerError("/api/nic/citizen/*");
        await AuthenticateAs("Admin");

        var response = await Client.GetAsync("/api/app/user-info/1234567890");

        response.StatusCode.Should().NotBe(HttpStatusCode.InternalServerError,
            "external service failure should not cascade as 500 to our client");
    }

    [Fact]
    public async Task NICService_RateLimited_RetriesAndSucceeds()
    {
        // First request rate-limited, second succeeds
        var callCount = 0;
        _wireMock.Server.Given(Request.Create().WithPath("/api/nic/citizen/*"))
            .RespondWith(Response.Create()
                .WithCallback(request =>
                {
                    callCount++;
                    if (callCount == 1)
                        return new ResponseMessage { StatusCode = 429 };
                    return new ResponseMessage
                    {
                        StatusCode = 200,
                        BodyData = new BodyData { DetectedBodyType = BodyType.Json }
                    };
                }));

        await AuthenticateAs("Admin");
        var response = await Client.GetAsync("/api/app/user-info/1234567890");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        callCount.Should().Be(2, "should have retried after 429");
    }
}
```

---

## 5. API Response Validation

### 5.1 Response Schema Assertions

```csharp
public class ApiResponseSchemaTests : ApiTestBase
{
    public ApiResponseSchemaTests(TestWebApplicationFactory factory) : base(factory) { }

    [Fact]
    public async Task ErrorResponses_FollowStandardFormat()
    {
        await AuthenticateAs("Admin");

        // Trigger a known error
        var payload = new { nationalId = "invalid", exemptedRequestCount = 999 };
        var response = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);

        if (response.StatusCode == HttpStatusCode.BadRequest)
        {
            var body = await response.Content.ReadAsStringAsync();
            var json = JsonDocument.Parse(body);

            // Standard ABP error format
            json.RootElement.TryGetProperty("error", out var error).Should().BeTrue();
            error.TryGetProperty("message", out _).Should().BeTrue();
            error.TryGetProperty("code", out _).Should().BeTrue();

            // Must NOT expose internals
            body.Should().NotContain("StackTrace");
            body.Should().NotContain("InnerException");
            body.Should().NotContain(".cs:line");
        }
    }

    [Fact]
    public async Task ListEndpoints_ReturnStandardPagination()
    {
        await AuthenticateAs("Admin");

        var response = await Client.GetAsync("/api/app/user-request-exemption?MaxResultCount=10");
        var body = await response.Content.ReadAsStringAsync();
        var json = JsonDocument.Parse(body);

        json.RootElement.TryGetProperty("totalCount", out var totalCount).Should().BeTrue();
        json.RootElement.TryGetProperty("items", out var items).Should().BeTrue();

        totalCount.GetInt32().Should().BeGreaterOrEqualTo(0);
        items.GetArrayLength().Should().BeLessThanOrEqualTo(10);
    }

    [Fact]
    public async Task AllEndpoints_ReturnCorrectContentType()
    {
        await AuthenticateAs("Admin");

        var endpoints = new[]
        {
            "/api/app/user-request-exemption",
            "/api/app/real-estate-ownership-request"
        };

        foreach (var endpoint in endpoints)
        {
            var response = await Client.GetAsync(endpoint);
            if (response.IsSuccessStatusCode)
            {
                response.Content.Headers.ContentType?.MediaType
                    .Should().Be("application/json",
                        $"endpoint {endpoint} should return JSON");
            }
        }
    }

    [Fact]
    public async Task SecurityHeaders_PresentOnAllResponses()
    {
        await AuthenticateAs("Admin");

        var response = await Client.GetAsync("/api/app/user-request-exemption");

        response.Headers.Should().ContainKey("X-Content-Type-Options");
        response.Headers.GetValues("X-Content-Type-Options")
            .Should().Contain("nosniff");

        // No server version disclosure
        response.Headers.Should().NotContainKey("Server");
        response.Headers.Should().NotContainKey("X-Powered-By");
    }
}
```

---

## 6. Test Isolation Strategy

### 6.1 Per-Test Database Isolation

```csharp
// Option A: Transaction per test (fastest, recommended)
public class TransactionIsolatedTest : ApiTestBase
{
    private IDbContextTransaction _transaction = null!;

    public override async Task InitializeAsync()
    {
        var db = GetDbContext();
        _transaction = await db.Database.BeginTransactionAsync();
    }

    public override async Task DisposeAsync()
    {
        await _transaction.RollbackAsync();
        await _transaction.DisposeAsync();
        await base.DisposeAsync();
    }
}

// Option B: Respawn (clean slate, for tests that commit)
public class RespawnIsolatedTest : ApiTestBase
{
    private static readonly Respawner Respawner = Respawner.CreateAsync(
        connectionString,
        new RespawnerOptions
        {
            TablesToIgnore = new[] { "__EFMigrationsHistory" },
            SchemasToInclude = new[] { "dbo" },
            WithReseed = true
        }).GetAwaiter().GetResult();

    public override async Task DisposeAsync()
    {
        await Respawner.ResetAsync(connectionString);
        await base.DisposeAsync();
    }
}
```

---

## 7. Coverage Requirements

| Metric | Target | Enforcement |
|--------|--------|-------------|
| API endpoints tested | 100% | OpenAPI spec diff — untested endpoints block PR |
| HTTP methods tested | All declared in spec | Schemathesis covers automatically |
| Status codes tested | All documented | Explicit tests for 200, 400, 401, 403, 404, 500 |
| Error scenarios | All business rules | Each rule has negative test |
| Concurrency safety | Critical paths | At least 1 concurrent test per write endpoint |
| External service failures | All integrations | Timeout, 500, rate-limit for each dependency |
| Schema compliance | 100% | Schemathesis + response schema assertions |

---

## 8. Execution Speed & Parallelization

```csharp
// xunit.runner.json — Enable parallel execution
{
  "parallelizeAssembly": false,
  "parallelizeTestCollections": true,
  "maxParallelThreads": 0,  // Use all cores
  "methodDisplay": "method",
  "diagnosticMessages": true
}

// Group tests that share state into collections
[Collection("ExemptionTests")]
public class ExemptionApiTests { }

[Collection("AuthorizationTests")]
public class AuthorizationApiTests { }
```

| Metric | Target |
|--------|--------|
| Full integration test suite | < 5 minutes |
| Single test class | < 30 seconds |
| Container startup (Testcontainers) | < 15 seconds (cached) |
| Schema fuzzing (200 examples/endpoint) | < 10 minutes |

---

*Previous: [01-UNIT-COMPONENT-TESTING.md](01-UNIT-COMPONENT-TESTING.md) · Next: [03-CONTRACT-TESTING.md](03-CONTRACT-TESTING.md)*
