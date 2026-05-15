# 04 — End-to-End UI Testing

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

End-to-end (E2E) tests validate **complete user workflows** through the real UI, real browser, real API calls, and real database. They prove that the entire system works together from the user's perspective — clicking buttons, filling forms, navigating pages, and verifying visible outcomes.

**What this layer kills:**
- "It works in isolation but breaks when users click through" → Full workflow test
- "This button doesn't do anything in production" → Real browser interaction
- "Form validation differs between frontend and backend" → Full round-trip verification
- "Navigation is broken after deploy" → Route/redirect testing
- "QA found this regression after 2 hours of manual testing" → Automated in 30 seconds

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    E2E TESTING ARCHITECTURE                                    │
│                                                                              │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │                    PLAYWRIGHT TEST RUNNER                              │  │
│   │                                                                       │  │
│   │  • Multi-browser: Chromium, Firefox, WebKit                           │  │
│   │  • Auto-wait: no flaky explicit waits                                 │  │
│   │  • Network interception: mock external services                       │  │
│   │  • Tracing: video + screenshots + HAR on failure                      │  │
│   │  • Parallel execution: sharded across workers                         │  │
│   └───────────────────────────────┬──────────────────────────────────────┘  │
│                                   │                                          │
│   ┌───────────────────────────────▼──────────────────────────────────────┐  │
│   │                      PAGE OBJECT LAYER                                │  │
│   │                                                                       │  │
│   │  LoginPage → DashboardPage → ExemptionListPage → ExemptionFormPage   │  │
│   │                                                                       │  │
│   │  • Encapsulates selectors and actions                                 │  │
│   │  • Reusable across tests                                              │  │
│   │  • Single source of truth for UI structure                            │  │
│   └───────────────────────────────┬──────────────────────────────────────┘  │
│                                   │                                          │
│   ┌───────────────────────────────▼──────────────────────────────────────┐  │
│   │                      APPLICATION UNDER TEST                           │  │
│   │                                                                       │  │
│   │  Angular Frontend  ──HTTP──►  .NET API  ──EF Core──►  SQL Server     │  │
│   │      (real)                    (real)                   (real/test)    │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  Gate: All critical user journeys pass | < 10 min total | Zero flake        │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Playwright Configuration

### 1.1 Global Config

```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 4 : undefined,
  reporter: [
    ['html', { open: 'never' }],
    ['junit', { outputFile: 'e2e-results.xml' }],
    ['list']
  ],
  
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:4200',
    trace: 'on-first-retry',
    video: 'on-first-retry',
    screenshot: 'only-on-failure',
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
    locale: 'ar-SA',
    timezoneId: 'Asia/Riyadh'
  },

  projects: [
    // Setup: authenticate once and store state
    {
      name: 'setup',
      testMatch: /.*\.setup\.ts/,
      teardown: 'cleanup'
    },
    {
      name: 'cleanup',
      testMatch: /.*\.teardown\.ts/
    },

    // Desktop browsers
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: '.auth/admin.json'
      },
      dependencies: ['setup']
    },
    {
      name: 'firefox',
      use: {
        ...devices['Desktop Firefox'],
        storageState: '.auth/admin.json'
      },
      dependencies: ['setup']
    },
    {
      name: 'webkit',
      use: {
        ...devices['Desktop Safari'],
        storageState: '.auth/admin.json'
      },
      dependencies: ['setup']
    },

    // Mobile viewports
    {
      name: 'mobile-chrome',
      use: {
        ...devices['Pixel 7'],
        storageState: '.auth/admin.json'
      },
      dependencies: ['setup']
    },
    {
      name: 'mobile-safari',
      use: {
        ...devices['iPhone 14'],
        storageState: '.auth/admin.json'
      },
      dependencies: ['setup']
    }
  ],

  // Start app before tests
  webServer: process.env.CI ? undefined : {
    command: 'ng serve',
    url: 'http://localhost:4200',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000
  }
});
```

### 1.2 Authentication Setup (Run Once)

