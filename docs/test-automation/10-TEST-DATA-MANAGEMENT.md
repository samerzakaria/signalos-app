# 10 — Test Data Management

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

Test data management provides **reliable, reproducible, and isolated data** for every test layer — from unit tests through production synthetics. It eliminates the "works on my machine" problem and ensures tests never interfere with each other.

**What this layer kills:**
- "Tests pass locally but fail in CI because of missing seed data" → Deterministic data factories
- "Test A passes alone but fails when run after Test B" → Complete isolation per test
- "We can't test because we need production-like data" → Automated data generation
- "Test database accumulated garbage over months" → Auto-cleanup after every run
- "We accidentally used real PII in tests" → Synthetic data generators for sensitive fields

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TEST DATA ARCHITECTURE                                     │
│                                                                              │
│  ┌─ LAYER 1: In-Memory Factories ──────────────────────────────────────┐   │
│  │  • Builder pattern for entity creation                               │   │
│  │  • Default valid state, override specific fields                     │   │
│  │  • No database dependency (unit tests)                               │   │
│  │  • Bogus/AutoFixture for random valid data                           │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ LAYER 2: Database Seeders ─────────────────────────────────────────┐   │
│  │  • Reference data (lookup tables, roles, permissions)                │   │
│  │  • Executed once per test run (not per test)                          │   │
│  │  • Version-controlled alongside migrations                           │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ LAYER 3: Per-Test Data ────────────────────────────────────────────┐   │
│  │  • Created in test setup, cleaned in teardown                         │   │
│  │  • Unique identifiers prevent collision                               │   │
│  │  • Transaction rollback or Respawn for cleanup                        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ LAYER 4: Production-Like Datasets ────────────────────────────────┐   │
│  │  • Anonymized production snapshots (for performance tests)           │   │
│  │  • Statistical distribution matching                                  │   │
│  │  • Volume testing (1M+ records)                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  Rules: No real PII | Tests don't share state | Cleanup is automatic        │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Entity Factories (Builder Pattern)

### 1.1 .NET Entity Builders

```csharp
// TestData/Builders/ExemptionBuilder.cs
public class ExemptionBuilder
{
    private Guid _id = Guid.NewGuid();
    private string _nationalId = GenerateNationalId();
    private int _exemptedRequestCount = 5;
    private int _submittedRequests = 0;
    private Guid _creatorId = Guid.NewGuid();
    private DateTime _creationTime = DateTime.UtcNow;
    private bool _isDeleted = false;
    private string? _notes = null;

    public static ExemptionBuilder Default() => new();

    public ExemptionBuilder WithNationalId(string nid)
    {
        _nationalId = nid;
        return this;
    }

    public ExemptionBuilder WithCount(int count)
    {
        _exemptedRequestCount = count;
        return this;
    }

    public ExemptionBuilder WithSubmittedRequests(int count)
    {
        _submittedRequests = count;
        return this;
    }

    public ExemptionBuilder AsExpired()
    {
        _submittedRequests = _exemptedRequestCount;
        return this;
    }

    public ExemptionBuilder AsDeleted()
    {
        _isDeleted = true;
        return this;
    }

    public ExemptionBuilder WithNotes(string notes)
    {
        _notes = notes;
        return this;
    }

    public ExemptionBuilder CreatedBy(Guid userId)
    {
        _creatorId = userId;
        return this;
    }

    public ExemptionBuilder CreatedAt(DateTime when)
    {
        _creationTime = when;
        return this;
    }

    public UserRequestExemption Build()
    {
        return new UserRequestExemption
        {
            Id = _id,
            NationalId = _nationalId,
            ExemptedRequestCount = _exemptedRequestCount,
            SubmittedRequests = _submittedRequests,
            CreatorId = _creatorId,
            CreationTime = _creationTime,
            IsDeleted = _isDeleted,
            Notes = _notes
        };
    }

    // Convenience: build and save to context
    public async Task<UserRequestExemption> BuildAndSave(MainDbContext db)
    {
        var entity = Build();
        db.UserRequestExemptions.Add(entity);
        await db.SaveChangesAsync();
        return entity;
    }

    // Build multiple
    public List<UserRequestExemption> BuildMany(int count)
    {
        return Enumerable.Range(0, count)
            .Select(i => new ExemptionBuilder()
                .WithNationalId(GenerateNationalId())
                .WithCount((i % 15) + 1)
                .Build())
            .ToList();
    }

    private static string GenerateNationalId()
    {
        return $"1{Random.Shared.Next(100000000, 999999999)}";
    }
}
```

