-- 02_tables.sql
-- Managed Delta tables — a small star schema across 4 subject areas.
-- Idempotent: CREATE TABLE IF NOT EXISTS. Key columns are NOT NULL so that
-- PRIMARY KEY constraints (06) can be declared. Gold summary tables are built
-- from data in 03 (CREATE OR REPLACE ... AS SELECT). No LOCATION => UC-managed.
--
-- Pillar coverage:
--   * UC Foundation  — managed tables in Unity Catalog.
--   * Relationships  — dim_/fact_ tables + NOT NULL keys for PK/FK (see 06).

-- ---- reference (conformed dimensions) ----
CREATE TABLE IF NOT EXISTS {{CATALOG}}.reference.dim_date (
  date_key      BIGINT  NOT NULL,
  cal_date      DATE,
  day_name      STRING,
  day_of_month  INT,
  month_num     INT,
  month_name    STRING,
  quarter       INT,
  year          INT,
  is_weekend    BOOLEAN
) USING DELTA;
-- @@
CREATE TABLE IF NOT EXISTS {{CATALOG}}.reference.dim_geography (
  geo_key   BIGINT  NOT NULL,
  country   STRING,
  region    STRING,
  city      STRING
) USING DELTA;
-- @@
-- ---- sales ----
CREATE TABLE IF NOT EXISTS {{CATALOG}}.sales.dim_customer (
  customer_key   BIGINT  NOT NULL,
  customer_id    STRING,
  customer_name  STRING,
  segment        STRING,
  email          STRING,
  geo_key        BIGINT,
  signup_date    DATE
) USING DELTA;
-- @@
CREATE TABLE IF NOT EXISTS {{CATALOG}}.sales.dim_product (
  product_key   BIGINT  NOT NULL,
  product_id    STRING,
  product_name  STRING,
  category      STRING,
  subcategory   STRING,
  unit_price    DECIMAL(10,2)
) USING DELTA;
-- @@
CREATE TABLE IF NOT EXISTS {{CATALOG}}.sales.fact_orders (
  order_key      BIGINT  NOT NULL,
  order_id       STRING,
  customer_key   BIGINT,
  product_key    BIGINT,
  date_key       BIGINT,
  quantity       INT,
  unit_price     DECIMAL(10,2),
  discount_pct   DECIMAL(5,2),
  gross_amount   DECIMAL(12,2),
  net_amount     DECIMAL(12,2),
  order_status   STRING
) USING DELTA;
-- @@
-- ---- finance ----
CREATE TABLE IF NOT EXISTS {{CATALOG}}.finance.dim_account (
  account_key    BIGINT  NOT NULL,
  account_id     STRING,
  account_name   STRING,
  account_type   STRING,
  cost_center    STRING
) USING DELTA;
-- @@
CREATE TABLE IF NOT EXISTS {{CATALOG}}.finance.fact_payments (
  payment_key     BIGINT  NOT NULL,
  payment_id      STRING,
  customer_key    BIGINT,
  account_key     BIGINT,
  date_key        BIGINT,
  amount          DECIMAL(12,2),
  payment_method  STRING,
  payment_status  STRING
) USING DELTA;
-- @@
CREATE TABLE IF NOT EXISTS {{CATALOG}}.finance.fact_gl_entries (
  entry_key    BIGINT  NOT NULL,
  account_key  BIGINT,
  date_key     BIGINT,
  amount       DECIMAL(14,2),
  entry_type   STRING
) USING DELTA;
-- @@
-- ---- operations ----
CREATE TABLE IF NOT EXISTS {{CATALOG}}.operations.dim_warehouse (
  warehouse_key   BIGINT  NOT NULL,
  warehouse_id    STRING,
  warehouse_name  STRING,
  region          STRING
) USING DELTA;
-- @@
CREATE TABLE IF NOT EXISTS {{CATALOG}}.operations.fact_shipments (
  shipment_key   BIGINT  NOT NULL,
  shipment_id    STRING,
  order_key      BIGINT,
  warehouse_key  BIGINT,
  date_key       BIGINT,
  ship_days      INT,
  units          INT,
  on_time_flag   BOOLEAN
) USING DELTA;
-- @@
CREATE TABLE IF NOT EXISTS {{CATALOG}}.operations.fact_inventory (
  inventory_key   BIGINT  NOT NULL,
  product_key     BIGINT,
  warehouse_key   BIGINT,
  date_key        BIGINT,
  on_hand_qty     INT,
  reorder_point   INT
) USING DELTA;
