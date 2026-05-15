# 05 — Visual Regression Testing

## Test Automation · Zero Manual Testing Architecture

---

## Purpose

Visual regression testing automatically detects **unintended visual changes** by comparing screenshots of UI components and pages against approved baselines. It catches CSS regressions, layout breaks, font changes, spacing issues, and rendering bugs that functional tests cannot see.

**What this layer kills:**
- "The button moved 3px left after the CSS refactor" → Pixel diff catches it
- "RTL layout broke in this component" → Snapshot per direction
- "Dark mode has unreadable text" → Theme-specific baselines
- "This looks fine on Chrome but broken on Safari" → Cross-browser snapshots
- "QA spent 2 hours comparing screenshots manually" → Automated in 60 seconds

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   VISUAL REGRESSION PIPELINE                                  │
│                                                                              │
│   ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐               │
│   │  Storybook  │    │  Playwright  │    │  Page-level     │               │
│   │  Component  │    │  Page        │    │  Screenshots    │               │
│   │  Snapshots  │    │  Snapshots   │    │  (Critical      │               │
│   │             │    │              │    │   Paths)         │               │
│   └──────┬──────┘    └──────┬───────┘    └───────┬─────────┘               │
│          │                  │                    │                           │
│          └──────────────────┼────────────────────┘                          │
│                             ▼                                                │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                    COMPARISON ENGINE                                  │   │
│   │                                                                      │   │
│   │  Current Screenshot ←→ Baseline Screenshot                           │   │
│   │                                                                      │   │
│   │  • Pixel-by-pixel diff (configurable threshold)                      │   │
│   │  • Anti-aliasing tolerance                                           │   │
│   │  • Ignore regions (dynamic content, timestamps, ads)                 │   │
│   │  • Perceptual diff (structural similarity index)                     │   │
│   └────────────────────────────────┬────────────────────────────────────┘   │
│                                    │                                         │
│                    ┌───────────────┼───────────────┐                        │
│                    ▼               ▼               ▼                         │
│              ┌──────────┐   ┌──────────┐   ┌──────────────┐                │
│              │  PASS    │   │  REVIEW  │   │  FAIL        │                │
│              │  (< 0.1% │   │  (0.1%   │   │  (> 1% diff  │                │
│              │   diff)  │   │  - 1%)   │   │   OR crash)  │                │
│              └──────────┘   └──────────┘   └──────────────┘                │
│                                    │                                         │
│                                    ▼                                         │
│                          ┌──────────────────┐                               │
│                          │  APPROVAL FLOW   │                               │
│                          │  (PR Comment     │                               │
│                          │   with diff      │                               │
│                          │   image)         │                               │
│                          └──────────────────┘                               │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  Gate: Zero unreviewed visual diffs before merge                             │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Playwright Visual Comparison (Built-in)

### 1.1 Configuration

```typescript
// playwright.config.ts — visual testing additions
import { defineConfig } from '@playwright/test';

export default defineConfig({
  // ... existing config ...
  
  expect: {
    toHaveScreenshot: {
      // Pixel comparison threshold (0 = exact match, 1 = completely different)
      threshold: 0.2,
      // Max allowed pixel difference percentage
      maxDiffPixelRatio: 0.005,
      // Max absolute number of different pixels
      maxDiffPixels: 100,
      // Animation handling
      animations: 'disabled',
      // Consistent rendering
      caret: 'hide',
      scale: 'device'
    },
    toMatchSnapshot: {
      threshold: 0.2
    }
  },

  projects: [
    // Visual tests only on stable Chromium (most deterministic)
    {
      name: 'visual-chromium',
      testMatch: /.*\.visual\.spec\.ts/,
      use: {
        browserName: 'chromium',
        viewport: { width: 1920, height: 1080 },
        storageState: '.auth/admin.json',
        // Consistent font rendering
        launchOptions: {
          args: ['--font-render-hinting=none', '--disable-skia-runtime-opts']
        }
      },
      dependencies: ['setup']
    },
    {
      name: 'visual-rtl',
      testMatch: /.*\.visual\.spec\.ts/,
      use: {
        browserName: 'chromium',
        viewport: { width: 1920, height: 1080 },
        storageState: '.auth/admin.json',
        locale: 'ar-SA'
      },
      dependencies: ['setup']
    },
    {
      name: 'visual-mobile',
      testMatch: /.*\.visual\.spec\.ts/,
      use: {
        browserName: 'chromium',
        viewport: { width: 390, height: 844 },
        storageState: '.auth/admin.json',
        isMobile: true
      },
      dependencies: ['setup']
    }
  ]
});
```

### 1.2 Page-Level Visual Tests

