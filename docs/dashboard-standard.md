# Dashboard standard

## Required metadata

Every production dashboard must carry:

| Requirement | Where it lives |
|---|---|
| Meaningful title | `dashboard_title`, per [naming-convention.md](naming-convention.md) |
| Description | `description` — the business purpose and audience |
| Owner | `owners` — an accountable team or person |
| Domain | encoded in the title prefix (`SALES_`, `FINANCE_`, …) |
| Business purpose | `description` — the decision it supports |
| Refresh expectation | `json_metadata.refresh_frequency` |
| Source dataset | implied by chart references; validated by `validate.py` |
| Certification status | `certified_by` + `certification_details` |
| Last validation | deployment record in `.deployments/<env>.jsonl` |

Reference implementation:
[`assets/dashboards/SALES_EXECUTIVE_OVERVIEW.yaml`](../assets/dashboards/SALES_EXECUTIVE_OVERVIEW.yaml).

```yaml
dashboard_title: SALES_EXECUTIVE_OVERVIEW
certified_by: Data Platform Team
certification_details: Validated against sales.vw_order_daily.
published: true
```

## Visual hierarchy

Layout follows a fixed reading order, top to bottom:

```
┌────────────────────────────────────────────┐
│ 1. KPI summary      the headline number    │
├──────────────────────┬─────────────────────┤
│ 2. Trend             │ 3. Breakdown        │
│    change over time  │    by dimension     │
├──────────────────────┴─────────────────────┤
│ 4. Detailed table    the underlying rows   │
└────────────────────────────────────────────┘
   5. Filters — in the native filter bar
```

The order mirrors how someone actually reads a dashboard: *what is the number*,
*is it moving*, *where is it coming from*, *show me the rows*. Putting a table
first forces every reader to hunt for the headline.

`tests/e2e/specs/regression.spec.ts` asserts this by vertical position — the
KPI must sit above the trend chart, and the detail table below it — so the
hierarchy is enforced rather than merely documented.

## Filters

- Use **native filters**, not per-chart filter boxes.
- Scope them explicitly; a filter that silently misses a chart produces a
  dashboard where two tiles disagree.
- Give every filter a description.
- Reference the dataset by `datasetUuid`, never `datasetId` — the numeric id is
  environment-specific and normalization strips it.

```yaml
native_filter_configuration:
  - id: NATIVE_FILTER-region
    name: Region
    filterType: filter_select
    targets:
      - datasetUuid: 765ad87c-689e-59a5-82c5-4c5ce4556aca
        column: {name: region}
    description: Restrict every chart to the selected sales regions.
```

A filter must actually affect the data. `dashboard-filter.spec.ts` applies the
Region filter and asserts the KPI value changes — a filter that renders but
does not reach the query is a silent correctness bug.

## Avoid

| Anti-pattern | Why |
|---|---|
| More than ~8 charts | nobody reads the ninth; split by audience |
| Redundant metrics | two tiles showing the same number invite doubt about which is right |
| Unreadable tables | cap rows; a dashboard is not an export |
| Inconsistent terminology | "revenue" and "sales" on one dashboard means two things or careless naming |
| Inconsistent number formats | `$1,234.56` next to `1234.5601` reads as a bug |
| Charts without a question | if you cannot state the question, delete the chart |

## Checklist

- [ ] Title follows `<DOMAIN>_<BUSINESS_PURPOSE>`
- [ ] Description states audience and decision supported
- [ ] Owner assigned
- [ ] `certified_by` / `certification_details` set
- [ ] Layout follows KPI → trend → breakdown → detail
- [ ] Native filters scoped and described
- [ ] Number formats consistent
- [ ] Every chart answers a stated business question
- [ ] `validate.py` passes
- [ ] `smoke_test.py` passes
- [ ] E2E render and filter specs pass
