# Metric standard

A metric is a **business definition**, not a SQL snippet. It is the unit of
agreement between the data platform and the people using the numbers.

## Every metric must define

| Field | Requirement | Enforced |
|---|---|---|
| `metric_name` | unique, lowercase `snake_case`, semantic | yes |
| `expression` | valid, non-empty SQL | yes |
| `description` | the business definition in plain language | yes |
| `verbose_name` | human-readable label for the UI | convention |
| `d3format` | explicit number format | convention |
| `metric_type` | aggregation rule (`sum`, `count_distinct`, `avg`, …) | convention |
| owner | the team accountable for the definition | `sql/metrics/*.sql` |
| unit | currency, count, ratio, percentage | `sql/metrics/*.sql` |
| validation query | how to reconcile it independently | `sql/validation/` |

`scripts/validate.py` fails on a metric with no expression, no description
(except the auto-generated `count`), a throwaway name, or SQL identical to
another metric under a different name.

## Define once

The rule that matters most: **never define the same business metric twice with
slightly different SQL.**

Two dashboards each defining their own `revenue` is how a company ends up with
two different revenue numbers in the same meeting. The centralized definition
lives on the dataset; charts reference it by name. `validate.py` rejects
duplicate SQL under different names.

## Worked example

```yaml
metric_name: revenue
verbose_name: Revenue
expression: SUM(CASE WHEN order_status = 'completed' THEN net_amount ELSE 0 END)
description: >-
  Sum of completed order revenue, net of discounts, excluding cancelled
  and pending orders.
d3format: "$,.2f"
metric_type: sum
```

- **Business definition** — completed revenue, net of discounts.
- **Owner** — Data Platform Team.
- **Unit** — currency.
- **Aggregation** — additive over every dimension.
- **Validation** — reconciles against
  `sql/validation/validate_sales_dataset.sql` query 5.

## Additive vs non-additive

This distinction causes more wrong numbers than any other.

**Additive** metrics can be summed across any dimension: `revenue`,
`order_count`, `gross_margin`.

**Non-additive** metrics cannot. `average_order_value` is a ratio and must be
recomputed at every level of aggregation:

```sql
-- Correct: a ratio of two sums, recomputed after any filter or roll-up.
SUM(CASE WHEN order_status = 'completed' THEN net_amount ELSE 0 END)
    / NULLIF(COUNT(DISTINCT CASE WHEN order_status = 'completed'
                                 THEN order_id END), 0)

-- Wrong: averaging an average. Gives each group equal weight regardless of
-- how many orders it contains.
AVG(order_value)
```

Expressing it as a ratio of two sums is what makes it correct after filtering
and roll-up. Always guard the denominator with `NULLIF(..., 0)`.

Document the aggregation rule in `sql/metrics/*.sql` so the next person does
not have to re-derive it.

## The SALES metrics

| Metric | Definition | Unit | Additive |
|---|---|---|---|
| `revenue` | completed order revenue, net of discounts | currency | yes |
| `order_count` | distinct completed orders | count | yes |
| `average_order_value` | completed revenue ÷ completed order count | currency/order | **no** |
| `gross_margin` | completed net revenue less cost of goods sold | currency | yes |

Canonical definitions with ownership and aggregation notes:
[`sql/metrics/sales_metrics.sql`](../sql/metrics/sales_metrics.sql).

## Validation

Every metric must be independently reconcilable. `smoke_test.py` compiles all
metrics into a real query and executes them, and
`sql/validation/validate_sales_dataset.sql` computes them directly against the
warehouse for comparison.

Current reconciliation: `revenue = 2,846,340.50` from raw SQL, matching the
value rendered on `SALES_EXECUTIVE_OVERVIEW`.
