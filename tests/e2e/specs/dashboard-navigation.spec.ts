import { test, expect } from '@playwright/test';
import { DASHBOARD_TITLE, login } from '../support/superset';

/**
 * Navigation: the dashboard must be reachable the way a user reaches it, not
 * only by deep-linking to a known id.
 */
test.describe('navigation', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('the dashboard list loads', async ({ page }) => {
    await page.goto('/dashboard/list/');
    await expect(page.locator('[data-test="listview-table"], table').first()).toBeVisible({
      timeout: 60_000,
    });
  });

  test('the managed dashboard appears in the list', async ({ page }) => {
    await page.goto('/dashboard/list/');
    await expect(page.getByText(DASHBOARD_TITLE).first()).toBeVisible({ timeout: 60_000 });
  });

  test('the dashboard opens from the list', async ({ page }) => {
    await page.goto('/dashboard/list/');
    await page.getByText(DASHBOARD_TITLE).first().click();
    await page.waitForURL(/\/superset\/dashboard\//, { timeout: 60_000 });
    await expect(page.getByText(DASHBOARD_TITLE).first()).toBeVisible();
  });

  test('the chart list loads and shows the managed charts', async ({ page }) => {
    await page.goto('/chart/list/');
    await expect(page.locator('[data-test="listview-table"], table').first()).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByText('SALES_REVENUE_TREND').first()).toBeVisible();
  });

  test('the dataset list shows the managed dataset', async ({ page }) => {
    await page.goto('/tablemodelview/list/');
    await expect(page.getByText('SALES_ORDER_DAILY').first()).toBeVisible({ timeout: 60_000 });
  });
});