```typescript
// e2e/visual/pages.visual.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Page Visual Regression', () => {
  
  test.beforeEach(async ({ page }) => {
    // Disable animations for consistent screenshots
    await page.addStyleTag({
      content: `
        *, *::before, *::after {
          animation-duration: 0s !important;
          animation-delay: 0s !important;
          transition-duration: 0s !important;
          transition-delay: 0s !important;
        }
      `
    });
  });

  test('dashboard page', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle');
    
    // Mask dynamic content
    await expect(page).toHaveScreenshot('dashboard.png', {
      mask: [
        page.locator('[data-testid="current-date"]'),
        page.locator('[data-testid="notification-count"]'),
        page.locator('[data-testid="recent-activity"]')
      ],
      fullPage: true
    });
  });

  test('exemption list page - with data', async ({ page }) => {
    await page.goto('/admin/exemptions');
    await page.waitForLoadState('networkidle');
    
    await expect(page).toHaveScreenshot('exemption-list.png', {
      mask: [
        page.locator('td:nth-child(1)'), // IDs
        page.locator('[data-testid="timestamp"]')
      ]
    });
  });

  test('exemption create form - empty', async ({ page }) => {
    await page.goto('/admin/exemptions/create');
    await page.waitForLoadState('networkidle');
    
    await expect(page).toHaveScreenshot('exemption-form-empty.png');
  });

  test('exemption create form - with validation errors', async ({ page }) => {
    await page.goto('/admin/exemptions/create');
    await page.getByRole('button', { name: /حفظ|Submit/ }).click();
    
    // Wait for validation to appear
    await expect(page.locator('.field-error')).toBeVisible();
    
    await expect(page).toHaveScreenshot('exemption-form-errors.png');
  });

  test('exemption create form - filled', async ({ page }) => {
    await page.goto('/admin/exemptions/create');
    await page.getByLabel(/رقم الهوية/).fill('1234567890');
    await page.getByLabel(/عدد الطلبات/).fill('5');
    await page.getByLabel(/ملاحظات/).fill('ملاحظة اختبار');
    
    await expect(page).toHaveScreenshot('exemption-form-filled.png');
  });

  test('login page', async ({ browser }) => {
    // Use fresh context (no auth)
    const context = await browser.newContext();
    const page = await context.newPage();
    
    await page.goto('/login');
    await page.waitForLoadState('networkidle');
    
    await expect(page).toHaveScreenshot('login-page.png');
    await context.close();
  });

  test('error page (404)', async ({ page }) => {
    await page.goto('/nonexistent-route');
    await page.waitForLoadState('networkidle');
    
    await expect(page).toHaveScreenshot('404-page.png');
  });
});
```

### 1.3 Component-Level Visual Tests

```typescript
// e2e/visual/components.visual.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Component Visual Regression', () => {

  test('sidebar - expanded', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle');
    
    const sidebar = page.locator('[data-testid="sidebar"]');
    await expect(sidebar).toHaveScreenshot('sidebar-expanded.png');
  });

  test('sidebar - collapsed', async ({ page }) => {
    await page.goto('/dashboard');
    await page.getByTestId('sidebar-toggle').click();
    
    const sidebar = page.locator('[data-testid="sidebar"]');
    await expect(sidebar).toHaveScreenshot('sidebar-collapsed.png');
  });

  test('data table - various states', async ({ page }) => {
    // Empty state
    await page.route('**/api/app/user-request-exemption**', route =>
      route.fulfill({ status: 200, body: JSON.stringify({ totalCount: 0, items: [] }) })
    );
    await page.goto('/admin/exemptions');
    await page.waitForLoadState('networkidle');
    
    const table = page.locator('[data-testid="exemptions-table"]');
    await expect(table).toHaveScreenshot('table-empty.png');
  });

  test('confirmation dialog', async ({ page }) => {
    await page.goto('/admin/exemptions');
    await page.waitForLoadState('networkidle');
    
    // Trigger delete dialog
    const firstDelete = page.locator('[data-testid="delete-btn"]').first();
    if (await firstDelete.isVisible()) {
      await firstDelete.click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toHaveScreenshot('confirm-dialog.png');
    }
  });

  test('toast notifications', async ({ page }) => {
    await page.goto('/admin/exemptions/create');
    
    // Submit valid form to trigger success toast
    await page.getByLabel(/رقم الهوية/).fill('9999999999');
    await page.getByLabel(/عدد الطلبات/).fill('5');
    await page.getByRole('button', { name: /حفظ/ }).click();
    
    const toast = page.locator('.p-toast-message');
    if (await toast.isVisible()) {
      await expect(toast).toHaveScreenshot('toast-success.png');
    }
  });
});
```

### 1.4 Responsive Viewport Tests

