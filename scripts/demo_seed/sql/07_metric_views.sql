-- 07_metric_views.sql
-- Unity Catalog metric views — the governed metrics layer that feeds Genie.
-- CREATE OR REPLACE VIEW ... WITH METRICS is idempotent. Each carries a YAML
-- `comment:` so it counts as a described metric view. Requires DBR 17.2+ for
-- YAML v1.1 (serverless warehouses qualify).
--
-- Pillar coverage:
--   * Metrics — count of metric views (table_type = 'METRIC_VIEW') + % described.

CREATE OR REPLACE VIEW {{CATALOG}}.marts.mv_sales_orders
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Sales order KPIs: revenue, order volume and average order value by month, status and customer segment."
source: {{CATALOG}}.sales.fact_orders
joins:
  - name: dim_date
    source: {{CATALOG}}.reference.dim_date
    on: source.date_key = dim_date.date_key
  - name: dim_customer
    source: {{CATALOG}}.sales.dim_customer
    on: source.customer_key = dim_customer.customer_key
dimensions:
  - name: Order Month
    expr: DATE_TRUNC('MONTH', dim_date.cal_date)
    comment: "Calendar month of the order."
  - name: Order Status
    expr: order_status
    comment: "Order lifecycle state."
  - name: Customer Segment
    expr: dim_customer.segment
    comment: "Segment of the ordering customer."
measures:
  - name: Order Count
    expr: COUNT(1)
    comment: "Number of order lines."
  - name: Gross Revenue
    expr: SUM(gross_amount)
    comment: "Gross revenue before discounts."
  - name: Net Revenue
    expr: SUM(net_amount)
    comment: "Net revenue after discounts."
  - name: Average Order Value
    expr: SUM(net_amount) / COUNT(1)
    comment: "Net revenue per order line."
$$;
-- @@
CREATE OR REPLACE VIEW {{CATALOG}}.marts.mv_product_performance
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Product performance: units sold and net revenue by product category."
source: {{CATALOG}}.sales.fact_orders
joins:
  - name: dim_product
    source: {{CATALOG}}.sales.dim_product
    on: source.product_key = dim_product.product_key
dimensions:
  - name: Category
    expr: dim_product.category
    comment: "Product category."
  - name: Subcategory
    expr: dim_product.subcategory
    comment: "Product subcategory."
measures:
  - name: Units Sold
    expr: SUM(quantity)
    comment: "Total units sold."
  - name: Net Revenue
    expr: SUM(net_amount)
    comment: "Net revenue after discounts."
  - name: Distinct Orders
    expr: COUNT(DISTINCT order_id)
    comment: "Distinct orders containing the product."
$$;
-- @@
CREATE OR REPLACE VIEW {{CATALOG}}.marts.mv_customer_revenue
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Customer revenue KPIs: active customers and revenue per customer by segment."
source: {{CATALOG}}.sales.fact_orders
joins:
  - name: dim_customer
    source: {{CATALOG}}.sales.dim_customer
    on: source.customer_key = dim_customer.customer_key
dimensions:
  - name: Customer Segment
    expr: dim_customer.segment
    comment: "Market segment."
measures:
  - name: Net Revenue
    expr: SUM(net_amount)
    comment: "Net revenue after discounts."
  - name: Active Customers
    expr: COUNT(DISTINCT customer_key)
    comment: "Distinct customers with orders."
  - name: Revenue per Customer
    expr: SUM(net_amount) / COUNT(DISTINCT customer_key)
    comment: "Net revenue divided by distinct customers."
$$;
-- @@
CREATE OR REPLACE VIEW {{CATALOG}}.marts.mv_payments
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Payment KPIs: payment volume and value by month, method and settlement status."
source: {{CATALOG}}.finance.fact_payments
joins:
  - name: dim_date
    source: {{CATALOG}}.reference.dim_date
    on: source.date_key = dim_date.date_key
dimensions:
  - name: Payment Month
    expr: DATE_TRUNC('MONTH', dim_date.cal_date)
    comment: "Calendar month of the payment."
  - name: Payment Method
    expr: payment_method
    comment: "Payment instrument."
  - name: Payment Status
    expr: payment_status
    comment: "Settlement status."
measures:
  - name: Payment Count
    expr: COUNT(1)
    comment: "Number of payments."
  - name: Total Paid
    expr: SUM(amount)
    comment: "Total payment amount."
$$;
-- @@
CREATE OR REPLACE VIEW {{CATALOG}}.marts.mv_gl_summary
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "General-ledger summary: net posted amount and entry volume by account type and entry type."
source: {{CATALOG}}.finance.fact_gl_entries
joins:
  - name: dim_account
    source: {{CATALOG}}.finance.dim_account
    on: source.account_key = dim_account.account_key
dimensions:
  - name: Account Type
    expr: dim_account.account_type
    comment: "Account classification."
  - name: Entry Type
    expr: entry_type
    comment: "Debit or Credit."
measures:
  - name: Net Amount
    expr: SUM(amount)
    comment: "Net signed posted amount."
  - name: Entry Count
    expr: COUNT(1)
    comment: "Number of journal lines."
$$;
-- @@
CREATE OR REPLACE VIEW {{CATALOG}}.marts.mv_shipments_sla
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Shipment SLA KPIs: shipment volume, average duration and on-time rate by warehouse region."
source: {{CATALOG}}.operations.fact_shipments
joins:
  - name: dim_warehouse
    source: {{CATALOG}}.operations.dim_warehouse
    on: source.warehouse_key = dim_warehouse.warehouse_key
dimensions:
  - name: Warehouse Region
    expr: dim_warehouse.region
    comment: "Operating region of the fulfilling warehouse."
measures:
  - name: Shipment Count
    expr: COUNT(1)
    comment: "Number of shipments."
  - name: Avg Ship Days
    expr: AVG(ship_days)
    comment: "Average days from order to shipment."
  - name: On-Time Rate
    expr: SUM(CASE WHEN on_time_flag THEN 1 ELSE 0 END) * 1.0 / COUNT(1)
    comment: "Fraction of shipments meeting SLA."
$$;
-- @@
CREATE OR REPLACE VIEW {{CATALOG}}.marts.mv_inventory
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Inventory KPIs: on-hand quantity and reorder points by product category and warehouse region."
source: {{CATALOG}}.operations.fact_inventory
joins:
  - name: dim_product
    source: {{CATALOG}}.sales.dim_product
    on: source.product_key = dim_product.product_key
  - name: dim_warehouse
    source: {{CATALOG}}.operations.dim_warehouse
    on: source.warehouse_key = dim_warehouse.warehouse_key
dimensions:
  - name: Category
    expr: dim_product.category
    comment: "Product category."
  - name: Warehouse Region
    expr: dim_warehouse.region
    comment: "Warehouse region."
measures:
  - name: Total On Hand
    expr: SUM(on_hand_qty)
    comment: "Total on-hand units."
  - name: Avg Reorder Point
    expr: AVG(reorder_point)
    comment: "Average reorder threshold."
$$;
