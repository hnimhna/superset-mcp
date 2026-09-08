import { test, expect } from '@playwright/test';
import {
  DASHBOARD_TITLE,
  EXPECTED_CHARTS,
  collectRenderErrors,
  login,
  openDashboard,
} from '../support/superset';

/**
 * Dashboard rendering.
 *
 * The reason this suite exists: a dashboard whose charts all return HTTP 200
 * can still render as a grid of "An error occurred" tiles. Only the browser
 * can tell the difference.
 */
test.describe('dashboard rendering', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('dashboard opens and shows its title', async ({ page }) => {
    await openDashboard(page, DASHBOARD_TITLE);
    await expect(page.getByText(DASHBOARD_TITLE).first()).toBeVisible();
  });

  test('every expected chart is present', async ({ page }) => {
    await openDashboard(page, DASHBOARD_TITLE);
    for (const name of EXPECTED_CHARTS) {
      await expect(
        page.getByText(name, { exact: false }).first(),
        `chart ${name} is missing from the dashboard`,
      ).toBeVisible();
    }
  });

  test('the KPI card shows a formatted number', async ({ page }) => {
    await openDashboard(page, DASHBOARD_TITLE);
    const kpi = page.locator('.header-line, [data-test="big-number-container"], .big_number_total').first();
    await expect(kpi).toBeVisible();
    const text = (await kpi.textContent()) ?? '';
    // The KPI is currency-formatted; an empty or NaN card means a broken query.
    expect(text.trim(), 'KPI card is empty').not.toEqual('');
    expect(text).not.toContain('NaN');
  });

  test('charts paint actual content, not just containers', async ({ page }) => {
    await openDashboard(page, DASHBOARD_TITLE);
    // ECharts renders into <canvas>; the table chart renders a <table>.
    const painted = await page.locator('canvas, .table-condensed, [data-test="table-container"] table').count();
    expect(painted, 'no chart painted any content').toBeGreaterThan(0);
  });

  test('no rendering errors are surfaced', async ({ page }) => {
    await openDashboard(page, DASHBOARD_TITLE);
    const errors = await collectRenderErrors(page);
    expect(errors, `dashboard surfaced errors: ${errors.join(' | ')}`).toEqual([]);
  });

  test('no chart reports a failed query', async ({ page }) => {
    await openDashboard(page, DASHBOARD_TITLE);
    const body = (await page.locator('body').textContent()) ?? '';
    for (const phrase of ['An error occurred', 'Query returned no results', 'Unexpected error']) {
      expect(body, `dashboard shows "${phrase}"`).not.toContain(phrase);
    }
  });
});
