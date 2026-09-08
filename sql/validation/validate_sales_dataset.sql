-- ---------------------------------------------------------------------------
-- Validation queries for the SALES_ORDER_DAILY dataset.
--
-- Run against the warehouse (not the Superset metadata database) to confirm
-- the physical model still supports the semantic layer. scripts/smoke_test.py
-- covers the Superset side; these check the data itself.
--
--   psql "$DATABASE_URI" -f sql/validation/validate_sales_dataset.sql
-- ---------------------------------------------------------------------------

-- 1. The backing view exists and is queryable ------------------------------
SELECT
    COUNT(*) AS row_count
FROM sales.vw_order_daily;

-- 2. Every column the dataset declares is present --------------------------
--    Expected: 13 rows. Fewer means a chart or metric references a column
--    that no longer exists.
SELECT
    column_name,
    data_type
FROM information_schema.columns
WHERE table_schema = 'sales'
  AND table_name   = 'vw_order_daily'
ORDER BY column_name;

-- 3. order_status is constrained to the values the metrics assume ----------
--    The metrics filter on 'completed'; an unexpected status silently drops
--    rows out of every metric without any error.
SELECT
    order_status,
    COUNT(*) AS order_count
FROM sales.vw_order_daily
GROUP BY order_status
ORDER BY order_status;

-- 4. Referential integrity: no order without a customer --------------------
--    Expected: 0.
SELECT
    COUNT(*) AS orphaned_orders
FROM sales.fct_orders AS o
LEFT JOIN sales.dim_customer AS c
    ON c.customer_id = o.customer_id
WHERE c.customer_id IS NULL;

-- 5. The metrics reconcile against a direct calculation --------------------
--    revenue, order_count and average_order_value computed here must equal
--    what Superset reports. A mismatch means the semantic layer has drifted
--    away from the physical model.
WITH completed_orders AS (
    SELECT
        o.order_id,
        o.net_amount,
        o.margin_amount
    FROM sales.vw_order_daily AS o
    WHERE o.order_status = 'completed'
)
SELECT
    SUM(co.net_amount)                                    AS revenue,
    COUNT(DISTINCT co.order_id)                           AS order_count,
    SUM(co.net_amount) / NULLIF(COUNT(DISTINCT co.order_id), 0)
                                                          AS average_order_value,
    SUM(co.margin_amount)                                 AS gross_margin
FROM completed_orders AS co;

-- 6. No NULLs in the columns charts group by -------------------------------
--    A NULL dimension shows up as an empty bar or a missing table row.
SELECT
    COUNT(*) FILTER (WHERE region IS NULL)        AS null_region,
    COUNT(*) FILTER (WHERE customer_name IS NULL) AS null_customer_name,
    COUNT(*) FILTER (WHERE order_date IS NULL)    AS null_order_date
FROM sales.vw_order_daily;
