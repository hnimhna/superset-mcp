import { test, expect } from '@playwright/test';
import { DASHBOARD_TITLE, login, openDashboard, waitForDashboardRender } from '../support/superset';

/**
 * Native dashboard filters.
 *
 * The SALES_EXECUTIVE_OVERVIEW dashboard defines a native filter on `region`.
 * Applying it must change what the charts show - a filter that renders but
 * does not affect the data is a silent correctness bug.
 */
test.describe('dashboard filters', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await openDashboard(page, DASHBOARD_TITLE);
  });

  test('the native filter bar is present', async ({ page }) => {
    const filterBar = page.locator('[data-test="filter-bar"], [data-test="dashboard-filters-panel"]');
    await expect(filterBar.first()).toBeVisible({ timeout: 60_000 });
  });

  test('the Region filter is offered', async ({ page }) => {
    await expect(page.getByText('Region', { exact: false }).first()).toBeVisible({
      timeout: 60_000,
    });
  });

  test('applying a region filter changes the KPI value', async ({ page }) => {
    const kpiSelector = '.header-line, [data-test="big-number-container"], .big_number_total';
    const before = (await page.locator(kpiSelector).first().textContent())?.trim() ?? '';
    expect(before, 'KPI had no value before filtering').not.toEqual('');

    // Open the filter's select and choose the first available region.
    const filterControl = page
      .locator('[data-test="filter-bar"] .ant-select, [data-test="filter-control"] .ant-select')
      .first();
    await filterControl.click({ timeout: 30_000 });
    const option = page.locator('.ant-select-item-option').first();
    await option.waitFor({ state: 'visible', timeout: 30_000 });
    const chosen = (await option.textContent())?.trim();
    await option.click();

    // Apply, if this Superset build requires an explicit apply.
    const apply = page.locator('[data-test="filter-bar__apply-button"]');
    if (await apply.count()) {
      await apply.first().click();
    }
    await waitForDashboardRender(page);

    const after = (await page.locator(kpiSelector).first().textContent())?.trim() ?? '';
    expect(
      after,
      `KPI did not change after filtering to region "${chosen}" (before=${before}, after=${after}); ` +
        'the filter is not reaching the query',
    ).not.toEqual(before);
  });

  test('charts still render with a filter applied', async ({ page }) => {
    const filterControl = page
      .locator('[data-test="filter-bar"] .ant-select, [data-test="filter-control"] .ant-select')
      .first();
    await filterControl.click({ timeout: 30_000 });
    const option = page.locator('.ant-select-item-option').first();
    await option.waitFor({ state: 'visible', timeout: 30_000 });
    await option.click();
    const apply = page.locator('[data-test="filter-bar__apply-button"]');
    if (await apply.count()) await apply.first().click();
    await waitForDashboardRender(page);

    const text = (await page.locator('body').textContent()) ?? '';
    expect(text).not.toContain('An error occurred');
    expect(await page.locator('canvas, table').count()).toBeGreaterThan(0);
  });
});
