-- 03_load_data.sql
-- Deterministic synthetic data. Every INSERT is guarded by
--   WHERE (SELECT COUNT(*) FROM <table>) = 0
-- so re-running never duplicates rows (idempotent). Values are derived from the
-- generator id via hash()/pmod(), so a fresh load reproduces identical data.
-- Dimensions load before facts. Gold summaries (marts.gold_*) are rebuilt from
-- the facts with CREATE OR REPLACE ... AS SELECT (idempotent by construction).
--
-- Pillar coverage:
--   * UC Foundation  — real rows, not empty tables.
--   * Adoption       — volume to make query activity / lineage meaningful.

-- ---- reference.dim_date (2 years) ----
INSERT INTO {{CATALOG}}.reference.dim_date
SELECT
  id AS date_key,
  date_add(DATE'2023-01-01', CAST(id AS INT)) AS cal_date,
  date_format(date_add(DATE'2023-01-01', CAST(id AS INT)), 'EEEE') AS day_name,
  day(date_add(DATE'2023-01-01', CAST(id AS INT))) AS day_of_month,
  month(date_add(DATE'2023-01-01', CAST(id AS INT))) AS month_num,
  date_format(date_add(DATE'2023-01-01', CAST(id AS INT)), 'MMMM') AS month_name,
  quarter(date_add(DATE'2023-01-01', CAST(id AS INT))) AS quarter,
  year(date_add(DATE'2023-01-01', CAST(id AS INT))) AS year,
  dayofweek(date_add(DATE'2023-01-01', CAST(id AS INT))) IN (1,7) AS is_weekend
FROM range(731)
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.reference.dim_date) = 0;
-- @@
INSERT INTO {{CATALOG}}.reference.dim_geography
SELECT
  id AS geo_key,
  element_at(array('USA','USA','USA','Canada','UK','Germany','France','Japan','Australia','Brazil'), CAST(pmod(id,10)+1 AS INT)) AS country,
  element_at(array('West','East','Central','South','North'), CAST(pmod(id,5)+1 AS INT)) AS region,
  concat('City ', CAST(id AS STRING)) AS city
FROM range(20)
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.reference.dim_geography) = 0;
-- @@
INSERT INTO {{CATALOG}}.sales.dim_customer
SELECT
  id AS customer_key,
  concat('CUST-', lpad(CAST(id AS STRING),6,'0')) AS customer_id,
  concat('Customer ', CAST(id AS STRING)) AS customer_name,
  element_at(array('Enterprise','SMB','Consumer','Public Sector'), CAST(pmod(id,4)+1 AS INT)) AS segment,
  concat('customer', CAST(id AS STRING), '@example.com') AS email,
  pmod(id,20) AS geo_key,
  date_add(DATE'2021-01-01', CAST(pmod(abs(hash(id)),900) AS INT)) AS signup_date
FROM range(500)
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.sales.dim_customer) = 0;
-- @@
INSERT INTO {{CATALOG}}.sales.dim_product
SELECT
  id AS product_key,
  concat('PROD-', lpad(CAST(id AS STRING),5,'0')) AS product_id,
  concat('Product ', CAST(id AS STRING)) AS product_name,
  element_at(array('Electronics','Apparel','Home','Grocery','Toys'), CAST(pmod(id,5)+1 AS INT)) AS category,
  element_at(array('Standard','Premium','Value'), CAST(pmod(id,3)+1 AS INT)) AS subcategory,
  CAST(round(5 + pmod(abs(hash(id)),500), 2) AS DECIMAL(10,2)) AS unit_price
FROM range(100)
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.sales.dim_product) = 0;
-- @@
INSERT INTO {{CATALOG}}.finance.dim_account
SELECT
  id AS account_key,
  concat('ACCT-', lpad(CAST(id AS STRING),5,'0')) AS account_id,
  concat(element_at(array('Revenue','COGS','Opex','Cash','AR','AP'), CAST(pmod(id,6)+1 AS INT)), ' ', CAST(id AS STRING)) AS account_name,
  element_at(array('Revenue','Expense','Asset','Liability'), CAST(pmod(id,4)+1 AS INT)) AS account_type,
  concat('CC-', CAST(pmod(id,8) AS STRING)) AS cost_center
FROM range(30)
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.finance.dim_account) = 0;
-- @@
INSERT INTO {{CATALOG}}.operations.dim_warehouse
SELECT
  id AS warehouse_key,
  concat('WH-', CAST(id AS STRING)) AS warehouse_id,
  concat('Warehouse ', CAST(id AS STRING)) AS warehouse_name,
  element_at(array('West','East','Central','South'), CAST(pmod(id,4)+1 AS INT)) AS region