### 1.2 Bogus Faker Integration

```csharp
// TestData/Fakers/ExemptionFaker.cs
public class ExemptionFaker : Faker<UserRequestExemption>
{
    public ExemptionFaker()
    {
        RuleFor(e => e.Id, f => f.Random.Guid());
        RuleFor(e => e.NationalId, f => f.Random.Replace("1#########"));
        RuleFor(e => e.ExemptedRequestCount, f => f.Random.Int(1, 15));
        RuleFor(e => e.SubmittedRequests, (f, e) => f.Random.Int(0, e.ExemptedRequestCount));
        RuleFor(e => e.CreatorId, f => f.Random.Guid());
        RuleFor(e => e.CreationTime, f => f.Date.Past(1));
        RuleFor(e => e.IsDeleted, false);
        RuleFor(e => e.Notes, f => f.Lorem.Sentence());
    }

    public ExemptionFaker WithArabicNotes()
    {
        RuleFor(e => e.Notes, f => f.Random.ArrayElement(new[]
        {
            "استثناء للموظف المعتمد",
            "حالة خاصة تمت الموافقة عليها",
            "طلب مدير الإدارة",
            "استثناء مؤقت للمشروع"
        }));
        return this;
    }
}

// Usage
var exemptions = new ExemptionFaker().WithArabicNotes().Generate(100);
```

### 1.3 TypeScript Test Factories

```typescript
// test/factories/exemption.factory.ts
import { faker } from '@faker-js/faker';

export interface ExemptionDto {
  id: string;
  nationalId: string;
  exemptedRequestCount: number;
  submittedRequests: number;
  creationTime: string;
  creatorId: string;
  notes?: string;
}

export class ExemptionFactory {
  private data: Partial<ExemptionDto> = {};

  static default(): ExemptionFactory {
    return new ExemptionFactory();
  }

  withNationalId(nid: string): this {
    this.data.nationalId = nid;
    return this;
  }

  withCount(count: number): this {
    this.data.exemptedRequestCount = count;
    return this;
  }

  asExpired(): this {
    this.data.submittedRequests = this.data.exemptedRequestCount || 5;
    return this;
  }

  build(): ExemptionDto {
    return {
      id: this.data.id || faker.string.uuid(),
      nationalId: this.data.nationalId || faker.string.numeric(10),
      exemptedRequestCount: this.data.exemptedRequestCount || faker.number.int({ min: 1, max: 15 }),
      submittedRequests: this.data.submittedRequests || 0,
      creationTime: this.data.creationTime || faker.date.past().toISOString(),
      creatorId: this.data.creatorId || faker.string.uuid(),
      notes: this.data.notes
    };
  }

  buildMany(count: number): ExemptionDto[] {
    return Array.from({ length: count }, () => ExemptionFactory.default().build());
  }

  buildPagedResult(count: number, total?: number): { totalCount: number; items: ExemptionDto[] } {
    const items = this.buildMany(count);
    return { totalCount: total || count, items };
  }
}

// Usage in tests
const exemption = ExemptionFactory.default().withNationalId('1234567890').build();
const pagedResult = ExemptionFactory.default().buildPagedResult(10, 50);
```

---

## 2. Database Seeding Strategy

### 2.1 Reference Data Seeder

```csharp
// TestData/Seeders/ReferenceDataSeeder.cs
public static class ReferenceDataSeeder
{
    public static async Task SeedAsync(MainDbContext db)
    {
        // Only seed if empty (idempotent)
        if (await db.Roles.AnyAsync()) return;

        // Roles
        var roles = new[]
        {
            new AppRole { Id = Guid.Parse("11111111-1111-1111-1111-111111111111"), Name = "Admin" },
            new AppRole { Id = Guid.Parse("22222222-2222-2222-2222-222222222222"), Name = "PublicUser" },
            new AppRole { Id = Guid.Parse("33333333-3333-3333-3333-333333333333"), Name = "ReportsCoordinator" },
            new AppRole { Id = Guid.Parse("44444444-4444-4444-4444-444444444444"), Name = "SuperAdmin" }
        };
        await db.Roles.AddRangeAsync(roles);

        // Lookup tables
        var requestTypes = new[]
        {
            new RequestType { Id = 1, NameAr = "طلب تملك عقار", NameEn = "Real Estate Ownership" },
            new RequestType { Id = 2, NameAr = "طلب إفراغ", NameEn = "Transfer Request" },
            new RequestType { Id = 3, NameAr = "طلب رهن", NameEn = "Mortgage Request" }
        };
        await db.RequestTypes.AddRangeAsync(requestTypes);

        await db.SaveChangesAsync();
    }
}
```

