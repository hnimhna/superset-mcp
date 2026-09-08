import { test, expect } from '@playwright/test';
import {
  DASHBOARD_TITLE,
  EXPECTED_CHARTS,
  login,
  openDashboard,
} from '../support/superset';

/**
 * Visual regression.
 *
 * Screenshots are compared against committed baselines so that a change which
 * passes every API and DOM assertion but visibly breaks the dashboard - an
 * overlapping layout, a collapsed chart, a legend covering the plot - still
 * fails the build.
 *
 * Baselines live in tests/e2e/specs/regression.spec.ts-snapshots/ and are
 * created on first run. Regenerate deliberately with:
 *     npm run test:e2e:update-snapshots
 * and review the image diff in the pull request like any other change.
 *
 * The comparison tolerance (maxDiffPixelRatio in playwright.config.ts) is set
 * so anti-aliasing noise passes but a missing or empty chart does not.
 */
test.describe('visual regression', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('dashboard matches its visual baseline', async ({ page }) => {
    await openDashboard(page, DASHBOARD_TITLE);

    // Mask elements that legitimately change between runs; leaving them in
    // would make the baseline fail every time and train people to ignore it.
    //
    // [data-test="metadata-bar"] carries the relative "N minutes ago" modified
    // time, which drifts on literally every run - it is the single most
    // important thing to mask here.
    const volatile = [
      page.locator('[data-test="metadata-bar"]'),
      page.locator('[data-test="cached-label"], .ant-tag'),
      page.locator('text=/Last (?:queried|modified)/i'),
    ];

    await expect(page).toHaveScreenshot('sales-executive-overview.png', {
      fullPage: true,
      mask: volatile,
      maxDiffPixelRatio: 0.02,
    });
  });

  test('dashboard layout follows the house visual hierarchy', async ({ page }) => {
    await openDashboard(page, DASHBOARD_TITLE);

    // docs/dashboard-standard.md: KPI summary, then trend and breakdown, then
    // the detail table. Verified by vertical position rather than by eye.
    const positions: Record<string, number> = {};
    for (const name of EXPECTED_CHARTS) {
      const box = await page.getByText(name, { exact: false }).first().boundingBox();
      if (box) positions[name] = box.y;
    }

    expect(Object.keys(positions).length, 'not all charts were located').toBe(
      EXPECTED_CHARTS.length,
    );
    expect(
      positions.SALES_REVENUE_KPI,
      'the KPI summary must sit above the trend chart',
    ).toBeLessThan(positions.SALES_REVENUE_TREND);
    expect(
      positions.SALES_REVENUE_TREND,
      'the detail table must sit below the trend chart',
    ).toBeLessThan(positions.SALES_TOP_CUSTOMERS);
  });

  test('no chart tile collapses to zero height', async ({ page }) => {
    await openDashboard(page, DASHBOARD_TITLE);
    const tiles = page.locator('[data-test="chart-container"], .dashboard-component-chart-holder');
    const count = await tiles.count();
    expect(count, 'no chart tiles found').toBeGreaterThan(0);
    for (let i = 0; i < count; i += 1) {
      const box = await tiles.nth(i).boundingBox();
      if (box) {
        expect(box.height, `chart tile ${i} collapsed to ${box.height}px`).toBeGreaterThan(50);
        expect(box.width, `chart tile ${i} collapsed to ${box.width}px`).toBeGreaterThan(50);
      }
    }
  });
});
