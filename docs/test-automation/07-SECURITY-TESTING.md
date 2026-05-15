# 07 — Security Testing

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

Security testing **automates the detection of vulnerabilities** across the entire stack — code, dependencies, APIs, infrastructure, and secrets. It replaces manual penetration testing with continuous, automated security validation that runs on every commit.

**What this layer kills:**
- "Pen test found SQL injection 3 months after code was written" → SAST catches at PR time
- "Dependency has critical CVE but nobody noticed" → SCA alerts within hours
- "API accepts payloads that should be blocked" → DAST fuzzes every endpoint
- "Secrets committed to git" → Pre-commit hook blocks it
- "OWASP Top 10 vulnerability in production" → Automated OWASP checks in pipeline
- "Security review bottleneck delays release by 2 weeks" → Automated gates replace manual review

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SECURITY TESTING LAYERS                                    │
│                                                                              │
│  ┌─ PRE-COMMIT ────────────────────────────────────────────────────────┐    │
│  │  • Secret scanning (git-secrets, gitleaks)                           │    │
│  │  • Credential pattern detection                                      │    │
│  │  • Private key file detection                                        │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─ STATIC ANALYSIS (SAST) ────────────────────────────────────────────┐    │
│  │  • Source code scanning (Semgrep, CodeQL, SonarQube)                 │    │
│  │  • SQL injection patterns                                            │    │
│  │  • XSS vectors                                                       │    │
│  │  • Path traversal                                                    │    │
│  │  • Insecure deserialization                                          │    │
│  │  • Hardcoded credentials                                             │    │
│  │  • Cryptographic weaknesses                                          │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─ DEPENDENCY SCANNING (SCA) ─────────────────────────────────────────┐    │
│  │  • NuGet vulnerability scanning (dotnet list package --vulnerable)   │    │
│  │  • npm audit (npm audit --production)                                │    │
│  │  • License compliance checking                                       │    │
│  │  • Transitive dependency analysis                                    │    │
│  │  • SBOM generation                                                   │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─ DYNAMIC ANALYSIS (DAST) ───────────────────────────────────────────┐    │
│  │  • OWASP ZAP automated scan                                          │    │
│  │  • API fuzzing (injection payloads against live endpoints)           │    │
│  │  • Authentication bypass attempts                                     │    │
│  │  • CORS misconfiguration detection                                   │    │
│  │  • Security header verification                                      │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─ CONTAINER SCANNING ────────────────────────────────────────────────┐    │
│  │  • Trivy (image vulnerability scanning)                              │    │
│  │  • Dockerfile linting (Hadolint)                                     │    │
│  │  • Base image CVE detection                                          │    │
│  │  • Runtime privilege analysis                                        │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─ INFRASTRUCTURE AS CODE (IaC) ─────────────────────────────────────┐    │
│  │  • Kubernetes manifest scanning (kubesec, kube-score)                │    │
│  │  • Terraform/Bicep misconfiguration (tfsec, checkov)                │    │
│  │  • Network policy validation                                         │    │
│  │  • RBAC policy review                                                │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  Gate: Zero critical/high SAST | Zero critical SCA | DAST clean | No leaks │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Secret Scanning (Pre-Commit)

### 1.1 Gitleaks Configuration