### 2.2 Test-Specific Data Patterns

```csharp
// TestData/Scenarios/ExemptionScenarios.cs
public static class ExemptionScenarios
{
    /// <summary>
    /// Creates a realistic scenario with mixed exemption states
    /// </summary>
    public static async Task SeedMixedStates(MainDbContext db)
    {
        var builder = ExemptionBuilder.Default();
        var entities = new List<UserRequestExemption>
        {
            // Active exemptions (has remaining count)
            builder.WithNationalId("1000000001").WithCount(10).WithSubmittedRequests(3).Build(),
            builder.WithNationalId("1000000002").WithCount(5).WithSubmittedRequests(0).Build(),
            builder.WithNationalId("1000000003").WithCount(15).WithSubmittedRequests(14).Build(),
            
            // Fully used (expired)
            builder.WithNationalId("2000000001").WithCount(5).AsExpired().Build(),
            builder.WithNationalId("2000000002").WithCount(3).AsExpired().Build(),
            
            // Deleted (soft delete)
            builder.WithNationalId("3000000001").AsDeleted().Build()
        };

        await db.UserRequestExemptions.AddRangeAsync(entities);
        await db.SaveChangesAsync();
    }

    /// <summary>
    /// Creates high-volume data for pagination/performance tests
    /// </summary>
    public static async Task SeedHighVolume(MainDbContext db, int count = 10_000)
    {
        var faker = new ExemptionFaker();
        var batch = faker.Generate(count);

        // Insert in batches to avoid memory issues
        foreach (var chunk in batch.Chunk(1000))
        {
            await db.UserRequestExemptions.AddRangeAsync(chunk);
            await db.SaveChangesAsync();
            db.ChangeTracker.Clear(); // Prevent memory buildup
        }
    }
}
```

---

## 3. Test Isolation Strategies

### 3.1 Transaction Rollback (Fastest)

```csharp
// Shared isolation base for all integration tests
public abstract class IsolatedTestBase : IClassFixture<TestWebApplicationFactory>, IAsyncLifetime
{
    private readonly TestWebApplicationFactory _factory;
    private IServiceScope _scope = null!;
    private IDbContextTransaction _transaction = null!;
    
    protected MainDbContext Db { get; private set; } = null!;
    protected HttpClient Client { get; private set; } = null!;

    protected IsolatedTestBase(TestWebApplicationFactory factory)
    {
        _factory = factory;
    }

    public async Task InitializeAsync()
    {
        _scope = _factory.Services.CreateScope();
        Db = _scope.ServiceProvider.GetRequiredService<MainDbContext>();
        Client = _factory.CreateClient();
        
        // Start transaction — will be rolled back after test
        _transaction = await Db.Database.BeginTransactionAsync();
    }

    public async Task DisposeAsync()
    {
        // Rollback ALL changes made during this test
        await _transaction.RollbackAsync();
        await _transaction.DisposeAsync();
        _scope.Dispose();
        Client.Dispose();
    }
}
```

### 3.2 Respawn (Clean Slate)

