# Datasource standard

Covers database connections and datasets.

## Database connections

### Requirements

| Requirement | Rule |
|---|---|
| Name | `<DOMAIN>_<SYSTEM>_<ENV>`; in Git, `<DOMAIN>_<SYSTEM>_${ENV}` |
| Identity | a stable `uuid`, identical in every environment |
| Credentials | **never** in Git — `sqlalchemy_uri: ${WAREHOUSE_URI}` |
| DML | `allow_dml: false` unless there is a stated reason |
| CTAS/CVAS | disabled unless required |
| SQL Lab | `expose_in_sqllab` deliberately set |

```yaml
database_name: SALES_DWH_${ENV}
sqlalchemy_uri: ${WAREHOUSE_URI}
uuid: 75c590eb-27e3-5327-b659-a6f31c462b14
expose_in_sqllab: true
allow_dml: false
allow_ctas: false
allow_cvas: false
```

### Credentials

Superset exports the password masked as `XXXXXXXXXX`, which is both useless and
environment-specific, so normalization replaces the whole URI with
`${WAREHOUSE_URI}`. At import:

1. `${WAREHOUSE_URI}` resolves from `config/environments/<env>.yaml`, whose
   value is an `env:` reference read from the process environment.
2. The password is additionally supplied through the import endpoint's separate
   `passwords` parameter, so it need never appear inside the bundle.

`validate.py` fails on any connection string containing a literal password.

### Least privilege

The warehouse account Superset uses should be read-only on the reporting
schema. `allow_dml: false` is a Superset-level control; database-level
permissions are the real boundary.

## Datasets

### Requirements

| Requirement | Rule |
|---|---|
| Name | `<DOMAIN>_<SUBJECT>_<GRAIN>` |
| Identity | stable `uuid` |
| Database | referenced by `database_uuid`, never a numeric id |
| Grain | stated in the name and the description |
| Columns | explicitly typed and described |
| Metrics | defined here, not in charts |
| Query | explicit column list, never `SELECT *` |

### Prefer a warehouse view

Business logic belongs in the warehouse, not hidden inside a Superset dataset:

```sql
CREATE OR REPLACE VIEW sales.vw_order_daily AS
WITH enriched_orders AS (
    SELECT o.order_id, o.order_date, o.order_status, ...
    FROM sales.fct_orders AS o
    INNER JOIN sales.dim_customer AS c ON c.customer_id = o.customer_id
)
SELECT eo.order_id, eo.order_date, ..., eo.net_amount - eo.cost_amount AS margin_amount
FROM enriched_orders AS eo;
```

A view is testable with plain SQL, reviewable in a diff, and reusable outside
Superset. See [`sql/models/01_sales_warehouse.sql`](../sql/models/01_sales_warehouse.sql).

### SQL style

- Uppercase keywords, consistently.
- Explicit column lists — **never `SELECT *`**, which silently changes shape
  when the underlying table does.
- CTEs rather than nested subqueries.
- Alias every table; qualify every column.
- Deterministic ordering wherever results are compared.
- Comment any non-obvious calculation.

### Validation

Every production dataset must have:

- [ ] Database exists and connects (`SELECT 1`)
- [ ] Schema and table/view exist
- [ ] Expected columns present with expected types
- [ ] Metrics compile into executable SQL
- [ ] Calculated columns compile
- [ ] A basic query returns rows
- [ ] No NULLs in columns used as dimensions

`smoke_test.py` covers the Superset side;
[`sql/validation/validate_sales_dataset.sql`](../sql/validation/validate_sales_dataset.sql)
covers the warehouse side — including checks Superset cannot make, such as
`order_status` gaining an unexpected value that would silently drop rows out of
every metric.

## Checklist

- [ ] Names follow the convention
- [ ] UUIDs stable and unique
- [ ] No credentials in Git
- [ ] `allow_dml: false` unless justified
- [ ] Dataset references its database by UUID
- [ ] Business logic in a warehouse view
- [ ] No `SELECT *`
- [ ] Metrics defined on the dataset
- [ ] Validation SQL passes
