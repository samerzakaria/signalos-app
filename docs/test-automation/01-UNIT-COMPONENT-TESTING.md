# 01 — Unit & Component Testing

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

Unit and component tests verify the **smallest behavioral units** of a system in isolation. They form the fastest feedback layer — executing in milliseconds — and validate that individual functions, methods, classes, and UI components produce correct outputs for given inputs.

**What this layer kills:**
- "I changed this function — did it break anything?" → Run unit tests (< 30 seconds)
- "Does this calculation handle edge cases?" → Property-based tests cover all boundaries
- "Are my tests actually testing anything useful?" → Mutation testing proves test quality

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    UNIT TEST LAYER                        │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────────┐  ┌──────────────────────────────┐│
│  │  Pure Logic Tests │  │  Component Interaction Tests  ││
│  │  (no dependencies)│  │  (mocked dependencies)        ││
│  │                   │  │                               ││
│  │  • Calculations   │  │  • Service → Repository      ││
│  │  • Validations    │  │  • Controller → Service       ││
│  │  • Transformations│  │  • Component → Service        ││
│  │  • State machines │  │  • Middleware → Context        ││
│  │  • Business rules │  │  • Guard → Auth Service        ││
│  └──────────────────┘  └──────────────────────────────┘│
│                                                          │
│  ┌──────────────────────────────────────────────────────┐│
│  │  Property-Based Tests (Generative)                    ││
│  │  • Auto-generate inputs (1000s of cases)              ││
│  │  • Assert invariants hold for ALL inputs              ││
│  │  • Discover edge cases humans miss                    ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  ┌──────────────────────────────────────────────────────┐│
│  │  Mutation Testing (Test Quality Verification)         ││
│  │  • Inject faults into production code                 ││
│  │  • Verify tests catch the mutation                    ││
│  │  • Score = caught / total mutations                   ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
├─────────────────────────────────────────────────────────┤
│  Gate: Coverage ≥ 85% | Mutation Score ≥ 95% (logic)    │
│  Execution: < 2 minutes total | Parallelized            │
└─────────────────────────────────────────────────────────┘
```

---

## 1. Backend Unit Testing (.NET / C# / ABP Framework)

### 1.1 Framework Selection

| Framework | Purpose | Justification |
|-----------|---------|---------------|
| xUnit | Test runner | .NET standard, async-native, parallel by default |
| Moq | Mocking | Interface-based, fluent API, verify interactions |
| NSubstitute | Alternative mock | Less verbose for simple cases |
| FluentAssertions | Assertions | Readable, rich error messages, chain-able |
| AutoFixture | Test data | Auto-generate complex objects, reduce boilerplate |
| Bogus | Fake data | Locale-aware fake data (names, emails, IDs) |
| FsCheck | Property-based | .NET property testing, integrates with xUnit |
| Stryker.NET | Mutation testing | Standard .NET mutator, report generation |

### 1.2 Project Structure

```
tests/
├── {Project}.Unit.Tests/
│   ├── {Project}.Unit.Tests.csproj
│   ├── _Setup/
│   │   ├── TestBase.cs                    # Shared test infrastructure
│   │   ├── AutoFixtureCustomizations.cs   # Object generation rules
│   │   └── FakeDataGenerators.cs          # Bogus generators
│   ├── Domain/
│   │   ├── Entities/
│   │   │   ├── {Entity}Tests.cs           # Entity behavior tests
│   │   │   └── {Entity}ValidationTests.cs # Entity invariant tests
│   │   ├── ValueObjects/
│   │   │   └── {ValueObject}Tests.cs      # Value object equality/validity
│   │   └── DomainServices/
│   │       └── {Service}Tests.cs          # Domain logic tests
│   ├── Application/
│   │   ├── {Feature}/
│   │   │   ├── {Feature}AppServiceTests.cs           # Application service tests
│   │   │   ├── {Feature}ValidatorTests.cs            # Input validation tests
│   │   │   └── {Feature}MappingTests.cs              # DTO mapping tests
│   │   └── Shared/
│   │       └── PaginationTests.cs                     # Shared behavior tests
│   ├── Properties/
│   │   └── {Feature}PropertyTests.cs     # Property-based tests
│   └── stryker-config.json               # Mutation testing config
```

### 1.3 Test Patterns

#### Pattern 1: Arrange-Act-Assert (Standard)

```csharp
public class UserRequestExemptionValidatorTests
{
    private readonly UserRequestExemptionValidator _sut;