```csharp
// For tests that MUST commit (e.g., testing transaction behavior)
public abstract class CleanSlateTestBase : IClassFixture<TestWebApplicationFactory>, IAsyncLifetime
{
    private static Respawner? _respawner;
    private static string _connectionString = null!;
    
    protected readonly TestWebApplicationFactory Factory;

    protected CleanSlateTestBase(TestWebApplicationFactory factory)
    {
        Factory = factory;
    }

    public async Task InitializeAsync()
    {
        if (_respawner == null)
        {
            _connectionString = Factory.Services
                .GetRequiredService<IConfiguration>()
                .GetConnectionString("Default")!;

            _respawner = await Respawner.CreateAsync(_connectionString, new RespawnerOptions
            {
                TablesToIgnore = new[]
                {
                    new Table("__EFMigrationsHistory"),
                    new Table("AppRoles"),        // Keep reference data
                    new Table("RequestTypes")     // Keep reference data
                },
                SchemasToInclude = new[] { "dbo" },
                WithReseed = true
            });
        }
    }

    public async Task DisposeAsync()
    {
        await _respawner!.ResetAsync(_connectionString);
    }
}
```

### 3.3 Unique Prefixes (for E2E)

```typescript
// e2e/helpers/test-prefix.ts
// Each test run gets a unique prefix to avoid collision in shared environments

export function testPrefix(): string {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).slice(2, 5);
  return `E2E_${timestamp}_${random}`;
}

export function testNationalId(prefix: string, index: number = 0): string {
  // Generate deterministic but unique NID per test
  const hash = (prefix + index).split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  return `1${hash.toString().padStart(9, '0').slice(0, 9)}`;
}

// Usage:
// const prefix = testPrefix(); // "E2E_lk4f2_9xm"
// const nid = testNationalId(prefix, 0); // "1234567890" (deterministic)
```

---

## 4. Sensitive Data Handling

### 4.1 PII Generators (Never Use Real Data)

```csharp
// TestData/Generators/SaudiDataGenerator.cs
public static class SaudiDataGenerator
{
    private static readonly Random Rng = new(42); // Deterministic seed

    /// <summary>
    /// Generates a valid-format Saudi National ID (not a real person)
    /// Starts with 1 (citizen) or 2 (resident)
    /// </summary>
    public static string NationalId(bool citizen = true)
    {
        var prefix = citizen ? "1" : "2";
        var digits = string.Join("", Enumerable.Range(0, 9).Select(_ => Rng.Next(0, 10)));
        return prefix + digits;
    }

    /// <summary>
    /// Generates realistic Arabic names (not real people)
    /// </summary>
    public static string ArabicFullName()
    {
        var firstNames = new[] { "محمد", "أحمد", "عبدالله", "فهد", "سلطان", "خالد", "نورة", "سارة" };
        var fatherNames = new[] { "عبدالرحمن", "سعود", "فيصل", "صالح", "إبراهيم" };
        var familyNames = new[] { "العتيبي", "القحطاني", "الحربي", "الشمري", "الدوسري", "المطيري" };

        return $"{firstNames[Rng.Next(firstNames.Length)]} " +
               $"{fatherNames[Rng.Next(fatherNames.Length)]} " +
               $"{familyNames[Rng.Next(familyNames.Length)]}";
    }

    /// <summary>
    /// Generates fake Saudi phone numbers
    /// </summary>
    public static string PhoneNumber()
    {
        var prefixes = new[] { "050", "055", "056", "053", "054", "058", "059" };
        var prefix = prefixes[Rng.Next(prefixes.Length)];
        var number = Rng.Next(1000000, 9999999);
        return $"+966{prefix[1..]}{number}";
    }

    /// <summary>
    /// Generates fake IBAN (Saudi format)
    /// </summary>
    public static string Iban()
    {
        var bankCode = Rng.Next(10, 80).ToString("D2");
        var accountNumber = string.Join("", Enumerable.Range(0, 18).Select(_ => Rng.Next(0, 10)));
        return $"SA{Rng.Next(10, 99)}{bankCode}{accountNumber}";
    }
}
```

### 4.2 Data Anonymization for Production Snapshots

