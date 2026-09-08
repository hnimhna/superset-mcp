# Chart standard

## Every chart must define

| Field | Requirement |
|---|---|
| `slice_name` | per [naming-convention.md](naming-convention.md) |
| `description` | **the business question it answers** |
| `dataset_uuid` | the dataset it reads (stable identity) |
| metrics | referenced from the dataset's semantic layer, never inline |
| dimensions | explicit `groupby` / `x_axis` |
| filters | explicit `adhoc_filters` |
| time grain | `time_grain_sqla` for temporal charts |
| sorting | explicit ordering for any ranked chart |
| formatting | explicit `d3format` / `y_axis_format` |
| owner | `owners` |

## Start from a question

The `description` field holds the business question. If it cannot be stated in
one sentence, the chart does not belong on a dashboard:

```yaml
slice_name: SALES_REVENUE_TREND
description: "Business question: how is revenue trending month over month?"
```

```yaml
slice_name: SALES_TOP_CUSTOMERS
description: "Business question: who are the highest-revenue customers?"
```

This is not paperwork. A chart whose question nobody can state is a chart
nobody can act on, and it will still be there in two years confusing people.

## Use the semantic layer

```yaml
# Correct - reference a defined metric.
metrics: [revenue]

# Wrong - an inline expression forks the definition of revenue.
metrics:
  - expressionType: SQL
    sqlExpression: SUM(net_amount)
```

Inline SQL in a chart is how two dashboards end up disagreeing. Define the
metric on the dataset; see [metric-standard.md](metric-standard.md).

## Formatting

Always explicit — an unformatted currency value reads as a bug:

| Kind | Format |
|---|---|
| Currency | `$,.2f` |
| Count | `,d` |
| Percentage | `.1%` |
| Large numbers | `.3s` (1.23M) |

Keep formats consistent across a dashboard.

## Ranked charts

A "top N" chart needs explicit ordering, or it shows an arbitrary N:

```yaml
slice_name: SALES_TOP_CUSTOMERS
params:
  row_limit: 25
  order_desc: true
  metrics: [revenue, order_count, average_order_value]
  groupby: [customer_name, region]
```

Superset shows a row-limit warning on such charts. That is expected and
correct for a deliberate top-N view — it tells the reader they are seeing a
subset.

## What normalization removes

Chart assets in Git deliberately do **not** contain:

- `query_context` — a derived cache full of numeric datasource IDs, regenerated
  by Superset and rewritten on import.
- `params.datasource` — a numeric pointer; the importer always sets it from
  `dataset_uuid`.

The real dependency edge is `dataset_uuid`, and `validate.py` checks it
resolves. Do not add these fields back by hand.

## Checklist

- [ ] Name follows `<DOMAIN>_<PURPOSE>_<VISUALIZATION>`
- [ ] `description` states the business question
- [ ] Metrics come from the dataset's semantic layer
- [ ] Dimensions and filters explicit
- [ ] Time grain set for temporal charts
- [ ] Ordering explicit for ranked charts
- [ ] Number formats explicit
- [ ] Owner assigned
- [ ] Query executes (`smoke_test.py`)
- [ ] Renders standalone in Explore (`chart-render.spec.ts`)