```typescript
// e2e/auth.setup.ts
import { test as setup, expect } from '@playwright/test';

const ADMIN_FILE = '.auth/admin.json';
const USER_FILE = '.auth/user.json';
const COORDINATOR_FILE = '.auth/coordinator.json';

setup('authenticate as admin', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('اسم المستخدم').fill(process.env.E2E_ADMIN_USER!);
  await page.getByLabel('كلمة المرور').fill(process.env.E2E_ADMIN_PASS!);
  await page.getByRole('button', { name: 'تسجيل الدخول' }).click();
  
  // Wait for redirect to dashboard
  await page.waitForURL('**/dashboard', { timeout: 15_000 });
  await expect(page.getByTestId('dashboard-title')).toBeVisible();
  
  // Save authenticated state
  await page.context().storageState({ path: ADMIN_FILE });
});

setup('authenticate as public user', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('اسم المستخدم').fill(process.env.E2E_USER_USER!);
  await page.getByLabel('كلمة المرور').fill(process.env.E2E_USER_PASS!);
  await page.getByRole('button', { name: 'تسجيل الدخول' }).click();
  
  await page.waitForURL('**/home');
  await page.context().storageState({ path: USER_FILE });
});

setup('authenticate as coordinator', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('اسم المستخدم').fill(process.env.E2E_COORD_USER!);
  await page.getByLabel('كلمة المرور').fill(process.env.E2E_COORD_PASS!);
  await page.getByRole('button', { name: 'تسجيل الدخول' }).click();
  
  await page.waitForURL('**/reports');
  await page.context().storageState({ path: COORDINATOR_FILE });
});
```

---

## 2. Page Object Pattern

### 2.1 Base Page

```typescript
// e2e/pages/base.page.ts
import { Page, Locator, expect } from '@playwright/test';

export abstract class BasePage {
  protected readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  // Common navigation
  async navigateTo(path: string) {
    await this.page.goto(path);
    await this.page.waitForLoadState('networkidle');
  }

  // Common assertions
  async expectToastSuccess(message?: string) {
    const toast = this.page.locator('.p-toast-message-success');
    await expect(toast).toBeVisible({ timeout: 5_000 });
    if (message) {
      await expect(toast).toContainText(message);
    }
  }

  async expectToastError(message?: string) {
    const toast = this.page.locator('.p-toast-message-error');
    await expect(toast).toBeVisible({ timeout: 5_000 });
    if (message) {
      await expect(toast).toContainText(message);
    }
  }

  // Wait for API call to complete
  async waitForApi(urlPattern: string | RegExp) {
    return this.page.waitForResponse(
      resp => resp.url().match(urlPattern) !== null && resp.status() < 400
    );
  }

  // Common loading state
  async waitForLoading() {
    const spinner = this.page.locator('[data-testid="loading-spinner"]');
    if (await spinner.isVisible()) {
      await expect(spinner).toBeHidden({ timeout: 30_000 });
    }
  }

  // RTL-aware helpers
  get isRtl() {
    return this.page.locator('html[dir="rtl"]').isVisible();
  }
}
```

### 2.2 Concrete Page Objects