```toml
# .gitleaks.toml
title = "Project Secret Scanning Rules"

[allowlist]
  description = "Global allowlist"
  paths = [
    '''\.gitleaks\.toml$''',
    '''test[s]?/.*''',
    '''.*_test\.go$''',
    '''.*\.test\.(ts|js)$'''
  ]

[[rules]]
  id = "azure-connection-string"
  description = "Azure Connection String"
  regex = '''(?i)(DefaultEndpointsProtocol|AccountKey|SharedAccessSignature)=[^;\s"']+'''
  tags = ["azure", "connection-string"]

[[rules]]
  id = "sql-connection-string"
  description = "SQL Server Connection String with Password"
  regex = '''(?i)(Password|Pwd)\s*=\s*[^;\s"']{8,}'''
  tags = ["database", "credential"]

[[rules]]
  id = "jwt-secret"
  description = "JWT Signing Key"
  regex = '''(?i)(jwt|signing|secret|token)[\w-]*[_\s]*[:=]\s*["']?[A-Za-z0-9+/=]{32,}["']?'''
  tags = ["jwt", "secret"]

[[rules]]
  id = "private-key"
  description = "Private Key"
  regex = '''-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'''
  tags = ["key", "private"]

[[rules]]
  id = "azure-devops-pat"
  description = "Azure DevOps Personal Access Token"
  regex = '''(?i)[a-z0-9]{52}'''
  entropy = 4.5
  tags = ["azure-devops", "pat"]

[[rules]]
  id = "generic-api-key"
  description = "Generic API Key"
  regex = '''(?i)(api[_-]?key|apikey|api[_-]?secret)[\s]*[:=][\s]*["']?[A-Za-z0-9_\-]{20,}["']?'''
  tags = ["api-key"]
```

### 1.2 Pre-Commit Hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.0
    hooks:
      - id: gitleaks
        name: Detect secrets in staged files
        entry: gitleaks protect --staged --config=.gitleaks.toml
        language: golang
        pass_filenames: false

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: detect-private-key
      - id: check-added-large-files
        args: ['--maxkb=1024']
```

---

## 2. Static Application Security Testing (SAST)

### 2.1 Semgrep Rules

```yaml
# .semgrep/custom-rules.yaml
rules:
  - id: sql-injection-ef-raw
    patterns:
      - pattern: $DB.Database.ExecuteSqlRaw($QUERY, ...)
      - metavariable-pattern:
          metavariable: $QUERY
          patterns:
            - pattern: $"..."
            - pattern-not: |
                "..."
    message: >
      Raw SQL with string interpolation detected. Use parameterized queries.
      Use ExecuteSqlInterpolated or pass parameters separately.
    severity: ERROR
    languages: [csharp]
    metadata:
      cwe: CWE-89
      owasp: A03:2021
      category: security

  - id: xss-raw-html
    patterns:
      - pattern-either:
          - pattern: "[innerHTML]=\"$EXPR\""
          - pattern: this.$EL.nativeElement.innerHTML = $EXPR
      - pattern-not: "[innerHTML]=\"$EXPR | safeHtml\""
    message: >
      Potential XSS: raw HTML binding without sanitization.
      Use DomSanitizer or Angular's built-in sanitization.
    severity: WARNING
    languages: [typescript, html]
    metadata:
      cwe: CWE-79
      owasp: A03:2021

  - id: insecure-cors
    patterns:
      - pattern: |
          .AllowAnyOrigin()
      - pattern-not-inside: |
          if ($ENV == "Development") { ... }
    message: >
      AllowAnyOrigin() in CORS is dangerous in production.
      Specify exact allowed origins.
    severity: ERROR
    languages: [csharp]
    metadata:
      cwe: CWE-346
      owasp: A05:2021

  - id: missing-auth-attribute
    patterns:
      - pattern: |
          [HttpPost]
          public ... $METHOD(...) { ... }
      - pattern-not: |
          [Authorize(...)]
          ...
          public ... $METHOD(...) { ... }
      - pattern-not: |
          [AllowAnonymous]
          ...
          public ... $METHOD(...) { ... }
    message: >
      HTTP endpoint without [Authorize] or [AllowAnonymous] attribute.
      All endpoints must explicitly declare their auth requirement.
    severity: WARNING
    languages: [csharp]
    metadata:
      cwe: CWE-862
      owasp: A01:2021

  - id: hardcoded-credential
    patterns:
      - pattern-either:
          - pattern: |
              ... password = "..."
          - pattern: |
              ... Password = "..."
          - pattern: |
              "Password": "..."
      - pattern-not-inside: |
          ... test ...
    message: >
      Hardcoded credential detected. Use environment variables or secret manager.
    severity: ERROR
    languages: [csharp, json]
    metadata:
      cwe: CWE-798
      owasp: A07:2021

  - id: insecure-deserialization
    patterns:
      - pattern-either:
          - pattern: BinaryFormatter.$METHOD(...)
          - pattern: new JavaScriptSerializer().Deserialize(...)
          - pattern: JsonConvert.DeserializeObject($INPUT)
      - pattern-not: JsonConvert.DeserializeObject<$TYPE>($INPUT)
    message: >
      Potentially unsafe deserialization. Use typed deserialization.
      BinaryFormatter is banned. Use System.Text.Json with explicit types.
    severity: ERROR
    languages: [csharp]
    metadata:
      cwe: CWE-502
      owasp: A08:2021

  - id: path-traversal
    patterns:
      - pattern: Path.Combine($BASE, $USER_INPUT)
      - pattern-not-inside: |
          ... = Path.GetFileName($USER_INPUT) ...
    message: >
      Potential path traversal. Validate and sanitize user input before
      using in file paths. Use Path.GetFileName() to strip directory components.
    severity: ERROR
    languages: [csharp]
    metadata:
      cwe: CWE-22
      owasp: A01:2021
