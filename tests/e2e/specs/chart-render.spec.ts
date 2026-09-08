import { test, expect } from '@playwright/test';
import { EXPECTED_CHARTS, login } from '../support/superset';

/**
 * Individual charts in Explore.
 *
 * A chart can render correctly inside a dashboard and still be broken when
 * opened on its own (missing dataset permission, stale form_data), so each is
 * also exercised standalone.
 */
test.describe('chart rendering', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  for (const name of EXPECTED_CHARTS) {
    test(`${name} renders in Explore`, async ({ page }) => {
      const response = await page.request.get(
        `/api/v1/chart/?q=(filters:!((col:slice_name,opr:eq,value:'${name}')))`,
      );
      expect(response.ok()).toBeTruthy();
      const body = await response.json();
      expect(body.result?.length, `chart ${name} not found`).toBeGreaterThan(0);

      await page.goto(`/explore/?slice_id=${body.result[0].id}`);
      await page.waitForSelector('[data-test="chart-container"], .chart-container', {
        timeout: 90_000,
      });
      await page.waitForTimeout(3_000);

      const text = (await page.locator('body').textContent()) ?? '';
      expect(text, `${name} shows an error in Explore`).not.toContain('An error occurred');

      // Something must actually have painted. The check is viz-aware: ECharts
      // draws into <canvas>, table charts emit <table>, and big_number renders
      // plain text into `.header-line` with no canvas at all - asserting on
      // canvas alone would fail a perfectly healthy KPI card.
      const painted = await page
        .locator(
          '[data-test="chart-container"] canvas, [data-test="chart-container"] svg, ' +
            '[data-test="chart-container"] table, .chart-container canvas, .chart-container table',
        )
        .count();
      const bigNumber = (await page.locator('.header-line').first().textContent().catch(() => ''))?.trim() ?? '';
      expect(
        painted > 0 || bigNumber.length > 0,
        `${name} painted nothing (no canvas/svg/table and no big-number value)`,
      ).toBeTruthy();
      if (bigNumber) {
        expect(bigNumber, `${name} rendered a NaN value`).not.toContain('NaN');
      }
    });
  }
});
