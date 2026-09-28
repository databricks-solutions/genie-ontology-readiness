-- 01_catalog_schemas.sql
-- Schemas for the Genie Ontology readiness demo, created under an EXISTING catalog.
-- The catalog ({{CATALOG}}) must already exist and be writable by the runner — we
-- do NOT create it (that needs metastore admin and is unnecessary here).
-- Idempotent: CREATE SCHEMA ... IF NOT EXISTS. Statements are separated by a line
-- containing only `-- @@` (see seed.py). {{CATALOG}} is substituted at runtime.
--
-- Pillar coverage:
--   * UC Foundation  — a governed catalog with >= 5 schemas (schema-count bonus).
--   * Relationships  — a `marts` gold layer + fact_/dim_ naming across schemas.

CREATE SCHEMA IF NOT EXISTS {{CATALOG}}.reference
  COMMENT 'Conformed, shared dimensions (date, geography) reused across sales, finance and operations.';
-- @@
CREATE SCHEMA IF NOT EXISTS {{CATALOG}}.sales
  COMMENT 'Sales subject area — customers, products and the order fact.';
-- @@
CREATE SCHEMA IF NOT EXISTS {{CATALOG}}.finance
  COMMENT 'Finance subject area — chart of accounts, payments and general-ledger entries.';
-- @@
CREATE SCHEMA IF NOT EXISTS {{CATALOG}}.operations
  COMMENT 'Operations subject area — warehouses, shipments and inventory positions.';
-- @@
CREATE SCHEMA IF NOT EXISTS {{CATALOG}}.marts
  COMMENT 'Curated gold layer — pre-joined summaries and governed metric views for analytics and Genie.';