```typescript
// e2e/pages/exemption-list.page.ts
import { Page, Locator, expect } from '@playwright/test';
import { BasePage } from './base.page';

export class ExemptionListPage extends BasePage {
  // Locators
  readonly pageTitle: Locator;
  readonly createButton: Locator;
  readonly table: Locator;
  readonly searchInput: Locator;
  readonly refreshButton: Locator;
  readonly emptyState: Locator;
  readonly pagination: Locator;
  readonly totalCount: Locator;

  constructor(page: Page) {
    super(page);
    this.pageTitle = page.getByTestId('exemption-list-title');
    this.createButton = page.getByRole('button', { name: /إضافة|جديد/ });
    this.table = page.getByTestId('exemptions-table');
    this.searchInput = page.getByPlaceholder(/بحث|Search/);
    this.refreshButton = page.getByTestId('refresh-btn');
    this.emptyState = page.getByTestId('empty-state');
    this.pagination = page.locator('.p-paginator');
    this.totalCount = page.getByTestId('total-count');
  }

  async goto() {
    await this.navigateTo('/admin/exemptions');
    await this.waitForLoading();
  }

  async getRowCount(): Promise<number> {
    return this.table.locator('tbody tr').count();
  }

  async getRowByNationalId(nid: string): Promise<Locator> {
    return this.table.locator(`tr:has-text("${nid}")`);
  }

  async clickCreate() {
    await this.createButton.click();
    await this.page.waitForURL('**/exemptions/create');
  }

  async deleteRow(nid: string) {
    const row = await this.getRowByNationalId(nid);
    await row.getByRole('button', { name: /حذف|Delete/ }).click();
    
    // Confirm dialog
    const dialog = this.page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: /تأكيد|نعم/ }).click();
    
    await this.waitForApi(/user-request-exemption/);
  }

  async searchByNationalId(nid: string) {
    await this.searchInput.fill(nid);
    await this.page.keyboard.press('Enter');
    await this.waitForLoading();
  }

  async goToPage(pageNumber: number) {
    await this.pagination.getByText(pageNumber.toString()).click();
    await this.waitForLoading();
  }
}
```

```typescript
// e2e/pages/exemption-form.page.ts
import { Page, Locator, expect } from '@playwright/test';
import { BasePage } from './base.page';

export class ExemptionFormPage extends BasePage {
  readonly nationalIdInput: Locator;
  readonly countInput: Locator;
  readonly notesInput: Locator;
  readonly submitButton: Locator;
  readonly cancelButton: Locator;
  readonly nationalIdError: Locator;
  readonly countError: Locator;

  constructor(page: Page) {
    super(page);
    this.nationalIdInput = page.getByLabel(/رقم الهوية|National ID/);
    this.countInput = page.getByLabel(/عدد الطلبات|Request Count/);
    this.notesInput = page.getByLabel(/ملاحظات|Notes/);
    this.submitButton = page.getByRole('button', { name: /حفظ|إضافة|Submit/ });
    this.cancelButton = page.getByRole('button', { name: /إلغاء|Cancel/ });
    this.nationalIdError = page.getByTestId('nid-error');
    this.countError = page.getByTestId('count-error');
  }

  async goto() {
    await this.navigateTo('/admin/exemptions/create');
    await this.waitForLoading();
  }

  async fillForm(data: { nationalId: string; count: number; notes?: string }) {
    await this.nationalIdInput.fill(data.nationalId);
    await this.countInput.fill(data.count.toString());
    if (data.notes) {
      await this.notesInput.fill(data.notes);
    }
  }

  async submit() {
    const responsePromise = this.waitForApi(/user-request-exemption/);
    await this.submitButton.click();
    return responsePromise;
  }

  async submitAndExpectSuccess() {
    await this.submit();
    await this.expectToastSuccess();
    // Should redirect to list
    await this.page.waitForURL('**/exemptions');
  }

  async submitAndExpectError(errorMessage?: string) {
    await this.submitButton.click();
    await this.expectToastError(errorMessage);
  }
}
```

---

## 3. E2E Test Suites

### 3.1 Critical User Journey Tests

