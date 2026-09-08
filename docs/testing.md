# Testing strategy

Four layers, each catching what the layer below cannot.

| Layer | Count | Needs | Runtime |
|---|---|---|---|
| unit | 135 | nothing | ~0.1 s |
| API | 42 | Superset + MCP | ~1.5 s |
| integration | 12 | Superset | ~2 s |
| E2E | 27 | Superset + Chromium | ~3.5 min |

```bash
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python -m pytest tests/api tests/integration -q
npx playwright test
npx playwright test --headed          # watch it
npx playwright test --debug           # step through
npm run test:e2e:update-snapshots     # regenerate visual baselines
```

Unit tests run anywhere. API, integration and E2E **skip cleanly** when no
Superset is reachable rather than failing, so a laptop with nothing running
still gets a meaningful `pytest` result.

## Unit tests — `tests/unit`

No network, no services. These encode the rules that make the repository work.

- `test_normalize.py` — determinism, idempotency, and every normalization rule.
  Each test names the failure it prevents; a normalizer regression is silent
  (the repo keeps working, the diffs just become useless) so the tests are the
  only alarm.
- `test_conventions.py` — naming rules, including the throwaway metric names
  (`metric1`, `test_metric`, `aaa`) that must be rejected.
- `test_secrets.py` — detection of real credentials *and* non-detection of the
  `${PLACEHOLDER}` / `env:` indirection forms. Flagging indirection would train
  people to ignore the scanner. Includes a planted-credential control so the
  "repository is clean" assertions cannot pass vacuously.
- `test_environment.py` — `env:` resolution, loud failure on a missing variable,
  and that prod defaults to read-only MCP.
- `test_assets.py` — runs CI's validation against the committed assets.

## API tests — `tests/api`

Both happy and failure paths, because a client that only works when everything
is fine fails silently in CI.

- `test_superset_api.py` — auth (valid, invalid, unauthenticated, CSRF), asset
  listing, UUID presence, SQL execution (valid and invalid), chart query
  execution, export shape, and import rejection of malformed bundles.
- `test_mcp.py` — the governance suite. Read-only is verified **by
  observation**: it enumerates the served registry and attempts to invoke all
  eight mutating tools, rather than trusting the configuration.

## Integration tests — `tests/integration`

`test_gitops_roundtrip.py` covers the invariants that would have caught the two
real bugs found while building this platform:

- **dependency chain** — database → dataset → metric → chart → dashboard all
  resolve by UUID.
- **export determinism** — two consecutive exports are byte-identical.
- **import idempotency** — repeated deploys create no duplicates and preserve
  UUIDs. *(Would have caught: `/api/v1/dashboard/import/` not overwriting
  existing charts.)*
- **round trip** — `export(import(x)) == x`. *(Caught: the importer adding
  `annotation_layers: []`.)*
- **placeholder resolution** — no literal `${...}` survives into a deployed
  instance.

## E2E tests — `tests/e2e`

Playwright + Chromium. **API tests are not sufficient**: a dashboard can return
200 from every endpoint and still render as a grid of error tiles.

| Spec | Covers |
|---|---|
| `login.spec.ts` | valid/invalid credentials, unauthenticated redirect |
| `dashboard-render.spec.ts` | title, chart presence, KPI value, painted content, no error text |
| `chart-render.spec.ts` | each chart opened standalone in Explore |
| `dashboard-filter.spec.ts` | applying the Region filter **changes the KPI value** |
| `dashboard-navigation.spec.ts` | reachable via the UI, not only by deep link |
| `regression.spec.ts` | screenshot baseline, layout order, collapsed tiles |

The filter test is the one that earns its keep: a filter that renders but does
not reach the query is a silent correctness bug that no API test would notice.

### Visual regression

Baselines live in `tests/e2e/specs/regression.spec.ts-snapshots/` and are
committed — they are the contract.

Two things make them stable rather than flaky:

- **Masking.** `[data-test="metadata-bar"]` holds a relative "N minutes ago"
  timestamp that changes on every run. Unmasked, the baseline fails constantly
  and people learn to ignore it.
- **Tolerance.** `maxDiffPixelRatio: 0.02` absorbs anti-aliasing and chart
  animation noise while still failing on a missing or empty chart.

Regenerate deliberately (`npm run test:e2e:update-snapshots`) and review the
image diff in the pull request like any other change. **Never regenerate a
baseline to make a failing test pass without understanding the diff.**

Baselines are platform-specific: `-chromium-darwin.png` locally,
`-chromium-linux.png` on CI runners. Only the darwin baseline is committed, and
Playwright's behaviour on a missing snapshot is to **write it and fail the
test** - which on CI would fail on every run with no way for CI to fix itself.

The PR gate therefore runs `npx playwright test --grep-invert "visual
regression"`. To enable visual regression in CI, generate a linux baseline once
and commit it:

```bash
docker run --rm --network host -v "$PWD":/w -w /w \
  mcr.microsoft.com/playwright:v1.63.0-jammy \
  npx playwright test tests/e2e/specs/regression.spec.ts --update-snapshots
```

then drop the `--grep-invert` filter from `.github/workflows/validate.yml`.
Until that is done, **CI visual regression is NOT VERIFIED**; the layout-order
and collapsed-tile assertions in the same spec are also skipped by the filter.

### Selector notes

Two facts established against the running instance, both of which broke naive
selectors:

- Superset 6.1's login inputs carry `data-test` and an `id` but **no `name`**,
  so `input[name="username"]` never matches. Use
  `[data-test="username-input"]`.
- `big_number` charts render text into `.header-line` with **no canvas**, so a
  canvas-only "did it paint?" assertion fails a perfectly healthy KPI card. The
  check is viz-aware.

## SQL validation

`sql/validation/validate_sales_dataset.sql` checks the warehouse itself — the
Superset-side smoke test cannot tell you that `order_status` gained an
unexpected value that silently drops rows out of every metric.

```bash
docker compose -f docker/docker-compose.yml --env-file .env exec -T warehouse-db \
  psql -U warehouse -d dwh -f - < sql/validation/validate_sales_dataset.sql
```

It also reconciles the semantic layer against a direct calculation. Current
result: `revenue = 2,846,340.50`, which matches the KPI rendered in the
dashboard — a full reconciliation from raw SQL through the semantic layer to
the browser.

## Smoke test

```bash
.venv/bin/python scripts/smoke_test.py --env dev
.venv/bin/python scripts/smoke_test.py --env prod --json > report.json
```

11 checks: API connectivity, database connectivity (`SELECT 1` through the real
connection), dataset columns and metrics present, **every metric compiled into
executable SQL**, every chart query executed, and every dashboard reference
resolved.

It deliberately does not depend on a saved `query_context` — that field is
excluded from version control, so validation rebuilds the query from the
chart's `params` instead.

## CI

`.github/workflows/validate.yml` runs cheapest-first so the fast, most damaging
checks gate the expensive ones:

```
lint ─┬─ secret-scan ─┬─ validate-assets ─┬─ integration (live Superset:
      │               │                    │   deploy → smoke → API → integration
      └─ unit-tests ──┴────────────────────┘   → determinism → drift → Playwright)
```

> **NOT VERIFIED** — the workflows are written and their YAML parses, but they
> have not executed on GitHub: this repository has no remote configured.