```typescript
// e2e/visual/responsive.visual.spec.ts
import { test, expect } from '@playwright/test';

const viewports = [
  { name: 'desktop-hd', width: 1920, height: 1080 },
  { name: 'desktop-md', width: 1366, height: 768 },
  { name: 'tablet-landscape', width: 1024, height: 768 },
  { name: 'tablet-portrait', width: 768, height: 1024 },
  { name: 'mobile-lg', width: 428, height: 926 },
  { name: 'mobile-sm', width: 375, height: 667 }
];

for (const vp of viewports) {
  test.describe(`Viewport: ${vp.name} (${vp.width}x${vp.height})`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test('dashboard layout', async ({ page }) => {
      await page.goto('/dashboard');
      await page.waitForLoadState('networkidle');
      await expect(page).toHaveScreenshot(`dashboard-${vp.name}.png`, {
        fullPage: true,
        mask: [page.locator('[data-testid="dynamic-content"]')]
      });
    });

    test('data table layout', async ({ page }) => {
      await page.goto('/admin/exemptions');
      await page.waitForLoadState('networkidle');
      await expect(page).toHaveScreenshot(`table-${vp.name}.png`, {
        mask: [page.locator('[data-testid="timestamp"]')]
      });
    });
  });
}
```

---

## 2. Storybook + Chromatic (Component Library)

### 2.1 Storybook Setup

```typescript
// .storybook/main.ts
import type { StorybookConfig } from '@storybook/angular';

const config: StorybookConfig = {
  stories: ['../src/**/*.stories.@(ts|tsx)'],
  addons: [
    '@storybook/addon-essentials',
    '@storybook/addon-a11y',
    '@storybook/addon-viewport',
    'storybook-addon-rtl'
  ],
  framework: {
    name: '@storybook/angular',
    options: {}
  }
};

export default config;
```

### 2.2 Component Stories for Visual Testing

```typescript
// src/app/shared/components/data-table/data-table.stories.ts
import type { Meta, StoryObj } from '@storybook/angular';
import { DataTableComponent } from './data-table.component';

const meta: Meta<DataTableComponent> = {
  title: 'Shared/DataTable',
  component: DataTableComponent,
  parameters: {
    chromatic: { viewports: [375, 768, 1200] },
    layout: 'fullscreen'
  }
};

export default meta;
type Story = StoryObj<DataTableComponent>;

export const WithData: Story = {
  args: {
    columns: [
      { field: 'nationalId', header: 'رقم الهوية' },
      { field: 'count', header: 'عدد الطلبات' },
      { field: 'status', header: 'الحالة' }
    ],
    data: Array.from({ length: 10 }, (_, i) => ({
      nationalId: `10000000${i.toString().padStart(2, '0')}`,
      count: i + 1,
      status: i % 2 === 0 ? 'active' : 'expired'
    })),
    totalRecords: 50,
    loading: false
  }
};

export const Empty: Story = {
  args: {
    columns: [
      { field: 'nationalId', header: 'رقم الهوية' },
      { field: 'count', header: 'عدد الطلبات' }
    ],
    data: [],
    totalRecords: 0,
    loading: false
  }
};

export const Loading: Story = {
  args: {
    ...WithData.args,
    data: [],
    loading: true
  }
};

export const RTL: Story = {
  ...WithData,
  parameters: {
    direction: 'rtl'
  }
};

export const ManyColumns: Story = {
  args: {
    columns: Array.from({ length: 12 }, (_, i) => ({
      field: `col${i}`,
      header: `عمود ${i + 1}`
    })),
    data: Array.from({ length: 5 }, () => 
      Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`col${i}`, `قيمة ${i}`]))
    ),
    totalRecords: 5,
    loading: false
  }
};
```

### 2.3 Chromatic Pipeline Integration

```yaml
# Visual regression via Chromatic
- bash: |
    npx chromatic \
      --project-token="$(CHROMATIC_PROJECT_TOKEN)" \
      --branch-name="$(Build.SourceBranchName)" \
      --build-script-name="build-storybook" \
      --exit-zero-on-changes \
      --only-changed \
      --externals="src/styles/**" \
      --skip="@(dependabot|renovate)/**"
  displayName: 'Visual Regression (Chromatic)'
  env:
    CHROMATIC_PROJECT_TOKEN: $(CHROMATIC_TOKEN)
```

---

## 3. Baseline Management

### 3.1 Updating Baselines

```bash
# Update all baselines (after intentional visual change)
npx playwright test --update-snapshots

# Update specific test baselines
npx playwright test visual/pages.visual.spec.ts --update-snapshots

# Update only for specific project
npx playwright test --project=visual-chromium --update-snapshots
```

### 3.2 Baseline Storage Strategy