```typescript
// e2e/tests/exemption-management.spec.ts
import { test, expect } from '@playwright/test';
import { ExemptionListPage } from '../pages/exemption-list.page';
import { ExemptionFormPage } from '../pages/exemption-form.page';

test.describe('Exemption Management — Full Workflow', () => {
  let listPage: ExemptionListPage;
  let formPage: ExemptionFormPage;

  test.beforeEach(async ({ page }) => {
    listPage = new ExemptionListPage(page);
    formPage = new ExemptionFormPage(page);
  });

  test('create → verify in list → delete → verify removed', async ({ page }) => {
    const testNid = `99${Date.now().toString().slice(-8)}`;

    // Navigate to list
    await listPage.goto();
    await expect(listPage.pageTitle).toBeVisible();

    // Click create
    await listPage.clickCreate();

    // Fill and submit form
    await formPage.fillForm({ nationalId: testNid, count: 5, notes: 'E2E test' });
    await formPage.submitAndExpectSuccess();

    // Verify in list
    await listPage.goto();
    const row = await listPage.getRowByNationalId(testNid);
    await expect(row).toBeVisible();
    await expect(row).toContainText('5');

    // Delete
    await listPage.deleteRow(testNid);
    await listPage.expectToastSuccess();

    // Verify removed
    await expect(row).toBeHidden();
  });

  test('form validation prevents invalid submission', async ({ page }) => {
    await formPage.goto();

    // Empty form submission
    await formPage.submitButton.click();
    await expect(formPage.nationalIdError).toBeVisible();
    await expect(formPage.countError).toBeVisible();

    // Invalid national ID (less than 10 digits)
    await formPage.nationalIdInput.fill('123');
    await formPage.submitButton.click();
    await expect(formPage.nationalIdError).toBeVisible();

    // Invalid count (0)
    await formPage.countInput.fill('0');
    await formPage.submitButton.click();
    await expect(formPage.countError).toBeVisible();

    // Valid data
    await formPage.fillForm({ nationalId: '1234567890', count: 5 });
    await expect(formPage.nationalIdError).toBeHidden();
    await expect(formPage.countError).toBeHidden();
  });

  test('duplicate active exemption shows error', async ({ page }) => {
    const testNid = `88${Date.now().toString().slice(-8)}`;

    // Create first
    await formPage.goto();
    await formPage.fillForm({ nationalId: testNid, count: 3 });
    await formPage.submitAndExpectSuccess();

    // Try to create duplicate
    await formPage.goto();
    await formPage.fillForm({ nationalId: testNid, count: 2 });
    await formPage.submitAndExpectError('استثناء فعّال');
  });

  test('pagination works correctly', async ({ page }) => {
    await listPage.goto();
    
    const totalText = await listPage.totalCount.textContent();
    const total = parseInt(totalText || '0');
    
    if (total > 10) {
      // Click page 2
      await listPage.goToPage(2);
      const rowCount = await listPage.getRowCount();
      expect(rowCount).toBeGreaterThan(0);
      expect(rowCount).toBeLessThanOrEqualTo(10);
    }
  });

  test('search filters results', async ({ page }) => {
    const testNid = `77${Date.now().toString().slice(-8)}`;

    // Create with known NID
    await formPage.goto();
    await formPage.fillForm({ nationalId: testNid, count: 1 });
    await formPage.submitAndExpectSuccess();

    // Search
    await listPage.goto();
    await listPage.searchByNationalId(testNid);
    
    const rowCount = await listPage.getRowCount();
    expect(rowCount).toBe(1);
    const row = await listPage.getRowByNationalId(testNid);
    await expect(row).toBeVisible();
  });
});
```

### 3.2 Navigation & Layout Tests

```typescript
// e2e/tests/navigation.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Navigation & Sidebar', () => {
  test('sidebar links navigate to correct pages', async ({ page }) => {
    await page.goto('/');
    
    const navItems = [
      { link: /الرئيسية|Dashboard/, url: '/dashboard' },
      { link: /الاستثناءات|Exemptions/, url: '/admin/exemptions' },
      { link: /التقارير|Reports/, url: '/reports' },
      { link: /المستخدمين|Users/, url: '/admin/users' }
    ];

    for (const item of navItems) {
      const link = page.getByRole('link', { name: item.link });
      if (await link.isVisible()) {
        await link.click();
        await expect(page).toHaveURL(new RegExp(item.url));
      }
    }
  });

  test('breadcrumb navigation works', async ({ page }) => {
    await page.goto('/admin/exemptions/create');
    
    // Click breadcrumb to go back to list
    const breadcrumb = page.locator('.breadcrumb').getByText(/الاستثناءات|Exemptions/);
    await breadcrumb.click();
    await expect(page).toHaveURL(/exemptions/);
  });

  test('back button preserves state', async ({ page }) => {
    await page.goto('/admin/exemptions');
    
    // Go to page 2
    await page.locator('.p-paginator').getByText('2').click();
    
    // Navigate away
    await page.goto('/dashboard');
    
    // Go back
    await page.goBack();
    
    // Should be on page 2 still (or at least on the list)
    await expect(page).toHaveURL(/exemptions/);
  });

  test('RTL layout is correct', async ({ page }) => {
    await page.goto('/dashboard');
    
    // Verify RTL direction
    const html = page.locator('html');
    await expect(html).toHaveAttribute('dir', 'rtl');
    
    // Sidebar should be on the right
    const sidebar = page.locator('[data-testid="sidebar"]');
    const sidebarBox = await sidebar.boundingBox();
    const viewportSize = page.viewportSize()!;
    
    // In RTL, sidebar should be near the right edge
    expect(sidebarBox!.x + sidebarBox!.width).toBeCloseTo(viewportSize.width, -1);
  });
});
```