```

### 2.2 Pipeline Integration

```yaml
# SAST stage
- stage: SAST
  displayName: 'Static Security Analysis'
  jobs:
  - job: semgrep
    displayName: 'Semgrep SAST'
    steps:
    - bash: |
        pip install semgrep
        semgrep scan \
          --config=auto \
          --config=.semgrep/ \
          --sarif \
          --output=semgrep-results.sarif \
          --error \
          --severity=ERROR \
          src/
      displayName: 'Run Semgrep'
      
    - task: PublishPipelineArtifact@1
      condition: always()
      inputs:
        targetPath: 'semgrep-results.sarif'
        artifactName: 'sast-results'

  - job: codeql
    displayName: 'CodeQL Analysis'
    steps:
    - task: AdvancedSecurity-Codeql-Init@1
      inputs:
        languages: 'csharp,javascript'
        querysuite: 'security-extended'
    
    - task: DotNetCoreCLI@2
      inputs:
        command: 'build'
        projects: '**/*.csproj'
    
    - task: AdvancedSecurity-Codeql-Analyze@1
```

---

## 3. Software Composition Analysis (SCA)

### 3.1 .NET Vulnerability Scanning

```yaml
- job: dotnet_sca
  displayName: '.NET Dependency Audit'
  steps:
  - bash: |
      # Check for vulnerable packages
      dotnet list package --vulnerable --include-transitive 2>&1 | tee vuln-report.txt
      
      # Fail on critical/high vulnerabilities
      if grep -i "Critical\|High" vuln-report.txt; then
        echo "##[error]Critical or High vulnerability found in NuGet packages"
        exit 1
      fi
    displayName: 'NuGet Vulnerability Check'
    
  - bash: |
      # Generate SBOM
      dotnet tool install --global Microsoft.Sbom.DotNetTool
      sbom-tool generate \
        -b ./src \
        -bc ./src \
        -pn "ProjectName" \
        -pv "$(Build.BuildNumber)" \
        -nsb "https://company.com/sbom"
    displayName: 'Generate SBOM'
```

### 3.2 npm Vulnerability Scanning

```yaml
- job: npm_sca
  displayName: 'npm Dependency Audit'
  steps:
  - bash: |
      cd src/frontend
      
      # Production dependencies only
      npm audit --production --audit-level=high 2>&1 | tee npm-audit.txt
      
      # Check exit code
      if [ $? -ne 0 ]; then
        echo "##[error]High/Critical npm vulnerability detected"
        echo "##[error]Run 'npm audit fix' or add to .nsprc for known accepted risks"
        exit 1
      fi
    displayName: 'npm Audit'
    
  - bash: |
      # License compliance check
      npx license-checker --production --failOn "GPL-3.0;AGPL-3.0" --summary
    displayName: 'License Compliance'