FROM range(8)
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.operations.dim_warehouse) = 0;
-- @@
-- ---- facts ----
INSERT INTO {{CATALOG}}.sales.fact_orders
WITH base AS (
  SELECT
    id AS order_key,
    pmod(abs(hash(id, 1)), 500) AS customer_key,
    pmod(abs(hash(id, 2)), 100) AS product_key,
    pmod(abs(hash(id, 3)), 731) AS date_key,
    CAST(1 + pmod(abs(hash(id, 4)), 10) AS INT) AS quantity,
    CAST(round(5 + pmod(abs(hash(pmod(abs(hash(id, 2)), 100))), 500), 2) AS DECIMAL(10,2)) AS unit_price,
    CAST(round(pmod(abs(hash(id, 5)), 30) / 100.0, 2) AS DECIMAL(5,2)) AS discount_pct,
    element_at(array('Fulfilled','Fulfilled','Fulfilled','Open','Processing','Cancelled'), CAST(pmod(id,6)+1 AS INT)) AS order_status
  FROM range(20000)
)
SELECT
  order_key,
  concat('ORD-', lpad(CAST(order_key AS STRING),8,'0')) AS order_id,
  customer_key, product_key, date_key, quantity, unit_price, discount_pct,
  CAST(quantity * unit_price AS DECIMAL(12,2)) AS gross_amount,
  CAST(quantity * unit_price * (1 - discount_pct) AS DECIMAL(12,2)) AS net_amount,
  order_status
FROM base
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.sales.fact_orders) = 0;
-- @@
INSERT INTO {{CATALOG}}.finance.fact_payments
SELECT
  id AS payment_key,
  concat('PAY-', lpad(CAST(id AS STRING),8,'0')) AS payment_id,
  pmod(abs(hash(id, 7)), 500) AS customer_key,
  pmod(abs(hash(id, 8)), 30) AS account_key,
  pmod(abs(hash(id, 9)), 731) AS date_key,
  CAST(round(10 + pmod(abs(hash(id, 10)), 5000), 2) AS DECIMAL(12,2)) AS amount,
  element_at(array('Card','ACH','Wire','Check'), CAST(pmod(id,4)+1 AS INT)) AS payment_method,
  element_at(array('Settled','Settled','Settled','Pending','Failed'), CAST(pmod(id,5)+1 AS INT)) AS payment_status
FROM range(15000)
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.finance.fact_payments) = 0;
-- @@
INSERT INTO {{CATALOG}}.finance.fact_gl_entries
SELECT
  id AS entry_key,
  pmod(abs(hash(id, 11)), 30) AS account_key,
  pmod(abs(hash(id, 12)), 731) AS date_key,
  CAST(round((pmod(abs(hash(id, 13)), 200000) - 100000), 2) AS DECIMAL(14,2)) AS amount,
  element_at(array('Debit','Credit'), CAST(pmod(id,2)+1 AS INT)) AS entry_type
FROM range(8000)
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.finance.fact_gl_entries) = 0;
-- @@
INSERT INTO {{CATALOG}}.operations.fact_shipments
SELECT
  id AS shipment_key,
  concat('SHIP-', lpad(CAST(id AS STRING),8,'0')) AS shipment_id,
  pmod(abs(hash(id, 14)), 20000) AS order_key,
  pmod(abs(hash(id, 15)), 8) AS warehouse_key,
  pmod(abs(hash(id, 16)), 731) AS date_key,
  CAST(1 + pmod(abs(hash(id, 17)), 9) AS INT) AS ship_days,
  CAST(1 + pmod(abs(hash(id, 18)), 20) AS INT) AS units,
  (pmod(abs(hash(id, 19)), 10) < 8) AS on_time_flag
FROM range(10000)
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.operations.fact_shipments) = 0;
-- @@
INSERT INTO {{CATALOG}}.operations.fact_inventory
SELECT
  id AS inventory_key,
  pmod(abs(hash(id, 20)), 100) AS product_key,
  pmod(abs(hash(id, 21)), 8) AS warehouse_key,
  pmod(abs(hash(id, 22)), 731) AS date_key,
  CAST(pmod(abs(hash(id, 23)), 1000) AS INT) AS on_hand_qty,
  CAST(50 + pmod(abs(hash(id, 24)), 200) AS INT) AS reorder_point
FROM range(5000)
WHERE (SELECT COUNT(*) FROM {{CATALOG}}.operations.fact_inventory) = 0;
-- @@
-- ---- gold summaries (rebuilt every run) ----
CREATE OR REPLACE TABLE {{CATALOG}}.marts.gold_daily_sales
COMMENT 'Gold: daily sales summary (orders, revenue, units) aggregated from sales.fact_orders.'
AS
SELECT d.cal_date AS report_date,
       COUNT(*) AS orders,
       CAST(SUM(o.gross_amount) AS DECIMAL(18,2)) AS gross_revenue,
       CAST(SUM(o.net_amount)   AS DECIMAL(18,2)) AS net_revenue,
       SUM(o.quantity) AS units
FROM {{CATALOG}}.sales.fact_orders o
JOIN {{CATALOG}}.reference.dim_date d ON o.date_key = d.date_key
GROUP BY d.cal_date;
-- @@
CREATE OR REPLACE TABLE {{CATALOG}}.marts.gold_customer_360
COMMENT 'Gold: one row per customer with lifetime order counts and net revenue.'
AS
SELECT c.customer_key, c.customer_name, c.segment,
       COUNT(o.order_key) AS lifetime_orders,
       CAST(COALESCE(SUM(o.net_amount),0) AS DECIMAL(18,2)) AS lifetime_net_revenue,
       MAX(d.cal_date) AS last_order_date
FROM {{CATALOG}}.sales.dim_customer c
LEFT JOIN {{CATALOG}}.sales.fact_orders o ON c.customer_key = o.customer_key
LEFT JOIN {{CATALOG}}.reference.dim_date d ON o.date_key = d.date_key
GROUP BY c.customer_key, c.customer_name, c.segment;