### 3.3 Error & Edge Case Tests

```typescript
// e2e/tests/error-handling.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Error Handling', () => {
  test('network error shows friendly message', async ({ page }) => {
    await page.goto('/admin/exemptions');
    
    // Intercept API and simulate failure
    await page.route('**/api/app/user-request-exemption**', route =>
      route.abort('connectionrefused')
    );
    
    // Trigger refresh
    await page.getByTestId('refresh-btn').click();
    
    // Should show error message, not crash
    const errorMessage = page.getByText(/خطأ في الاتصال|حدث خطأ|Connection error/);
    await expect(errorMessage).toBeVisible();
  });

  test('session expired redirects to login', async ({ page }) => {
    await page.goto('/admin/exemptions');
    
    // Intercept API and return 401
    await page.route('**/api/app/**', route =>
      route.fulfill({ status: 401, body: 'Unauthorized' })
    );
    
    // Trigger any API call
    await page.getByTestId('refresh-btn').click();
    
    // Should redirect to login
    await expect(page).toHaveURL(/login/, { timeout: 10_000 });
  });

  test('404 page shows for invalid routes', async ({ page }) => {
    await page.goto('/nonexistent-page-xyz');
    
    const notFound = page.getByText(/404|الصفحة غير موجودة|not found/i);
    await expect(notFound).toBeVisible();
  });

  test('large data sets don\'t crash the UI', async ({ page }) => {
    // Mock API to return max page size
    await page.route('**/api/app/user-request-exemption**', route =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          totalCount: 10000,
          items: Array.from({ length: 100 }, (_, i) => ({
            id: crypto.randomUUID(),
            nationalId: `10000${i.toString().padStart(5, '0')}`,
            exemptedRequestCount: (i % 15) + 1,
            submittedRequests: i % 5,
            creationTime: new Date().toISOString(),
            creatorId: crypto.randomUUID()
          }))
        })
      })
    );
    
    await page.goto('/admin/exemptions');
    
    // Page should load without performance issues
    const table = page.getByTestId('exemptions-table');
    await expect(table).toBeVisible({ timeout: 5_000 });
    
    // Verify pagination shows
    const pagination = page.locator('.p-paginator');
    await expect(pagination).toBeVisible();
  });
});
```

### 3.4 Accessibility Tests