```

---

## 4. Dynamic Application Security Testing (DAST)

### 4.1 OWASP ZAP Automated Scan

```yaml
# DAST stage — runs against deployed test environment
- stage: DAST
  displayName: 'Dynamic Security Scan'
  dependsOn: DeployToTest
  jobs:
  - job: zap_scan
    displayName: 'OWASP ZAP Scan'
    container:
      image: ghcr.io/zaproxy/zaproxy:stable
    steps:
    - bash: |
        # API scan using OpenAPI spec
        zap-api-scan.py \
          -t $(TEST_URL)/swagger/v1/swagger.json \
          -f openapi \
          -r zap-report.html \
          -x zap-report.xml \
          -J zap-report.json \
          -c zap-config.conf \
          -z "-config api.key=$(ZAP_API_KEY)" \
          --hook=zap-hooks.py

        # Check for high/critical alerts
        HIGH_ALERTS=$(cat zap-report.json | python3 -c "
        import json, sys
        data = json.load(sys.stdin)
        high = [a for a in data.get('site', [{}])[0].get('alerts', []) 
                if int(a.get('riskcode', 0)) >= 3]
        print(len(high))
        ")
        
        if [ "$HIGH_ALERTS" -gt "0" ]; then
          echo "##[error]OWASP ZAP found $HIGH_ALERTS high/critical vulnerabilities"
          exit 1
        fi
      displayName: 'ZAP API Scan'
```

### 4.2 ZAP Configuration

```conf
# zap-config.conf
# Disable passive scanners that produce false positives
10015	IGNORE	# Incomplete or No Cache-control Header Set
10037	IGNORE	# Server Leaks via "X-Powered-By" Header (handled in middleware)
10096	IGNORE	# Timestamp Disclosure - Unix

# Enforce these active scanners
40012	FAIL	# Cross Site Scripting (Reflected)
40014	FAIL	# Cross Site Scripting (Persistent)
40018	FAIL	# SQL Injection
40019	FAIL	# SQL Injection (MySQL)
40020	FAIL	# SQL Injection (Hypersonic)
40021	FAIL	# SQL Injection (Oracle)
40022	FAIL	# SQL Injection (PostgreSQL)
90018	FAIL	# Advanced SQL Injection
90019	FAIL	# Server Side Code Injection
90020	FAIL	# Remote OS Command Injection
90021	FAIL	# XPath Injection
6	    FAIL	# Path Traversal
7	    FAIL	# Remote File Inclusion
```

### 4.3 Custom ZAP Hooks

```python
# zap-hooks.py — Authentication and custom behavior
import json

def zap_started(zap, target):
    """Called when ZAP starts scanning"""
    # Add authentication token
    token = generate_test_jwt()
    zap.replacer.add_rule(
        description='Auth Header',
        enabled=True,
        matchtype='REQ_HEADER',
        matchregex=False,
        matchstring='Authorization',
        replacement=f'Bearer {token}'
    )

def zap_pre_shutdown(zap):
    """Generate custom report before shutdown"""
    alerts = zap.core.alerts()
    high_critical = [a for a in alerts if int(a['risk']) >= 3]
    
    if high_critical:
        print("\n=== CRITICAL/HIGH SECURITY FINDINGS ===")
        for alert in high_critical:
            print(f"\n[{alert['risk']}] {alert['name']}")
            print(f"  URL: {alert['url']}")
            print(f"  Description: {alert['description'][:200]}")
            print(f"  Solution: {alert['solution'][:200]}")
```

---

## 5. Container Security Scanning

### 5.1 Trivy Configuration

```yaml
# trivy.yaml
severity:
  - CRITICAL
  - HIGH

vulnerability:
  type:
    - os
    - library

scan:
  scanners:
    - vuln
    - misconfig
    - secret

misconfig:
  scanners:
    - dockerfile
    - kubernetes
```

### 5.2 Pipeline Integration

```yaml
- job: container_scan
  displayName: 'Container Security Scan'
  steps:
  - bash: |
      # Scan built image
      trivy image \
        --severity CRITICAL,HIGH \
        --exit-code 1 \
        --ignore-unfixed \
        --format sarif \
        --output trivy-results.sarif \
        $(HARBOR_REGISTRY)/$(IMAGE_NAME):$(Build.BuildNumber)
    displayName: 'Trivy Image Scan'

  - bash: |
      # Dockerfile linting
      hadolint Dockerfile \
        --format sarif \
        --failure-threshold error \
        > hadolint-results.sarif
    displayName: 'Dockerfile Lint'

  - bash: |
      # Scan Kubernetes manifests
      trivy config \
        --severity CRITICAL,HIGH \
        --exit-code 1 \
        k8s/
    displayName: 'K8s Manifest Scan'
```

---

## 6. Security Test Cases (Integration Tests)

```csharp
// SecurityIntegrationTests.cs — Automated OWASP checks
public class SecurityIntegrationTests : ApiTestBase
{
    public SecurityIntegrationTests(TestWebApplicationFactory factory) : base(factory) { }

    [Theory]
    [InlineData("'; DROP TABLE users; --")]
    [InlineData("1' OR '1'='1")]
    [InlineData("1; EXEC xp_cmdshell('dir')")]
    [InlineData("1 UNION SELECT * FROM information_schema.tables")]
    public async Task SqlInjection_BlockedByParameterization(string maliciousInput)
    {
        await AuthenticateAs("Admin");

        var response = await Client.GetAsync(
            $"/api/app/user-request-exemption?filter={Uri.EscapeDataString(maliciousInput)}");

        // Should not return 500 (would indicate SQL error = injection worked)
        response.StatusCode.Should().NotBe(HttpStatusCode.InternalServerError);
    }

    [Theory]
    [InlineData("<script>alert('xss')</script>")]
    [InlineData("<img src=x onerror=alert(1)>")]
    [InlineData("javascript:alert(1)")]
    [InlineData("'-alert(1)-'")]
    public async Task XSS_SanitizedInResponse(string xssPayload)
    {
        await AuthenticateAs("Admin");

        var payload = new { nationalId = "1234567890", exemptedRequestCount = 5, notes = xssPayload };
        var createResponse = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);

        if (createResponse.IsSuccessStatusCode)
        {
            var body = await createResponse.Content.ReadAsStringAsync();
            body.Should().NotContain("<script>");
            body.Should().NotContain("onerror=");
            body.Should().NotContain("javascript:");
        }
    }

    [Fact]
    public async Task IDOR_CannotAccessOtherUsersData()
    {
        // Create as User A
        await AuthenticateAs("Admin", "user-a-id");
        var payload = new { nationalId = "1111111111", exemptedRequestCount = 3 };
        var created = await Client.PostAsJsonAsync("/api/app/user-request-exemption", payload);
        var dto = await created.Content.ReadFromJsonAsync<ExemptionDto>();

        // Try to delete as User B (non-admin)
        await AuthenticateAs("PublicUser", "user-b-id");
        var deleteResponse = await Client.DeleteAsync($"/api/app/user-request-exemption/{dto!.Id}");

        deleteResponse.StatusCode.Should().Be(HttpStatusCode.Forbidden);
    }

    [Fact]
    public async Task RateLimiting_BlocksExcessiveRequests()
    {
        await AuthenticateAs("PublicUser");

        var responses = new List<HttpResponseMessage>();
        for (int i = 0; i < 200; i++)
        {
            responses.Add(await Client.GetAsync("/api/app/user-request-exemption"));
        }

        // After rate limit, should get 429
        var rateLimited = responses.Count(r => r.StatusCode == HttpStatusCode.TooManyRequests);
        rateLimited.Should().BeGreaterThan(0,
            "rate limiting should kick in after excessive requests");
    }

    [Fact]
    public async Task SecurityHeaders_Present()
    {
        await AuthenticateAs("Admin");
        var response = await Client.GetAsync("/api/app/user-request-exemption");

        response.Headers.Should().ContainKey("X-Content-Type-Options");
        response.Headers.Should().ContainKey("X-Frame-Options");
        response.Headers.GetValues("X-Content-Type-Options").Should().Contain("nosniff");
        response.Headers.GetValues("X-Frame-Options").Should().Contain("DENY");
    }

    [Theory]
    [InlineData("../../../etc/passwd")]
    [InlineData("..\\..\\..\\windows\\system32\\config\\sam")]
    [InlineData("%2e%2e%2f%2e%2e%2f")]
    [InlineData("....//....//....//")]
    public async Task PathTraversal_Blocked(string traversalAttempt)
    {
        await AuthenticateAs("Admin");

        var response = await Client.GetAsync($"/api/app/documents/{traversalAttempt}");

        response.StatusCode.Should().NotBe(HttpStatusCode.OK);
        response.StatusCode.Should().BeOneOf(
            HttpStatusCode.BadRequest,
            HttpStatusCode.NotFound,
            HttpStatusCode.Forbidden);
    }

    [Fact]
    public async Task CORS_RejectsUnauthorizedOrigins()
    {
        var request = new HttpRequestMessage(HttpMethod.Options, "/api/app/user-request-exemption");
        request.Headers.Add("Origin", "https://evil-site.com");
        request.Headers.Add("Access-Control-Request-Method", "GET");

        var response = await Client.SendAsync(request);

        // Should not include the evil origin in allowed origins
        if (response.Headers.Contains("Access-Control-Allow-Origin"))
        {
            response.Headers.GetValues("Access-Control-Allow-Origin")
                .Should().NotContain("https://evil-site.com");
        }
    }

    [Fact]
    public async Task MassAssignment_ExtraFieldsIgnored()
    {
        await AuthenticateAs("Admin");

        // Try to set fields that shouldn't be settable via API
        var json = """
        {
            "nationalId": "1234567890",
            "exemptedRequestCount": 5,
            "id": "00000000-0000-0000-0000-000000000001",
            "creatorId": "attacker-id",
            "isDeleted": true,
            "creationTime": "2000-01-01T00:00:00Z"
        }
        """;

        var content = new StringContent(json, Encoding.UTF8, "application/json");
        var response = await Client.PostAsync("/api/app/user-request-exemption", content);

        if (response.IsSuccessStatusCode)
        {
            var result = await response.Content.ReadFromJsonAsync<ExemptionDto>();
            result!.Id.Should().NotBe(Guid.Parse("00000000-0000-0000-0000-000000000001"));
            result.CreatorId.Should().NotBe("attacker-id");
        }
    }
}
```

---

## 7. Security Gate Definition

| Check | Severity | Action on Failure |
|-------|----------|-------------------|
| Gitleaks (secrets) | Critical | Block commit (pre-commit) |
| Semgrep Critical | Critical | Block PR merge |
| Semgrep High | High | Block PR merge |
| Semgrep Medium | Medium | Warning in PR, must acknowledge |
| NuGet Critical CVE | Critical | Block build |
| npm High CVE | High | Block build |
| OWASP ZAP High | High | Block deploy to production |
| Trivy Critical | Critical | Block image push |
| Dockerfile lint error | Medium | Block build |
| K8s misconfig Critical | Critical | Block deploy |

---

## 8. OWASP Top 10 Coverage Matrix

| OWASP 2021 | Automated Check | Tool |
|------------|-----------------|------|
| A01: Broken Access Control | Auth tests + IDOR tests | Integration tests + ZAP |
| A02: Cryptographic Failures | Semgrep rules + config scan | Semgrep + Trivy |
| A03: Injection | SQL/XSS/Command injection tests | Integration tests + ZAP + Semgrep |
| A04: Insecure Design | Architecture review (manual) | Review Cards (T3) |
| A05: Security Misconfiguration | Header tests + config scan | Integration tests + Trivy |
| A06: Vulnerable Components | SCA scanning | npm audit + dotnet audit |
| A07: Auth Failures | Rate limiting + brute force tests | Integration tests + ZAP |
| A08: Software Integrity | SBOM + dependency pinning | SCA + Sigstore |
| A09: Logging Failures | Log coverage assertion | Integration tests |
| A10: SSRF | URL validation tests | Integration tests + Semgrep |

---

*Previous: [06-PERFORMANCE-TESTING.md](06-PERFORMANCE-TESTING.md) · Next: [08-CHAOS-RESILIENCE-TESTING.md](08-CHAOS-RESILIENCE-TESTING.md)*
