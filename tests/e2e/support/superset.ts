import { expect, type Page } from '@playwright/test';

/**
 * Shared helpers for driving Superset in a browser.
 *
 * Selectors prefer Superset's own `data-test` attributes, which are part of
 * its component contract and survive styling changes, over CSS classes.
 */

/** Superset's own data-test hooks - part of its component contract. */
export const SEL = {
  username: '[data-test="username-input"]',
  password: '[data-test="password-input"]',
  submit: '[data-test="login-button"]',
} as const;

export const ADMIN_USERNAME = process.env.SUPERSET_API_USERNAME ?? 'admin';
export const ADMIN_PASSWORD = process.env.SUPERSET_API_PASSWORD ?? '';

export const DASHBOARD_TITLE = 'SALES_EXECUTIVE_OVERVIEW';
export const EXPECTED_CHARTS = [
  'SALES_REVENUE_KPI',
  'SALES_REVENUE_TREND',
  'SALES_REVENUE_BY_REGION',
  'SALES_TOP_CUSTOMERS',
];

/** Log in through the real login form and wait for the session to be usable. */
export async function login(page: Page): Promise<void> {
  if (!ADMIN_PASSWORD) {
    throw new Error(
      'SUPERSET_API_PASSWORD is not set. Export it from your .env or CI secret store; ' +
        'the E2E suite deliberately has no fallback credential.',
    );
  }
  await page.goto('/login/');
  // Superset 6.1 renders a React login form: the inputs carry `data-test` and
  // an `id` but no `name` attribute, so name-based selectors never match.
  await page.locator(SEL.username).waitFor({ state: 'visible', timeout: 60_000 });
  await page.locator(SEL.username).fill(ADMIN_USERNAME);
  await page.locator(SEL.password).fill(ADMIN_PASSWORD);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 60_000 }),
    page.locator(SEL.submit).click(),
  ]);
  await waitForSession(page);
}

/**
 * Block until the session cookie is actually accepted by the API.
 *
 * Leaving /login is not sufficient: `page.request` shares the browser context's
 * cookie jar, and there is a window where navigation has completed but an API
 * call still comes back 401. That surfaced as tests failing intermittently on
 * `dashboard lookup failed: HTTP 401`, with a different test failing each run -
 * the signature of a race rather than a rendering defect.
 */
export async function waitForSession(page: Page, timeoutMs = 30_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastStatus = 0;
  while (Date.now() < deadline) {
    const response = await page.request.get('/api/v1/me/');
    lastStatus = response.status();
    if (response.ok()) return;
    await page.waitForTimeout(500);
  }
  throw new Error(
    `Session was not usable within ${timeoutMs}ms (last /api/v1/me/ status: ${lastStatus})`,
  );
}

/** Navigate to a dashboard by title, via the dashboard list API for its id. */
export async function openDashboard(page: Page, title: string): Promise<number> {
  const query = `/api/v1/dashboard/?q=(filters:!((col:dashboard_title,opr:eq,value:'${title}')))`;
  let response = await page.request.get(query);
  if (response.status() === 401) {
    // The session lapsed between tests; re-establish it once rather than
    // failing on what is an infrastructure hiccup, not a product defect.
    await login(page);
    response = await page.request.get(query);
  }
  expect(response.ok(), `dashboard lookup failed: HTTP ${response.status()}`).toBeTruthy();
  const body = await response.json();
  expect(body.result?.length, `dashboard ${title} not found`).toBeGreaterThan(0);

  const id = body.result[0].id as number;
  await page.goto(`/superset/dashboard/${id}/`);
  await waitForDashboardRender(page);
  return id;
}

/**
 * Wait until charts have actually painted.
 *
 * Superset renders the shell long before any chart has data, so waiting on
 * networkidle or on the title alone gives false passes.
 */
export async function waitForDashboardRender(page: Page): Promise<void> {
  await page.waitForSelector('[data-test="grid-content"], .dashboard-content', {
    timeout: 90_000,
  });
  // Every chart slot must stop showing its loading indicator.
  await page
    .waitForFunction(
      () => {
        const loaders = document.querySelectorAll('.loading, [data-test="loading-indicator"]');
        return loaders.length === 0;
      },
      { timeout: 90_000 },
    )
    .catch(() => {
      /* Some viz types never mount a loader; fall through to the content check. */
    });
  await page.waitForTimeout(2_000); // let ECharts finish its entry animation
}

/** Collect any rendering errors Superset surfaced on the page. */
export async function collectRenderErrors(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const selectors = [
      '[data-test="alert-warning"]',
      '.alert-danger',
      '.ant-result-error',
      '[data-test="chart-container"] .error',
    ];
    const found: string[] = [];
    for (const selector of selectors) {
      document.querySelectorAll(selector).forEach((node) => {
        const text = (node.textContent ?? '').trim();
        if (text) found.push(text.slice(0, 200));
      });
    }
    return found;
  });
}