```typescript
// e2e/tests/accessibility.spec.ts
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test.describe('Accessibility', () => {
  const pages = [
    { name: 'Dashboard', url: '/dashboard' },
    { name: 'Exemption List', url: '/admin/exemptions' },
    { name: 'Create Exemption', url: '/admin/exemptions/create' },
    { name: 'Reports', url: '/reports' }
  ];

  for (const { name, url } of pages) {
    test(`${name} page has no critical accessibility violations`, async ({ page }) => {
      await page.goto(url);
      await page.waitForLoadState('networkidle');
      
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .exclude('.third-party-widget') // Exclude things we don't control
        .analyze();
      
      // Filter to critical and serious only
      const critical = results.violations.filter(
        v => v.impact === 'critical' || v.impact === 'serious'
      );
      
      expect(critical, `${name} has accessibility violations: ${
        critical.map(v => `${v.id}: ${v.description}`).join('\n')
      }`).toHaveLength(0);
    });
  }

  test('keyboard navigation works through form', async ({ page }) => {
    await page.goto('/admin/exemptions/create');
    
    // Tab through form fields
    await page.keyboard.press('Tab'); // Focus NID
    await expect(page.getByLabel(/رقم الهوية/)).toBeFocused();
    
    await page.keyboard.press('Tab'); // Focus count
    await expect(page.getByLabel(/عدد الطلبات/)).toBeFocused();
    
    await page.keyboard.press('Tab'); // Focus notes
    await expect(page.getByLabel(/ملاحظات/)).toBeFocused();
    
    await page.keyboard.press('Tab'); // Focus submit
    await expect(page.getByRole('button', { name: /حفظ/ })).toBeFocused();
  });

  test('screen reader labels are present', async ({ page }) => {
    await page.goto('/admin/exemptions');
    
    // Table should have accessible name
    const table = page.getByRole('table');
    await expect(table).toHaveAttribute('aria-label', /.+/);
    
    // Action buttons have labels
    const deleteButtons = page.getByRole('button', { name: /حذف/ });
    const count = await deleteButtons.count();
    for (let i = 0; i < count; i++) {
      await expect(deleteButtons.nth(i)).toHaveAccessibleName(/.+/);
    }
  });
});
```

---

## 4. Test Data Management for E2E

### 4.1 API-Based Setup/Teardown

```typescript
// e2e/helpers/api-helper.ts
import { APIRequestContext } from '@playwright/test';

export class TestApiHelper {
  constructor(private request: APIRequestContext, private baseUrl: string) {}

  async createExemption(data: { nationalId: string; count: number }) {
    const response = await this.request.post(`${this.baseUrl}/api/app/user-request-exemption`, {
      data: {
        nationalId: data.nationalId,
        exemptedRequestCount: data.count
      }
    });
    return response.json();
  }

  async deleteExemption(id: string) {
    await this.request.delete(`${this.baseUrl}/api/app/user-request-exemption/${id}`);
  }

  async cleanupTestData(prefix: string) {
    const response = await this.request.get(
      `${this.baseUrl}/api/app/user-request-exemption?MaxResultCount=1000`
    );
    const data = await response.json();
    
    for (const item of data.items) {
      if (item.nationalId.startsWith(prefix)) {
        await this.deleteExemption(item.id);
      }
    }
  }
}

// Usage in test fixtures
import { test as base } from '@playwright/test';
import { TestApiHelper } from '../helpers/api-helper';

export const test = base.extend<{ apiHelper: TestApiHelper }>({
  apiHelper: async ({ request }, use) => {
    const helper = new TestApiHelper(request, process.env.API_BASE_URL!);
    await use(helper);
    // Cleanup after test
    await helper.cleanupTestData('E2E_');
  }
});
```

### 4.2 Test Fixtures

```typescript
// e2e/fixtures/exemption.fixture.ts
import { test as base } from '@playwright/test';
import { ExemptionListPage } from '../pages/exemption-list.page';
import { ExemptionFormPage } from '../pages/exemption-form.page';
import { TestApiHelper } from '../helpers/api-helper';

type ExemptionFixtures = {
  listPage: ExemptionListPage;
  formPage: ExemptionFormPage;
  apiHelper: TestApiHelper;
  seededExemption: { id: string; nationalId: string };
};

export const test = base.extend<ExemptionFixtures>({
  listPage: async ({ page }, use) => {
    await use(new ExemptionListPage(page));
  },
  formPage: async ({ page }, use) => {
    await use(new ExemptionFormPage(page));
  },
  apiHelper: async ({ request }, use) => {
    const helper = new TestApiHelper(request, process.env.API_BASE_URL!);
    await use(helper);
  },
  seededExemption: async ({ apiHelper }, use) => {
    // Create test data via API
    const nid = `E2E_${Date.now().toString().slice(-7)}`;
    const result = await apiHelper.createExemption({ nationalId: nid, count: 5 });
    
    await use({ id: result.id, nationalId: nid });
    
    // Cleanup
    await apiHelper.deleteExemption(result.id);
  }
});
```

---

## 5. Cross-Browser & Viewport Matrix