    public UserRequestExemptionValidatorTests()
    {
        _sut = new UserRequestExemptionValidator();
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    [InlineData(16)]
    [InlineData(100)]
    [InlineData(int.MaxValue)]
    [InlineData(int.MinValue)]
    public void Validate_InvalidExemptionCount_ReturnsError(int invalidCount)
    {
        // Arrange
        var input = new CreateUserRequestExemptionDto
        {
            NationalId = "1234567890",
            ExemptedRequestCount = invalidCount
        };

        // Act
        var result = _sut.Validate(input);

        // Assert
        result.IsValid.Should().BeFalse();
        result.Errors.Should().ContainSingle()
            .Which.ErrorMessage.Should().Contain("1")
            .And.Contain("15");
    }

    [Theory]
    [InlineData(1)]
    [InlineData(5)]
    [InlineData(10)]
    [InlineData(15)]
    public void Validate_ValidExemptionCount_ReturnsSuccess(int validCount)
    {
        // Arrange
        var input = new CreateUserRequestExemptionDto
        {
            NationalId = "1234567890",
            ExemptedRequestCount = validCount
        };

        // Act
        var result = _sut.Validate(input);

        // Assert
        result.IsValid.Should().BeTrue();
        result.Errors.Should().BeEmpty();
    }
}
```

#### Pattern 2: Service Tests with Mocked Dependencies

```csharp
public class ExemptionAppServiceTests
{
    private readonly Mock<IRepository<UserRequestExemption, Guid>> _repoMock;
    private readonly Mock<IRepository<PublicUserInformation, long>> _userRepoMock;
    private readonly Mock<ICurrentUser> _currentUserMock;
    private readonly UserRequestExemptionAppService _sut;

    public ExemptionAppServiceTests()
    {
        _repoMock = new Mock<IRepository<UserRequestExemption, Guid>>();
        _userRepoMock = new Mock<IRepository<PublicUserInformation, long>>();
        _currentUserMock = new Mock<ICurrentUser>();

        _sut = new UserRequestExemptionAppService(
            _repoMock.Object,
            _userRepoMock.Object,
            _currentUserMock.Object
        );
    }

    [Fact]
    public async Task AddExemption_DuplicateActiveExemption_ThrowsUserFriendlyException()
    {
        // Arrange
        var input = new CreateUserRequestExemptionDto
        {
            NationalId = "1234567890",
            ExemptedRequestCount = 5
        };

        _repoMock.Setup(r => r.FirstOrDefaultAsync(It.IsAny<Expression<Func<UserRequestExemption, bool>>>()))
            .ReturnsAsync(new UserRequestExemption { NationalId = "1234567890", ExemptedRequestCount = 3 });

        // Act
        var act = () => _sut.AddUserRequestExemption(input);

        // Assert
        await act.Should().ThrowAsync<UserFriendlyException>()
            .WithMessage("*استثناء فعّال*");
    }

    [Fact]
    public async Task AddExemption_NewUser_InsertsAndReturnsDto()
    {
        // Arrange
        var input = new CreateUserRequestExemptionDto
        {
            NationalId = "9876543210",
            ExemptedRequestCount = 10
        };

        _repoMock.Setup(r => r.FirstOrDefaultAsync(It.IsAny<Expression<Func<UserRequestExemption, bool>>>()))
            .ReturnsAsync((UserRequestExemption)null);

        _repoMock.Setup(r => r.InsertAsync(It.IsAny<UserRequestExemption>(), true))
            .ReturnsAsync((UserRequestExemption e, bool _) => e);

        _currentUserMock.Setup(u => u.Id).Returns(Guid.NewGuid());

        // Act
        var result = await _sut.AddUserRequestExemption(input);

        // Assert
        result.Should().NotBeNull();
        result.NationalId.Should().Be("9876543210");
        result.ExemptedRequestCount.Should().Be(10);
        _repoMock.Verify(r => r.InsertAsync(It.Is<UserRequestExemption>(
            e => e.NationalId == "9876543210" && e.ExemptedRequestCount == 10
        ), true), Times.Once);
    }
}
```

#### Pattern 3: Property-Based Testing

```csharp
public class ExemptionPropertyTests
{
    [Property]
    public Property ValidCount_AlwaysAccepted()
    {
        return Prop.ForAll(
            Gen.Choose(1, 15).ToArbitrary(),
            count =>
            {
                var validator = new UserRequestExemptionValidator();
                var input = new CreateUserRequestExemptionDto
                {
                    NationalId = "1234567890",
                    ExemptedRequestCount = count
                };
                return validator.Validate(input).IsValid;
            });
    }

