import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for the Superset GitOps platform.
 *
 * These tests exist because API tests are not sufficient: a dashboard can
 * return 200 from every endpoint and still render as a wall of error tiles.
 * Only a browser proves the thing a user actually sees.
 *
 * Credentials come from the environment, never from this file.
 */
const SUPERSET_URL = process.env.SUPERSET_URL ?? 'http://localhost:8088';

export default defineConfig({
  testDir: './tests/e2e/specs',
  // Dashboard rendering is genuinely slow; a tight timeout produces flaky
  // failures that get ignored, which is worse than a slow suite.
  timeout: 120_000,
  expect: {
    timeout: 30_000,
    toHaveScreenshot: {
      // Anti-aliasing and chart animation differ slightly between runs and
      // between machines. This tolerance catches "the chart did not render"
      // without failing on sub-pixel noise.
      maxDiffPixelRatio: 0.02,
      animations: 'disabled',
    },
  },
  fullyParallel: false,      // shared Superset instance; keep runs predictable
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never' }], ['json', { outputFile: 'test-results/results.json' }]]
    : [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: SUPERSET_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
    ignoreHTTPSErrors: true,
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1600, height: 1000 },
      },
    },
  ],

  outputDir: 'test-results',
});