| Browser | Viewport | Priority | When to Run |
|---------|----------|----------|-------------|
| Chromium | 1920×1080 | P0 | Every PR |
| Firefox | 1920×1080 | P1 | Every PR |
| WebKit | 1920×1080 | P1 | Every PR |
| Chromium | 1366×768 | P1 | Every PR |
| Mobile Chrome (Pixel 7) | 412×915 | P2 | Nightly |
| Mobile Safari (iPhone 14) | 390×844 | P2 | Nightly |
| Chromium (4K) | 3840×2160 | P3 | Weekly |

---

## 6. Flakiness Prevention

### 6.1 Anti-Flake Patterns

```typescript
// DO: Use auto-waiting (Playwright handles this)
await page.getByRole('button', { name: 'Submit' }).click();

// DON'T: Explicit waits
// await page.waitForTimeout(2000); // NEVER

// DO: Wait for specific condition
await page.waitForResponse(resp => resp.url().includes('/api/') && resp.status() === 200);

// DO: Use test isolation
test.describe.configure({ mode: 'parallel' });

// DO: Use unique test data
const uniqueId = `test_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;

// DO: Retry assertions (not actions)
await expect(page.getByText('Success')).toBeVisible({ timeout: 5_000 });

// DO: Use network-idle for page loads
await page.waitForLoadState('networkidle');
```

### 6.2 Flake Detection & Quarantine

```yaml
# In CI: Run each test 3 times to detect flakes before merge
- bash: |
    npx playwright test --repeat-each 3 --shard=${{ strategy.shard }}
  displayName: 'E2E Tests (flake detection mode)'
  condition: eq(variables['Build.Reason'], 'PullRequest')

# Mark known flaky tests
# In the test file:
# test.fixme('flaky test name', async () => { ... });
# Or use annotation:
# test('test name', { tag: '@flaky' }, async () => { ... });
```

---

## 7. Execution Speed Targets

| Metric | Target |
|--------|--------|
| Full E2E suite (all browsers) | < 10 minutes |
| Single browser | < 4 minutes |
| Sharded execution (4 workers) | < 3 minutes |
| Single critical journey test | < 30 seconds |
| Authentication setup | < 5 seconds (cached) |

### Speed Optimization

```typescript
// 1. Parallel execution with sharding
// playwright.config.ts
{
  workers: process.env.CI ? 4 : undefined,
  fullyParallel: true
}

// 2. Shared auth state (authenticate once, reuse)
// See auth.setup.ts above

// 3. API-based data setup (not UI-based)
// Create test data via API calls, not by clicking through UI

// 4. Selective test execution on PR
// Only run tests for changed modules
```

---

## 8. Reporting & Artifacts

### 8.1 Pipeline Integration

```yaml
- task: Bash@3
  inputs:
    targetType: 'inline'
    script: |
      npx playwright test \
        --reporter=junit,html \
        --output=playwright-results
  displayName: 'Run E2E Tests'
  env:
    BASE_URL: $(TEST_ENVIRONMENT_URL)
    E2E_ADMIN_USER: $(E2E_ADMIN_USER)
    E2E_ADMIN_PASS: $(E2E_ADMIN_PASS)

- task: PublishTestResults@2
  condition: always()
  inputs:
    testResultsFormat: 'JUnit'
    testResultsFiles: '**/e2e-results.xml'
    testRunTitle: 'E2E Test Results'

- task: PublishPipelineArtifact@1
  condition: failed()
  inputs:
    targetPath: 'playwright-results'
    artifactName: 'e2e-traces-$(System.JobId)'
  displayName: 'Upload Traces on Failure'
```

### 8.2 Trace Viewer on Failure

When a test fails in CI:
1. Traces are uploaded as pipeline artifacts
2. Download and run `npx playwright show-trace trace.zip`
3. Full timeline: screenshots, network, console, DOM snapshots
4. Pinpoint exactly where and why the test failed

---

*Previous: [03-CONTRACT-TESTING.md](03-CONTRACT-TESTING.md) · Next: [05-VISUAL-REGRESSION-TESTING.md](05-VISUAL-REGRESSION-TESTING.md)*