    [Property]
    public Property InvalidCount_AlwaysRejected()
    {
        return Prop.ForAll(
            Arb.From(
                Gen.OneOf(
                    Gen.Choose(int.MinValue, 0),
                    Gen.Choose(16, int.MaxValue)
                )),
            count =>
            {
                var validator = new UserRequestExemptionValidator();
                var input = new CreateUserRequestExemptionDto
                {
                    NationalId = "1234567890",
                    ExemptedRequestCount = count
                };
                return !validator.Validate(input).IsValid;
            });
    }

    [Property]
    public Property NationalId_10Digits_AlwaysValid()
    {
        return Prop.ForAll(
            Gen.ArrayOf(10, Gen.Choose(0, 9))
                .Select(digits => string.Concat(digits))
                .ToArbitrary(),
            nid =>
            {
                var validator = new NationalIdValidator();
                return validator.IsValid(nid);
            });
    }

    [Property]
    public Property ExemptionCount_NeverExceedsOriginal()
    {
        // Invariant: submitted requests can never exceed exempted count
        return Prop.ForAll(
            Gen.Choose(1, 15).ToArbitrary(),
            Gen.Choose(0, 100).ToArbitrary(),
            (exemptedCount, submittedCount) =>
            {
                var exemption = new UserRequestExemption
                {
                    ExemptedRequestCount = exemptedCount,
                    SubmittedRequests = Math.Min(submittedCount, exemptedCount)
                };
                return exemption.SubmittedRequests <= exemption.ExemptedRequestCount;
            });
    }
}
```

### 1.4 Mutation Testing Configuration

```json
// stryker-config.json
{
  "stryker-config": {
    "project": "src/{Project}.Application/{Project}.Application.csproj",
    "test-projects": ["tests/{Project}.Unit.Tests/{Project}.Unit.Tests.csproj"],
    "reporters": ["html", "json", "dashboard"],
    "thresholds": {
      "high": 95,
      "low": 85,
      "break": 80
    },
    "mutate": [
      "src/{Project}.Application/**/*.cs",
      "src/{Project}.Domain/**/*.cs",
      "!src/**/*Dto.cs",
      "!src/**/*Mapping*.cs",
      "!src/**/Migrations/**"
    ],
    "mutation-level": "Advanced",
    "concurrency": 4,
    "since": {
      "enabled": true,
      "target": "main"
    }
  }
}
```

**Mutation operators applied:**
| Operator | Example | What it tests |
|----------|---------|---------------|
| Arithmetic | `a + b` → `a - b` | Math correctness |
| Conditional boundary | `x > 0` → `x >= 0` | Boundary conditions |
| Negate conditional | `if (valid)` → `if (!valid)` | Logic paths |
| Return value | `return true` → `return false` | Return correctness |
| String mutation | `"error"` → `""` | String handling |
| Remove statement | `list.Add(item)` → `` | Side effect testing |
| Linq mutation | `.Where(x => x.Active)` → `.Where(x => true)` | Query correctness |

---

## 2. Frontend Unit Testing (Angular / TypeScript)

### 2.1 Framework Selection

| Framework | Purpose | Justification |
|-----------|---------|---------------|
| Jest | Test runner | 3× faster than Karma, snapshot testing, parallel |
| Angular Testing Library | Component testing | User-behavior focused, not implementation-detail |
| ng-mocks | Mocking | Angular-specific, mock modules/pipes/directives |
| jest-auto-spies | Auto-mocking | Auto-generate spies from interfaces |
| fast-check | Property-based | TypeScript property testing |
| StrykerJS | Mutation testing | JavaScript/TypeScript mutation analysis |

### 2.2 Project Structure

```
src/
├── app/
│   ├── features/
│   │   ├── exemptions/
│   │   │   ├── components/
│   │   │   │   ├── exemption-list/
│   │   │   │   │   ├── exemption-list.component.ts
│   │   │   │   │   ├── exemption-list.component.spec.ts  ← component test
│   │   │   │   │   └── exemption-list.component.html
│   │   │   │   └── exemption-form/
│   │   │   │       ├── exemption-form.component.ts
│   │   │   │       ├── exemption-form.component.spec.ts  ← component test
│   │   │   │       └── exemption-form.component.html
│   │   │   ├── services/
│   │   │   │   ├── exemption.service.ts
│   │   │   │   └── exemption.service.spec.ts             ← service test
│   │   │   ├── validators/
│   │   │   │   ├── exempted-count.validator.ts
│   │   │   │   └── exempted-count.validator.spec.ts      ← validator test
│   │   │   └── models/
│   │   │       └── exemption.model.ts
│   │   └── ...
│   └── shared/
│       ├── validators/
│       │   ├── national-id.validator.ts
│       │   └── national-id.validator.spec.ts
│       └── pipes/
│           ├── hijri-date.pipe.ts
│           └── hijri-date.pipe.spec.ts
├── jest.config.ts
└── stryker.conf.json
```

### 2.3 Test Patterns

#### Pattern 1: Component Testing (User-Behavior Focused)

```typescript
import { render, screen, fireEvent, waitFor } from '@testing-library/angular';
import { ExemptionFormComponent } from './exemption-form.component';
import { ExemptionService } from '../../services/exemption.service';
import { createSpyFromClass } from 'jest-auto-spies';

describe('ExemptionFormComponent', () => {
  let mockService: jest.Mocked<ExemptionService>;

  beforeEach(() => {
    mockService = createSpyFromClass(ExemptionService);
  });

  it('shows single error when count is out of range 1-15', async () => {
    await render(ExemptionFormComponent, {
      providers: [{ provide: ExemptionService, useValue: mockService }]
    });

    const countInput = screen.getByLabelText(/عدد طلبات التملك/);
    fireEvent.input(countInput, { target: { value: '20' } });
    fireEvent.blur(countInput);

    const errors = screen.getAllByRole('alert');
    expect(errors).toHaveLength(1);
    expect(errors[0]).toHaveTextContent(/بين 1 و 15/);
  });

  it('disables submit when form is invalid', async () => {
    await render(ExemptionFormComponent, {
      providers: [{ provide: ExemptionService, useValue: mockService }]
    });

    const submitButton = screen.getByRole('button', { name: /حفظ|إضافة/ });
    expect(submitButton).toBeDisabled();
  });

  it('calls service with correct data on valid submit', async () => {
    mockService.addExemption.mockReturnValue(of({ id: '123', nid: '1234567890', count: 5 }));

    await render(ExemptionFormComponent, {
      providers: [{ provide: ExemptionService, useValue: mockService }]
    });

    const nidInput = screen.getByLabelText(/رقم الهوية/);
    const countInput = screen.getByLabelText(/عدد طلبات/);

    fireEvent.input(nidInput, { target: { value: '1234567890' } });
    fireEvent.input(countInput, { target: { value: '5' } });

    const submitButton = screen.getByRole('button', { name: /حفظ|إضافة/ });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(mockService.addExemption).toHaveBeenCalledWith({
        nationalId: '1234567890',
        exemptedRequestCount: 5
      });
    });
  });

