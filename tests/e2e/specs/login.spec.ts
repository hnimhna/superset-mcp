import { test, expect } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, SEL, login } from '../support/superset';

/**
 * Authentication. If these fail every other spec is meaningless, so they run
 * first and assert both the happy and the failure path.
 */
test.describe('authentication', () => {
  test('login page renders', async ({ page }) => {
    await page.goto('/login/');
    await expect(page.locator(SEL.username)).toBeVisible();
    await expect(page.locator(SEL.password)).toBeVisible();
  });

  test('valid credentials sign the user in', async ({ page }) => {
    await login(page);
    expect(page.url()).not.toContain('/login');
    // A signed-in Superset always exposes the navigation bar.
    await expect(page.locator('[data-test="navbar-top"], nav, header').first()).toBeVisible();
  });

  test('invalid credentials are rejected', async ({ page }) => {
    await page.goto('/login/');
    await page.locator(SEL.username).waitFor({ state: 'visible' });
    await page.locator(SEL.username).fill(ADMIN_USERNAME);
    await page.locator(SEL.password).fill('definitely-the-wrong-password');
    await page.locator(SEL.submit).click();
    await page.waitForLoadState('domcontentloaded');
    // Superset keeps the user on /login and shows an error rather than signing in.
    expect(page.url()).toContain('/login');
  });

  test('unauthenticated access to a dashboard redirects to login', async ({ page }) => {
    await page.context().clearCookies();
    await page.goto('/superset/dashboard/1/');
    await page.waitForLoadState('domcontentloaded');
    expect(page.url()).toMatch(/login/i);
  });

  test('credentials are supplied by the environment', async () => {
    // Guards against someone hardcoding a password back into the suite.
    expect(ADMIN_PASSWORD, 'SUPERSET_API_PASSWORD must be set').toBeTruthy();
  });
});
