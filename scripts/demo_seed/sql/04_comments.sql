-- 04_comments.sql
-- Table + column descriptions. COMMENT ON TABLE and ALTER COLUMN ... COMMENT are
-- idempotent (they overwrite), so this is safe to re-run. All 13 tables are
-- described (100%); a small, deliberate set of surrogate-key / minor columns is
-- left uncommented (~85% column coverage) so the report shows a realistic mix
-- rather than a synthetic 100%.
--
-- Pillar coverage:
--   * Metadata Richness — table_pct and column_pct comment coverage.

-- ============ table comments (all 13) ============
COMMENT ON TABLE {{CATALOG}}.reference.dim_date       IS 'Conformed date dimension, one row per calendar day (2023-2024), with day/month/quarter/year attributes.';
-- @@
COMMENT ON TABLE {{CATALOG}}.reference.dim_geography  IS 'Conformed geography dimension: country, region and city used across subject areas.';
-- @@
COMMENT ON TABLE {{CATALOG}}.sales.dim_customer       IS 'Customer master: identity, market segment and home geography. Grain: one row per customer.';
-- @@
COMMENT ON TABLE {{CATALOG}}.sales.dim_product        IS 'Product master: category, subcategory and list unit price. Grain: one row per product.';
-- @@
COMMENT ON TABLE {{CATALOG}}.sales.fact_orders        IS 'Order line fact: quantity, price, discount and gross/net amounts. Grain: one row per order line.';
-- @@
COMMENT ON TABLE {{CATALOG}}.finance.dim_account      IS 'Chart of accounts: account name, type and cost centre. Grain: one row per GL account.';
-- @@
COMMENT ON TABLE {{CATALOG}}.finance.fact_payments    IS 'Customer payment fact: amount, method and settlement status. Grain: one row per payment.';
-- @@
COMMENT ON TABLE {{CATALOG}}.finance.fact_gl_entries  IS 'General-ledger entry fact: signed amount by account and date. Grain: one row per journal line.';
-- @@
COMMENT ON TABLE {{CATALOG}}.operations.dim_warehouse IS 'Warehouse/distribution-centre dimension. Grain: one row per warehouse.';
-- @@
COMMENT ON TABLE {{CATALOG}}.operations.fact_shipments IS 'Shipment fact: shipping duration, units and on-time flag per order shipment. Grain: one row per shipment.';
-- @@
COMMENT ON TABLE {{CATALOG}}.operations.fact_inventory IS 'Daily inventory position: on-hand quantity and reorder point by product and warehouse.';
-- @@
COMMENT ON TABLE {{CATALOG}}.marts.gold_daily_sales   IS 'Gold: daily sales summary (orders, revenue, units) for dashboards and Genie.';
-- @@
COMMENT ON TABLE {{CATALOG}}.marts.gold_customer_360  IS 'Gold: customer 360 with lifetime order counts, net revenue and recency.';
-- @@
-- ============ column comments ============
-- reference.dim_date (all)
ALTER TABLE {{CATALOG}}.reference.dim_date ALTER COLUMN date_key     COMMENT 'Surrogate date key (days since 2023-01-01). Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_date ALTER COLUMN cal_date     COMMENT 'Calendar date.';
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_date ALTER COLUMN day_name     COMMENT 'Day of week name (Monday-Sunday).';
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_date ALTER COLUMN day_of_month COMMENT 'Day of month (1-31).';
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_date ALTER COLUMN month_num    COMMENT 'Month number (1-12).';
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_date ALTER COLUMN month_name   COMMENT 'Month name (January-December).';
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_date ALTER COLUMN quarter      COMMENT 'Calendar quarter (1-4).';
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_date ALTER COLUMN year         COMMENT 'Calendar year.';
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_date ALTER COLUMN is_weekend   COMMENT 'True if the day falls on a weekend.';
-- @@
-- reference.dim_geography (city intentionally left uncommented)
ALTER TABLE {{CATALOG}}.reference.dim_geography ALTER COLUMN geo_key COMMENT 'Surrogate geography key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_geography ALTER COLUMN country COMMENT 'Country name.';
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_geography ALTER COLUMN region  COMMENT 'Sales region within the country.';
-- @@
-- sales.dim_customer (all)
ALTER TABLE {{CATALOG}}.sales.dim_customer ALTER COLUMN customer_key  COMMENT 'Surrogate customer key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer ALTER COLUMN customer_id   COMMENT 'Natural customer identifier (business key).';
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer ALTER COLUMN customer_name COMMENT 'Customer display name.';
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer ALTER COLUMN segment       COMMENT 'Market segment: Enterprise, SMB, Consumer or Public Sector.';
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer ALTER COLUMN email         COMMENT 'Primary contact email (synthetic).';
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer ALTER COLUMN geo_key       COMMENT 'Foreign key to reference.dim_geography.';
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer ALTER COLUMN signup_date   COMMENT 'Date the customer first signed up.';
-- @@
-- sales.dim_product (subcategory intentionally left uncommented)
ALTER TABLE {{CATALOG}}.sales.dim_product ALTER COLUMN product_key  COMMENT 'Surrogate product key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_product ALTER COLUMN product_id   COMMENT 'Natural product identifier (business key).';
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_product ALTER COLUMN product_name COMMENT 'Product display name.';
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_product ALTER COLUMN category     COMMENT 'Product category.';
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_product ALTER COLUMN unit_price   COMMENT 'List unit price in USD.';
-- @@
-- sales.fact_orders (FK surrogate keys intentionally left uncommented)
ALTER TABLE {{CATALOG}}.sales.fact_orders ALTER COLUMN order_key    COMMENT 'Surrogate order-line key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders ALTER COLUMN order_id     COMMENT 'Natural order identifier.';
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders ALTER COLUMN quantity     COMMENT 'Units ordered on this line.';
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders ALTER COLUMN unit_price   COMMENT 'Unit price applied at order time (USD).';
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders ALTER COLUMN discount_pct COMMENT 'Discount fraction applied (0.00-0.30).';
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders ALTER COLUMN gross_amount COMMENT 'Gross line amount = quantity * unit_price (USD).';
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders ALTER COLUMN net_amount   COMMENT 'Net line amount after discount (USD).';
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders ALTER COLUMN order_status COMMENT 'Order state: Fulfilled, Open, Processing or Cancelled.';
-- @@
-- finance.dim_account (all)
ALTER TABLE {{CATALOG}}.finance.dim_account ALTER COLUMN account_key  COMMENT 'Surrogate account key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.finance.dim_account ALTER COLUMN account_id   COMMENT 'Natural account identifier.';
-- @@
ALTER TABLE {{CATALOG}}.finance.dim_account ALTER COLUMN account_name COMMENT 'Account display name.';
-- @@
ALTER TABLE {{CATALOG}}.finance.dim_account ALTER COLUMN account_type COMMENT 'Account classification: Revenue, Expense, Asset or Liability.';
-- @@
ALTER TABLE {{CATALOG}}.finance.dim_account ALTER COLUMN cost_center  COMMENT 'Owning cost centre.';
-- @@
-- finance.fact_payments (FK surrogate keys intentionally left uncommented)
ALTER TABLE {{CATALOG}}.finance.fact_payments ALTER COLUMN payment_key    COMMENT 'Surrogate payment key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments ALTER COLUMN payment_id     COMMENT 'Natural payment identifier.';
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments ALTER COLUMN amount         COMMENT 'Payment amount (USD).';
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments ALTER COLUMN payment_method COMMENT 'Payment method: Card, ACH, Wire or Check.';
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments ALTER COLUMN payment_status COMMENT 'Settlement status: Settled, Pending or Failed.';
-- @@
-- finance.fact_gl_entries (all)
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries ALTER COLUMN entry_key   COMMENT 'Surrogate GL entry key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries ALTER COLUMN account_key COMMENT 'Foreign key to finance.dim_account.';
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries ALTER COLUMN date_key    COMMENT 'Foreign key to reference.dim_date.';
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries ALTER COLUMN amount      COMMENT 'Signed journal amount (USD); positive debit, negative credit.';
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries ALTER COLUMN entry_type  COMMENT 'Entry type: Debit or Credit.';
-- @@
-- operations.dim_warehouse (all)
ALTER TABLE {{CATALOG}}.operations.dim_warehouse ALTER COLUMN warehouse_key  COMMENT 'Surrogate warehouse key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.operations.dim_warehouse ALTER COLUMN warehouse_id   COMMENT 'Natural warehouse identifier.';
-- @@
ALTER TABLE {{CATALOG}}.operations.dim_warehouse ALTER COLUMN warehouse_name COMMENT 'Warehouse display name.';
-- @@
ALTER TABLE {{CATALOG}}.operations.dim_warehouse ALTER COLUMN region         COMMENT 'Operating region of the warehouse.';
-- @@
-- operations.fact_shipments (FK surrogate keys intentionally left uncommented)
ALTER TABLE {{CATALOG}}.operations.fact_shipments ALTER COLUMN shipment_key COMMENT 'Surrogate shipment key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments ALTER COLUMN shipment_id  COMMENT 'Natural shipment identifier.';
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments ALTER COLUMN order_key    COMMENT 'Foreign key to sales.fact_orders.';
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments ALTER COLUMN ship_days    COMMENT 'Days elapsed from order to shipment.';
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments ALTER COLUMN units        COMMENT 'Units in the shipment.';
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments ALTER COLUMN on_time_flag COMMENT 'True if the shipment met its SLA.';
-- @@
-- operations.fact_inventory (FK surrogate keys intentionally left uncommented)
ALTER TABLE {{CATALOG}}.operations.fact_inventory ALTER COLUMN inventory_key COMMENT 'Surrogate inventory-position key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory ALTER COLUMN product_key   COMMENT 'Foreign key to sales.dim_product.';
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory ALTER COLUMN on_hand_qty   COMMENT 'On-hand quantity for the product at the warehouse.';
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory ALTER COLUMN reorder_point COMMENT 'Reorder threshold that triggers replenishment.';
-- @@
-- marts.gold_daily_sales (all)
ALTER TABLE {{CATALOG}}.marts.gold_daily_sales ALTER COLUMN report_date   COMMENT 'Calendar date of the summary.';
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_daily_sales ALTER COLUMN orders        COMMENT 'Number of order lines that day.';
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_daily_sales ALTER COLUMN gross_revenue COMMENT 'Total gross revenue that day (USD).';
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_daily_sales ALTER COLUMN net_revenue   COMMENT 'Total net revenue after discounts that day (USD).';
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_daily_sales ALTER COLUMN units         COMMENT 'Total units sold that day.';
-- @@
-- marts.gold_customer_360 (all)
ALTER TABLE {{CATALOG}}.marts.gold_customer_360 ALTER COLUMN customer_key         COMMENT 'Surrogate customer key. Primary key.';
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_customer_360 ALTER COLUMN customer_name        COMMENT 'Customer display name.';
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_customer_360 ALTER COLUMN segment              COMMENT 'Market segment.';
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_customer_360 ALTER COLUMN lifetime_orders      COMMENT 'Lifetime count of order lines.';
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_customer_360 ALTER COLUMN lifetime_net_revenue COMMENT 'Lifetime net revenue (USD).';
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_customer_360 ALTER COLUMN last_order_date      COMMENT 'Date of the customer''s most recent order.';
