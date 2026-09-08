-- ---------------------------------------------------------------------------
-- SALES semantic metric definitions.
--
-- These are the canonical, reviewable definitions of the SALES metrics. The
-- executable copies live on the SALES_ORDER_DAILY dataset in
-- assets/datasets/SALES_DWH/SALES_ORDER_DAILY.yaml; this file is what a
-- business stakeholder reads and what a reviewer diffs when a definition
-- changes.
--
-- Rule: one business concept, one definition. If two dashboards need
-- "revenue", they use this metric - they do not each define their own.
-- scripts/validate.py fails the build when two metrics share identical SQL
-- under different names.
-- ---------------------------------------------------------------------------

-- revenue -------------------------------------------------------------------
-- Business definition:
--   Sum of completed order revenue, net of discounts, excluding cancelled and
--   pending orders.
-- Owner:       Data Platform Team
-- Unit:        currency (reporting currency of the warehouse)
-- Aggregation: additive over every dimension
-- Format:      $,.2f
SUM(CASE WHEN order_status = 'completed' THEN net_amount ELSE 0 END)

-- order_count ---------------------------------------------------------------
-- Business definition:
--   Number of distinct completed orders. Cancelled and pending orders are
--   excluded so the count reconciles with `revenue`.
-- Owner:       Data Platform Team
-- Unit:        count
-- Aggregation: additive over every dimension; distinct at order grain
-- Format:      ,d
COUNT(DISTINCT CASE WHEN order_status = 'completed' THEN order_id END)

-- average_order_value -------------------------------------------------------
-- Business definition:
--   Completed revenue divided by completed order count.
-- Owner:       Data Platform Team
-- Unit:        currency per order
-- Aggregation: NON-ADDITIVE. It is a ratio and must be recomputed at each
--              level of aggregation, never summed or averaged from a lower
--              grain. Expressing it as a ratio of two sums (rather than as
--              AVG of a per-row value) is what makes it correct after
--              filtering and roll-up.
-- Format:      $,.2f
SUM(CASE WHEN order_status = 'completed' THEN net_amount ELSE 0 END)
    / NULLIF(COUNT(DISTINCT CASE WHEN order_status = 'completed' THEN order_id END), 0)

-- gross_margin --------------------------------------------------------------
-- Business definition:
--   Completed net revenue less cost of goods sold.
-- Owner:       Data Platform Team
-- Unit:        currency
-- Aggregation: additive over every dimension
-- Format:      $,.2f
SUM(CASE WHEN order_status = 'completed' THEN margin_amount ELSE 0 END)