  it('shows server error message on API failure', async () => {
    mockService.addExemption.mockReturnValue(
      throwError(() => ({ error: { message: 'يوجد استثناء فعّال' } }))
    );

    await render(ExemptionFormComponent, {
      providers: [{ provide: ExemptionService, useValue: mockService }]
    });

    // Fill form and submit
    fireEvent.input(screen.getByLabelText(/رقم الهوية/), { target: { value: '1234567890' } });
    fireEvent.input(screen.getByLabelText(/عدد طلبات/), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: /حفظ|إضافة/ }));

    await waitFor(() => {
      expect(screen.getByText(/يوجد استثناء فعّال/)).toBeInTheDocument();
    });
  });
});
```

#### Pattern 2: Validator Testing

```typescript
import { FormControl } from '@angular/forms';
import { exemptedCountValidator } from './exempted-count.validator';

describe('exemptedCountValidator', () => {
  const validator = exemptedCountValidator(1, 15);

  describe('valid inputs', () => {
    it.each([1, 2, 5, 10, 14, 15])('accepts %d', (value) => {
      const control = new FormControl(value);
      expect(validator(control)).toBeNull();
    });

    it.each(['1', '5', '15'])('accepts string "%s" (type="text" input)', (value) => {
      const control = new FormControl(value);
      expect(validator(control)).toBeNull();
    });
  });

  describe('invalid inputs', () => {
    it.each([0, -1, 16, 100, -100])('rejects %d', (value) => {
      const control = new FormControl(value);
      const result = validator(control);
      expect(result).not.toBeNull();
      expect(result?.['exemptedCount']).toBeTruthy();
    });

    it.each([null, undefined, ''])('rejects empty: %s', (value) => {
      const control = new FormControl(value);
      const result = validator(control);
      expect(result).not.toBeNull();
    });

    it.each(['abc', '1.5', '1e2', '١٢'])('rejects non-numeric: "%s"', (value) => {
      const control = new FormControl(value);
      const result = validator(control);
      expect(result).not.toBeNull();
    });
  });

  describe('boundary exhaustiveness', () => {
    it('returns exactly one error object (not multiple)', () => {
      const control = new FormControl(0);
      const result = validator(control);
      expect(Object.keys(result!)).toHaveLength(1);
    });
  });
});
```

#### Pattern 3: Property-Based Testing (Frontend)

```typescript
import fc from 'fast-check';
import { exemptedCountValidator } from './exempted-count.validator';
import { FormControl } from '@angular/forms';

