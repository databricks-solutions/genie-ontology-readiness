-- 05_tags.sql
-- Unity Catalog governed tags. SET TAGS is idempotent (overwrites), safe to re-run.
--
-- Pillar coverage:
--   * Metadata Richness    — tagged_tables signal (any governed tag).
--   * Domains & Stewardship — probe reads information_schema.table_tags:
--       domain-style keys : domain / data_domain / business_domain / subject_area / data_product
--       steward keys       : owner / data_owner / steward / data_steward
--       certification      : certification_status = 'certified'
--     Five distinct domain values (shared/sales/finance/operations/analytics)
--     maximise the tag-proxy score.
--
-- NOTE ON TAG POLICIES: some metastores enforce a UC *tag policy* that restricts the
-- allowed VALUES of certain governed tag keys. On this workspace the keys `domain`,
-- `data_domain` and `business_domain` are policy-constrained, so we use the
-- unconstrained domain-style key **`subject_area`** (also recognised by the probe).
-- If `subject_area` is constrained on your metastore, switch to any allowed
-- domain-style key or use values from the policy's allow-list.

-- ---- subject_area (domain) + steward on every table (100% domain coverage) ----
ALTER TABLE {{CATALOG}}.reference.dim_date        SET TAGS ('subject_area' = 'shared',     'steward' = 'data-platform@example.com');
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_geography   SET TAGS ('subject_area' = 'shared',     'steward' = 'data-platform@example.com');
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer        SET TAGS ('subject_area' = 'sales',      'steward' = 'sales-analytics@example.com');
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_product         SET TAGS ('subject_area' = 'sales',      'steward' = 'sales-analytics@example.com');
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders         SET TAGS ('subject_area' = 'sales',      'steward' = 'sales-analytics@example.com');
-- @@
ALTER TABLE {{CATALOG}}.finance.dim_account       SET TAGS ('subject_area' = 'finance',    'steward' = 'finance-analytics@example.com');
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments     SET TAGS ('subject_area' = 'finance',    'steward' = 'finance-analytics@example.com');
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries   SET TAGS ('subject_area' = 'finance',    'steward' = 'finance-analytics@example.com');
-- @@
ALTER TABLE {{CATALOG}}.operations.dim_warehouse  SET TAGS ('subject_area' = 'operations', 'steward' = 'ops-analytics@example.com');
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments SET TAGS ('subject_area' = 'operations', 'steward' = 'ops-analytics@example.com');
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory SET TAGS ('subject_area' = 'operations', 'steward' = 'ops-analytics@example.com');
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_daily_sales    SET TAGS ('subject_area' = 'analytics',  'steward' = 'bi-team@example.com');
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_customer_360   SET TAGS ('subject_area' = 'analytics',  'steward' = 'bi-team@example.com');
-- @@
-- ---- certification on canonical / high-trust assets ----
ALTER TABLE {{CATALOG}}.marts.gold_daily_sales    SET TAGS ('certification_status' = 'certified');
-- @@
ALTER TABLE {{CATALOG}}.marts.gold_customer_360   SET TAGS ('certification_status' = 'certified');
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders         SET TAGS ('certification_status' = 'certified');
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer        SET TAGS ('certification_status' = 'certified');
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_product         SET TAGS ('certification_status' = 'certified');
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments     SET TAGS ('certification_status' = 'certified');
-- @@
-- ---- a few column tags (discovery / PII) ----
ALTER TABLE {{CATALOG}}.sales.dim_customer ALTER COLUMN email         SET TAGS ('pii' = 'email');
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer ALTER COLUMN customer_name SET TAGS ('pii' = 'name');
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders  ALTER COLUMN net_amount    SET TAGS ('classification' = 'financial');
