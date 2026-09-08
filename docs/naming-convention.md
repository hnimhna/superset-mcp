# Naming conventions

These are **enforced**, not advisory. `scripts/validate.py` fails the build on a
violation, and the rules live as executable regexes in
`scripts/lib/conventions.py` so the prose and the check cannot drift apart.

## Why enforce naming at all

A BI platform accumulates `Untitled Dashboard`, `chart (1)`, `test_metric` and
`copy of revenue` unless something prevents it. Those names are unsearchable,
they make ownership ambiguous, and they turn a dashboard list into an
archaeology exercise. Enforcement is cheap; cleanup later is not.

## Domains

Every asset belongs to a business domain:

```
SALES  FINANCE  MARKETING  OPS  PRODUCT
```

Adding a domain is a governance decision — edit `DOMAINS` in
`scripts/lib/conventions.py`, deliberately.

## Database — `<DOMAIN>_<SYSTEM>_<ENV>`

```
SALES_DWH_PROD          FINANCE_DWH_DEV          MARKETING_LAKEHOUSE_STAGING
```

Environments: `DEV`, `STAGING`, `PROD`, `TEST`.

**In Git the environment is a placeholder**, because one file serves every
environment:

```yaml
database_name: SALES_DWH_${ENV}
uuid: 75c590eb-27e3-5327-b659-a6f31c462b14   # the real, stable identity
```

`${ENV}` resolves at import time from `config/environments/<env>.yaml`.

## Dataset — `<DOMAIN>_<SUBJECT>_<GRAIN>`

```
SALES_ORDER_DAILY       SALES_REVENUE_MONTHLY     FINANCE_LEDGER_DAILY
```

The grain is part of the name because "which row is one row?" is the first
question anyone asks of a dataset, and answering it in the name prevents the
most common class of BI error — summing something at the wrong grain.

Rejected: `orders`, `sales_order_daily` (lowercase), `TMP_TABLE`.

## Chart — `<DOMAIN>_<PURPOSE>_<VISUALIZATION>`

```
SALES_REVENUE_TREND         SALES_REVENUE_BY_REGION      SALES_TOP_CUSTOMERS
```

Rejected outright: names starting `Untitled`, `New chart`, `Copy of`, `test`.

## Dashboard — `<DOMAIN>_<BUSINESS_PURPOSE>`

```
SALES_EXECUTIVE_OVERVIEW    SALES_PERFORMANCE      FINANCE_EXECUTIVE_OVERVIEW
```

The purpose names an audience or a decision, not a data source.
`SALES_EXECUTIVE_OVERVIEW` tells you who it is for; `SALES_ORDERS_DATA` does not.

## Metric — lowercase `snake_case`, semantic

```
revenue    order_count    average_order_value    gross_margin
```

Rejected by name:

```
metric1   metric_1   test_metric   tmp   temp_metric   aaa
foo       bar        test          new_metric         untitled   copy_of_metric
```

Also rejected: two metrics with **identical SQL under different names**. A
business concept is defined once. See [metric-standard.md](metric-standard.md).

## Quick reference

| Asset | Pattern | Example | Enforced by |
|---|---|---|---|
| Database | `<DOMAIN>_<SYSTEM>_<ENV>` | `SALES_DWH_PROD` | `DATABASE_RE` |
| Dataset | `<DOMAIN>_<SUBJECT>_<GRAIN>` | `SALES_ORDER_DAILY` | `DATASET_RE` |
| Chart | `<DOMAIN>_<PURPOSE>_<VIZ>` | `SALES_REVENUE_TREND` | `CHART_RE` |
| Dashboard | `<DOMAIN>_<BUSINESS_PURPOSE>` | `SALES_EXECUTIVE_OVERVIEW` | `DASHBOARD_RE` |
| Metric | `snake_case`, semantic | `average_order_value` | `METRIC_RE` |

## Checking

```bash
.venv/bin/python scripts/validate.py
.venv/bin/python -m pytest tests/unit/test_conventions.py -q
```