```sql
-- scripts/anonymize-production-snapshot.sql
-- Used when copying production data for performance testing

-- Anonymize national IDs
UPDATE UserRequestExemptions
SET NationalId = CONCAT('1', RIGHT(REPLICATE('0', 9) + CAST(ABS(CHECKSUM(NEWID())) AS VARCHAR), 9));

-- Anonymize names
UPDATE Users
SET FullNameAr = CONCAT(N'مستخدم_', CAST(Id AS VARCHAR(10))),
    FullNameEn = CONCAT('User_', CAST(Id AS VARCHAR(10))),
    Email = CONCAT('user_', CAST(Id AS VARCHAR(10)), '@test.local'),
    PhoneNumber = CONCAT('+966500', RIGHT(REPLICATE('0', 6) + CAST(ABS(CHECKSUM(NEWID())) % 1000000 AS VARCHAR), 6));

-- Remove all actual credentials
UPDATE Users SET PasswordHash = NULL, SecurityStamp = NEWID();

-- Remove audit trail PII
UPDATE AuditLogs SET UserName = CONCAT('User_', UserId);

-- Verify no PII remains
SELECT 'VERIFICATION' as [Check],
    (SELECT COUNT(*) FROM Users WHERE Email NOT LIKE '%@test.local') as RealEmails,
    (SELECT COUNT(*) FROM Users WHERE PhoneNumber NOT LIKE '+966500%') as RealPhones;
```

---

## 5. Data Lifecycle Per Test Layer

| Layer | Data Source | Creation | Isolation | Cleanup |
|-------|------------|----------|-----------|---------|
| Unit Tests | In-memory builders | Per test method | N/A (no shared state) | GC |
| Integration Tests | Testcontainers DB | Per test class | Transaction rollback | Auto (container dies) |
| API Tests | Real DB (seeded) | Per test | Respawn | After each test |
| E2E Tests | API calls | Per test (unique prefix) | Unique identifiers | API cleanup in teardown |
| Performance Tests | Bulk seeder | Per test run | Isolated DB instance | Drop database |
| Production Synthetics | Pre-seeded test account | Static | Dedicated test tenant | Never deleted |

---

## 6. Cleanup Automation

### 6.1 Test Run Cleanup Script

```csharp
// TestData/Cleanup/TestCleanup.cs
public static class TestCleanup
{
    /// <summary>
    /// Remove all test data created by E2E tests (identified by prefix)
    /// </summary>
    public static async Task CleanupE2EData(MainDbContext db, string prefix = "E2E_")
    {
        // Clean in dependency order (children first)
        var exemptions = await db.UserRequestExemptions
            .Where(e => e.Notes != null && e.Notes.StartsWith(prefix))
            .ToListAsync();
        db.UserRequestExemptions.RemoveRange(exemptions);

        await db.SaveChangesAsync();
    }

    /// <summary>
    /// Clean up stale test data older than 24 hours
    /// Runs as scheduled job in test environments
    /// </summary>
    public static async Task CleanupStaleTestData(MainDbContext db)
    {
        var cutoff = DateTime.UtcNow.AddHours(-24);
        
        var stale = await db.UserRequestExemptions
            .Where(e => e.CreationTime < cutoff)
            .Where(e => e.Notes != null && (
                e.Notes.StartsWith("E2E_") ||
                e.Notes.StartsWith("k6 ") ||
                e.Notes.StartsWith("test_")))
            .ToListAsync();

        if (stale.Any())
        {
            db.UserRequestExemptions.RemoveRange(stale);
            await db.SaveChangesAsync();
        }
    }
}
```

### 6.2 CI Cleanup Pipeline

```yaml
# Scheduled: Run daily to clean test environments
schedules:
  - cron: '0 2 * * *'  # 2 AM daily
    displayName: 'Nightly Test Data Cleanup'
    branches:
      include: [main]

steps:
  - bash: |
      dotnet run --project tools/TestDataCleanup/ -- \
        --connection-string "$(TEST_DB_CONNECTION)" \
        --max-age-hours 24 \
        --prefix "E2E_,k6_,test_"
    displayName: 'Clean Stale Test Data'
```

---

## 7. Environment-Specific Data Strategy

| Environment | Data Strategy | Volume | Refresh Cycle |
|-------------|--------------|--------|---------------|
| Local Dev | Minimal seed + on-demand | ~100 records | On demand |
| CI/CD | Fresh per pipeline run | ~1000 records | Every run |
| Test (shared) | Seeded + E2E accumulation | ~5000 records | Nightly cleanup |
| Staging | Anonymized production snapshot | Production-scale | Weekly refresh |
| Production | Synthetic test tenant only | Minimal | Never cleaned |

---

*Previous: [09-PRODUCTION-MONITORING.md](09-PRODUCTION-MONITORING.md) · Next: [11-PIPELINE-INTEGRATION.md](11-PIPELINE-INTEGRATION.md)*