describe('exemptedCountValidator properties', () => {
  const validator = exemptedCountValidator(1, 15);

  it('always accepts integers in [1, 15]', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 15 }), (value) => {
        const result = validator(new FormControl(value));
        return result === null;
      })
    );
  });

  it('always rejects integers outside [1, 15]', () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.integer({ min: -1000, max: 0 }),
          fc.integer({ min: 16, max: 1000 })
        ),
        (value) => {
          const result = validator(new FormControl(value));
          return result !== null;
        }
      )
    );
  });

  it('error message always mentions the valid range', () => {
    fc.assert(
      fc.property(fc.integer({ min: 16, max: 1000 }), (value) => {
        const result = validator(new FormControl(value));
        const message = result?.['exemptedCount']?.message || '';
        return message.includes('1') && message.includes('15');
      })
    );
  });

  it('never returns multiple error keys', () => {
    fc.assert(
      fc.property(fc.anything(), (value) => {
        const result = validator(new FormControl(value));
        if (result === null) return true;
        return Object.keys(result).length === 1;
      })
    );
  });
});
```

### 2.4 Frontend Mutation Testing

```json
// stryker.conf.json
{
  "$schema": "./node_modules/@stryker-mutator/core/schema/stryker-schema.json",
  "mutate": [
    "src/app/**/*.ts",
    "!src/app/**/*.spec.ts",
    "!src/app/**/*.module.ts",
    "!src/app/**/index.ts",
    "!src/environments/**"
  ],
  "testRunner": "jest",
  "jest": {
    "configFile": "jest.config.ts"
  },
  "reporters": ["html", "clear-text", "progress", "dashboard"],
  "thresholds": {
    "high": 95,
    "low": 85,
    "break": 80
  },
  "concurrency": 4,
  "timeoutMS": 60000,
  "incremental": true
}
```

---

## 3. Test Naming Convention

### Standard Format
```
{MethodUnderTest}_{Scenario}_{ExpectedBehavior}
```

### Examples
```csharp
// C#
AddExemption_DuplicateNationalId_ThrowsUserFriendlyException()
Validate_CountBelowMinimum_ReturnsSingleError()
GetAll_EmptyDatabase_ReturnsEmptyPagedResult()
Delete_NonExistentId_ThrowsEntityNotFoundException()
```

```typescript
// TypeScript
'shows error when count exceeds 15'
'disables submit button when form is invalid'
'calls API with correct payload on valid submit'
'displays server error message on 400 response'
```

---

## 4. Coverage Requirements

### Coverage by Module Type

| Module Type | Line Coverage | Branch Coverage | Mutation Score |
|-------------|:------------:|:---------------:|:--------------:|
| Domain entities / value objects | 95% | 90% | 95% |
| Application services (business logic) | 90% | 85% | 95% |
| Validators / Guards | 100% | 100% | 98% |
| DTOs / Mappings | 80% | N/A | Exempt |
| UI Components (logic-heavy) | 85% | 80% | 90% |
| UI Components (template-only) | 60% | N/A | Exempt |
| Pipes / Directives | 95% | 90% | 95% |
| Infrastructure / Config | 50% | N/A | Exempt |

### What NOT to Unit Test
- Framework behavior (Angular DI, ABP module registration)
- Simple property getters/setters with no logic
- Auto-generated code (migrations, scaffolded files)
- Configuration constants
- Logging statements
- Third-party library wrappers (test at integration layer instead)

---

## 5. Test Execution Configuration

### CI Pipeline Integration

```yaml
# Unit test step in CI pipeline
- task: DotNetCoreCLI@2
  displayName: 'Run Unit Tests'
  inputs:
    command: 'test'
    projects: 'tests/**/*.Unit.Tests.csproj'
    arguments: >
      --configuration Release
      --no-build
      --logger "trx;LogFileName=unit-test-results.trx"
      --collect:"XPlat Code Coverage"
      --settings tests/coverlet.runsettings
      -- RunConfiguration.MaxCpuCount=0
  env:
    DOTNET_ENVIRONMENT: Test