```
e2e/
├── visual/
│   ├── pages.visual.spec.ts
│   ├── components.visual.spec.ts
│   └── responsive.visual.spec.ts
├── visual.spec.ts-snapshots/          ← Generated baselines
│   ├── visual-chromium/
│   │   ├── dashboard.png
│   │   ├── exemption-list.png
│   │   └── login-page.png
│   ├── visual-rtl/
│   │   ├── dashboard.png
│   │   └── exemption-list.png
│   └── visual-mobile/
│       ├── dashboard.png
│       └── exemption-list.png
└── test-results/                      ← Failure artifacts (gitignored)
    ├── dashboard-actual.png
    ├── dashboard-expected.png
    └── dashboard-diff.png
```

### 3.3 Git LFS for Baselines

```gitattributes
# .gitattributes
e2e/**/*-snapshots/**/*.png filter=lfs diff=lfs merge=lfs -text
```

---

## 4. Handling Dynamic Content

### 4.1 Masking Strategy

```typescript
// Mask strategy: hide content that changes between runs
const DYNAMIC_MASKS = {
  timestamps: '[data-testid*="timestamp"], [data-testid*="date"], time',
  userContent: '[data-testid*="user-name"], [data-testid*="avatar"]',
  counts: '[data-testid*="notification-count"], .badge',
  animations: '.loading-spinner, .skeleton'
};

async function screenshotWithMasks(page: Page, name: string, extra: string[] = []) {
  const allMasks = [
    ...Object.values(DYNAMIC_MASKS).map(s => page.locator(s)),
    ...extra.map(s => page.locator(s))
  ];
  
  await expect(page).toHaveScreenshot(name, { mask: allMasks, fullPage: true });
}
```

### 4.2 Freezing Dynamic Data

```typescript
// Mock APIs to return consistent data for visual tests
test.beforeEach(async ({ page }) => {
  // Freeze time for consistent timestamps
  await page.addInitScript(() => {
    const fixedDate = new Date('2024-06-15T10:30:00Z');
    const OriginalDate = Date;
    
    // @ts-ignore
    window.Date = class extends OriginalDate {
      constructor(...args: any[]) {
        if (args.length === 0) return new OriginalDate(fixedDate);
        // @ts-ignore
        return new OriginalDate(...args);
      }
      static now() { return fixedDate.getTime(); }
    };
  });

  // Mock API responses with deterministic data
  await page.route('**/api/app/user-request-exemption**', route =>
    route.fulfill({
      status: 200,
      body: JSON.stringify({
        totalCount: 3,
        items: [
          { id: '1', nationalId: '1234567890', exemptedRequestCount: 5, creationTime: '2024-06-01T10:00:00Z' },
          { id: '2', nationalId: '0987654321', exemptedRequestCount: 3, creationTime: '2024-06-05T14:30:00Z' },
          { id: '3', nationalId: '5555555555', exemptedRequestCount: 10, creationTime: '2024-06-10T09:15:00Z' }
        ]
      })
    })
  );
});
```

---

## 5. Diff Thresholds & Rules

| Component Type | Max Diff Pixels | Threshold | Rationale |
|---------------|----------------|-----------|-----------|
| Critical pages (login, forms) | 0 | 0.1 | Must be pixel-perfect |
| Data tables | 50 | 0.2 | Minor anti-aliasing OK |
| Charts/graphs | 200 | 0.3 | Rendering varies slightly |
| Animations (frozen) | 100 | 0.2 | Frame timing varies |
| Mobile viewports | 100 | 0.25 | Font hinting differs |

---

## 6. Integration with PR Workflow

### 6.1 PR Comment with Visual Diff

```yaml
# Post visual diff results to PR
- bash: |
    # Find all diff images
    diffs=$(find test-results -name "*-diff.png" 2>/dev/null | head -20)
    
    if [ -n "$diffs" ]; then
      echo "##[warning]Visual differences detected!"
      echo "##vso[task.setvariable variable=HAS_VISUAL_DIFFS]true"
      
      # Create summary
      count=$(echo "$diffs" | wc -l)
      echo "Found $count visual differences"
    fi
  displayName: 'Check Visual Diffs'

- task: PublishPipelineArtifact@1
  condition: eq(variables['HAS_VISUAL_DIFFS'], 'true')
  inputs:
    targetPath: 'test-results'
    artifactName: 'visual-diffs'
  displayName: 'Upload Visual Diffs'
```

---

## 7. Metrics

| Metric | Target | Alert |
|--------|--------|-------|
| Visual test pass rate | 100% (after approval) | Any unapproved diff blocks PR |
| Baseline freshness | Updated within 7 days of UI change | Stale warning |
| False positive rate | < 5% | > 10% → adjust thresholds |
| Execution time (full suite) | < 3 minutes | > 5 min → optimize |
| Pages/components covered | > 90% of routes | Track coverage map |

---

*Previous: [04-E2E-UI-TESTING.md](04-E2E-UI-TESTING.md) · Next: [06-PERFORMANCE-TESTING.md](06-PERFORMANCE-TESTING.md)*
