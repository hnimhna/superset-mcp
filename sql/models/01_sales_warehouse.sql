-- ---------------------------------------------------------------------------
-- SALES domain warehouse model.
--
-- Loaded automatically by the warehouse-db container on first initialization
-- (mounted into /docker-entrypoint-initdb.d). Deterministic and idempotent:
-- the generated data is seeded from fixed arithmetic, never random(), so the
-- same rows appear in every environment and round-trip tests stay stable.
--
-- Style: uppercase keywords, explicit column lists, no SELECT *.
-- ---------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS sales;

-- --------------------------------------------------------------------------
-- Dimension: customer
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales.dim_customer (
    customer_id     INTEGER      PRIMARY KEY,
    customer_name   VARCHAR(128) NOT NULL,
    region          VARCHAR(32)  NOT NULL,
    segment         VARCHAR(32)  NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE  sales.dim_customer IS 'Customer master, one row per customer.';
COMMENT ON COLUMN sales.dim_customer.region IS 'Sales region the customer is billed through.';

-- --------------------------------------------------------------------------
-- Fact: orders (grain = one row per order)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales.fct_orders (
    order_id        INTEGER        PRIMARY KEY,
    order_date      DATE           NOT NULL,
    customer_id     INTEGER        NOT NULL REFERENCES sales.dim_customer (customer_id),
    order_status    VARCHAR(16)    NOT NULL,
    gross_amount    NUMERIC(12, 2) NOT NULL,
    discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
    cost_amount     NUMERIC(12, 2) NOT NULL,
    quantity        INTEGER        NOT NULL
);

COMMENT ON TABLE  sales.fct_orders IS 'Order fact table, grain: one row per order.';
COMMENT ON COLUMN sales.fct_orders.order_status IS 'One of: completed, cancelled, pending.';
COMMENT ON COLUMN sales.fct_orders.gross_amount IS 'Order value before discount, in reporting currency.';

CREATE INDEX IF NOT EXISTS idx_fct_orders_order_date ON sales.fct_orders (order_date);
CREATE INDEX IF NOT EXISTS idx_fct_orders_status     ON sales.fct_orders (order_status);

-- --------------------------------------------------------------------------
-- Deterministic seed data
-- --------------------------------------------------------------------------
INSERT INTO sales.dim_customer (customer_id, customer_name, region, segment, is_active)
SELECT
    seq.n                                                   AS customer_id,
    'Customer ' || LPAD(seq.n::TEXT, 4, '0')                AS customer_name,
    (ARRAY['North', 'South', 'East', 'West'])[1 + (seq.n % 4)]      AS region,
    (ARRAY['Enterprise', 'Mid-Market', 'SMB'])[1 + (seq.n % 3)]     AS segment,
    (seq.n % 17) <> 0                                       AS is_active
FROM generate_series(1, 120) AS seq (n)
ON CONFLICT (customer_id) DO NOTHING;

INSERT INTO sales.fct_orders (
    order_id, order_date, customer_id, order_status,
    gross_amount, discount_amount, cost_amount, quantity
)
SELECT
    seq.n                                                          AS order_id,
    DATE '2025-01-01' + ((seq.n * 7) % 540)                        AS order_date,
    1 + (seq.n % 120)                                              AS customer_id,
    CASE
        WHEN seq.n % 23 = 0 THEN 'cancelled'
        WHEN seq.n % 19 = 0 THEN 'pending'
        ELSE 'completed'
    END                                                            AS order_status,
    ROUND((120 + (seq.n % 880))::NUMERIC, 2)                       AS gross_amount,
    ROUND(((seq.n % 37) * 1.5)::NUMERIC, 2)                        AS discount_amount,
    ROUND((70 + (seq.n % 520))::NUMERIC, 2)                        AS cost_amount,
    1 + (seq.n % 9)                                                AS quantity
FROM generate_series(1, 6000) AS seq (n)
ON CONFLICT (order_id) DO NOTHING;

-- --------------------------------------------------------------------------
-- Reporting view backing the SALES_ORDER_DAILY dataset.
-- Business logic lives here, not hidden inside chart definitions.
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW sales.vw_order_daily AS
WITH enriched_orders AS (
    SELECT
        o.order_id,
        o.order_date,
        o.order_status,
        o.quantity,
        o.gross_amount,
        o.discount_amount,
        o.cost_amount,
        o.gross_amount - o.discount_amount AS net_amount,
        c.customer_id,
        c.customer_name,
        c.region,
        c.segment
    FROM sales.fct_orders  AS o
    INNER JOIN sales.dim_customer AS c
        ON c.customer_id = o.customer_id
)
SELECT
    eo.order_id,
    eo.order_date,
    eo.order_status,
    eo.customer_id,
    eo.customer_name,
    eo.region,
    eo.segment,
    eo.quantity,
    eo.gross_amount,
    eo.discount_amount,
    eo.net_amount,
    eo.cost_amount,
    eo.net_amount - eo.cost_amount AS margin_amount
FROM enriched_orders AS eo;

COMMENT ON VIEW sales.vw_order_daily IS
    'Order-grain reporting view backing the SALES_ORDER_DAILY dataset.';