- task: PublishTestResults@2
  displayName: 'Publish Unit Test Results'
  condition: always()
  inputs:
    testResultsFormat: 'VSTest'
    testResultsFiles: '**/unit-test-results.trx'
    mergeTestResults: true
    failTaskOnFailedTests: true

- task: PublishCodeCoverageResults@2
  displayName: 'Publish Coverage'
  inputs:
    summaryFileLocation: '**/coverage.cobertura.xml'
    failIfCoverageEmpty: true

# Coverage gate
- bash: |
    COVERAGE=$(grep -oP 'line-rate="\K[^"]+' **/coverage.cobertura.xml | head -1)
    COVERAGE_PCT=$(echo "$COVERAGE * 100" | bc)
    echo "Line coverage: ${COVERAGE_PCT}%"
    if (( $(echo "$COVERAGE_PCT < 85" | bc -l) )); then
      echo "##[error]Coverage ${COVERAGE_PCT}% is below threshold (85%)"
      exit 1
    fi
  displayName: 'Coverage Gate (≥ 85%)'
```

### Local Development Integration

```json
// .husky/pre-push (runs before git push)
{
  "scripts": {
    "pre-push": "dotnet test tests/ --filter 'Category!=Integration' --no-build && cd src/frontend && npx jest --bail"
  }
}
```

---

## 6. Anti-Patterns (What to Avoid)

| Anti-Pattern | Why It's Bad | Correct Approach |
|-------------|-------------|-----------------|
| Testing implementation details | Breaks on refactor, doesn't test behavior | Test inputs/outputs only |
| One assertion per test (dogmatic) | Creates 500 trivial tests | Group related assertions logically |
| Mocking everything | Tests pass but system is broken | Mock only external boundaries |
| Testing private methods | Coupling to implementation | Test through public interface |
| Setup/teardown spaghetti | Shared state, hidden dependencies | Each test creates own context |
| `Thread.Sleep` in tests | Slow, flaky, non-deterministic | Use async/await, inject clock |
| Commented-out tests | Dead code, false coverage | Delete or fix immediately |
| Test data in constants file | Hidden coupling, fragile | Generate data per test (AutoFixture/Bogus) |
| Asserting on exact error messages | Fragile, locale-dependent | Assert on error code/type |
| 100% coverage goal | Covers trivial code, misses logic | Focus on mutation score instead |

---

## 7. Execution Speed Requirements

| Scope | Max Duration | Action if Exceeded |
|-------|:------------:|-------------------|
| Single unit test | 100ms | Investigate — likely I/O or sleep |
| Full unit test suite | 2 minutes | Parallelize or split into shards |
| Mutation testing (incremental) | 10 minutes | Use `--since` flag for changed files only |
| Mutation testing (full) | 30 minutes | Run nightly, not on every PR |

---

*Previous: [00-INDEX.md](00-INDEX.md) · Next: [02-API-INTEGRATION-TESTING.md](02-API-INTEGRATION-TESTING.md)*
